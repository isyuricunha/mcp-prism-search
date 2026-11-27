# Development

## Requirements

- Python **3.12+** (the code uses PEP 695 generic syntax and ruff targets `py312`)
- Git

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Windows:

```text
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e '.[dev]'
```

The `[dev]` extra installs `pytest` and `ruff`.

## Commands

```bash
# tests (offline; no network needed)
.venv/bin/python -m pytest -q

# lint
.venv/bin/python -m ruff check src tests

# run the server locally over stdio
.venv/bin/python -m mcp_prism_search.server

# run the server over streamable HTTP
MCP_TRANSPORT=http MCP_AUTH_TOKEN=$(openssl rand -hex 32) MCP_PORT=8000 \
  .venv/bin/python -m mcp_prism_search.server
```

## Test layout

| File | Covers |
|---|---|
| `tests/test_search.py` | HTML result parsing, redirect resolution, ddgs compat shims, dedupe, provider request sizing, deep search |
| `tests/test_cache.py` | read/write, expiry, corrupt files, prune (expired/size/dry-run), stats, clear |
| `tests/test_server.py` | HTTP settings (fail-closed defaults, wildcard opt-out), transport security, bearer middleware, tool schemas |
| `tests/test_aggregator.py` | Registry, API contracts, selection, cache, timeouts and filtering |
| `tests/test_protocol.py` | MCP client discovery, tool listing and structured responses in modern and legacy protocol modes |
| `tests/test_security.py` | URL shape validation and IP/hostname blocklists |
| `tests/test_domains.py` | domain normalization and allow/block/preferred controls |
| `tests/test_audit_fixes.py` | regression tests for every audit finding and improvement (time filter, cache poisoning invalidation, byte cap, IP pinning, CGNAT blocklist, ToolError contract, progress reporting, `/health`, placeholder token rejection) |

Notes:

- Tests are offline by default; network-touching paths are covered with
  `httpx.MockTransport` or patched resolvers.
- HTTP-mode startup tests rely on the fail-fast behavior introduced with the
  mcp SDK 2.x migration (`MCP_AUTH_TOKEN` required, placeholders rejected).

## Conventions

- **Conventional Commits are required**: the release workflow derives the
  version from commit messages (`fix:` → patch, `feat:` → minor,
  `feat!:`/`BREAKING CHANGE:` → major) and publishes the tag, release notes,
  and Docker images automatically.
- `ruff` with `line-length = 100` and `target-version = "py312"`.
- Tool inputs and outputs are strict Pydantic models (`extra="forbid"`);
  add new fields to `models.py` and `ARGUMENT_DESCRIPTIONS` so the MCP
  schemas stay documented.
- Every security-relevant behavior added to `security.py`/`fetch.py` must
  come with a regression test (mocked DNS or `MockTransport`).

## Dependency policy

- Runtime dependencies carry lower bounds (`mcp>=2.3,<3`, and so on).
- `requirements-lock.txt` is the committed lock used by the Dockerfile for
  reproducible image builds; regenerate it after changing dependencies:

```bash
.venv/bin/pip freeze | grep -v '^-e\|^mcp-prism-search==' > requirements-lock.txt
```

- The release workflow builds the Docker image on every push to `main`
  (paths that are markdown-only are ignored).
