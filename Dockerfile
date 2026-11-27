FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS dashboard-build
WORKDIR /ui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY webui/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_CACHE_DIR=/data/cache \
    MCP_TRANSPORT=stdio \
    MCP_WEBUI_ASSETS=/app/webui \
    MCP_WEBUI_DATA_DIR=/data/admin

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=dashboard-build /ui/dist /app/webui
# Local source contexts omit .git. CI passes the verified tag version;
# an unversioned source build is deliberately marked unknown.
# Optional manual OTLP trace export; base image has only the API.
ARG PRISM_OTEL=0
RUN if [ "$PRISM_OTEL" = "1" ]; then \
      pip install --no-cache-dir opentelemetry-sdk==1.44.0 \
          opentelemetry-exporter-otlp-proto-http==1.44.0; \
    fi
ARG PRISM_VERSION=0.0.0+unknown
RUN SETUPTOOLS_SCM_PRETEND_VERSION="$PRISM_VERSION" pip install --no-cache-dir --no-deps . \
    && python -c 'import sys; from importlib.metadata import version; assert version("mcp-prism-search") == sys.argv[1]' "$PRISM_VERSION"

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mcp \
    && mkdir -p /data/cache \
    && chown -R 10001:10001 /data /app

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0) if os.getenv('MCP_TRANSPORT','stdio') == 'stdio' else None; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('MCP_PORT','8000')+'/health', timeout=4).close()" || exit 1

ENTRYPOINT ["python", "-m", "mcp_prism_search.server"]