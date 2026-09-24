#!/usr/bin/env bash
# バックエンドとフロントエンドを同時に起動する。
# どちらかが終了するか Ctrl+C を押すと、両方のプロセスをまとめて停止する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_READY_HOST="${BACKEND_READY_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-3001}"
BACKEND_URL="${BACKEND_URL:-http://${BACKEND_READY_HOST}:${BACKEND_PORT}}"
BACKEND_READY_URL="${BACKEND_READY_URL:-${BACKEND_URL}/api/health}"
BACKEND_READY_TIMEOUT_SECONDS="${BACKEND_READY_TIMEOUT_SECONDS:-90}"

pids=()
_cleaning=0

cleanup() {
  # 再入防止: 最初の Ctrl+C で全トラップを解除し、二度目以降の INT は無視する。
  # (これをしないと cleanup 中の Ctrl+C で再入し「停止しています...」を繰り返す)
  if [ "${_cleaning}" -eq 1 ]; then
    return
  fi
  _cleaning=1
  trap - INT TERM EXIT

  echo ""
  echo "[start-all] 停止しています..."

  # まずプロセスグループへ SIGTERM(子の uvicorn --reload / vite も巻き取る)。
  for pid in "${pids[@]}"; do
    kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
  done

  # 最大 ~3 秒だけ graceful 終了を待ち、残っていれば SIGKILL で即時停止する。
  local waited=0
  while [ "${waited}" -lt 15 ]; do
    local alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        alive=1
      fi
    done
    if [ "${alive}" -eq 0 ]; then
      break
    fi
    sleep 0.2
    waited=$((waited + 1))
  done
  for pid in "${pids[@]}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 -- "-${pid}" 2>/dev/null || kill -9 "${pid}" 2>/dev/null || true
    fi
  done

  echo "[start-all] 停止しました。"
  exit 130
}
# INT/TERM は即時停止、EXIT は正常終了時の後始末(いずれも cleanup を 1 度だけ実行)。
trap cleanup INT TERM
trap cleanup EXIT

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
