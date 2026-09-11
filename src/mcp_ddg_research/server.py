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

from mcp_ddg_research import cache as cache_ops
from mcp_ddg_research.cache import (
    _get_env_bool,
    get_cache_prune_config,
    prune_cache,
)
from mcp_ddg_research.fetch import ddg_deep_search as perform_deep_search
from mcp_ddg_research.fetch import web_fetch as perform_web_fetch
from mcp_ddg_research.models import (
    ARGUMENT_DESCRIPTIONS,
    CacheNamespace,
    DeepSearchResponse,
    FetchResponse,
    SafeSearch,
    SearchResponse,
    TimeFilter,
)
from mcp_ddg_research.search import ddg_search as perform_ddg_search

LOGGER = logging.getLogger(__name__)

KNOWN_PLACEHOLDER_TOKENS = {"change-me-now", "changeme", "secret", "token"}
ACCEPTED_TRANSPORTS = {"stdio", "http", "streamable-http"}
HEALTH_PATH = "/health"
_BACKGROUND_TASKS: set[asyncio.Task] = set()


class KnownPlaceholderTokenError(RuntimeError):
    """Raised when MCP_AUTH_TOKEN is a publicly known placeholder."""


def _tool_annotations(
    *, read_only: bool = False, destructive: bool = False
) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=destructive,
        open_world_hint=True,
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
    name="mcp-ddg-research",
    version="0.6.0",
    instructions=(
        "Deterministic DuckDuckGo search and safe webpage fetching tools. "
        "This server does not call LLMs, summarize, or generate reports."
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
        if authorization is None or not _safe_token_match(
            authorization, self.expected_authorization
        ):
            await _send_unauthorized(send)
            return

        await self.app(scope, receive, send)


def _safe_token_match(left: str, right: str) -> bool:
    try:
        return compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    except (TypeError, UnicodeEncodeError):
        return False


def _authorization_header(scope: dict[str, Any]) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            return value.decode("latin-1")
    return None


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


async def _run_http_async(
    settings: HttpSettings | None = None,
) -> None:
    import uvicorn

    settings = settings or _configure_http_settings()
    _BACKGROUND_TASKS.add(asyncio.create_task(_cache_prune_loop()))
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
    )
    server = uvicorn.Server(config)
    await server.serve()


@mcp.tool(annotations=_tool_annotations(read_only=True))
async def ddg_search(
    query: Annotated[str, Field(min_length=1, description=ARGUMENT_DESCRIPTIONS["query"])],
    max_results: Annotated[
        int,
        Field(ge=1, le=30, description=ARGUMENT_DESCRIPTIONS["max_results"]),
    ] = 10,
    search_window: Annotated[
        int | None,
        Field(ge=1, le=100, description=ARGUMENT_DESCRIPTIONS["search_window"]),
    ] = None,
    safe_search: Annotated[
        SafeSearch,
        Field(description=ARGUMENT_DESCRIPTIONS["safe_search"]),
    ] = "off",
    time_filter: Annotated[
        TimeFilter | None,
        Field(description=ARGUMENT_DESCRIPTIONS["time_filter"]),
    ] = None,
    blocked_domains: Annotated[
        list[str] | None,
        Field(description=ARGUMENT_DESCRIPTIONS["blocked_domains"]),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        Field(description=ARGUMENT_DESCRIPTIONS["allowed_domains"]),
    ] = None,
    preferred_domains: Annotated[
        list[str] | None,
        Field(description=ARGUMENT_DESCRIPTIONS["preferred_domains"]),
    ] = None,
) -> SearchResponse:
    """Search DuckDuckGo using the configured provider mode."""

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
async def web_fetch(
    url: Annotated[str, Field(min_length=1, description=ARGUMENT_DESCRIPTIONS["url"])],
    max_chars: Annotated[
        int,
        Field(ge=1000, le=50000, description=ARGUMENT_DESCRIPTIONS["max_chars"]),
    ] = 12000,
) -> FetchResponse:
    """Fetch one HTTP(S) page safely and return extracted text."""

    response = await perform_web_fetch(url=url, max_chars=max_chars)
    if not response.success:
        raise ToolError(response.error or "Fetch failed")
    return response


@mcp.tool(annotations=_tool_annotations(read_only=True))
async def ddg_deep_search(
    query: Annotated[str, Field(min_length=1, description=ARGUMENT_DESCRIPTIONS["query"])],
    max_results: Annotated[
        int,
        Field(ge=1, le=30, description=ARGUMENT_DESCRIPTIONS["max_results"]),
    ] = 10,
    search_window: Annotated[
        int | None,
        Field(ge=1, le=100, description=ARGUMENT_DESCRIPTIONS["search_window"]),
    ] = None,
    max_pages: Annotated[
        int,
        Field(ge=1, le=10, description=ARGUMENT_DESCRIPTIONS["max_pages"]),
    ] = 5,
    max_chars_per_page: Annotated[
        int,
        Field(ge=1000, le=50000, description=ARGUMENT_DESCRIPTIONS["max_chars_per_page"]),
    ] = 12000,
    safe_search: Annotated[
        SafeSearch,
        Field(description=ARGUMENT_DESCRIPTIONS["safe_search"]),
    ] = "off",
    time_filter: Annotated[
        TimeFilter | None,
        Field(description=ARGUMENT_DESCRIPTIONS["time_filter"]),
    ] = None,
    blocked_domains: Annotated[
        list[str] | None,
        Field(description=ARGUMENT_DESCRIPTIONS["blocked_domains"]),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        Field(description=ARGUMENT_DESCRIPTIONS["allowed_domains"]),
    ] = None,
    preferred_domains: Annotated[
        list[str] | None,
        Field(description=ARGUMENT_DESCRIPTIONS["preferred_domains"]),
    ] = None,
    max_concurrency: Annotated[
        int | None,
        Field(ge=1, le=12, description=ARGUMENT_DESCRIPTIONS["max_concurrency"]),
    ] = None,
    ctx: Context | None = None,
) -> DeepSearchResponse:
    """Search once, fetch top pages in parallel, and return raw page text."""

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


@mcp.tool(annotations=_tool_annotations(read_only=True))
async def cache_stats() -> dict:
    """Return cache file counts and byte totals for each cache namespace."""

    return cache_ops.get_cache_stats().to_dict()


@mcp.tool(annotations=_tool_annotations(destructive=True))
async def cache_prune(
    expired_only: Annotated[
        bool,
        Field(description=ARGUMENT_DESCRIPTIONS["expired_only"]),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(description=ARGUMENT_DESCRIPTIONS["dry_run"]),
    ] = False,
) -> dict:
    """Prune expired, corrupt, temporary, and oversized cache files."""

    return cache_ops.prune_cache(expired_only=expired_only, dry_run=dry_run).to_dict()


@mcp.tool(annotations=_tool_annotations(destructive=True))
async def cache_clear(
    namespace: Annotated[
        CacheNamespace,
        Field(description=ARGUMENT_DESCRIPTIONS["namespace"]),
    ],
    confirm: Annotated[
        bool,
        Field(description=ARGUMENT_DESCRIPTIONS["confirm"]),
    ],
) -> dict:
    """Clear one cache namespace, or all namespaces, when confirm is true."""

    return cache_ops.clear_cache(namespace=namespace, confirm=confirm).to_dict()


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

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()