"""Bing RSS search — keyless XML output, very stable from datacenter IPs."""

from __future__ import annotations

import defusedxml.ElementTree as ET  # untrusted feed: XXE-safe parser
import httpx

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"q": request.query, "count": str(count), "format": "rss"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get("https://www.bing.com/search", params=params)
        response.raise_for_status()

    root = ET.fromstring(response.content)
    results: list[SearchResult] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not link.startswith("http"):
            continue
        results.append(
            SearchResult(
                title=normalize_whitespace(title),
                url=link,
                snippet=normalize_whitespace(description),
            )
        )
        if len(results) >= count:
            break
    return results