# 本番は Gunicorn + UvicornWorker。build context は workspace root を想定する:
#   docker build -f no.1-production-ready-nl2sql/backend/Dockerfile .
# 共有 backend_core（path 依存）も同梱できるようにする。
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ARG APP_DIR=no.1-production-ready-nl2sql
ARG PLATFORM_DIR=no.1-production-ready-platform

# 依存解決（uv.lock を尊重）。
COPY ${APP_DIR}/backend/pyproject.toml ${APP_DIR}/backend/uv.lock ./${APP_DIR}/backend/
COPY ${PLATFORM_DIR}/packages/backend_core ./${PLATFORM_DIR}/packages/backend_core

WORKDIR /app/${APP_DIR}/backend
RUN uv sync --locked --no-dev

COPY ${APP_DIR}/backend/ ./

EXPOSE 8000
CMD ["uv", "run", "gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
