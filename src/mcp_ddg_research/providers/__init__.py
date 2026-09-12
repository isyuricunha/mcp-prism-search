"""Provider registry: every search source of the aggregator.

Auto mode builds a fallback chain from these specs, ordered by recent
health. Providers whose required env var is missing are skipped.
"""

from __future__ import annotations

import os

from mcp_ddg_research.models import SearchRequest, SearchResult
from mcp_ddg_research.providers import (  # noqa: F401
    bing_html,
    bing_rss,
    brave,
    ddg_html,
    ddg_lite,
    ddgs_lib,
    exa,
    fourget,
    freeserp,
    hackernews,
    jina,
    keenable,
    mwmbl,
    searxng,
    serper,
    tavily,
    wikipedia,
    yandex_tr,
    youcom,
)
from mcp_ddg_research.providers.base import (  # noqa: F401
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
    add("bing_html", "general", None,
        "Bing SERP scraping (bing.com/search, li.b_algo).",
        bing_html.search)
    add("ddg_html", "general", None,
        "DuckDuckGo HTML endpoint scraping (IP-reputation dependent).",
        ddg_html.search)
    add("ddg_lite", "general", None,
        "DuckDuckGo lite endpoint (lightweight HTML SERP).",
        ddg_lite.search)
    add("ddgs:bing", "general", None,
        "Bing web results via the ddgs multi-backend library.",
        ddgs_lib.make_search(backend="bing"))
    add("ddgs:yahoo", "general", None,
        "Yahoo web results (Bing feed) via ddgs.",
        ddgs_lib.make_search(backend="yahoo"))
    add("ddgs:brave", "general", None,
        "Brave results via the ddgs library.",
        ddgs_lib.make_search(backend="brave"))
    add("ddgs:yandex", "general", None,
        "Yandex results via the ddgs library (fragile from some IPs).",
        ddgs_lib.make_search(backend="yandex"))
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

    # Keyed general web (free tiers verified in docs)
    add("tavily", "general", None,
        "Tavily search: 1,000 credits/month free, or keyless mode (rate-limited). "
        "Set TAVILY_API_KEY for the authenticated tier.",
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
    return build_chain(requested, avail)