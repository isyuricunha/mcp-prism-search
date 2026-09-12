"""Tavily search provider — 1,000 free credits/month, plus a keyless mode.

With TAVILY_API_KEY set: authenticated requests (Bearer tvly-...).
Without: keyless mode via the X-Tavily-Access-Mode header (rate-limited).
"""

from __future__ import annotations

import os

import httpx

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace

SEARCH_URL = "https://api.tavily.com/search"
KEYLESS_HEADERS = {"X-Tavily-Access-Mode": "keyless"}


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = os.getenv("TAVILY_API_KEY")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers.update(KEYLESS_HEADERS)

    body: dict = {
        "query": request.query,
        "max_results": count,
        "search_depth": "basic",
        "include_answer": False,
    }
    if request.blocked_domains:
        body["exclude_domains"] = list(request.blocked_domains)
    if request.allowed_domains:
        body["include_domains"] = list(request.allowed_domains)
    time_filter_code = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(
        request.time_filter or ""
    )
    if time_filter_code:
        body["time_range"] = time_filter_code

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(SEARCH_URL, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for item in data.get("results", []):
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("content") or ""))
        if not url.startswith("http") or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results