"""URL safety checks for SSRF-resistant fetching."""

from __future__ import annotations

import asyncio
import socket
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from urllib.parse import urlparse

BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal"}
BLOCKED_HOSTNAME_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".intranet")
BLOCKED_NETWORKS: tuple[IPv4Network | IPv6Network, ...] = (
    ip_network("0.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("100.64.0.0/10"),
    ip_network("127.0.0.0/8"),
    ip_network("169.254.0.0/16"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
    # Reject transition prefixes; the embedded destination is not necessarily
    # subject to the same routing policy as a native global IPv6 address.
    ip_network("64:ff9b::/96"),
    ip_network("64:ff9b:1::/48"),
    ip_network("2002::/16"),
    ip_network("2001::/32"),
)


class UnsafeUrlError(ValueError):
    """Raised when a URL is not safe to fetch."""


def _normalize_hostname(hostname: str) -> str:
    return hostname.rstrip(".").lower()


def is_blocked_hostname(hostname: str) -> bool:
    normalized = _normalize_hostname(hostname)
    return normalized in BLOCKED_HOSTNAMES or normalized.endswith(BLOCKED_HOSTNAME_SUFFIXES)


def is_unsafe_ip(value: str) -> bool:
    try:
        ip = ip_address(value)
    except ValueError:
        return False

    # Scoped literals are invalid even if their mapped address is public.
    if getattr(ip, "scope_id", None) is not None:
        return True
    # IPv4-mapped IPv6 must follow the embedded IPv4 policy too.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return is_unsafe_ip(str(mapped))
    if not ip.is_global or any(ip in network for network in BLOCKED_NETWORKS):
        return True
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_reserved,
            ip.is_multicast,
            ip.is_unspecified,
        )
    )


def validate_url_shape(url: str) -> None:
    if len(url) > 8192:
        raise UnsafeUrlError("URL exceeds the safety limit")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise UnsafeUrlError("Whitespace and control characters are not allowed in URLs")
    try:
        parsed = urlparse(url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise UnsafeUrlError("Malformed URL") from None
    if port == 0:
        raise UnsafeUrlError("Invalid URL port")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL credentials are not allowed")
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only http and https URLs are allowed")
    if not hostname:
        raise UnsafeUrlError("URL must include a hostname")

    hostname = _normalize_hostname(hostname)
    if is_blocked_hostname(hostname):
        raise UnsafeUrlError(f"Blocked internal hostname: {hostname}")
    if is_unsafe_ip(hostname):
        raise UnsafeUrlError(f"Blocked unsafe IP address: {hostname}")


def _resolve_hostname(hostname: str, port: int) -> list[str]:
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"DNS resolution failed for {hostname}") from exc

    addresses: list[str] = []
    for record in records:
        sockaddr = record[4]
        if sockaddr:
            address = str(sockaddr[0])
            if address not in addresses:
                addresses.append(address)
    if not addresses:
        raise UnsafeUrlError(f"DNS resolution returned no addresses for {hostname}")
    # Prefer IPv4: many egress networks have no working IPv6 route, and a
    # pinned IPv6 connection would then fail even though the host is reachable.
    addresses.sort(key=lambda address: ":" in address)
    return addresses


def validate_resolved_addresses(url: str) -> None:
    validate_url_shape(url)
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname is None:
        raise UnsafeUrlError("URL must include a hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    for resolved_ip in _resolve_hostname(hostname, port):
        if is_unsafe_ip(resolved_ip):
            raise UnsafeUrlError(f"DNS resolved to unsafe IP address: {resolved_ip}")


async def validate_fetch_url(url: str) -> None:
    validate_url_shape(url)
    await asyncio.to_thread(validate_resolved_addresses, url)


async def resolve_safe_address(url: str) -> tuple[str, int]:
    """Resolve the hostname once and return a validated (ip, port) pair.

    The caller must connect to this IP directly instead of letting the HTTP
    client re-resolve DNS, otherwise a short-TTL rebinding record can pass
    validation and point the connection at an internal address.
    """
    validate_url_shape(url)
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname is None:
        raise UnsafeUrlError("URL must include a hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = await asyncio.to_thread(_resolve_hostname, hostname, port)
    if any(is_unsafe_ip(address) for address in addresses):
        raise UnsafeUrlError("DNS returned an unsafe address")
    return addresses[0], port
