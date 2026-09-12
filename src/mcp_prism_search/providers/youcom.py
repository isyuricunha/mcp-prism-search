"""You.com web search provider ($100 one-time free credit at signup)."""

from __future__ import annotations

import os

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://api.ydc-index.io/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = os.getenv("YDC_API_KEY")
    if not api_key:
        raise RuntimeError("YDC_API_KEY is not set")

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    body: dict = {"query": request.query, "num_web_results": count}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(SEARCH_URL, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for hit in data.get("hits", []):
        url = str(hit.get("url") or "")
        title = normalize_whitespace(str(hit.get("title") or ""))
        snippet = normalize_whitespace(str(hit.get("description") or ""))
        if not url.startswith("http") or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results