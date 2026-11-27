"""MCP server entrypoint (mcp SDK 2.x, MCPServer API)."""

from __future__ import annotations

import asyncio
import logging
import os
from hmac import compare_digest
from typing import Annotated, Any, cast

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_prism_search import __version__, observability, runtime
from mcp_prism_search import cache as cache_ops
from mcp_prism_search.cache import (
    _get_env_bool,
    get_cache_prune_config,
    prune_cache,
)
from mcp_prism_search.defaults import Recency, effective_defaults
from mcp_prism_search.fetch import ddg_deep_search as perform_deep_search
from mcp_prism_search.fetch import web_fetch as perform_web_fetch
from mcp_prism_search.models import (
    ARGUMENT_DESCRIPTIONS,
    CacheNamespace,
    DeepSearchResponse,
    FetchResponse,
    SafeSearch,
    SearchResponse,
)
from mcp_prism_search.native import (
    AnswerProvider,
    Effort,
    NativeError,
    NativeResponse,
    ResearchProvider,
    synthesize,
)
from mcp_prism_search.providers import general_provider_ids
from mcp_prism_search.search import aggregated_search as perform_aggregated_search
from mcp_prism_search.search import ddg_search as perform_ddg_search

LOGGER = logging.getLogger(__name__)

KNOWN_PLACEHOLDER_TOKENS = {"change-me-now", "changeme", "secret", "token"}
HEALTH_PATH = "/health"


class KnownPlaceholderTokenError(RuntimeError):
    """Raised when MCP_AUTH_TOKEN is a publicly known placeholder."""


def _tool_annotations(
    *, read_only: bool = False, destructive: bool = False, open_world: bool = True
) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=destructive,
        open_world_hint=open_world,
    )


ACCEPTED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _validated_log_level() -> str:
    level = os.getenv("MCP_LOG_LEVEL", "INFO").strip().upper()
    if level not in ACCEPTED_LOG_LEVELS:
        raise RuntimeError(
            f"Invalid MCP_LOG_LEVEL={level!r}; accepted values are "
            + ", ".join(sorted(ACCEPTED_LOG_LEVELS))
        )
    return cast(Any, level)


mcp = MCPServer(
    name="mcp-prism-search",
    version=__version__,
    instructions=(
        "Multi-provider web search and safe webpage fetching tools. "
        "Use web_search with just a query to inherit server defaults, or explicitly override "
        "provider and other options. Authorized provider AI may accompany search; "
        "include_answers=false opts out. Use web_fetch to verify sources. "
        "Standalone web_answer and web_research require explicit cost consent. "
        "Legacy ddg tools remain available for DuckDuckGo-specific workflows."
    ),
    log_level=_validated_log_level(),
)


@mcp.custom_route(HEALTH_PATH, methods=["GET"])
async def _health_route(_request: Any) -> Any:
    from starlette.responses import JSONResponse

    return JSONResponse({"ok": True})



class BearerTokenAuthMiddleware:
    """ASGI middleware that requires one configured bearer token."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected_authorization = f"Bearer {token}"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type == "http" and scope.get("path") == HEALTH_PATH:
            # Unauthenticated liveness endpoint for load balancers and monitors.
            await self.app(scope, receive, send)
            return
        if scope_type != "http":
            # Fail closed: unknown or websocket scopes never bypass the token.
            raise RuntimeError(
                f"BearerTokenAuthMiddleware refuses to serve scope type {scope_type!r}"
            )

        authorization = _authorization_header(scope)
        if authorization is not None:
            scheme, separator, credential = authorization.partition(" ")
            if separator and scheme.lower() == "bearer":
                authorization = "Bearer " + credential.lstrip(" ")
        if authorization is None or not _safe_token_match(
            authorization, self.expected_authorization
        ):
            observability.emit("auth", "denied")
            await _send_unauthorized(send)
            return

        await self.app(scope, receive, send)


def _safe_token_match(left: str, right: str) -> bool:
    try:
        return compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except (TypeError, UnicodeEncodeError):
        return False


def _authorization_header(scope: dict[str, Any]) -> str | None:
    values = [value for name, value in scope.get("headers", [])
              if name.lower() == b"authorization"]
    if len(values) != 1:
        return None
    return values[0].decode("latin-1")


async def _send_unauthorized(send: Any) -> None:
    body = b"Unauthorized"
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw_value = os.getenv(name, "")
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    return values or default


def _has_wildcard(values: list[str]) -> bool:
    return "*" in values


def _http_auth_token() -> str | None:
    token = os.getenv("MCP_AUTH_TOKEN")
    if token is None or not token.strip():
        return None
    return token.strip()


class HttpSettings:
    def __init__(
        self,
        host: str,
        port: int,
        transport_security: TransportSecuritySettings,
    ) -> None:
        self.host = host
        self.port = port
        self.transport_security = transport_security


def _build_transport_security_settings(
    allowed_hosts: list[str],
    allowed_origins: list[str],
) -> TransportSecuritySettings:
    wildcard_requested = _has_wildcard(allowed_hosts) or _has_wildcard(allowed_origins)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=not wildcard_requested,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _configure_http_settings() -> HttpSettings:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    raw_port = os.getenv("MCP_PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid MCP_PORT={raw_port!r}; expected an integer port number"
        ) from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"Invalid MCP_PORT={raw_port!r}; out of range")
    allowed_hosts = _csv_env("MCP_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
    allowed_origins = _csv_env("MCP_ALLOWED_ORIGINS", ["http://localhost:5173"])
    return HttpSettings(
        host=host,
        port=port,
        transport_security=_build_transport_security_settings(
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )


async def _cache_prune_loop() -> None:
    """Periodic cache pruning in a worker thread: keeps the event loop free
    and replaces the old on-access synchronous prune."""
    interval = max(
        60, get_cache_prune_config().prune_interval_seconds
    )
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(prune_cache)
        except Exception:  # noqa: BLE001 - pruning must never break the server.
            LOGGER.warning("Background cache pruning failed", exc_info=True)


async def _run_stdio_async() -> None:
    prune_task = asyncio.create_task(_cache_prune_loop())
    try:
        await mcp.run_stdio_async()
    finally:
        prune_task.cancel()
        await asyncio.gather(prune_task, return_exceptions=True)


async def _run_http_async(
    settings: HttpSettings | None = None,
) -> None:
    import uvicorn

    settings = settings or _configure_http_settings()
    auth_token = _http_auth_token()
    if auth_token is None:
        if not _get_env_bool("MCP_ALLOW_UNAUTHENTICATED_HTTP", False):
            raise RuntimeError(
                "MCP HTTP transport requires MCP_AUTH_TOKEN to be set. "
                "Set a strong random token, or set MCP_ALLOW_UNAUTHENTICATED_HTTP=1 "
                "to explicitly run without authentication."
            )
        LOGGER.warning(
            "MCP HTTP transport is running without MCP_AUTH_TOKEN; "
            "all HTTP requests are unauthenticated."
        )
    elif auth_token.lower() in KNOWN_PLACEHOLDER_TOKENS:
        raise KnownPlaceholderTokenError(
            f"MCP_AUTH_TOKEN is set to the publicly known placeholder {auth_token!r}. "
            "Set a strong random token before exposing the server over HTTP."
        )

    starlette_app = mcp.streamable_http_app(
        transport_security=settings.transport_security,
        host=settings.host,
    )
    if auth_token is not None:
        starlette_app.add_middleware(BearerTokenAuthMiddleware, token=auth_token)

    config = uvicorn.Config(
        starlette_app,
        host=settings.host,
        port=settings.port,
        log_level=os.getenv("MCP_LOG_LEVEL", "INFO").lower(),
        log_config=None, access_log=False,
    )
    server = uvicorn.Server(config)
    prune_task = asyncio.create_task(_cache_prune_loop())
    try:
        await _serve_http(server, settings)
    finally:
        prune_task.cancel()
        await asyncio.gather(prune_task, return_exceptions=True)


async def _serve_http(server, settings: HttpSettings) -> None:
    import uvicorn

    if not _get_env_bool("MCP_WEBUI_ENABLED", False):
        await server.serve()
        return

    from contextlib import nullcontext

    from mcp_prism_search.panel import configured_app

    panel_port = int(os.getenv("MCP_WEBUI_PORT", "49318"))
    panel_host = os.getenv("MCP_WEBUI_HOST", "127.0.0.1")
    if panel_port == settings.port or not 1 <= panel_port <= 65535:
        raise RuntimeError("Invalid MCP_WEBUI_PORT")
    admin = uvicorn.Server(uvicorn.Config(
        configured_app(), host=panel_host, port=panel_port, access_log=False, log_config=None,
    ))
    # Only the primary server owns signal handlers.
    admin.capture_signals = nullcontext
    task = asyncio.create_task(admin.serve())
    primary = asyncio.create_task(server.serve())
    try:
        await asyncio.wait({task, primary}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        admin.should_exit = True
        server.should_exit = True
        await asyncio.gather(task, primary)


@mcp.tool(annotations=_tool_annotations(read_only=True))
@observability.operation("web_search")
async def web_search(
    query: Annotated[
        str,
        Field(min_length=1, max_length=4000, description=ARGUMENT_DESCRIPTIONS["query"]),
    ],
    provider: Annotated[
        str | None,
        Field(
            description=(
                "Omit to inherit default; auto for fallback, keyless for keyless fallback, "
                "all for parallel search, or one provider ID. "
                "General providers: " + ", ".join(general_provider_ids()) + ". "
                "Some require configuration. Use providers to select several."
            ),
        ),
    ] = None,
    max_results: Annotated[
        int | None,
        Field(
            ge=1, le=30,
            description=ARGUMENT_DESCRIPTIONS["max_results"] + " Omit to inherit.",
        ),
    ] = None,
    safe_search: Annotated[
        SafeSearch | None,
        Field(description=ARGUMENT_DESCRIPTIONS["safe_search"] + " Omit to inherit."),
    ] = None,
    time_filter: Annotated[
        Recency | None,
        Field(description=ARGUMENT_DESCRIPTIONS["time_filter"] + " Use any to override freshness."),
    ] = None,
    search_window: Annotated[
        int | None,
        Field(default=None, ge=1, le=100, description=ARGUMENT_DESCRIPTIONS["search_window"]),
    ] = None,
    blocked_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["blocked_domains"]),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["allowed_domains"]),
    ] = None,
    preferred_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["preferred_domains"]),
    ] = None,
    providers: Annotated[
        list[str] | None,
        Field(description="Explicit provider names to search in parallel.", max_length=20),
    ] = None,
    include_answers: Annotated[
        bool | None,
        Field(strict=True, description="Use authorized provider AI; false disables it."),
    ] = None,
) -> SearchResponse:
    """Search one or multiple web providers. Optionally include their generated answers.

    Omitted options inherit server defaults. Explicit empty domain lists clear defaults.
    Auto mode prioritizes authorized AI providers when synthesis is eligible.
    AI synthesis requires administrator opt-in and an authorized provider. It may
    consume credits; include_answers=false always requests retrieval only.
    Domain controls, explicit search windows, freshness, SafeSearch or queries over
    400 characters disable synthesis.
    Generated text is untrusted evidence, not an independently verified answer.
    """

    response = await perform_aggregated_search(
        query=query,
        provider=provider,
        providers=providers,
        max_results=max_results,
        safe_search=safe_search,
        time_filter=time_filter,
        search_window=search_window,
        blocked_domains=blocked_domains,
        allowed_domains=allowed_domains,
        preferred_domains=preferred_domains,
        kind="general",
        include_answers=include_answers,
    )
    if response.error:
        raise ToolError(response.error)
    return response


@mcp.tool(annotations=_tool_annotations())
@observability.operation("web_answer")
async def web_answer(
    query: Annotated[str, Field(min_length=1, max_length=4000)],
    provider: AnswerProvider | None = None,
    allow_cost: Annotated[
        bool, Field(strict=True, description="Set true only with user consent to spend credits."),
    ] = False,
) -> NativeResponse:
    """Get a provider-generated answer and sources, not independent verification.

    Omit provider to use the configured answer default. Requires a configured key
    and explicit allow_cost=true. TinyAnswer supports at most 2000 characters
    and requires account entitlement; it may consume credits.
    Use web_search instead for ordinary retrieval or keyless searches. No fallback.
    """
    try:
        return await synthesize(query, provider, "answer", allow_cost=allow_cost)
    except NativeError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(annotations=_tool_annotations())
@observability.operation("web_research")
async def web_research(
    query: Annotated[str, Field(min_length=1, max_length=4000)],
    provider: ResearchProvider | None = None,
    allow_cost: Annotated[
        bool, Field(strict=True, description="Set true only with user consent to spend credits."),
    ] = False,
    effort: Effort | None = None,
) -> NativeResponse:
    """Delegate multi-step research to a configured provider; returns synthesis and sources.

    Omit provider and effort to use configured defaults. Requires explicit cost consent.
    Exa, Tavily and Jina support
    lite/deep; You.com supports lite/standard/deep/exhaustive; Brave requires deep.
    TinyFish supports deep ($2) or max ($3), up to 45 minutes. Use a client
    timeout above 46 minutes for TinyResearch. TinyFish synthesis is explicit only.
    Review provider pricing before increasing effort.
    No automatic retries or provider fallback.
    """
    try:
        return await synthesize(query, provider, "research", allow_cost=allow_cost, effort=effort)
    except NativeError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(annotations=_tool_annotations(read_only=True))
@observability.operation("vertical_search")
async def vertical_search(
    query: Annotated[
        str,
        Field(min_length=1, max_length=4000, description=ARGUMENT_DESCRIPTIONS["query"]),
    ],
    source: Annotated[
        str | None,
        Field(
            description=(
                "Vertical search source: wikipedia (encyclopedic) or "
                "hackernews (tech stories)."
            ),
        ),
    ] = None,
    max_results: Annotated[
        int | None,
        Field(
            ge=1, le=20,
            description=ARGUMENT_DESCRIPTIONS["max_results"] + " Omit to inherit.",
        ),
    ] = None,
) -> SearchResponse:
    """Search one keyless vertical source (wikipedia, hackernews)."""

    response = await perform_aggregated_search(
        query=query,
        provider=source,
        max_results=max_results,
        kind="vertical",
    )
    if response.error:
        raise ToolError(response.error)
    return response


@mcp.tool(annotations=_tool_annotations(read_only=True))
@observability.operation("ddg_search")
async def ddg_search(
    query: Annotated[
        str, Field(min_length=1, max_length=4000, description=ARGUMENT_DESCRIPTIONS["query"]),
    ],
    max_results: Annotated[
        int | None,
        Field(
            ge=1, le=30,
            description=ARGUMENT_DESCRIPTIONS["max_results"] + " Omit to inherit.",
        ),
    ] = None,
    search_window: Annotated[
        int | None,
        Field(ge=1, le=100, description=ARGUMENT_DESCRIPTIONS["search_window"]),
    ] = None,
    safe_search: Annotated[
        SafeSearch | None,
        Field(description=ARGUMENT_DESCRIPTIONS["safe_search"] + " Omit to inherit."),
    ] = None,
    time_filter: Annotated[
        Recency | None,
        Field(description=ARGUMENT_DESCRIPTIONS["time_filter"] + " Use any to override freshness."),
    ] = None,
    blocked_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["blocked_domains"]),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["allowed_domains"]),
    ] = None,
    preferred_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["preferred_domains"]),
    ] = None,
) -> SearchResponse:
    """Legacy DuckDuckGo search. Prefer web_search unless DuckDuckGo-specific behavior is needed."""

    response = await perform_ddg_search(
        query=query,
        max_results=max_results,
        search_window=search_window,
        safe_search=safe_search,
        time_filter=time_filter,
        blocked_domains=blocked_domains,
        allowed_domains=allowed_domains,
        preferred_domains=preferred_domains,
    )
    if response.error:
        raise ToolError(response.error)
    return response


@mcp.tool(annotations=_tool_annotations(read_only=True))
@observability.operation("web_fetch")
async def web_fetch(
    url: Annotated[
        str, Field(min_length=1, max_length=8192, description=ARGUMENT_DESCRIPTIONS["url"]),
    ],
    max_chars: Annotated[
        int | None,
        Field(
            ge=1000, le=50000,
            description=ARGUMENT_DESCRIPTIONS["max_chars"] + " Omit to inherit.",
        ),
    ] = None,
) -> FetchResponse:
    """Fetch one HTTP(S) page safely and return extracted text."""

    response = await perform_web_fetch(url=url, max_chars=max_chars)
    if not response.success:
        raise ToolError(response.error or "Fetch failed")
    return response


@mcp.tool(annotations=_tool_annotations(read_only=True))
@observability.operation("ddg_deep_search")
async def ddg_deep_search(
    query: Annotated[
        str, Field(min_length=1, max_length=4000, description=ARGUMENT_DESCRIPTIONS["query"]),
    ],
    max_results: Annotated[
        int | None,
        Field(
            ge=1, le=30,
            description=ARGUMENT_DESCRIPTIONS["max_results"] + " Omit to inherit.",
        ),
    ] = None,
    search_window: Annotated[
        int | None,
        Field(ge=1, le=100, description=ARGUMENT_DESCRIPTIONS["search_window"]),
    ] = None,
    max_pages: Annotated[
        int | None,
        Field(
            ge=1, le=10,
            description=ARGUMENT_DESCRIPTIONS["max_pages"] + " Omit to inherit.",
        ),
    ] = None,
    max_chars_per_page: Annotated[
        int | None,
        Field(
            ge=1000, le=50000,
            description=ARGUMENT_DESCRIPTIONS["max_chars_per_page"] + " Omit to inherit.",
        ),
    ] = None,
    safe_search: Annotated[
        SafeSearch | None,
        Field(description=ARGUMENT_DESCRIPTIONS["safe_search"] + " Omit to inherit."),
    ] = None,
    time_filter: Annotated[
        Recency | None,
        Field(description=ARGUMENT_DESCRIPTIONS["time_filter"] + " Use any to override freshness."),
    ] = None,
    blocked_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["blocked_domains"]),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["allowed_domains"]),
    ] = None,
    preferred_domains: Annotated[
        list[str] | None,
        Field(max_length=50, description=ARGUMENT_DESCRIPTIONS["preferred_domains"]),
    ] = None,
    max_concurrency: Annotated[
        int | None,
        Field(
            ge=1, le=12,
            description=ARGUMENT_DESCRIPTIONS["max_concurrency"] + " Omit to inherit.",
        ),
    ] = None,
    ctx: Context | None = None,
) -> DeepSearchResponse:
    """Legacy DuckDuckGo search plus parallel webpage extraction. No AI synthesis."""

    async def _report_progress(progress: float, total: float | None) -> None:
        if ctx is not None:
            await ctx.report_progress(progress=progress, total=total)

    response = await perform_deep_search(
        query=query,
        max_results=max_results,
        search_window=search_window,
        max_pages=max_pages,
        max_chars_per_page=max_chars_per_page,
        safe_search=safe_search,
        time_filter=time_filter,
        blocked_domains=blocked_domains,
        allowed_domains=allowed_domains,
        preferred_domains=preferred_domains,
        max_concurrency=max_concurrency,
        progress_callback=_report_progress,
    )
    if response.search_error and not response.pages:
        raise ToolError(response.search_error)
    return response


@mcp.tool(annotations=_tool_annotations(read_only=True, open_world=False))
@observability.operation("cache_stats")
async def cache_stats() -> dict[str, Any]:
    """Return cache file counts and byte totals for each cache namespace."""

    return (await asyncio.to_thread(cache_ops.get_cache_stats)).to_dict()


@mcp.tool(annotations=_tool_annotations(destructive=True, open_world=False))
@observability.operation("cache_prune")
async def cache_prune(
    expired_only: Annotated[
        bool,
        Field(strict=True, description=ARGUMENT_DESCRIPTIONS["expired_only"]),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(strict=True, description=ARGUMENT_DESCRIPTIONS["dry_run"]),
    ] = False,
) -> dict[str, Any]:
    """Prune expired entries, stale temporary files and oversized entries.

    Full pruning also enforces configured age and size limits. Expired-only
    pruning preserves fresh entries. Dry-run never deletes files.
    """

    return (await asyncio.to_thread(
        cache_ops.prune_cache, expired_only=expired_only, dry_run=dry_run
    )).to_dict()


@mcp.tool(annotations=_tool_annotations(destructive=True, open_world=False))
@observability.operation("cache_clear")
async def cache_clear(
    namespace: Annotated[
        CacheNamespace,
        Field(description=ARGUMENT_DESCRIPTIONS["namespace"]),
    ],
    confirm: Annotated[
        bool,
        Field(strict=True, description=ARGUMENT_DESCRIPTIONS["confirm"]),
    ],
) -> dict[str, Any]:
    """Clear one cache namespace, or all namespaces, when confirm is true."""

    return (await asyncio.to_thread(
        cache_ops.clear_cache, namespace=namespace, confirm=confirm
    )).to_dict()


def _prune_cache_on_startup() -> None:
    try:
        stats = cache_ops.prune_cache_on_startup()
    except Exception:  # noqa: BLE001 - cache pruning must not block server startup.
        LOGGER.warning("Startup cache pruning failed", exc_info=True)
        return
    if stats is not None and stats.deleted_files:
        LOGGER.info(
            "Pruned %s cache files on startup (%s bytes)",
            stats.deleted_files,
            stats.deleted_bytes,
        )


def main() -> None:
    previous = runtime.state()
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    directory = (
        runtime.state_directory() / "logs"
        if transport in {"http", "streamable-http"} and _get_env_bool("MCP_WEBUI_ENABLED", False)
        else None
    )
    observability.configure(_validated_log_level(), directory)
    observability.emit("lifecycle", "started", operation="server_start")
    try:
        observability.configure_otel()
        runtime.install(runtime.configured_store())
        effective_defaults()  # Fail clearly on invalid environment settings before serving.
        _run()
    finally:
        observability.emit("lifecycle", "stopped", operation="server_stop")
        observability.close()
        runtime.install(previous)


def _run() -> None:
    _prune_cache_on_startup()
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in {"http", "streamable-http"}:
        # NOTE: mcp.run("streamable-http") cannot inject the bearer-token
        # middleware, so HTTP mode wires the Starlette app by hand here.
        settings = _configure_http_settings()
        asyncio.run(_run_http_async(settings))
        return
    if transport != "stdio":
        raise RuntimeError(
            f"Invalid MCP_TRANSPORT={transport!r}; accepted values are 'stdio' and 'http'"
        )

    if _get_env_bool("MCP_WEBUI_ENABLED", False):
        raise RuntimeError("Web dashboard requires MCP_TRANSPORT=http")
    asyncio.run(_run_stdio_async())


if __name__ == "__main__":
    main()
