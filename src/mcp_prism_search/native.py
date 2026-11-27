"""Explicit provider-native synthesis, separate from ordinary retrieval."""
from __future__ import annotations

import asyncio
import codecs
import json
import re
from contextlib import asynccontextmanager
from time import monotonic
from typing import Literal

import httpx

from mcp_prism_search import providers, runtime
from mcp_prism_search.native_models import Effort, Mode, NativeResponse, Source
from mcp_prism_search.upstream import bounded_request

AnswerProvider = Literal["tavily", "exa", "youcom", "brave", "tinyfish"]
ResearchProvider = Literal["exa", "youcom", "tavily", "brave", "jina", "tinyfish"]

CAPABILITIES = {
    "tinyfish": {
        "answer": True, "research": True, "automatic_search": False,
        "pricing": (
            "TinyAnswer: account-dependent access and billing; no confirmed free quota. "
            "TinyResearch: Deep $2/run, Max $3/run. Personal API key required for Answer."
        ),
        "answer_max_chars": 2000,
        "answer_name": "TinyFish · TinyAnswer",
        "research_name": "TinyFish · TinyResearch",
        "research_docs": "https://docs.tinyfish.ai/tinyresearch/reference",
        "efforts": {"deep": "Deep — $2/run (5–20 min)", "max": "Max — $3/run (5–45 min)"},
        "research_wait_note": (
            "Research can take up to 45 minutes. Keep this request open; "
            "do not retry a disconnected request without checking the provider run."
        ),
        "docs": "https://docs.tinyfish.ai/tinyanswer",
        "pricing_url": "https://www.tinyfish.ai/pricing",
    },
    "tavily": {
        "answer": True, "research": True,
        "pricing": "Basic search: 1 credit; 1,000 free credits/month. Research: 4–250 credits.",
        "research_docs": "https://docs.tavily.com/documentation/api-reference/endpoint/research",
        "efforts": {"lite": "Mini — 4–110 credits", "deep": "Pro — 15–250 credits"},
        "docs": "https://docs.tavily.com/documentation/api-reference/endpoint/search",
        "pricing_url": "https://docs.tavily.com/documentation/api-credits",
    },
    "exa": {
        "answer": True, "research": True,
        "pricing": "Answer: $0.005; Deep Search: $0.012/call. Starter: $10/month credits.",
        "research_docs": "https://exa.ai/docs/search/deep-search",
        "efforts": {"lite": "Deep Lite — $0.012/call", "deep": "Deep — $0.012/call"},
        "docs": "https://exa.ai/docs/reference/answer",
        "pricing_url": "https://exa.ai/pricing",
    },
    "youcom": {
        "answer": True, "research": True, "answer_max_chars": 400,
        "answer_name": "You.com Answer",
        "pricing": (
            "Answer $0.005; research lite $0.012, standard $0.05, "
            "deep $0.10, exhaustive $0.45."
        ),
        "research_docs": "https://you.com/docs/guides/research",
        "efforts": {
            "lite": "Lite — $0.012/call",
            "standard": "Standard — $0.05/call",
            "deep": "Deep — $0.10/call",
            "exhaustive": "Exhaustive — $0.45/call (up to 5 min)",
        },
        "docs": "https://you.com/docs/choose-the-right-api",
        "pricing_url": "https://you.com/pricing",
    },
    "brave": {
        "answer": True, "research": True,
        "pricing": "Answers: $4/1,000 searches plus $5/million input and output tokens.",
        "docs": "https://api-dashboard.search.brave.com/documentation/services/answers",
        "pricing_url": "https://api-dashboard.search.brave.com/documentation/services/answers",
        "efforts": {"deep": "Research mode — variable searches and tokens"},
    },
    "jina": {
        "answer": False, "research": True,
        "pricing": "DeepSearch uses variable tokens; a token budget is not a guaranteed spend cap.",
        "docs": "https://jina.ai/deepsearch",
        "pricing_url": "https://jina.ai/deepsearch",
        "efforts": {"lite": "20k token budget", "deep": "100k token budget"},
    },
}


class NativeError(ValueError):
    """Sanitized error safe for clients."""

    def __init__(self, message: str, *, outcome: str = "error") -> None:
        super().__init__(message)
        self.outcome = outcome


# Limit paid operations independently from general web search; no retries/fanout.
_slots: asyncio.Semaphore | None = None
_loop: asyncio.AbstractEventLoop | None = None


def capabilities():
    available = providers.available_providers("general")
    return [
        {"id": name, "available": name in available, "automatic_search": True, **spec}
        for name, spec in CAPABILITIES.items()
    ]


def _source(index: int, row: object, url_key: str = "url") -> Source:
    # Preserve upstream citation indices, including invalid URLs. Never shift numbers.
    item = row if isinstance(row, dict) else {}
    url = item.get(url_key)
    from mcp_prism_search.search import valid_result_url

    if not isinstance(url, str) or len(url) > 4096 or not valid_result_url(url):
        url = None
    excerpt = (item.get("content") or item.get("text") or item.get("description")
               or item.get("exactQuote") or item.get("snippet"))
    if not excerpt:
        snippets = item.get("excerpts") or item.get("snippets") or []
        excerpt = (
            " ".join(v for v in snippets if isinstance(v, str))
            if isinstance(snippets, list) else ""
        )
    return Source(index=index, url=url, title=str(item.get("title") or url or "Source")[:500],
                  excerpt=str(excerpt or "")[:1200])


def _decode(provider: str, mode: Mode, data: dict, effort: Effort) -> NativeResponse:
    source_kind = "citations"
    if mode == "research" and provider == "exa":
        output = data["output"]
        answer = output["content"]["answer"]
        grounding = output["grounding"]
        if not isinstance(grounding, list):
            raise ValueError("Invalid grounding")
        rows = []
        for field in grounding:
            if not isinstance(field, dict) or field.get("field") != "answer":
                continue
            citations = field.get("citations")
            if not isinstance(citations, list):
                raise ValueError("Invalid grounding citations")
            rows.extend(citations)
        source_kind = "grounding"
    elif mode == "research":
        output = data["output"]
        answer, rows = output["content"], output["sources"]
        if output.get("content_type", "text") != "text":
            raise ValueError("Expected text research output")
    else:
        answer = data["answer"]
        rows = data["results"] if provider == "tavily" else data["citations"]
        if provider == "tavily":
            source_kind = "search_results"
    if not isinstance(answer, str) or not answer.strip() or not isinstance(rows, list):
        raise ValueError("Invalid native response")
    # Bound context; preserve original order and citation numbers.
    limit = 24000
    truncated = len(answer) > limit or len(rows) > 100
    sources = [
        _source(i, row, "source" if provider == "youcom" and mode == "answer" else "url")
        for i, row in enumerate(rows[:100], 1)
    ]
    return NativeResponse(provider=provider, mode=mode, answer=answer[:limit],
                          sources=sources, effort=effort if mode == "research" else None,
                          truncated=truncated, source_kind=source_kind)


@asynccontextmanager
async def synthesis_slot(*, wait: bool = False):
    global _slots, _loop
    loop = asyncio.get_running_loop()
    if _loop is not loop:
        _loop, _slots = loop, asyncio.Semaphore(2)
    assert _slots is not None
    if _slots.locked() and not wait:
        raise NativeError("Too many synthesis requests; try again later.")
    async with _slots:
        yield


SSE_MAX_EVENT_CHARS = 1_000_000
SSE_MAX_STREAM_BYTES = 32 * 1024 * 1024


async def _sse_data(response):
    """Bound line and event buffering, and honor SSE data-field framing."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    pending = ""
    fields = []
    size = 0
    trailing_cr = False
    total_bytes = 0
    if response.headers.get("content-encoding", "identity").strip().lower() not in {
        "", "identity",
    }:
        raise ValueError("Unsupported stream content encoding")
    async for chunk in response.aiter_bytes(chunk_size=4096):
        total_bytes += len(chunk)
        if total_bytes > SSE_MAX_STREAM_BYTES:
            raise ValueError("SSE stream exceeds size limit")
        text = decoder.decode(chunk)
        if not text:
            continue
        if trailing_cr and text.startswith("\n"):
            text = text[1:]
        trailing_cr = text.endswith("\r")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines[0] = pending + lines[0]
        pending = lines.pop()
        if len(pending) > SSE_MAX_EVENT_CHARS:
            raise ValueError("SSE line exceeds limit")
        for line in lines:
            if len(line) > SSE_MAX_EVENT_CHARS:
                raise ValueError("SSE line exceeds limit")
            if not line:
                if fields:
                    yield "\n".join(fields)
                fields, size = [], 0
                continue
            field, _, value = line.partition(":")
            if field == "data":
                value = value.removeprefix(" ")
                size += len(value) + 1
                if size > SSE_MAX_EVENT_CHARS:
                    raise ValueError("SSE event exceeds limit")
                fields.append(value)
    decoder.decode(b"", final=True)


async def _chat_stream(client, url, headers, payload, provider):
    """Read documented SSE final text and citations, ignoring reasoning and usage tags."""
    chunks = []
    rows = []
    numbers = []
    size = 0
    finished = False
    async with client.stream(
        "POST", url, headers={**headers, "Accept-Encoding": "identity"}, json=payload
    ) as response:
        response.raise_for_status()
        async for data in _sse_data(response):
            value = data.strip()
            if not value:
                continue
            if value == "[DONE]":
                finished = True
                break
            event = json.loads(value)
            if event.get("error"):
                raise ValueError("Upstream stream failed")
            for choice in event.get("choices", []):
                reason = choice.get("finish_reason")
                if reason == "stop":
                    finished = True
                elif reason is not None:
                    # A terminal length/error/tool-call event is not a complete
                    # grounded response, even when followed by [DONE].
                    raise ValueError("Upstream stream did not complete normally")
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if (isinstance(text, str)
                        and (provider != "jina" or delta.get("type") in {None, "text"})):
                    size += len(text)
                    if size > 500000:
                        raise ValueError("Stream exceeds limit")
                    chunks.append(text)
                if provider == "jina":
                    for annotation in delta.get("annotations") or []:
                        if annotation.get("type") == "url_citation":
                            row = annotation["url_citation"]
                            if row not in rows and len(rows) < 100:
                                rows.append(row)
    if not finished:
        raise ValueError("Incomplete stream")
    answer = "".join(chunks)
    if provider == "brave":
        def citation(match):
            try:
                item = json.loads(match.group(1))
                number = item["number"]
            except (ValueError, KeyError, TypeError):
                return ""
            if type(number) is not int or not 1 <= number <= 100:
                return ""
            if number not in numbers:
                numbers.append(number)
                rows.append(item)
            return f"[{number}]"

        answer = re.sub(r"<citation>(.*?)</citation>", citation, answer, flags=re.S)
        answer = re.sub(r"<usage>.*?</usage>", "", answer, flags=re.S)
    else:
        numbers = list(range(1, len(rows) + 1))
    if not answer.strip():
        raise ValueError("Empty answer")
    return answer, [_source(i, row) for i, row in sorted(zip(numbers, rows, strict=True))]


TINY_RESEARCH_TIMEOUT = 2760
TINY_RESEARCH_BASE = "https://agent.tinyfish.ai"


async def _tiny_research(client, headers, query: str, effort: Effort) -> NativeResponse:
    """One upstream run. Consume only the authoritative final_result, not drafts."""
    run_id = None
    completed = False
    try:
        async with client.stream(
            "POST", TINY_RESEARCH_BASE + "/v1/automation/run-research",
            headers={**headers, "Accept-Encoding": "identity"},
            json={"query": query, "mode": effort, "stream": False},
        ) as response:
            response.raise_for_status()
            candidate = response.headers.get("X-Research-Run-Id", "")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate):
                run_id = candidate
            async for data in _sse_data(response):
                if not data.strip():
                    continue
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise ValueError("Invalid research event")
                kind = event.get("event")
                if kind == "created" and run_id is None:
                    candidate = event.get("research_run_id")
                    if isinstance(candidate, str) and re.fullmatch(
                        r"[A-Za-z0-9_-]{1,128}", candidate
                    ):
                        run_id = candidate
                elif kind == "error":
                    raise ValueError("Research failed")
                elif kind == "final_result":
                    # The provider may bill a completed run even if its final
                    # report cannot be parsed locally. Never resubmit it.
                    completed = True
                    answer, rows = event.get("result"), event.get("citations")
                    reason = event.get("termination_reason")
                    if reason == "synthesis_failed":
                        raise NativeError("tinyfish: final synthesis failed; check saved run.")
                    if (not isinstance(answer, str) or not answer.strip()
                            or not isinstance(rows, list)):
                        raise ValueError("Invalid research report")
                    if reason in {"deadline", "max_iterations", "abort", "planner_no_queries"}:
                        answer = (
                            "[Research ended at a provider budget or planning limit.] " + answer
                        )
                    return NativeResponse(
                        provider="tinyfish", mode="research", effort=effort,
                        answer=answer[:24000], source_kind="citations",
                        sources=[
                            _source(i, {"url": row} if isinstance(row, str) else row)
                            for i, row in enumerate(rows[:100], 1)
                        ],
                        truncated=len(answer) > 24000 or len(rows) > 100,
                    )
                elif kind == "done":
                    break
        raise ValueError("No final research report")
    finally:
        # A disconnected stream does not cancel a TinyResearch job by itself.
        # Best-effort cancellation of known unfinished runs, no new creation.
        if run_id and not completed:
            try:
                async with asyncio.timeout(10):
                    await bounded_request(
                        client, "POST",
                        TINY_RESEARCH_BASE + "/v1/research-run/" + run_id + "/cancel",
                        headers=headers, timeout=10,
                    )
            except (httpx.HTTPError, TimeoutError, ValueError):
                pass


TAVILY_POLL_SECONDS = 2


async def _tavily_research(client, headers, payload):
    # One creation request. Poll the existing job, never resubmit it.
    response = await bounded_request(
        client, "POST", "https://api.tavily.com/research", headers=headers, json=payload
    )
    job = response.json()["request_id"]
    if not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job):
        raise ValueError("Invalid task ID")
    while True:
        await asyncio.sleep(TAVILY_POLL_SECONDS)
        response = await bounded_request(
            client, "GET", "https://api.tavily.com/research/" + job, headers=headers
        )
        data = response.json()
        if data.get("status") == "completed":
            return data
        if data.get("status") not in {"pending", "in_progress"}:
            raise ValueError("Research failed")


async def synthesize(query: str, provider: str | None, mode: Mode, *,
                     allow_cost: bool = False, effort: Effort | None = None,
                     wait_for_slot: bool = False,
                     require_search_policy: bool = False) -> NativeResponse:
    from mcp_prism_search.defaults import effective_defaults

    defaults = effective_defaults()
    if provider is None:
        provider = defaults.answer_provider if mode == "answer" else defaults.research_provider
        if provider == "explicit":
            raise NativeError("Choose a provider or configure a default provider.")
    if effort is None:
        effort = defaults.research_effort if mode == "research" else "lite"
    query = query.strip()
    if not query or len(query) > 4000:
        raise NativeError("Enter a question of 1–4000 characters.")
    if (mode not in {"answer", "research"} or provider not in CAPABILITIES
            or not CAPABILITIES[provider][mode]):
        raise NativeError("Provider does not support this synthesis mode.")
    if allow_cost is not True:
        raise NativeError("Explicit allow_cost=true is required; provider credits may be consumed.")
    spec = providers.available_providers("general").get(provider)
    if not spec or not spec.needs:
        raise NativeError("Provider must be enabled and configured.")
    credential = runtime.setting(spec.needs)
    if not credential:
        raise NativeError("Provider must be configured.")
    if require_search_policy and CAPABILITIES[provider].get("automatic_search") is False:
        raise NativeError("Provider synthesis requires an explicit standalone request.")
    if effort not in {"lite", "standard", "deep", "max", "exhaustive"} or (
        mode == "research" and effort not in CAPABILITIES[provider].get("efforts", {})
    ):
        raise NativeError("Unsupported research effort for this provider.")
    answer_limit = CAPABILITIES[provider].get("answer_max_chars", 4000)
    if mode == "answer" and len(query) > answer_limit:
        label = CAPABILITIES[provider].get("answer_name", provider + " Answer")
        raise NativeError(f"{label} supports at most {answer_limit} characters.")
    if provider == "tinyfish":
        url = "https://api.answer.tinyfish.ai/"
        headers = {"X-API-Key": credential}
        payload = {"question": query}
    elif provider == "tavily":
        url = "https://api.tavily.com/search"
        headers = {"Authorization": "Bearer " + credential}
        payload = {"query": query, "search_depth": "basic", "include_answer": "basic",
                   "max_results": 5, "auto_parameters": False}
        if mode == "research":
            payload = {"input": query, "model": "mini" if effort == "lite" else "pro",
                       "stream": False}
    elif provider == "brave":
        url = "https://api.search.brave.com/res/v1/chat/completions"
        headers = {"Authorization": "Bearer " + credential}
        payload = {"model": "brave", "messages": [{"role": "user", "content": query}],
                   "stream": True, "enable_citations": True, "enable_research": mode == "research"}
    elif provider == "jina":
        url = "https://deepsearch.jina.ai/v1/chat/completions"
        headers = {"Authorization": "Bearer " + credential}
        payload = {"model": "jina-deepsearch-v1", "messages": [{"role": "user", "content": query}],
                   "stream": True, "no_direct_answer": True,
                   "budget_tokens": 20000 if effort == "lite" else 100000,
                   "max_attempts": 1 if effort == "lite" else 2, "team_size": 1}
    elif provider == "exa":
        headers = {"x-api-key": credential}
        if mode == "research":
            url = "https://api.exa.ai/search"
            payload = {
                "query": query,
                "type": "deep-lite" if effort == "lite" else "deep",
                "numResults": 10,
                "outputSchema": {
                    "type": "object",
                    "required": ["answer"],
                    "properties": {
                        "answer": {"type": "string", "description": "Grounded research synthesis"},
                    },
                },
            }
        else:
            url = "https://api.exa.ai/answer"
            payload = {"query": query}
    else:
        url = "https://api.you.com/v1/" + mode
        headers = {"X-API-Key": credential}
        payload = {"query": query} if mode == "answer" else {
            "input": query, "research_effort": effort,
        }
    if provider == "tinyfish":
        timeout = 70 if mode == "answer" else TINY_RESEARCH_TIMEOUT
    else:
        timeout = 45 if mode == "answer" else {
            "lite": 60, "standard": 90, "deep": 150, "exhaustive": 330,
        }[effort]
    start = monotonic()
    outcome = "error"
    async with synthesis_slot(wait=wait_for_slot):
        if (provider not in providers.available_providers("general")
                or runtime.setting(spec.needs) != credential):
            raise NativeError("Provider configuration changed before synthesis.")
        if require_search_policy:
            from mcp_prism_search.ai_search import policy

            enabled, selected = policy()
            if (not enabled or provider not in selected
                    or provider not in providers.available_providers("general")
                    or runtime.setting(spec.needs) != credential):
                raise NativeError("Provider AI is no longer authorized")
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    if provider == "tinyfish" and mode == "research":
                        result = await _tiny_research(client, headers, query, effort)
                    elif provider in {"brave", "jina"}:
                        answer, sources = await _chat_stream(
                            client, url, headers, payload, provider
                        )
                        result = NativeResponse(
                            provider=provider, mode=mode, answer=answer[:24000],
                            sources=sources, effort=effort if mode == "research" else None,
                            truncated=len(answer) > 24000,
                            source_kind="grounding" if provider == "jina" else "citations",
                        )
                    elif provider == "tavily" and mode == "research":
                        data = await _tavily_research(client, headers, payload)
                        answer, rows = data["content"], data["sources"]
                        if (not isinstance(answer, str) or not answer.strip()
                                or not isinstance(rows, list)):
                            raise ValueError("Invalid research response")
                        result = NativeResponse(
                            provider=provider, mode=mode, answer=answer[:24000],
                            sources=[_source(i, row) for i, row in enumerate(rows[:100], 1)],
                            effort=effort, truncated=len(answer) > 24000 or len(rows) > 100,
                            source_kind="grounding",
                        )
                    else:
                        response = await bounded_request(
                            client, "POST", url, headers=headers, json=payload
                        )
                        result = _decode(provider, mode, response.json(), effort)
            outcome = "ok"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except httpx.HTTPStatusError as exc:
            outcome = f"HTTP {exc.response.status_code}"
            raise NativeError(
                f"{provider}: {outcome}; check provider quota or credentials."
            ) from None
        except (TimeoutError, httpx.TimeoutException):
            outcome = "timeout"
            raise NativeError(f"{provider}: timed out; check usage before retrying.") from None
        except NativeError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise NativeError(f"{provider}: invalid or unavailable native response.") from None
        finally:
            # Separate aggregate metric from ordinary retrieval; no query/answer storage.
            runtime.record_metric(provider + ":" + mode, outcome, monotonic() - start)
