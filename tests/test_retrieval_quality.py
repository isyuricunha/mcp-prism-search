"""Offline, deterministic ranking and extraction quality evaluations."""
import pytest

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.search import _final_results, rank_results


def row(title, url, snippet):
    return SearchResult(title=title, url=url, snippet=snippet)


@pytest.mark.parametrize(("query", "target", "distractors"), [
    ("Python asyncio TaskGroup", row("Python asyncio TaskGroup", "https://docs.python.org/tasks",
                                     "Structured concurrency with asyncio TaskGroup"), [
        row("Python news", "https://example.org/news", "Python release"),
        row("Department of transport", "https://example.gov/", "Annual report"),
    ]),
    ("Docker Compose healthcheck", row("Docker Compose healthcheck",
                                      "https://docs.docker.com/compose", "healthcheck reference"), [
        row("Docker installation", "https://example.com/install", "Install Docker"),
        row("Compose music", "https://example.org/music", "Write a song"),
    ]),
    ("MCP Streamable HTTP", row("MCP Streamable HTTP", "https://modelcontextprotocol.io/spec",
                               "MCP transport specification"), [
        row("HTTP tutorial", "https://example.org/http", "Learn HTTP"),
        row("MCP unrelated", "https://example.net/", "General introduction"),
    ]),
])
def test_relevant_documentation_ranks_first(query, target, distractors):
    assert rank_results([*distractors, target], query)[0] == target


def test_quality_pipeline_respects_domain_controls_and_deduplication():
    primary = row("Reference", "https://docs.example.org/ref", "Reference")
    duplicate = primary.model_copy(update={"url": "https://docs.example.org:443/ref#intro"})
    unrelated = row("Reference", "https://outside.example/ref", "Reference")
    request = SearchRequest(query="reference", allowed_domains=["example.org"], max_results=2)
    assert _final_results([unrelated, primary, duplicate], request) == [primary]


def test_duplicate_url_retains_more_relevant_description():
    weak = row("Welcome", "https://docs.example.org/ref", "Introduction")
    strong = row("Python asyncio TaskGroup", "https://docs.example.org/ref#asyncio",
                 "Python structured concurrency with asyncio TaskGroup")
    request = SearchRequest(query="Python asyncio TaskGroup")
    assert _final_results([weak, strong], request) == [strong]
