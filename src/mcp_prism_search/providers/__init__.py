"""Provider registry: every search source of the aggregator.

Auto mode builds a fallback chain from these specs, ordered by recent
health. Providers whose required env var is missing are skipped.
"""

from __future__ import annotations

from mcp_prism_search import runtime
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


def build_registry() -> None:
    """Build the single registry consumed by retrieval, MCP schemas and the UI."""
    SPECS.clear()

    def add(name, kind, needs, title, description, docs, search_fn, *, signup=None, usage=""):
        SPECS[name] = ProviderSpec(
            name=name, kind=kind, needs=needs, description=description, search=search_fn,
            display_name=title, docs_url=docs, signup_url=signup, usage_note=usage,
        )

    add("ddg_html", "general", None, "DuckDuckGo HTML",
        "Keyless HTML search; upstream protections and network reputation affect results.",
        "https://duckduckgo.com/", ddg_html.search,
        usage="No key; upstream throttling or empty responses are possible.")
    add("ddgs:yahoo", "general", None, "Yahoo via DDGS",
        "Yahoo web results through the DDGS search library.",
        "https://search.yahoo.com/", ddgs_lib.make_search(backend="yahoo"),
        usage="No key; availability depends on the upstream service.")
    add("bing_rss", "general", None, "Bing RSS",
        "Web results from Bing's RSS feed.",
        "https://www.bing.com/", bing_rss.search,
        usage="No key; upstream availability may vary.")
    add("mwmbl", "general", None, "Mwmbl",
        "Community-maintained search index.",
        "https://developer.mwmbl.org/", mwmbl.search,
        usage="Anonymous quota: 1,000/month, 1/second per IP; paced locally.")
    add("freeserp", "general", None, "FreeSerp",
        "Web results from the FreeSerp search index.",
        "https://freeserp.ai/serp-scraper-api", freeserp.search,
        usage="Public keyless endpoint; consult provider terms for current limits.")
    add("keenable", "general", None, "Keenable",
        "Public web search with extracted text snippets.",
        "https://docs.keenable.ai/", keenable.search,
        usage="Public keyless API; published limits: 1,000/hour and 10/second.")
    add("youcom_keyless", "general", None, "You.com (public MCP)",
        "Public MCP search without paid extraction.",
        "https://you.com/docs/agents/mcp-server", youcom_keyless.search,
        usage="Published public allowance: 100 queries/day.")

    add("tinyfish", "general", "TINYFISH_API_KEY", "TinyFish TinySearch",
        "Official web search API with domain and recency filtering.",
        "https://docs.tinyfish.ai/tinysearch/reference", tinyfish.search,
        signup="https://agent.tinyfish.ai/sign-up",
        usage="Free Search: 30/min, 500/hour; pages count separately. "
              "Check account allowance and Wallet settings. TinyAnswer and Research are separate.")
    add("tavily", "general", "TAVILY_API_KEY", "Tavily",
        "Search API for research and AI applications.",
        "https://docs.tavily.com/", tavily.search,
        signup="https://app.tavily.com/",
        usage="Basic search: one credit; free monthly allowance subject to provider terms.")
    add("exa", "general", "EXA_API_KEY", "Exa",
        "Semantic and keyword web search API.",
        "https://exa.ai/docs", exa.search, signup="https://dashboard.exa.ai/",
        usage="Usage-based search and synthesis; check current account credits.")
    add("brave", "general", "BRAVE_API_KEY", "Brave Search",
        "Web search through Brave's independent search index.",
        "https://api-dashboard.search.brave.com/documentation", brave.search,
        signup="https://api-dashboard.search.brave.com/",
        usage="Usage-based API; plan credits and Answers charges vary.")
    add("youcom", "general", "YDC_API_KEY", "You.com (API)",
        "Authenticated You.com web search API.",
        "https://you.com/docs/guides/search", youcom.search,
        signup="https://you.com/",
        usage="Authenticated usage and account credits; separate from public MCP allowance.")
    add("serper", "general", "SERPER_API_KEY", "Serper",
        "Google search results through the Serper API.",
        "https://serper.dev/", serper.search, signup="https://serper.dev/",
        usage="Check the account's introductory allowance and remaining credits.")
    add("jina", "general", "JINA_API_KEY", "Jina Search",
        "Web search with text content prepared for research.",
        "https://api.jina.ai/docs", jina.search,
        signup="https://jina.ai/api-dashboard/key-manager",
        usage="Token-based usage; check the account grant and rate limits.")
    add("searxng", "general", "SEARXNG_URL", "SearXNG",
        "Your own metasearch instance, configured through the environment.",
        "https://docs.searxng.org/dev/search_api.html", searxng.search,
        usage="Limits depend on the configured instance and its upstream engines.")

    add("wikipedia", "vertical", None, "Wikipedia",
        "Encyclopedic search through MediaWiki.",
        "https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy", wikipedia.search,
        usage="No key; respect Wikimedia's access policy.")
    add("hackernews", "vertical", None, "Hacker News",
        "Search technology discussions and submissions through Algolia.",
        "https://hn.algolia.com/api", hackernews.search,
        usage="No key; subject to public API limits.")


def general_provider_ids() -> list[str]:
    if not SPECS:
        build_registry()
    return [name for name, spec in SPECS.items() if spec.kind == "general"]


def configured(spec: ProviderSpec) -> bool:
    """Whether the provider has all required local configuration."""
    return not spec.needs or bool((runtime.setting(spec.needs) or "").strip())


def enabled(spec: ProviderSpec) -> bool:
    """Effective activation, not just the administrator's stored preference."""
    return configured(spec) and runtime.enabled(spec.name)


def available_providers(kind: str | None = None) -> dict[str, ProviderSpec]:
    if not SPECS:
        build_registry()
    result = {}
    for name, spec in SPECS.items():
        if not enabled(spec):
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
    spec = available_providers().get(name)
    if spec is None:
        raise ValueError("Provider is no longer available")
    return await spec.search(request, count, timeout)


def resolve_chain(requested: str, kind: str | None = None) -> list[str]:
    if not SPECS:
        build_registry()
    avail = available_providers(kind)
    if requested == "keyless":
        return build_chain("auto", {name: spec for name, spec in avail.items() if not spec.needs})
    if requested == "all":
        return build_chain("auto", avail)
    return build_chain(requested, avail)