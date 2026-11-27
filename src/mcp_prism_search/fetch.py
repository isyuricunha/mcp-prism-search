"""Safe webpage fetching and clean text extraction."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from mcp_prism_search.cache import JsonFileCache, build_fetch_cache, get_env_int
from mcp_prism_search.defaults import effective_defaults, search_parameters
from mcp_prism_search.models import (
    DeepSearchPage,
    DeepSearchRequest,
    DeepSearchResponse,
    FailedPage,
    FetchRequest,
    FetchResponse,
)
from mcp_prism_search.search import ddg_search
from mcp_prism_search.security import (
    UnsafeUrlError,
    resolve_safe_address,
    validate_fetch_url,
    validate_url_shape,
)
from mcp_prism_search.text import extract_html_text, is_plain_text_content, truncate_text

LOGGER = logging.getLogger(__name__)

MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
    # Without this a compressed body cannot be size-capped reliably.
    "Accept-Encoding": "identity",
}


def get_fetch_timeout_seconds() -> int:
    return effective_defaults().fetch_timeout


def get_fetch_max_bytes() -> int:
    return get_env_int("FETCH_MAX_BYTES", DEFAULT_MAX_RESPONSE_BYTES)


def get_max_concurrency(max_concurrency: int | None = None) -> int:
    if max_concurrency is not None:
        return max_concurrency
    return effective_defaults().deep_concurrency


def _cache_payload(request: FetchRequest) -> dict[str, Any]:
    return request.to_cache_payload(_extraction_version=3)


def _cacheable_fetch_response(response: FetchResponse) -> dict[str, Any]:
    return response.model_copy(update={"cached": False}).model_dump(mode="json")


def _error_response(url: str, error: str) -> FetchResponse:
    return FetchResponse(
        url=url,
        final_url="",
        title="",
        content="",
        content_type="",
        cached=False,
        success=False,
        error=error,
    )


class RawFetchedResponse:
    """Fully-read response body with the logical (pre-pinning) URL."""

    def __init__(
        self,
        status_code: int,
        headers: httpx.Headers,
        content: bytes,
        logical_url: str,
        charset: str | None,
        truncated: bool = False,
        fetched_bytes: int = 0,
    ) -> None:
        self.status_code = status_code
        self.headers = headers
        self.truncated = truncated
        self.fetched_bytes = fetched_bytes
        self.content = content
        self.logical_url = logical_url
        self.charset = charset


def _pinned_request_url(url: str, safe_ip: str) -> str:
    parsed = urlparse(url)
    host_part = f"[{safe_ip}]" if ":" in safe_ip else safe_ip
    port = parsed.port
    default_port = port in (None, 80) and parsed.scheme == "http"
    default_port = default_port or (port in (None, 443) and parsed.scheme == "https")
    netloc = host_part if default_port else f"{host_part}:{port}"
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))


def _original_host_header(url: str) -> str:
    parsed = httpx.URL(url)
    host = parsed.raw_host.decode("ascii")
    if ":" in host:
        host = "[" + host + "]"
    return host if parsed.port is None else f"{host}:{parsed.port}"


async def _get_with_validated_redirects(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
) -> RawFetchedResponse:
    """Fetch while validating every hop and pinning the connection to the IP
    resolved during validation, so a rebinding DNS answer cannot redirect the
    connection to an internal address. The body is streamed and hard-capped."""

    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        validate_url_shape(current_url)
        safe_ip, _port = await resolve_safe_address(current_url)
        parsed = urlparse(current_url)
        hostname = httpx.URL(current_url).raw_host.decode("ascii")
        request = client.build_request(
            "GET",
            _pinned_request_url(current_url, safe_ip),
            headers={**FETCH_HEADERS, "host": _original_host_header(current_url)},
            extensions={"sni_hostname": hostname} if parsed.scheme == "https" else None,
        )
        response = await client.send(request, stream=True)

        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise UnsafeUrlError("Redirect missing Location header")
                next_url = urljoin(current_url, location)
                validate_url_shape(next_url)
                current_url = next_url
                continue
            if not 200 <= response.status_code < 300:
                return RawFetchedResponse(
                    response.status_code, response.headers, b"", current_url, None,
                )
            content_encoding = response.headers.get("content-encoding", "identity")
            if content_encoding.lower() not in {"identity", ""}:
                raise UnsafeUrlError("Server returned unsupported compressed content")

            chunks: list[bytes] = []
            kept = 0
            truncated = False
            async for chunk in response.aiter_bytes():
                remaining = max_bytes - kept
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    kept += remaining
                    truncated = True
                    break
                chunks.append(chunk)
                kept += len(chunk)
            return RawFetchedResponse(
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                logical_url=current_url,
                charset=response.encoding,
                truncated=truncated,
                fetched_bytes=kept,
            )
        finally:
            await response.aclose()

    raise UnsafeUrlError(f"Too many redirects; maximum is {MAX_REDIRECTS}")


def _response_to_fetch_response(
    request_url: str,
    response: RawFetchedResponse,
    max_chars: int,
) -> FetchResponse:
    final_url = response.logical_url
    content_type = response.headers.get("content-type", "")
    if not 200 <= response.status_code < 300:
        return FetchResponse(
            url=request_url,
            final_url=final_url,
            title="",
            content="",
            content_type=content_type,
            cached=False,
            success=False,
            error=f"HTTP {response.status_code}",
        )

    charset = response.charset or "utf-8"
    try:
        text = response.content.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        text = response.content.decode("utf-8", errors="replace")
    notice = (
        f"[PARTIAL CONTENT: download stopped at the {len(response.content)} byte "
        "safety limit (FETCH_MAX_BYTES). The page continues beyond this point.] "
        if response.truncated else ""
    )

    lowered_content_type = content_type.lower()
    if "html" in lowered_content_type:
        extracted = extract_html_text(text, max_chars)
        return FetchResponse(
            url=request_url,
            final_url=final_url,
            title=extracted.title,
            content=truncate_text(notice + extracted.content, max_chars),
            content_type=content_type,
            cached=False,
            success=True,
            error=None,
            truncated=response.truncated or extracted.truncated,
            fetched_bytes=response.fetched_bytes,
        )

    if is_plain_text_content(content_type, final_url):
        return FetchResponse(
            url=request_url,
            final_url=final_url,
            title="",
            content=truncate_text(notice + text, max_chars),
            content_type=content_type,
            cached=False,
            success=True,
            error=None,
            truncated=response.truncated or len(text) > max_chars,
            fetched_bytes=response.fetched_bytes,
        )

    return FetchResponse(
        url=request_url,
        final_url=final_url,
        title="",
        content="",
        content_type=content_type,
        cached=False,
        success=False,
        error=f"Unsupported content type: {content_type or 'unknown'}",
    )


async def web_fetch(
    url: str,
    max_chars: int | None = None,
    *,
    cache: JsonFileCache | None = None,
) -> FetchResponse:
    from mcp_prism_search.observability import emit

    request = FetchRequest(
        url=url,
        max_chars=max_chars if max_chars is not None else effective_defaults().fetch_max_chars,
    )

    try:
        validate_url_shape(request.url)
    except UnsafeUrlError as exc:
        return _error_response(request.url, str(exc))

    cache = cache or build_fetch_cache()
    payload = _cache_payload(request) | {"_max_bytes": get_fetch_max_bytes()}
    cached_value = cache.get(payload)
    if cached_value is not None:
        try:
            cached = FetchResponse.model_validate(cached_value)
            validate_url_shape(cached.final_url)
            if (cached.url == request.url and cached.success and not cached.error
                    and len(cached.content) <= request.max_chars
                    and ("html" in cached.content_type.lower()
                         or is_plain_text_content(cached.content_type, cached.final_url))):
                # A cache hit does not connect to a pinned address, so retain
                # DNS validation here. Live fetches validate and resolve once per hop.
                try:
                    async with asyncio.timeout(get_fetch_timeout_seconds()):
                        await validate_fetch_url(request.url)
                except UnsafeUrlError as exc:
                    return _error_response(request.url, str(exc))
                except TimeoutError:
                    return _error_response(request.url, "DNS validation timed out")
                emit("cache", "hit", level="INFO", operation="web_fetch")
                return cached.model_copy(update={"cached": True})
            cache.delete(payload)
        except Exception:  # noqa: BLE001 - poisoned/stale cache must not break the tool.
            cache.delete(payload)

    emit("cache", "miss", level="INFO", operation="web_fetch")
    try:
        timeout = get_fetch_timeout_seconds()
        async with asyncio.timeout(timeout), httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0),
            headers=FETCH_HEADERS,
        ) as client:
            response = await _get_with_validated_redirects(
                client, request.url, get_fetch_max_bytes()
            )
        fetch_response = await asyncio.to_thread(
            _response_to_fetch_response, request.url, response, request.max_chars
        )
        if fetch_response.success:
            cache.set(payload, _cacheable_fetch_response(fetch_response))
        return fetch_response
    except UnsafeUrlError as exc:
        return _error_response(request.url, str(exc))
    except Exception as exc:  # noqa: BLE001 - fetch failures must be structured.
        return _error_response(request.url, "Fetch failed: " + type(exc).__name__)


async def ddg_deep_search(
    query: str,
    max_results: int | None = None,
    search_window: int | None = None,
    max_pages: int | None = None,
    max_chars_per_page: int | None = None,
    safe_search: str | None = None,
    time_filter: str | None = None,
    blocked_domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    preferred_domains: list[str] | None = None,
    max_concurrency: int | None = None,
    progress_callback: Any = None,
) -> DeepSearchResponse:
    defaults = effective_defaults()
    request = DeepSearchRequest(
        query=query,
        search_window=search_window,
        max_pages=max_pages if max_pages is not None else defaults.deep_max_pages,
        max_chars_per_page=(max_chars_per_page if max_chars_per_page is not None
                            else defaults.fetch_max_chars),
        max_concurrency=max_concurrency,
        **search_parameters(
            defaults, max_results=max_results, safe_search=safe_search,
            time_filter=time_filter, blocked_domains=blocked_domains,
            allowed_domains=allowed_domains, preferred_domains=preferred_domains,
        ),
    )

    async def _safe_progress(progress: float, total: float) -> None:
        if progress_callback is None:
            return
        try:
            await progress_callback(progress, total)
        except Exception:  # noqa: BLE001 - progress reporting must not break the call.
            LOGGER.debug("deep search progress callback failed", exc_info=True)

    total_steps = float(request.max_pages + 1)
    await _safe_progress(0, total_steps)

    search_response = await ddg_search(
        query=request.query,
        max_results=request.max_results,
        search_window=request.search_window,
        safe_search=request.safe_search,
        time_filter=request.time_filter or "any",
        blocked_domains=request.blocked_domains,
        allowed_domains=request.allowed_domains,
        preferred_domains=request.preferred_domains,
    )
    selected_sources = search_response.results[: request.max_pages]
    semaphore = asyncio.Semaphore(get_max_concurrency(request.max_concurrency))

    await _safe_progress(1, total_steps)
    completed = 1
    progress_lock = asyncio.Lock()

    async def fetch_source(source_url: str) -> FetchResponse:
        nonlocal completed
        async with semaphore:
            try:
                page = await web_fetch(source_url, max_chars=request.max_chars_per_page)
            except Exception as exc:  # noqa: BLE001 - isolate per-page failures.
                page = _error_response(source_url, "Fetch failed: " + type(exc).__name__)
            async with progress_lock:
                completed += 1
                await _safe_progress(float(completed), total_steps)
            return page

    fetched_pages = await asyncio.gather(*(fetch_source(source.url) for source in selected_sources))
    if completed < total_steps:
        await _safe_progress(total_steps, total_steps)
    pages: list[DeepSearchPage] = []
    failed_pages: list[FailedPage] = []
    cached_flags = [search_response.cached]

    for source, page in zip(selected_sources, fetched_pages, strict=True):
        cached_flags.append(page.cached)
        if page.success:
            pages.append(
                DeepSearchPage(
                    title=page.title or source.title,
                    url=source.url,
                    final_url=page.final_url,
                    content=page.content,
                )
            )
        else:
            failed_pages.append(FailedPage(url=source.url, error=page.error or "Fetch failed"))

    return DeepSearchResponse(
        query=request.query,
        search_provider=search_response.provider,
        sources=selected_sources,
        pages=pages,
        failed_pages=failed_pages,
        cached=bool(cached_flags) and all(cached_flags),
        search_error=search_response.error,
    )
