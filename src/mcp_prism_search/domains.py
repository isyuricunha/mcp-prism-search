"""Domain normalization and opt-in search result controls."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx


def normalize_domain(value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 or c == chr(92) for c in value):
        raise ValueError("Invalid domain")
    raw_value = value.strip().lower()
    if not raw_value:
        return ""

    try:
        # Bare IPv6 literals do not have URL authority syntax.
        return str(ip_address(raw_value))
    except ValueError:
        pass
    parsed = urlparse(raw_value if "://" in raw_value else f"//{raw_value}")
    if (parsed.scheme not in {"", "http", "https"} or parsed.username is not None
            or parsed.password is not None or not parsed.hostname or parsed.port == 0):
        raise ValueError("Invalid domain")
    normalized = parsed.hostname.rstrip(".")
    try:
        return str(ip_address(normalized))
    except ValueError:
        pass
    try:
        normalized = httpx.URL("https://" + normalized).raw_host.decode("ascii")
    except (httpx.InvalidURL, UnicodeError):
        raise ValueError("Invalid domain") from None
    if len(normalized) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in normalized.split(".")
    ):
        raise ValueError("Invalid domain")
    if normalized.startswith("www.") and "." in normalized[4:]:
        normalized = normalized[4:]
    return normalized


def normalize_domains(values: Iterable[str]) -> list[str]:
    normalized_domains: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_domain(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_domains.append(normalized)
    return normalized_domains


def domain_matches(hostname: str, domain: str) -> bool:
    try:
        normalized_hostname = normalize_domain(hostname)
        normalized_domain = normalize_domain(domain)
    except ValueError:
        return False
    if not normalized_hostname or not normalized_domain:
        return False
    if normalized_hostname == normalized_domain:
        return True
    try:
        ip_address(normalized_domain)
    except ValueError:
        return normalized_hostname.endswith(f".{normalized_domain}")
    return False


def url_matches_domains(url: str, domains: Sequence[str]) -> bool:
    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return False
    except ValueError:
        return False
    return any(domain_matches(parsed.hostname, domain) for domain in domains)


def apply_domain_controls[T](
    items: Sequence[T],
    *,
    get_url: Callable[[T], str],
    allowed_domains: Sequence[str],
    blocked_domains: Sequence[str],
    preferred_domains: Sequence[str],
) -> list[T]:
    controlled_items = list(items)

    if allowed_domains:
        controlled_items = [
            item for item in controlled_items if url_matches_domains(get_url(item), allowed_domains)
        ]

    if blocked_domains:
        controlled_items = [
            item
            for item in controlled_items
            if not url_matches_domains(get_url(item), blocked_domains)
        ]

    if preferred_domains:
        preferred_items, other_items = [], []
        for item in controlled_items:
            target = (preferred_items if url_matches_domains(get_url(item), preferred_domains)
                      else other_items)
            target.append(item)
        controlled_items = [*preferred_items, *other_items]

    return controlled_items
