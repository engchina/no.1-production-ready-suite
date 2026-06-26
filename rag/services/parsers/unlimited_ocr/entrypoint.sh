#!/bin/sh
# 同一コンテナで SGLang(OpenAI 互換)と parser FastAPI を起動する。
# SGLang は 127.0.0.1:10000 で内部待受し、外部には parser の /parse だけを公開する。
set -eu

: "${UNLIMITED_OCR_MODEL_ID:=baidu/Unlimited-OCR}"
: "${UNLIMITED_OCR_SGLANG_MODEL:=Unlimited-OCR}"
: "${UNLIMITED_OCR_SGLANG_ATTENTION_BACKEND:=flashinfer}"
: "${UNLIMITED_OCR_SGLANG_MEM_FRACTION_STATIC:=0.8}"
: "${UNLIMITED_OCR_SGLANG_CONTEXT_LENGTH:=32768}"
: "${UNLIMITED_OCR_SGLANG_EXTRA_ARGS:=}"

# shellcheck disable=SC2086  # EXTRA_ARGS は運用チューニング用に意図的に分割展開する。
python3 -m sglang.launch_server \
    --model "$UNLIMITED_OCR_MODEL_ID" \
    --served-model-name "$UNLIMITED_OCR_SGLANG_MODEL" \
    --trust-remote-code \
    --attention-backend "$UNLIMITED_OCR_SGLANG_ATTENTION_BACKEND" \
    --page-size 1 \
    --mem-fraction-static "$UNLIMITED_OCR_SGLANG_MEM_FRACTION_STATIC" \
    --context-length "$UNLIMITED_OCR_SGLANG_CONTEXT_LENGTH" \
    --enable-custom-logit-processor \
    --disable-overlap-schedule \
    --skip-server-warmup \
    --host 127.0.0.1 \
    --port 10000 \
    $UNLIMITED_OCR_SGLANG_EXTRA_ARGS &

exec gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}" \
    --timeout "${GUNICORN_TIMEOUT:-1260}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
