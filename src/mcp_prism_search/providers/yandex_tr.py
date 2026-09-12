"""Yandex TR search provider (mobile UA avoids SmartCaptcha from datacenter IPs)."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://yandex.com.tr/search/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"text": request.query}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=True, headers=HEADERS
    ) as client:
        response = await client.get(SEARCH_URL, params=params)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[SearchResult] = []
    seen: set[str] = set()

    for anchor in soup.select("a.organic__url, a.OrganicTitle-Link, li.serp-item a.link"):
        url = anchor.get("href") or ""
        title = normalize_whitespace(anchor.get_text(" ", strip=True))
        if not url.startswith("http") or not title or url in seen:
            continue
        seen.add(url)
        snippet = ""
        parent = anchor.find_parent("li")
        if parent is not None:
            text_el = parent.select_one(".OrganicTextContentSpan, .organic__text, .text-container")
            snippet = normalize_whitespace(text_el.get_text(" ", strip=True)) if text_el else ""
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results