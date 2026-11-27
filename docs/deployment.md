# Deployment & Operations

Two supported modes:

- **stdio** (default): the server is a subprocess of the MCP client
  (desktop apps, CLI launchers). No network, no token needed.
- **streamable HTTP**: the server is a long-running service on the network.
  Requires a bearer token; protected by Host/Origin validation and the
  SSRF fetch defenses.

## Docker

```bash
docker build -t mcp-prism-search:local .
```

The image:

- installs dependencies from `requirements-lock.txt` (reproducible builds)
- runs as non-root uid `10001:10001`
- declares a `HEALTHCHECK` against the configured `MCP_PORT`
- defaults to `MCP_TRANSPORT=stdio` and `MCP_CACHE_DIR=/data/cache`

## docker-compose (HTTP mode)

```bash
mkdir -p ~/docker/docker-data/mcp-prism-search
sudo chown -R 10001:10001 ~/docker/docker-data/mcp-prism-search   # container uid
MCP_AUTH_TOKEN=$(openssl rand -hex 32) docker compose up -d
```

The checked-in compose file:

- publishes `127.0.0.1:49317 -> 8000` (loopback only; edit deliberately if
  you want LAN exposure)
- requires `MCP_AUTH_TOKEN` via `${MCP_AUTH_TOKEN:?...}` — the server also
  refuses to boot on known placeholder tokens
- mounts the cache volume at `/data`
- enables startup and hourly cache pruning

Health and smoke checks:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:49317/health
# 200 = alive (no token needed)

curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:49317/mcp
# 401 = auth middleware active

curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Authorization: Bearer <token>' \
  -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:49317/mcp
# 400/406/200 depending on protocol headers — anything but 401 works
```

## Updating

```bash
git pull
MCP_AUTH_TOKEN=$(openssl rand -hex 32) docker compose up -d --build
```

`requirements-lock.txt` keeps builds reproducible across updates. The
release workflow builds and publishes images on every push to `main` and
creates a GitHub release/tag from conventional commits.

## Exposing beyond loopback

The compose port mapping is loopback-only by default. For LAN, VPN,
reverse proxy, or Cloudflare Tunnel deployments:

- set a strong `MCP_AUTH_TOKEN` (the server rejects placeholders at boot)
- set explicit `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` values (a bare
  `*` disables the SDK DNS-rebinding checks — documented opt-out)
- publish the port on the interface you actually need
- keep the container behind TLS termination where possible

Full worked examples (LAN, HTTPS reverse proxy, Cloudflare Tunnel) live in
the repository README.

## Environment variables (reference)

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` (streamable HTTP) |
| `MCP_HOST` | `127.0.0.1` | HTTP bind host |
| `MCP_PORT` | `8000` | HTTP port (validated at startup) |
| `MCP_AUTH_TOKEN` | unset | Bearer token; required in HTTP mode |
| `MCP_ALLOW_UNAUTHENTICATED_HTTP` | `false` | Explicit opt-out from the token requirement |
| `MCP_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host allowlist; `*` disables rebinding checks |
| `MCP_ALLOWED_ORIGINS` | `http://localhost:5173` | Origin allowlist; `*` disables rebinding checks |
| `MCP_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `MCP_CACHE_DIR` | `/data/cache` (image) | Cache root directory |
| `SEARCH_PROVIDER` | `duckduckgo_html` | `duckduckgo_html`, `ddgs`, or `auto` |
| `DDGS_BACKEND` | `duckduckgo` | ddgs backend selection (`auto` allowed) |
| `DDG_TIMEOUT_SECONDS` | `15` | Search timeout |
| `FETCH_TIMEOUT_SECONDS` | `15` | Fetch timeout |
| `FETCH_MAX_BYTES` | `5242880` | Hard cap on downloaded response body |
| `MAX_CONCURRENCY` | `5` | Default deep-search fetch concurrency (cap 12) |
| `DDG_CACHE_TTL_SECONDS` | `21600` | Search cache TTL |
| `FETCH_CACHE_TTL_SECONDS` | `7200` | Fetch cache TTL |
| `CACHE_PRUNE_ON_START` | `true` | Prune once at startup |
| `CACHE_PRUNE_INTERVAL_SECONDS` | `3600` | Background prune interval (min 60) |
| `CACHE_MAX_AGE_SECONDS` | `604800` | Max cache file age |
| `CACHE_MAX_SIZE_MB` | `512` | Max total cache size |

Fail-fast startup checks (HTTP mode): missing token (unless explicitly
allowed), known placeholder tokens, invalid `MCP_PORT`, invalid
`MCP_TRANSPORT`, invalid `MCP_LOG_LEVEL`.

## Rollback

Configuration changes: edit `.env`/compose and `docker compose up -d`
again. Code/image changes: re-pull the previous image tag or re-deploy the
previous commit. The cache volume is disposable by design — deleting it
never loses user data.


## Docker stdio mode

## Docker Stdio Usage

Build the local image:

```bash
docker build -t mcp-prism-search:local .
```

Run the server over stdio. This mode is auth-free because the MCP client owns
stdin/stdout and there is no listening network socket:

```bash
docker run --rm -i -v "$PWD/data:/data" mcp-prism-search:local
```

## Docker Stdio MCP Client Configuration

```json
{
  "mcpServers": {
    "ddg-research": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-v",
        "/opt/mcp-prism-search/data:/data",
        "mcp-prism-search:local"
      ]
    }
  }
}
```

## docker-compose Usage

> Operation, health checks, and hardening reference: [docs/deployment.md](docs/deployment.md).

The included compose file starts the server in streamable HTTP mode on `/mcp`.
It maps host port `49317` to container port `8000` and requires
`Authorization: Bearer change-me-now` by default.

Build and start the service:

```bash
docker compose up --build ddg-research
```

The compose file persists cache data at:

```text
~/docker/docker-data/mcp-prism-search/cache
```

It also enables conservative cache pruning defaults:

```yaml
CACHE_PRUNE_ON_START: "true"
CACHE_PRUNE_INTERVAL_SECONDS: "3600"
CACHE_MAX_AGE_SECONDS: "604800"
CACHE_MAX_SIZE_MB: "512"
```

The compose file keeps the legacy DuckDuckGo tools DuckDuckGo-only by default.
The aggregated `web_search` uses [provider policy](providers.md) independently:

```yaml
SEARCH_PROVIDER: duckduckgo_html
DDGS_BACKEND: duckduckgo
```

The compose file requires a real `MCP_AUTH_TOKEN` (it uses `${MCP_AUTH_TOKEN:?}`
and the server refuses to start with the known placeholder `change-me-now`).
Generate one with `openssl rand -hex 32` before starting the stack. The bind
mount used for `/data` must be writable by the container uid 10001:
`sudo chown -R 10001:10001 ~/docker/docker-data/mcp-prism-search`.

The server defaults `MCP_ALLOWED_HOSTS=*` and `MCP_ALLOWED_ORIGINS=*` so the
same container can run behind a LAN IP, hostname, domain, reverse proxy, or
HTTPS endpoint. The compose file documents these optional variables as commented
examples. The MCP SDK (2.x) Host/Origin allowlist does not support bare wildcards,
so wildcard mode disables the SDK rebinding checks and relies on the bearer
token. Prefer exact comma-separated values and keep the default loopback-only
port mapping. To enable strict Host/Origin checks,
uncomment the variables in `docker-compose.yml` and set exact comma-separated
values such as:

```bash
MCP_ALLOWED_HOSTS="example.com,example.com:443,localhost:49317"
MCP_ALLOWED_ORIGINS="https://example.com,http://localhost:*"
```

### LAN HTTP Example

Set a real token in `docker-compose.yml` and start the server:

```bash
docker compose up -d --build
```

Use your server's LAN IP in the client URL:

```text
http://YOUR_SERVER_IP:49317/mcp
```

OpenCode remote MCP configuration for a LAN deployment:

```json
{
  "mcp": {
    "ddg-research": {
      "type": "remote",
      "enabled": true,
      "url": "http://YOUR_SERVER_IP:49317/mcp",
      "oauth": false,
      "headers": {
        "Authorization": "Bearer change-me-now"
      }
    }
  }
}
```

### HTTPS Reverse Proxy Example

Run the container on the server and terminate TLS in a reverse proxy. The proxy
should forward `/mcp` to `http://127.0.0.1:49317/mcp` and preserve standard
upgrade/streaming behavior.

Minimal Nginx-style location:

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:49317/mcp;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
}
```

OpenCode configuration for the HTTPS endpoint:

```json
{
  "mcp": {
    "ddg-research": {
      "type": "remote",
      "enabled": true,
      "url": "https://your-domain.example/mcp",
      "oauth": false,
      "headers": {
        "Authorization": "Bearer change-me-now"
      }
    }
  }
}
```

### Cloudflare Tunnel Example

Cloudflare Tunnel lets `cloudflared` make outbound-only connections from your
server to Cloudflare, so you can publish the MCP HTTP endpoint without opening
an inbound router/firewall port.

In the Cloudflare dashboard, create a tunnel and add a public hostname such as:

```text
https://mcp.example.com
```

If `cloudflared` runs on the host, set the tunnel service URL to:

```text
http://127.0.0.1:49317
```

If `cloudflared` runs as another service in the same compose project/network,
set the tunnel service URL to the container service name and internal port:

```text
http://ddg-research:8000
```

Minimal compose service example for token-managed tunnels:

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  restart: unless-stopped
  command: tunnel --no-autoupdate run --token ${CLOUDFLARE_TUNNEL_TOKEN}
  depends_on:
    - ddg-research
```

Keep `CLOUDFLARE_TUNNEL_TOKEN` outside version control. In OpenCode, use the
public HTTPS URL and keep the MCP bearer token header:

```json
{
  "mcp": {
    "ddg-research": {
      "type": "remote",
      "enabled": true,
      "url": "https://mcp.example.com/mcp",
      "oauth": false,
      "headers": {
        "Authorization": "Bearer change-me-now"
      }
    }
  }
}
```

For production, replace `change-me-now` with a long random token. Cloudflare
Tunnel protects the network path, but the MCP server should still require its
own bearer token.

Do not expose HTTP mode to an untrusted network without HTTPS and a strong
`MCP_AUTH_TOKEN`. If `MCP_AUTH_TOKEN` is unset in HTTP mode, the server logs a
warning and accepts unauthenticated HTTP requests.

For MCP stdio clients, direct `docker run -i` is usually simpler than compose because the client owns stdin/stdout.

## HTTP Smoke Tests

Raw `curl` is useful for checking HTTP authentication and Host handling, but it
does not perform a complete MCP streamable HTTP session. A request with the
correct bearer token may therefore return `406 Not Acceptable` because curl did
not send the MCP client's expected `Accept: text/event-stream` negotiation
headers. That still proves the request passed bearer-token auth and Host
validation.

With the compose server running and the default compose token:

```bash
curl -i http://127.0.0.1:49317/mcp
```

Expected: `401 Unauthorized`.

```bash
curl -i \
  -H "Host: YOUR_SERVER_IP:49317" \
  -H "Authorization: Bearer change-me-now" \
  http://127.0.0.1:49317/mcp
```

Expected: usually `406 Not Acceptable` from raw curl, but not `401 Unauthorized`
and not `421 Misdirected Request`.

With a real MCP client, such as OpenCode configured with the same URL and
Authorization header, `ListTools` and `CallTool` should work for `ddg_search`,
`web_fetch`, `ddg_deep_search`, `cache_stats`, `cache_prune`, and
`cache_clear`.

## Tool Response Examples

All tools publish typed `outputSchema` and return `structuredContent`
(validated by the client). Provider failures surface as MCP `isError` results
(`ToolError`), not as success payloads with an error field.

`ddg_search` returns:

```json
{
  "query": "example",
  "provider": "duckduckgo_html",
  "results": [
    {"title": "Example Domain", "url": "https://example.com/", "snippet": "This domain is for use in examples."}
  ],
  "cached": false,
  "error": null
}
```

`web_fetch` returns:

```json
{
  "url": "https://example.com/",
  "final_url": "https://example.com/",
  "title": "Example Domain",
  "content": "This domain is for use in illustrative examples...",
  "content_type": "text/html",
  "cached": false,
  "success": true,
  "error": null
}
```

`ddg_deep_search` returns sources, fetched pages, failed pages, a
`search_error` field for total provider failures, and `cached`.

`cache_stats`, `cache_prune`, and `cache_clear` return per-namespace file
counts and byte totals (`deleted_files`, `remaining_files`, and so on).

## Cache behavior (detailed)

## Cache Behavior

Search results are cached under the `search` cache namespace. Fetch responses are cached under the `fetch` cache namespace. Cache keys are SHA256 hashes of stable JSON payloads, so equivalent tool arguments map to the same file path.

Cache files are written atomically by writing a temporary file in the target cache directory and then renaming it into place. Corrupt, malformed, or expired cache files are ignored safely.

TTL checks on read prevent stale cache values from being returned, but TTL on
read alone does not remove old files from disk. Long-running deployments can
therefore accumulate expired cache files unless pruning deletes them. This
server prunes cache files on startup when `CACHE_PRUNE_ON_START=true` and also
opportunistically during cache access/write, throttled by
`CACHE_PRUNE_INTERVAL_SECONDS`.

Pruning covers both known cache namespaces:

- `search`
- `fetch`

Pruning deletes:

- corrupt or malformed `.json` cache files
- leftover `.tmp` files from interrupted atomic writes
- files expired by `DDG_CACHE_TTL_SECONDS` or `FETCH_CACHE_TTL_SECONDS`
- files older than `CACHE_MAX_AGE_SECONDS`, when set
- oldest cache files first when total cache size exceeds `CACHE_MAX_SIZE_MB`

If `CACHE_MAX_AGE_SECONDS` is unset, invalid, or `0`, max-age pruning is
disabled. If `CACHE_MAX_SIZE_MB` is unset, invalid, or `0`, size-based pruning
is disabled. Pruning errors are logged and do not fail search or fetch requests.

Manual cache tools are available:

- `cache_stats`: inspect current file counts and byte totals.
- `cache_prune`: run pruning manually, with optional `dry_run`.
- `cache_clear`: clear `search`, `fetch`, or `all` after `confirm=true`.

The default compose configuration persists cache files in `/data/cache`, with
`~/docker/docker-data/mcp-prism-search` mounted into the container.

## Rate Limit Notes

## Rate-limit notes

## Rate Limit Notes

Defaults are intentionally conservative:

- `ddg_search` defaults to 10 results and caps at 30.
- `ddg_deep_search` defaults to 5 fetched pages and caps at 10.
- Deep search concurrency defaults to 5.
- Search and fetch results are cached to reduce repeated DuckDuckGo and website hits.
- Legacy DuckDuckGo search defaults to DuckDuckGo HTML only. `DDGS_BACKEND=auto` is broader
  metasearch and can generate requests to additional upstream services.

This project does not rotate proxies, bypass captchas, or attempt to evade rate limits. If DuckDuckGo blocks or rate limits requests, the tool returns structured errors instead of retrying aggressively.

## Environment variables (detailed reference)

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `MCP_CACHE_DIR` | `/data/cache` | Directory for JSON cache files. |
| `SEARCH_PROVIDER` | `duckduckgo_html` | Search provider mode: `duckduckgo_html`, `ddgs`, or `auto`. Invalid values safely fall back to `duckduckgo_html`. |
| `DDGS_BACKEND` | `duckduckgo` | Backend passed to `DDGS().text(...)` when `SEARCH_PROVIDER=ddgs` or `SEARCH_PROVIDER=auto`. `duckduckgo` keeps `ddgs` DuckDuckGo-focused when supported. `auto` is broader metasearch mode and may query multiple upstream providers. Invalid values safely fall back to `duckduckgo`. |
| `DDG_CACHE_TTL_SECONDS` | `21600` | Search cache TTL in seconds. |
| `FETCH_CACHE_TTL_SECONDS` | `7200` | Web fetch cache TTL in seconds. |
| `DDG_TIMEOUT_SECONDS` | `15` | DuckDuckGo HTML and optional `ddgs` provider timeout in seconds. |
| `FETCH_TIMEOUT_SECONDS` | `15` | Web fetch timeout in seconds. |
| `MAX_CONCURRENCY` | `5` | Default deep search page fetch concurrency limit when `max_concurrency` is omitted. Runtime caps this at `12`. |
| `MCP_TRANSPORT` | `stdio` | MCP transport. `stdio` is the default. `http` uses streamable HTTP when supported by the installed SDK. |
| `MCP_HOST` | `0.0.0.0` | Host used for optional streamable HTTP mode. |
| `MCP_PORT` | `8000` | Port used for optional streamable HTTP mode. |
| `MCP_LOG_LEVEL` | `INFO` | Server log level (DEBUG, INFO, WARNING, ERROR, CRITICAL). |
| `FETCH_MAX_BYTES` | `5242880` | Hard cap on downloaded response body size before extraction. |
| `MCP_AUTH_TOKEN` | unset | Bearer token for HTTP mode. HTTP mode refuses to start without it (unless `MCP_ALLOW_UNAUTHENTICATED_HTTP=1`) and rejects known placeholder values. |
| `MCP_ALLOWED_HOSTS` | `*` | Comma-separated Host allowlist for HTTP mode. `*` supports arbitrary deployment hosts by disabling SDK Host/Origin rebinding checks; prefer exact values. |
| `MCP_ALLOWED_ORIGINS` | `*` | Comma-separated Origin allowlist for HTTP mode. `*` supports arbitrary origins by disabling SDK Host/Origin rebinding checks; prefer exact values. |
| `CACHE_PRUNE_ON_START` | `true` | Run cache pruning once when the MCP server starts. |
| `CACHE_PRUNE_INTERVAL_SECONDS` | `3600` | Minimum interval between opportunistic runtime prune attempts triggered by cache access/write. |
| `CACHE_MAX_AGE_SECONDS` | unset | Optional maximum cache file age in seconds. If unset, invalid, or `0`, max-age pruning is disabled. The compose file sets `604800` (7 days). |
| `CACHE_MAX_SIZE_MB` | unset | Optional total cache size limit in MiB. If unset, invalid, or `0`, size-based pruning is disabled. The compose file sets `512`. |
