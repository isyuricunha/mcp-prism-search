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
        {"safe_search": "strict"}, {"search_window": 20},
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
    assert bool(result.error) is broken
    assert bool(result.results) is not broken
    assert not result.answers
    assert len(calls) == 1
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


def test_auto_prioritizes_authorized_ai_and_optout_preserves_cache(configure, monkeypatch):
    from mcp_prism_search import providers
    from mcp_prism_search.models import SearchResult

    calls = []

    async def retrieve(name, *args):
        calls.append(name)
        return [SearchResult(title="Example", url=ROW["url"], snippet="Evidence")]

    monkeypatch.setattr(providers, "resolve_chain", lambda *args: ["keenable", "tavily"])
    monkeypatch.setattr(providers, "run_provider", retrieve)
    first = asyncio.run(search.aggregated_search(
        "question", provider="auto", include_answers=False,
    ))
    second = asyncio.run(search.aggregated_search(
        "question", provider="auto", include_answers=False,
    ))
    assert first.provider == "keenable" and not first.answers
    assert second.cached and not second.answers and calls == ["keenable"]

    mock_http(monkeypatch, lambda req: httpx.Response(200, json={
        "answer": "AI", "results": [ROW],
    }))
    third = asyncio.run(search.aggregated_search("question", provider="auto"))
    assert third.provider == "tavily" and third.answers and not third.cached


@pytest.mark.parametrize("reason", ["length", "content_filter", "error"])
def test_native_incomplete_stream_never_reported_as_complete(configure, monkeypatch, reason):
    mock_http(monkeypatch, lambda req: httpx.Response(
        200, text=sse({"choices": [{"delta": {"content": "Partial"},
                                    "finish_reason": reason}]}),
        headers={"Content-Type": "text/event-stream"},
    ))
    with pytest.raises(native.NativeError, match="invalid or unavailable"):
        asyncio.run(native.synthesize("question", "brave", "answer", allow_cost=True))


def test_separate_answer_rechecks_authorization_before_cost(configure, monkeypatch):
    monkeypatch.setenv("MCP_AI_SEARCH_ENABLED", "false")

    async def unexpected(*args, **kwargs):
        pytest.fail("Must not call paid synthesis after consent is revoked")

    monkeypatch.setattr(native, "synthesize", unexpected)
    with pytest.raises(native.NativeError, match="no longer authorized"):
        asyncio.run(ai_search.separate_answer("brave", "question", 5))



@pytest.mark.parametrize("provider", ["tavily", "brave"])
def test_queued_synthesis_rechecks_policy_after_slot(configure, monkeypatch, provider):
    def unexpected(req):
        pytest.fail("Revoked synthesis must not reach the upstream API")

    mock_http(monkeypatch, unexpected)

    async def verify():
        async with native.synthesis_slot(), native.synthesis_slot():
            if provider == "tavily":
                operation = ai_search.combined_search(
                    provider, SearchRequest(query="question"), 5, 5,
                )
            else:
                operation = ai_search.separate_answer(provider, "question", 5)
            task = asyncio.create_task(operation)
            await asyncio.sleep(0)
            assert not task.done()
            monkeypatch.setenv("MCP_AI_SEARCH_ENABLED", "false")
        with pytest.raises(native.NativeError, match="no longer authorized"):
            await task

    asyncio.run(verify())



@pytest.fixture
def tinyfish_config(monkeypatch):
    monkeypatch.setenv("TINYFISH_API_KEY", "fixture-key")
    monkeypatch.setenv("MCP_AI_SEARCH_ENABLED", "true")
    monkeypatch.setenv("MCP_AI_SEARCH_PROVIDERS", "tinyfish")


def test_tinyfish_answer_contract_and_consent(tinyfish_config, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.answer.tinyfish.ai/"
        assert request.headers["X-API-Key"] == "fixture-key"
        assert json.loads(request.content) == {"question": "question"}
        return httpx.Response(200, json={
            "answer": "Answer [1]", "citations": [{"url": ROW["url"], "title": None}],
            "request_id": "fixture",
        })

    mock_http(monkeypatch, handler)
    with pytest.raises(native.NativeError, match="allow_cost"):
        asyncio.run(native.synthesize("question", "tinyfish", "answer"))
    with pytest.raises(native.NativeError, match="2000"):
        asyncio.run(native.synthesize("x" * 2001, "tinyfish", "answer", allow_cost=True))
    assert not calls
    result = asyncio.run(native.synthesize(" question ", "tinyfish", "answer", allow_cost=True))
    assert result.answer == "Answer [1]"
    assert result.sources[0].index == 1 and result.sources[0].url == ROW["url"]
    assert len(calls) == 1


@pytest.mark.parametrize("effort", ["deep", "max"])
def test_tinyfish_research_authoritative_final_only(tinyfish_config, monkeypatch, effort):
    calls = []

    def handler(req):
        calls.append(req)
        assert str(req.url) == "https://agent.tinyfish.ai/v1/automation/run-research"
        assert req.headers["X-API-Key"] == "fixture-key"
        assert json.loads(req.content) == {"query": "question", "mode": effort, "stream": False}
        return httpx.Response(200, text=sse(
            {"event": "created", "research_run_id": "fixture-id"},
            {"event": "partial_summary", "result": "Do not include"},
            {"event": "synthesis_delta", "text": "Draft"},
            {"event": "final_result", "result": "Report [1]",
             "citations": [ROW["url"], "javascript:alert(1)", ROW],
             "termination_reason": "planner_done"},
            {"event": "done"},
        ), headers={"Content-Type": "text/event-stream",
                    "X-Research-Run-Id": "fixture-id"})

    mock_http(monkeypatch, handler)
    result = asyncio.run(native.synthesize(
        "question", "tinyfish", "research", allow_cost=True, effort=effort,
    ))
    assert result.answer == "Report [1]" and result.effort == effort
    assert [source.index for source in result.sources] == [1, 2, 3]
    assert [source.url for source in result.sources] == [ROW["url"], None, ROW["url"]]
    assert len(calls) == 1


@pytest.mark.parametrize("status", [402, 403, 429])
def test_tinyfish_http_error_never_retries(tinyfish_config, monkeypatch, status):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"error": {"message": "private detail"}})

    mock_http(monkeypatch, handler)
    with pytest.raises(native.NativeError, match=f"HTTP {status}") as error:
        asyncio.run(native.synthesize("question", "tinyfish", "answer", allow_cost=True))
    assert "private detail" not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize("final", [False, True])
def test_tinyfish_unfinished_run_cancel_only_when_needed(tinyfish_config, monkeypatch, final):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if req.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"status": "CANCELLED", "cancelled": True})
        event = (
            {"event": "final_result", "result": "Interim", "citations": [],
             "termination_reason": "synthesis_failed"}
            if final else {"event": "error", "message": "private detail"}
        )
        return httpx.Response(200, text=sse(event, {"event": "done"}),
                              headers={"X-Research-Run-Id": "fixture-id"})

    mock_http(monkeypatch, handler)
    with pytest.raises(native.NativeError) as error:
        asyncio.run(native.synthesize(
            "question", "tinyfish", "research", allow_cost=True, effort="deep",
        ))
    assert "private detail" not in str(error.value)
    assert len(calls) == (1 if final else 2)
    if not final:
        assert calls[-1].endswith("/v1/research-run/fixture-id/cancel")


def test_tinyfish_explicit_only_and_disabled_provider(tinyfish_config, monkeypatch):
    assert not ai_search.eligible(["tinyfish"], SearchRequest(query="question"), True)
    cap = next(c for c in native.capabilities() if c["id"] == "tinyfish")
    assert cap["available"] and not cap["automatic_search"]
    monkeypatch.setenv("MCP_DISABLED_PROVIDERS", "tinyfish")

    def unexpected(req):
        pytest.fail("Disabled provider must not be contacted")

    mock_http(monkeypatch, unexpected)
    with pytest.raises(native.NativeError, match="enabled and configured"):
        asyncio.run(native.synthesize("question", "tinyfish", "answer", allow_cost=True))


def test_youcom_exhaustive_contract(configure, monkeypatch):
    def handler(req):
        assert str(req.url) == "https://api.you.com/v1/research"
        assert json.loads(req.content) == {"input": "question", "research_effort": "exhaustive"}
        return httpx.Response(200, json={"output": {"content": "Report", "sources": [ROW]}})

    mock_http(monkeypatch, handler)
    result = asyncio.run(native.synthesize(
        "question", "youcom", "research", allow_cost=True, effort="exhaustive",
    ))
    assert result.effort == "exhaustive" and result.answer == "Report"



@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_tinyfish_mcp_contract_and_cost_gate(tinyfish_config, monkeypatch, mode):
    from mcp import Client

    from mcp_prism_search.server import mcp

    calls = []

    def handler(req):
        calls.append(req)
        if req.url.host == "api.answer.tinyfish.ai":
            return httpx.Response(200, json={"answer": "Answer", "citations": []})
        return httpx.Response(200, text=sse({
            "event": "final_result", "result": "Report", "citations": [ROW["url"]],
            "termination_reason": "planner_done",
        }))

    mock_http(monkeypatch, handler)

    async def verify():
        async with Client(mcp, mode=mode) as client:
            for tool, extra in [("web_answer", {}), ("web_research", {"effort": "max"})]:
                args = {"query": "test", "provider": "tinyfish", **extra}
                denied = await client.call_tool(tool, args)
                assert denied.is_error
                accepted = await client.call_tool(tool, dict(args, allow_cost=True))
                assert not accepted.is_error
                assert accepted.structured_content["provider"] == "tinyfish"
        assert len(calls) == 2

    asyncio.run(verify())


def test_tinyfish_explicit_only_contract_is_enforced_before_network(tinyfish_config, monkeypatch):
    def unexpected(req):
        pytest.fail("TinyFish automatic synthesis must never contact the network")

    mock_http(monkeypatch, unexpected)
    with pytest.raises(native.NativeError, match="explicit standalone"):
        asyncio.run(native.synthesize(
            "question", "tinyfish", "answer", allow_cost=True, require_search_policy=True,
        ))
    cap = next(c for c in native.capabilities() if c["id"] == "tinyfish")
    assert cap["docs"] == "https://docs.tinyfish.ai/tinyanswer"


def test_sse_reader_accepts_multiline_events_and_bounds_buffers(monkeypatch):
    async def collect(content):
        response = httpx.Response(200, content=content)
        return [item async for item in native._sse_data(response)]

    assert asyncio.run(collect(b': comment\r\ndata: {\r\ndata: "ok": true}\r\n\r\n')) == [
        '{\n"ok": true}'
    ]
    assert asyncio.run(collect(b"data: incomplete")) == []
    monkeypatch.setattr(native, "SSE_MAX_EVENT_CHARS", 32)
    for content in (b"data: " + b"x" * 33, b"data: " + b"x" * 20 +
                    b"\ndata: " + b"x" * 20 + b"\n\n"):
        with pytest.raises(ValueError, match="exceeds limit"):
            asyncio.run(collect(content))


def test_combined_ai_cancellation_is_not_upstream_failure(configure, monkeypatch):
    from mcp_prism_search import runtime
    from mcp_prism_search.models import SearchRequest

    metrics = []
    monkeypatch.setattr(runtime, "record_metric", lambda *args: metrics.append(args))

    async def handler(req):
        raise asyncio.CancelledError

    mock_http(monkeypatch, handler)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ai_search.combined_search("tavily", SearchRequest(query="test"), 5, 5))
    assert len(metrics) == 1 and metrics[0][:2] == ("tavily:answer", "cancelled")


def test_sse_reader_ignores_initial_utf8_bom():
    async def collect():
        response = httpx.Response(200, content=bytes.fromhex("efbbbf") + b"data: ok\n\n")
        return [item async for item in native._sse_data(response)]

    assert asyncio.run(collect()) == ["ok"]


@pytest.mark.parametrize("url", ["https://example.org/\x00a", "https://example.org/\\a"])
def test_native_source_preserves_index_but_rejects_ambiguous_url(url):
    source = native._source(7, {"url": url, "title": "Evidence"})
    assert source.index == 7 and source.url is None and source.title == "Evidence"


def test_native_json_depth_failure_is_sanitized(configure, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(
        200, content=b"[" * 2000 + b"]" * 2000))
    with pytest.raises(native.NativeError, match="invalid or unavailable"):
        asyncio.run(native.synthesize("question", "exa", "answer", allow_cost=True))


def test_combined_timeout_never_retries_same_paid_provider(configure, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("response lost after upstream execution")

    mock_http(monkeypatch, handler)
    result = asyncio.run(search.aggregated_search("question", provider="tavily"))
    assert result.error and not result.results
    assert result.answer_attempts == ["tavily: timeout"]
    assert len(calls) == 1


def test_native_sse_ignores_empty_data_events(configure, monkeypatch):
    stream = "data:\n\n" + sse({"choices": [{"delta": {"content": "Answer"}}]})
    mock_http(monkeypatch, lambda req: httpx.Response(
        200, text=stream, headers={"content-type": "text/event-stream"}))
    result = asyncio.run(native.synthesize("question", "brave", "answer", allow_cost=True))
    assert result.answer == "Answer"


def test_combined_invalid_rows_do_not_consume_requested_result_window(configure, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={
        "answer": "AI", "results": [None, {"title": "Bad", "url": "javascript:bad"}, ROW],
    }))
    result = asyncio.run(search.aggregated_search(
        "question", provider="tavily", max_results=1))
    assert len(result.results) == 1 and result.results[0].url == ROW["url"]


def test_sse_reader_fragmented_utf8():
    class Fragmented(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b":" + b"x" * 4083 + b"\ndata: caf\xc3"
            yield b"\xa9\r\n\r\ndata: next\r\n\r\n"

    async def collect():
        response = httpx.Response(200, stream=Fragmented())
        return [item async for item in native._sse_data(response)]

    assert asyncio.run(collect()) == ["café", "next"]


def test_sse_reject_compressed():
    class Unexpected(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("compressed stream must not be consumed")
            yield b""

    async def collect():
        response = httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=Unexpected(),
        )
        return [item async for item in native._sse_data(response)]

    with pytest.raises(ValueError, match="encoding"):
        asyncio.run(collect())


def test_native_slot_saturation():
    async def exercise():
        async with native.synthesis_slot(), native.synthesis_slot():
            with pytest.raises(native.NativeError, match="Too many"):
                async with native.synthesis_slot():
                    pytest.fail("unexpected third slot")
        async with native.synthesis_slot():
            pass

    asyncio.run(exercise())


@pytest.mark.parametrize("job", ["../other", "a/b", "", None])
def test_tavily_invalid_task_identifier(configure, monkeypatch, job):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(201, json={"request_id": job})

    mock_http(monkeypatch, handler)
    with pytest.raises(native.NativeError):
        asyncio.run(native.synthesize("question", "tavily", "research", allow_cost=True))
    assert calls == ["POST"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "unknown"])
def test_tavily_terminal_failure(configure, monkeypatch, status):
    calls = []
    monkeypatch.setattr(native, "TAVILY_POLL_SECONDS", 0)

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(201, json={"request_id": "job-id"})
        return httpx.Response(200, json={"status": status})

    mock_http(monkeypatch, handler)
    with pytest.raises(native.NativeError):
        asyncio.run(native.synthesize("question", "tavily", "research", allow_cost=True))
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("reason", ["deadline", "max_iterations", "abort", "planner_no_queries"])
def test_tinyfish_partial_report(tinyfish_config, monkeypatch, reason):
    mock_http(monkeypatch, lambda req: httpx.Response(200, text=sse({
        "event": "final_result", "result": "Report", "citations": ["https://example.org"],
        "termination_reason": reason,
    })))
    result = asyncio.run(native.synthesize(
        "question", "tinyfish", "research", allow_cost=True, effort="deep",
    ))
    assert "budget or planning limit" in result.answer
    assert result.answer.endswith("Report")
    assert result.sources[0].url == "https://example.org"


def test_tinyfish_created_run_cancellation(tinyfish_config, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"cancelled": True})
        return httpx.Response(200, text=sse(
            {"event": "created", "research_run_id": "fixture-run"},
            {"event": "done"},
        ))

    mock_http(monkeypatch, handler)
    with pytest.raises(native.NativeError):
        asyncio.run(native.synthesize(
            "question", "tinyfish", "research", allow_cost=True, effort="deep",
        ))
    assert len(calls) == 2
    assert calls[1] == "/v1/research-run/fixture-run/cancel"


def test_tinyfish_unsafe_run_id(tinyfish_config, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, text=sse(
            {"event": "created", "research_run_id": "../other"},
            {"event": "error"},
        ))

    mock_http(monkeypatch, handler)
    with pytest.raises(native.NativeError):
        asyncio.run(native.synthesize(
            "question", "tinyfish", "research", allow_cost=True, effort="deep",
        ))
    assert len(calls) == 1
