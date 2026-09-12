"""Bing SERP scraping provider (keyless, polite volume)."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace

SEARCH_URL = "https://www.bing.com/search"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    window = request.search_window or count
    params = {"q": request.query, "count": str(max(window, count))}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=True, headers=HEADERS
    ) as client:
        response = await client.get(SEARCH_URL, params=params)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in soup.select("li.b_algo"):
        link = item.select_one("h2 a")
        if link is None:
            continue
        url = link.get("href") or ""
        title = normalize_whitespace(link.get_text(" ", strip=True))
        if not url.startswith("http") or not title:
            continue
        snippet_el = item.select_one(".b_caption p, p")
        snippet = normalize_whitespace(snippet_el.get_text(" ", strip=True)) if snippet_el else ""
        if url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results