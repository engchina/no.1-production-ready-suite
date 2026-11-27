#!/usr/bin/env bash
# 開発用の共有 UI を、3 製品が同じ依存・成果物で使えるように準備する。
set -euo pipefail

SHARED_PLATFORM_DIR="$1"
SHARED_UI_DIR="$2"
if [ ! -f "${SHARED_UI_DIR}/package.json" ]; then
  echo "[frontend] 共有 UI パッケージが見つかりません: ${SHARED_UI_DIR}" >&2
  echo "[frontend] 共有基盤の場所を SHARED_PLATFORM_DIR または SHARED_UI_DIR で指定してください。" >&2
  exit 1
fi
if [ ! -f "${SHARED_PLATFORM_DIR}/package.json" ]; then
  echo "[frontend] 共有 platform の package.json が見つかりません: ${SHARED_PLATFORM_DIR}" >&2
  exit 1
fi

settings_dir="${SHARED_PLATFORM_DIR}/packages/system-settings"
artifacts=("${SHARED_UI_DIR}/dist/index.js" "${SHARED_UI_DIR}/dist/index.d.ts" "${SHARED_UI_DIR}/dist/tokens.css")
if [ -f "${settings_dir}/package.json" ]; then
  artifacts+=("${settings_dir}/dist/index.js" "${settings_dir}/dist/index.d.ts")
fi

# 共有 UI（packages/ui と packages/system-settings）の成果物が、ソース・設定より新しいか。
# 変更が無いときは build を省く（起動中の別の製品の Vite が読む dist を消さない。#816）。
shared_ui_up_to_date() {
  [ "${FORCE_SHARED_UI_BUILD:-0}" != "1" ] || return 1
  local artifact
  for artifact in "${artifacts[@]}"; do
    [ -f "${artifact}" ] || return 1
  done
  # vite が最初に書く index.js を基準にする（その後に変えたソースがあれば build し直す）。
  local stamp="${SHARED_UI_DIR}/dist/index.js"
  local newer
  newer="$(find "${SHARED_UI_DIR}" "${settings_dir}" "${SHARED_PLATFORM_DIR}/package.json" \
    "${SHARED_PLATFORM_DIR}/package-lock.json" \
    \( -name node_modules -o -name dist \) -prune -o -type f -newer "${stamp}" -print -quit 2>/dev/null || true)"
  [ -z "${newer}" ]
}

# 同じ platform を使う製品間で依存の入れ替えとビルドを直列化する。終了時に FD を閉じる。
if command -v flock >/dev/null 2>&1; then
  key="$(printf '%s' "$(cd "${SHARED_PLATFORM_DIR}" && pwd -P)" | cksum | cut -d' ' -f1)"
  exec 9>"${TMPDIR:-/tmp}/production-ready-shared-ui-${key}.lock"
  if ! flock -n 9; then
    echo "[frontend] 別の製品が共有 UI を準備しています。終わるまで待ちます..."
    flock 9
  fi
fi

lockfile="${SHARED_PLATFORM_DIR}/package-lock.json"
installed="${SHARED_PLATFORM_DIR}/node_modules"
installed_inputs="${installed}/.production-ready-shared-ui-inputs"
build_complete="${installed}/.production-ready-shared-ui-built"
inputs=""
install_needed=0
if [ -f "${lockfile}" ]; then
  # mtime を維持する checkout やキャッシュの復元でも、最後に準備した入力と照合する。
  manifests=("${lockfile}" "${SHARED_PLATFORM_DIR}/package.json" "${SHARED_UI_DIR}/package.json")
  if [ -f "${settings_dir}/package.json" ]; then
    manifests+=("${settings_dir}/package.json")
  fi
  inputs="$(
    for manifest in "${manifests[@]}"; do
      cksum < "${manifest}"
    done
  )"
  if [ ! -f "${installed}/.package-lock.json" ] || [ ! -f "${installed_inputs}" ] || [ "$(<"${installed_inputs}")" != "${inputs}" ]; then
    install_needed=1
  fi
elif [ ! -d "${installed}" ]; then
  echo "[frontend] 共有 platform の package-lock.json が見つかりません: ${lockfile}" >&2
  exit 1
fi

if [ "${install_needed}" = 1 ]; then
  echo "[frontend] 共有 UI の依存を同期します (npm ci)..."
  (cd "${SHARED_PLATFORM_DIR}" && npm ci)
  printf '%s\n' "${inputs}" > "${installed_inputs}"
elif { [ -z "${inputs}" ] || [ -s "${build_complete}" ]; } && shared_ui_up_to_date; then
  echo "[frontend] 共有 UI パッケージは最新です（build を省きます。常に build するには FORCE_SHARED_UI_BUILD=1）。"
  exit 0
fi

# 途中で失敗した build を、次回の起動で準備済みと扱わない。
if [ -n "${inputs}" ]; then
  : > "${build_complete}"
fi
echo "[frontend] 共有 UI パッケージをビルドします..."
(cd "${SHARED_PLATFORM_DIR}" && npm run build)
for artifact in "${artifacts[@]}"; do
  if [ ! -f "${artifact}" ]; then
    echo "[frontend] 共有 UI のビルド成果物が見つかりません: ${artifact}" >&2
    exit 1
  fi
done
if [ -n "${inputs}" ]; then
  printf 'ok\n' > "${build_complete}"
fi
