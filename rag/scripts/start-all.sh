#!/usr/bin/env bash
# バックエンドとフロントエンドを同時に起動する。
# どちらかが終了するか Ctrl+C を押すと、両方のプロセスをまとめて停止する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_READY_HOST="${BACKEND_READY_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_URL="${BACKEND_URL:-http://${BACKEND_READY_HOST}:${BACKEND_PORT}}"
BACKEND_READY_URL="${BACKEND_READY_URL:-${BACKEND_URL}/api/health}"
BACKEND_READY_TIMEOUT_SECONDS="${BACKEND_READY_TIMEOUT_SECONDS:-90}"

pids=()

cleanup() {
  echo ""
  echo "[start-all] 停止しています..."
  for pid in "${pids[@]}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      # プロセスグループごと停止する(子の uvicorn / vite も巻き取る)
      kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 各スクリプトを独立したプロセスグループで起動する
set -m

wait_for_backend_ready() {
  local backend_pid="$1"
  local deadline=$((SECONDS + BACKEND_READY_TIMEOUT_SECONDS))

  if ! command -v curl >/dev/null 2>&1; then
    echo "[start-all] curl が見つからないため backend readiness を確認できません。" >&2
    return 1
  fi

  echo "[start-all] backend readiness を待機します: ${BACKEND_READY_URL}"
  while true; do
    if curl -fsS --max-time 2 "${BACKEND_READY_URL}" >/dev/null 2>&1; then
      echo "[start-all] backend ready を確認しました。"
      return 0
    fi

    if ! kill -0 "${backend_pid}" 2>/dev/null; then
      echo "[start-all] backend プロセスが ready 前に終了しました。" >&2
      return 1
    fi

    if [ "${SECONDS}" -ge "${deadline}" ]; then
      echo "[start-all] backend readiness の待機がタイムアウトしました (${BACKEND_READY_TIMEOUT_SECONDS}s)。" >&2
      return 1
    fi

    sleep 1
  done
}

echo "[start-all] バックエンドを起動します..."
HOST="${BACKEND_HOST}" PORT="${BACKEND_PORT}" "${SCRIPT_DIR}/start-backend.sh" &
pids+=("$!")
backend_pid="$!"

wait_for_backend_ready "${backend_pid}"

echo "[start-all] フロントエンドを起動します..."
PORT="${FRONTEND_PORT}" BACKEND_URL="${BACKEND_URL}" "${SCRIPT_DIR}/start-frontend.sh" &
pids+=("$!")

echo "[start-all] backend: ${BACKEND_URL}/docs  frontend: http://localhost:${FRONTEND_PORT}"
echo "[start-all] 停止するには Ctrl+C を押してください。"

# いずれかのプロセスが終了したら cleanup が走る
wait -n
