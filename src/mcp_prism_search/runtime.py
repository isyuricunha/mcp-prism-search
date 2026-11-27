"""Shared server state; a fresh headless install needs no database."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_state: Any = None


def install(state: Any) -> None:
    global _state
    _state = state


def state() -> Any:
    return _state


def setting(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    if _state:
        return _state.credentials.get(name) or _state.options.get(name)
    return None


def disabled_by_env(name: str) -> bool:
    return name in {v.strip() for v in os.getenv("MCP_DISABLED_PROVIDERS", "").split(",")}


def enabled(name: str) -> bool:
    return not disabled_by_env(name) and (not _state or _state.flags.get(name, True))


def state_directory() -> Path:
    return Path(os.getenv("MCP_STATE_DIR") or os.getenv("MCP_WEBUI_DATA_DIR")
                or str(Path.home() / ".local/share/prism-admin"))


def configured_store(*, create: bool = False):
    """Load existing settings even without the dashboard; never create state unnecessarily."""
    directory = state_directory()
    database = directory / "settings.sqlite3"
    if not create and not database.exists() and not database.is_symlink():
        return None

    from mcp_prism_search.panel_store import PanelStore
    if database.exists() or database.is_symlink():
        if not (directory / "encryption.key").is_file():
            raise RuntimeError("Existing state encryption key is missing")
        return PanelStore(directory)
    return PanelStore(directory) if create else None


def record_metric(name: str, outcome: str, elapsed: float) -> None:
    if _state:
        _state.record(name, outcome, elapsed)


def record_cache(hit: bool) -> None:
    if _state:
        _state.cache_result(hit)
