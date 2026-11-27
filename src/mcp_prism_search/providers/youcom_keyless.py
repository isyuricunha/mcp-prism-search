"""You.com public MCP search, without credentials."""

from __future__ import annotations

import json

from mcp import Client

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.text import normalize_whitespace

MCP_URL = "https://api.you.com/mcp/free"


async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]:
    arguments = {
        "query": request.query,
        "count": min(count, 100),
        "extraction": "none",
        "safesearch": request.safe_search,
    }
    if request.time_filter:
        arguments["freshness"] = request.time_filter
    async with Client(MCP_URL, read_timeout_seconds=timeout) as client:
        result = await client.call_tool("you-search", arguments)
    if result.is_error:
        raise RuntimeError("You.com public MCP search failed")
    data = result.structured_content
    if data is None:
        for item in result.content:
            if item.type == "text":
                try:
                    data = json.loads(item.text)
                except ValueError:
                    continue
                if isinstance(data, dict) and "results" in data:
                    break
    if not isinstance(data, dict) or not isinstance(data.get("results"), dict):
        raise ValueError("Invalid You.com search response")

    rows = []
    for item in data["results"].get("web", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = normalize_whitespace(str(item.get("title") or ""))
        snippet = normalize_whitespace(
            str(item.get("description") or " ".join(item.get("snippets") or []))
        )
        if url.startswith(("https://", "http://")) and title:
            rows.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(rows) >= count:
            break
    return rows
