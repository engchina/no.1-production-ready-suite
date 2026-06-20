#!/usr/bin/env bash
# フロントエンド(Vite + React)を開発モードで起動する。
# - node_modules が無い場合のみ npm install を実行する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3001}"

if ! command -v npm >/dev/null 2>&1; then
  echo "[frontend] npm が見つかりません。Node.js をインストールしてください。" >&2
  exit 1
fi

# 既に同じポートで起動しているプロセスがあれば停止する
kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    echo "[frontend] ポート ${port} を使用中のプロセスを停止します (PID: ${pids//$'\n'/ })..."
    kill ${pids} 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
    if [ -n "${pids}" ]; then
      echo "[frontend] 強制停止します (kill -9)..."
      kill -9 ${pids} 2>/dev/null || true
    fi
  fi
}

kill_port "${PORT}"

cd "${FRONTEND_DIR}"

if [ ! -d node_modules ]; then
  echo "[frontend] 依存をインストールします (npm install)..."
  npm install
fi

echo "[frontend] http://localhost:${PORT} で起動します..."
exec npm run dev -- --host "${HOST}" --port "${PORT}"
