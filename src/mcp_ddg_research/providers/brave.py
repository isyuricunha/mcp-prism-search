"""Brave Search API provider ($5/month recurring credits; card required at signup)."""

from __future__ import annotations

import os

import httpx

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_API_KEY is not set")

    params: dict[str, str] = {"q": request.query, "count": str(count)}
    safe_map = {"off": "off", "moderate": "moderate", "strict": "strict"}
    params["safesearch"] = safe_map[request.safe_search]
    if request.allowed_domains:
        params["q"] += " " + " ".join(
            f"site:{domain}" for domain in request.allowed_domains
        )

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.get(
            SEARCH_URL,
            params=params,
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in (data.get("web") or {}).get("results", []):
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("description") or ""))
        if not url.startswith("http") or not title or url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results