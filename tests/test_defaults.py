"""The MCP contract, not the dashboard, owns persistent preferences."""
import asyncio
import time

import httpx
import pytest
from mcp import Client

from mcp_prism_search import defaults, fetch, native, providers, runtime, search
from mcp_prism_search.cache import JsonFileCache
from mcp_prism_search.models import SearchResponse, SearchResult
from mcp_prism_search.panel_store import PanelStore
from mcp_prism_search.server import mcp

ROW = SearchResult(title="Example", url="https://example.org/page", snippet="Evidence")


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch, tmp_path):
    for env in defaults.ENV.values():
        monkeypatch.delenv(env, raising=False)
    for env in ["MCP_DISABLED_PROVIDERS", "MCP_AI_SEARCH_ENABLED",
                "MCP_AI_SEARCH_PROVIDERS", "MCP_WEBUI_DATA_DIR", "MCP_STATE_DIR",
                "MCP_PREFERRED_PROVIDER"]:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path / "cache"))
    old = runtime.state()
    runtime.install(None)
    yield
    runtime.install(old)


def test_environment_store_and_builtin_precedence(monkeypatch, tmp_path):
    assert defaults.effective_defaults().search_max_results == 10
    directory = tmp_path / "state"
    monkeypatch.setenv("MCP_STATE_DIR", str(directory))
    assert runtime.configured_store() is None and not directory.exists()
    store = runtime.configured_store(create=True)
    runtime.install(store)
    defaults.update_defaults(store, {"search_max_results": 7, "fetch_max_chars": 9000})
    store.update("tinyfish", "TINYFISH_API_KEY", True, "fixture-key", False)
    store.update("ddg_html", None, False, None, False)
    runtime.install(runtime.configured_store())
    assert defaults.effective_defaults().search_max_results == 7
    assert defaults.effective_defaults().fetch_max_chars == 9000
    assert runtime.setting("TINYFISH_API_KEY") == "fixture-key"
    assert not runtime.enabled("ddg_html")
    monkeypatch.setenv(defaults.ENV["search_max_results"], "3")
    assert defaults.effective_defaults().search_max_results == 3
    with pytest.raises(ValueError, match="managed"):
        defaults.update_defaults(runtime.state(), {"search_max_results": 4})
    monkeypatch.delenv(defaults.ENV["search_max_results"])
    assert defaults.effective_defaults().search_max_results == 7
    defaults.update_defaults(runtime.state(), {"search_max_results": None})
    assert defaults.effective_defaults().search_max_results == 10


def test_legacy_state_dir_and_environment_disable(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_WEBUI_DATA_DIR", str(tmp_path / "old"))
    store = runtime.configured_store(create=True)
    store.update("keenable", None, True, None, False)
    runtime.install(runtime.configured_store())
    monkeypatch.setenv("MCP_DISABLED_PROVIDERS", "keenable, exa")
    assert not runtime.enabled("keenable")
    assert "keenable" not in providers.available_providers()
    assert runtime.disabled_by_env("exa")
    monkeypatch.delenv("MCP_DISABLED_PROVIDERS")
    assert runtime.enabled("keenable")


def test_invalid_defaults_are_atomic(monkeypatch, tmp_path):
    store = PanelStore(tmp_path / "state")
    runtime.install(store)
    with pytest.raises(ValueError, match="requires at least one"):
        defaults.update_defaults(store, {"search_mode": "selected", "search_max_results": 4})
    assert not store.options
    with pytest.raises(ValueError, match="Invalid server defaults"):
        defaults.update_defaults(store, {"search_providers": ["unknown"]})
    with pytest.raises(ValueError, match="unsupported"):
        defaults.update_defaults(store, {"research_provider": "brave", "research_effort": "lite"})
    monkeypatch.setenv(defaults.ENV["search_max_results"], "999")
    with pytest.raises(ValueError, match="search_max_results"):
        defaults.effective_defaults()


def test_mcp_minimal_search_inherits_and_explicit_overrides(monkeypatch):
    env = {
        "search_mode": "selected", "search_providers": "keenable",
        "search_max_results": "3", "search_safe_search": "strict",
        "search_recency": "week", "allowed_domains": "example.org",
        "preferred_domains": "example.org",
    }
    for key, value in env.items():
        monkeypatch.setenv(defaults.ENV[key], value)
    calls = []

    async def retrieve(name, request, count, timeout):
        calls.append((name, request, count))
        return [ROW]

    monkeypatch.setattr(providers, "run_provider", retrieve)

    async def verify():
        for protocol in ("auto", "legacy"):
            async with Client(mcp, mode=protocol) as client:
                listing = await client.list_tools()
                tools = listing.tools if hasattr(listing, "tools") else listing[0]
                tool = next(t for t in tools if t.name == "web_search")
                assert tool.input_schema.get("required") == ["query"]
                result = await client.call_tool("web_search", {"query": "query-" + protocol})
                assert not result.is_error
                name, request, _ = calls[-1]
                assert name == "keenable" and request.max_results == 3
                assert request.safe_search == "strict" and request.time_filter == "week"
                assert request.allowed_domains == ["example.org"]
                result = await client.call_tool("web_search", {
                    "query": "override-" + protocol, "provider": "freeserp",
                    "max_results": 2, "safe_search": "off", "time_filter": "any",
                    "allowed_domains": [], "preferred_domains": [],
                })
                assert not result.is_error
                name, request, _ = calls[-1]
                assert name == "freeserp" and request.max_results == 2
                assert request.safe_search == "off" and request.time_filter is None
                assert not request.allowed_domains and not request.preferred_domains
    asyncio.run(verify())


def test_selected_default_never_widens_and_keyless_excludes_keyed(monkeypatch):
    monkeypatch.setenv(defaults.ENV["search_mode"], "selected")
    monkeypatch.setenv(defaults.ENV["search_providers"], "keenable,freeserp")
    monkeypatch.setenv("MCP_DISABLED_PROVIDERS", "keenable")
    monkeypatch.setenv("EXA_API_KEY", "fixture")
    calls = []

    async def retrieve(name, request, count, timeout):
        calls.append(name)
        return [ROW]

    monkeypatch.setattr(providers, "run_provider", retrieve)
    result = asyncio.run(search.aggregated_search("query"))
    assert not result.error and calls == ["freeserp"]
    monkeypatch.setenv("MCP_DISABLED_PROVIDERS", "keenable,freeserp")
    result = asyncio.run(search.aggregated_search("new-query"))
    assert result.error == "No default selected providers are available"
    assert calls == ["freeserp"]
    assert "exa" not in providers.resolve_chain("keyless", "general")
    assert all(not providers.SPECS[n].needs for n in providers.resolve_chain("keyless", "general"))


def test_vertical_and_fetch_defaults(monkeypatch):
    monkeypatch.setenv(defaults.ENV["vertical_source"], "hackernews")
    monkeypatch.setenv(defaults.ENV["vertical_max_results"], "2")
    monkeypatch.setenv(defaults.ENV["search_safe_search"], "strict")
    monkeypatch.setenv(defaults.ENV["fetch_max_chars"], "4000")
    calls = []

    async def retrieve(name, request, count, timeout):
        calls.append((name, request))
        return [ROW]

    async def valid(url):
        return None

    monkeypatch.setattr(providers, "run_provider", retrieve)
    monkeypatch.setattr(fetch, "validate_fetch_url", valid)
    cache = JsonFileCache(fetch.build_fetch_cache().root_dir, "fetch", 60)
    from mcp_prism_search.models import FetchRequest, FetchResponse
    request = FetchRequest(url=ROW.url, max_chars=4000)
    cache.set(fetch._cache_payload(request) | {"_max_bytes": fetch.get_fetch_max_bytes()},
              FetchResponse(url=ROW.url, final_url=ROW.url, title="Example", content="Evidence",
                            content_type="text/plain", cached=False, success=True,
                            error=None).model_dump())
    result = asyncio.run(search.aggregated_search("query", kind="vertical"))
    assert not result.error
    assert calls[-1][0] == "hackernews" and calls[-1][1].max_results == 2
    assert calls[-1][1].safe_search == "off"
    result = asyncio.run(fetch.web_fetch(ROW.url, cache=cache))
    assert result.cached


def test_legacy_deep_inherits_resolved_fetch_limits(monkeypatch):
    monkeypatch.setenv(defaults.ENV["deep_max_pages"], "1")
    monkeypatch.setenv(defaults.ENV["fetch_max_chars"], "2000")
    monkeypatch.setenv(defaults.ENV["search_max_results"], "4")
    monkeypatch.setenv(defaults.ENV["search_recency"], "week")
    monkeypatch.setenv(defaults.ENV["deep_concurrency"], "30")
    assert fetch.get_max_concurrency() == 12
    calls = []

    async def retrieve(**kwargs):
        assert kwargs["max_results"] == 4 and kwargs["time_filter"] == "week"
        return SearchResponse(query="q", provider="ddgs", results=[ROW, ROW],
                              cached=False, error=None)

    async def get_page(url, max_chars):
        calls.append(max_chars)
        from mcp_prism_search.models import FetchResponse
        return FetchResponse(url=url, final_url=url, title="Example", content="Evidence",
                             content_type="text/plain", cached=False, success=True, error=None)

    monkeypatch.setattr(fetch, "ddg_search", retrieve)
    monkeypatch.setattr(fetch, "web_fetch", get_page)
    result = asyncio.run(fetch.ddg_deep_search("q"))
    assert len(result.pages) == 1 and calls == [2000]


def test_native_provider_default_does_not_grant_cost_consent(monkeypatch):
    monkeypatch.setenv(defaults.ENV["answer_provider"], "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "fixture-key")
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"answer": "AI", "results": [ROW.model_dump()]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(native.NativeError, match="allow_cost"):
        asyncio.run(native.synthesize("query", None, "answer"))
    assert not calls
    result = asyncio.run(native.synthesize("query", None, "answer", allow_cost=True))
    assert result.provider == "tavily" and len(calls) == 1


def test_freshness_cache_never_exceeds_regular_ttl(monkeypatch, tmp_path):
    monkeypatch.setenv(defaults.ENV["news_cache_ttl"], "100")
    cache = JsonFileCache(tmp_path, "search", 1)
    payload = {"query": "latest news"}
    cache.set(payload, {"answer": "old"})
    monkeypatch.setattr(time, "time", lambda: 10**12)
    assert search.search_ttl_for_query("latest news") == 100
    assert cache.get(payload, ttl_seconds=100) is None
