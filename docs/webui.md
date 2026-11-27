# Optional Web Dashboard

The dashboard is a static Astro application with canonical YuTheme tokens
from revision `49bf3d2`. Dark uses the canonical charcoal/teal palette; White is
optional. No React, CDN, external fonts, Node.js production server, Redis,
or external database is required.

## Enable with Docker Compose

Keep your existing private MCP bearer token in the protected environment
file used by Compose. Add a strong, unique dashboard password (12–1024
characters) as `MCP_WEBUI_PASSWORD`. Do not commit the file, put the
password in tracked Compose YAML, or reuse the MCP token.

```bash
docker compose --env-file "$HOME/.config/mcp-prism-search/server.env" \
  -f docker-compose.yml -f docker-compose.webui.yml up -d --build
```

The optional overlay enables the dashboard and publishes
`127.0.0.1:49318`. The normal Compose file does not publish that port.
The MCP endpoint remains `http://127.0.0.1:49317/mcp`.
The dashboard is independent of MCP authentication.

To disable it, start the stack again using only the base Compose file.
The dashboard data is retained but not loaded in headless mode; credentials
and options saved through the dashboard therefore require enabling the
dashboard again. Environment-configured providers remain independent.

## Local development (HTTP transport)

Build the UI from the checked-in lockfile:

```bash
cd webui
npm ci
npm run build
cd ..
```

Use Node 24 LTS for the build. Astro 6.4.8 is pinned because the tested
Astro 7.4.13 native compiler fails with SIGILL on the older x86 LAB CPU.
This restriction only affects building assets, not the Python runtime.
Run the installed Python server in HTTP mode with the two panel variables
and the usual MCP authentication settings. For example, use a private
environment file loaded by your service manager; never use a literal
example password or print credentials.

| Variable | Default | Purpose |
|---|---|---|
| MCP_WEBUI_ENABLED | false | Enable the separate administrative listener (HTTP MCP only) |
| MCP_WEBUI_PASSWORD | unset | Required private admin password, minimum 12 characters |
| MCP_WEBUI_HOST | 127.0.0.1 | Local administrative bind address |
| MCP_WEBUI_PORT | 49318 | Administrative port |
| MCP_WEBUI_ALLOWED_HOSTS | localhost,127.0.0.1 | Exact allowed hostnames; no wildcard |
| MCP_WEBUI_DATA_DIR | ~/.local/share/prism-admin | Private data directory, mode 0700 |
| MCP_WEBUI_ASSETS | webui/dist in source checkout | Static assets directory |
| MCP_PREFERRED_PROVIDER | unset | Optional tie-break preference in auto routing |

The Docker image sets the assets path to /app/webui and the state directory
to /data/admin. Both MCP and the admin listener run in the same Python
process. Enabling the dashboard with stdio is rejected explicitly; use
HTTP if you need live management. When disabled the panel module is not
loaded and no panel database or listener is created.

## Features

- Overview: configured/available providers, aggregate upstream request
  counts, successes, empty results, errors, rate limits, average latency
  and aggregate search cache hits.
- Providers: enable/disable, add, replace or remove a stored API key.
  Environment credentials are read-only and take precedence. SearXNG URL
  configuration is intentionally environment-only.
- Playground: run an explicit selection of available general engines
  and inspect result URLs, snippets and attempt outcomes. Authenticated
  providers may consume credits; no automatic paid-provider testing.
- Cache: inspect current namespaces, prune expired/corrupt files or clear
  after confirmation.
- Settings: provider timeout, search/fetch cache TTLs, preferred engine
  (used to break health ties). Environment values override the persisted
  settings. Legacy DuckDuckGo search retains its separate provider mode.
- Connection: generate a remote client example with a token placeholder;
  the dashboard never exposes the MCP token.

Provider availability means configured/enabled, not a successful upstream
health check. Use the playground to test a provider. Metrics count actual
upstream attempts, not HTTP calls made internally by paginated providers,
and are collected only while the dashboard is enabled. Query text is not
stored in the metrics database; the existing search cache and upstream
HTTP logs can still contain query text.

## Security and persistence

The dashboard is a separate ASGI application and port. MCP tokens cannot
authorize administrative routes, and the panel session cannot authorize
the MCP endpoint. There are no default credentials, public signup or
administrative MCP tools.

- One password configured via environment; no generated password file.
- Eight-hour random, server-side sessions; logout or process restart
  invalidates sessions. Cookies are HttpOnly and SameSite=Strict, Secure
  when using HTTPS.
- Same-origin verification and a custom request header for mutations.
- Simple global login throttling: 10 failed attempts per minute.
- Explicit host allowlist, CSP, frame protection, no cross-origin API.
- API keys are write-only in the UI and encrypted using authenticated
  Fernet encryption. Only configured/not configured is returned.
- State in SQLite under the private state directory; encryption key
  automatically generated in a separate 0600 file. No plaintext
  provider credential is stored in the database. Protect the state
  directory and key together; encryption does not protect against an
  attacker with access to both files or the running process.

Back up the entire private state directory together (preferably while the
server is stopped or using a consistent SQLite backup). Do not commit it.
Rotate provider keys if the host is compromised. Keep the private
environment file protected as well. Changing the admin password requires
restarting the process; existing sessions are then invalid.

## Remote access

The simplest option is an authenticated SSH tunnel to the loopback panel.
For a remote hostname, terminate TLS at a trusted reverse proxy, add the
exact hostname to MCP_WEBUI_ALLOWED_HOSTS and ensure the backend sees the
trusted HTTPS scheme. Configure Uvicorn trusted forwarded IPs narrowly
using FORWARDED_ALLOW_IPS if needed; never trust arbitrary proxies.
Non-local hostnames over plain HTTP are rejected. Do not publish the panel
directly to the internet. The MCP listener retains its own separate
Host/Origin allowlists and bearer authentication.

## Build and tests

The Docker multi-stage build copies only the static assets into the
Python image. No frontend build tools are present at runtime. Run the
offline Python suite, frontend build and JavaScript syntax check:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
cd webui
npm ci
npm run build
node --check public/app.js
```

See [provider policy](providers.md) and [deployment](deployment.md).

### Optional animated background

Settings → Appearance → Starfield Depth enables the canonical YuTheme Canvas effect (off by default). It is available only in Dark, remembers the browser-local preference, pauses by disposing its canvas while the tab is hidden, and renders a static frame for reduced-motion users. White mode suspends it without resetting the preference. No server process or package dependency is added.
