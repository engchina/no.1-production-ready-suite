# syntax=docker/dockerfile:1
# build context = リポジトリ root。GPU(CUDA)必須のため CUDA runtime ベースを使う。
# nvidia/cuda の ubuntu24.04 cudnn-runtime は 12.6.0 以降のみ提供(12.4.x は ubuntu24.04 タグ無し)。
# torch は RTX 50xx(sm_120) 対応のため CUDA 13 系 wheel を解決できる版を使う。
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_CACHE_DIR=/tmp/uv-cache

# git は dots.ocr の GitHub install に必要
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/parsers/dots_ocr
COPY services/parsers/dots_ocr/pyproject.toml services/parsers/dots_ocr/README.md ./
RUN uv sync --no-install-project --python python3.12 \
    && dots_model_file="$(find /tmp/uv-cache/git-v0/checkouts -path '*/dots_ocr/model/inference.py' -print -quit)" \
    && test -n "$dots_model_file" \
    && dots_pkg_dir="$(.venv/bin/python -c 'import importlib.util, pathlib; spec = importlib.util.find_spec("dots_ocr"); print(pathlib.Path(spec.origin).parent)')" \
    && mkdir -p "$dots_pkg_dir/model" \
    && cp -R "$(dirname "$dots_model_file")"/. "$dots_pkg_dir/model"/ \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache \
    && chown -R appuser:appuser /tmp/uv-cache /build

COPY --chown=appuser:appuser services/parsers/dots_ocr/app ./app

EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-600} --graceful-timeout 30 --access-logfile - --error-logfile -"]
