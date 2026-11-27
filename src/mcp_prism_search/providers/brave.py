"""Brave Search API provider ($5/month recurring credits; card required at signup)."""

from __future__ import annotations

import httpx

from mcp_prism_search import runtime
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = runtime.setting("BRAVE_API_KEY")
    if not api_key:
        raise RuntimeError("BRAVE_API_KEY is not set")

    params: dict[str, str] = {"q": request.query, "count": str(min(count, 20))}
    safe_map = {"off": "off", "moderate": "moderate", "strict": "strict"}
    params["safesearch"] = safe_map[request.safe_search]
    if request.time_filter:
        params["freshness"] = {"day": "pd", "week": "pw", "month": "pm",
                               "year": "py"}[request.time_filter]
    # Multiple site operators can change query semantics; local filtering is canonical.
    if len(request.allowed_domains) == 1:
        params["q"] += " site:" + request.allowed_domains[0]

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

    from mcp_prism_search.search import valid_result_url

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in (data.get("web") or {}).get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("description") or ""))
        if not valid_result_url(url) or not title or url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results