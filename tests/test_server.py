import asyncio

from mcp_ddg_research.server import (
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