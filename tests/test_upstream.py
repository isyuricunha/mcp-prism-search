"""Bounded upstream payloads and transport resource cleanup."""
import asyncio

import httpx
import pytest

from mcp_prism_search import upstream
from mcp_prism_search.models import SearchRequest
from mcp_prism_search.providers import keenable


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.read = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("headers,chunks", [
    ({"content-length": "9000"}, [b"x"]),
    ({}, [b"x" * 65536, b"x"]),
    ({"content-encoding": "gzip"}, [b"not a gzip stream"]),
])
def test_bounded_provider_response_closes_stream(monkeypatch, headers, chunks):
    monkeypatch.setattr(upstream, "MAX_RESPONSE_BYTES", 1024)
    stream = Chunks(chunks)
    original = httpx.AsyncClient

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers=headers, stream=stream)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError):
        asyncio.run(keenable.search(SearchRequest(query="test"), 5, 5))
    assert stream.closed
    assert stream.read <= 1


def test_native_oversize_response_does_not_retry(monkeypatch):
    from mcp_prism_search import native

    monkeypatch.setenv("EXA_API_KEY", "fixture-key")
    monkeypatch.setattr(upstream, "MAX_RESPONSE_BYTES", 1024)
    calls = []
    original = httpx.AsyncClient

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-length": "4096"}, stream=Chunks([b"x"]))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(native.NativeError, match="invalid or unavailable"):
        asyncio.run(native.synthesize("question", "exa", "answer", allow_cost=True))
    assert len(calls) == 1


def test_legacy_html_search_bounds_response_before_parsing(monkeypatch):
    from mcp_prism_search import search

    monkeypatch.setattr(upstream, "MAX_RESPONSE_BYTES", 1024)
    original = httpx.AsyncClient
    stream = Chunks([b"x" * 65536])

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)), **kw))
    with pytest.raises(ValueError, match="size limit"):
        asyncio.run(search._search_with_duckduckgo_html(SearchRequest(query="test"), 5))
    assert stream.closed


def test_sse_bounds_total_ignored_comment_traffic(monkeypatch):
    from mcp_prism_search import native

    monkeypatch.setattr(native, "SSE_MAX_STREAM_BYTES", 4096)
    stream = Chunks([b": heartbeat\n\n" * 4096])

    async def consume():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, stream=stream)
        )) as client, client.stream("GET", "https://example.org") as response:
            return [event async for event in native._sse_data(response)]

    with pytest.raises(ValueError, match="stream exceeds"):
        asyncio.run(consume())
    assert stream.closed


def test_bounded_request_never_reads_or_follows_redirect_body():
    calls = []
    stream = Chunks([b"x" * 65536])

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://other.example"},
                              stream=stream)

    async def retrieve():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True,
        ) as client:
            return await upstream.bounded_request(client, "GET", "https://example.org")

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(retrieve())
    assert len(calls) == 1 and stream.read == 0 and stream.closed
