"""4get metasearch JSON API provider (default instance: 4get.canine.tools)."""

from __future__ import annotations

import os

import httpx

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    base_url = os.getenv("FOURGET_BASE", "https://4get.canine.tools").rstrip("/")
    params = {"s": request.query}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.get(f"{base_url}/api/v1/web", params=params)
        response.raise_for_status()
        data = response.json()

    if data.get("status") != "ok":
        return []

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in data.get("web", []):
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        if not url.startswith("http") or not title or url in seen:
            continue
        seen.add(url)
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=normalize_whitespace(str(item.get("description") or "")),
            )
        )
        if len(results) >= count:
            break
    return results