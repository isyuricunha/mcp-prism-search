"""Tests for keyed/keyless provider modules (mocked HTTP)."""

import asyncio
import contextlib

import httpx
import pytest

from mcp_prism_search.models import SearchRequest
from mcp_prism_search.providers import brave, exa, jina, serper, tavily, youcom


def _request(query: str = "test") -> SearchRequest:
    return SearchRequest(query=query)


@contextlib.contextmanager
def patched_client(module, handler):
    """Patch module.httpx.AsyncClient with a MockTransport-backed client."""
    real_client = httpx.AsyncClient

    class Wrapper(real_client):
        def __init__(self, **kwargs):
            kwargs.pop("follow_redirects", None)
            super().__init__(transport=httpx.MockTransport(handler), timeout=10)

    original = module.httpx.AsyncClient
    module.httpx.AsyncClient = Wrapper
    try:
        yield
    finally:
        module.httpx.AsyncClient = original


def test_tavily_requires_key(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        asyncio.run(tavily.search(_request(), 5, 10))


def test_tavily_with_key_uses_bearer(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"results": []})

    with patched_client(tavily, handler):
        asyncio.run(tavily.search(_request("test"), 5, 10))

    assert captured["auth"] == "Bearer tvly-test"


def test_exa_requires_key(monkeypatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        asyncio.run(exa.search(_request("test"), 5, 10))


def test_exa_parses_results(monkeypatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "k")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={"results": [{"title": "E", "url": "https://e.example/", "text": "body"}]},
        )

    with patched_client(exa, handler):
        results = asyncio.run(exa.search(_request("test"), 5, 10))

    assert captured["key"] == "k"
    assert results[0].url == "https://e.example/"


def test_brave_parses_web_results(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "b")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "web": {"results": [
                    {"title": "B", "url": "https://b.example/", "description": "d"}
                ]}
            },
        )

    with patched_client(brave, handler):
        results = asyncio.run(brave.search(_request("test"), 5, 10))

    assert results[0].url == "https://b.example/"


def test_serper_parses_organic(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "s")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"organic": [{"title": "S", "link": "https://s.example/", "snippet": "sn"}]},
        )

    with patched_client(serper, handler):
        results = asyncio.run(serper.search(_request("test"), 5, 10))

    assert results[0].title == "S"
    assert results[0].url == "https://s.example/"


def test_youcom_parses_v1_results(monkeypatch) -> None:
    monkeypatch.setenv("YDC_API_KEY", "y")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": {"web": [
                {"title": "Y", "url": "https://y.example/", "description": "d"}
            ]}},
        )

    with patched_client(youcom, handler):
        results = asyncio.run(youcom.search(_request("test"), 5, 10))

    assert results[0].url == "https://y.example/"


def test_jina_requires_key_and_parses_data(monkeypatch) -> None:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JINA_API_KEY"):
        asyncio.run(jina.search(_request("test"), 5, 10))

    monkeypatch.setenv("JINA_API_KEY", "j")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"title": "J", "url": "https://j.example/", "description": "d"}]},
        )

    with patched_client(jina, handler):
        results = asyncio.run(jina.search(_request("test"), 5, 10))

    assert results[0].url == "https://j.example/"

def test_youcom_shared_parser_skips_invalid_rows_and_snippets():
    data = {"results": {"web": [None, {"url": 3, "title": "Bad"},
            {"url": "https://example.org", "title": "  Example ",
             "snippets": [None, 3, "Valid evidence"]}]}}
    rows = youcom.parse_web_results(data, 5)
    assert len(rows) == 1 and rows[0].title == "Example"
    assert rows[0].snippet == "Valid evidence"
    with pytest.raises(ValueError, match="web results"):
        youcom.parse_web_results({"results": {"web": {}}}, 5)
