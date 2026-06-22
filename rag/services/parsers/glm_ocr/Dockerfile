# syntax=docker/dockerfile:1
# build context = リポジトリ root。GPU(CUDA)必須のため CUDA runtime ベースを使う。
# Ubuntu 24.04 は python3.12 を同梱(rag_parser_core は PEP 695 type 文で 3.12+ 必須)。
# nvidia/cuda の ubuntu24.04 cudnn-runtime は 12.6.0 以降のみ提供(12.4.x は ubuntu24.04 タグ無し)。
# torch は RTX 50xx(sm_120) 対応のため CUDA 13 系 wheel を解決できる版を使う。
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_CACHE_DIR=/tmp/uv-cache \
    # GLM-OCR モデルの HuggingFace キャッシュ。永続化したい場合はこのパスへ volume を割り当てる。
    HF_HOME=/home/appuser/.cache/huggingface

# git は一部 HF モデルの custom code 取得に使われることがある
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/parsers/glm_ocr
COPY services/parsers/glm_ocr/pyproject.toml services/parsers/glm_ocr/README.md ./
# GPU torch + transformers を含むため重い。CUDA host の build 環境で実行する。
RUN uv sync --no-install-project --python python3.12 \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache /home/appuser/.cache/huggingface \
    && chown -R appuser:appuser /tmp/uv-cache /build /home/appuser/.cache

COPY --chown=appuser:appuser services/parsers/glm_ocr/app ./app

EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
# OCR は遅いため timeout を長めに。GPU 1 枚なら workers=1。
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-600} --graceful-timeout 30 --access-logfile - --error-logfile -"]
