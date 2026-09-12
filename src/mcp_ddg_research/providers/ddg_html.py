"""DuckDuckGo HTML endpoint provider (keyless, IP-reputation dependent)."""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.text import normalize_whitespace

HTML_URL = "https://html.duckduckgo.com/html/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
HTML_SAFE_SEARCH = {"off": "-2", "moderate": "-1", "strict": "1"}


def resolve_duckduckgo_redirect_url(href: str) -> str:
    from urllib.parse import parse_qs, urljoin, urlparse

    if href.startswith("//"):
        resolved = f"https:{href}"
    elif href.startswith("/"):
        resolved = urljoin(HTML_URL, href)
    else:
        resolved = href

    parsed = urlparse(resolved)
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    is_ddg_host = hostname == "duckduckgo.com" or hostname.endswith(".duckduckgo.com")
    if is_ddg_host and parsed.path.rstrip("/") == "/l":
        values = parse_qs(parsed.query).get("uddg")
        if values and values[0]:
            return values[0]
    return resolved


def _result_from_soup_link(link) -> SearchResult | None:
    href = link.get("href") or ""
    title = normalize_whitespace(link.get_text(" ", strip=True))
    if not href or not title:
        return None
    container = link.find_parent("div", class_="result") or link.parent
    snippet = ""
    if container is not None:
        snippet_el = container.select_one(".result__snippet")
        if snippet_el is not None:
            snippet = normalize_whitespace(snippet_el.get_text(" ", strip=True))
    return SearchResult(
        title=title,
        url=resolve_duckduckgo_redirect_url(href),
        snippet=snippet,
    )


def _dedupe(results: list[SearchResult], count: int) -> list[SearchResult]:
    seen: set[str] = set()
    out: list[SearchResult] = []
    for result in results:
        key = result.url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(result)
        if len(out) >= count:
            break
    return out


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    params: dict[str, str | int] = {
        "q": request.query,
        "kp": HTML_SAFE_SEARCH[request.safe_search],
    }
    time_filter_code = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(
        request.time_filter or ""
    )
    if time_filter_code:
        params["df"] = time_filter_code

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=False, headers=HEADERS
    ) as client:
        response = await client.get(HTML_URL, params=params)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results: list[SearchResult] = []
    for link in soup.select("a.result__a"):
        result = _result_from_soup_link(link)
        if result is not None:
            results.append(result)
    return _dedupe(results, count)