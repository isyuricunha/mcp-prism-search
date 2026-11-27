"""Mwmbl community-index search provider (keyless, AGPL)."""

from __future__ import annotations

import asyncio
import time

import httpx

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

_lock: asyncio.Lock | None = None
_loop: asyncio.AbstractEventLoop | None = None
_last_request = 0.0

SEARCH_URL = "https://api.mwmbl.org/api/v2/search/"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"q": request.query}

    global _last_request, _lock, _loop
    loop = asyncio.get_running_loop()
    if _loop is not loop:
        _loop, _lock = loop, asyncio.Lock()
    assert _lock is not None
    async with _lock:
        await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.get(SEARCH_URL, params=params)
            response.raise_for_status()
            data = response.json()

    results: list[SearchResult] = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url.startswith("http"):
            continue
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("content") or ""))
        if not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results