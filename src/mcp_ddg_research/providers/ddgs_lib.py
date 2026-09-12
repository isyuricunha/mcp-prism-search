"""ddgs library provider: one factory per backend (bing, yahoo, brave, yandex...)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from mcp_ddg_research.models import SearchRequest, SearchResult

DDGS_SAFE_SEARCH = {"off": "off", "moderate": "moderate", "strict": "on"}
TIME_FILTERS = {"day": "d", "week": "w", "month": "m", "year": "y"}


def provider_request_size(request: SearchRequest) -> int:
    if request.search_window is not None:
        return request.search_window
    return request.max_results


def _ddgs_text_kwargs(
    request: SearchRequest,
    backend: str,
    *,
    include_backend: bool,
) -> dict[str, Any]:
    timelimit = TIME_FILTERS.get(request.time_filter) if request.time_filter else None
    kwargs: dict[str, Any] = {
        "max_results": provider_request_size(request),
        "timelimit": timelimit,
        "safesearch": DDGS_SAFE_SEARCH[request.safe_search],
    }
    if include_backend:
        kwargs["backend"] = backend
    return kwargs


def _result_from_payload(payload: dict[str, Any]) -> SearchResult | None:
    from mcp_ddg_research.text import normalize_whitespace

    title = normalize_whitespace(str(payload.get("title") or ""))
    url = str(payload.get("href") or payload.get("url") or "")
    snippet = normalize_whitespace(str(payload.get("body") or payload.get("snippet") or ""))
    if not title or not url:
        return None
    return SearchResult(title=title, url=url, snippet=snippet)


def make_search(backend: str):
    """Return a provider fn pinned to one ddgs backend."""

    async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
        from ddgs import DDGS

        def run() -> list[SearchResult]:
            with DDGS(timeout=timeout) as ddgs:
                text_signature = inspect.signature(ddgs.text)
                include_backend = "backend" in text_signature.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in text_signature.parameters.values()
                )
                kwargs = _ddgs_text_kwargs(request, backend, include_backend=include_backend)
                raw_results = ddgs.text(request.query, **kwargs)
                out = []
                for raw in raw_results or []:
                    result = _result_from_payload(raw)
                    if result is not None:
                        out.append(result)
                seen: set[str] = set()
                deduped = []
                for item in out:
                    key = item.url.rstrip("/").lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(item)
                    if len(deduped) >= count:
                        break
                return deduped

        return await asyncio.to_thread(run)

    return search