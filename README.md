# mcp-prism-search

MCP search aggregator: one query, refracted across many keyless search engines.

`mcp-prism-search` is a self-hosted Python MCP server (mcp SDK 2.x) that
aggregates web search from multiple keyless engines — Bing, DuckDuckGo
(HTML + lite), ddgs backends, FreeSerp, Keenable, Mwmbl, 4get, self-hosted
SearXNG — with automatic fallback chains, deterministic ranking, TTL caching,
SSRF-safe webpage fetching, and keyless vertical sources (Wikipedia,
HackerNews). Keyed providers with free tiers (Tavily, Exa, Brave, You.com,
Serper, Jina) plug in via env vars and are skipped when absent.

The client or agent reasons over the returned data; the server never calls
LLMs, summarizes, or generates reports.

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Module map, request flow, layered SSRF/security model, domain controls, cache design |
| [docs/development.md](docs/development.md) | Dev setup, test suite layout, commands, conventions, semantic-release, dependency policy |
| [docs/deployment.md](docs/deployment.md) | Docker and docker-compose operation, HTTP hardening, health checks, LAN/proxy/tunnel examples, full env var reference |

## Tools

| Tool | Description |
|---|---|
| `web_search` | Aggregated web search. `provider="auto"` walks the healthy fallback chain; `provider="all"` fans out to every provider in parallel and merges results; or pin one provider by name. |
| `web_fetch` | SSRF-safe webpage fetch with IP pinning, redirect validation, streamed byte cap, and clean text extraction. |
| `ddg_deep_search` | Search once, fetch the top pages concurrently, return raw page text with progress notifications. |
| `vertical_search` | Search keyless vertical sources: `wikipedia`, `hackernews`. |
| `ddg_search` | Legacy single-provider DuckDuckGo search (kept for compatibility). |
| `cache_stats` / `cache_prune` / `cache_clear` | Cache inspection, pruning, and clearing. |

All tools publish typed `outputSchema` and return validated
`structuredContent`. Provider failures surface as MCP `isError` results
(`ToolError`), with a `provider_attempts` tally for diagnostics.

## Providers

**Keyless (no configuration needed):**

| Provider | What it is |
|---|---|
| `bing_html` | Bing SERP scraping — the most permissive engine from datacenter IPs |
| `ddg_html` / `ddg_lite` | DuckDuckGo HTML and lite endpoints (IP-reputation dependent) |
| `ddgs:bing` / `ddgs:yahoo` / `ddgs:brave` / `ddgs:yandex` | ddgs library multi-backend |
| `freeserp` | FreeSerp own-index SERP (keyless JSON API) |
| `keenable` | Keenable web search with extracted text |
| `mwmbl` | Mwmbl community index (keyless, AGPL) |
| `wikipedia` / `hackernews` | Keyless vertical sources |

**Keyed (free tiers verified against official docs):**

| Provider | Free tier | Env var |
|---|---|---|
| `tavily` | 1,000 credits/month, or keyless mode | `TAVILY_API_KEY` (optional — works keyless) |
| `exa` | ~1,430 searches/month (no card) | `EXA_API_KEY` |
| `brave` | ~1,000 searches/month (card required at signup, prepay $0) | `BRAVE_API_KEY` |
| `youcom` | $100 one-time credit | `YDC_API_KEY` |
| `serper` | 2,500 Google SERP queries one-time | `SERPER_API_KEY` |
| `jina` | Search with full page content (10M token grant) | `JINA_API_KEY` |
| `searxng` | Unlimited (self-hosted) | `SEARXNG_URL` |

Providers whose key is missing are skipped automatically; only the keyless
ones run.

## Quick start

stdio (desktop MCP clients — no network, no token):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m mcp_prism_search.server
```

Streamable HTTP (Docker compose — loopback-only by default):

```bash
MCP_AUTH_TOKEN=$(openssl rand -hex 32) docker compose up -d --build
curl -s http://127.0.0.1:49317/health   # -> {"ok": true}
```

Then call it from any MCP client at `http://127.0.0.1:49317/mcp` with
`Authorization: Bearer <token>`.

Search across every keyless engine at once:

```json
{"query": "model context protocol", "provider": "all", "max_results": 15}
```

## Security

Layered SSRF defenses on every fetch: scheme allowlist, hostname and literal
IP blocklists (private, loopback, CGNAT, link-local, metadata), DNS
resolution checks with IP pinning (the validate-then-connect TOCTOU window is
closed), per-hop redirect re-validation with a 5-hop cap, streamed bodies
hard-capped at `FETCH_MAX_BYTES`, and `Accept-Encoding: identity` enforcement.
HTTP mode requires a bearer token and rejects known placeholder tokens at
startup. Full model: [docs/architecture.md](docs/architecture.md#security-model).

The server does not rotate proxies, bypass captchas, or attempt to evade
rate limits: upstream blocks surface as structured errors instead.

## Development

Python 3.12+, `pytest` (offline, 100+ tests), `ruff`. Conventional commits
drive semantic-release (automatic versioning, tags, releases, and
multi-arch Docker images).

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
```

Details: [docs/development.md](docs/development.md).

## License

AGPL-3.0 — see [LICENSE](LICENSE).