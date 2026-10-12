#!/usr/bin/env bash
# 配備した Compute の上で、今のコードから build して全サービスを再起動する（#1566・#1577）。
#
# 既定（build して再起動）:
#   1. 各製品の今の backend/.env を props（cloud-init が置いた元）へ写す。suite-init.sh は props から backend/.env を
#      作り直すため、写さないと画面で保存した値（RAG の HuggingFace・解析後の処理、NL2SQL の Select AI Credential・
#      Deep Data Security など）が消える。元の props は recovery/<時刻>/ に退避する。platform/.env・Wallet・HTTPS の CA は
#      suite-init.sh がそのまま保つ。
#   2. suite-init.sh を再実行する（依存の同期・共有 UI と各製品の frontend の build・DB の初期化と migration・
#      systemd の unit・Nginx・サービスの再起動）。suite-pull.sh で取得した最新のコードは、ここで使われる。
#   3. 配備した製品の backend の /api/health を待つ。
# --no-build: build せず、有効（enabled）な production-ready-* の service と Nginx だけを再起動する。
#   RAG の前処理 / parser で画面から「停止」にしたもの（disabled）は起動しない。証明書の更新の oneshot も対象にしない。
# --status: 再起動せず、service の状態と health だけを表示する。
#
# OS の再起動では、enabled の unit と Nginx が自動で起動する（WantedBy=multi-user.target）。
set -euo pipefail

SUITE_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_RESTART_SCRIPT="${SUITE_DEPLOY_DIR}/suite-restart.sh"
# shellcheck source=suite-nginx.sh
source "${SUITE_DEPLOY_DIR}/suite-nginx.sh"

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
SUITE_REPO_DIR="${SUITE_REPO_DIR:-${APP_ROOT}/no.1-production-ready-suite}"
PROPS_DIR="${PROPS_DIR:-${APP_ROOT}/props}"
SUITE_RECOVERY_DIR="${SUITE_RECOVERY_DIR:-${APP_ROOT}/recovery}"
SUITE_INIT_SCRIPT="${SUITE_INIT_SCRIPT:-${SUITE_DEPLOY_DIR}/suite-init.sh}"
# suite-update.sh と共有する lock と log。
SUITE_OPS_LOG="${SUITE_OPS_LOG:-/var/log/suite-ops.log}"
SUITE_OPS_LOCK_FILE="${SUITE_OPS_LOCK_FILE:-/run/lock/production-ready-suite-ops.lock}"
SUITE_UNIT_PATTERN="production-ready-*.service"
SUITE_TLS_RENEW_SERVICE="production-ready-suite-tls-renew.service"
SUITE_HEALTH_TIMEOUT_SECONDS="${SUITE_HEALTH_TIMEOUT_SECONDS:-180}"
SUITE_HEALTH_INTERVAL_SECONDS="${SUITE_HEALTH_INTERVAL_SECONDS:-3}"

restart_log() { printf '[suite-restart] %s\n' "$*"; }
restart_fail() {
  printf '[suite-restart] ERROR: %s\n' "$*" >&2
  return 1
}

restart_usage() {
  cat <<'EOF'
Usage: sudo platform/deploy/suite-restart.sh [--no-build | --status]

引数なし     今のコードから build して再配備し（依存の同期・共有 UI と各製品の frontend の build・DB の migration・
             systemd・Nginx）、全サービスを再起動して、各製品の backend の health を待ちます。
             suite-pull.sh で取得した最新のコードは、これで反映されます。
--no-build  build せず、有効な production-ready-* の service と Nginx だけを再起動します。
--status    再起動せず、service の状態と health だけを表示します。

画面で保存した値（各製品の backend/.env・platform/.env）、Wallet、HTTPS の CA は保ちます。
log は /var/log/suite-ops.log（再配備の詳細は /var/log/suite-init.log）。

Environment overrides:
  SUITE_HEALTH_TIMEOUT_SECONDS   health を待つ上限の秒数（既定 180）
EOF
}

# suite-restart.sh / suite-update.sh の同時の実行を防ぐ。suite-update.sh から呼ばれたときは、lock と log を引き継ぐ。
suite_ops_begin() {
  local name="$1"
  if [ "${SUITE_OPS_INHERITED:-false}" = "true" ]; then
    return 0
  fi
  install -d -m 0755 "$(dirname "${SUITE_OPS_LOCK_FILE}")"
  exec 9>"${SUITE_OPS_LOCK_FILE}"
  flock -n 9 || { restart_fail "別の更新・再起動が実行中です（${SUITE_OPS_LOCK_FILE}）。"; return 1; }
  exec > >(tee -a "${SUITE_OPS_LOG}") 2>&1
  printf '[%s] 開始（%s）\n' "${name}" "$(date -Is)"
  export SUITE_OPS_INHERITED=true
}

# 有効（enabled）な suite の service。証明書の更新の oneshot は除く。
suite_enabled_units() {
  systemctl list-unit-files --type=service --state=enabled --no-legend --plain "${SUITE_UNIT_PATTERN}" 2>/dev/null \
    | awk '{print $1}' | grep -vxF "${SUITE_TLS_RENEW_SERVICE}" || true
}

# 配備した製品（props/products.txt）。読めなければ 3 製品。
suite_deployed_products() {
  local names=() name
  read -r -a names <<< "$(tr '\n' ' ' 2>/dev/null < "${PROPS_DIR}/products.txt" || true)"
  for name in "${SUITE_PRODUCTS_ORDER[@]}"; do
    if [ "${#names[@]}" -eq 0 ] || suite_contains "${name}" "${names[@]}"; then
      printf '%s\n' "${name}"
    fi
  done
}

suite_wait_for_health() {
  local product port url deadline failed=0
  while read -r product; do
    port="$(suite_backend_port "${product}")"
    url="http://127.0.0.1:${port}/api/health"
    deadline=$((SECONDS + SUITE_HEALTH_TIMEOUT_SECONDS))
    until curl -fsS --max-time 5 -o /dev/null "${url}" 2>/dev/null; do
      if [ "${SECONDS}" -ge "${deadline}" ]; then
        restart_log "${product}: health が ${SUITE_HEALTH_TIMEOUT_SECONDS} 秒以内に応答しません（${url}）。sudo journalctl -u production-ready-${product}-backend -n 200 --no-pager で確認してください。"
        failed=1
        continue 2
      fi
      sleep "${SUITE_HEALTH_INTERVAL_SECONDS}"
    done
    restart_log "${product}: health OK（${url}）"
  done < <(suite_deployed_products)
  return "${failed}"
}

suite_report_status() {
  local unit
  for unit in $(suite_enabled_units) nginx.service; do
    restart_log "  ${unit}: $(systemctl is-active "${unit}" 2>/dev/null || true)"
  done
}

# 各製品の今の backend/.env を props へ写す（suite-init.sh が props から作り直しても値が変わらないように）。
suite_preserve_backend_envs() {
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
    restart_log "${product}: 今の backend/.env を保ちます（元の props は ${backup_dir}/ に退避）。"
  done < <(suite_deployed_products)
}

# 今のコードで再配備する（build・migration・systemd・Nginx・再起動）。
suite_rebuild_and_restart() {
  local status=0
  suite_preserve_backend_envs
  restart_log "今のコードから build して再起動します（${SUITE_INIT_SCRIPT}。詳細は /var/log/suite-init.log）。"
  APP_ROOT="${APP_ROOT}" bash "${SUITE_INIT_SCRIPT}" || status=$?
  if [ "${status}" -ne 0 ]; then
    restart_log "失敗した製品があります（status=${status}）。/var/log/suite-init.log と /var/log/<製品>-init.log を確認してください。"
  fi
  return "${status}"
}

# build せずにサービスだけを再起動する。
suite_restart_services() {
  local units=()
  mapfile -t units < <(suite_enabled_units)
  if [ "${#units[@]}" -eq 0 ]; then
    restart_fail "有効な ${SUITE_UNIT_PATTERN} がありません。配備（suite-init.sh）が終わっているか確認してください。"
    return 1
  fi
  restart_log "再起動します: ${units[*]}"
  systemctl daemon-reload
  systemctl restart "${units[@]}"
  restart_log "Nginx の設定を確かめて再起動します。"
  nginx -t
  systemctl restart nginx
}

suite_restart_main() {
  local action="build" status=0
  case "${1:-}" in
    -h | --help) restart_usage; return 0 ;;
    --no-build) action="restart" ;;
    --status)
      action="status"
      # 状態の確認では待たない（1 回だけ問い合わせる）。
      SUITE_HEALTH_TIMEOUT_SECONDS=0
      ;;
    "") ;;
    *) restart_usage >&2; return 2 ;;
  esac
  if [ "$(id -u)" -ne 0 ]; then
    restart_fail "root で実行してください: sudo ${SUITE_RESTART_SCRIPT} ${1:-}"
    return 1
  fi
  case "${action}" in
    build)
      suite_ops_begin suite-restart
      suite_rebuild_and_restart || status=$?
      ;;
    restart)
      suite_ops_begin suite-restart
      suite_restart_services
      ;;
  esac
  restart_log "service の状態:"
  suite_report_status
  if suite_wait_for_health && [ "${status}" -eq 0 ]; then
    restart_log "完了しました。すべての製品の backend が応答しています。"
    return 0
  fi
  restart_fail "失敗または応答しない製品があります（上の log を確認してください）。"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  suite_restart_main "$@"
  exit $?
fi
