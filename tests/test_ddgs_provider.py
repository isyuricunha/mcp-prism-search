"""Test the registered DDGS adapter independently from legacy DuckDuckGo."""
import asyncio

import pytest

from mcp_prism_search.models import SearchRequest
from mcp_prism_search.providers import ddgs_lib


def test_registered_ddgs_cannot_silently_switch_backend(monkeypatch):
    import ddgs

    class Unpinnable:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, max_results, safesearch, timelimit):
            pytest.fail("An unpinnable DDGS backend must not perform a search")

    monkeypatch.setattr(ddgs, "DDGS", Unpinnable)
    with pytest.raises(ValueError, match="cannot pin"):
        asyncio.run(ddgs_lib.make_search("yahoo")(SearchRequest(query="test"), 5, 5))
