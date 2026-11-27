"""Server-owned, validated defaults shared by every transport and the optional UI."""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mcp_prism_search import runtime
from mcp_prism_search.domains import normalize_domains

Recency = Literal["any", "day", "week", "month", "year"]


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    search_mode: Literal["auto", "keyless", "all", "selected"] = "auto"
    search_providers: list[str] = Field(default_factory=list, max_length=20)
    search_max_results: int = Field(default=10, ge=1, le=30)
    search_safe_search: Literal["off", "moderate", "strict"] = "off"
    search_recency: Recency = "any"
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    blocked_domains: list[str] = Field(default_factory=list, max_length=50)
    preferred_domains: list[str] = Field(default_factory=list, max_length=50)
    fetch_max_chars: int = Field(default=12000, ge=1000, le=50000)
    deep_max_pages: int = Field(default=5, ge=1, le=10)
    vertical_source: Literal["wikipedia", "hackernews"] = "wikipedia"
    vertical_max_results: int = Field(default=5, ge=1, le=20)
    answer_provider: Literal["explicit", "tavily", "exa", "youcom", "brave"] = "explicit"
    research_provider: Literal["explicit", "tavily", "exa", "youcom", "brave", "jina"] = "explicit"
    research_effort: Literal["lite", "standard", "deep"] = "lite"
    fetch_timeout: int = Field(default=15, ge=1, le=120)
    deep_concurrency: int = Field(default=5, ge=1, le=12)
    news_cache_ttl: int = Field(default=1800, ge=1, le=604800)

    @field_validator("allowed_domains", "blocked_domains", "preferred_domains")
    @classmethod
    def normalize_domain_list(cls, value):
        return normalize_domains(value)

    @field_validator("search_providers")
    @classmethod
    def unique_providers(cls, value):
        from mcp_prism_search import providers

        if not providers.SPECS:
            providers.build_registry()
        names = list(dict.fromkeys(value))
        if any(name not in providers.SPECS or providers.SPECS[name].kind != "general"
               for name in names):
            raise ValueError("Choose registered general providers")
        return names


# Stable, documented environment names. Reuse old names rather than create aliases.
ENV = {
    name: "MCP_DEFAULT_" + name.upper() for name in Defaults.model_fields
}
ENV.update(fetch_timeout="FETCH_TIMEOUT_SECONDS", deep_concurrency="MAX_CONCURRENCY",
           news_cache_ttl="MCP_NEWS_CACHE_TTL_SECONDS")
LISTS = {"search_providers", "allowed_domains", "blocked_domains", "preferred_domains"}

# UI metadata describes the core contract; the browser never invents runtime defaults.
LABELS = {
    "search_mode": (
        "Search", "Search mode",
        "Automatic fallback, keyless fallback, all, or selected.",
    ),
    "search_providers": (
        "Search", "Selected engines",
        "Used only in Selected mode. Disabled engines are skipped; no widening.",
    ),
    "search_max_results": (
        "Search", "Maximum results",
        "Final result limit for general and legacy searches.",
    ),
    "search_safe_search": (
        "Search", "SafeSearch",
        "Preferred filtering level; upstream support varies.",
    ),
    "search_recency": (
        "Search", "Freshness",
        "Any time or a preferred publication window; support varies.",
    ),
    "allowed_domains": (
        "Advanced search", "Allowed domains",
        "Restrict results to these domains and their subdomains.",
    ),
    "blocked_domains": (
        "Advanced search", "Blocked domains",
        "Exclude these domains and their subdomains.",
    ),
    "preferred_domains": (
        "Advanced search", "Preferred domains",
        "Rank these domains first without excluding others.",
    ),
    "fetch_max_chars": (
        "Fetching", "Page text limit",
        "Characters returned per webpage, including legacy deep search.",
    ),
    "deep_max_pages": (
        "Fetching", "Deep search pages",
        "Maximum pages fetched by the legacy deep-search tool.",
    ),
    "fetch_timeout": (
        "Fetching", "Fetch timeout (seconds)",
        "Network timeout for webpage fetching.",
    ),
    "deep_concurrency": (
        "Fetching", "Parallel page fetches",
        "Per-call concurrency for legacy deep search.",
    ),
    "vertical_source": (
        "Specialized search", "Default vertical source",
        "Wikipedia or Hacker News.",
    ),
    "vertical_max_results": (
        "Specialized search", "Vertical result limit",
        "Results returned by vertical_search.",
    ),
    "answer_provider": (
        "Standalone AI", "Answer provider",
        "Used when web_answer omits provider. Explicit requires a choice.",
    ),
    "research_provider": (
        "Standalone AI", "Research provider",
        "Used when web_research omits provider. No paid fallback.",
    ),
    "research_effort": (
        "Standalone AI", "Research effort",
        "Default depth; unsupported tiers require an explicit override.",
    ),
    "news_cache_ttl": (
        "Cache", "Freshness cache cap (seconds)",
        "Freshness-looking queries never outlive the normal search TTL.",
    ),
}


def _decode(name: str, raw: str):
    if name in LISTS:
        return [] if raw == "none" else [v.strip() for v in raw.split(",") if v.strip()]
    if name == "deep_concurrency":
        try:
            return min(int(raw), 12)
        except ValueError:
            return raw
    return raw.strip()


def effective_defaults(overrides: dict | None = None) -> Defaults:
    values = {}
    for name, env in ENV.items():
        raw = runtime.setting(env)
        if raw is not None:
            values[name] = _decode(name, raw)
    if overrides:
        for name, value in overrides.items():
            if value is None:
                values.pop(name, None)
            else:
                values[name] = value
    try:
        result = Defaults.model_validate(values)
    except ValidationError as exc:
        fields = ", ".join(str(error["loc"][0]) for error in exc.errors())
        raise ValueError("Invalid server defaults: " + fields) from None
    if result.search_mode == "selected" and not result.search_providers:
        raise ValueError("Selected default mode requires at least one provider")
    if result.research_provider != "explicit":
        from mcp_prism_search.native import CAPABILITIES

        if result.research_effort not in CAPABILITIES[result.research_provider]["efforts"]:
            raise ValueError("Default research effort is unsupported by the selected provider")
    return result


def locked(name: str) -> bool:
    return bool(os.getenv(ENV[name]))


def update_defaults(store, patch: dict) -> Defaults:
    if not set(patch) <= ENV.keys():
        raise ValueError("Unknown default setting")
    if any(locked(name) for name in patch):
        raise ValueError("Default is managed by the environment")
    validated = effective_defaults(patch)
    store.set_options({
        ENV[name]: None if value is None else (
            ",".join(getattr(validated, name)) or "none"
            if name in LISTS else str(getattr(validated, name))
        )
        for name, value in patch.items()
    })
    return validated


def describe_defaults() -> dict:
    from mcp_prism_search import providers

    current = effective_defaults().model_dump(mode="json")
    builtin = Defaults().model_dump(mode="json")
    schema = Defaults.model_json_schema()["properties"]
    if not providers.SPECS:
        providers.build_registry()
    fields = []
    for name, (group, label, description) in LABELS.items():
        spec = schema[name]
        fields.append({
            "id": name, "group": group, "label": label, "description": description,
            "env": ENV[name], "value": current[name], "builtin": builtin[name],
            "locked": locked(name), "type": spec.get("type"),
            "choices": ([
                n for n, item in providers.SPECS.items() if item.kind == "general"
            ] if name == "search_providers" else spec.get("enum")),
            "min": spec.get("minimum"), "max": spec.get("maximum"),
        })
    return {"fields": fields}


def search_parameters(defaults: Defaults, *, max_results, safe_search, time_filter,
                      allowed_domains, blocked_domains, preferred_domains, vertical=False):
    """Resolve once at the retrieval boundary. Explicit empty lists clear defaults."""
    return {
        "max_results": max_results if max_results is not None else (
            defaults.vertical_max_results if vertical else defaults.search_max_results
        ),
        "safe_search": safe_search if safe_search is not None else (
            "off" if vertical else defaults.search_safe_search
        ),
        "time_filter": (None if time_filter == "any" else time_filter) if time_filter is not None
        else (None if vertical or defaults.search_recency == "any" else defaults.search_recency),
        "allowed_domains": allowed_domains if allowed_domains is not None else (
            [] if vertical else defaults.allowed_domains
        ),
        "blocked_domains": blocked_domains if blocked_domains is not None else (
            [] if vertical else defaults.blocked_domains
        ),
        "preferred_domains": preferred_domains if preferred_domains is not None else (
            [] if vertical else defaults.preferred_domains
        ),
    }
