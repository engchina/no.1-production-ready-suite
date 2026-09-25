# 本番は Gunicorn + UvicornWorker。Compose の build context は workspace root。
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ネイティブ拡張を sdist から build する依存に備えて build tool を入れる。
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends build-essential cargo libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 依存解決（uv.lock を尊重）。
COPY agent/backend/pyproject.toml \
     agent/backend/uv.lock ./backend/
COPY platform/packages/backend_core \
     /platform/packages/backend_core
COPY platform/packages/system_settings_backend \
     /platform/packages/system_settings_backend

WORKDIR /app/backend
RUN uv sync --locked --no-dev

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

COPY agent/backend/ ./

EXPOSE 8000
# Runtime の repository は process の中の状態を正として書き戻すため、worker は 1 つに固定する（#165）。
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "1"]
