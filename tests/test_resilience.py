"""Regression cases for malformed upstream input and unavailable local storage."""
import asyncio
import json
import time

import httpx
import pytest

from mcp_prism_search import cache, fetch, security


@pytest.mark.parametrize("raw", [
    b"null", b"[]", b"42", b"\xff", b"{}", b'{"created_at":NaN,"value":{}}',
])
def test_bad_cache_envelope_is_a_miss(tmp_path, raw):
    store = cache.JsonFileCache(tmp_path, "search", 60)
    store.path_for("q").write_bytes(raw)
    assert store.get("q") is None


def test_cache_future_timestamp_is_a_miss(tmp_path):
    store = cache.JsonFileCache(tmp_path, "search", 60)
    store.path_for("q").write_text(json.dumps({"created_at":time.time()+3600,"value":{}}))
    assert store.get("q") is None


def test_cache_write_failure_is_nonfatal(tmp_path, monkeypatch):
    store = cache.JsonFileCache(tmp_path, "search", 60)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", fail)
    store.set("q", {"result": "ok"})
    assert not list(store.cache_dir.iterdir())


def test_cache_symlink_not_read(tmp_path):
    store = cache.JsonFileCache(tmp_path, "search", 60)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"created_at": time.time(), "value": {"private": True}}))
    store.path_for("q").symlink_to(outside)
    assert store.get("q") is None


@pytest.mark.parametrize("url", [
    "https://example.org:invalid", "https://example.org:0",
    "https://[bad", "https://user:secret@example.org",
    "https://example.org/a b", "https://example.org/\n",
])
def test_invalid_fetch_url_returns_structured_error(url, tmp_path):
    result = asyncio.run(fetch.web_fetch(url, cache=cache.JsonFileCache(tmp_path, "fetch", 60)))
    assert not result.success
    assert "secret" not in result.error


def test_mapped_ipv4_and_mixed_dns_rejected(monkeypatch):
    assert security.is_unsafe_ip("::ffff:100.64.0.1")
    monkeypatch.setattr(security, "_resolve_hostname", lambda *a: ["8.8.8.8", "127.0.0.1"])
    with pytest.raises(security.UnsafeUrlError):
        asyncio.run(security.resolve_safe_address("https://example.org"))


def test_fetch_preserves_prefix_and_disables_proxy_and_reuse(tmp_path, monkeypatch):
    original = httpx.AsyncClient
    options = {}

    async def validate(url):
        return None

    async def resolve(url):
        return "8.8.8.8", 443

    def factory(**kwargs):
        options.update(kwargs)
        return original(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, content=b"prefix" * 1000, headers={"content-type":"text/plain"}
        )), **kwargs)

    monkeypatch.setattr(fetch, "validate_fetch_url", validate)
    monkeypatch.setattr(fetch, "resolve_safe_address", resolve)
    monkeypatch.setattr(fetch, "get_fetch_max_bytes", lambda: 1000)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    result = asyncio.run(fetch.web_fetch("https://example.org",
                         cache=cache.JsonFileCache(tmp_path,"fetch",60)))
    assert result.success and result.truncated and result.fetched_bytes == 1000
    assert "prefix" in result.content
    assert options["trust_env"] is False
    assert options["limits"].max_keepalive_connections == 0
