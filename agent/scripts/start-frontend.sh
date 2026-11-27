#!/usr/bin/env bash
# フロントエンド(Vite + React)を開発モードで起動する。
# - 共有 UI パッケージを検証・build してから Vite を起動する。
# - node_modules / 共有 UI のリンクが無い場合、または package.json / package-lock.json が
#   前回インストール時（node_modules/.package-lock.json）より新しい場合は npm install を実行する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
SHARED_PLATFORM_DIR="${SHARED_PLATFORM_DIR:-${ROOT_DIR}/../platform}"
SHARED_UI_DIR="${SHARED_UI_DIR:-${SHARED_PLATFORM_DIR}/packages/ui}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3002}"
# Vite は BACKEND_URL を明示したときだけ /api を proxy する（未指定なら 404 の hermetic モード）。
# このスクリプトはローカル backend と組み合わせる起動経路なので、接続先を明示して渡す。
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8020}"

# OS の CA（企業 CA を含む）も信頼する。明示された追加 CA は保持し、TLS 検証は無効化しない。
# CA bundle の場所は Debian / Ubuntu と RHEL / Oracle Linux で違うため、読める最初のものを使う。
if [ -z "${NODE_EXTRA_CA_CERTS:-}" ]; then
  for ca_bundle in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
    if [ -r "${ca_bundle}" ]; then
      export NODE_EXTRA_CA_CERTS="${ca_bundle}"
      break
    fi
  done
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[frontend] npm が見つかりません。Node.js をインストールしてください。" >&2
  exit 1
fi

# 同じポートの listener だけを停止する（接続側の client / readiness は停止しない）。
kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -t -a -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    echo "[frontend] ポート ${port} を使用中のプロセスを停止します (PID: ${pids//$'\n'/ })..."
    kill ${pids} 2>/dev/null || true
    sleep 1
    pids="$(lsof -nP -t -a -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "${pids}" ]; then
      echo "[frontend] 強制停止します (kill -9)..."
      kill -9 ${pids} 2>/dev/null || true
    fi
  fi
}

kill_port "${PORT}"

# 依存の照合・同期・build は製品間で同じ手順とロックを使う。
prepare_script="${ROOT_DIR}/../platform/scripts/prepare-shared-ui.sh"
if [ ! -f "${prepare_script}" ]; then
  prepare_script="${SHARED_PLATFORM_DIR}/scripts/prepare-shared-ui.sh"
fi
bash "${prepare_script}" "${SHARED_PLATFORM_DIR}" "${SHARED_UI_DIR}"

cd "${FRONTEND_DIR}"

# git pull 等で依存が追加・更新されたのに node_modules が古いままだと、Vite が import を解決できない。
dependencies_changed() {
  local installed="node_modules/.package-lock.json"
  [ -f package-lock.json ] || return 1
  [ ! -f "${installed}" ] || [ package-lock.json -nt "${installed}" ] || [ package.json -nt "${installed}" ]
}

if [ ! -d node_modules ] || [ ! -e node_modules/@production-ready/ui/package.json ]; then
  echo "[frontend] 依存をインストールします (npm install)..."
  npm install
elif dependencies_changed; then
  echo "[frontend] package.json / package-lock.json が更新されているため依存を更新します (npm install)..."
  npm install
fi

echo "[frontend] http://localhost:${PORT} で起動します..."
exec npm run dev -- --host "${HOST}" --port "${PORT}"
