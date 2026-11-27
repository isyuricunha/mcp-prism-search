"""Jina AI search provider (s.jina.ai — search + full page content).

Requires JINA_API_KEY (new keys include a 10M token grant; r.jina.ai URL
extraction is separate and keyless at 20 RPM).
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from mcp_prism_search import runtime
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace
from mcp_prism_search.upstream import bounded_request

SEARCH_URL = "https://s.jina.ai/"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = runtime.setting("JINA_API_KEY")
    if not api_key:
        raise RuntimeError("JINA_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "X-Respond-With": "no-content",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await bounded_request(
            client, "GET",
            f"{SEARCH_URL}{quote(request.query, safe='')}", headers=headers
        )
        data = response.json()

    from mcp_prism_search.search import valid_result_url

    results: list[SearchResult] = []
    for item in data.get("data") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(
            str(item.get("description") or item.get("content") or "")
        )
        if not valid_result_url(url) or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results