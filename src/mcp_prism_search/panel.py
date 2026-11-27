"""Small, isolated, optional administration ASGI application."""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from hmac import compare_digest
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from mcp_prism_search import __version__, cache, providers, runtime
from mcp_prism_search.panel_store import PanelStore
from mcp_prism_search.search import aggregated_search, get_ddg_timeout_seconds

COOKIE = "prism_admin"
SESSION_TTL = 8 * 3600
MAX_BODY = 16384
SETTINGS_ENV = {
    "timeout": "DDG_TIMEOUT_SECONDS",
    "search_ttl": "DDG_CACHE_TTL_SECONDS",
    "fetch_ttl": "FETCH_CACHE_TTL_SECONDS",
    "preferred": "MCP_PREFERRED_PROVIDER",
}


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(max_length=1024)


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    credential: str | None = Field(default=None, min_length=1, max_length=4096)
    remove: bool = False


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout: int | None = Field(default=None, ge=2, le=120)
    search_ttl: int | None = Field(default=None, ge=60, le=604800)
    fetch_ttl: int | None = Field(default=None, ge=60, le=604800)
    preferred: str | None = Field(default=None, max_length=100)


class ClearCache(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["search", "fetch", "all"]
    confirm: Literal[True]


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)
    providers: list[str] | None = Field(default=None, min_length=1, max_length=50)
    mode: Literal["selected", "auto", "all"] = "selected"
    max_results: int = Field(default=5, ge=1, le=10)


def error(message: str, status: int = 400):
    return JSONResponse({"error": message}, status_code=status)


async def body(request: Request, model):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ValueError("JSON required")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY:
            raise ValueError("Request too large")
        data.extend(chunk)
    return model.model_validate_json(data)


def create_app(password: str, store: PanelStore, assets: Path,
               hosts: list[str] | None = None):
    if len(password) < 12 or len(password) > 1024:
        raise RuntimeError("MCP_WEBUI_PASSWORD must contain 12–1024 characters")
    if not (assets / "index.html").is_file():
        raise RuntimeError("Build the optional Astro panel first")
    sessions: dict[str, float] = {}
    failures: deque[float] = deque(maxlen=10)
    search_slots = asyncio.Semaphore(2)

    def valid_session(request):
        now = time.monotonic()
        for token in list(sessions):
            if sessions[token] <= now:
                sessions.pop(token, None)
        return request.cookies.get(COOKIE, "") in sessions

    async def login(request):
        data = await body(request, Login)
        now = time.monotonic()
        while failures and now - failures[0] > 60:
            failures.popleft()
        if len(failures) >= 10:
            return error("Too many attempts; try again in a minute", 429)
        if not compare_digest(data.password.encode(), password.encode()):
            failures.append(now)
            return error("Invalid password", 401)
        failures.clear()
        valid_session(request)
        if len(sessions) >= 64:
            sessions.pop(next(iter(sessions)))
        token = secrets.token_urlsafe(32)
        sessions[token] = now + SESSION_TTL
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, token, max_age=SESSION_TTL,
                            httponly=True, samesite="strict", secure=request.url.scheme == "https")
        return response

    async def logout(request):
        sessions.pop(request.cookies.get(COOKIE, ""), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE)
        return response

    async def overview(request):
        rows = []
        available = providers.available_providers()
        for name, spec in providers.SPECS.items():
            env = spec.needs
            rows.append({
                "id": name, "kind": spec.kind, "description": spec.description,
                "enabled": runtime.enabled(name), "available": name in available,
                "credential": bool(env and runtime.setting(env)),
                "managed": bool(env and os.getenv(env)),
                "editable": bool(env and env != "SEARXNG_URL"), "env": env,
            })
        return JSONResponse({
            "server": {
                "version": __version__,
                "sdk_version": version("mcp"),
                "protocol_version": LATEST_PROTOCOL_VERSION,
                "transport": "streamable-http",
            },
            "providers": rows, "metrics": store.metrics(), "counters": store.counters(),
            "settings": current_settings(),
            "cache": (await asyncio.to_thread(cache.get_cache_stats)).to_dict(),
        })

    def current_settings():
        return {
            "timeout": get_ddg_timeout_seconds(),
            "search_ttl": cache.build_search_cache().ttl_seconds,
            "fetch_ttl": cache.build_fetch_cache().ttl_seconds,
            "preferred": runtime.setting("MCP_PREFERRED_PROVIDER"),
            "locked": [field for field, name in SETTINGS_ENV.items() if os.getenv(name)],
        }

    async def settings(request):
        data = await body(request, Settings)
        values = {}
        for field, value in data.model_dump(exclude_unset=True).items():
            env = SETTINGS_ENV[field]
            if os.getenv(env):
                return error("Setting is managed by the environment")
            if field == "preferred" and value is not None:
                providers.available_providers()
                spec = providers.SPECS.get(value)
                if not spec or spec.kind != "general":
                    return error("Choose a general provider")
            values[env] = str(value) if value is not None else None
        store.set_options(values)
        return JSONResponse(current_settings())

    async def update(request):
        name = request.path_params["name"]
        providers.available_providers()
        spec = providers.SPECS.get(name)
        if not spec:
            return error("Unknown provider", 404)
        data = await body(request, Update)
        if data.credential is not None and data.remove:
            return error("Choose replace or remove, not both")
        if (data.credential is not None or data.remove) and (
            not spec.needs or spec.needs == "SEARXNG_URL" or os.getenv(spec.needs)
        ):
            return error("Credential is not editable")
        store.update(name, spec.needs, data.enabled, data.credential, data.remove)
        return JSONResponse({"ok": True})

    async def search(request):
        data = await body(request, Query)
        available = providers.available_providers("general")
        if data.mode == "selected":
            if not data.providers or any(name not in available for name in data.providers):
                return error("Choose available general providers")
        elif data.providers is not None:
            return error("Use either automatic/all mode or an explicit provider selection")
        if not data.query.strip():
            return error("Enter a non-empty search query")
        if search_slots.locked():
            return error("Too many concurrent searches", 429)
        async with search_slots:
            kwargs = {"providers": data.providers} if data.mode == "selected" else {
                "provider": data.mode
            }
            result = await aggregated_search(
                data.query.strip(), max_results=data.max_results, **kwargs
            )
        return JSONResponse(
            result.model_dump(mode="json"), status_code=400 if result.error else 200
        )

    async def prune(request):
        # Preserve active entries; clearing is a separate confirmed operation.
        return JSONResponse((await asyncio.to_thread(
            cache.prune_cache, expired_only=True
        )).to_dict())

    async def clear(request):
        data = await body(request, ClearCache)
        return JSONResponse((await asyncio.to_thread(
            cache.clear_cache, namespace=data.namespace, confirm=data.confirm
        )).to_dict())

    @asynccontextmanager
    async def lifespan(app):
        runtime.install(store)
        try:
            yield
        finally:
            runtime.install(None)

    app = Starlette(routes=[
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/overview", overview),
        Route("/api/providers/{name}", update, methods=["PUT"]),
        Route("/api/search", search, methods=["POST"]),
        Route("/api/cache/prune", prune, methods=["POST"]),
        Route("/api/cache/clear", clear, methods=["POST"]),
        Route("/api/settings", settings, methods=["PUT"]),
        Mount("/", app=StaticFiles(directory=assets, html=True)),
    ], lifespan=lifespan)

    async def security(request, call_next):
        local = request.url.hostname in {"localhost", "127.0.0.1"}
        if not local and request.url.scheme != "https":
            return error("HTTPS required", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if (request.headers.get("origin") != expected
                    or request.headers.get("x-prism-admin") != "1"):
                return error("Invalid request origin", 403)
        if (request.url.path.startswith("/api/") and request.url.path != "/api/login"
                and not valid_session(request)):
            return error("Authentication required", 401)
        try:
            response = await call_next(request)
        except (ValueError, ValidationError):
            response = error("Invalid request")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=security)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ["localhost", "127.0.0.1"])
    return app


def configured_app():
    directory = Path(os.getenv("MCP_WEBUI_DATA_DIR", str(Path.home() / ".local/share/prism-admin")))
    assets = Path(os.getenv(
        "MCP_WEBUI_ASSETS", str(Path(__file__).resolve().parents[2] / "webui/dist")
    ))
    password = os.getenv("MCP_WEBUI_PASSWORD", "")
    if len(password) < 12:
        raise RuntimeError("Set MCP_WEBUI_PASSWORD (at least 12 characters)")
    hosts = os.getenv("MCP_WEBUI_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    hosts = [h.strip() for h in hosts if h.strip()]
    if not hosts or any("*" in h for h in hosts):
        raise RuntimeError("Use explicit MCP_WEBUI_ALLOWED_HOSTS values")
    return create_app(password, PanelStore(directory), assets, hosts)
