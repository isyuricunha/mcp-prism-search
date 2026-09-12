"""FreeSerp own-index SERP provider (keyless JSON API)."""

from __future__ import annotations

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://freeserp.ai/api.php"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"q": request.query, "size": str(min(count, 100))}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.get(SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()

    rows = data if isinstance(data, list) else (data.get("results") or [])
    results: list[SearchResult] = []
    for item in rows:
        url = str(item.get("url") or item.get("link") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("summary") or item.get("snippet") or ""))
        if not url.startswith("http") or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results