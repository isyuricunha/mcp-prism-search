"""Mwmbl community-index search provider (keyless, AGPL)."""

from __future__ import annotations

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://mwmbl.org/api/v1/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"q": request.query, "s": count}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.get(SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for item in data:
        url = str(item.get("url") or "")
        if not url.startswith("http"):
            continue
        title_segments = item.get("title") or []
        title = normalize_whitespace(
            " ".join(str(segment.get("value") or "") for segment in title_segments)
        )
        extract_segments = item.get("extract") or []
        snippet = normalize_whitespace(
            " ".join(str(segment.get("value") or "") for segment in extract_segments)
        )
        if not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results