"""Provider contract audit, with deterministic transport fixtures and no paid calls."""
import asyncio
import json

import httpx
import pytest

from mcp_prism_search.models import SearchRequest


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))


def test_registered_ddgs_contract(monkeypatch):
    import ddgs

    from mcp_prism_search.providers import ddgs_lib

    calls = []

    class Engine:
        def __init__(self, timeout):
            assert timeout == 7

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("closed")

        def text(self, query, **kwargs):
            assert query == "needle"
            assert kwargs == {"max_results": 2, "timelimit": "w",
                              "safesearch": "on", "backend": "yahoo"}
            return [None, {"title": ""}, {"title": " A ", "href": "https://a.example/",
                                         "body": " Evidence "},
                    {"title": "Duplicate", "url": "https://a.example/#x"},
                    {"title": "B", "url": "https://b.example/", "snippet": "Second"}]

    monkeypatch.setattr(ddgs, "DDGS", Engine)
    rows = asyncio.run(ddgs_lib.make_search("yahoo")(
        SearchRequest(query="needle", safe_search="strict", time_filter="week"), 2, 7))
    assert [(r.title, r.snippet) for r in rows] == [("A", "Evidence"), ("B", "Second")]
    assert calls == ["closed"]


def test_ddgs_unregistered_backend(monkeypatch):
    import ddgs

    from mcp_prism_search.providers import ddgs_lib

    def unexpected(*args, **kwargs):
        pytest.fail("Unsupported engine must not construct a client")

    monkeypatch.setattr(ddgs, "DDGS", unexpected)
    with pytest.raises(ValueError, match="Unsupported DDGS backend"):
        asyncio.run(ddgs_lib.make_search("not-a-real-backend")(
            SearchRequest(query="test"), 5, 7))


def test_registered_ddg_html_contract(monkeypatch):
    from mcp_prism_search.providers import ddg_html

    def handler(req):
        assert req.url == httpx.URL(ddg_html.HTML_URL, params={
            "q": "C++", "kp": "1", "df": "m",
        })
        assert req.headers["accept-encoding"] == "identity"
        return httpx.Response(200, text=(
            '<div class="result"><a class="result__a" href="https://example.org"> A </a>'
            '<div class="result__snippet"> Evidence </div></div>'
        ))

    mock_http(monkeypatch, handler)
    rows = asyncio.run(ddg_html.search(
        SearchRequest(query="C++", safe_search="strict", time_filter="month"), 1, 4))
    assert [(r.title, r.url, r.snippet) for r in rows] == [
        ("A", "https://example.org", "Evidence")]


def test_wikipedia_title_and_snippet(monkeypatch):
    from mcp_prism_search.providers import wikipedia

    def handler(req):
        assert req.url.params["action"] == "query"
        assert req.url.params["list"] == "search"
        assert req.url.params["srsearch"] == "São Paulo"
        assert req.url.params["srlimit"] == "20"
        assert "mcp-prism-search" in req.headers["user-agent"]
        return httpx.Response(200, json={"query": {"search": [
            None, {"title": ""}, {"title": "São Paulo", "snippet": "<b>City</b> in Brazil"},
        ]}})

    mock_http(monkeypatch, handler)
    rows = asyncio.run(wikipedia.search(SearchRequest(query="São Paulo"), 50, 5))
    assert rows[0].url == "https://en.wikipedia.org/wiki/S%C3%A3o_Paulo"
    assert rows[0].snippet == "City in Brazil"


@pytest.mark.parametrize("domains,query", [
    (["example.org"], "needle site:example.org"),
    (["example.org", "other.example"], "needle"),
])
def test_brave_domain_pushdown(monkeypatch, domains, query):
    from mcp_prism_search.providers import brave

    monkeypatch.setenv("BRAVE_API_KEY", "fixture-key")

    def handler(req):
        assert req.url.params["q"] == query
        assert req.url.params["count"] == "20"
        assert req.url.params["safesearch"] == "strict"
        assert req.url.params["freshness"] == "py"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "Bad", "url": "javascript:bad"},
            {"title": "Good", "url": "https://example.org"},
            {"title": "Duplicate", "url": "https://example.org"},
        ]}})

    mock_http(monkeypatch, handler)
    rows = asyncio.run(brave.search(SearchRequest(
        query="needle", allowed_domains=domains, time_filter="year", safe_search="strict"),
        50, 5))
    assert [r.title for r in rows] == ["Good"]


def test_exa_domain_contract(monkeypatch):
    from mcp_prism_search.providers import exa

    monkeypatch.setenv("EXA_API_KEY", "fixture-key")

    def handler(req):
        data = json.loads(req.content)
        assert data["includeDomains"] == ["example.org"]
        assert data["excludeDomains"] == ["blocked.example"]
        assert data["numResults"] == 1
        return httpx.Response(200, json={"results": [
            {"title": "", "url": "https://example.org/bad"},
            {"title": "Good", "url": "https://example.org/good", "summary": "Evidence"},
        ]})

    mock_http(monkeypatch, handler)
    rows = asyncio.run(exa.search(SearchRequest(query="test", allowed_domains=["example.org"],
        blocked_domains=["blocked.example"]), 1, 5))
    assert [(r.title, r.snippet) for r in rows] == [("Good", "Evidence")]


@pytest.mark.parametrize("safe", ["off", "moderate", "strict"])
def test_tavily_filter_contract(monkeypatch, safe):
    from mcp_prism_search.providers import tavily

    monkeypatch.setenv("TAVILY_API_KEY", "fixture-key")

    def handler(req):
        data = json.loads(req.content)
        assert data.get("safe_search", False) is (safe != "off")
        assert data["include_domains"] == ["example.org"]
        assert data["exclude_domains"] == ["blocked.example"]
        assert data["time_range"] == "d"
        assert data["auto_parameters"] is False and data["include_answer"] is False
        return httpx.Response(200, json={"results": [
            {"title": "Invalid", "url": "javascript:bad"},
            {"title": "Good", "url": "https://example.org", "content": "Evidence"},
        ]})

    mock_http(monkeypatch, handler)
    rows = asyncio.run(tavily.search(SearchRequest(query="test", safe_search=safe,
        allowed_domains=["example.org"], blocked_domains=["blocked.example"], time_filter="day"),
        1, 5))
    assert len(rows) == 1 and rows[0].snippet == "Evidence"


def test_serper_result_window(monkeypatch):
    from mcp_prism_search.providers import serper

    monkeypatch.setenv("SERPER_API_KEY", "fixture-key")

    def handler(req):
        assert json.loads(req.content) == {"q": "needle", "num": 10}
        return httpx.Response(200, json={"organic": [
            None, {"title": "Invalid", "link": "javascript:bad"},
            {"title": "First", "link": "https://example.org/1", "snippet": "One"},
            {"title": "Second", "link": "https://example.org/2"},
        ]})

    mock_http(monkeypatch, handler)
    rows = asyncio.run(serper.search(SearchRequest(query="needle"), 1, 5))
    assert [r.title for r in rows] == ["First"]
