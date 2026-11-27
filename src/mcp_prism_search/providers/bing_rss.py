"""Bing RSS search — keyless XML output, very stable from datacenter IPs."""

from __future__ import annotations

import defusedxml.ElementTree as ET  # untrusted feed: XXE-safe parser
import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace
from mcp_prism_search.upstream import bounded_request


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"q": request.query, "count": str(count), "format": "rss"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await bounded_request(
            client, "GET", "https://www.bing.com/search", params=params
        )

    from mcp_prism_search.search import valid_result_url

    root = ET.fromstring(response.content)
    results: list[SearchResult] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not title or not valid_result_url(link):
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
