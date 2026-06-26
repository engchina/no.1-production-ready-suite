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
    # 焼き込み/host マウントのキャッシュ先を appuser 配下に固定する(dev の bind 先と一致)。
    HF_HOME=/home/appuser/.cache/huggingface

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/parsers/mineru
COPY services/parsers/mineru/pyproject.toml services/parsers/mineru/README.md ./
# GPU torch を含むため重い。CUDA host の build 環境で実行する。
RUN uv sync --no-install-project --python python3.12 \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache \
    && chown -R appuser:appuser /tmp/uv-cache /build

COPY --chown=appuser:appuser services/parsers/mineru/app ./app

EXPOSE 8000
USER appuser
# MinerU のモデル一式をビルド時に焼き込む(prod・配布向け)。dev は `--build-arg BAKE_MODELS=0`
# で省き host マウントのキャッシュを使う。ミラーは HF_ENDPOINT build-arg、token は runtime env のみ(image 履歴へ残さない)。
# ponytail: MinerU 3.x の公式非対話 DL コマンド。フラグ名が将来変わったら当該行を直す。
ENV MINERU_MODEL_SOURCE=huggingface
ARG BAKE_MODELS=1
ARG HF_ENDPOINT=
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        HF_ENDPOINT="$HF_ENDPOINT" \
        uv run --no-sync mineru-models-download -s huggingface -m all; \
    fi
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
# OCR は遅いため timeout を長めに。GPU 1 枚なら workers=1。
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-600} --graceful-timeout 30 --access-logfile - --error-logfile -"]
