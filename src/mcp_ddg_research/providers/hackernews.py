"""HackerNews full-text search provider (Algolia API, keyless)."""

from __future__ import annotations

import httpx

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace

SEARCH_URL = "https://hn.algolia.com/api/v1/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {
        "query": request.query,
        "hitsPerPage": min(count, 20),
        "tags": "story",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.get(SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for hit in data.get("hits", []):
        title = normalize_whitespace(str(hit.get("title") or hit.get("story_title") or ""))
        url = str(hit.get("url") or hit.get("story_url") or "")
        if not url.startswith("http"):
            url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title:
            continue
        snippet = normalize_whitespace(str(hit.get("story_text") or hit.get("comment_text") or ""))
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results