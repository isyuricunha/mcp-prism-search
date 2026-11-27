# Architecture

`mcp-prism-search` is a Python 3.12 MCP server built on the official
`mcp` SDK (2.x, `MCPServer` API) with FastMCP-style tool decorators.
It exposes deterministic research primitives over stdio or streamable HTTP.
The agent/client reasons over the returned data; the server never calls models.

## Module map

| Module | Responsibility |
|---|---|
| `server.py` | MCPServer wiring, tool definitions, bearer auth middleware, HTTP settings, background prune loop, `/health` route |
| `search.py` | Multi-provider orchestration, cache, health-ordered fallback, parallel selection, ranking and legacy DuckDuckGo search |
| `providers/` | Provider registry and engine-specific API adapters |
| `fetch.py` | SSRF-checked webpage fetching with IP pinning, redirect chain validation, streamed byte cap, clean text extraction, deep search orchestration |
| `cache.py` | File-based TTL JSON cache (one file per key), atomic writes, background-capable pruning, stats and clear tools |
| `security.py` | URL shape validation, hostname/IP blocklists, DNS resolution checks, safe-address pinning helper |
| `domains.py` | Domain normalization, allow/block/preferred result controls |
| `models.py` | Pydantic request/response models (strict, `extra="forbid"`), shared `SearchRequestBase`, cache payload helper |
| `text.py` | Whitespace normalization, HTML text extraction with boilerplate removal, truncation |

## Request flow

```
MCP client
  │  (bearer token checked by ASGI middleware in HTTP mode)
  ▼
MCPServer tool call
  │
  ├─ ddg_search ──▶ search.py ──▶ DuckDuckGo HTML endpoint (or ddgs)
  │                      │
  │                      ▼
  │                JsonFileCache (search namespace, TTL)
  │
  ├─ web_fetch ──▶ fetch.py
  │                ├─ validate_url_shape (scheme, host, literal IP)
  │                ├─ validate_resolved_addresses (DNS → blocklist)
  │                ├─ resolve_safe_address (single validated IP)
  │                ├─ GET pinned to that IP (Host/SNI preserved)
  │                ├─ streamed body, hard byte cap (FETCH_MAX_BYTES)
  │                └─ per-hop re-validation on redirects (≤ 5)
  │                      │
  │                      ▼
  │                JsonFileCache (fetch namespace, TTL)
  │
  └─ ddg_deep_search ──▶ ddg_search + concurrent web_fetch per top source,
                         progress notifications per page
```

## Security model

Layered SSRF defenses in `security.py` + `fetch.py`:

1. **Scheme allowlist**: only `http` and `https`.
2. **Hostname blocklist**: `localhost`, `metadata`, `metadata.google.internal`,
   `*.local`, `*.localhost`, `*.internal`, `*.lan`, `*.intranet`.
3. **Literal IP blocklist**: loopback, RFC1918, CGNAT (100.64.0.0/10),
   link-local (169.254/16, cloud metadata), ULA, unspecified, multicast,
   reserved. IPv4-mapped IPv6 and encoded forms (decimal/octal/hex) are
   caught at the DNS-resolution layer.
4. **DNS pinning**: `resolve_safe_address` resolves once, validates every
   answer, and the HTTP connection is made to the validated IP directly
   (Host header and SNI preserved) — the classic validate-then-reresolve
   TOCTOU window is closed. IPv4 is preferred because many egress networks
   have no working IPv6 route.
5. **Redirect safety**: `follow_redirects=False`; every hop is re-validated
   (shape + DNS + pin), maximum 5 hops.
6. **Body cap**: responses are streamed and truncated at `FETCH_MAX_BYTES`
   (default 5 MiB) before extraction, bounding memory use.
7. **Auth (HTTP mode)**: bearer token required; the server refuses to start
   with a missing token (unless `MCP_ALLOW_UNAUTHENTICATED_HTTP=1`) or with
   known placeholder tokens. Token comparison is `hmac.compare_digest` over
   UTF-8 bytes; non-ASCII headers get a clean 401. Only `http` and `lifespan`
   scopes pass through — anything else fails closed.
8. **Transport security**: `TransportSecuritySettings` Host/Origin allowlist
   wired into the streamable HTTP app. Wildcard disables it and is therefore
   an explicit opt-in.

Known residual risk: a hostile origin can still serve oversized or malicious
*content* within the byte cap; the server treats fetched text as untrusted
data and returns it verbatim to the client.

## Aggregated search

See [provider policy](providers.md) for modes, provider selection, timeout,
cache and filtering semantics. Legacy DuckDuckGo behavior below is unchanged.

## Cache design

- One JSON file per key under `<cache_dir>/<namespace>/<sha256>.json`, where
  the key is the canonical JSON of the request payload — no path traversal.
- Writes are atomic: `tempfile` + `os.replace` (no partial files visible).
- Creation time is read from the file **mtime** (each file is written exactly
  once), so pruning never parses file contents.
- Pruning runs as a background worker thread in HTTP mode (interval:
  `CACHE_PRUNE_INTERVAL_SECONDS`) and once at startup (`CACHE_PRUNE_ON_START`).
  It removes expired, corrupt, temporary, and oversized files. It never runs
  inline with cache reads/writes, so the event loop is never blocked by it.
- A cache directory that cannot be created (permissions) degrades the cache
  to a logged no-op instead of failing tool calls.

## Release format

`translate`-style corpus releases are not part of this project; published
artifacts are the Docker images (`ghcr.io/isyuricunha/mcp-prism-search`)
built from `requirements-lock.txt` + source, plus GitHub releases generated
by semantic-release from conventional commits.

- `fix:` → patch, `feat:` → minor, `feat!:`/`BREAKING CHANGE:` → major.
- The release workflow runs ruff, pytest, a Docker build, and publishes the
  image and GitHub release/tag.


## Domain controls (tools behavior)

## Domain Controls

Domain controls are opt-in. If you do not pass `blocked_domains`,
`allowed_domains`, `preferred_domains`, or `search_window`, `ddg_search`
requests exactly `max_results` from DuckDuckGo and preserves DuckDuckGo's
default ranking order after URL deduplication. The server does not apply a
built-in source bias, source boost, or domain blocklist.

When any domain control is provided, the server requests a larger internal
search result window from the provider before applying dedupe and domain
controls. The default provider result window is:

```text
min(max_results * 3, 50)
```

The final response is still capped to `max_results`. You can override the
provider result window with `search_window`, minimum `1`, maximum `100`. This is
a count of search results requested from DuckDuckGo, not a recency or day range.
It is useful when a desired allowed/preferred domain might appear outside the
first `max_results` provider results.

Domain inputs are normalized by lowercasing, removing URL schemes, removing
paths and query strings, and stripping a leading `www.`. Matching supports exact
domains and subdomains. For example, `docs.example.com` matches `example.com`,
but `example.com.evil.com` does not.

Filtering order:

1. Apply `allowed_domains` if provided.
2. Apply `blocked_domains` if provided.
3. Apply `preferred_domains` if provided.

`preferred_domains` performs a stable partition: preferred matches move earlier,
relative order is preserved inside the preferred and non-preferred groups, and
no numeric score is invented.

Block domains:

```json
{
  "query": "self hosted photo backup",
  "blocked_domains": ["example.com", "old-docs.example.org"]
}
```

Allow only specific domains:

```json
{
  "query": "python mcp server",
  "allowed_domains": ["github.com", "modelcontextprotocol.io"]
}
```

Prefer domains without excluding others:

```json
{
  "query": "duckduckgo html search endpoint",
  "preferred_domains": ["duckduckgo.com", "github.com"]
}
```

Search a larger internal search result window before applying domain controls:

```json
{
  "query": "python mcp server",
  "max_results": 10,
  "search_window": 40,
  "allowed_domains": ["github.com", "modelcontextprotocol.io"]
}
```

Deep search with the same provider result window behavior:

```json
{
  "query": "model context protocol python sdk",
  "max_results": 10,
  "max_pages": 5,
  "search_window": 40,
  "preferred_domains": ["github.com"]
}
```

Limit deep-search fetch concurrency for one call:

```json
{
  "query": "model context protocol python sdk",
  "max_pages": 5,
  "max_concurrency": 2
}
```


## SSRF blocklists (reference)

## SSRF and Security Protections

> Full layered defense model: [docs/architecture.md](docs/architecture.md#security-model).

`web_fetch` only allows `http` and `https` URLs. It blocks known local or internal hostnames, including:

- `localhost`
- `metadata`
- `metadata.google.internal`
- hostnames ending in `.local`, `.localhost`, `.internal`, `.lan`, `.intranet`

It also rejects IP addresses in private, loopback, link-local, reserved, multicast, or unspecified ranges, including:

- `0.0.0.0/8`
- `10.0.0.0/8`
- `127.0.0.0/8`
- `169.254.0.0/16`
- `172.16.0.0/12`
- `192.168.0.0/16`
- `::1/128`
- `fc00::/7`
- `fe80::/10`

DNS is resolved before fetching. If any resolved address is unsafe, the request is rejected. Redirects are followed manually, and every redirect target is validated before the next request.

Unsupported schemes such as `file://`, `ftp://`, `ssh://`, `gopher://`, and `data:` are never fetched.


## Optional dashboard

The independent admin ASGI listener uses the same process and search registry,
but a separate password/session authentication boundary. Static Astro assets
are served directly from disk. SQLite stores encrypted keys, provider flags,
settings and bounded aggregate metrics. Headless mode does not import the panel
or initialize state. See [dashboard design and operations](webui.md).
