"""HackerNews full-text search provider (Algolia API, keyless)."""

from __future__ import annotations

import re

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

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
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise ValueError("Invalid Hacker News response")
    for hit in data["hits"]:
        if not isinstance(hit, dict):
            continue
        title = normalize_whitespace(str(hit.get("title") or hit.get("story_title") or ""))
        url = str(hit.get("url") or hit.get("story_url") or "")
        if not url.startswith(("http://", "https://")):
            identifier = str(hit.get("objectID") or "")
            if not re.fullmatch(r"[0-9]{1,20}", identifier):
                continue
            url = f"https://news.ycombinator.com/item?id={identifier}"
        if not title:
            continue
        snippet = normalize_whitespace(str(hit.get("story_text") or hit.get("comment_text") or ""))
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results