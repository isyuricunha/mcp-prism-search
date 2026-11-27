"""You.com public MCP search, without credentials."""

from __future__ import annotations

import json

from mcp import Client

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.providers.youcom import parse_web_results

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
    if result.structured_content is not None:
        try:
            return parse_web_results(result.structured_content, count)
        except ValueError:
            pass
    for item in result.content:
        if item.type != "text":
            continue
        try:
            return parse_web_results(json.loads(item.text), count)
        except (ValueError, TypeError):
            continue
    raise ValueError("Invalid You.com public MCP search response")
