"""Administrator-authorized synthesis in regular search.

No implicit authorization from an MCP argument. Restricted searches keep
retrieval-only semantics because not all synthesis APIs honor those filters.
"""
from __future__ import annotations

import asyncio
from time import monotonic

import httpx

from mcp_prism_search import native, providers, runtime
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.native_models import NativeResponse
from mcp_prism_search.text import normalize_whitespace
from mcp_prism_search.upstream import bounded_request

ENABLED_ENV = "MCP_AI_SEARCH_ENABLED"
PROVIDERS_ENV = "MCP_AI_SEARCH_PROVIDERS"
COMBINED = frozenset({"tavily", "exa", "youcom"})
SEPARATE = frozenset({"brave", "jina"})
SUPPORTED = COMBINED | SEPARATE


def policy() -> tuple[bool, set[str]]:
    enabled = (runtime.setting(ENABLED_ENV) or "").lower() in {"1", "true", "yes", "on"}
    selected = set((runtime.setting(PROVIDERS_ENV) or "").split(","))
    return enabled, {name.strip() for name in selected} & SUPPORTED


def eligible(chain: list[str], request: SearchRequest, include_answers: bool | None) -> set[str]:
    enabled, selected = policy()
    if not enabled or include_answers is False:
        return set()
    # Do not silently weaken caller filters in an AI-generated answer.
    if (request.allowed_domains or request.blocked_domains or request.preferred_domains
            or request.time_filter or request.search_window is not None
            or request.safe_search != "off" or len(request.query) > 400):
        return set()
    return set(chain) & selected & providers.available_providers("general").keys()


async def combined_search(
    name: str, request: SearchRequest, count: int, timeout: int,
) -> tuple[list[SearchResult], NativeResponse | None]:
    """Use the provider's documented combined response, without a second synthesis call."""
    if name not in eligible([name], request, None):
        raise native.NativeError("Provider AI is no longer authorized")
    spec = providers.available_providers("general").get(name)
    credential = runtime.setting(spec.needs) if spec and spec.needs else None
    if not credential:
        raise native.NativeError("Provider is not configured")
    if name == "tavily":
        url = "https://api.tavily.com/search"
        headers = {"Authorization": "Bearer " + credential}
        payload = {"query": request.query, "search_depth": "basic", "include_answer": "basic",
                   "max_results": min(count, 20), "auto_parameters": False}
    elif name == "exa":
        url = "https://api.exa.ai/search"
        headers = {"x-api-key": credential}
        payload = {"query": request.query, "type": "auto", "numResults": min(count, 100),
                   "outputSchema": {"type": "object", "required": ["answer"],
                                    "properties": {"answer": {"type": "string"}}}}
    elif name == "youcom":
        url = "https://api.you.com/v1/answer"
        headers = {"X-API-Key": credential}
        payload = {"query": request.query, "safesearch": request.safe_search}
    else:
        raise native.NativeError("Unsupported combined provider")
    start = monotonic()
    outcome = "error"
    try:
        async with asyncio.timeout(timeout), native.synthesis_slot(wait=True):
            if (name not in eligible([name], request, None)
                    or runtime.setting(spec.needs) != credential):
                raise native.NativeError("Provider AI is no longer authorized")
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await bounded_request(
                    client, "POST", url, headers=headers, json=payload
                )
                data = response.json()
            rows = data["results"]["web"] if name == "youcom" else data["results"]
            if not isinstance(rows, list):
                raise ValueError("Invalid results")
            results = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                source = native._source(1, row)
                if source.url and row.get("title"):
                    results.append(SearchResult(
                        title=normalize_whitespace(source.title), url=source.url,
                        snippet=normalize_whitespace(
                            source.excerpt or str(
                                row.get("description") or row.get("summary") or ""
                            )
                        ),
                    ))
                    if len(results) >= count:
                        break
            # Valid retrieval must survive a missing or malformed synthesis.
            answer = None
            try:
                answer = native._decode(
                    name, "research" if name == "exa" else "answer", data, "lite"
                )
                if name == "exa":
                    answer = answer.model_copy(update={"mode": "answer", "effort": None})
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
            outcome = "ok" if answer else "empty"
            return results, answer
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except httpx.HTTPStatusError as exc:
        outcome = f"HTTP {exc.response.status_code}"
        raise native.NativeError(outcome) from None
    except (TimeoutError, httpx.TimeoutException):
        outcome = "timeout"
        raise native.NativeError(outcome) from None
    except native.NativeError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise native.NativeError("invalid or unavailable response") from None
    finally:
        runtime.record_metric(name + ":answer", outcome, monotonic() - start)


def compact_answer(answer: NativeResponse) -> NativeResponse:
    """Keep optional answers high-signal; preserve provider citation indices."""
    sources = [source.model_copy(update={"excerpt": source.excerpt[:500]})
               for source in answer.sources[:12]]
    truncated = (answer.truncated or len(answer.answer) > 6000 or len(answer.sources) > 12
                 or any(len(source.excerpt) > 500 for source in answer.sources[:12]))
    return answer.model_copy(update={
        "answer": answer.answer[:6000], "sources": sources, "truncated": truncated,
    })


async def separate_answer(name: str, query: str, timeout: int) -> NativeResponse:
    enabled, selected = policy()
    if not enabled or name not in selected:
        raise native.NativeError("Provider AI is no longer authorized")
    async with asyncio.timeout(timeout):
        return await native.synthesize(
            query, name, "research" if name == "jina" else "answer", allow_cost=True,
            effort="lite", wait_for_slot=True, require_search_policy=True,
        )
