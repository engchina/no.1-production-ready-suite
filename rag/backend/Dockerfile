# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

# uv をインストール
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /u01/production-ready-rag /tmp/uv-cache \
    && chown -R appuser:appuser /u01/production-ready-rag /tmp/uv-cache

COPY --chown=appuser:appuser app ./app
RUN chown appuser:appuser /app

EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=3).read()"
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout ${GUNICORN_TIMEOUT:-60} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} --keep-alive ${GUNICORN_KEEP_ALIVE:-5} --access-logfile - --error-logfile -"]
