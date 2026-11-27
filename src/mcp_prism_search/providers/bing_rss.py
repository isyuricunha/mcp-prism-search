"""Bing RSS search — keyless XML output, very stable from datacenter IPs."""

from __future__ import annotations

from urllib.parse import urlparse

import defusedxml.ElementTree as ET  # untrusted feed: XXE-safe parser
import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace


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
        try:
            parsed = urlparse(link)
            if (not title or parsed.scheme not in {"http", "https"}
                    or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.port == 0
                    or any(c.isspace() or ord(c) < 32 for c in link)):
                continue
        except ValueError:
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