"""Exercise the public MCP v2 client contract in both protocol eras."""

import asyncio

import pytest
from mcp import Client

from mcp_prism_search import __version__
from mcp_prism_search.server import mcp


@pytest.mark.parametrize(
    ("mode", "protocol"),
    [("auto", "2026-07-28"), ("legacy", "2025-11-25")],
)
def test_protocol_compatibility(mode, protocol, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path))

    async def verify():
        async with Client(mcp, mode=mode) as client:
            assert client.protocol_version == protocol
            assert client.server_info.name == "mcp-prism-search"
            assert client.server_info.version == __version__
            listing = await client.list_tools()
            tools = listing.tools if hasattr(listing, "tools") else listing[0]
            assert {tool.name for tool in tools} == {
                "web_search", "web_fetch", "ddg_search", "ddg_deep_search",
                "vertical_search", "cache_stats", "cache_prune", "cache_clear",
            }
            result = await client.call_tool("cache_stats", {})
            assert not result.is_error
            assert isinstance(result.structured_content, dict)

    asyncio.run(verify())
