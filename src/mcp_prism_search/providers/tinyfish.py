"""TinyFish TinySearch: authenticated web search over the official REST API."""

from __future__ import annotations

import httpx

from mcp_prism_search import runtime
from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace
from mcp_prism_search.upstream import bounded_request

SEARCH_URL = "https://api.search.tinyfish.ai"
RECENCY_MINUTES = {"day": 1440, "week": 10080, "month": 43200, "year": 525600}
MAX_PAGE = 10


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    api_key = runtime.setting("TINYFISH_API_KEY")
    if not api_key:
        raise RuntimeError("TINYFISH_API_KEY is not set")

    params: dict[str, str | int] = {"query": request.query}
    if request.time_filter:
        params["recency_minutes"] = RECENCY_MINUTES[request.time_filter]
    if request.allowed_domains:
        params["include_domains"] = ",".join(request.allowed_domains)
    if request.blocked_domains:
        params["exclude_domains"] = ",".join(request.blocked_domains)

    from mcp_prism_search.search import _dedupe_results, normalize_url_for_dedupe

    results: list[SearchResult] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), headers={"X-API-Key": api_key},
    ) as client:
        for page in range(MAX_PAGE + 1):
            response = await bounded_request(
                client, "GET", SEARCH_URL, params={**params, "page": page}
            )
            rows = response.json()["results"]
            if not isinstance(rows, list):
                raise ValueError("Invalid TinyFish search results")
            if not rows:
                break
            previous_count = len(results)
            for item in rows:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                title = normalize_whitespace(str(item.get("title") or ""))
                if not title:
                    continue
                candidate = SearchResult(
                    title=title, url=url,
                    snippet=normalize_whitespace(str(item.get("snippet") or "")),
                )
                if not _dedupe_results([candidate]):
                    continue
                identity = normalize_url_for_dedupe(url)
                if identity in seen:
                    continue
                seen.add(identity)
                results.append(candidate)
                if len(results) >= count:
                    return results
            if len(results) == previous_count:
                break
    return results
