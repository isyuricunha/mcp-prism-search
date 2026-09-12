"""DuckDuckGo lite search provider (keyless, lightweight HTML endpoint)."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace

SEARCH_URL = "https://lite.duckduckgo.com/lite/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html",
    "Accept-Language": "en-US,en;q=0.9",
}


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params = {"q": request.query}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=False, headers=HEADERS
    ) as client:
        response = await client.get(SEARCH_URL, params=params)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[SearchResult] = []

    for link in soup.select("a.result-link"):
        url = link.get("href") or ""
        title = normalize_whitespace(link.get_text(" ", strip=True))
        if not url.startswith("http") or not title:
            continue
        snippet = ""
        snippet_td = link.find_parent("tr")
        if snippet_td is not None:
            snippet_row = snippet_td.find_next_sibling("tr")
            if snippet_row is not None:
                snippet_el = snippet_row.select_one("td.result-snippet")
                if snippet_el is not None:
                    snippet = normalize_whitespace(snippet_el.get_text(" ", strip=True))
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= count:
            break
    return results