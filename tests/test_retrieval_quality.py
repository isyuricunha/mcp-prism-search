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


def test_final_results_bound_title_and_snippet_context():
    from mcp_prism_search.search import MAX_RESULT_SNIPPET, MAX_RESULT_TITLE

    result = row("needle " * 200, "https://example.org", "evidence " * 1000)
    request = SearchRequest(query="needle")
    bounded = _final_results([result], request)[0]
    assert 0 < len(bounded.title) <= MAX_RESULT_TITLE
    assert 0 < len(bounded.snippet) <= MAX_RESULT_SNIPPET
    assert bounded.url == result.url


def test_context_bounded_ranking_is_idempotent_for_cache_validation():
    first = row("Generic", "https://a.example/", "x" * 5000 + " needle")
    second = row("Generic", "https://b.example/", "needle")
    request = SearchRequest(query="needle")
    final = _final_results([first, second], request)
    assert final[0].url == second.url
    assert _final_results(final, request) == final


def test_dedupe_idna_unicode_and_ascii_hostnames():
    unicode_row = row("Reference", "https://bücher.example/ref", "Evidence")
    ascii_row = row("Reference", "https://xn--bcher-kva.example:443/ref", "Evidence")
    assert rank_results([unicode_row, ascii_row], "reference") == [unicode_row]
