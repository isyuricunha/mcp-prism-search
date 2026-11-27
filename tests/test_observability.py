"""Tests for privacy, bounded retention, request correlation and safe log access."""
import asyncio
import json
import logging

import pytest
from starlette.testclient import TestClient

from mcp_prism_search import observability as obs
from mcp_prism_search import providers
from mcp_prism_search.panel import create_app
from mcp_prism_search.panel_store import PanelStore


@pytest.fixture
def logs(tmp_path, monkeypatch):
    providers.build_registry()
    monkeypatch.setattr(obs, "MAX_BYTES", 4096)
    tmp_path.chmod(0o700)
    directory = tmp_path / "logs"
    obs.configure("DEBUG", directory)
    try:
        yield directory
    finally:
        obs.close()


def test_structured_logs_no_content_and_bounded_rotation(logs, capsys):
    secret = "private-key-query-response-url"
    for i in range(100):
        obs.emit("provider", "HTTP 429", provider="tavily", elapsed=0.13, result_count=i)
        obs.emit("provider", secret, provider=secret)
    logging.getLogger("httpx").error("HTTP Request: https://example.org/?q=%s", secret)
    paths = list(logs.glob("events.jsonl*"))
    assert len(paths) == 3
    assert all(p.stat().st_size <= 4096 and not p.stat().st_mode & 0o077 for p in paths)
    assert secret not in "".join(p.read_text() for p in paths)
    assert secret not in capsys.readouterr().err
    data = obs.history(level="WARNING", provider="tavily", limit=5)
    assert len(data["events"]) == 5
    assert [r["result_count"] for r in data["events"]] == [99, 98, 97, 96, 95]
    assert all(r["status_code"] == 429 and r["duration_ms"] == 130 for r in data["events"])


def test_concurrent_request_correlation_and_no_exception_content(logs):
    @obs.operation("web_search")
    async def run(value):
        obs.emit("provider", "ok", provider="keenable", result_count=value)
        await asyncio.sleep(0)
        obs.emit("provider", "ok", provider="keenable", result_count=value)
        if value == 2:
            raise RuntimeError("private payload")
        return None

    async def both():
        return await asyncio.gather(run(1), run(2), return_exceptions=True)

    asyncio.run(both())
    rows = obs.history()["events"]
    ids = {n: {r["request_id"] for r in rows if r.get("result_count") == n}
           for n in (1, 2)}
    assert all(len(v) == 1 for v in ids.values()) and ids[1] != ids[2]
    assert len([r for r in rows if r["event"] == "operation"]) == 4
    assert "private payload" not in json.dumps(rows)
    assert obs.request_id.get() is None


def test_history_sanitizes_corrupted_and_forged_fields(logs):
    obs.emit("provider", "ok", provider="keenable")
    path = logs / "events.jsonl"
    row = obs.history()["events"][0]
    with path.open("a") as f:
        f.write("malformed\n")
        f.write(json.dumps(dict(row, event=[])) + "\n")
        f.write(json.dumps(dict(row, provider="private", query="secret",
                                request_id="not-an-id", outcome="secret")) + "\n")
    rows = obs.history()["events"]
    assert len(rows) == 2
    assert rows[0]["outcome"] == "error" and "provider" not in rows[0]
    assert "secret" not in json.dumps(rows)


def test_log_api_requires_login_and_validates_filters(logs, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("test")
    store = PanelStore(tmp_path / "state")
    app = create_app("test-password-123", store, assets)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/logs").status_code == 401
        headers = {"Origin": "http://localhost", "X-Prism-Admin": "1"}
        assert client.post("/api/login", headers=headers,
                           json={"password": "test-password-123"}).status_code == 200
        obs.emit("provider", "HTTP 401", provider="exa", elapsed=0.5)
        response = client.get("/api/logs?level=WARNING&provider=exa&period=hour&limit=5")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["events"][0]["status_code"] == 401
        for param in ("level=SECRET", "provider=unknown", "limit=501", "period=invalid"):
            assert client.get("/api/logs?" + param).status_code == 400
        assert client.get("/api/logs?search=unknown").json()["events"] == []


def test_otel_explicit_endpoint_required(monkeypatch):
    monkeypatch.setenv("MCP_OTEL_ENABLED", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="Explicit OTLP endpoint"):
        obs.configure_otel()



def test_second_history_writer_is_refused(logs):
    pytest.importorskip("fcntl")
    with pytest.raises(BlockingIOError):
        obs._lock_writer(logs)


def test_large_history_reverse_scan_preserves_order(logs, monkeypatch):
    monkeypatch.setattr(obs, "MAX_BYTES", 1024 * 1024)
    # Raise the handler limit for this test so it exercises chunk boundaries.
    obs._history.maxBytes = obs.MAX_BYTES
    for i in range(700):
        obs.emit("provider", "ok", provider="keenable", result_count=i)
    rows = obs.history(limit=500)["events"]
    assert [r["result_count"] for r in rows] == list(range(699, 199, -1))


def test_log_history_survives_deeply_nested_corruption_and_reports_write_failure(logs, monkeypatch):
    obs.emit("provider", "ok", provider="keenable")
    with (logs / "events.jsonl").open("a") as handle:
        handle.write("[" * 1500 + "]" * 1500 + "\n")
    assert len(obs.history()["events"]) == 1
    handler = obs._history
    with monkeypatch.context() as patch:
        def fail(record):
            raise OSError("disk unavailable")
        patch.setattr(handler, "shouldRollover", fail)
        obs.emit("provider", "ok", provider="keenable")
        assert obs.history()["healthy"] is False
    obs.emit("provider", "ok", provider="keenable")
    assert obs.history()["healthy"] is True


def test_cancelled_provider_is_logged_without_counting_as_upstream_error(logs, monkeypatch):
    from mcp_prism_search import runtime, search
    from mcp_prism_search.models import SearchRequest

    class Store:
        def record(self, *args):
            pytest.fail("Cancellation must not penalize provider metrics")

    async def cancelled(*args):
        raise asyncio.CancelledError

    previous = runtime.state()
    runtime.install(Store())
    try:
        monkeypatch.setattr(providers, "run_provider", cancelled)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(search._search_one("keenable", SearchRequest(query="test"), 5, 5))
    finally:
        runtime.install(previous)
    assert obs.history()["events"][0]["outcome"] == "cancelled"


def test_exporter_shutdown_failure_does_not_prevent_history_cleanup(logs, monkeypatch):
    class BrokenExporter:
        def shutdown(self):
            raise RuntimeError("unavailable")

    monkeypatch.setattr(obs, "_otel_provider", BrokenExporter())
    obs.close()
    assert obs._history is None
    assert obs._writer_lock is None
    assert obs._otel_provider is None


def test_panel_operation_rate_limit_retains_http_status(logs):
    from starlette.responses import JSONResponse

    @obs.operation("panel_search")
    async def run():
        return JSONResponse({"error": "private"}, status_code=429)

    asyncio.run(run())
    row = obs.history()["events"][0]
    assert row["outcome"] == "limited" and row["status_code"] == 429
    assert row["level"] == "WARNING"


@pytest.mark.parametrize("timestamp", [None, "not-a-date", "2026-11-27T17:00:00", 123])
def test_history_rejects_untrusted_timestamps(logs, timestamp):
    obs.emit("provider", "ok", provider="keenable")
    row = obs.history()["events"][0]
    with (logs / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(dict(row, timestamp=timestamp)) + "\n")
    assert obs.history()["events"] == [row]


def test_history_time_cutoff(logs):
    from datetime import UTC, datetime

    obs.emit("provider", "ok", provider="exa:answer")
    row = obs.history()["events"][0]
    with (logs / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(dict(row, timestamp="2000-01-01T00:00:00+00:00")) + "\n")
    cutoff = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    assert obs.history(provider="exa", since=cutoff)["events"] == [row]


def test_history_private_permissions(tmp_path):
    directory = tmp_path / "logs"
    tmp_path.chmod(0o700)
    directory.mkdir(mode=0o700)
    path = directory / "events.jsonl"
    path.write_text("preserve")
    path.chmod(0o644)
    obs.configure("INFO", directory)
    try:
        assert obs.history()["enabled"] is False
        obs.emit("provider", "ok", provider="exa")
        assert path.read_text() == "preserve"
    finally:
        obs.close()
