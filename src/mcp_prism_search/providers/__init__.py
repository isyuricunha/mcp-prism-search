"""Provider registry: every search source of the aggregator.

Auto mode builds a fallback chain from these specs, ordered by recent
health. Providers whose required env var is missing are skipped.
"""

from __future__ import annotations

import os

from mcp_prism_search.models import SearchRequest, SearchResult
from mcp_prism_search.providers import (  # noqa: F401
    bing_rss,
    brave,
    ddg_html,
    ddgs_lib,
    exa,
    freeserp,
    hackernews,
    jina,
    keenable,
    mwmbl,
    searxng,
    serper,
    tavily,
    tinyfish,
    wikipedia,
    youcom,
    youcom_keyless,
)
from mcp_prism_search.providers.base import (  # noqa: F401
    ProviderHealth,
    ProviderSpec,
    build_chain,
    health_for,
    record,
)

SPECS: dict[str, ProviderSpec] = {}


def _spec(name: str, kind: str, needs: str | None, description: str, search_fn) -> ProviderSpec:
    return ProviderSpec(
        name=name, kind=kind, needs=needs, description=description, search=search_fn
    )


def build_registry() -> None:
    """Populate SPECS from the provider modules."""
    SPECS.clear()

    def add(name: str, kind: str, needs: str | None, description: str, search_fn) -> None:
        SPECS[name] = _spec(name, kind, needs, description, search_fn)

    # Keyless general web (live-tested from server IP)
    add("ddg_html", "general", None,
        "DuckDuckGo HTML endpoint scraping (IP-reputation dependent).",
        ddg_html.search)
    add("ddgs:yahoo", "general", None,
        "Yahoo web results (Bing feed) via ddgs.",
        ddgs_lib.make_search(backend="yahoo"))
    add("bing_rss", "general", None,
        "Bing RSS XML search (stable, no key).",
        bing_rss.search)
    add("mwmbl", "general", None,
        "Mwmbl community-index search (keyless, AGPL).",
        mwmbl.search)
    add("freeserp", "general", None,
        "FreeSerp own-index SERP (keyless JSON API).",
        freeserp.search)
    add("keenable", "general", None,
        "Keenable keyless web search (extracted text snippets).",
        keenable.search)

    add("youcom_keyless", "general", None,
        "You.com public MCP search (100 queries per day).",
        youcom_keyless.search)

    # Keyed general web (free tiers verified in docs)
    add("tinyfish", "general", "TINYFISH_API_KEY",
        "TinyFish TinySearch official REST API (free allowance; key required).",
        tinyfish.search)
    add("tavily", "general", "TAVILY_API_KEY",
        "Tavily authenticated search (API key required).",
        tavily.search)
    add("exa", "general", "EXA_API_KEY",
        "Exa neural/keyword search (~1,430 free searches/month, no card).",
        exa.search)
    add("brave", "general", "BRAVE_API_KEY",
        "Brave Search API (~1,000 free searches/month via recurring credits; "
        "card required at signup).",
        brave.search)
    add("youcom", "general", "YDC_API_KEY",
        "You.com web search ($100 one-time free credit).",
        youcom.search)
    add("serper", "general", "SERPER_API_KEY",
        "Serper Google SERP (2,500 free queries one-time).",
        serper.search)
    add("jina", "general", "JINA_API_KEY",
        "Jina s.jina.ai search with full page content.",
        jina.search)

    # Self-hosted (needs instance URL)
    add("searxng", "general", "SEARXNG_URL",
        "Self-hosted SearXNG JSON API.",
        searxng.search)

    # Verticals
    add("wikipedia", "vertical", None,
        "Wikipedia encyclopedic search (MediaWiki API).",
        wikipedia.search)
    add("hackernews", "vertical", None,
        "HackerNews full-text search (Algolia API).",
        hackernews.search)


def available_providers(kind: str | None = None) -> dict[str, ProviderSpec]:
    if not SPECS:
        build_registry()
    result = {}
    for name, spec in SPECS.items():
        if spec.needs and not os.getenv(spec.needs):
            continue
        if kind and spec.kind != kind:
            continue
        result[name] = spec
    return result


async def run_provider(
    name: str,
    request: SearchRequest,
    count: int,
    timeout: int,
) -> list[SearchResult]:
    if not SPECS:
        build_registry()
    return await SPECS[name].search(request, count, timeout)


def resolve_chain(requested: str, kind: str | None = None) -> list[str]:
    if not SPECS:
        build_registry()
    avail = available_providers(kind)
    if requested == "all":
        return build_chain("auto", avail)
    return build_chain(requested, avail)