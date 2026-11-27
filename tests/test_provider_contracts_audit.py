"""Provider contract audit, with deterministic transport fixtures and no paid calls."""
import asyncio

import httpx

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
