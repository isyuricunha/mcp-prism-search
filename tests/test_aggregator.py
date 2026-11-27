"""Regression coverage for provider selection, caching and fan-out."""

import asyncio
import json

import httpx
import pytest

from mcp_prism_search import providers, search, server
from mcp_prism_search.cache import JsonFileCache
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.providers import freeserp, keenable, mwmbl, searxng
from mcp_prism_search.providers.base import HEALTH, ProviderSpec


def result(url, title="needle"):
    return SearchResult(title=title, url=url, snippet="needle")


@pytest.fixture
def registry(monkeypatch):
    specs = {}
    calls = []
    monkeypatch.setattr(providers, "SPECS", specs)
    HEALTH.clear()

    def register(name, rows=(), *, fail=False, slow=False):
        async def run(request, count, timeout):
            calls.append((name, count))
            if slow:
                await asyncio.sleep(10)
            if fail:
                raise RuntimeError("sensitive-token")
            return list(rows)

        specs[name] = ProviderSpec(name, "general", None, name, run)

    yield register, calls
    HEALTH.clear()


def test_registry_pruned_and_tavily_requires_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    providers.build_registry()
    assert len(providers.SPECS) == 17
    assert not {"bing_html", "ddg_lite", "ddgs:bing", "ddgs:yandex", "ddgs:brave"} & (
        providers.SPECS.keys()
    )
    assert "tavily" not in providers.available_providers()
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    assert "tavily" in providers.available_providers()


def test_single_cache_filters_and_real_provider(registry, tmp_path):
    register, calls = registry
    register("keenable", [
        result("https://a.test/page"), result("https://a.test/page#fragment"),
        result("https://b.test/"), result("https://sub.a.test/page"),
    ])
    cache = JsonFileCache(tmp_path, "search", 3600)
    kwargs = dict(provider="keenable", allowed_domains=["a.test"], cache=cache)
    first = asyncio.run(search.aggregated_search("needle", **kwargs))
    second = asyncio.run(search.aggregated_search("needle", **kwargs))
    assert first.provider == "keenable"
    assert len(first.results) == 2
    assert second.cached
    assert len(calls) == 1


def test_selected_parallel_deduplicates_and_prefers_domains(registry, tmp_path):
    register, calls = registry
    register("keenable", [result("https://a.test/"), result("https://b.test/")])
    register("freeserp", [result("https://a.test"), result("https://c.test/")])
    register("bing_rss", [result("https://unexpected.test/")])
    cache = JsonFileCache(tmp_path, "search", 3600)
    response = asyncio.run(search.aggregated_search(
        "needle", providers=["keenable", "freeserp", "keenable"],
        preferred_domains=["c.test"], blocked_domains=["b.test"], cache=cache,
    ))
    assert response.provider == "selected"
    assert [r.url for r in response.results] == ["https://c.test/", "https://a.test/"]
    assert [n for n, _ in calls] == ["keenable", "freeserp"]


def test_auto_health_and_redacted_failures(registry, tmp_path):
    register, calls = registry
    register("keenable", fail=True)
    register("freeserp", [result("https://a.test/")])
    cache = JsonFileCache(tmp_path, "search", 3600)
    first = asyncio.run(search.aggregated_search("first", cache=cache))
    second = asyncio.run(search.aggregated_search("second", cache=cache))
    assert first.provider == second.provider == "freeserp"
    assert first.provider_attempts[0] == "keenable: error (RuntimeError)"
    assert [n for n, _ in calls] == ["keenable", "freeserp", "freeserp"]


def test_parallel_timeout_partial_not_cached(registry, tmp_path, monkeypatch):
    register, calls = registry
    register("keenable", slow=True)
    register("freeserp", [result("https://a.test/")])
    monkeypatch.setattr(search, "get_ddg_timeout_seconds", lambda: 0.02)
    cache = JsonFileCache(tmp_path, "search", 3600)

    async def run():
        async with asyncio.timeout(0.5):
            return await search.aggregated_search("needle", provider="all", cache=cache)

    response = asyncio.run(run())
    assert response.error is None
    assert "keenable: error (timeout)" in response.provider_attempts
    assert not asyncio.run(run()).cached
    assert len(calls) == 4


@pytest.mark.parametrize("kwargs", [
    {"provider": "unknown"},
    {"providers": []},
    {"providers": ["unknown"]},
    {"provider": "keenable", "providers": ["keenable"]},
])
def test_invalid_selection_is_error(registry, tmp_path, kwargs):
    register, calls = registry
    register("keenable", [result("https://a.test/")])
    cache = JsonFileCache(tmp_path, "search", 3600)
    response = asyncio.run(search.aggregated_search("needle", cache=cache, **kwargs))
    assert response.error
    assert not calls


def test_mcp_unknown_provider_surfaces_tool_error(registry):
    register, calls = registry
    register("keenable")
    with pytest.raises(server.ToolError, match="Unknown provider"):
        asyncio.run(server.web_search("needle", provider="unknown"))


@pytest.mark.parametrize(("module", "data", "check"), [
    (keenable, {"results": [{"url": "https://a.test", "title": "A", "text": "B"}]},
     lambda req: json.loads(req.content)["max_results"] == 5),
    (freeserp, {"results": [{"url": "https://a.test", "title": "A", "snippet": "B"}]},
     lambda req: req.url.params["index"] == "web"),
    (mwmbl, {"results": [{"url": "https://a.test", "title": "A", "content": "B"}]},
     lambda req: req.url.path == "/api/v2/search/" and req.url.params["q"] == "needle"),
    (searxng, {"results": [{"url": "https://a.test", "title": "A", "content": "B"}]},
     lambda req: req.url.params["time_range"] == "month"),
])
def test_corrected_provider_contract(module, data, check, monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "https://searx.example")
    client = httpx.AsyncClient

    def handler(req):
        assert check(req)
        return httpx.Response(200, json=data)

    def wrapper(**kwargs):
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", wrapper)
    rows = asyncio.run(module.search(SearchRequest(query="needle", time_filter="month"), 5, 2))
    assert rows[0].title == "A"
    assert rows[0].snippet == "B"


def test_provider_health_recovers_after_recent_successes():
    from mcp_prism_search.providers.base import ProviderHealth

    health = ProviderHealth()
    for _ in range(100):
        health.bump("error")
    assert health.score == 0
    for _ in range(32):
        health.bump("ok")
    assert health.score == 1 and health.total == 132
    assert len(health.recent) == 32


def test_provider_disabled_after_chain_resolution_is_not_called(monkeypatch):
    monkeypatch.setenv("MCP_DISABLED_PROVIDERS", "keenable")
    with pytest.raises(ValueError, match="no longer available"):
        asyncio.run(providers.run_provider("keenable", SearchRequest(query="test"), 5, 5))


def test_hackernews_fallback_requires_valid_story_id(monkeypatch):
    from mcp_prism_search.providers import hackernews

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"hits": [
            {"title": "Missing ID"},
            {"title": "Injected ID", "objectID": "42&other=1"},
            {"title": "Valid story", "objectID": "42"},
        ]})), **kw))
    rows = asyncio.run(hackernews.search(SearchRequest(query="test"), 1, 5))
    assert len(rows) == 1 and rows[0].url.endswith("id=42")


def test_bing_rss_invalid_rows_do_not_consume_result_limit(monkeypatch):
    from mcp_prism_search.providers import bing_rss

    original = httpx.AsyncClient
    xml = """<rss><channel>
      <item><title>Bad</title><link>httpx://evil.example</link></item>
      <item><title>Bad port</title><link>https://example.org:wrong</link></item>
      <item><title></title><link>https://example.org/empty</link></item>
      <item><title>Good</title><link>https://example.org/good</link></item>
      </channel></rss>"""
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=xml)), **kw))
    rows = asyncio.run(bing_rss.search(SearchRequest(query="test"), 1, 5))
    assert [row.title for row in rows] == ["Good"]


def test_youcom_public_falls_back_to_valid_text_content(monkeypatch):
    from types import SimpleNamespace

    from mcp_prism_search.providers import youcom_keyless

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call_tool(self, *args):
            return SimpleNamespace(is_error=False, structured_content={"results": []},
                                   content=[
                SimpleNamespace(type="text", text='{"results": {"web": null}}'),
                SimpleNamespace(type="text", text=json.dumps({"results": {"web": [
                    {"title": "Good", "url": "https://example.org"}
                ]}})),
            ])

    monkeypatch.setattr(youcom_keyless, "Client", Client)
    rows = asyncio.run(youcom_keyless.search(SearchRequest(query="test"), 5, 5))
    assert [row.title for row in rows] == ["Good"]


def test_mwmbl_pacing_lock_is_scoped_to_event_loop(monkeypatch):
    from mcp_prism_search.providers import mwmbl

    original = httpx.AsyncClient
    async def response(req):
        await asyncio.sleep(0)
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(response), **kw))
    async def exercise():
        await asyncio.gather(*[mwmbl.search(SearchRequest(query="test"), 1, 2)
                               for _ in range(2)])

    asyncio.run(exercise())
    asyncio.run(exercise())


@pytest.mark.parametrize("module", [keenable, freeserp, mwmbl, searxng])
def test_invalid_provider_urls_do_not_consume_result_limit(monkeypatch, module):
    monkeypatch.setenv("SEARXNG_URL", "https://search.example")
    original = httpx.AsyncClient
    rows = [
        {"title": "Invalid", "url": "httpx://example.org"},
        {"title": "Invalid port", "url": "https://example.org:bad"},
        {"title": "Valid", "url": "https://example.org/valid"},
    ]
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
            "results": rows,
        })), **kw))
    assert [row.title for row in asyncio.run(
        module.search(SearchRequest(query="test"), 1, 5)
    )] == ["Valid"]
