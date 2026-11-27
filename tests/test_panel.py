"""Optional panel: auth isolation, persistence, request safety and provider integration."""
import asyncio

import httpx
import pytest
from starlette.testclient import TestClient

from mcp_prism_search import providers, runtime, search
from mcp_prism_search.panel import create_app
from mcp_prism_search.panel_store import PanelStore

PASSWORD = "long-admin-password"
HEADERS = {"Origin": "http://localhost", "X-Prism-Admin": "1"}


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path / "cache"))
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>Prism</html>")
    directory = tmp_path / "admin"
    store = PanelStore(directory)
    app = create_app(PASSWORD, store, assets)
    with TestClient(app, base_url="http://localhost") as client:
        yield client, store, directory
    assert runtime.state() is None


def sign_in(client):
    response = client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    return response


def test_auth_and_csrf(panel):
    client, _, _ = panel
    assert client.get("/").status_code == 200
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"Authorization": "Bearer mcp"}).status_code == 401
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 403
    assert client.post("/api/login", json={"password": "wrong"}, headers=HEADERS).status_code == 401
    sign_in(client)
    response = client.get("/api/overview")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    response = client.put("/api/providers/keenable", json={"enabled": False},
                          headers={"Origin": "https://evil.test", "X-Prism-Admin": "1"})
    assert response.status_code == 403
    assert client.post("/api/logout", headers=HEADERS).status_code == 200
    assert client.get("/api/overview").status_code == 401


def test_credentials_and_flags(panel):
    client, store, directory = panel
    sign_in(client)
    value = "fake-secret-unique-test-key"
    result = client.put("/api/providers/tinyfish", headers=HEADERS,
                        json={"enabled": True, "credential": value})
    assert result.status_code == 200
    assert runtime.setting("TINYFISH_API_KEY") == value
    assert "tinyfish" in providers.available_providers()
    assert value not in client.get("/api/overview").text
    assert value.encode() not in store.path.read_bytes()
    assert (directory / "encryption.key").stat().st_mode & 0o077 == 0
    assert store.path.stat().st_mode & 0o077 == 0
    restored = PanelStore(directory)
    assert restored.credentials["TINYFISH_API_KEY"] == value
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": False}).status_code == 200
    assert "tinyfish" not in providers.available_providers()
    assert PanelStore(directory).flags["tinyfish"] == 0
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": True, "remove": True}).status_code == 200
    assert runtime.setting("TINYFISH_API_KEY") is None


def test_env_precedence_and_no_arbitrary_endpoint(panel, monkeypatch):
    client, _, _ = panel
    monkeypatch.setenv("TINYFISH_API_KEY", "env-secret")
    sign_in(client)
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": True, "credential": "replace"}).status_code == 400
    assert client.put("/api/providers/searxng", headers=HEADERS,
                      json={"enabled": True, "credential": "http://127.0.0.1"}).status_code == 400
    assert "env-secret" not in client.get("/api/overview").text


def test_search_uses_managed_key_and_records_metrics(panel, monkeypatch):
    client, store, _ = panel
    sign_in(client)
    client.put("/api/providers/tinyfish", headers=HEADERS,
               json={"enabled": True, "credential": "panel-key"})
    original = httpx.AsyncClient

    def handler(req):
        assert req.headers["X-API-Key"] == "panel-key"
        return httpx.Response(200, json={"results": [
            {"title": "Example", "url": "https://example.com", "snippet": "Text"}
        ]})

    def wrapper(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", wrapper)
    args = {"query": "unique test", "providers": ["tinyfish"], "max_results": 1}
    response = client.post("/api/search", headers=HEADERS, json=args)
    assert response.status_code == 200
    assert response.json()["results"][0]["url"] == "https://example.com"
    assert client.post("/api/search", headers=HEADERS, json=args).json()["cached"]
    assert store.metrics()[0]["requests"] == 1
    assert "unique test" not in store.path.read_bytes().decode(errors="ignore")
    assert asyncio.run(search.aggregated_search(
        "another query", provider="tinyfish", max_results=1
    )).results


def test_rate_limit(panel):
    client, _, _ = panel
    for _ in range(10):
        assert client.post("/api/login", headers=HEADERS,
                           json={"password": "bad"}).status_code == 401
    assert client.post("/api/login", headers=HEADERS,
                       json={"password": PASSWORD}).status_code == 429


def test_password_and_host_validation(panel, tmp_path):
    client, store, _ = panel
    assert client.get("/", headers={"Host": "evil.test"}).status_code == 400
    with pytest.raises(RuntimeError, match="MCP_WEBUI_PASSWORD"):
        create_app("short", store, tmp_path / "assets")
    sign_in(client)
    assert client.post("/api/search", headers=HEADERS,
                       json={"query": "x", "providers": ["unknown"]}).status_code == 400
    assert client.put("/api/providers/not-real", headers=HEADERS,
                      json={"enabled": True}).status_code == 404
    assert client.put("/api/providers/keenable", headers=HEADERS,
                      json={"enabled": True, "unexpected": 1}).status_code == 400


def test_settings_and_clear_cache(panel, monkeypatch):
    client, _, directory = panel
    for env in ("DDG_TIMEOUT_SECONDS", "DDG_CACHE_TTL_SECONDS", "MCP_PREFERRED_PROVIDER"):
        monkeypatch.delenv(env, raising=False)
    sign_in(client)
    response = client.put("/api/settings", headers=HEADERS,
                          json={"timeout": 23, "search_ttl": 180, "preferred": "freeserp"})
    assert response.status_code == 200
    assert search.get_ddg_timeout_seconds() == 23
    assert PanelStore(directory).options["DDG_CACHE_TTL_SECONDS"] == "180"
    assert providers.resolve_chain("auto", "general")[0] == "freeserp"
    monkeypatch.setenv("DDG_TIMEOUT_SECONDS", "15")
    assert client.put("/api/settings", headers=HEADERS,
                      json={"timeout": 30}).status_code == 400
    assert search.get_ddg_timeout_seconds() == 15
    assert client.post("/api/cache/clear", headers=HEADERS,
                       json={"namespace": "all", "confirm": False}).status_code == 400
    assert client.post("/api/cache/clear", headers=HEADERS,
                       json={"namespace": "all", "confirm": True}).status_code == 200


def test_dashboard_reports_installed_versions(panel):
    from importlib.metadata import version

    from mcp.types import LATEST_PROTOCOL_VERSION

    from mcp_prism_search import __version__
    from mcp_prism_search.version_info import build_info

    client, _, _ = panel
    sign_in(client)
    assert client.get("/api/overview").json()["server"] == {
        "version": __version__,
        **build_info(__version__),
        "sdk_version": version("mcp"),
        "protocol_version": LATEST_PROTOCOL_VERSION,
        "transport": "streamable-http",
    }


def test_dashboard_search_modes(panel, monkeypatch):
    from mcp_prism_search.models import SearchResponse

    client, _, _ = panel
    sign_in(client)
    calls = []

    async def fake(query, **kwargs):
        calls.append((query, kwargs))
        return SearchResponse(query=query, provider="keenable", results=[], cached=False,
                              error=None)

    monkeypatch.setattr("mcp_prism_search.panel.aggregated_search", fake)
    for mode in ("auto", "all"):
        response = client.post("/api/search", headers=HEADERS,
                               json={"mode": mode, "query": " test ", "max_results": 3})
        assert response.status_code == 200
        assert calls[-1] == ("test", {"provider": mode, "max_results": 3})
    assert client.post("/api/search", headers=HEADERS,
                       json={"mode": "auto", "providers": ["keenable"],
                             "query": "test"}).status_code == 400
    assert client.post("/api/search", headers=HEADERS,
                       json={"query": " ", "providers": ["keenable"]}).status_code == 400
    assert client.post("/api/search", headers=HEADERS,
                       json={"query": "test"}).status_code == 200
    assert calls[-1] == ("test", {"max_results": None})


def test_native_panel_cost_gate(panel, monkeypatch):
    client, _, _ = panel
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    assert client.post("/api/native", headers=HEADERS, json={
        "mode": "answer", "provider": "exa", "query": "test",
    }).status_code == 401
    sign_in(client)
    caps = client.get("/api/overview").json()["native"]
    assert any(p["id"] == "exa" and p["available"] for p in caps)
    result = client.post("/api/native", headers=HEADERS, json={
        "mode": "answer", "provider": "exa", "query": "test",
    })
    assert result.status_code == 400
    assert "allow_cost" in result.json()["error"]


def test_ai_search_settings_persist(panel, monkeypatch):
    for env in ("MCP_AI_SEARCH_ENABLED", "MCP_AI_SEARCH_PROVIDERS"):
        monkeypatch.delenv(env, raising=False)
    client, _, directory = panel
    sign_in(client)
    initial = client.get("/api/overview").json()["settings"]
    assert not initial["ai_search_enabled"]
    response = client.put("/api/settings", headers=HEADERS, json={
        "ai_search_enabled": True, "ai_search_providers": ["tavily", "brave"],
    })
    assert response.status_code == 200
    assert response.json()["ai_search_providers"] == ["brave", "tavily"]
    assert PanelStore(directory).options["MCP_AI_SEARCH_ENABLED"] == "True"
    assert client.put("/api/settings", headers=HEADERS, json={
        "ai_search_providers": ["unknown"],
    }).status_code == 400
    monkeypatch.setenv("MCP_AI_SEARCH_ENABLED", "false")
    assert not client.get("/api/overview").json()["settings"]["ai_search_enabled"]
    assert client.put("/api/settings", headers=HEADERS, json={
        "ai_search_enabled": True,
    }).status_code == 400


def test_defaults_api_schema_and_shared_resolution(panel, monkeypatch):
    from mcp_prism_search.defaults import ENV, effective_defaults

    client, store, _ = panel
    assert client.get("/api/defaults").status_code == 401
    sign_in(client)
    response = client.get("/api/defaults")
    fields = {f["id"]: f for f in response.json()["fields"]}
    rows = client.get("/api/overview").json()["providers"]
    assert len(rows) == 17
    assert all(p["display_name"] and p["docs_url"] and p["usage_note"] for p in rows)
    assert fields["search_max_results"]["value"] == 10
    assert fields["search_max_results"]["min"] == 1
    assert "keyless" in fields["search_mode"]["choices"]
    response = client.put("/api/defaults", headers=HEADERS, json={"values": {
        "search_mode": "selected", "search_providers": ["keenable"], "search_max_results": 6,
    }})
    assert response.status_code == 200
    assert effective_defaults().search_max_results == 6
    assert store.options[ENV["search_mode"]] == "selected"
    response = client.put("/api/defaults", headers=HEADERS, json={"values": {
        "search_providers": [], "fetch_max_chars": 6000,
    }})
    assert response.status_code == 400
    assert effective_defaults().fetch_max_chars == 12000
    monkeypatch.setenv(ENV["search_max_results"], "4")
    assert client.put("/api/defaults", headers=HEADERS, json={"values": {
        "search_max_results": 5,
    }}).status_code == 400
    monkeypatch.setenv("MCP_DISABLED_PROVIDERS", "keenable")
    row = next(p for p in client.get("/api/overview").json()["providers"] if p["id"]=="keenable")
    assert row["enabled_locked"] and not row["available"]
    assert client.put("/api/providers/keenable", headers=HEADERS,
                      json={"enabled": True}).status_code == 400


def test_playground_limits_match_mcp(panel, monkeypatch):
    from mcp_prism_search.models import SearchResponse

    client, _, _ = panel
    sign_in(client)
    limits = client.get("/api/overview").json()["limits"]
    assert limits == {"query_max_chars": 4000, "selected_providers_max": 20}

    async def fake(query, **kwargs):
        return SearchResponse(query=query, provider="keenable", results=[],
                              cached=False, error=None)

    monkeypatch.setattr("mcp_prism_search.panel.aggregated_search", fake)
    assert client.post("/api/search", headers=HEADERS, json={
        "query": "x" * 4000,
    }).status_code == 200
    assert client.post("/api/search", headers=HEADERS, json={
        "query": "x" * 4001,
    }).status_code == 400
    assert client.post("/api/search", headers=HEADERS, json={
        "query": "test", "providers": ["keenable"] * 21,
    }).status_code == 400


@pytest.mark.parametrize("payload", [
    {"enabled": "false"}, {"enabled": True, "remove": "true"},
    {"enabled": True, "credential": "   "},
    {"enabled": True, "credential": "one\ntwo"},
])
def test_provider_update_rejects_ambiguous_values(panel, payload):
    client, store, _ = panel
    sign_in(client)
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json=payload).status_code == 400
    assert "TINYFISH_API_KEY" not in store.credentials
    assert "tinyfish" not in store.flags


@pytest.mark.parametrize("confirmation", [1, "true", False])
def test_panel_cache_clear_requires_boolean_true(panel, confirmation):
    from mcp_prism_search.cache import JsonFileCache, get_cache_dir

    client, _, _ = panel
    sign_in(client)
    store = JsonFileCache(get_cache_dir(), "search", 60)
    store.set({"test": 1}, {"value": "preserved"})
    assert client.post("/api/cache/clear", headers=HEADERS, json={
        "namespace": "all", "confirm": confirmation,
    }).status_code == 400
    assert store.get({"test": 1}) == {"value": "preserved"}


def test_panel_security_headers_on_early_denials(panel):
    client, _, _ = panel
    for response in (client.get("/api/overview"),
                     client.post("/api/login", json={})):
        assert response.status_code in {401, 403}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


def test_successful_reauthentication_revokes_previous_cookie(panel):
    from mcp_prism_search.panel import COOKIE

    client, _, _ = panel
    first = sign_in(client).cookies.get(COOKIE)
    second = sign_in(client).cookies.get(COOKIE)
    assert first and second and first != second
    client.cookies.clear()
    client.cookies.set(COOKIE, first)
    assert client.get("/api/overview").status_code == 401
    client.cookies.clear()
    client.cookies.set(COOKIE, second)
    assert client.get("/api/overview").status_code == 200



@pytest.mark.parametrize(("installed", "kind", "commit"), [
    ("0.15.3", "release", None),
    ("0.15.4.dev1+g123abc456", "development", "123abc456"),
    ("0.15.4.dev1+g123abc456.d20261127", "development", "123abc456"),
    ("0.15.4.dev0", "development", None),
    ("0.15.3+local", "development", None),
    ("0.0.0+unknown", "unknown", None),
    ("invalid", "unknown", None),
])
def test_dashboard_build_identity(panel, monkeypatch, installed, kind, commit):
    client, _, _ = panel
    monkeypatch.setattr("mcp_prism_search.panel.__version__", installed)
    sign_in(client)
    server = client.get("/api/overview").json()["server"]
    assert server["version"] == installed
    assert server["build_kind"] == kind
    assert server["commit"] == commit



def test_provider_activation_requires_configuration_and_removal_disables(panel, monkeypatch):
    client, store, directory = panel
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    sign_in(client)

    def row():
        return next(p for p in client.get("/api/overview").json()["providers"]
                    if p["id"] == "tinyfish")

    assert not row()["configured"] and not row()["enabled"] and not row()["available"]
    assert "tinyfish" not in providers.resolve_chain("all", "general")
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": True}).status_code == 400
    assert "tinyfish" not in store.flags
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": True, "credential": "test-key"}).status_code == 200
    assert row()["configured"] and row()["enabled"] and row()["available"]
    assert "tinyfish" in providers.resolve_chain("all", "general")
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": True, "remove": True}).status_code == 200
    assert not row()["configured"] and not row()["enabled"] and not row()["available"]
    assert not PanelStore(directory).flags["tinyfish"]
    assert "tinyfish" not in providers.resolve_chain("all", "general")
    # A new environment key does not override an explicit persisted disable.
    monkeypatch.setenv("TINYFISH_API_KEY", "fixture-env-key")
    assert row()["configured"] and not row()["enabled"]
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": True}).status_code == 200
    assert row()["available"]


def test_searxng_requires_configured_instance(panel, monkeypatch):
    client, _, _ = panel
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    sign_in(client)
    assert client.put("/api/providers/searxng", headers=HEADERS,
                      json={"enabled": True}).status_code == 400
    monkeypatch.setenv("SEARXNG_URL", "https://search.example.org")
    assert client.put("/api/providers/searxng", headers=HEADERS,
                      json={"enabled": True}).status_code == 200
    assert "searxng" in providers.available_providers()


def test_unconfigured_explicit_provider_is_mcp_error(panel, monkeypatch):
    from mcp_prism_search import server

    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    with pytest.raises(server.ToolError):
        asyncio.run(server.web_search("test", provider="tinyfish"))



def test_tinyfish_native_panel_explicit_only(panel, monkeypatch):
    client, _, _ = panel
    monkeypatch.setenv("TINYFISH_API_KEY", "fixture-key")
    sign_in(client)
    cap = next(c for c in client.get("/api/overview").json()["native"]
               if c["id"] == "tinyfish")
    assert cap["available"] and not cap["automatic_search"]
    assert cap["answer_max_chars"] == 2000
    assert set(cap["efforts"]) == {"deep", "max"}
    assert client.put("/api/settings", headers=HEADERS, json={
        "ai_search_providers": ["tinyfish"],
    }).status_code == 400
    for mode, effort in [("answer", "lite"), ("research", "max")]:
        response = client.post("/api/native", headers=HEADERS, json={
            "mode": mode, "provider": "tinyfish", "query": "test", "effort": effort,
        })
        assert response.status_code == 400
        assert "allow_cost" in response.json()["error"]
    assert client.put("/api/defaults", headers=HEADERS, json={"values": {
        "research_provider": "tinyfish", "research_effort": "lite",
    }}).status_code == 400
    assert client.put("/api/defaults", headers=HEADERS, json={"values": {
        "research_provider": "tinyfish", "research_effort": "deep",
    }}).status_code == 200


def test_panel_accepts_full_unicode_query_in_escaped_json(panel, monkeypatch):
    import json

    from mcp_prism_search.models import SearchResponse

    client, _, _ = panel
    sign_in(client)

    async def retrieve(query, **kwargs):
        return SearchResponse(query=query, provider="keyless", results=[], cached=False, error=None)

    monkeypatch.setattr("mcp_prism_search.panel.aggregated_search", retrieve)
    query = chr(0x1F600) * 4000
    encoded = json.dumps({"query": query, "mode": "keyless"}, ensure_ascii=True)
    assert len(encoded) > 16384
    response = client.post("/api/search", headers={
        **HEADERS, "Content-Type": "Application/JSON; charset=utf-8",
    }, content=encoded)
    assert response.status_code == 200 and response.json()["query"] == query
    assert client.post("/api/search", headers={
        **HEADERS, "Content-Type": "application/json",
    }, content=" " * 65537).status_code == 400


@pytest.mark.parametrize("value", ["abc def", "abc" + chr(0x1F600), "abc" + chr(127)])
def test_panel_rejects_credentials_that_cannot_be_sent_as_api_keys(panel, value):
    client, store, _ = panel
    sign_in(client)
    assert client.put("/api/providers/tinyfish", headers=HEADERS,
                      json={"enabled": True, "credential": value}).status_code == 400
    assert "TINYFISH_API_KEY" not in store.credentials


def test_whitespace_only_environment_key_is_not_configured(panel, monkeypatch):
    client, _, _ = panel
    monkeypatch.setenv("TINYFISH_API_KEY", "   ")
    sign_in(client)
    row = next(p for p in client.get("/api/overview").json()["providers"]
               if p["id"] == "tinyfish")
    assert not row["credential"] and not row["configured"] and not row["available"]
    assert row["managed"]


def test_native_capabilities_publish_provider_answer_limits(panel):
    client, _, _ = panel
    sign_in(client)
    caps = {p["id"]: p for p in client.get("/api/overview").json()["native"]}
    assert caps["youcom"]["answer_max_chars"] == 400
    assert caps["tinyfish"]["answer_max_chars"] == 2000


def test_log_filters_initialize_registry_without_overview(panel, monkeypatch):
    client, _, _ = panel
    monkeypatch.setattr(providers, "SPECS", {})
    sign_in(client)
    assert client.get("/api/logs?provider=keenable").status_code == 200
    assert client.get("/api/logs?provider=unknown").status_code == 400


@pytest.mark.parametrize("value", ["key with spaces", " leading", "trailing ", "chavé"])
def test_invalid_environment_api_keys_are_not_available(panel, monkeypatch, value):
    client, _, _ = panel
    monkeypatch.setenv("EXA_API_KEY", value)
    sign_in(client)
    row = next(p for p in client.get("/api/overview").json()["providers"] if p["id"] == "exa")
    assert not row["configured"] and not row["available"] and row["managed"]
    assert "exa" not in providers.available_providers()


@pytest.mark.parametrize("value", [
    "file:///etc/passwd", "https://example.org/search?format=json",
    "https://example.org/#fragment", "https://example.org:wrong",
])
def test_malformed_searxng_instance_is_not_available(panel, monkeypatch, value):
    client, _, _ = panel
    monkeypatch.setenv("SEARXNG_URL", value)
    sign_in(client)
    row = next(p for p in client.get("/api/overview").json()["providers"]
               if p["id"] == "searxng")
    assert not row["configured"] and not row["available"]
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080/searx")
    assert "searxng" in providers.available_providers()


@pytest.mark.parametrize("payload", [{"timeout": True}, {"search_ttl": "3600"},
                                    {"fetch_ttl": 3600.5}])
def test_admin_settings_reject_noninteger_json_values(panel, payload):
    client, _, _ = panel
    sign_in(client)
    assert client.put("/api/settings", headers=HEADERS, json=payload).status_code == 400
    assert client.post("/api/search", headers=HEADERS, json={
        "query": "test", "max_results": True,
    }).status_code == 400


def test_panel_rejects_oversized_json(panel):
    client, store, _ = panel
    sign_in(client)
    before = dict(store.options)
    response = client.put("/api/settings", content=b" " * 65537,
                          headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 400
    assert store.options == before
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("media_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_panel_requires_json_content_type(panel, media_type):
    client, store, _ = panel
    sign_in(client)
    before = dict(store.options)
    response = client.put("/api/settings", content=b'{"timeout": 12}',
                          headers={**HEADERS, "Content-Type": media_type})
    assert response.status_code == 400
    assert store.options == before


def test_panel_remote_https_required(panel, tmp_path):
    _, store, _ = panel
    app = create_app(PASSWORD, store, tmp_path / "assets", ["panel.example.org"])
    with TestClient(app, base_url="http://panel.example.org") as client:
        assert client.get("/").status_code == 403
    with TestClient(app, base_url="https://panel.example.org") as client:
        response = client.post("/api/login", json={"password": PASSWORD},
                               headers={**HEADERS, "Origin": "https://panel.example.org"})
        assert response.status_code == 200
        assert "secure" in response.headers["Set-Cookie"].lower()
        assert client.get("/api/overview").status_code == 200


def test_panel_session_expires(tmp_path, monkeypatch):
    import mcp_prism_search.panel as panel_module

    monkeypatch.setattr(panel_module, "SESSION_TTL", 0)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("index")
    app = create_app(PASSWORD, PanelStore(tmp_path / "admin"), assets)
    with TestClient(app, base_url="http://localhost") as client:
        sign_in(client)
        assert client.get("/api/overview").status_code == 401
