"""Jina AI search provider (s.jina.ai — search + full page content).

Requires JINA_API_KEY (new keys include a 10M token grant; r.jina.ai URL
extraction is separate and keyless at 20 RPM).
"""

from __future__ import annotations

import os

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://s.jina.ai/"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = os.getenv("JINA_API_KEY")
    if not api_key:
        raise RuntimeError("JINA_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "X-Respond-With": "no-content",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.get(
            f"{SEARCH_URL}{request.query}", headers=headers
        )
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for item in (data.get("data") or [])[:count]:
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(
            str(item.get("description") or item.get("content") or "")
        )
        if not url.startswith("http") or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results