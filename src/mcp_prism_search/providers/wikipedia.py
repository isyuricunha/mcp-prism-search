"""Wikipedia encyclopedic search provider (MediaWiki API, keyless)."""

from __future__ import annotations

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

API_URL = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "mcp-prism-search/0.7 (personal MCP aggregator)"}


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": request.query,
        "srlimit": min(count, 20),
        "format": "json",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), headers=HEADERS) as client:
        response = await client.get(API_URL, params=params)
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for item in data.get("query", {}).get("search", []):
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("snippet") or ""))
        if not title:
            continue
        wiki_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
        results.append(SearchResult(title=title, url=wiki_url, snippet=snippet))
        if len(results) >= count:
            break
    return results