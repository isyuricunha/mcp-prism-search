"""Verify admin consent, one-call synthesis, fallback, and optional API contracts."""
import asyncio
import json

import httpx
import pytest

from mcp_prism_search import ai_search, native, search
from mcp_prism_search.models import SearchRequest

ROW = {"title": "Example", "url": "https://example.org", "content": "Evidence"}


@pytest.fixture
def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setenv("YDC_API_KEY", "test-key")
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    monkeypatch.setenv("JINA_API_KEY", "test-key")
    monkeypatch.setenv("MCP_AI_SEARCH_ENABLED", "true")
    monkeypatch.setenv("MCP_AI_SEARCH_PROVIDERS", "tavily,exa,youcom,brave,jina")


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize(("provider", "url", "body"), [
    ("tavily", "https://api.tavily.com/search", {"answer": "AI", "results": [ROW]}),
    ("exa", "https://api.exa.ai/search", {"results": [ROW], "output": {
        "content": {"answer": "AI"}, "grounding": [{"field": "answer", "citations": [ROW]}],
    }}),
    ("youcom", "https://api.you.com/v1/answer", {"answer": "AI", "citations": [
        {"source": ROW["url"], "excerpts": ["Evidence"]},
    ], "results": {"web": [ROW]}}),
])
def test_combined_one_call(configure, monkeypatch, provider, url, body):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == url
        return httpx.Response(200, json=body)

    mock_http(monkeypatch, handler)
    result = asyncio.run(search.aggregated_search("question", provider=provider))
    assert not result.error
    assert result.results[0].url == ROW["url"]
    assert result.answers[0].answer == "AI"
    assert result.answers[0].provider == provider
    assert result.answer_attempts == [provider + ": ok"]
    assert len(calls) == 1


def test_policy_optout_and_normal_cache(configure, monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [ROW]})

    mock_http(monkeypatch, handler)
    monkeypatch.setenv("MCP_AI_SEARCH_ENABLED", "false")
    normal = asyncio.run(search.aggregated_search(
        "question", provider="tavily", include_answers=True,
    ))
    assert not normal.answers and calls[0]["include_answer"] is False
    monkeypatch.setenv("MCP_AI_SEARCH_ENABLED", "true")
    cached = asyncio.run(search.aggregated_search(
        "question", provider="tavily", include_answers=False,
    ))
    assert cached.cached and not cached.answers and len(calls) == 1
    for kwargs in [
        {"allowed_domains": ["example.org"]}, {"blocked_domains": ["example.org"]},
        {"preferred_domains": ["example.org"]}, {"time_filter": "week"},
        {"safe_search": "strict"},
    ]:
        assert not ai_search.eligible(["tavily"], SearchRequest(query="test", **kwargs), None)
    monkeypatch.setenv("MCP_AI_SEARCH_PROVIDERS", "exa")
    assert not ai_search.eligible(["tavily"], SearchRequest(query="test"), True)


@pytest.mark.parametrize("broken", [True, False])
def test_combined_degrades_without_losing_retrieval(configure, monkeypatch, broken):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if broken and payload["include_answer"] == "basic":
            return httpx.Response(429, json={"secret": "do-not-expose"})
        return httpx.Response(200, json={"results": [ROW]})

    mock_http(monkeypatch, handler)
    result = asyncio.run(search.aggregated_search("question", provider="tavily"))
    assert not result.error and result.results and not result.answers
    assert len(calls) == (2 if broken else 1)
    assert "HTTP 429" in result.answer_attempts[0] if broken else (
        "no answer" in result.answer_attempts[0]
    )


def sse(*events):
    return "\n\n".join("data: " + json.dumps(event) for event in events) + "\n\ndata: [DONE]\n\n"


@pytest.mark.parametrize("provider", ["brave", "jina"])
def test_native_streams(configure, monkeypatch, provider):
    calls = []
    citation = {"number": 2, "url": ROW["url"], "snippet": "Evidence"}
    if provider == "brave":
        deltas = [{"content": "Answer "}, {"content": "<citation>" + json.dumps(citation)},
                  {"content": "</citation>"}]
    else:
        deltas = [{"type": "think", "content": "Do not expose"}, {
            "type": "text", "content": "Answer",
            "annotations": [{"type": "url_citation", "url_citation": ROW}],
        }]

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["stream"] is True
        if provider == "jina":
            assert payload["budget_tokens"] == 20000 and payload["no_direct_answer"]
        else:
            assert payload["enable_citations"]
        return httpx.Response(200, text=sse(*[
            {"choices": [{"delta": delta}]} for delta in deltas
        ]), headers={"Content-Type": "text/event-stream"})

    mock_http(monkeypatch, handler)
    result = asyncio.run(native.synthesize(
        "question", provider, "research" if provider == "jina" else "answer", allow_cost=True,
    ))
    assert result.answer.startswith("Answer")
    assert "Do not expose" not in result.answer
    assert result.sources[0].url == ROW["url"]
    assert result.sources[0].index == (2 if provider == "brave" else 1)
    assert len(calls) == 1


def test_tavily_research_single_job(configure, monkeypatch):
    calls = []
    monkeypatch.setattr(native, "TAVILY_POLL_SECONDS", 0)

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.method == "POST":
            assert json.loads(request.content)["model"] == "mini"
            return httpx.Response(201, json={"request_id": "test-job", "status": "pending"})
        assert str(request.url).endswith("/research/test-job")
        if len(calls) == 2:
            return httpx.Response(200, json={"status": "in_progress"})
        return httpx.Response(200, json={"status": "completed", "content": "Report",
                                         "sources": [ROW]})

    mock_http(monkeypatch, handler)
    result = asyncio.run(native.synthesize("question", "tavily", "research", allow_cost=True))
    assert result.answer == "Report"
    assert [method for method, _ in calls] == ["POST", "GET", "GET"]


def test_separate_generation_failure_preserves_results(configure, monkeypatch):
    from mcp_prism_search import providers
    from mcp_prism_search.models import SearchResult

    async def normal(*args):
        return [SearchResult(title="Example", url=ROW["url"], snippet="Evidence")]

    async def fail(*args):
        raise native.NativeError("HTTP 429")

    monkeypatch.setattr(providers, "run_provider", normal)
    monkeypatch.setattr(ai_search, "separate_answer", fail)
    result = asyncio.run(search.aggregated_search("question", provider="brave"))
    assert not result.error and result.results and not result.answers
    assert result.answer_attempts == ["brave: HTTP 429"]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_enhanced_mcp_structured_output(configure, monkeypatch, mode):
    from mcp import Client

    from mcp_prism_search.server import mcp

    mock_http(monkeypatch, lambda req: httpx.Response(200, json={
        "answer": "AI", "results": [ROW],
    }))

    async def verify():
        async with Client(mcp, mode=mode) as client:
            result = await client.call_tool("web_search", {
                "query": "question", "provider": "tavily", "include_answers": True,
            })
            assert not result.is_error
            assert result.structured_content["answers"][0]["answer"] == "AI"
            assert result.structured_content["results"][0]["url"] == ROW["url"]
    asyncio.run(verify())


def test_parallel_combined_engines(configure, monkeypatch):
    calls = []
    active = peak = 0

    async def handler(req):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append(req.url.host)
        try:
            await asyncio.sleep(0.01)
            if req.url.host == "api.tavily.com":
                return httpx.Response(200, json={"answer": "AI", "results": [ROW]})
            if req.url.host == "api.exa.ai":
                return httpx.Response(200, json={"results": [ROW], "output": {
                    "content": {"answer": "AI"}, "grounding": [
                        {"field": "answer", "citations": [ROW]},
                    ],
                }})
            return httpx.Response(200, json={"answer": "AI", "citations": [
                {"source": ROW["url"]},
            ], "results": {"web": [ROW]}})
        finally:
            active -= 1

    mock_http(monkeypatch, handler)
    result = asyncio.run(search.aggregated_search(
        "question", providers=["tavily", "exa", "youcom"],
    ))
    assert [a.provider for a in result.answers] == ["tavily", "exa", "youcom"]
    assert len(calls) == 3 and peak <= 2
    assert len(result.results) == 1


def test_auto_keyless_winner_preserves_cache(configure, monkeypatch):
    from mcp_prism_search import providers
    from mcp_prism_search.models import SearchResult

    calls = []

    async def retrieve(name, *args):
        calls.append(name)
        return [SearchResult(title="Example", url=ROW["url"], snippet="Evidence")]

    monkeypatch.setattr(providers, "resolve_chain", lambda *args: ["keenable", "tavily"])
    monkeypatch.setattr(providers, "run_provider", retrieve)
    first = asyncio.run(search.aggregated_search("question", provider="auto"))
    second = asyncio.run(search.aggregated_search("question", provider="auto"))
    assert first.provider == "keenable" and not first.answers
    assert second.cached and not second.answers and calls == ["keenable"]
