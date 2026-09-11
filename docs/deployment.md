# Deployment & Operations

Two supported modes:

- **stdio** (default): the server is a subprocess of the MCP client
  (desktop apps, CLI launchers). No network, no token needed.
- **streamable HTTP**: the server is a long-running service on the network.
  Requires a bearer token; protected by Host/Origin validation and the
  SSRF fetch defenses.

## Docker

```bash
docker build -t mcp-ddg-research:local .
```

The image:

- installs dependencies from `requirements-lock.txt` (reproducible builds)
- runs as non-root uid `10001:10001`
- declares a `HEALTHCHECK` against the configured `MCP_PORT`
- defaults to `MCP_TRANSPORT=stdio` and `MCP_CACHE_DIR=/data/cache`

## docker-compose (HTTP mode)

```bash
mkdir -p ~/docker/docker-data/mcp-ddg-research
sudo chown -R 10001:10001 ~/docker/docker-data/mcp-ddg-research   # container uid
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
