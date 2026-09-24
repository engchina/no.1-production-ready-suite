# 本番は Gunicorn + UvicornWorker（RAG と統一）。build context = リポジトリ root を想定し、
# 共有 backend_core（path 依存）も同梱できるようにする。
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 依存解決（uv.lock を尊重）。
COPY backend/pyproject.toml backend/uv.lock ./backend/
# 共有 backend_core を path source 解決のため同梱（build context = repo root）。
# COPY ../no.1-production-ready-platform/packages/backend_core ... は CI/Compose 側で配置する。

WORKDIR /app/backend
RUN uv sync --locked --no-dev

COPY backend/ ./

EXPOSE 8000
CMD ["uv", "run", "gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "2"]
