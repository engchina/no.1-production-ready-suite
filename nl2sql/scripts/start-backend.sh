#!/usr/bin/env bash
# バックエンド(FastAPI / Uvicorn)を開発モードで起動する。
# - 依存は uv sync で解決する。
# - .env が無い場合でも、デフォルト設定(local)で起動できる。OCI/Oracle 等の
#   サービス固有設定は backend/.env に追加する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8010}"

if ! command -v uv >/dev/null 2>&1; then
  echo "[backend] uv が見つかりません。https://docs.astral.sh/uv/ を参照してインストールしてください。" >&2
  exit 1
fi

# 既に同じポートで起動しているプロセスがあれば停止する
kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    echo "[backend] ポート ${port} を使用中のプロセスを停止します (PID: ${pids//$'\n'/ })..."
    kill ${pids} 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
    if [ -n "${pids}" ]; then
      echo "[backend] 強制停止します (kill -9)..."
      kill -9 ${pids} 2>/dev/null || true
    fi
  fi
}

kill_port "${PORT}"

cd "${BACKEND_DIR}"

echo "[backend] 依存を解決します (uv sync)..."
uv sync

echo "[backend] http://${HOST}:${PORT}/docs で起動します..."
exec uv run uvicorn app.main:app --reload --host "${HOST}" --port "${PORT}"
