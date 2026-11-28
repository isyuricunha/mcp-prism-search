"""FreeSerp own-index SERP provider (keyless JSON API)."""

from __future__ import annotations

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace
from mcp_prism_search.upstream import bounded_request

SEARCH_URL = "https://freeserp.ai/api.php"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"q": request.query, "index": "web", "size": str(min(count, 100))}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await bounded_request(client, "GET", SEARCH_URL, params=params)
        data = response.json()

    if isinstance(data, dict):
        # The API can return successful HTTP responses containing site profiles
        # instead of web documents when its Global engine is unavailable.
        if data.get("engine_fallback") is True or data.get("index") in ("sites", "main"):
            raise ValueError("FreeSerp web index unavailable")
        if data.get("ok") is False:
            raise ValueError("Invalid FreeSerp response")
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("Invalid FreeSerp response")
    from mcp_prism_search.search import valid_result_url

    results: list[SearchResult] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("snippet") or item.get("summary") or ""))
        if not valid_result_url(url) or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results