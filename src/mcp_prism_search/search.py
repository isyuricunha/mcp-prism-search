"""DuckDuckGo-focused search with optional ddgs provider support."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import re
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import httpx

from mcp_prism_search.cache import JsonFileCache, build_search_cache, get_env_int
from mcp_prism_search.defaults import effective_defaults, search_parameters
from mcp_prism_search.domains import apply_domain_controls
from mcp_prism_search.models import (
    SearchProvider,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from mcp_prism_search.text import normalize_whitespace

DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
DDGS_SAFE_SEARCH = {"off": "off", "moderate": "moderate", "strict": "on"}
HTML_SAFE_SEARCH = {"off": "-2", "moderate": "-1", "strict": "1"}
TIME_FILTERS = {"day": "d", "week": "w", "month": "m", "year": "y"}
DEFAULT_SEARCH_PROVIDER = "duckduckgo_html"
DEFAULT_DDGS_BACKEND = "duckduckgo"
SEARCH_PROVIDERS = {"duckduckgo_html", "ddgs", "auto"}
DEFAULT_DDG_TIMEOUT_SECONDS = 15
DEFAULT_DOMAIN_CONTROL_WINDOW_MULTIPLIER = 3
DEFAULT_DOMAIN_CONTROL_WINDOW_CAP = 50
LOGGER = logging.getLogger(__name__)
SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_ddg_timeout_seconds() -> int:
    return get_env_int("DDG_TIMEOUT_SECONDS", DEFAULT_DDG_TIMEOUT_SECONDS)


def get_search_provider() -> str:
    provider = os.getenv("SEARCH_PROVIDER", DEFAULT_SEARCH_PROVIDER).strip().lower()
    if provider in SEARCH_PROVIDERS:
        return provider
    LOGGER.warning(
        "Invalid SEARCH_PROVIDER=%r; falling back to %s",
        provider,
        DEFAULT_SEARCH_PROVIDER,
    )
    return DEFAULT_SEARCH_PROVIDER


def _available_ddgs_text_backends() -> set[str] | None:
    try:
        ddgs_module = importlib.import_module("ddgs.ddgs")
    except Exception:  # noqa: BLE001 - ddgs is optional at runtime unless selected.
        return None

    engines = getattr(ddgs_module, "ENGINES", None)
    if not isinstance(engines, dict):
        return None
    text_engines = engines.get("text")
    if not isinstance(text_engines, dict):
        return None
    return {str(backend).lower() for backend in text_engines}


def get_ddgs_backend() -> str:
    backend = os.getenv("DDGS_BACKEND", DEFAULT_DDGS_BACKEND).strip().lower()
    if not backend:
        return DEFAULT_DDGS_BACKEND
    if backend == "auto":
        return backend

    requested_backends = [part.strip() for part in backend.split(",") if part.strip()]
    if not requested_backends:
        return DEFAULT_DDGS_BACKEND

    available_backends = _available_ddgs_text_backends()
    if available_backends is None:
        return ",".join(requested_backends)

    invalid_backends = [
        requested_backend
        for requested_backend in requested_backends
        if requested_backend not in available_backends
    ]
    if invalid_backends:
        LOGGER.warning(
            "Invalid DDGS_BACKEND=%r; falling back to %s",
            backend,
            DEFAULT_DDGS_BACKEND,
        )
        return DEFAULT_DDGS_BACKEND
    return ",".join(requested_backends)


def has_domain_controls(request: SearchRequest) -> bool:
    return bool(
        request.allowed_domains
        or request.blocked_domains
        or request.preferred_domains
    )


NEWS_HINT_TOKENS = {
    "news", "latest", "breaking", "today", "current", "updates", "happening",
}


def search_ttl_for_query(query: str) -> int | None:
    """Shorter cache TTL for freshness-looking queries (news-style tokens).

    Returns None to use the cache instance default TTL.
    """
    tokens = set(re.findall(r"\w+", query.lower()))
    if tokens & NEWS_HINT_TOKENS:
        return effective_defaults().news_cache_ttl
    return None


def _trusted_domain_score(hostname: str) -> float:
    if hostname.endswith(".gov") or hostname.endswith(".edu"):
        return 1.0
    if hostname == "wikipedia.org" or hostname.endswith(".wikipedia.org"):
        return 0.9
    if hostname.endswith(".org"):
        return 0.7
    return 0.4


def rank_results(results: list[SearchResult], query: str) -> list[SearchResult]:
    """Stable deterministic ranking: title/snippet term coverage plus domain
    trust. No ML, no external services."""
    terms = {token for token in re.findall(r"\w+", query.lower()) if len(token) >= 2}

    def score(result: SearchResult) -> float:
        title_tokens = set(re.findall(r"\w+", result.title.lower()))
        snippet_tokens = set(re.findall(r"\w+", result.snippet.lower()))
        title_score = len(title_tokens & terms) / len(terms) if terms else 0.0
        snippet_score = len(snippet_tokens & terms) / len(terms) if terms else 0.0
        hostname = urlparse(result.url).hostname or ""
        return 2.0 * title_score + 1.0 * snippet_score + 1.5 * _trusted_domain_score(hostname)

    valid = [row for row in results if isinstance(row, SearchResult)
             and row.title.strip() and valid_result_url(row.url)]
    return _dedupe_results(sorted(valid, key=score, reverse=True))


def provider_request_size(request: SearchRequest) -> int:
    if request.search_window is not None:
        return request.search_window
    if has_domain_controls(request):
        return min(
            request.max_results * DEFAULT_DOMAIN_CONTROL_WINDOW_MULTIPLIER,
            DEFAULT_DOMAIN_CONTROL_WINDOW_CAP,
        )
    return request.max_results


def resolve_duckduckgo_redirect_url(href: str) -> str:
    from mcp_prism_search.providers.ddg_html import resolve_duckduckgo_redirect_url as resolve

    return resolve(href)


def normalize_url_for_dedupe(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # Explicit default ports identify the same HTTP resource.
    if (scheme == "http" and parsed.port == 80
            or scheme == "https" and parsed.port == 443):
        netloc = netloc.rsplit(":", 1)[0]
    path = parsed.path or ""
    if path == "/" and not parsed.query:
        path = ""
    query = parsed.query
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def _result_from_payload(payload: dict[str, Any]) -> SearchResult | None:
    title = normalize_whitespace(str(payload.get("title") or ""))
    url = str(payload.get("href") or payload.get("url") or "")
    snippet = normalize_whitespace(str(payload.get("body") or payload.get("snippet") or ""))
    if not title or not url:
        return None
    return SearchResult(title=title, url=resolve_duckduckgo_redirect_url(url), snippet=snippet)


def valid_result_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return bool(
            parsed.scheme in {"http", "https"} and parsed.hostname
            and parsed.username is None and parsed.password is None
            and parsed.port != 0 and len(url) <= 8192
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == chr(92)
                        for c in url)
        )
    except ValueError:
        return False


def _dedupe_results(
    results: list[SearchResult],
    max_results: int | None = None,
) -> list[SearchResult]:
    if max_results is not None and max_results <= 0:
        return []
    seen: set[str] = set()
    deduped: list[SearchResult] = []
    for result in results:
        if (not isinstance(result, SearchResult) or not result.title.strip()
                or not valid_result_url(result.url)):
            continue
        normalized_url = normalize_url_for_dedupe(result.url)
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        deduped.append(result)
        if max_results is not None and len(deduped) >= max_results:
            break
    return deduped


def _signature_accepts_parameter(signature: inspect.Signature, parameter_name: str) -> bool:
    return parameter_name in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _ddgs_text_kwargs(
    request: SearchRequest,
    backend: str,
    *,
    include_backend: bool,
) -> dict[str, Any]:
    timelimit = TIME_FILTERS.get(request.time_filter) if request.time_filter else None
    kwargs: dict[str, Any] = {
        "max_results": provider_request_size(request),
        "timelimit": timelimit,
        "safesearch": DDGS_SAFE_SEARCH[request.safe_search],
    }
    if include_backend:
        kwargs["backend"] = backend
    return kwargs


def _legacy_ddgs_allowed(backend: str) -> bool:
    from mcp_prism_search import providers, runtime

    if not providers.SPECS:
        providers.build_registry()
    disabled = {name.split(":", 1)[1] for name in providers.SPECS
                if name.startswith("ddgs:") and not runtime.enabled(name)}
    requested = set(backend.split(","))
    return not (disabled & requested or ("auto" in requested and disabled))


def _search_with_ddgs(
    request: SearchRequest,
    timeout_seconds: int,
    backend: str = DEFAULT_DDGS_BACKEND,
) -> list[SearchResult]:
    from ddgs import DDGS

    if not _legacy_ddgs_allowed(backend):
        raise ValueError("Legacy DDGS backend disabled")

    with DDGS(timeout=timeout_seconds) as ddgs:
        text_signature = inspect.signature(ddgs.text)
        include_backend = _signature_accepts_parameter(text_signature, "backend")
        if not include_backend and not _legacy_ddgs_allowed("auto"):
            raise ValueError("DDGS cannot enforce disabled backends")
        kwargs = _ddgs_text_kwargs(request, backend, include_backend=include_backend)
        LOGGER.debug(
            "Running ddgs search with backend=%s",
            backend if include_backend else "unsupported",
        )
        try:
            raw_results = ddgs.text(request.query, **kwargs)
        except TypeError as exc:
            if (include_backend and "backend" in str(exc)
                    and _legacy_ddgs_allowed("auto")):
                kwargs.pop("backend", None)
                LOGGER.debug("Retrying ddgs search without backend parameter")
                raw_results = ddgs.text(request.query, **kwargs)
            else:
                raise
    results = [_result_from_payload(result) for result in raw_results or []
               if isinstance(result, dict)]
    valid_results = [result for result in results if result is not None]
    return _dedupe_results(valid_results)


def parse_duckduckgo_html_results(html: str, max_results: int) -> list[SearchResult]:
    from mcp_prism_search.providers.ddg_html import parse_results

    return parse_results(html, max_results)


async def _search_with_duckduckgo_html(
    request: SearchRequest,
    timeout_seconds: int,
) -> list[SearchResult]:
    params: dict[str, str | int] = {
        "q": request.query,
        "kp": HTML_SAFE_SEARCH[request.safe_search],
    }
    time_filter_code = TIME_FILTERS.get(request.time_filter or "")
    if time_filter_code:
        params["df"] = time_filter_code
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
        headers=SEARCH_HEADERS,
    ) as client:
        response = await client.get(DUCKDUCKGO_HTML_URL, params=params)
        response.raise_for_status()
    return parse_duckduckgo_html_results(response.text, provider_request_size(request))


def _cache_payload(
    request: SearchRequest,
    *,
    provider: str,
    ddgs_backend: str | None,
) -> dict[str, Any]:
    return request.to_cache_payload(_provider=provider, _ddgs_backend=ddgs_backend)


def _cacheable_search_response(response: SearchResponse) -> dict[str, Any]:
    return response.model_copy(update={"cached": False}).model_dump(mode="json")


def apply_search_domain_controls(
    results: list[SearchResult],
    request: SearchRequest,
) -> list[SearchResult]:
    filtered_results = apply_domain_controls(
        results,
        get_url=lambda result: result.url,
        allowed_domains=request.allowed_domains,
        blocked_domains=request.blocked_domains,
        preferred_domains=request.preferred_domains,
    )
    return filtered_results[: request.max_results]


async def ddg_search(
    query: str,
    max_results: int | None = None,
    search_window: int | None = None,
    safe_search: str | None = None,
    time_filter: str | None = None,
    blocked_domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    preferred_domains: list[str] | None = None,
    *,
    cache: JsonFileCache | None = None,
) -> SearchResponse:
    defaults = effective_defaults()
    request = SearchRequest(
        query=query,
        search_window=search_window,
        **search_parameters(
            defaults, max_results=max_results, safe_search=safe_search,
            time_filter=time_filter, blocked_domains=blocked_domains,
            allowed_domains=allowed_domains, preferred_domains=preferred_domains,
        ),
    )
    cache = cache or build_search_cache()
    provider = cast(SearchProvider, get_search_provider())
    ddgs_backend = get_ddgs_backend() if provider in {"ddgs", "auto"} else None
    payload = _cache_payload(request, provider=provider, ddgs_backend=ddgs_backend)
    from mcp_prism_search import runtime

    cached_value = cache.get(payload, ttl_seconds=search_ttl_for_query(request.query))
    if cached_value is not None:
        try:
            cached = SearchResponse.model_validate(cached_value)
            valid_providers = {"duckduckgo_html", "ddgs"} if provider == "auto" else {provider}
            if (cached.query.strip() == request.query and cached.provider in valid_providers
                    and not cached.error and not cached.answers and not cached.answer_attempts
                    and (cached.provider != "duckduckgo_html" or runtime.enabled("ddg_html"))
                    and (cached.provider != "ddgs" or _legacy_ddgs_allowed(
                        ddgs_backend or DEFAULT_DDGS_BACKEND
                    ))
                    and _final_results(cached.results, request) == cached.results):
                return cached.model_copy(update={"cached": True})
            cache.delete(payload)
        except Exception:  # noqa: BLE001 - poisoned/stale cache must not break the tool.
            cache.delete(payload)

    timeout_seconds = get_ddg_timeout_seconds()
    provider_errors: list[str] = []
    provider_attempts: list[str] = []

    if provider in {"duckduckgo_html", "auto"} and not runtime.enabled("ddg_html"):
        provider_errors.append("DuckDuckGo HTML provider disabled")
    if provider in {"duckduckgo_html", "auto"} and runtime.enabled("ddg_html"):
        try:
            LOGGER.debug("Running DuckDuckGo HTML search")
            html_results = apply_search_domain_controls(
                rank_results(await _search_with_duckduckgo_html(request, timeout_seconds),
                             request.query),
                request,
            )
            if html_results:
                response = SearchResponse(
                    query=request.query,
                    provider="duckduckgo_html",
                    results=html_results,
                    cached=False,
                    error=None,
                    provider_attempts=[f"duckduckgo_html: ok ({len(html_results)})"],
                )
                cache.set(payload, _cacheable_search_response(response))
                return response
            provider_attempts.append("duckduckgo_html: empty")
            provider_errors.append("duckduckgo_html returned no results")
        except Exception as exc:  # noqa: BLE001 - provider failures must not crash the server.
            provider_attempts.append("duckduckgo_html: error")
            provider_errors.append(f"duckduckgo_html failed: {type(exc).__name__}")

    if provider in {"ddgs", "auto"}:
        backend = ddgs_backend or DEFAULT_DDGS_BACKEND
        if not _legacy_ddgs_allowed(backend):
            provider_errors.append("Legacy DDGS backend disabled")
            return SearchResponse(
                query=request.query, provider=provider, results=[], cached=False,
                error="; ".join(provider_errors), provider_attempts=provider_attempts,
            )
        try:
            ddgs_results = apply_search_domain_controls(
                rank_results(await asyncio.to_thread(
                    _search_with_ddgs, request, timeout_seconds, backend), request.query),
                request,
            )
            if ddgs_results:
                response = SearchResponse(
                    query=request.query,
                    provider="ddgs",
                    results=ddgs_results,
                    cached=False,
                    error=None,
                    provider_attempts=[
                        *provider_attempts,
                        f"ddgs ({backend}): ok ({len(ddgs_results)})",
                    ],
                )
                cache.set(payload, _cacheable_search_response(response))
                return response
            provider_attempts.append(f"ddgs ({backend}): empty")
            provider_errors.append(f"ddgs backend {backend} returned no results")
        except Exception as exc:  # noqa: BLE001 - provider failures must not crash the server.
            provider_attempts.append(f"ddgs ({backend}): error")
            provider_errors.append(f"ddgs backend {backend} failed: {type(exc).__name__}")

    return SearchResponse(
        query=request.query,
        provider=provider,
        results=[],
        cached=False,
        error="; ".join(provider_errors) or "Search failed",
        provider_attempts=provider_attempts,
    )


async def _search_one(
    name: str, request: SearchRequest, count: int, timeout: int,
    answer_sink=None, answer_status=None,
):
    """Return results and an accurate, credential-free attempt status."""
    from time import monotonic

    from mcp_prism_search import runtime
    from mcp_prism_search.providers import record, run_provider

    start = monotonic()

    try:
        raw = None
        if answer_sink is not None:
            from mcp_prism_search import ai_search, native

            if name in ai_search.COMBINED:
                try:
                    raw, answer = await ai_search.combined_search(name, request, count, timeout)
                    if answer:
                        answer_sink[name] = ai_search.compact_answer(answer)
                    answer_status[name] = "ok" if answer else "no answer returned"
                except native.NativeError as exc:
                    answer_status[name] = str(exc)
                # If combined retrieval failed or was empty, preserve ordinary search.
                if not raw:
                    raw = None
        if raw is None:
            async with asyncio.timeout(timeout):
                raw = await run_provider(name, request, count, timeout)
        valid = rank_results(raw, request.query)
        filtered = apply_domain_controls(
            valid,
            get_url=lambda item: item.url,
            allowed_domains=request.allowed_domains,
            blocked_domains=request.blocked_domains,
            preferred_domains=[],
        )
        record(name, valid, None)
        runtime.record_metric(name, 'ok' if valid else 'empty', monotonic() - start, len(filtered))
        if answer_sink is not None and name in ai_search.SEPARATE and filtered:
            try:
                answer = await ai_search.separate_answer(name, request.query, timeout)
                answer_sink[name] = ai_search.compact_answer(answer)
                answer_status[name] = "ok"
            except (native.NativeError, TimeoutError) as exc:
                answer_status[name] = str(exc) or "timeout"
            except Exception as exc:  # noqa: BLE001 - preserve successful retrieval.
                answer_status[name] = type(exc).__name__
        return filtered, f"{name}: ok ({len(filtered)})" if filtered else f"{name}: empty"
    except asyncio.CancelledError:
        runtime.record_metric(name, "cancelled", monotonic() - start)
        raise
    except (TimeoutError, httpx.TimeoutException):
        status = "timeout"
    except httpx.HTTPStatusError as exc:
        status = f"HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001 - isolate upstream failures.
        status = type(exc).__name__
    record(name, None, status)
    runtime.record_metric(name, status, monotonic() - start)
    return [], f"{name}: error ({status})"


def _final_results(results: list[SearchResult], request: SearchRequest) -> list[SearchResult]:
    return apply_search_domain_controls(
        rank_results(results, request.query), request
    )


async def aggregated_search(
    query: str,
    *,
    provider: str | None = None,
    providers: list[str] | None = None,
    max_results: int | None = None,
    search_window: int | None = None,
    safe_search: str | None = None,
    time_filter: str | None = None,
    blocked_domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    preferred_domains: list[str] | None = None,
    kind: str = "general",
    cache: JsonFileCache | None = None,
    include_answers: bool | None = None,
) -> SearchResponse:
    """Search one provider, a health-ordered fallback chain or a parallel selection."""
    from mcp_prism_search import runtime
    from mcp_prism_search.providers import resolve_chain

    defaults = effective_defaults()
    inherited_selection = provider is None and providers is None and kind == "general"
    if providers is not None:
        provider = provider or "auto"
    elif provider is None:
        provider = defaults.vertical_source if kind == "vertical" else defaults.search_mode
        if provider == "selected":
            from mcp_prism_search.providers import available_providers

            available = available_providers("general")
            providers = [name for name in defaults.search_providers if name in available]
            provider = "auto"
    request = SearchRequest(
        query=query,
        search_window=search_window,
        **search_parameters(
            defaults, max_results=max_results, safe_search=safe_search,
            time_filter=time_filter, blocked_domains=blocked_domains,
            allowed_domains=allowed_domains, preferred_domains=preferred_domains,
            vertical=kind == "vertical",
        ),
    )
    cache = cache or build_search_cache()
    attempts: list[str] = []
    answers = {}
    answer_status = {}
    mode = "selected" if providers is not None else provider

    def response(results, error=None, actual=None):
        return SearchResponse(
            query=request.query, provider=actual or mode, results=results, cached=False,
            error=error, provider_attempts=attempts,
            answers=[answers[n] for n in answer_status if n in answers],
            answer_attempts=[f"{n}: {v}" for n, v in answer_status.items()],
        )

    try:
        if inherited_selection and providers == []:
            raise ValueError("No default selected providers are available")
        if providers is not None:
            if provider not in {"auto", "all"} or not providers:
                raise ValueError("Provide a nonempty providers list without a single provider.")
            chain = list(dict.fromkeys(providers))
            for name in chain:
                resolve_chain(name, kind)
                if name in {"auto", "all", "keyless", "selected"}:
                    raise ValueError("Select explicit provider names in providers.")
        else:
            chain = resolve_chain(provider, kind)
    except ValueError as exc:
        return response([], str(exc))

    if not chain:
        return response([], "No search providers configured for this environment")

    from mcp_prism_search.ai_search import eligible

    enhanced = eligible(chain, request, include_answers) if kind == "general" else set()
    # An administrator who opted into AI answers expects auto to actually try
    # an eligible provider first. Retain health order within both groups.
    if provider == "auto" and providers is None and enhanced:
        chain.sort(key=lambda name: name not in enhanced)
    attempted_enhanced = set()

    async def run_one(name, count, timeout):
        if name in enhanced:
            attempted_enhanced.add(name)
            return await _search_one(name, request, count, timeout, answers, answer_status)
        return await _search_one(name, request, count, timeout)

    parallel = provider == "all" or providers is not None
    payload = request.to_cache_payload(
        _aggregator_version=2, _mode=mode, _kind=kind,
        _providers=sorted(chain) if mode in {"auto", "keyless"} else chain,
    )
    # A cached retrieval-only winner must not suppress the requested AI preference.
    cached = None if enhanced else cache.get(payload, ttl_seconds=search_ttl_for_query(query))
    if cached is not None:
        try:
            result = SearchResponse.model_validate(cached).model_copy(update={"cached": True})
            # Only retrieval results from the current request and allowed chain
            # are cacheable. Older or corrupted envelopes must not return AI.
            if (result.query.strip() == request.query and result.results
                    and not result.error and not result.answers and not result.answer_attempts
                    and result.provider in {*chain, mode}
                    and _final_results(result.results, request) == result.results):
                runtime.record_cache(True)
                return result
            cache.delete(payload)
        except ValueError:
            cache.delete(payload)

    if not enhanced:
        runtime.record_cache(False)
    timeout = get_ddg_timeout_seconds()
    count = provider_request_size(request)
    actual = None
    merged: list[SearchResult] = []
    if parallel:
        batches = await asyncio.gather(
            *(run_one(name, count, timeout) for name in chain)
        )
        attempts.extend(status for _, status in batches)
        # Interleave before stable ranking so tied scores preserve source diversity.
        for index in range(max((len(rows) for rows, _ in batches), default=0)):
            merged.extend(rows[index] for rows, _ in batches if index < len(rows))
    else:
        for name in chain:
            rows, status = await run_one(name, count, timeout)
            attempts.append(status)
            if rows:
                merged = rows
                actual = name
                break

    results = _final_results(merged, request)
    answer_status = {n: answer_status[n] for n in chain if n in answer_status}
    result = response(
        results, None if results else "; ".join(attempts) or "No search results", actual
    )
    # Do not cache degraded parallel responses or failures.
    if enhanced and not attempted_enhanced:
        runtime.record_cache(False)
    if (not attempted_enhanced and results
            and (not parallel or all(": ok (" in s for s in attempts))):
        cache.set(payload, _cacheable_search_response(result))
    return result
