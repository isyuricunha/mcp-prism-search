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
