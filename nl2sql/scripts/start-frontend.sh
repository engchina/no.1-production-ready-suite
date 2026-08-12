#!/usr/bin/env bash
# フロントエンド(Vite + React)を開発モードで起動する。
# - 共有 UI パッケージを検証・build してから Vite を起動する。
# - node_modules または共有 UI のリンクが無い場合は npm install を実行する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
SHARED_PLATFORM_DIR="${SHARED_PLATFORM_DIR:-${ROOT_DIR}/../no.1-production-ready-platform}"
SHARED_UI_DIR="${SHARED_UI_DIR:-${SHARED_PLATFORM_DIR}/packages/ui}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3001}"

if ! command -v npm >/dev/null 2>&1; then
  echo "[frontend] npm が見つかりません。Node.js をインストールしてください。" >&2
  exit 1
fi

prepare_shared_ui() {
  if [ ! -f "${SHARED_UI_DIR}/package.json" ]; then
    echo "[frontend] 共有 UI パッケージが見つかりません: ${SHARED_UI_DIR}" >&2
    echo "[frontend] no.1-production-ready-platform を NL2SQL リポジトリと同じ親ディレクトリに配置してください。" >&2
    echo "[frontend] 別の場所に配置した場合は SHARED_PLATFORM_DIR または SHARED_UI_DIR を指定してください。" >&2
    return 1
  fi

  if [ ! -f "${SHARED_PLATFORM_DIR}/package.json" ]; then
    echo "[frontend] 共有 platform の package.json が見つかりません: ${SHARED_PLATFORM_DIR}" >&2
    return 1
  fi

  if [ ! -d "${SHARED_PLATFORM_DIR}/node_modules" ]; then
    if [ ! -f "${SHARED_PLATFORM_DIR}/package-lock.json" ]; then
      echo "[frontend] 共有 platform の package-lock.json が見つかりません: ${SHARED_PLATFORM_DIR}" >&2
      return 1
    fi
    echo "[frontend] 共有 UI の依存をインストールします (npm ci)..."
    (
      cd "${SHARED_PLATFORM_DIR}"
      npm ci
    )
  fi

  echo "[frontend] 共有 UI パッケージをビルドします..."
  (
    cd "${SHARED_PLATFORM_DIR}"
    npm run build --workspace @engchina/production-ready-ui
  )

  local artifact
  for artifact in index.js index.d.ts tokens.css; do
    if [ ! -f "${SHARED_UI_DIR}/dist/${artifact}" ]; then
      echo "[frontend] 共有 UI のビルド成果物が見つかりません: ${SHARED_UI_DIR}/dist/${artifact}" >&2
      return 1
    fi
  done
}

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

prepare_shared_ui

cd "${FRONTEND_DIR}"

if [ ! -d node_modules ] || [ ! -e node_modules/@engchina/production-ready-ui/package.json ]; then
  echo "[frontend] 依存をインストールします (npm install)..."
  npm install
fi

echo "[frontend] http://localhost:${PORT} で起動します..."
exec npm run dev -- --host "${HOST}" --port "${PORT}"
