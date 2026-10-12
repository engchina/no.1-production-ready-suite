#!/usr/bin/env bash
# 配備した Compute の上で、最新のコード（origin の main）を取り込み、再配備する（#1566）。
#
#   1. suite-pull.sh で repository を main の最新にする（追跡ファイルに変更があれば中止）。
#   2. 取得した新しい suite-update.sh で続きを実行する（更新の手順そのものも最新にする）。
#   3. 各製品の今の backend/.env を props（cloud-init が置いた元）へ写す。suite-init.sh は props から backend/.env を
#      作り直すため、写さないと画面で保存した値（RAG の HuggingFace・解析後の処理、NL2SQL の Select AI Credential・
#      Deep Data Security など）が消える。元の props は recovery/<時刻>/ に退避する。platform/.env・Wallet・HTTPS の CA は
#      suite-init.sh がそのまま保つ。
#   4. suite-init.sh を再実行する（依存の同期・build・DB の初期化と migration・systemd の unit・Nginx・サービスの再起動）。
#   5. 各製品の backend の /api/health を待つ。
set -euo pipefail

SUITE_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_UPDATE_SCRIPT="${SUITE_DEPLOY_DIR}/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=suite-pull.sh
source "${SUITE_DEPLOY_DIR}/suite-pull.sh"
# shellcheck source=suite-restart.sh
source "${SUITE_DEPLOY_DIR}/suite-restart.sh"

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
PROPS_DIR="${PROPS_DIR:-${APP_ROOT}/props}"
SUITE_RECOVERY_DIR="${SUITE_RECOVERY_DIR:-${APP_ROOT}/recovery}"
SUITE_UPDATE_LOG="${SUITE_UPDATE_LOG:-/var/log/suite-update.log}"
SUITE_UPDATE_LOCK_FILE="${SUITE_UPDATE_LOCK_FILE:-/run/lock/production-ready-suite-update.lock}"
SUITE_INIT_SCRIPT="${SUITE_INIT_SCRIPT:-${SUITE_DEPLOY_DIR}/suite-init.sh}"

update_log() { printf '[suite-update] %s\n' "$*"; }
update_fail() {
  printf '[suite-update] ERROR: %s\n' "$*" >&2
  return 1
}

update_usage() {
  cat <<'EOF'
Usage: sudo platform/deploy/suite-update.sh [--no-pull]

引数なし    origin の main を取得し（suite-pull.sh）、再配備して、各製品の health を待ちます。
--no-pull  取得せず、今のコードで再配備だけを行います。

画面で保存した値（各製品の backend/.env・platform/.env）、Wallet、HTTPS の CA は保ちます。
log は /var/log/suite-update.log（再配備の詳細は /var/log/suite-init.log）。
EOF
}

update_acquire_lock() {
  install -d -m 0755 "$(dirname "${SUITE_UPDATE_LOCK_FILE}")"
  exec 9>"${SUITE_UPDATE_LOCK_FILE}"
  flock -n 9 || { update_fail "別の更新が実行中です（${SUITE_UPDATE_LOCK_FILE}）。"; return 1; }
}

# 各製品の今の backend/.env を props へ写す（suite-init.sh が props から作り直しても値が変わらないように）。
update_preserve_backend_envs() {
  local product env_file prop backup_dir=""
  while read -r product; do
    env_file="${SUITE_REPO_DIR}/${product}/backend/.env"
    prop="${PROPS_DIR}/${product}.backend.env"
    [ -f "${env_file}" ] || continue
    if [ -z "${backup_dir}" ]; then
      install -d -m 0700 "${SUITE_RECOVERY_DIR}"
      backup_dir="$(mktemp -d "${SUITE_RECOVERY_DIR}/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")"
      chmod 0700 "${backup_dir}"
    fi
    if [ -f "${prop}" ]; then
      install -m 0600 "${prop}" "${backup_dir}/${product}.backend.env"
    fi
    install -m 0600 "${env_file}" "${prop}"
    update_log "${product}: 今の backend/.env を保ちます（元の props は ${backup_dir}/ に退避）。"
  done < <(suite_deployed_products)
}

update_redeploy() {
  local status=0
  update_preserve_backend_envs
  update_log "再配備します（${SUITE_INIT_SCRIPT}。詳細は /var/log/suite-init.log）。"
  APP_ROOT="${APP_ROOT}" bash "${SUITE_INIT_SCRIPT}" || status=$?
  if [ "${status}" -ne 0 ]; then
    update_log "再配備で失敗した製品があります（status=${status}）。/var/log/suite-init.log と /var/log/<製品>-init.log を確認してください。"
  fi
  if suite_wait_for_health && [ "${status}" -eq 0 ]; then
    update_log "更新が完了しました: $(suite_git log -1 --format='%h %cs %s')"
    return 0
  fi
  update_fail "更新は完了しましたが、失敗または応答しない製品があります（上の log を確認してください）。"
}

suite_update_main() {
  local pull=true
  case "${1:-}" in
    -h | --help) update_usage; return 0 ;;
    --no-pull) pull=false ;;
    "") ;;
    *) update_usage >&2; return 2 ;;
  esac
  if [ "$(id -u)" -ne 0 ]; then
    update_fail "root で実行してください: sudo $0 ${1:-}"
    return 1
  fi
  # 取得の後に新しい suite-update.sh で続きを実行するとき（下の exec）は、lock と log を引き継ぐ。
  if [ "${SUITE_UPDATE_REEXEC:-false}" != "true" ]; then
    update_acquire_lock
    exec > >(tee -a "${SUITE_UPDATE_LOG}") 2>&1
    update_log "開始（$(date -Is)）"
  fi
  if [ "${pull}" = "true" ]; then
    suite_pull
    exec env SUITE_UPDATE_REEXEC=true bash "${SUITE_UPDATE_SCRIPT}" --no-pull
  fi
  update_redeploy
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  suite_update_main "$@"
  exit $?
fi
