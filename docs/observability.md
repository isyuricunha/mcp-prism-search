# Operational observability

Prism emits content-free JSON events to stderr. Standard output remains
reserved for MCP STDIO protocol traffic. With the HTTP dashboard enabled,
the same operational events are stored in the private state directory
under logs/events.jsonl (10 MiB per file, three files including the active
file). The directory and files must be private (0700/0600). Use a separate
state directory for each server process; rotating files are single-writer.
POSIX instances acquire a nonblocking exclusive writer lock; a second instance
falls back to stderr instead of corrupting the first instance’s history.
No file history is created for headless STDIO. If the history cannot be
opened, stderr remains available and the dashboard reports no history.

Events include UTC timestamp, severity, operation, generated request ID,
provider, outcome, duration, result count and HTTP status where available.
The same request ID groups attempts spawned by a tool or dashboard
operation, including concurrent searches. Starting and completed events
are separate. No query, fetched URL, result text, answer, credential,
authorization header, cookie, raw exception or upstream response is
recorded in these events. Unknown outcomes are normalized to error.
Dependency warnings are reduced to generic diagnostic metadata on stderr;
they are not persisted to the dashboard history. HTTP access logs are
disabled to avoid logging query parameters. This is not a query audit log.
The ordinary search cache still contains cached retrieval data.

Set MCP_LOG_LEVEL to DEBUG, INFO (default), WARNING, ERROR or CRITICAL.
The dashboard Logs page polls every five seconds while visible, with
severity, provider, period and metadata search filters. It shows the latest
200 matching entries; JSON export contains exactly that visible snapshot.
The authenticated GET /api/logs API supports level, provider, period
(all/hour/day/week), search (100 chars max), and limit (1–500). No raw file
access is exposed. Disabling live updates retains the current snapshot.

Docker Compose limits container stdout/stderr logs to three 10 MB files
as well; this is separate from the 30 MiB local event history. Existing
containers need recreation to apply Docker logging settings.

## Optional OpenTelemetry traces

Disabled by default. No external collector or SDK is needed for local logs.
Use the optional telemetry extra for a Python install:
```sh
pip install '.[telemetry]'
```
For a Docker source build set PRISM_OTEL=1 (build argument; Compose forwards
it). Then explicitly set:
```dotenv
MCP_OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318
```
Or use OTEL_EXPORTER_OTLP_TRACES_ENDPOINT with the full trace ingestion URL.
Only manually instrumented operation spans and sanitized event attributes
are exported, not automatic HTTP instrumentation, payloads or exception
stack traces. The batch exporter has a five-second transport timeout;
collector failures do not retry provider requests. Configure a trusted
collector and TLS for remote transport. The base Docker image does not
bundle exporter dependencies. No metrics or log export to OTLP is enabled.

References: [MCP logging migration](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/),
[Anthropic research observability](https://www.anthropic.com/research/multi-agent-research-system),
[Python logging cookbook](https://docs.python.org/3/howto/logging-cookbook.html),
[OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/).

Log history reports writer health separately from availability. The panel can
filter all retained events by request ID, resumes polling when visible, and
uses the last applied metadata filter rather than unfinished search text.
