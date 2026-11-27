"""Optional panel state; headless callers never open a database."""
from __future__ import annotations

import os
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


def enabled(name: str) -> bool:
    return not _state or _state.flags.get(name, True)


def record_metric(name: str, outcome: str, elapsed: float) -> None:
    if _state:
        _state.record(name, outcome, elapsed)


def record_cache(hit: bool) -> None:
    if _state:
        _state.cache_result(hit)
