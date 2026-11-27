#!/usr/bin/env bash
set -euo pipefail

# 引数で RAG / Agent の start-frontend.sh も同じ契約で検証できる（既定は NL2SQL）。
TEST_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START_FRONTEND_SCRIPT="$(cd "$(dirname "${1:-${TEST_SCRIPT_DIR}/../start-frontend.sh}")" && pwd)/$(basename "${1:-start-frontend.sh}")"
TEST_TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEST_TMP_DIR}"' EXIT

APP_DIR="${TEST_TMP_DIR}/nl2sql"
# SHARED_PLATFORM_DIR で別の場所の共有パッケージを指定しても、製品と同じ版の準備手順を使う。
PLATFORM_DIR="${TEST_TMP_DIR}/shared-source"
UI_DIR="${PLATFORM_DIR}/packages/ui"
SETTINGS_DIR="${PLATFORM_DIR}/packages/system-settings"
MOCK_BIN_DIR="${TEST_TMP_DIR}/bin"
NPM_CALL_LOG="${TEST_TMP_DIR}/npm-calls.log"

mkdir -p \
  "${APP_DIR}/scripts" \
  "${APP_DIR}/frontend/node_modules/@production-ready/ui" \
  "${TEST_TMP_DIR}/platform/scripts" \
  "${UI_DIR}" "${SETTINGS_DIR}" "${MOCK_BIN_DIR}"
cp "${START_FRONTEND_SCRIPT}" "${APP_DIR}/scripts/start-frontend.sh"
cp "${TEST_SCRIPT_DIR}/../../../platform/scripts/prepare-shared-ui.sh" "${TEST_TMP_DIR}/platform/scripts/"
cp "${TEST_SCRIPT_DIR}/fixtures/npm" "${MOCK_BIN_DIR}/npm"
chmod +x "${MOCK_BIN_DIR}/npm"

: > "${PLATFORM_DIR}/package.json"
: > "${PLATFORM_DIR}/package-lock.json"
: > "${UI_DIR}/package.json"
: > "${SETTINGS_DIR}/package.json"
: > "${APP_DIR}/frontend/node_modules/@production-ready/ui/package.json"

export PATH="${MOCK_BIN_DIR}:${PATH}" NPM_CALL_LOG
export SHARED_PLATFORM_DIR="${PLATFORM_DIR}" SHARED_UI_DIR="${UI_DIR}" PORT=3997

start_frontend() {
  : > "${NPM_CALL_LOG}"
  "${APP_DIR}/scripts/start-frontend.sh"
}

assert_calls() {
  local expected actual
  expected="$(printf '%s\n' "$@")"
  actual="$(<"${NPM_CALL_LOG}")"
  if [ "${actual}" != "${expected}" ]; then
    printf 'npm の呼び出しが一致しません。\n期待値:\n%s\n実際:\n%s\n' "${expected}" "${actual}" >&2
    exit 1
  fi
}

ci="${PLATFORM_DIR}|ci"
build="${PLATFORM_DIR}|run build"
dev="${APP_DIR}/frontend|run dev -- --host 0.0.0.0 --port 3997"
start_frontend
assert_calls "${ci}" "${build}" "${dev}"

for artifact in index.js index.d.ts tokens.css; do
  if [ ! -f "${UI_DIR}/dist/${artifact}" ]; then
    echo "共有 UI の成果物が生成されていません: ${artifact}" >&2
    exit 1
  fi
done

start_frontend
assert_calls "${dev}"

# 旧版の起動スクリプトや手動の npm ci が作った既存ディレクトリからも同期する。
mv "${PLATFORM_DIR}/node_modules" "${TEST_TMP_DIR}/first-install"
mkdir "${PLATFORM_DIR}/node_modules"
touch "${PLATFORM_DIR}/node_modules/.package-lock.json"
start_frontend
assert_calls "${ci}" "${build}" "${dev}"

# 全入力の mtime がインストールより古くても、内容を変えたときだけ再同期する。
for input in "${PLATFORM_DIR}/package-lock.json" "${PLATFORM_DIR}/package.json" \
    "${UI_DIR}/package.json" "${SETTINGS_DIR}/package.json"; do
  printf '{}\n' > "${input}"
  touch -r "${PLATFORM_DIR}/node_modules/.package-lock.json" "${input}"
  start_frontend
  assert_calls "${ci}" "${build}" "${dev}"
  start_frontend
  assert_calls "${dev}"
done

# 同じ platform を同時に起動しても ci と build は一度で済み、両方が Vite に進む。
if command -v flock >/dev/null 2>&1; then
  printf '\n' >> "${PLATFORM_DIR}/package-lock.json"
  : > "${NPM_CALL_LOG}"
  "${APP_DIR}/scripts/start-frontend.sh" &
  first_pid=$!
  "${APP_DIR}/scripts/start-frontend.sh" &
  second_pid=$!
  wait "${first_pid}"
  wait "${second_pid}"
  assert_calls "${ci}" "${build}" "${dev}" "${dev}"
fi

# インストール済みの記録が欠けたら復旧する。同期に失敗した場合は build へ進まない。
mv "${PLATFORM_DIR}/node_modules/.package-lock.json" "${TEST_TMP_DIR}/old-lock"
if FAIL_SHARED_INSTALL=1 start_frontend; then exit 1; fi
assert_calls "${ci}"
start_frontend
assert_calls "${ci}" "${build}" "${dev}"

# 失敗した build の古い成果物が残っても、再起動で build だけをやり直す。
if FORCE_SHARED_UI_BUILD=1 FAIL_SHARED_BUILD=1 start_frontend; then exit 1; fi
assert_calls "${build}"
start_frontend
assert_calls "${build}" "${dev}"

# 共有設定の型宣言だけが欠けた場合は、依存を使い直して両パッケージをビルドする。
mv "${SETTINGS_DIR}/dist/index.d.ts" "${TEST_TMP_DIR}/old-types"
start_frontend
assert_calls "${build}" "${dev}"

# 製品側の lockfile 更新は従来どおり npm install を呼ぶ。
touch -d "2026-01-01 00:00:00" "${APP_DIR}/frontend/node_modules/.package-lock.json"
: > "${APP_DIR}/frontend/package.json"
: > "${APP_DIR}/frontend/package-lock.json"
start_frontend
assert_calls "${APP_DIR}/frontend|install" "${dev}"
start_frontend
assert_calls "${dev}"

echo "start-frontend preflight test: ok"
