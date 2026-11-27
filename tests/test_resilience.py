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


@pytest.mark.parametrize("name,env", [
    ("exa", "EXA_API_KEY"), ("tavily", "TAVILY_API_KEY"),
    ("brave", "BRAVE_API_KEY"), ("jina", "JINA_API_KEY"),
])
def test_provider_contract_boundaries(name, env, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from importlib import import_module

    from mcp_prism_search.models import SearchRequest

    monkeypatch.setenv(env, "fixture-not-real-key")
    module = import_module("mcp_prism_search.providers." + name)
    original = httpx.AsyncClient
    row = {"url": "https://example.org", "title": "Example", "description": "Text"}
    calls = []

    def handler(req):
        calls.append(req)
        if name == "exa":
            payload = json.loads(req.content)
            start = datetime.fromisoformat(payload["startPublishedDate"])
            assert abs((datetime.now(UTC) - timedelta(days=7) - start).total_seconds()) < 5
        elif name == "tavily":
            payload = json.loads(req.content)
            assert payload["max_results"] == 20 and payload["auto_parameters"] is False
        elif name == "brave":
            assert req.url.params["count"] == "20" and req.url.params["freshness"] == "pw"
        else:
            assert "%3F" in str(req.url) and "%23" in str(req.url)
            assert not req.url.query and not req.url.fragment
        data = {"results": [None, row]}
        if name == "brave":
            data = {"web": {"results": [None, row]}}
        elif name == "jina":
            data = {"data": [None, row]}
        return httpx.Response(200, json=data)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    rows = asyncio.run(module.search(SearchRequest(query="C++? #test", time_filter="week"), 50, 5))
    assert len(calls) == 1 and rows[0].url == row["url"]


def test_dedupe_retains_case_and_path_params_and_rejects_malformed():
    from mcp_prism_search.models import SearchResult
    from mcp_prism_search.search import _dedupe_results

    urls = ["https://example.org/A", "https://example.org/a",
            "https://example.org/a;x", "https://example.org/a#fragment",
            "https://[invalid", "https://example.org:bad", "javascript:alert(1)"]
    rows = _dedupe_results([SearchResult(title="T", url=url, snippet="") for url in urls])
    assert [row.url for row in rows] == urls[:3]


def test_malformed_ddg_link_does_not_discard_valid_row():
    from mcp_prism_search.providers.ddg_html import parse_results

    rows = parse_results(
        '<div class="result"><a class="result__a" href="https://[bad">Bad</a></div>'
        '<div class="result"><a class="result__a" href="https://example.org">Good</a></div>',
        10,
    )
    assert len(rows) == 1 and rows[0].title == "Good"


def test_ranking_skips_bad_urls_and_retains_good_results():
    from mcp_prism_search.models import SearchResult
    from mcp_prism_search.search import rank_results

    rows = [SearchResult(title="test", url=url, snippet="test") for url in (
        "https://[bad", "https://example.org:bad", "https://example.org",
    )]
    assert rank_results(rows, "test") == rows[2:]


def test_telemetry_backend_failure_does_not_propagate(monkeypatch):
    from types import SimpleNamespace

    from mcp_prism_search import runtime

    def fail(*args):
        raise RuntimeError("telemetry offline")

    monkeypatch.setattr(runtime, "_state", SimpleNamespace(record=fail, cache_result=fail))
    runtime.record_metric("test", "ok", 0.1)
    runtime.record_cache(True)


def test_aggregated_cache_rejects_wrong_query(tmp_path, monkeypatch):
    from mcp_prism_search import providers, search
    from mcp_prism_search.models import SearchResult

    store = cache.JsonFileCache(tmp_path, "search", 60)
    calls = []

    async def retrieve(*args):
        calls.append(args)
        return [SearchResult(title="Evidence", url="https://example.org", snippet="Text")]

    monkeypatch.setattr(providers, "run_provider", retrieve)
    first = asyncio.run(search.aggregated_search("query", provider="keenable", cache=store))
    assert first.results
    path = next(store.cache_dir.glob("*.json"))
    envelope = json.loads(path.read_text())
    envelope["value"]["query"] = "another query"
    path.write_text(json.dumps(envelope))
    second = asyncio.run(search.aggregated_search("query", provider="keenable", cache=store))
    assert not second.cached and second.query == "query" and len(calls) == 2


@pytest.mark.parametrize("content_type", ["text/html", "text/plain"])
def test_character_limit_reports_truncation(content_type):
    body = ("<main>" + "a" * 2000 + "</main>") if content_type == "text/html" else "a" * 2000
    raw = fetch.RawFetchedResponse(
        200, httpx.Headers({"content-type": content_type}),
        body.encode(), "https://example.org", "utf-8", fetched_bytes=len(body),
    )
    response = fetch._response_to_fetch_response("https://example.org", raw, 1000)
    assert response.success and response.truncated and len(response.content) == 1000


def test_request_limits_bound_query_url_and_domain_lists():
    from mcp_prism_search.models import FetchRequest, SearchRequest

    with pytest.raises(ValueError):
        SearchRequest(query="x" * 4001)
    with pytest.raises(ValueError):
        SearchRequest(query="query", allowed_domains=["example.org"] * 51)
    with pytest.raises(ValueError):
        FetchRequest(url="https://example.org/" + "x" * 8192)


@pytest.mark.parametrize("address", ["fe80::1%eth0", "100.64.0.1", "192.0.2.1", "224.0.0.1"])
def test_non_public_ip_addresses_are_not_fetchable(address):
    assert security.is_unsafe_ip(address)


def test_redirect_url_length_is_bounded():
    with pytest.raises(security.UnsafeUrlError, match="safety limit"):
        security.validate_url_shape("https://example.org/" + "x" * 8192)


def test_deep_search_keeps_successful_pages_and_completes_progress(monkeypatch):
    from mcp_prism_search.models import FetchResponse, SearchResponse, SearchResult

    rows = [SearchResult(title=str(i), url=f"https://example.org/{i}", snippet="")
            for i in range(2)]
    progress = []

    async def retrieve(**kwargs):
        return SearchResponse(query="query", provider="ddgs", results=rows,
                              cached=False, error=None)

    async def page(url, **kwargs):
        if url.endswith("/0"):
            raise RuntimeError("private detail")
        return FetchResponse(url=url, final_url=url, title="Good", content="Evidence",
                             content_type="text/plain", cached=False, success=True, error=None)

    async def report(done, total):
        progress.append((done, total))

    monkeypatch.setattr(fetch, "ddg_search", retrieve)
    monkeypatch.setattr(fetch, "web_fetch", page)
    result = asyncio.run(fetch.ddg_deep_search("query", max_pages=5, progress_callback=report))
    assert len(result.pages) == len(result.failed_pages) == 1
    assert "private detail" not in result.failed_pages[0].error
    assert progress == [(0, 6), (1, 6), (2, 6), (3, 6), (6, 6)]


@pytest.mark.parametrize("bad_url", ["https://other.example", "http://127.0.0.1"])
def test_fetch_cache_rejects_mismatched_or_unsafe_url(tmp_path, monkeypatch, bad_url):
    from mcp_prism_search.models import FetchRequest, FetchResponse

    url = "https://example.org"
    store = cache.JsonFileCache(tmp_path, "fetch", 60)
    request = FetchRequest(url=url)
    value = FetchResponse(
        url=bad_url, final_url=bad_url, title="Wrong", content="Other page",
        content_type="text/plain", success=True, cached=False, error=None,
    )
    store.set(fetch._cache_payload(request) | {"_max_bytes": fetch.get_fetch_max_bytes()},
              value.model_dump())

    async def valid(url):
        pass

    async def failed(*args):
        raise RuntimeError("offline")

    monkeypatch.setattr(fetch, "validate_fetch_url", valid)
    monkeypatch.setattr(fetch, "_get_with_validated_redirects", failed)
    response = asyncio.run(fetch.web_fetch(url, cache=store))
    assert not response.cached and not response.success
    assert not list(store.cache_dir.glob("*.json"))


def test_tinyfish_stops_when_page_has_only_duplicate_urls(monkeypatch):
    from mcp_prism_search.models import SearchRequest
    from mcp_prism_search.providers import tinyfish

    monkeypatch.setenv("TINYFISH_API_KEY", "fixture-key")
    original = httpx.AsyncClient
    calls = []

    def handler(req):
        page = int(req.url.params["page"])
        calls.append(page)
        return httpx.Response(200, json={"results": [
            {"title": "Example", "url": f"https://example.org/#page{page}"},
            {"title": "Malformed", "url": "https://[bad"},
        ]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw))
    results = asyncio.run(tinyfish.search(SearchRequest(query="query"), 10, 5))
    assert len(results) == 1 and calls == [0, 1]


def test_legacy_search_cache_rejects_wrong_query(tmp_path, monkeypatch):
    from mcp_prism_search import search
    from mcp_prism_search.models import SearchRequest, SearchResponse, SearchResult

    monkeypatch.setenv("SEARCH_PROVIDER", "duckduckgo_html")
    store = cache.JsonFileCache(tmp_path, "search", 60)
    row = SearchResult(title="Old", url="https://example.org", snippet="")
    payload = search._cache_payload(SearchRequest(query="query"),
                                    provider="duckduckgo_html", ddgs_backend=None)
    store.set(payload, SearchResponse(query="wrong", provider="duckduckgo_html",
                                     results=[row], cached=False, error=None).model_dump())

    async def retrieve(*args):
        return [row.model_copy(update={"title": "New"})]

    monkeypatch.setattr(search, "_search_with_duckduckgo_html", retrieve)
    result = asyncio.run(search.ddg_search("query", cache=store))
    assert not result.cached and result.results[0].title == "New"


def test_existing_state_without_key_never_generates_replacement(tmp_path):
    from mcp_prism_search.panel_store import PanelStore

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    database = state / "settings.sqlite3"
    database.write_bytes(b"existing database")
    with pytest.raises(RuntimeError, match="key is missing"):
        PanelStore(state)
    assert not (state / "encryption.key").exists()
    assert database.read_bytes() == b"existing database"


@pytest.mark.parametrize("status", [300, 304, 399])
def test_fetch_unhandled_redirect_status_is_not_success(status):
    raw = fetch.RawFetchedResponse(status, httpx.Headers({"content-type": "text/plain"}),
                                   b"not a page", "https://example.org", "utf-8")
    result = fetch._response_to_fetch_response("https://example.org", raw, 1000)
    assert not result.success and result.error == f"HTTP {status}"


def test_fetch_unknown_charset_falls_back_to_utf8():
    raw = fetch.RawFetchedResponse(200, httpx.Headers({"content-type": "text/plain"}),
                                   "Olá".encode(), "https://example.org", "invalid-charset")
    result = fetch._response_to_fetch_response("https://example.org", raw, 1000)
    assert result.success and result.content == "Olá"


@pytest.mark.parametrize(("url", "host"), [
    ("https://bücher.example/a", "xn--bcher-kva.example"),
    ("https://[2001:4860:4860::8888]:8443/", "[2001:4860:4860::8888]:8443"),
])
def test_pinned_fetch_host_header_is_ascii_and_preserves_authority(url, host):
    assert fetch._original_host_header(url) == host


def test_redirect_resolution_uses_one_pinned_lookup_per_hop(monkeypatch):
    calls = []

    async def resolve(url):
        calls.append(url)
        return "93.184.216.34", 443

    async def verify():
        def handler(req):
            if req.url.path == "/first":
                return httpx.Response(302, headers={"location": "/second"})
            return httpx.Response(200, content=b"hello")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch._get_with_validated_redirects(
                client, "https://example.org/first", 1000
            )
        assert result.logical_url.endswith("/second")

    monkeypatch.setattr(fetch, "resolve_safe_address", resolve)
    asyncio.run(verify())
    assert calls == ["https://example.org/first", "https://example.org/second"]


def test_partial_html_notice_survives_article_extraction():
    body = b"<article>" + b"hello " * 300 + b"</article>"
    raw = fetch.RawFetchedResponse(200, httpx.Headers({"content-type": "text/html"}),
                                   body, "https://example.org", "utf-8", truncated=True)
    result = fetch._response_to_fetch_response("https://example.org", raw, 1000)
    assert result.truncated and result.content.startswith("[PARTIAL CONTENT:")
    assert "hello" in result.content and len(result.content) <= 1000


@pytest.mark.parametrize("name", ["settings.sqlite3", "encryption.key"])
def test_panel_store_rejects_nonregular_state_paths(tmp_path, name):
    from mcp_prism_search.panel_store import PanelStore

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / name).mkdir()
    with pytest.raises(RuntimeError, match="Invalid panel"):
        PanelStore(state)


@pytest.mark.parametrize("address", [
    "::ffff:8.8.8.8%eth0", "64:ff9b::a00:1", "64:ff9b:1::1",
    "2002:7f00:1::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
])
def test_fetch_rejects_scoped_mapped_and_transition_ips(address):
    assert security.is_unsafe_ip(address)
    assert not security.is_unsafe_ip("2001:4860:4860::8888")


def test_fetch_dns_uses_same_idna_hostname_as_tls(monkeypatch):
    names = []

    def resolve(host, port):
        names.append((host, port))
        return ["93.184.216.34"]

    monkeypatch.setattr(security, "_resolve_hostname", resolve)
    url = "https://faß.example/"
    security.validate_resolved_addresses(url)
    assert asyncio.run(security.resolve_safe_address(url)) == ("93.184.216.34", 443)
    assert names == [("xn--fa-hia.example", 443)] * 2
