"""Serper Google SERP provider (2,500 free queries one-time at signup)."""

from __future__ import annotations

import os

import httpx

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace

SEARCH_URL = "https://google.serper.dev/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is not set")

    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    body: dict = {"q": request.query, "num": max(count, 10)}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(SEARCH_URL, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    results: list[SearchResult] = []
    for item in data.get("organic", []):
        url = str(item.get("link") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(str(item.get("snippet") or ""))
        if not url.startswith("http") or not title:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results