#!/usr/bin/env bash
# バックエンドとフロントエンドを同時に起動する。
# どちらかが終了するか Ctrl+C を押すと、両方のプロセスをまとめて停止する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

echo "[start-all] バックエンドを起動します..."
"${SCRIPT_DIR}/start-backend.sh" &
pids+=("$!")

echo "[start-all] フロントエンドを起動します..."
"${SCRIPT_DIR}/start-frontend.sh" &
pids+=("$!")

echo "[start-all] backend: http://localhost:8000/docs  frontend: http://localhost:3000"
echo "[start-all] 停止するには Ctrl+C を押してください。"

# いずれかのプロセスが終了したら cleanup が走る
wait -n
