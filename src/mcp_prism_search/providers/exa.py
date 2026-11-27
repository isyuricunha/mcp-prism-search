"""Exa neural/keyword search provider (~1,430 free searches/month, no card)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from mcp_prism_search import runtime
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://api.exa.ai/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = runtime.setting("EXA_API_KEY")
    if not api_key:
        raise RuntimeError("EXA_API_KEY is not set")

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    body: dict = {"query": request.query, "numResults": count}
    if request.allowed_domains:
        body["includeDomains"] = list(request.allowed_domains)
    if request.blocked_domains:
        body["excludeDomains"] = list(request.blocked_domains)
    time_filter_code = {"day": 1, "week": 7, "month": 30, "year": 365}.get(
        request.time_filter or ""
    )
    if time_filter_code:
        body["startPublishedDate"] = (
            datetime.now(UTC) - timedelta(days=time_filter_code)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(SEARCH_URL, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    from mcp_prism_search.search import valid_result_url

    results: list[SearchResult] = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(
            str(item.get("text") or item.get("summary") or "")
        )
        if not valid_result_url(url) or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results