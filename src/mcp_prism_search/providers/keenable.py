"""Keenable keyless web search provider (extracted text snippets)."""

from __future__ import annotations

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://api.keenable.ai/v1/search/public"
HEADERS = {
    "X-Keenable-Title": "mcp-prism-search",
    "Content-Type": "application/json",
}


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    body = {"query": request.query, "max_results": min(count, 20)}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(SEARCH_URL, json=body, headers=HEADERS)
        response.raise_for_status()
        data = response.json()

    rows = data if isinstance(data, list) else (data.get("results") or data.get("data") or [])
    results: list[SearchResult] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(
            str(item.get("text") or item.get("snippet") or item.get("content") or "")
        )
        if not url.startswith("http") or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results