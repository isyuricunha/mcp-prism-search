"""TinySearch request contract, pagination and opt-in behavior."""

import asyncio

import httpx
import pytest

from mcp_prism_search import providers, search
from mcp_prism_search.models import SearchRequest
from mcp_prism_search.providers import tinyfish


def mock_http(monkeypatch, handler):
    client = httpx.AsyncClient

    def wrapper(**kwargs):
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", wrapper)


def test_optional_registry(monkeypatch):
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    providers.build_registry()
    assert "tinyfish" not in providers.available_providers()
    monkeypatch.setenv("TINYFISH_API_KEY", "test")
    assert providers.available_providers("general")["tinyfish"].search is tinyfish.search


def test_missing_key(monkeypatch):
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TINYFISH_API_KEY"):
        asyncio.run(tinyfish.search(SearchRequest(query="test"), 5, 10))


def test_contract_and_pagination(monkeypatch):
    monkeypatch.setenv("TINYFISH_API_KEY", "test")
    pages = []

    def handler(req):
        assert req.method == "GET"
        assert req.url.host == "api.search.tinyfish.ai"
        assert req.headers["X-API-Key"] == "test"
        params = req.url.params
        assert params["query"] == "Python"
        assert params["recency_minutes"] == "10080"
        assert params["include_domains"] == "python.org"
        assert params["exclude_domains"] == "example.org"
        page = int(params["page"])
        pages.append(page)
        return httpx.Response(200, json={"results": [
            {"url": "https://python.org/a", "title": "A", "snippet": "Text A"},
            {"url": f"https://python.org/{page}", "title": "B", "snippet": "Text B"},
            {"url": "ftp://invalid.test", "title": "Invalid"},
        ]})

    mock_http(monkeypatch, handler)
    rows = asyncio.run(tinyfish.search(SearchRequest(
        query="Python", time_filter="week", allowed_domains=["python.org"],
        blocked_domains=["example.org"],
    ), 3, 10))
    assert pages == [0, 1]
    assert len(rows) == 3
    assert rows[0].snippet == "Text A"


def test_empty_page_ends_pagination(monkeypatch):
    monkeypatch.setenv("TINYFISH_API_KEY", "test")
    pages = []

    def handler(req):
        pages.append(req.url.params["page"])
        return httpx.Response(200, json={"results": []})

    mock_http(monkeypatch, handler)
    assert asyncio.run(tinyfish.search(SearchRequest(query="Python"), 30, 10)) == []
    assert pages == ["0"]


@pytest.mark.parametrize("status", [401, 402, 429, 503])
def test_no_retry_and_redacted_errors(monkeypatch, tmp_path, status):
    from mcp_prism_search.cache import JsonFileCache

    monkeypatch.setenv("TINYFISH_API_KEY", "secret-test")
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"error": "secret-test"})

    mock_http(monkeypatch, handler)
    response = asyncio.run(search.aggregated_search(
        "Python", provider="tinyfish", cache=JsonFileCache(tmp_path, "search", 60),
    ))
    assert len(calls) == 1
    assert response.error
    assert str(status) in response.error
    assert "secret-test" not in response.error
