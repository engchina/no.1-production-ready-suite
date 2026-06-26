#!/bin/sh
# 同一コンテナで vLLM(OpenAI 互換)と parser FastAPI を起動する。
# - vLLM は 127.0.0.1:8080 で内部待受(外部公開しない)。
# - parser は 0.0.0.0:8000 で公開。registry が localhost の vLLM へ HTTP で OCR を委譲する。
# parser の runtime_health が vLLM /health を監視するため、モデルロード完了前は
# サービス管理 UI 上「推論サーバー未起動」として可視化される。
#
# ponytail: vLLM は背景プロセス、parser を exec で PID1 にする。docker stop 時は
# parser が graceful 終了→コンテナ停止で vLLM も落ち GPU を解放する(vLLM の graceful
# drain はしない=ステートレス推論なので許容)。
set -eu

: "${GLM_OCR_MODEL:=zai-org/GLM-OCR}"
: "${GLM_OCR_SERVED_MODEL_NAME:=glm-ocr}"
: "${GLM_OCR_GPU_MEMORY_UTILIZATION:=0.9}"
: "${GLM_OCR_TENSOR_PARALLEL_SIZE:=1}"
# 追加チューニング(例: 投機的デコード `--speculative-config.method mtp ...`)は
# GLM_OCR_VLLM_EXTRA_ARGS で渡す(再ビルド不要の calibration knob)。
: "${GLM_OCR_VLLM_EXTRA_ARGS:=}"

# shellcheck disable=SC2086  # GLM_OCR_VLLM_EXTRA_ARGS は意図的に分割展開する
vllm serve "$GLM_OCR_MODEL" \
    --served-model-name "$GLM_OCR_SERVED_MODEL_NAME" \
    --trust-remote-code \
    --host 127.0.0.1 \
    --port 8080 \
    --tensor-parallel-size "$GLM_OCR_TENSOR_PARALLEL_SIZE" \
    --gpu-memory-utilization "$GLM_OCR_GPU_MEMORY_UTILIZATION" \
    $GLM_OCR_VLLM_EXTRA_ARGS &

exec gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}" \
    --timeout "${GUNICORN_TIMEOUT:-900}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile -
