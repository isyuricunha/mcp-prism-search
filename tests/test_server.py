import asyncio

import pytest

from mcp_prism_search.server import (
    BearerTokenAuthMiddleware,
    _build_transport_security_settings,
    _configure_http_settings,
    mcp,
)


def test_configure_http_settings_defaults_are_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

    settings = _configure_http_settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.transport_security is not None
    assert settings.transport_security.enable_dns_rebinding_protection is True
    assert settings.transport_security.allowed_hosts == ["localhost", "127.0.0.1"]


def test_configure_http_settings_wildcard_is_explicit_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9000")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "*")

    settings = _configure_http_settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.transport_security.enable_dns_rebinding_protection is False
    assert settings.transport_security.allowed_hosts == ["*"]


def test_explicit_hosts_keep_dns_rebinding_protection_enabled() -> None:
    settings = _build_transport_security_settings(
        allowed_hosts=["example.com", "example.com:443"],
        allowed_origins=["https://example.com"],
    )

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["example.com", "example.com:443"]
    assert settings.allowed_origins == ["https://example.com"]


def test_bearer_token_middleware_rejects_missing_authorization() -> None:
    called = False

    async def app(scope, receive, send) -> None:
        nonlocal called
        called = True

    middleware = BearerTokenAuthMiddleware(app, token="secret")
    messages = asyncio.run(_call_middleware(middleware, headers=[]))

    assert called is False
    assert messages[0]["status"] == 401
    assert (b"www-authenticate", b"Bearer") in messages[0]["headers"]


def test_bearer_token_middleware_accepts_valid_authorization() -> None:
    called = False

    async def app(scope, receive, send) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BearerTokenAuthMiddleware(app, token="secret")
    messages = asyncio.run(
        _call_middleware(
            middleware,
            headers=[(b"authorization", b"Bearer secret")],
        )
    )

    assert called is True
    assert messages[0]["status"] == 200


def test_tool_schema_describes_search_window_as_result_count() -> None:
    schemas = asyncio.run(_tool_schemas_by_name())

    search_window = schemas["ddg_search"]["properties"]["search_window"]
    deep_search_window = schemas["ddg_deep_search"]["properties"]["search_window"]

    assert "Internal provider result count" in search_window["description"]
    assert "not a time range or number of days" in search_window["description"]
    assert deep_search_window["description"] == search_window["description"]


def test_tool_schema_describes_domain_controls() -> None:
    schemas = asyncio.run(_tool_schemas_by_name())
    properties = schemas["ddg_search"]["properties"]

    assert "Exclusive domain allowlist" in properties["allowed_domains"]["description"]
    assert "Exclusion list" in properties["blocked_domains"]["description"]
    assert "not exclusive filtering" in properties["preferred_domains"]["description"]


def test_tool_schema_describes_deep_search_fetch_controls() -> None:
    schemas = asyncio.run(_tool_schemas_by_name())
    properties = schemas["ddg_deep_search"]["properties"]

    assert "Number of top search result pages" in properties["max_pages"]["description"]
    assert "Per-call concurrent page fetch limit" in properties["max_concurrency"]["description"]


def test_cache_tools_are_listed_with_described_arguments() -> None:
    schemas = asyncio.run(_tool_schemas_by_name())

    assert "cache_stats" in schemas
    assert "cache_prune" in schemas
    assert "cache_clear" in schemas
    assert "dry_run" in schemas["cache_prune"]["properties"]
    assert "without deleting" in schemas["cache_prune"]["properties"]["dry_run"]["description"]
    assert "namespace" in schemas["cache_clear"]["properties"]
    assert "confirm" in schemas["cache_clear"]["properties"]


async def _tool_schemas_by_name() -> dict:
    tools = await mcp.list_tools()
    if isinstance(tools, tuple):
        tools = tools[0]
    schemas = {}
    for tool in tools:
        schema = getattr(tool, "input_schema", None) or getattr(
            tool, "inputSchema", None
        )
        schemas[tool.name] = schema
    return schemas


async def _call_middleware(middleware, headers):
    messages = []
    scope = {"type": "http", "method": "GET", "path": "/mcp", "headers": headers}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)
    return messages

def test_schema_advertises_backend_size_limits():
    schemas = asyncio.run(_tool_schemas_by_name())

    def limit(schema, key):
        return max([schema.get(key, 0)] + [
            v.get(key, 0) for v in schema.get("anyOf", [])
        ])

    for name in ("web_search", "ddg_search", "ddg_deep_search", "vertical_search"):
        assert limit(schemas[name]["properties"]["query"], "maxLength") == 4000
    for name in ("web_search", "ddg_search", "ddg_deep_search"):
        for field in ("allowed_domains", "blocked_domains", "preferred_domains"):
            assert limit(schemas[name]["properties"][field], "maxItems") == 50
    assert limit(schemas["web_fetch"]["properties"]["url"], "maxLength") == 8192


def test_duplicate_authorization_headers_are_rejected():
    from mcp_prism_search.server import _authorization_header

    assert _authorization_header({"headers": [
        (b"authorization", b"Bearer first"), (b"Authorization", b"Bearer second"),
    ]}) is None


def test_cache_tools_declare_closed_world_annotations():
    async def verify():
        tools = await mcp.list_tools()
        if isinstance(tools, tuple):
            tools = tools[0]
        by_name = {tool.name: tool.annotations for tool in tools}
        for name in ("cache_stats", "cache_prune", "cache_clear"):
            assert by_name[name].open_world_hint is False
        assert by_name["cache_stats"].read_only_hint is True
        assert by_name["cache_clear"].destructive_hint is True
        assert by_name["web_search"].open_world_hint is True

    asyncio.run(verify())


def test_bearer_scheme_is_case_insensitive_but_token_is_not():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    middleware = BearerTokenAuthMiddleware(app, "fixture-Token")
    for header, expected in [(b"bEaReR  fixture-Token", 200),
                             (b"Bearer fixture-token", 401),
                             (b"Basic fixture-Token", 401)]:
        response = asyncio.run(_call_middleware(middleware, [(b"authorization", header)]))
        assert response[0]["status"] == expected


def test_stdio_lifecycle_runs_and_cancels_periodic_pruning(monkeypatch):
    from mcp_prism_search import server

    async def verify():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def prune():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        async def stdio():
            await started.wait()

        monkeypatch.setattr(server, "_cache_prune_loop", prune)
        monkeypatch.setattr(server.mcp, "run_stdio_async", stdio)
        await server._run_stdio_async()
        assert stopped.is_set()

    asyncio.run(verify())


def test_cache_prune_description_explains_dry_run_and_expired_scope():
    async def verify():
        listing = await mcp.list_tools()
        tools = listing[0] if isinstance(listing, tuple) else listing
        tool = next(tool for tool in tools if tool.name == "cache_prune")
        assert "preserves fresh entries" in tool.description
        assert "Dry-run never deletes" in tool.description

    asyncio.run(verify())


def test_mcp_prune_requires_literal_boolean_flags(tmp_path, monkeypatch):
    from mcp import Client

    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path))

    async def verify():
        async with Client(mcp) as client:
            for args in [{"dry_run": "false"}, {"expired_only": 0}]:
                response = await client.call_tool("cache_prune", args)
                assert response.is_error

    asyncio.run(verify())


def test_http_runtime_uses_normalized_log_level(monkeypatch):
    import uvicorn

    from mcp_prism_search import server

    monkeypatch.setenv("MCP_LOG_LEVEL", " warning ")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "fixture-auth-not-production")
    captured = []

    class FakeServer:
        def __init__(self, config):
            captured.append(config.log_level)

    async def serve(*args):
        pass

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(server, "_serve_http", serve)
    asyncio.run(server._run_http_async())
    assert captured == ["warning"]


@pytest.mark.parametrize("port", ["bad", "0", "65536", "-1"])
def test_invalid_http_port(monkeypatch, port):
    monkeypatch.setenv("MCP_PORT", port)
    with pytest.raises(RuntimeError, match="Invalid MCP_PORT"):
        _configure_http_settings()


def test_invalid_log_level(monkeypatch):
    from mcp_prism_search.server import _validated_log_level

    monkeypatch.setenv("MCP_LOG_LEVEL", " unsupported ")
    with pytest.raises(RuntimeError, match="accepted values"):
        _validated_log_level()


def test_health_and_lifespan_auth():
    calls = []

    async def app(scope, receive, send):
        calls.append((scope["type"], scope.get("path")))

    async def unused(*args):
        pytest.fail("unexpected authentication response")

    async def verify():
        middleware = BearerTokenAuthMiddleware(app, "fixture-token")
        await middleware({"type": "http", "path": "/health"}, unused, unused)
        await middleware({"type": "lifespan"}, unused, unused)
        with pytest.raises(RuntimeError, match="refuses"):
            await middleware({"type": "websocket"}, unused, unused)

    asyncio.run(verify())
    assert calls == [("http", "/health"), ("lifespan", None)]


@pytest.mark.parametrize("transport,enabled,expected", [
    ("invalid", "false", "Invalid MCP_TRANSPORT"),
    ("stdio", "true", "requires MCP_TRANSPORT=http"),
])
def test_webui_requires_http(monkeypatch, transport, enabled, expected):
    from mcp_prism_search import server

    monkeypatch.setenv("MCP_TRANSPORT", transport)
    monkeypatch.setenv("MCP_WEBUI_ENABLED", enabled)
    monkeypatch.setattr(server, "_prune_cache_on_startup", lambda: None)
    with pytest.raises(RuntimeError, match=expected):
        server._run()


@pytest.mark.parametrize("port", ["8000", "0", "65536"])
def test_invalid_dashboard_port(monkeypatch, port):
    from mcp_prism_search import server

    monkeypatch.setenv("MCP_PORT", "8000")
    monkeypatch.setenv("MCP_WEBUI_ENABLED", "true")
    monkeypatch.setenv("MCP_WEBUI_PORT", port)
    with pytest.raises(RuntimeError, match="Invalid MCP_WEBUI_PORT"):
        asyncio.run(server._serve_http(None, _configure_http_settings()))


def test_server_http_without_dashboard(monkeypatch):
    from mcp_prism_search import server

    monkeypatch.setenv("MCP_WEBUI_ENABLED", "false")
    calls = []

    class FakeServer:
        async def serve(self):
            calls.append("serve")

    asyncio.run(server._serve_http(FakeServer(), _configure_http_settings()))
    assert calls == ["serve"]


def test_startup_prune_failure_nonfatal(monkeypatch, caplog):
    from mcp_prism_search import server

    def fail():
        raise OSError("fixture disk failure")

    monkeypatch.setattr(server.cache_ops, "prune_cache_on_startup", fail)
    server._prune_cache_on_startup()
    assert "Startup cache pruning failed" in caplog.text


@pytest.mark.parametrize("failed", [False, True])
def test_vertical_mcp_contract(monkeypatch, failed):
    from mcp import Client

    from mcp_prism_search import server
    from mcp_prism_search.models import SearchResponse

    async def search(**kwargs):
        assert kwargs == {"query": "topic", "provider": "wikipedia",
                          "max_results": 3, "kind": "vertical"}
        return SearchResponse(query="topic", provider="wikipedia", results=[],
                              cached=False, error="Unavailable" if failed else None)

    monkeypatch.setattr(server, "perform_aggregated_search", search)

    async def verify():
        async with Client(mcp) as client:
            result = await client.call_tool("vertical_search", {
                "query": "topic", "source": "wikipedia", "max_results": 3,
            })
            assert result.is_error is failed

    asyncio.run(verify())


def test_mcp_cache_prune_dry_run(monkeypatch, tmp_path):
    import os
    import time

    from mcp import Client

    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("DDG_CACHE_TTL_SECONDS", "1")
    namespace = tmp_path / "search"
    namespace.mkdir()
    stale = namespace / "entry.json"
    stale.write_text("{}")
    old = time.time() - 10
    os.utime(stale, (old, old))

    async def verify():
        async with Client(mcp) as client:
            result = await client.call_tool("cache_prune", {"expired_only": True, "dry_run": True})
            assert not result.is_error
            assert stale.exists()
            result = await client.call_tool("cache_prune", {"expired_only": True, "dry_run": False})
            assert not result.is_error
            assert not stale.exists()

    asyncio.run(verify())


def test_mcp_fetch_failure():
    from mcp import Client

    async def verify():
        async with Client(mcp) as client:
            result = await client.call_tool("web_fetch", {"url": "http://127.0.0.1"})
            assert result.is_error

    asyncio.run(verify())
