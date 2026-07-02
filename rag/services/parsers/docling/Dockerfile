# syntax=docker/dockerfile:1
# build context = リポジトリ root(共有 package を含めるため)
FROM python:3.12.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    # 焼き込み/named volume のキャッシュ先を appuser 配下に固定する。
    HF_HOME=/home/appuser/.cache/huggingface

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

# 共有 contract package と service を repo 相対レイアウトで配置し、
# pyproject の path source(../../../packages/rag_parser_core)を解決可能にする。
COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/parsers/docling
COPY services/parsers/docling/pyproject.toml services/parsers/docling/README.md ./
RUN uv sync --no-install-project \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache /home/appuser/.cache \
    && chown -R appuser:appuser /tmp/uv-cache /home/appuser/.cache /build

COPY --chown=appuser:appuser services/parsers/docling/app ./app

EXPOSE 8000
USER appuser
# docling のレイアウト/OCR モデルをビルド時に取得して焼き込む(実行時 DL を排除)。
RUN uv run --no-sync docling-tools models download
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-300} --graceful-timeout 30 --access-logfile - --error-logfile -"]
