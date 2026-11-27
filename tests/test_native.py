"""Provider-native synthesis is explicit, bounded and isolated from search."""
import asyncio
import json

import httpx
import pytest
from mcp import Client

from mcp_prism_search import native
from mcp_prism_search.server import mcp


@pytest.mark.parametrize(("provider", "mode", "env", "url", "body", "response"), [
    ("tavily", "answer", "TAVILY_API_KEY", "https://api.tavily.com/search",
     {"query": "question", "search_depth": "basic", "include_answer": "basic",
      "max_results": 5, "auto_parameters": False},
     {"answer": "Answer", "results": [{"url": "https://example.org", "title": "Source"}]}),
    ("exa", "answer", "EXA_API_KEY", "https://api.exa.ai/answer",
     {"query": "question"},
     {"answer": "Answer", "citations": [{"url": "https://example.org", "title": "Source"}]}),
    ("youcom", "answer", "YDC_API_KEY", "https://api.you.com/v1/answer",
     {"query": "question"},
     {"answer": "Answer [[1]]", "citations": [{"source": "https://example.org",
                                             "excerpts": ["Source"]}]}),
    ("exa", "research", "EXA_API_KEY", "https://api.exa.ai/search",
     {"query": "question", "type": "deep-lite", "numResults": 10,
      "outputSchema": {"type": "object", "required": ["answer"], "properties": {
          "answer": {"type": "string", "description": "Grounded research synthesis"},
      }}},
     {"output": {"content": {"answer": "Answer"}, "grounding": [
         {"field": "answer", "citations": [{"url": "https://example.org", "title": "Source"}]},
     ]}}),
    ("youcom", "research", "YDC_API_KEY", "https://api.you.com/v1/research",
     {"input": "question", "research_effort": "lite"},
     {"output": {"content": "Answer [[1]]", "content_type": "text",
                 "sources": [{"url": "https://example.org", "title": "Source"}]}}),
])
def test_native_contract(monkeypatch, provider, mode, env, url, body, response):
    monkeypatch.setenv(env, "test-not-real-secret")
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == url
        assert json.loads(request.content) == body
        assert "test-not-real-secret" in (
            request.headers.get("authorization") or request.headers.get("x-api-key")
        )
        return httpx.Response(200, json=response)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(native.NativeError, match="allow_cost"):
        asyncio.run(native.synthesize("question", provider, mode))
    assert not calls
    result = asyncio.run(native.synthesize(" question ", provider, mode, allow_cost=True))
    assert result.provider == provider and result.mode == mode
    assert result.sources[0].url == "https://example.org"
    assert result.sources[0].index == 1
    assert len(calls) == 1


def test_native_errors_redacted_and_no_retry(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-private-secret")
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "test-private-secret"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(native.NativeError, match="HTTP 429") as exc:
        asyncio.run(native.synthesize("question", "exa", "answer", allow_cost=True))
    assert "test-private-secret" not in str(exc.value)
    assert len(calls) == 1


def test_preserve_citation_indices_and_bound_context():
    data = {"answer": "x" * 25000, "citations": [
        {"source": "javascript:alert(1)"},
        {"source": "https://example.org", "excerpts": ["e" * 5000]},
        {"source": "https://user:pass@example.org"},
    ]}
    result = native._decode("youcom", "answer", data, "lite")
    assert result.truncated and len(result.answer) == 24000
    assert [s.index for s in result.sources] == [1, 2, 3]
    assert [s.url for s in result.sources] == [None, "https://example.org", None]
    assert len(result.sources[1].excerpt) == 1200


def test_native_tools_cost_gate():
    async def run():
        async with Client(mcp) as client:
            for name, provider in [("web_answer", "tavily"), ("web_research", "youcom")]:
                result = await client.call_tool(name, {"query": "test", "provider": provider})
                assert result.is_error
    asyncio.run(run())


def test_exa_unsupported_effort(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    with pytest.raises(native.NativeError, match="Unsupported research effort"):
        asyncio.run(native.synthesize(
            "question", "exa", "research", effort="standard", allow_cost=True
        ))


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_mcp_native_structured_output(mode, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    original = httpx.AsyncClient

    def handler(request):
        assert str(request.url) == "https://api.exa.ai/answer"
        return httpx.Response(200, json={
            "answer": "Grounded answer",
            "citations": [{"url": "https://example.org", "title": "Example"}],
        })

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))

    async def run():
        async with Client(mcp, mode=mode) as client:
            result = await client.call_tool("web_answer", {
                "query": "question", "provider": "exa", "allow_cost": True,
            })
            assert not result.is_error
            assert result.structured_content["answer"] == "Grounded answer"
            assert result.structured_content["sources"][0]["url"] == "https://example.org"
    asyncio.run(run())
