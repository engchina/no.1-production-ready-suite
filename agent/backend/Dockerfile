# 本番は Gunicorn + UvicornWorker。Compose の build context は workspace root。
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# OCI SDK が cryptography<47 を要求する間は、patched system OpenSSL に対して
# sdist を build する。PyPI wheel 同梱 OpenSSL の GHSA-537c-gmf6-5ccf を回避する。
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential cargo libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 依存解決（uv.lock を尊重）。
COPY no.1-production-ready-agent/backend/pyproject.toml \
     no.1-production-ready-agent/backend/uv.lock ./backend/
COPY no.1-production-ready-platform/packages/backend_core \
     /no.1-production-ready-platform/packages/backend_core

WORKDIR /app/backend
RUN uv sync --locked --no-dev --no-binary-package cryptography

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/backend/.venv/bin:$PATH"

# builder と同じ配布元の最新 security update を適用し、動的リンク先 OpenSSL を更新する。
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY --from=builder /app/backend/.venv ./.venv

COPY no.1-production-ready-agent/backend/ ./

EXPOSE 8000
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "2"]
