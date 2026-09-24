# syntax=docker/dockerfile:1
# build context = リポジトリ root。GPU(CUDA)必須のため CUDA runtime ベースを使う。
# faster-whisper(CTranslate2)は cudnn を要求するため cudnn-runtime ベース。
# 音声/動画デコードに ffmpeg を導入する。
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_CACHE_DIR=/tmp/uv-cache \
    # 焼き込み/named volume のキャッシュ先を appuser 配下に固定する。
    HF_HOME=/home/appuser/.cache/huggingface

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/

COPY packages/rag_parser_core /build/packages/rag_parser_core
WORKDIR /build/services/parsers/asr
COPY services/parsers/asr/pyproject.toml ./
# GPU 依存(faster-whisper / CTranslate2)を含むため重い。CUDA host の build 環境で実行する。
RUN uv sync --no-install-project --python python3.12 \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/uv-cache /home/appuser/.cache \
    && chown -R appuser:appuser /tmp/uv-cache /home/appuser/.cache /build

COPY --chown=appuser:appuser services/parsers/asr/app ./app

EXPOSE 8000
USER appuser
# モデルをビルド時に焼き込む(prod・配布向け)。dev は `--build-arg BAKE_MODELS=0` で省き
# named volume のキャッシュを使う。別サイズは `--build-arg ASR_MODEL_SIZE=...`、
# ミラーは HF_ENDPOINT build-arg、token は runtime env のみ(image 履歴へ残さない)。
ARG ASR_MODEL_SIZE=large-v3
ENV ASR_MODEL_SIZE=${ASR_MODEL_SIZE}
ARG BAKE_MODELS=1
ARG HF_ENDPOINT=
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        HF_ENDPOINT="$HF_ENDPOINT" \
        uv run --no-sync python -c "from faster_whisper.utils import download_model; download_model('${ASR_MODEL_SIZE}')"; \
    fi
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
# 転写は長尺で遅いため timeout を長めに。GPU 1 枚なら workers=1。
CMD ["sh", "-c", "exec uv run --no-sync gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-900} --graceful-timeout 30 --access-logfile - --error-logfile -"]
