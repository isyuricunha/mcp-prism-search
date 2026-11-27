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

    client, _, _ = panel
    sign_in(client)
    assert client.get("/api/overview").json()["server"] == {
        "version": __version__,
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
                       json={"query": "test"}).status_code == 400
