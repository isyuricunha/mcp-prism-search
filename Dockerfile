FROM node:24-bookworm-slim AS dashboard-build
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
RUN pip install --no-cache-dir --no-deps .

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mcp \
    && mkdir -p /data/cache \
    && chown -R 10001:10001 /data /app

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket,sys; s=socket.socket(); s.settimeout(4); s.connect(('127.0.0.1', int(__import__('os').getenv('MCP_PORT','8000'))))" || exit 1

ENTRYPOINT ["python", "-m", "mcp_prism_search.server"]