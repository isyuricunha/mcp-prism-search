# Server defaults

Prism Search owns its configuration independently of the optional dashboard.
Configure once, and omit parameters from tool calls to inherit preferences.

```json
{"query":"Model Context Protocol Streamable HTTP documentation"}
```

## Precedence

1. Explicit tool arguments override preferences where applicable.
2. Nonempty environment values override persisted settings.
3. Persisted settings override built-in defaults.
4. Built-in defaults apply otherwise.

Omitted or null optional arguments inherit preferences. Explicit provider
selection never enables a disabled or unconfigured engine. Explicit empty
domain lists clear domain preferences; `time_filter: "any"` clears freshness.
Domain filtering is applied locally, while recency and SafeSearch depend
on upstream support. Preferences are not security policies.

## Configuration

Settings → Server defaults in the optional WebUI renders the configuration
schema supplied by the MCP. Environment-managed values are locked. The
same preferences work in headless stdio and HTTP deployments.

| Preference | Environment | Built-in |
|---|---|---|
| General routing | MCP_DEFAULT_SEARCH_MODE | auto |
| Default selected engines | MCP_DEFAULT_SEARCH_PROVIDERS | none |
| General/legacy results | MCP_DEFAULT_SEARCH_MAX_RESULTS | 10 |
| SafeSearch | MCP_DEFAULT_SEARCH_SAFE_SEARCH | off |
| Freshness | MCP_DEFAULT_SEARCH_RECENCY | any |
| Allowed domains | MCP_DEFAULT_ALLOWED_DOMAINS | none |
| Blocked domains | MCP_DEFAULT_BLOCKED_DOMAINS | none |
| Preferred domains | MCP_DEFAULT_PREFERRED_DOMAINS | none |
| Extracted text per page | MCP_DEFAULT_FETCH_MAX_CHARS | 12000 |
| Legacy deep-search pages | MCP_DEFAULT_DEEP_MAX_PAGES | 5 |
| Vertical source | MCP_DEFAULT_VERTICAL_SOURCE | wikipedia |
| Vertical results | MCP_DEFAULT_VERTICAL_MAX_RESULTS | 5 |
| Standalone answer provider | MCP_DEFAULT_ANSWER_PROVIDER | explicit |
| Standalone research provider | MCP_DEFAULT_RESEARCH_PROVIDER | explicit |
| Standalone research effort | MCP_DEFAULT_RESEARCH_EFFORT | lite |
| Fetch timeout (seconds) | FETCH_TIMEOUT_SECONDS | 15 |
| Legacy parallel page fetches | MAX_CONCURRENCY | 5 |
| News cache TTL cap (seconds) | MCP_NEWS_CACHE_TTL_SECONDS | 1800 |

List values are comma-separated; use `none` to clear a persisted list via
the environment. Blank values inherit. Defaults are validated at startup.
Selected mode requires a nonempty list of registered general engines;
unavailable engines are skipped without broadening the list. Explicit
provider lists remain strict.

- `auto`: health-ordered fallback; prioritize authorized, eligible AI
  engines when synthesis is enabled.
- `keyless`: fallback among general engines requiring no API key or
  instance URL.
- `selected`: search the configured default list in parallel. A list
  of one provides a single-provider default.
- `all`: parallel search across available general engines. Authenticated
  engines may consume credits.

Override routing with provider auto/keyless/all, one provider ID, or a
nonempty providers list. Existing tools and response contracts are retained.
Legacy ddg_search/ddg_deep_search keep their SEARCH_PROVIDER/DDGS_BACKEND
routing and inherit common search/extraction preferences.

DDG_TIMEOUT_SECONDS, DDG_CACHE_TTL_SECONDS, FETCH_CACHE_TTL_SECONDS,
MCP_PREFERRED_PROVIDER and cache-pruning settings remain supported.
The news TTL never extends the ordinary cache TTL. MCP_DISABLED_PROVIDERS
is a comma-separated list of registry IDs, overriding persisted enabled
flags; ddg_html also disables the legacy HTML path.

## AI by default

```dotenv
MCP_DEFAULT_SEARCH_MODE=auto
MCP_AI_SEARCH_ENABLED=true
MCP_AI_SEARCH_PROVIDERS=tavily,exa
```

This authorizes potential API credit consumption for configured, enabled
providers. Auto prioritizes eligible AI engines rather than reusing
retrieval-only cached winners. Keyless mode does not call authenticated
engines. Restrictive domain, freshness, SafeSearch or query-length
constraints can disable synthesis. If AI fails, preserve usable retrieval
results instead of retrying paid synthesis. Use include_answers=false to
opt out per call. Standalone web_answer and web_research still require
allow_cost=true even with configured defaults. See [native AI](native-ai.md).

## Persistent state and headless mode

MCP_STATE_DIR is the canonical directory override. MCP_WEBUI_DATA_DIR
remains a compatible alias; existing Docker deployments retain /data/admin.
The local default is ~/.local/share/prism-admin. Existing private state
loads on startup without WebUI, in HTTP and stdio. Fresh headless installs
do not create a SQLite database or encryption key. The optional dashboard
creates and uses the same state. Keep settings.sqlite3 and encryption.key
together, private and backed up; a missing encryption key for an existing
database fails startup rather than recreating credentials. Metrics are
recorded whenever persistent state is active.

The website is an optional management surface, not a separate configuration
source or a prerequisite for running the MCP.

Search queries are limited to 4,000 characters, domain preference lists to
50 entries each, and fetch URLs (including redirects) to 8,192 characters.
Internationalized domains are normalized to ASCII IDNA and malformed domain
preferences are rejected. Explicit search windows suppress native AI synthesis.

The MCP discovery schemas advertise the core request size limits. Legacy
deep search reports progress as individual pages finish and preserves
successful pages when another page fails. Cache maintenance skips entries
that have been replaced since its scan.
