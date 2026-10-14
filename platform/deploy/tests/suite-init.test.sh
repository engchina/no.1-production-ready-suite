#!/usr/bin/env bash
# platform/deploy/suite-init.sh（1 台の Compute への 3 製品の配備。#1316）の振る舞いを外部依存なしで検証する。
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
task_dir="$(mktemp -d)"
trap 'rm -rf -- "${task_dir}"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

prepare_case() {
  local case_dir="$1"
  mkdir -p "${case_dir}/app/props" "${case_dir}/app/no.1-production-ready-suite" "${case_dir}/units" \
    "${case_dir}/conf.d"
  ln -s "${repo}/platform" "${case_dir}/app/no.1-production-ready-suite/platform"
  export SUITE_INIT_TEST_MODE=true
  export APP_ROOT="${case_dir}/app"
  export SYSTEMD_UNIT_DIR="${case_dir}/units"
  export NGINX_CONF_DIR="${case_dir}/conf.d"
  export NGINX_MAIN_CONF="${case_dir}/nginx.conf"
  export SUITE_SSL_DIR="${case_dir}/ssl"
}

# 1. 製品の選択: 並びは rag → nl2sql → agent、未知の名前と 0 件は失敗。
(
  case_dir="${task_dir}/products"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  printf 'agent rag\n' > "${PROPS_DIR}/products.txt"
  load_products >/dev/null
  [ "${SUITE_PRODUCTS[*]}" = "rag agent" ] || fail "製品の並び: ${SUITE_PRODUCTS[*]}"
  printf 'rag platform\n' > "${PROPS_DIR}/products.txt"
  if load_products >/dev/null 2>&1; then fail "未知の製品を受け入れた"; fi
  : > "${PROPS_DIR}/products.txt"
  if load_products >/dev/null 2>&1; then fail "製品 0 件を受け入れた"; fi
  # HTTPS は既定 on。false のときだけ off。
  https_enabled || fail "https_enabled.txt が無いときは on"
  printf 'false\n' > "${PROPS_DIR}/https_enabled.txt"
  if https_enabled; then fail "false なら off"; fi
  # 公開の port（#1316）: 既定は 80 / 443。props の値を使い、使えない port は配備の前に失敗する。
  [ "$(http_port)" = "80" ] && [ "$(https_port)" = "443" ] || fail "既定の port"
  [ "$(public_base_url 203.0.113.5)" = "http://203.0.113.5" ] || fail "既定の http の URL に port を付けない"
  printf '8080\n' > "${PROPS_DIR}/http_port.txt"
  [ "$(public_base_url 203.0.113.5)" = "http://203.0.113.5:8080" ] || fail "既定でない http の port を URL に付ける"
  validate_ports 2>/dev/null || fail "8080 は使える"
  printf 'true\n' > "${PROPS_DIR}/https_enabled.txt"
  [ "$(public_base_url 203.0.113.5)" = "https://203.0.113.5" ] || fail "既定の https の URL に port を付けない"
  printf '8443\n' > "${PROPS_DIR}/https_port.txt"
  [ "$(public_base_url 203.0.113.5)" = "https://203.0.113.5:8443" ] || fail "既定でない https の port を URL に付ける"
  printf '22\n' > "${PROPS_DIR}/https_port.txt"
  if validate_ports 2>/dev/null; then fail "https_port に 22 を受け入れた"; fi
  printf '8010\n' > "${PROPS_DIR}/https_port.txt"
  if validate_ports 2>/dev/null; then fail "https_port に NL2SQL の backend の port を受け入れた"; fi
  printf 'false\n' > "${PROPS_DIR}/https_enabled.txt"
  validate_ports 2>/dev/null || fail "HTTPS が off なら https_port は見ない"
  printf '18020\n' > "${PROPS_DIR}/http_port.txt"
  if validate_ports 2>/dev/null; then fail "http_port に RAG の parser の port を受け入れた"; fi
)

# 2. 製品の init_script.sh に渡す環境変数。
(
  case_dir="${task_dir}/env"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  rag_env="$(product_init_env rag false)"
  if printf '%s\n' "$(product_init_env rag false)" | grep -q 'PR_SUITE_MODE'; then fail "単独の配備の mode は無い"; fi
  for expected in "FRONTEND_BASE_PATH=/rag/" "BACKEND_ENV_SOURCE=${PROPS_DIR}/rag.backend.env" \
    "PR_SUITE_SKIP_PLATFORM_UI_BUILD=false" "SERVICE_USER=opc" "SERVICE_GROUP=opc" "SERVICE_HOME=/home/opc"; do
    printf '%s\n' "${rag_env}" | grep -qxF "${expected}" || fail "RAG の環境変数に ${expected} が無い"
  done
  agent_env="$(product_init_env agent true)"
  printf '%s\n' "${agent_env}" | grep -qxF "FRONTEND_BASE_PATH=/agent/" || fail "Agent の base"
  printf '%s\n' "${agent_env}" | grep -qxF "PR_SUITE_SKIP_PLATFORM_UI_BUILD=true" || fail "共有 UI の build を飛ばす"
  if printf '%s\n' "${agent_env}" | grep -q '^SERVICE_USER='; then fail "SERVICE_USER は RAG だけ"; fi
)

# 3. 1 つの製品が失敗しても残りを配備し、共有 UI は最初に成功した製品だけが build する。
(
  case_dir="${task_dir}/deploy"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  SUITE_PRODUCTS=(rag nl2sql agent)
  run_product_init() {
    printf '%s %s\n' "$1" "$2" >> "${case_dir}/calls.log"
    [ "$1" != "rag" ]
  }
  deploy_products >/dev/null
  [ "$(cat "${case_dir}/calls.log")" = "$(printf 'rag false\nnl2sql false\nagent true')" ] \
    || fail "製品の呼び出し: $(cat "${case_dir}/calls.log")"
  [ "${SUITE_FAILED_PRODUCTS[*]}" = "rag" ] || fail "失敗した製品: ${SUITE_FAILED_PRODUCTS[*]}"
)

# 4. Nginx: site を 1 つだけ書き、製品ごとの site と default を外す。製品の API の本文の上限は backend/.env から（#1597）。
(
  case_dir="${task_dir}/nginx"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  SUITE_PRODUCTS=(rag agent)
  printf 'true\n' > "${PROPS_DIR}/https_enabled.txt"
  mkdir -p "${SUITE_REPO_DIR}/rag/backend" 2>/dev/null || true
  nginx() { printf 'nginx %s\n' "$*" >> "${case_dir}/commands.log"; }
  systemctl() { printf 'systemctl %s\n' "$*" >> "${case_dir}/commands.log"; }
  configure_nginx >/dev/null
  # Oracle Linux の Nginx（#1611）: site は conf.d の .conf、nginx.conf は package の既定の server を外したもの。
  site="${NGINX_CONF_DIR}/production-ready-suite.conf"
  [ -f "${site}" ] || fail "site を conf.d に置かない"
  cmp -s "${NGINX_MAIN_CONF}" "${repo}/platform/templates/nginx/nginx.conf" || fail "nginx.conf を置き換えない"
  grep -qx 'user nginx;' "${NGINX_MAIN_CONF}" || fail "nginx.conf の worker は nginx ユーザー"
  grep -qF 'include /etc/nginx/conf.d/*.conf;' "${NGINX_MAIN_CONF}" || fail "nginx.conf が conf.d を読まない"
  if grep -Eq '^\s*(server|listen)\b' "${NGINX_MAIN_CONF}"; then fail "nginx.conf に既定の server が残っている"; fi
  grep -q 'listen 443 ssl http2 default_server;' "${site}" || fail "HTTPS の site ではない"
  grep -q 'client_max_body_size 210M;' "${site}" || fail "RAG の既定の上限（200 MiB + 10 MiB）"
  grep -q 'client_max_body_size 110M;' "${site}" || fail "Agent の既定の上限（100 MiB + 10 MiB）"
  grep -q 'return 302 /agent/;' "${site}" || fail "/ は /agent/"
  if grep -q '/nl2sql/' "${site}"; then fail "配備しない NL2SQL の location を書いた"; fi
  [ -f "${NGINX_CONF_DIR}/production-ready-logging.conf" ] || fail "logging.conf を置かない"
  [ -f "${NGINX_CONF_DIR}/production-ready-login-rate-limit.conf" ] || fail "login-rate-limit.conf を置かない"
  grep -qx 'nginx -t' "${case_dir}/commands.log" || fail "nginx -t を実行しない"
  printf 'RAG_MAX_UPLOAD_BYTES=104857600\n' > "${case_dir}/app/no.1-production-ready-suite/rag/backend/.env"
  [ "$(product_client_max_body_size rag)" = "110M" ] || fail "RAG_MAX_UPLOAD_BYTES から上限を作る"
  # MiB へ切り上げ、引用符を扱う。不正な値なら失敗する（#306）。
  printf "RAG_MAX_UPLOAD_BYTES='1000000'\n" > "${case_dir}/app/no.1-production-ready-suite/rag/backend/.env"
  [ "$(product_client_max_body_size rag)" = "11M" ] || fail "RAG_MAX_UPLOAD_BYTES を MiB へ切り上げていない"
  printf 'RAG_MAX_UPLOAD_BYTES=200MB\n' > "${case_dir}/app/no.1-production-ready-suite/rag/backend/.env"
  if product_client_max_body_size rag >/dev/null 2>&1; then fail "不正な RAG_MAX_UPLOAD_BYTES を受け入れた"; fi
  # NL2SQL / Agent も backend/.env の <製品>_MAX_UPLOAD_BYTES から（無ければ backend の既定値 + 10 MiB。#1597）。
  [ "$(product_client_max_body_size nl2sql)" = "210M" ] || fail "NL2SQL の既定の上限（200 MiB + 10 MiB）"
  [ "$(product_client_max_body_size agent)" = "110M" ] || fail "Agent の既定の上限（100 MiB + 10 MiB）"
  mkdir -p "${case_dir}/app/no.1-production-ready-suite/nl2sql/backend"
  printf 'NL2SQL_MAX_UPLOAD_BYTES=52428800\n' > "${case_dir}/app/no.1-production-ready-suite/nl2sql/backend/.env"
  [ "$(product_client_max_body_size nl2sql)" = "60M" ] || fail "NL2SQL_MAX_UPLOAD_BYTES から上限を作る"
)

# 5. HTTPS: 証明書と更新の timer。off なら timer を外す。
(
  case_dir="${task_dir}/tls"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  systemctl() { printf 'systemctl %s\n' "$*" >> "${case_dir}/commands.log"; }
  export SUITE_TLS_PRIVATE_IP=10.0.0.5
  export SUITE_TLS_PUBLIC_IP=203.0.113.5
  configure_tls 2>/dev/null >/dev/null
  [ -s "${SUITE_SSL_DIR}/ca.crt" ] && [ -s "${SUITE_SSL_DIR}/server.crt" ] || fail "証明書を作らない"
  [ "$(cat "${PROPS_DIR}/public_ip.txt")" = "203.0.113.5" ] || fail "公開 IP を props に残さない"
  grep -q "suite-tls.sh renew-if-needed" "${SYSTEMD_UNIT_DIR}/production-ready-suite-tls-renew.service" || fail "更新の unit"
  grep -q 'OnCalendar=daily' "${SYSTEMD_UNIT_DIR}/production-ready-suite-tls-renew.timer" || fail "更新の timer"
  grep -qx 'systemctl enable --now production-ready-suite-tls-renew.timer' "${case_dir}/commands.log" || fail "timer を有効にしない"
  printf 'false\n' > "${PROPS_DIR}/https_enabled.txt"
  configure_tls >/dev/null
  [ ! -e "${SYSTEMD_UNIT_DIR}/production-ready-suite-tls-renew.timer" ] || fail "HTTPS が off なら timer を外す"
)

# 5b. Oracle Linux 9（#1611）: dnf の package、firewalld で公開の port、SELinux の context・boolean・port。
(
  case_dir="${task_dir}/oracle-linux"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  calls="${case_dir}/commands.log"
  dnf() { printf 'dnf %s\n' "$*" >> "${calls}"; }
  systemctl() { printf 'systemctl %s\n' "$*" >> "${calls}"; }
  firewall-cmd() { printf 'firewall-cmd %s\n' "$*" >> "${calls}"; }
  selinuxenabled() { return 0; }
  semanage() { printf 'semanage %s\n' "$*" >> "${calls}"; }
  restorecon() { printf 'restorecon %s\n' "$*" >> "${calls}"; }
  setsebool() { printf 'setsebool %s\n' "$*" >> "${calls}"; }
  install_os_packages >/dev/null
  grep -qx 'dnf -y module enable nginx:1.24' "${calls}" || fail "Nginx の module の stream を固定しない"
  grep -qx "dnf -y install ${SUITE_OS_PACKAGES[*]}" "${calls}" || fail "OS の package を dnf で入れない"
  for name in nginx firewalld policycoreutils-python-utils; do
    printf ' %s ' "${SUITE_OS_PACKAGES[*]}" | grep -qF " ${name} " || fail "${name} を入れない"
  done
  # 既定（HTTPS on・80 / 443）: 443 だけを開け、80 は開けない（#1638）。http_port_t の追加は要らない。
  # 新しい Compute では 80 は permanent で開いていない（--query-port は 1）。
  firewall-cmd() {
    printf 'firewall-cmd %s\n' "$*" >> "${calls}"
    case "$*" in --permanent\ --query-port=*) return 1 ;; esac
  }
  : > "${calls}"
  configure_firewall >/dev/null
  configure_selinux >/dev/null
  grep -qx 'systemctl enable --now firewalld' "${calls}" || fail "firewalld を有効にしない"
  grep -qx 'firewall-cmd --permanent --add-port=443/tcp' "${calls}" || fail "443 を開けない"
  if grep -q -- '--add-port=80/tcp' "${calls}"; then fail "HTTPS が on なのに 80 を開けた"; fi
  grep -qx 'firewall-cmd --permanent --query-port=80/tcp' "${calls}" || fail "以前に開けた 80 を確かめない"
  if grep -q -- '--remove-port' "${calls}"; then fail "開いていない 80 を閉じようとした"; fi
  grep -qx 'firewall-cmd --reload' "${calls}" || fail "firewalld を reload しない"
  grep -qxF "semanage fcontext -a -t usr_t ${SUITE_REPO_DIR}(/.*)?" "${calls}" || fail "repository を usr_t にしない"
  for product in rag nl2sql agent; do
    grep -qxF "semanage fcontext -a -t httpd_sys_content_t ${SUITE_REPO_DIR}/${product}/frontend/dist(/.*)?" "${calls}" \
      || fail "${product} の dist を httpd_sys_content_t にしない"
  done
  grep -qxF "semanage fcontext -a -t cert_t ${SUITE_SSL_DIR}(/.*)?" "${calls}" || fail "証明書を cert_t にしない"
  grep -qxF "restorecon -R ${SUITE_REPO_DIR}" "${calls}" || fail "repository の context を戻さない"
  grep -qx 'setsebool -P httpd_can_network_connect 1' "${calls}" || fail "Nginx から backend へ proxy できない"
  if grep -q '^semanage port' "${calls}"; then fail "80 / 443 は http_port_t に足さない"; fi
  # 再配備（suite-restart.sh / suite-update.sh）: 以前の配備で開けた 80 が permanent に残っていれば閉じる（#1638）。
  firewall-cmd() {
    printf 'firewall-cmd %s\n' "$*" >> "${calls}"
    case "$*" in --permanent\ --query-port=*) return 0 ;; esac
  }
  : > "${calls}"
  removed_log="$(configure_firewall)"
  grep -qx 'firewall-cmd --permanent --add-port=443/tcp' "${calls}" || fail "再配備で 443 を開けない"
  grep -qx 'firewall-cmd --permanent --remove-port=80/tcp' "${calls}" || fail "以前に開けた 80 を閉じない"
  printf '%s\n' "${removed_log}" | grep -q 'closed 80/tcp' || fail "80 を閉じたことを log に出さない: ${removed_log}"
  [ "$(tail -n 1 "${calls}")" = 'firewall-cmd --reload' ] || fail "80 を閉じた後に reload しない"
  # 既定でない port は firewalld で開け、http_port_t にする。HTTPS が on なら http_port は開けない（開いていれば閉じる）。
  printf '8080\n' > "${PROPS_DIR}/http_port.txt"
  printf '8443\n' > "${PROPS_DIR}/https_port.txt"
  : > "${calls}"
  configure_firewall >/dev/null
  configure_selinux >/dev/null
  grep -qx 'firewall-cmd --permanent --add-port=8443/tcp' "${calls}" || fail "https_port を開けない"
  if grep -q -- '--add-port=8080/tcp' "${calls}"; then fail "HTTPS が on なのに http_port を開けた"; fi
  grep -qx 'firewall-cmd --permanent --remove-port=8080/tcp' "${calls}" || fail "以前に開けた http_port を閉じない"
  grep -qx 'semanage port -a -t http_port_t -p tcp 8443' "${calls}" || fail "https_port を http_port_t にしない"
  if grep -q '^semanage port .* 8080$' "${calls}"; then fail "HTTPS が on なのに http_port を http_port_t にした"; fi
  # https_port と http_port が同じなら、開けた port を閉じない。
  printf '8443\n' > "${PROPS_DIR}/http_port.txt"
  : > "${calls}"
  configure_firewall >/dev/null
  if grep -q -- '--remove-port' "${calls}"; then fail "https_port と同じ http_port を閉じた"; fi
  printf '8080\n' > "${PROPS_DIR}/http_port.txt"
  # HTTPS が off なら http_port だけを開け、https_port は開けない。http_port は閉じない。
  printf 'false\n' > "${PROPS_DIR}/https_enabled.txt"
  : > "${calls}"
  configure_firewall >/dev/null
  configure_selinux >/dev/null
  grep -qx 'firewall-cmd --permanent --add-port=8080/tcp' "${calls}" || fail "HTTPS が off なのに http_port を開けない"
  grep -qx 'semanage port -a -t http_port_t -p tcp 8080' "${calls}" || fail "http_port を http_port_t にしない"
  if grep -q '8443' "${calls}"; then fail "HTTPS が off なのに https_port を開けた"; fi
  if grep -q -- '--remove-port' "${calls}"; then fail "HTTPS が off なのに port を閉じた"; fi
  # Nginx が読む dist と証明書の context を、build と証明書の作成の後に戻す。
  SUITE_PRODUCTS=(rag agent)
  mkdir -p "${SUITE_REPO_DIR}/rag/frontend/dist" "${SUITE_REPO_DIR}/agent/frontend/dist" "${SUITE_SSL_DIR}"
  : > "${calls}"
  restore_nginx_contexts
  grep -qxF "restorecon -R ${SUITE_REPO_DIR}/rag/frontend/dist ${SUITE_REPO_DIR}/agent/frontend/dist ${SUITE_SSL_DIR}" "${calls}" \
    || fail "dist と証明書の context を戻さない: $(cat "${calls}")"
  # SELinux が無効なら何もしない。
  selinuxenabled() { return 1; }
  : > "${calls}"
  configure_selinux >/dev/null
  restore_nginx_contexts
  [ ! -s "${calls}" ] || fail "SELinux が無効なのに設定した: $(cat "${calls}")"
  # main の順: package → firewall → SELinux → 製品の配備（venv・unit の前に context の規則を置く）。
  main_body="$(sed -n '/^main() {$/,/^}$/p' "${repo}/platform/deploy/suite-init.sh")"
  # アプリ用の DB ユーザー（#1668）は、製品の配備（system schema の初期化）の前に作る。
  # provision_db_app_user は失敗しても続ける（#1756）ため、`if ! provision_db_app_user; then` の形。
  printf '%s\n' "${main_body}" | sed -E 's/^  if ! (provision_db_app_user); then$/  \1/' \
    | grep -E '^  (install_os_packages|configure_firewall|configure_selinux|provision_db_app_user|deploy_products)$' \
    | awk '{printf "%s ", $1}' \
    | grep -qx 'install_os_packages configure_firewall configure_selinux provision_db_app_user deploy_products ' \
    || fail "main の順が違う"
  # 最後に配備全体を確かめる（#1642。log の最後の check の行が結果）。URL の一覧の後、失敗の判定の前。
  printf '%s\n' "${main_body}" | grep -E '^  (configure_nginx|report_urls|suite_check_deployment .*)$' \
    | awk '{printf "%s ", $1}' | grep -qx 'configure_nginx report_urls suite_check_deployment ' \
    || fail "main の最後に配備の確認が無い"
  # 初回は Nginx が止まっている（reload は失敗の log を出す）。止まっていれば起動する。
  grep -qxF '  systemctl reload-or-restart nginx' "${repo}/platform/deploy/suite-init.sh" || fail "Nginx を reload-or-restart しない"
)

# 6. 完了の log の URL（#1561）: 入口（suite）を製品の URL の上に出し、転送先を添える。
(
  case_dir="${task_dir}/urls"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  printf '203.0.113.5\n' > "${PROPS_DIR}/public_ip.txt"
  SUITE_PRODUCTS=(rag nl2sql agent)
  expected="$(printf '%s\n' \
    '[suite-init]   suite: https://203.0.113.5/ (entry point; redirects to /agent/)' \
    '[suite-init]   rag: https://203.0.113.5/rag/' \
    '[suite-init]   nl2sql: https://203.0.113.5/nl2sql/' \
    '[suite-init]   agent: https://203.0.113.5/agent/' \
    '[suite-init]   CA certificate: https://203.0.113.5/platform/ca.crt (import it as a trusted root CA on client PCs)')"
  [ "$(report_urls)" = "${expected}" ] || fail "完了の log の URL: $(report_urls)"
  SUITE_PRODUCTS=(rag nl2sql)
  first="$(report_urls)"
  first="${first%%$'\n'*}"
  [ "${first}" = '[suite-init]   suite: https://203.0.113.5/ (entry point; redirects to /rag/)' ] \
    || fail "Agent が無いときの入口の転送先: ${first}"
)

# 7. 製品の init_script.sh の契約（#1316。単独では配備しない）: Nginx の site を書かない・/<製品>/ を base に build・
#    backend/.env の元。Nginx の location は platform/deploy/suite-nginx.sh の 1 か所だけ。
for product in rag nl2sql agent; do
  source_text="$(cat "${repo}/${product}/init_script.sh")"
  for expected in "FRONTEND_BASE_PATH=\"\${FRONTEND_BASE_PATH:-/${product}/}\"" \
    "FRONTEND_BASE_PATH='\${FRONTEND_BASE_PATH}' npm run build" 'BACKEND_ENV_SOURCE="${BACKEND_ENV_SOURCE:-' \
    'PR_SUITE_SKIP_PLATFORM_UI_BUILD'; do
    case "${source_text}" in
      *"${expected}"*) ;;
      *) fail "${product}/init_script.sh に ${expected} が無い" ;;
    esac
  done
  for unexpected in 'configure_nginx' 'PR_SUITE_MODE' 'sites-available' 'proxy_pass' 'APPLICATION_PORT'; do
    case "${source_text}" in
      *"${unexpected}"*) fail "${product}/init_script.sh に単独の配備（${unexpected}）が残っている" ;;
    esac
  done
done

# 8. 権限の不足で表の初期化に失敗した配備（#1648）: 製品の init_script.sh は backend を起動して 0 で終わる（画面から
#    直せる）が、最後の確認（suite_check_deployment）の system schema が NG になり、main は 0 以外で終わる。
(
  case_dir="${task_dir}/schema-ng"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  printf 'rag nl2sql\n' > "${PROPS_DIR}/products.txt"
  out="${case_dir}/main.log"
  install_os_packages() { :; }
  configure_firewall() { :; }
  configure_selinux() { :; }
  configure_tls() { :; }
  configure_nginx() { :; }
  report_urls() { :; }
  run_product_init() { log "deployed $1"; }
  systemctl() {
    case "$1" in
      list-unit-files)
        printf '%s\n' "production-ready-rag-backend.service enabled enabled" \
          "production-ready-nl2sql-backend.service enabled enabled"
        ;;
      is-active) printf 'active\n' ;;
      show) printf '0\n' ;;
    esac
  }
  curl() { return 0; }
  journalctl() { :; }
  # shellcheck disable=SC2034 # suite_check_deployment（source した suite-health.sh）が読む
  SUITE_CHECK_SETTLE_SECONDS=0
  # shellcheck disable=SC2034 # 同上
  SUITE_PRODUCT_LOG_DIR="${case_dir}"
  suite_run_schema_check() {
    case "$1" in
      nl2sql) printf 'status=missing・操作 failed・前回の操作の失敗 ORA-01031\n'; return 1 ;;
      *) printf 'status=ready\n' ;;
    esac
  }
  status=0
  main > "${out}" 2>&1 || status=$?
  [ "${status}" -ne 0 ] || fail "schema が NG なのに main が 0 で終わった: $(cat "${out}")"
  [ "${#SUITE_FAILED_PRODUCTS[@]}" -eq 0 ] || fail "製品の配備は成功している: ${SUITE_FAILED_PRODUCTS[*]}"
  grep -qxF '[suite-init] check: nl2sql schema NG（status=missing・操作 failed・前回の操作の失敗 ORA-01031）' "${out}" \
    || fail "schema の NG の行: $(cat "${out}")"
  grep -qF '[suite-init] check: nl2sql の DB ユーザー（PLATFORM_ORACLE_USER）の権限が足りない可能性があります' "${out}" \
    || fail "権限の不足の手がかり: $(cat "${out}")"
  tail -n 1 "${out}" | grep -q 'check: NG' || fail "最後の行が NG ではない: $(tail -n 1 "${out}")"
  # schema が ready なら 0 で終わる。
  suite_run_schema_check() { printf 'status=ready\n'; }
  main > "${out}" 2>&1 || fail "すべて OK なのに失敗した: $(cat "${out}")"
  tail -n 1 "${out}" | grep -q 'check: OK' || fail "最後の行が OK ではない: $(tail -n 1 "${out}")"
)

# アプリ用の DB ユーザー（#1668）: props の adb_admin.env があるときだけ、製品の配備の前に suite-db-user.py を
# uv run（固定の python-oracledb）で実行する。無ければ何もしない。失敗したら 0 以外を返し、Wallet の一時の展開を消す。
# main は失敗しても製品の配備と起動を続け、最後に失敗として返す（#1756。ADB に接続できなくてもアプリケーションは起動する）。
(
  case_dir="${task_dir}/db-user"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  calls="${case_dir}/uv.calls"
  selinux_enabled() { return 1; }
  uv() {
    printf '%s\n' "$*" >> "${calls}"
    local arg previous=""
    for arg in "$@"; do
      if [ "${previous}" = "--wallet-dir" ]; then
        [ -d "${arg}" ] || fail "Wallet の一時の directory が無い"
        [ "$(stat -c %a "${arg}")" = "700" ] || fail "Wallet の一時の directory は 0700"
        printf '%s\n' "${arg}" > "${case_dir}/wallet-dir"
      fi
      previous="${arg}"
    done
    return "${UV_STATUS:-0}"
  }
  out="${case_dir}/out.log"
  # adb_admin.env が無い: 作らない（uv も呼ばない）。
  provision_db_app_user > "${out}" 2>&1 || fail "adb_admin.env が無いのに失敗した: $(cat "${out}")"
  [ ! -e "${calls}" ] || fail "adb_admin.env が無いのに uv を呼んだ"
  grep -q "アプリ用の DB ユーザーは作りません" "${out}" || fail "作らない旨の log: $(cat "${out}")"
  # adb_admin.env がある: suite-db-user.py apply を固定の oracledb で実行し、0600 にする。
  printf "SUITE_DB_ADMIN_USER=ADMIN\nSUITE_DB_ADMIN_PASSWORD='stub'\nSUITE_DB_APP_USER='ADB_APP_USER'\n" > "${PROPS_DIR}/adb_admin.env"
  chmod 0644 "${PROPS_DIR}/adb_admin.env"
  provision_db_app_user > "${out}" 2>&1 || fail "成功するはず: $(cat "${out}")"
  [ "$(stat -c %a "${PROPS_DIR}/adb_admin.env")" = "600" ] || fail "adb_admin.env は 0600"
  call="$(cat "${calls}")"
  for expected in "run --no-project --python 3.12 --with oracledb==${SUITE_DB_USER_ORACLEDB_VERSION}" \
    "suite-db-user.py apply" "--admin-env ${PROPS_DIR}/adb_admin.env" "--app-env ${PROPS_DIR}/platform.env" \
    "--current-env ${SUITE_REPO_DIR}/platform/.env"; do
    printf '%s\n' "${call}" | grep -qF -- "${expected}" || fail "uv の引数に ${expected} が無い: ${call}"
  done
  [ ! -e "$(cat "${case_dir}/wallet-dir")" ] || fail "Wallet の一時の directory を消していない"
  grep -qxF "[suite-init] check: db user OK" "${out}" || fail "OK の行: $(cat "${out}")"
  # 失敗: 0 以外で返し、NG の行を出し、一時の directory を消す。
  status=0
  UV_STATUS=1 provision_db_app_user > "${out}" 2>&1 || status=$?
  [ "${status}" -eq 1 ] || fail "失敗を返さない（${status}）"
  grep -qF "[suite-init] check: db user NG" "${out}" || fail "NG の行: $(cat "${out}")"
  [ ! -e "$(cat "${case_dir}/wallet-dir")" ] || fail "失敗しても Wallet の一時の directory を消す"

  # main: DB ユーザーの準備が失敗しても（ADB に接続できない）、製品の配備・Nginx・確認まで進み、最後に失敗として返す。
  steps="${case_dir}/steps.log"
  for step in load_products validate_ports install_os_packages configure_firewall configure_selinux deploy_products \
    configure_tls configure_nginx report_urls suite_check_deployment; do
    eval "${step}() { printf '%s\\n' ${step} >> \"\${steps}\"; }"
  done
  status=0
  UV_STATUS=1 main > "${out}" 2>&1 || status=$?
  [ "${status}" -eq 1 ] || fail "DB ユーザーの失敗を最後に返さない（${status}）: $(cat "${out}")"
  [ "$(tr '\n' ' ' < "${steps}")" = "load_products validate_ports install_os_packages configure_firewall configure_selinux deploy_products configure_tls configure_nginx report_urls suite_check_deployment " ] \
    || fail "DB ユーザーの失敗で配備を止めた: $(tr '\n' ' ' < "${steps}")"
  grep -qF "WARNING: アプリ用の DB ユーザーを準備できませんでした。製品の配備と起動は続けます" "${out}" || fail "warning: $(cat "${out}")"
  tail -n 1 "${out}" | grep -qF "[suite-init] check: db user NG" || fail "最後の行: $(tail -n 1 "${out}")"
  # 成功すれば確認の結果を返す。
  : > "${steps}"
  main > "${out}" 2>&1 || fail "DB ユーザーが成功したのに失敗した: $(cat "${out}")"
  if grep -qF "WARNING: アプリ用の DB ユーザー" "${out}"; then fail "成功したのに warning を出した"; fi
)

echo "suite-init: pass"
