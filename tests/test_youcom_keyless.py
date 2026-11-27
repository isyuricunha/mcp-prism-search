"""Public MCP provider contract."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from mcp_prism_search.models import SearchRequest
from mcp_prism_search.providers import youcom_keyless


@pytest.mark.parametrize("structured", [True, False])
def test_public_mcp_response(monkeypatch, structured):
    data = {"results": {"web": [
        {"url": "https://example.com", "title": "Example", "description": "Excerpt"}
    ]}}

    class FakeClient:
        def __init__(self, url, **kwargs):
            assert url == "https://api.you.com/mcp/free"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call_tool(self, name, args):
            assert name == "you-search"
            assert args["count"] == 5
            assert args["extraction"] == "none"
            assert args["freshness"] == "month"
            return SimpleNamespace(
                is_error=False, structured_content=data if structured else None,
                content=[SimpleNamespace(type="text", text=json.dumps(data))],
            )

    monkeypatch.setattr(youcom_keyless, "Client", FakeClient)
    rows = asyncio.run(youcom_keyless.search(
        SearchRequest(query="needle", time_filter="month"), 5, 10
    ))
    assert rows[0].snippet == "Excerpt"
