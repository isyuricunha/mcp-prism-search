# Search provider policy

## Modes

- `web_search(query=...)`: inherit [server defaults](defaults.md).
- `web_search(provider="auto")`: choose the first provider with usable results;
  authorized AI engines are prioritized when eligible. No synthesis retries.
- `web_search(provider="keyless")`: automatic fallback restricted to keyless engines.
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
- [TinyFish TinySearch](https://docs.tinyfish.ai/tinysearch/reference): official
  GET search API with domain and recency filters. Set `TINYFISH_API_KEY` in
  the server environment (also forwarded by Compose). Pages 0–10 are fetched
  only as needed; each page counts as a request. SafeSearch is not exposed by
  the current API. The [pricing page](https://www.tinyfish.ai/pricing) lists
  30 requests/min and 500/hour; the API reference lists 12,000/day and
  possible Wallet spending beyond the allowance when enabled. Keep Wallet
  spending disabled if you need free-only operation. No automatic retries.
- [Wikimedia](https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy):
  identifiable User-Agent with a project URL.

TinyFish, Tavily, Exa, Brave, You.com, Serper, Jina and SearXNG are enabled only when
their corresponding environment variable is configured (or, for API keys,
when stored in the optional [dashboard](webui.md)). Configured API
providers can consume credits in auto or all mode; use explicit selection
when controlling spend. No quota evasion or proxy rotation is performed. The dashboard manages persisted provider flags and preferences; the MCP
loads them even without WebUI. MCP_DISABLED_PROVIDERS forces registry IDs
off. Environment settings take precedence. Public provider metadata comes
from the server registry.

## Migration notes

Removed registry names: `bing_html`, `ddg_lite`, `ddgs:bing`,
`ddgs:yandex`, `ddgs:brave`. Removed unused modules: 4get and Yandex TR.
Use `bing_rss`, `ddg_html` or `ddgs:yahoo` where appropriate. Tavily now
requires a key. The legacy `ddg_search` / `ddg_deep_search` contracts remain.

Mojeek and Startpage failed initial LAB probes and are not enabled.
You.com keyless MCP passed follow-up discovery and search using SDK v2.

## Native synthesis

The [native AI tools and AI-enhanced search policy](native-ai.md) support Tavily, Exa, You.com, Brave, Jina and explicit TinyFish Answer/Research. Ordinary search remains retrieval-only by default. Administrator opt-in permits native AI alongside search; standalone synthesis requires explicit per-call consent.

Automatic routing uses the last 32 observed outcomes per provider for
health ordering; aggregate counters still retain lifetime counts. This
allows recovered engines to regain priority after successful searches.

Providers requiring a key or instance URL are effectively disabled until
configured. The panel derives activation from the same registry used by
MCP routing. Paste a key, enable the switch, and save; saving a key with
the switch off leaves the provider disabled. Removing a stored key also
persists the disabled flag. Environment locks remain authoritative.
