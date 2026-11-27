"""ddgs library provider: one factory per backend (bing, yahoo, brave, yandex...)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from mcp_prism_search.models import SearchRequest, SearchResult

DDGS_SAFE_SEARCH = {"off": "off", "moderate": "moderate", "strict": "on"}
TIME_FILTERS = {"day": "d", "week": "w", "month": "m", "year": "y"}


def _ddgs_text_kwargs(
    request: SearchRequest,
    backend: str,
    *,
    include_backend: bool,
    count: int,
) -> dict[str, Any]:
    timelimit = TIME_FILTERS.get(request.time_filter) if request.time_filter else None
    kwargs: dict[str, Any] = {
        "max_results": count,
        "timelimit": timelimit,
        "safesearch": DDGS_SAFE_SEARCH[request.safe_search],
    }
    if include_backend:
        kwargs["backend"] = backend
    return kwargs


def _result_from_payload(payload: dict[str, Any]) -> SearchResult | None:
    from mcp_prism_search.text import normalize_whitespace

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
        from ddgs.ddgs import ENGINES

        if backend not in ENGINES.get("text", {}):
            raise ValueError(f"Unsupported DDGS backend: {backend}")

        def run() -> list[SearchResult]:
            with DDGS(timeout=timeout) as ddgs:
                text_signature = inspect.signature(ddgs.text)
                include_backend = "backend" in text_signature.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in text_signature.parameters.values()
                )
                kwargs = _ddgs_text_kwargs(
                    request, backend, include_backend=include_backend, count=count
                )
                raw_results = ddgs.text(request.query, **kwargs)
                out = []
                for raw in raw_results or []:
                    if not isinstance(raw, dict):
                        continue
                    result = _result_from_payload(raw)
                    if result is not None:
                        out.append(result)
                from mcp_prism_search.search import _dedupe_results

                return _dedupe_results(out, count)

        return await asyncio.to_thread(run)

    return search