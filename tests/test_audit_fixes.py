"""Tests for the audit fixes: time filter, cache poisoning, byte cap, auth, CGNAT."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

import mcp_prism_search.fetch as fetch_module
import mcp_prism_search.search as search_module
import mcp_prism_search.server as server_module
from mcp_prism_search.cache import JsonFileCache
from mcp_prism_search.models import FetchRequest, SearchRequest
from mcp_prism_search.search import _search_with_duckduckgo_html, ddg_search
from mcp_prism_search.text import extract_html_text

REAL_ASYNC_CLIENT = httpx.AsyncClient


def _html_transport(captured: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            content=(
                "<html><body>"
                "<div class='result'><a class='result__a' href='https://example.com/1'>One</a>"
                "<a class='result__snippet'>snippet one</a></div>"
                "<div class='result'><a class='result__a' href='https://example.com/2'>Two</a>"
                "<a class='result__snippet'>snippet two</a></div>"
                "</body></html>"
            ),
        )

    return httpx.MockTransport(handler)


# --- search: time_filter now reaches the HTML endpoint ------------------------


def test_html_search_sends_df_time_filter() -> None:
    captured: dict = {}
    with patch.object(
        search_module.httpx, "AsyncClient", return_value=REAL_ASYNC_CLIENT
    ) as factory:
        factory.return_value = REAL_ASYNC_CLIENT(
            transport=_html_transport(captured), timeout=10
        )
        request = SearchRequest(query="test query", time_filter="day", safe_search="strict")
        asyncio.run(_search_with_duckduckgo_html(request, timeout_seconds=10))

    assert "df=d" in captured["url"], captured["url"]


def test_html_search_omits_df_when_no_time_filter() -> None:
    captured: dict = {}
    with patch.object(search_module.httpx, "AsyncClient") as factory:
        factory.return_value = REAL_ASYNC_CLIENT(
            transport=_html_transport(captured), timeout=10
        )
        request = SearchRequest(query="test query", safe_search="strict")
        asyncio.run(_search_with_duckduckgo_html(request, timeout_seconds=10))

    assert "df=" not in captured["url"], captured["url"]


# --- search + fetch: poisoned cache is invalidated, not fatal -----------------


def _search_payload_for(query: str) -> dict:
    request = SearchRequest(
        query=query,
        max_results=10,
        search_window=None,
        safe_search="off",
        time_filter=None,
        blocked_domains=[],
        allowed_domains=[],
        preferred_domains=[],
    )
    return search_module._cache_payload(
        request, provider="duckduckgo_html", ddgs_backend=None
    )


def test_search_poisoned_cache_is_invalidated(tmp_path) -> None:
    cache = JsonFileCache(tmp_path, "search", 3600)
    cache.set(
        _search_payload_for("needle"),
        {"totally": "wrong shape", "not": "a SearchResponse"},
    )
    assert list(tmp_path.glob("search/*.json"))

    with patch.object(search_module, "build_search_cache", return_value=cache), patch.object(
        search_module, "_search_with_duckduckgo_html", side_effect=AssertionError
    ), patch.object(search_module, "_search_with_ddgs", side_effect=AssertionError):
        result = asyncio.run(ddg_search(query="needle", cache=cache))

    assert result.results == []
    assert result.error
    # the poisoning file is gone, so a later real search can repopulate
    assert not list(tmp_path.glob("search/*.json"))


def test_fetch_poisoned_cache_is_invalidated(tmp_path) -> None:
    cache = JsonFileCache(tmp_path, "fetch", 3600)
    payload = FetchRequest(url="https://example.com/", max_chars=12000).to_cache_payload(
        _max_bytes=fetch_module.get_fetch_max_bytes()
    )
    cache.set(payload, {"wrong": True})
    assert list(tmp_path.glob("fetch/*.json"))

    async def fake_validate(url: str) -> None:
        return None

    async def fake_resolve(url: str) -> tuple[str, int]:
        return "127.0.0.1", 80

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content="boom", headers={"content-type": "text/html"})

    with patch.object(fetch_module, "validate_fetch_url", fake_validate), patch.object(
        fetch_module, "resolve_safe_address", fake_resolve
    ), patch.object(fetch_module.httpx, "AsyncClient") as factory:
        factory.return_value = REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), timeout=15
        )
        result = asyncio.run(
            fetch_module.web_fetch(url="https://example.com/", cache=cache)
        )

    assert result.error  # structured provider error, no ValidationError escapes
    assert not list(tmp_path.glob("fetch/*.json"))


# --- fetch: byte cap + IP pinning ---------------------------------------------


def test_fetch_byte_cap_aborts_large_body(tmp_path) -> None:
    cache = JsonFileCache(tmp_path, "fetch", 3600)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.headers.get("host")
        return httpx.Response(
            200, content=b"x" * (2 * 1024 * 1024), headers={"content-type": "text/html"}
        )

    async def fake_validate(url: str) -> None:
        return None

    async def fake_resolve(url: str) -> tuple[str, int]:
        return "127.0.0.1", 80

    with patch.object(fetch_module, "get_fetch_max_bytes", return_value=1024 * 1024), patch.object(
        fetch_module, "validate_fetch_url", fake_validate
    ), patch.object(fetch_module, "resolve_safe_address", fake_resolve), patch.object(
        fetch_module.httpx, "AsyncClient", return_value=REAL_ASYNC_CLIENT
    ) as factory:
        factory.return_value = REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), timeout=15
        )
        result = asyncio.run(
            fetch_module.web_fetch(url="https://example.com/", max_chars=1000, cache=cache)
        )

    # the oversized body is truncated with an actionable notice, not an error
    assert result.success
    assert result.truncated is True
    assert result.fetched_bytes <= 1024 * 1024
    assert "PARTIAL CONTENT" in result.content
    assert captured["host"] == "example.com"  # Host header preserved during pinning


def test_small_body_succeeds_with_host_preserved_in_pin(tmp_path) -> None:
    cache = JsonFileCache(tmp_path, "fetch", 3600)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.headers.get("host")
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            content="<html><body><article>Hello world content</article></body></html>",
            headers={"content-type": "text/html"},
        )

    async def fake_validate(url: str) -> None:
        return None

    async def fake_resolve(url: str) -> tuple[str, int]:
        return "127.0.0.1", 80

    with patch.object(fetch_module, "validate_fetch_url", fake_validate), patch.object(
        fetch_module, "resolve_safe_address", fake_resolve
    ), patch.object(
        fetch_module.httpx, "AsyncClient", return_value=REAL_ASYNC_CLIENT
    ) as factory:
        factory.return_value = REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler), timeout=15
        )
        result = asyncio.run(fetch_module.web_fetch(url="https://example.com/page", cache=cache))

    assert result.success
    assert captured["host"] == "example.com"
    assert "127.0.0.1" in captured["url"]  # connection pinned to the validated IP


# --- security: CGNAT now blocked -----------------------------------------------


def test_cgnat_range_is_blocked() -> None:
    from mcp_prism_search.security import is_unsafe_ip

    assert is_unsafe_ip("100.64.0.1")
    assert is_unsafe_ip("100.100.200.200")
    assert not is_unsafe_ip("100.63.255.255")  # just outside the range stays public


# --- auth middleware: non-ASCII header returns 401 ------------------------------


async def _run_middleware(middleware, scope) -> int:
    status: list[int] = []

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])

    async def receive() -> dict:
        return {}

    await middleware(scope, receive, send)
    return status[0]


def test_middleware_non_ascii_authorization_is_401() -> None:
    middleware = server_module.BearerTokenAuthMiddleware(
        SimpleNamespace(__call__=lambda *a, **k: None), token="real-token"
    )
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer s\xe9cret")]}
    status = asyncio.run(_run_middleware(middleware, scope))
    assert status == 401


def test_middleware_unknown_scope_is_rejected() -> None:
    middleware = server_module.BearerTokenAuthMiddleware(
        SimpleNamespace(__call__=lambda *a, **k: None), token="real-token"
    )
    with pytest.raises(RuntimeError):
        asyncio.run(_run_middleware(middleware, {"type": "websocket", "headers": []}))


# --- server startup validation ----------------------------------------------------


def test_placeholder_token_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "change-me-now")
    with pytest.raises(server_module.KnownPlaceholderTokenError):
        asyncio.run(server_module._run_http_async())


def test_http_mode_without_token_is_rejected(monkeypatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_ALLOW_UNAUTHENTICATED_HTTP", raising=False)
    with pytest.raises(RuntimeError, match="MCP_AUTH_TOKEN"):
        asyncio.run(server_module._run_http_async())


def test_invalid_transport_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "sse")
    with pytest.raises(RuntimeError, match="MCP_TRANSPORT"):
        server_module.main()


def test_invalid_port_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("MCP_PORT", "not-a-port")
    with pytest.raises(RuntimeError, match="MCP_PORT"):
        server_module._configure_http_settings()


# --- text extraction: fallback keeps the longer candidate -------------------------


def test_text_fallback_keeps_longer_body_candidate() -> None:
    candidate = "<main>" + "p " * 300 + "</main>"  # ~600 chars
    html = f"<html><head><title>T</title></head>{candidate}<body><p>tiny</p></body></html>"
    extracted = extract_html_text(html, max_chars=2000)
    assert len(extracted.content) > 500


# --- deep search reports provider failure -------------------------------------------


def test_deep_search_surfaces_search_error() -> None:
    with patch.object(
        fetch_module,
        "ddg_search",
        return_value=SimpleNamespace(
            query="anything",
            provider="duckduckgo_html",
            results=[],
            cached=False,
            error="provider exploded",
        ),
    ):
        result = asyncio.run(fetch_module.ddg_deep_search(query="anything"))
    assert result.search_error == "provider exploded"
    assert result.sources == []

# --- improvement pass: structured output, /health, ToolError, progress --------


async def _tool_output_schemas_by_name() -> dict:
    tools = await server_module.mcp.list_tools()
    if isinstance(tools, tuple):
        tools = tools[0]
    schemas = {}
    for tool in tools:
        schema = getattr(tool, "output_schema", None) or getattr(
            tool, "outputSchema", None
        )
        schemas[tool.name] = schema
    return schemas


def test_tools_publish_typed_output_schema() -> None:
    schemas = asyncio.run(_tool_output_schemas_by_name())
    for name in ("ddg_search", "web_fetch", "ddg_deep_search"):
        assert schemas.get(name), f"{name} has no output schema"


def test_health_path_bypasses_auth() -> None:
    called = False

    async def app(scope, receive, send) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = server_module.BearerTokenAuthMiddleware(app, token="secret")
    status = asyncio.run(
        _run_middleware(
            middleware,
            {"type": "http", "method": "GET", "path": "/health", "headers": []},
        )
    )

    assert called is True  # inner app (route) ran without auth
    assert status == 200


def test_ddg_search_tool_raises_tool_error_on_provider_failure() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from mcp_prism_search.models import SearchResponse

    async def failing_provider(**_kwargs):
        return SearchResponse(
            query="q", provider="duckduckgo_html", results=[], cached=False,
            error="duckduckgo_html failed: timeout",
        )

    with patch.object(server_module, "perform_ddg_search", failing_provider), pytest.raises(
        ToolError, match="timeout"
    ):
        asyncio.run(server_module.ddg_search(query="q"))


def test_web_fetch_tool_raises_tool_error_on_failure() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from mcp_prism_search.models import FetchResponse

    async def failing_provider(**_kwargs):
        return FetchResponse(
            url="https://example.com/", final_url="", title="", content="",
            content_type="", cached=False, success=False, error="boom",
        )

    with patch.object(server_module, "perform_web_fetch", failing_provider), pytest.raises(
        ToolError, match="boom"
    ):
        asyncio.run(server_module.web_fetch(url="https://example.com/"))


def test_deep_search_reports_progress() -> None:
    from mcp_prism_search.models import (
        DeepSearchPage,
        DeepSearchResponse,
        SearchResult,
    )

    async def fake_deep(**_kwargs):
        return DeepSearchResponse(
            query="q",
            search_provider="duckduckgo_html",
            sources=[SearchResult(title="T", url="https://a.example/", snippet="s")],
            pages=[
                DeepSearchPage(
                    title="T", url="https://a.example/",
                    final_url="https://a.example/", content="c",
                )
            ],
            failed_pages=[],
            cached=False,
            search_error=None,
        )

    progress_calls: list[tuple[float, float | None]] = []

    async def progress_callback(progress: float, total: float | None) -> None:
        progress_calls.append((progress, total))

    with patch.object(fetch_module, "ddg_search", return_value=SimpleNamespace(
        query="q", provider="duckduckgo_html",
        results=[SearchResult(title="T", url="https://a.example/", snippet="s")],
        cached=False, error=None,
    )), patch.object(
        fetch_module, "web_fetch",
        side_effect=lambda *a, **k: _fake_fetch_result(),
    ):
        result = asyncio.run(
            fetch_module.ddg_deep_search(query="q", progress_callback=progress_callback)
        )

    assert result.pages  # fetched page present
    assert progress_calls, "progress callback was never called"
    assert progress_calls[0][1] == 6.0  # total = max_pages(5) + search(1)


def _fake_fetch_result():
    from mcp_prism_search.models import FetchResponse

    return FetchResponse(
        url="https://a.example/", final_url="https://a.example/", title="T",
        content="c", content_type="text/html", cached=False, success=True, error=None,
    )
