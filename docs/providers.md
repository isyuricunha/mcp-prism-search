# Search provider policy

## Modes

- `web_search(provider="auto")`: choose the first provider with usable results,
  ordered by process-local success rate, then preferred order. No automatic retries.
- `web_search(provider="all")`: query every configured general provider concurrently.
- `web_search(provider="keenable")`: query exactly that provider.
- `web_search(providers=["keenable", "freeserp"])`: query exactly the selected set
  concurrently. Unknown, unavailable or conflicting selections are errors.

All modes enforce domain controls, validate HTTP(S) URLs, deduplicate and
rank before truncating. Upstream support for recency and SafeSearch varies;
those filters are not guaranteed by engines without native support.
Each provider gets the configured DDG_TIMEOUT_SECONDS budget; parallel
calls are bounded by that budget, except unavoidable synchronous worker
cleanup. Auto fallback may take multiple budgets. Provider errors are
reported without raw exception strings that may contain credentials.
Successful single/auto responses and complete parallel responses are cached;
degraded parallel responses are not cached. Cache keys include the selected
provider set and request filters. Health is process-local, not an SLA.

## Active providers

Preference on a fresh process: Keenable, FreeSerp, Bing RSS, DDGS Yahoo,
Mwmbl, You.com keyless, configured optional APIs and DuckDuckGo HTML as last resort.
Wikipedia and Hacker News are separate vertical tools.

- [Keenable](https://docs.keenable.ai/): public search with `max_results`.
- [FreeSerp](https://freeserp.ai/serp-scraper-api): use `index=web`, not the
  default homepage index.
- [Mwmbl](https://developer.mwmbl.org/): v2 API, anonymous quota of 1,000
  requests/month and 1 request/second per IP. Requests are paced per process.
- [SearXNG](https://docs.searxng.org/dev/search_api.html): configured instance;
  only supported time ranges are forwarded.
- [You.com](https://you.com/docs/guides/search): authenticated POST v1 search.
- [You.com public MCP](https://you.com/docs/agents/mcp-server): keyless
  search with 100 queries/day; plain snippets, no paid extraction.
- [Wikimedia](https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy):
  identifiable User-Agent with a project URL.

Tavily, Exa, Brave, You.com, Serper, Jina and SearXNG are enabled only when
their corresponding environment variable is configured. Configured API
providers can consume credits in auto or all mode; use explicit selection
when controlling spend. No quota evasion or proxy rotation is performed.

## Migration notes

Removed registry names: `bing_html`, `ddg_lite`, `ddgs:bing`,
`ddgs:yandex`, `ddgs:brave`. Removed unused modules: 4get and Yandex TR.
Use `bing_rss`, `ddg_html` or `ddgs:yahoo` where appropriate. Tavily now
requires a key. The legacy `ddg_search` / `ddg_deep_search` contracts remain.

Mojeek and Startpage failed initial LAB probes and are not enabled.
You.com keyless MCP passed follow-up discovery and search using SDK v2.
