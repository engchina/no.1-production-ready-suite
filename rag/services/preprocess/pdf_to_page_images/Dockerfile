# syntax=docker/dockerfile:1
# build context = リポジトリ root(共有 package を含めるため)
FROM python:3.12.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache

# CJK フォント(ラスタライズ後の表示確認・OCR 経路の互換のため)。PyMuPDF は pip(uv)。
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

# 共有 contract package と service を repo 相対レイアウトで配置し、
# pyproject の path source(../../../packages/rag_parser_core)を解決可能にする。
COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/preprocess/pdf_to_page_images
COPY services/preprocess/pdf_to_page_images/pyproject.toml ./
RUN uv sync --no-install-project \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache \
    && chown -R appuser:appuser /tmp/uv-cache /build

COPY --chown=appuser:appuser services/preprocess/pdf_to_page_images/app ./app

EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-300} --graceful-timeout 30 --access-logfile - --error-logfile -"]
