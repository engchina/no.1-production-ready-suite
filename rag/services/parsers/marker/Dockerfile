# syntax=docker/dockerfile:1
# build context = リポジトリ root(共有 package を含めるため)
FROM python:3.12.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    # 焼き込み/host マウントのキャッシュ先を appuser 配下に固定する(dev の bind 先と一致)。
    HF_HOME=/home/appuser/.cache/huggingface

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/parsers/marker
COPY services/parsers/marker/pyproject.toml services/parsers/marker/README.md ./
RUN uv sync --no-install-project \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache \
    && chown -R appuser:appuser /tmp/uv-cache /build

COPY --chown=appuser:appuser services/parsers/marker/app ./app

EXPOSE 8000
USER appuser
# surya モデル一式をビルド時に焼き込む(prod・配布向け)。dev は `--build-arg BAKE_MODELS=0`
# で省き host マウントのキャッシュを使う。ミラーは HF_ENDPOINT build-arg、token は runtime env のみ(image 履歴へ残さない)。
ARG BAKE_MODELS=1
ARG HF_ENDPOINT=
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        HF_ENDPOINT="$HF_ENDPOINT" \
        uv run --no-sync python -c "from marker.models import create_model_dict; create_model_dict()"; \
    fi
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-300} --graceful-timeout 30 --access-logfile - --error-logfile -"]
