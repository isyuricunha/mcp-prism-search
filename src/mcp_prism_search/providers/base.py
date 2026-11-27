"""Provider abstraction: every search source speaks the same interface.

A provider is an async callable with the signature:

    async def search(request: SearchRequest, count: int, timeout: int) -> list[SearchResult]

Registered providers expose metadata (display name, whether they need an
API key or an instance URL) so the registry can skip the ones the operator
has not configured.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from mcp_prism_search import runtime
from mcp_prism_search.models import SearchRequest, SearchResult

ProviderFn = Callable[
    [SearchRequest, int, int],
    Awaitable[list[SearchResult]],
]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    kind: str  # "general" | "vertical"
    needs: str | None  # env var required to enable (None = keyless)
    description: str
    search: ProviderFn
    display_name: str = ""
    docs_url: str | None = None
    signup_url: str | None = None
    usage_note: str = ""


@dataclass
class ProviderHealth:
    ok: int = 0
    empty: int = 0
    errors: int = 0
    recent: deque[str] = field(default_factory=lambda: deque(maxlen=32))

    def bump(self, outcome: str) -> None:
        self.recent.append(outcome)
        if outcome == "ok":
            self.ok += 1
        elif outcome == "empty":
            self.empty += 1
        else:
            self.errors += 1

    @property
    def total(self) -> int:
        return self.ok + self.empty + self.errors

    @property
    def score(self) -> float:
        """Simple health score used to order the auto fallback chain."""
        if self.recent:
            return self.recent.count("ok") / len(self.recent)
        if self.total == 0:
            return 1.0  # untried providers start optimistic
        return self.ok / self.total


HEALTH: dict[str, ProviderHealth] = {}


def health_for(name: str) -> ProviderHealth:
    if name not in HEALTH:
        HEALTH[name] = ProviderHealth()
    return HEALTH[name]


def record(name: str, results: list[SearchResult] | None, error: str | None) -> None:
    health = health_for(name)
    if error is not None:
        health.bump("error")
    elif not results:
        health.bump("empty")
    else:
        health.bump("ok")


# Explicit preference order from live testing on the target deployment.
# Providers not listed here go last, alphabetically.
PREFERRED_ORDER = [
    "keenable", "freeserp", "bing_rss", "ddgs:yahoo", "mwmbl", "youcom_keyless",
    "searxng", "tinyfish", "tavily", "exa", "brave", "youcom", "serper", "jina",
    "ddg_html",
]


def build_chain(requested: str, available: dict[str, ProviderSpec]) -> list[str]:
    """Order providers for a search: requested first, then healthy ones."""
    if requested != "auto":
        if requested not in available:
            raise ValueError(f"Unknown provider {requested!r}")
        return [requested]

    preference_rank = {name: index for index, name in enumerate(PREFERRED_ORDER)}
    preferred = runtime.setting("MCP_PREFERRED_PROVIDER")
    if preferred in available:
        preference_rank[preferred] = -1
    ordered = sorted(
        available,
        key=lambda name: (
            -health_for(name).score,
            preference_rank.get(name, len(preference_rank)),
        ),
    )
    return ordered