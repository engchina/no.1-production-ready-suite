#!/usr/bin/env bash
# 配備した Compute の上で、suite の全サービスを再起動する（#1566）。
#
# - 対象は systemd で有効（enabled）な production-ready-* の service（各製品の backend・worker、RAG の前処理 / parser）
#   と Nginx。RAG の前処理 / parser で画面から「停止」にしたもの（disabled）は起動しない（再起動の後も停止のまま）。
# - HTTPS の証明書の更新（production-ready-suite-tls-renew）は timer が起動する oneshot なので対象にしない。
# - 再起動の後、配備した製品の backend の /api/health を待つ。
# - OS の再起動では、同じ unit が自動で起動する（WantedBy=multi-user.target）。このスクリプトは手で再起動するとき用。
set -euo pipefail

SUITE_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=suite-nginx.sh
source "${SUITE_DEPLOY_DIR}/suite-nginx.sh"

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
PROPS_DIR="${PROPS_DIR:-${APP_ROOT}/props}"
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
Usage: sudo platform/deploy/suite-restart.sh [--status]

引数なし   有効な production-ready-* の service と Nginx を再起動し、各製品の backend の health を待ちます。
--status  再起動せず、service の状態と health だけを表示します。

Environment overrides:
  SUITE_HEALTH_TIMEOUT_SECONDS   health を待つ上限の秒数（既定 180）
EOF
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

suite_restart_all() {
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
  local action="restart"
  case "${1:-}" in
    -h | --help) restart_usage; return 0 ;;
    --status)
      action="status"
      # 状態の確認では待たない（1 回だけ問い合わせる）。
      SUITE_HEALTH_TIMEOUT_SECONDS=0
      ;;
    "") ;;
    *) restart_usage >&2; return 2 ;;
  esac
  if [ "$(id -u)" -ne 0 ]; then
    restart_fail "root で実行してください: sudo $0 ${1:-}"
    return 1
  fi
  if [ "${action}" = "restart" ]; then
    suite_restart_all
  fi
  restart_log "service の状態:"
  suite_report_status
  if suite_wait_for_health; then
    restart_log "すべての製品の backend が応答しています。"
  else
    restart_fail "応答しない製品があります（上の log を確認してください）。"
    return 1
  fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  suite_restart_main "$@"
fi
