"""Content-free operational events; never serialize request arguments or exceptions."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import re
import stat
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import monotonic
from typing import get_type_hints
from uuid import uuid4

from opentelemetry import trace

MAX_BYTES = 10 * 1024 * 1024
FILES = 3
MAX_EVENTS = 500
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
OPERATIONS = frozenset({
    "web_search", "web_answer", "web_research", "vertical_search", "ddg_search",
    "web_fetch", "ddg_deep_search", "cache_stats", "cache_prune", "cache_clear",
    "panel_search", "panel_native", "provider_update", "settings_update",
    "defaults_update", "panel_login", "panel_logout", "server_start", "server_stop",
})
EVENTS = frozenset({"operation", "provider", "cache", "lifecycle", "diagnostic", "auth"})
OUTCOMES = frozenset({
    "ok", "empty", "error", "timeout", "cancelled", "hit", "miss",
    "denied", "started", "stopped", "limited", "planned",
})
request_id: ContextVar[str | None] = ContextVar("prism_request_id", default=None)
_logger = logging.getLogger("prism.events")
_logger.propagate = False
_logger.addHandler(logging.NullHandler())
_history: PrivateRotatingHandler | None = None
_writer_lock = None
_tracer = trace.NoOpTracer()
_otel_provider = None
_logging_state = []
_ID = re.compile(r"[0-9a-f]{32}")


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            os.close(fd)
            raise PermissionError("Log file must be private")
        return os.fdopen(fd, "a", encoding="utf-8")

    def handleError(self, record):  # noqa: N802 - logging API
        # Logging must never break a tool or print the original LogRecord.
        pass


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        # Dependency messages, exception strings, URLs and arbitrary args
        # are never included, even in DEBUG mode.
        return json.dumps({
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname if record.levelname in LEVELS else "INFO",
            "event": "diagnostic", "outcome": "error" if record.levelno >= 40 else "ok",
            "component": "dependency",
        }, separators=(",", ":"))


def configure(level: str, directory: Path | None = None):
    """One writer per HTTP instance; STDIO uses stderr only."""
    global _history, _writer_lock
    close()
    _logger.setLevel(LEVELS[level])
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(stream)
    if directory is not None:
        try:
            directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.mkdir(mode=0o700, exist_ok=True)
            if (directory.parent.is_symlink() or directory.parent.stat().st_mode & 0o077
                    or directory.is_symlink() or directory.stat().st_mode & 0o077):
                raise PermissionError("Log directory must be private")
            for suffix in ("", ".1", ".2"):
                path = directory / ("events.jsonl" + suffix)
                if path.is_symlink() or (path.exists() and (
                    not path.is_file() or path.stat().st_mode & 0o077
                )):
                    raise PermissionError("Invalid log file")
            _writer_lock = _lock_writer(directory)
            _history = PrivateRotatingHandler(
                directory / "events.jsonl", maxBytes=MAX_BYTES, backupCount=FILES - 1,
                encoding="utf-8",
            )
            _history.setFormatter(logging.Formatter("%(message)s"))
            _logger.addHandler(_history)
        except OSError:
            if _writer_lock is not None:
                _writer_lock.close()
                _writer_lock = None
            emit("diagnostic", "error", level="WARNING")
    # Do not rely on regex redaction of arbitrary upstream messages.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(DiagnosticFormatter())
    root = logging.getLogger()
    _logging_state.append((root, list(root.handlers), root.level, root.propagate))
    root.handlers[:] = [handler]
    root.setLevel(max(logging.WARNING, LEVELS[level]))
    prefixes = ("httpx", "httpcore", "ddgs", "mcp", "uvicorn", "opentelemetry")
    names = set(prefixes) | {
        name for name in logging.Logger.manager.loggerDict
        if name.startswith(tuple(p + "." for p in prefixes))
    }
    for name in sorted(names):
        logger = logging.getLogger(name)
        _logging_state.append((logger, list(logger.handlers), logger.level, logger.propagate))
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.WARNING)


def _lock_writer(directory):
    try:
        import fcntl
    except ImportError:
        # On non-POSIX systems the operator must use a unique state directory.
        return None
    fd = os.open(directory / ".writer.lock", os.O_CREAT | os.O_RDWR
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise PermissionError("Log lock must be private")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return os.fdopen(fd, "r+b")
    except OSError:
        os.close(fd)
        raise


def configure_otel():
    """Optional exporter, no network traffic unless explicitly enabled."""
    global _tracer, _otel_provider
    if os.getenv("MCP_OTEL_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return
    if not (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")):
        raise RuntimeError("Explicit OTLP endpoint required for MCP_OTEL_ENABLED")
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        raise RuntimeError("Install the optional telemetry extra to export traces") from None
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or (
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/") + "/v1/traces"
    )
    _otel_provider = TracerProvider(
        resource=Resource({"service.name": "mcp-prism-search"}),
    )
    _otel_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=5)),
    )
    _tracer = _otel_provider.get_tracer("mcp-prism-search")


def close():
    global _history, _otel_provider, _tracer, _writer_lock
    if _otel_provider is not None:
        _otel_provider.shutdown()
        _otel_provider = None
        _tracer = trace.NoOpTracer()
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
        handler.close()
    _logger.addHandler(logging.NullHandler())
    _history = None
    if _writer_lock is not None:
        _writer_lock.close()
        _writer_lock = None
    for logger, handlers, level, propagate in reversed(_logging_state):
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
    _logging_state.clear()


def _provider(value):
    from mcp_prism_search import providers

    if not isinstance(value, str):
        return None
    base, _, suffix = value.partition(":")
    if value in providers.SPECS:
        return value
    if base in providers.SPECS and suffix in {"answer", "research"}:
        return value
    return None


def _outcome(value):
    if isinstance(value, str) and value in OUTCOMES:
        return value, None
    match = re.fullmatch(r"HTTP ([1-5][0-9]{2})", value) if isinstance(value, str) else None
    if match:
        code = int(match[1])
        return ("limited" if code == 429 else "error"), code
    return "error", None


def _safe_row(row):
    if not isinstance(row, dict):
        return None
    event, level = row.get("event"), row.get("level")
    if not isinstance(event, str) or not isinstance(level, str):
        return None
    if event not in EVENTS or level not in LEVELS:
        return None
    try:
        timestamp = datetime.fromisoformat(row["timestamp"])
        if timestamp.tzinfo is None:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    outcome, code = _outcome(row.get("outcome"))
    data = {"timestamp": timestamp.astimezone(UTC).isoformat(timespec="milliseconds"),
            "level": level, "event": event, "outcome": outcome}
    operation = row.get("operation")
    if isinstance(operation, str) and operation in OPERATIONS:
        data["operation"] = operation
    provider = _provider(row.get("provider"))
    if provider:
        data["provider"] = provider
    rid = row.get("request_id")
    if isinstance(rid, str) and _ID.fullmatch(rid):
        data["request_id"] = rid
    for key in ("duration_ms", "result_count", "status_code"):
        value = row.get(key)
        if (type(value) is int and 0 <= value <= 10**9
                and (key != "status_code" or 100 <= value <= 599)):
            data[key] = value
    if code:
        data["status_code"] = code
    return data


def emit(event, outcome, *, level=None, operation=None, provider=None, elapsed=None,
         result_count=None):
    try:
        outcome, status = _outcome(outcome)
        row = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event, "outcome": outcome,
            "level": level or ("WARNING" if outcome in {"error", "timeout", "limited", "denied"}
                               else "DEBUG" if event == "cache" else "INFO"),
            "operation": operation, "provider": provider, "request_id": request_id.get(),
            "status_code": status, "result_count": result_count,
        }
        if isinstance(elapsed, (float, int)) and math.isfinite(elapsed):
            row["duration_ms"] = max(0, min(10**9, round(elapsed * 1000)))
        safe = _safe_row(row)
        if safe is None:
            return
        _logger.log(LEVELS[safe["level"]], json.dumps(safe, separators=(",", ":")))
        span = trace.get_current_span()
        if _otel_provider is not None and span.is_recording():
            span.add_event("prism." + event, {
                "prism." + key: val for key, val in safe.items()
                if key not in {"timestamp", "request_id"}
            })
    except Exception:  # noqa: BLE001 - telemetry is never on the critical path.
        pass


def operation(name):
    if name not in OPERATIONS:
        raise ValueError("Unknown operation")

    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            token = request_id.set(request_id.get() or uuid4().hex)
            start = monotonic()
            outcome, count = "error", None
            try:
                # Disable automatic exception capture: exception messages can
                # contain query text, URLs, upstream payloads or credentials.
                with _tracer.start_as_current_span(
                    "prism." + name, record_exception=False, set_status_on_exception=False,
                ) as span:
                    try:
                        emit("operation", "started", operation=name)
                        result = await fn(*args, **kwargs)
                        status = getattr(result, "status_code", 200)
                        outcome = (
                            "ok" if status < 400 and not getattr(result, "error", None)
                            and getattr(result, "success", True)
                            and not (isinstance(result, dict) and result.get("error"))
                            else "denied" if status in {401, 403} else "error"
                        )
                        rows = getattr(result, "results", None)
                        if isinstance(rows, list):
                            count = len(rows)
                        return result
                    except asyncio.CancelledError:
                        outcome = "cancelled"
                        raise
                    finally:
                        emit("operation", outcome, operation=name,
                             level="ERROR" if outcome == "error" else None,
                             elapsed=monotonic() - start, result_count=count)
                        if _otel_provider is not None and span.is_recording():
                            span.set_attribute("prism.outcome", outcome)
                            if outcome == "error":
                                span.set_status(trace.Status(trace.StatusCode.ERROR))
            finally:
                request_id.reset(token)

        wrapped.__signature__ = inspect.signature(fn, eval_str=True)
        wrapped.__annotations__ = get_type_hints(fn, include_extras=True)
        return wrapped
    return decorate


def history(*, level=None, provider=None, since=None, search="", limit=200):
    """Return a bounded newest-first sanitized snapshot, never raw file content."""
    handler = _history
    if handler is None:
        return {"enabled": False, "events": [], "limit": limit}
    rows = []
    cutoff = datetime.fromtimestamp(since, UTC) if since is not None else None
    term = search.casefold()
    handles = []
    handler.acquire()
    try:
        for suffix in ("", ".1", ".2"):
            path = Path(handler.baseFilename + suffix)
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
                mode = os.fstat(fd).st_mode
                if not stat.S_ISREG(mode) or mode & 0o077:
                    os.close(fd)
                    continue
                handles.append(os.fdopen(fd, "rb"))
            except OSError:
                continue
    finally:
        handler.release()
    try:
        for handle in handles:
            for raw in _reverse_lines(handle):
                try:
                    row = _safe_row(json.loads(raw))
                except (ValueError, TypeError, OverflowError):
                    continue
                if not row or (level and row["level"] != level):
                    continue
                if provider and row.get("provider", "").removesuffix(":answer").removesuffix(
                    ":research"
                ) != provider:
                    continue
                if cutoff and datetime.fromisoformat(row["timestamp"]) < cutoff:
                    continue
                if term and term not in json.dumps(row).casefold():
                    continue
                rows.append(row)
                if len(rows) >= min(MAX_EVENTS, max(1, limit)):
                    return {"enabled": True, "events": rows, "limit": limit}
    finally:
        for handle in handles:
            handle.close()
    return {"enabled": True, "events": rows, "limit": limit}


def _reverse_lines(handle):
    """Read bounded files from the end, without accumulating huge malformed lines."""
    handle.seek(0, os.SEEK_END)
    pos = handle.tell()
    floor = max(0, pos - MAX_BYTES)
    carry = b""
    discard = False
    while pos > floor:
        size = min(65536, pos - floor)
        pos -= size
        handle.seek(pos)
        parts = handle.read(size).split(b"\n")
        tail = parts.pop()
        if not discard and len(tail) + len(carry) <= 4096 and tail + carry:
            yield tail + carry
        for line in reversed(parts[1:]):
            if 0 < len(line) <= 4096:
                yield line
        if parts:
            carry = parts[0]
            discard = len(carry) > 4096
        else:
            carry = b""
            discard = True
    if carry and not discard and floor == 0:
        yield carry
