# syntax=docker/dockerfile:1
# build context = リポジトリ root(共有 package を含めるため)
FROM python:3.12.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

# unstructured[all-docs] は poppler / tesseract 等の OS 依存を要する。
# cv2 は headless 版(pyproject)を入れるため、GUI 用の libgl1 / libglib2.0-0 は入れない(#310)。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr tesseract-ocr-jpn \
    && rm -rf /var/lib/apt/lists/*

COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/parsers/unstructured
# uv.lock も入れて --locked で同期する(lock と違う依存を image へ入れない)。
COPY services/parsers/unstructured/pyproject.toml services/parsers/unstructured/uv.lock services/parsers/unstructured/README.md ./
RUN uv sync --locked --no-install-project \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache \
    && chown -R appuser:appuser /tmp/uv-cache /build

COPY --chown=appuser:appuser services/parsers/unstructured/app ./app

# GUI の OS ライブラリなしで cv2(unstructured-inference が使う)を import できることを build で確かめる。
RUN uv run --no-sync python -c "import cv2"

EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-300} --graceful-timeout 30 --access-logfile - --error-logfile -"]
