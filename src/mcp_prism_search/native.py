"""Explicit provider-native synthesis, separate from ordinary retrieval."""
from __future__ import annotations

import asyncio
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from mcp_prism_search import providers, runtime

Mode = Literal["answer", "research"]
AnswerProvider = Literal["tavily", "exa", "youcom"]
ResearchProvider = Literal["exa", "youcom"]
Effort = Literal["lite", "standard", "deep"]

CAPABILITIES = {
    "tavily": {
        "answer": True, "research": False,
        "pricing": "Basic search: 1 credit; 1,000 free credits/month.",
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
        "answer": True, "research": True,
        "pricing": "Answer $0.005; research lite $0.012, standard $0.05, deep $0.10.",
        "research_docs": "https://you.com/docs/guides/research",
        "efforts": {
            "lite": "Lite — $0.012/call",
            "standard": "Standard — $0.05/call",
            "deep": "Deep — $0.10/call",
        },
        "docs": "https://you.com/docs/choose-the-right-api",
        "pricing_url": "https://you.com/pricing",
    },
}


class Source(BaseModel):
    index: int
    url: str | None
    title: str
    excerpt: str = ""


class NativeResponse(BaseModel):
    provider: str
    mode: Mode
    answer: str
    sources: list[Source]
    effort: Effort | None = None
    truncated: bool = False
    source_kind: Literal["citations", "grounding", "search_results"] = "citations"


class NativeError(ValueError):
    """Sanitized error safe for clients."""


# Limit paid operations independently from general web search; no retries/fanout.
_slots: asyncio.Semaphore | None = None
_loop: asyncio.AbstractEventLoop | None = None


def capabilities():
    available = providers.available_providers("general")
    return [
        {"id": name, "available": name in available, **spec}
        for name, spec in CAPABILITIES.items()
    ]


def _source(index: int, row: object, url_key: str = "url") -> Source:
    # Preserve upstream citation indices, including invalid URLs. Never shift numbers.
    item = row if isinstance(row, dict) else {}
    url = item.get(url_key)
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (not parsed or parsed.scheme not in {"http", "https"}
                or not parsed.hostname or parsed.username or parsed.password
                or any(c.isspace() for c in url) or len(url) > 4096):
            url = None
    except ValueError:
        url = None
    excerpt = item.get("content") or item.get("text") or item.get("description")
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
            rows.extend(field.get("citations") or [])
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


async def synthesize(query: str, provider: str, mode: Mode, *,
                     allow_cost: bool = False, effort: Effort = "lite") -> NativeResponse:
    global _slots, _loop
    query = query.strip()
    if not query or len(query) > 4000:
        raise NativeError("Enter a question of 1–4000 characters.")
    if (mode not in {"answer", "research"} or provider not in CAPABILITIES
            or not CAPABILITIES[provider][mode]):
        raise NativeError("Provider does not support this synthesis mode.")
    if not allow_cost:
        raise NativeError("Explicit allow_cost=true is required; provider credits may be consumed.")
    spec = providers.available_providers("general").get(provider)
    if not spec or not spec.needs:
        raise NativeError("Provider must be enabled and configured.")
    credential = runtime.setting(spec.needs)
    if not credential:
        raise NativeError("Provider must be configured.")
    if effort not in {"lite", "standard", "deep"} or (
        mode == "research" and effort not in CAPABILITIES[provider].get("efforts", {})
    ):
        raise NativeError("Unsupported research effort for this provider.")
    if provider == "tavily":
        url = "https://api.tavily.com/search"
        headers = {"Authorization": "Bearer " + credential}
        payload = {"query": query, "search_depth": "basic", "include_answer": "basic",
                   "max_results": 5, "auto_parameters": False}
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
    timeout = 45 if mode == "answer" else {"lite": 30, "standard": 75, "deep": 150}[effort]
    loop = asyncio.get_running_loop()
    if _loop is not loop:
        _loop, _slots = loop, asyncio.Semaphore(2)
    assert _slots is not None
    if _slots.locked():
        raise NativeError("Too many synthesis requests; try again later.")
    start = monotonic()
    outcome = "error"
    async with _slots:
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    result = _decode(provider, mode, response.json(), effort)
            outcome = "ok"
            return result
        except httpx.HTTPStatusError as exc:
            outcome = f"HTTP {exc.response.status_code}"
            raise NativeError(
                f"{provider}: {outcome}; check provider quota or credentials."
            ) from None
        except (TimeoutError, httpx.TimeoutException):
            outcome = "timeout"
            raise NativeError(f"{provider}: timed out; check usage before retrying.") from None
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            raise NativeError(f"{provider}: invalid or unavailable native response.") from None
        finally:
            # Separate aggregate metric from ordinary retrieval; no query/answer storage.
            runtime.record_metric(provider + ":" + mode, outcome, monotonic() - start)
