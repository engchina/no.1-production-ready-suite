# syntax=docker/dockerfile:1
# build context = リポジトリ root(共有 package rag_parser_core を含めるため)。
# 重い parser 依存は backend に載せない。外部 adapter は parser マイクロサービスへ HTTP 委譲する。
FROM python:3.12.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

# uv をインストール
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends libaio1t64 \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 \
    && rm -rf /var/lib/apt/lists/*

# 共有 contract package を repo 相対レイアウトで配置し、
# backend pyproject の path source を解決可能にする。
COPY packages/rag_parser_core /build/packages/rag_parser_core
COPY packages/rag_pipeline_core /build/packages/rag_pipeline_core
COPY --from=backend_core . /no.1-production-ready-platform/packages/backend_core
WORKDIR /build/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /u01/production-ready-rag /tmp/uv-cache \
    && chown -R appuser:appuser /u01/production-ready-rag /tmp/uv-cache /build

COPY --chown=appuser:appuser backend/app ./app
RUN chown appuser:appuser /build/backend

EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=3).read()"
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout ${GUNICORN_TIMEOUT:-60} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} --keep-alive ${GUNICORN_KEEP_ALIVE:-5} --access-logfile - --error-logfile -"]
