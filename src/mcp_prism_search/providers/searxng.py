"""Self-hosted SearXNG JSON API provider (SEARXNG_URL env required)."""

from __future__ import annotations

import os

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SAFE_SEARCH = {"off": "0", "moderate": "1", "strict": "2"}


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    base_url = os.getenv("SEARXNG_URL", "").rstrip("/")
    params: dict[str, str | int] = {
        "q": request.query,
        "format": "json",
        "safesearch": SAFE_SEARCH[request.safe_search],
    }
    time_filter_code = (
        request.time_filter if request.time_filter in {"day", "month", "year"} else None
    )
    if time_filter_code:
        params["time_range"] = time_filter_code

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.get(f"{base_url}/search", params=params)
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        if not url.startswith("http") or not title or url in seen:
            continue
        seen.add(url)
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=normalize_whitespace(str(item.get("content") or "")),
            )
        )
        if len(results) >= count:
            break
    return results