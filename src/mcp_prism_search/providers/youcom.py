"""You.com web search provider ($100 one-time free credit at signup)."""

from __future__ import annotations

import httpx

from mcp_prism_search import runtime
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

SEARCH_URL = "https://ydc-index.io/v1/search"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = runtime.setting("YDC_API_KEY")
    if not api_key:
        raise RuntimeError("YDC_API_KEY is not set")

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    body: dict = {
        "query": request.query, "count": min(count, 100), "safesearch": request.safe_search,
    }

    if request.time_filter:
        body["freshness"] = request.time_filter
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(SEARCH_URL, json=body, headers=headers)
        response.raise_for_status()
        data = response.json()

    return parse_web_results(data, count)


def parse_web_results(data: object, count: int) -> list[SearchResult]:
    """Shared decoding for authenticated and public MCP search responses."""
    if not isinstance(data, dict) or not isinstance(data.get("results"), dict):
        raise ValueError("Invalid You.com search response")
    web = data["results"].get("web", [])
    if not isinstance(web, list):
        raise ValueError("Invalid You.com web results")
    from mcp_prism_search.search import valid_result_url

    results: list[SearchResult] = []
    for hit in web:
        if not isinstance(hit, dict):
            continue
        url = hit.get("url")
        title = hit.get("title")
        if not isinstance(url, str) or not isinstance(title, str):
            continue
        title = normalize_whitespace(title)
        if not title or not valid_result_url(url):
            continue
        description = hit.get("description")
        snippets = hit.get("snippets")
        if not isinstance(description, str) or not description.strip():
            description = (" ".join(s for s in snippets if isinstance(s, str))
                           if isinstance(snippets, list) else "")
        results.append(SearchResult(title=title, url=url,
                                    snippet=normalize_whitespace(description)))
        if len(results) >= count:
            break
    return results
