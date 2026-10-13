#!/usr/bin/env bash
# 1 台の Compute に、選んだ製品（RAG / NL2SQL / Agent）を配備する（#1316）。
# OCI Resource Manager の統合 stack（terraform/stack）の cloud-init から呼ぶ。
#
# - 3 製品はこの suite でセットで配備する（単独では配備しない）。各製品の配備（uv の venv・systemd の unit・
#   DB の初期化・frontend の build）は各製品の init_script.sh が持ち、ここから順に呼ぶ。Nginx の site は書かない。
#   frontend は /<製品>/ を base に build させる（FRONTEND_BASE_PATH）。backend・worker・port は製品ごとに独立
#   （RAG 8000・NL2SQL 8010・Agent 8020。RAG の前処理 / parser は 18010〜18028。すべて 127.0.0.1）。
# - Nginx の site は 1 つだけ（platform/deploy/suite-nginx.sh）。/rag/ /nl2sql/ /agent/ と、/ → /agent/。
# - HTTPS（既定 on）: 自作の Root CA と IP のサーバー証明書（platform/deploy/suite-tls.sh）で https_port（既定 443）。
#   http_port（既定 80）は 301 で https へ。HTTPS が off なら http_port でそのまま配信する。
#   CA の証明書は /platform/ca.crt で配る（CA の秘密鍵は Compute の中だけ）。
#
# cloud-init が /u01/aipoc/props に置くもの:
#   products.txt（配備する製品。空白区切り）・https_enabled.txt（true / false）・http_port.txt / https_port.txt（公開の port）・
#   assign_public_ip.txt（true / false）
#   platform.env（3 製品で共通の platform/.env）・<製品>.backend.env（各製品の backend/.env）・rag_services.txt・wallet.zip など
#
# Compute の OS は Oracle Linux 9（#1611）。package は dnf（Nginx は AppStream の module）、firewall は firewalld、
# SELinux は enforcing のまま、次の設定で動かす（configure_selinux）:
#   - suite の repository を usr_t にする（systemd が venv の実行ファイルと EnvironmentFile を読めるように。
#     /u01 の既定の default_t は systemd も Nginx も読めない）。新しく作るファイルは親の usr_t を受け継ぐ。
#   - 各製品の frontend の dist は httpd_sys_content_t、証明書の directory は cert_t（Nginx が読む）。
#   - Nginx から 127.0.0.1 の backend への proxy は httpd_can_network_connect。80 / 443 以外の公開の port は http_port_t。
set -euo pipefail

if [ "${SUITE_INIT_TEST_MODE:-false}" != "true" ]; then
  exec > >(tee -a /var/log/suite-init.log) 2>&1
fi

SUITE_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=suite-nginx.sh
source "${SUITE_DEPLOY_DIR}/suite-nginx.sh"
# shellcheck source=suite-tls.sh
source "${SUITE_DEPLOY_DIR}/suite-tls.sh"
# shellcheck source=suite-health.sh
source "${SUITE_DEPLOY_DIR}/suite-health.sh"

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
# 3 製品のプロセスは同じユーザーで動かす（共通の platform/.env・model-settings.json・Wallet・~/.oci を
# 画面から読み書きするため。#1316。製品ごとの Compute では RAG だけ専用の ragsvc で動かしていた）。
APP_USER="${APP_USER:-opc}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
APP_HOME="${APP_HOME:-/home/${APP_USER}}"
SUITE_REPO_DIR="${SUITE_REPO_DIR:-${APP_ROOT}/no.1-production-ready-suite}"
PROPS_DIR="${PROPS_DIR:-${APP_ROOT}/props}"
SSL_DIR="${SUITE_SSL_DIR:-${APP_ROOT}/ssl}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
# Oracle Linux の Nginx は /etc/nginx/conf.d/*.conf を読む（site も conf.d に置く）。nginx.conf は package の既定の
# server（port 80）を外したもの（platform/templates/nginx/nginx.conf）に置き換える。
NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx/conf.d}"
NGINX_MAIN_CONF="${NGINX_MAIN_CONF:-/etc/nginx/nginx.conf}"
SUITE_SITE_NAME="production-ready-suite"
# AppStream の nginx の module の stream（1.24 系。CI の Suite / Oracle Linux 9 も同じ stream で確かめる）。
NGINX_MODULE_STREAM="1.24"
# suite-init.sh が dnf で入れる OS の package（製品の init_script.sh は製品が使うものだけを入れる）。
SUITE_OS_PACKAGES=(firewalld git nginx openssl policycoreutils-python-utils tar unzip)
TLS_RENEW_UNIT="production-ready-suite-tls-renew"
# 各製品の backend の <製品>_MAX_UPLOAD_BYTES の既定値と、Nginx の multipart の余白（#306・#1597。
# verify_stack_contract.py が backend の既定値・suite-nginx.sh の既定の上限と照合する）。
# shellcheck disable=SC2034 # product_client_max_body_size が製品の名前から参照する
RAG_DEFAULT_MAX_UPLOAD_BYTES=209715200
# shellcheck disable=SC2034 # product_client_max_body_size が製品の名前から参照する
NL2SQL_DEFAULT_MAX_UPLOAD_BYTES=209715200
# shellcheck disable=SC2034 # product_client_max_body_size が製品の名前から参照する
AGENT_DEFAULT_MAX_UPLOAD_BYTES=104857600
NGINX_UPLOAD_MARGIN_MIB=10

SUITE_PRODUCTS=()
SUITE_FAILED_PRODUCTS=()

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

log() {
  printf '[suite-init] %s\n' "$*"
}

# 配備する製品（products.txt）。並びは rag → nl2sql → agent に固定する。
load_products() {
  local names=() name
  read -r -a names <<< "$(tr '\n' ' ' 2>/dev/null < "${PROPS_DIR}/products.txt" || true)"
  for name in "${names[@]}"; do
    if ! suite_contains "${name}" "${SUITE_PRODUCTS_ORDER[@]}"; then
      log "Unknown product in ${PROPS_DIR}/products.txt: ${name}"
      return 1
    fi
  done
  SUITE_PRODUCTS=()
  for name in "${SUITE_PRODUCTS_ORDER[@]}"; do
    if suite_contains "${name}" "${names[@]}"; then
      SUITE_PRODUCTS+=("${name}")
    fi
  done
  if [ "${#SUITE_PRODUCTS[@]}" -eq 0 ]; then
    log "No product is selected in ${PROPS_DIR}/products.txt."
    return 1
  fi
  log "Products: ${SUITE_PRODUCTS[*]}"
}

# HTTPS（既定 on）。
https_enabled() {
  local value
  value="$(tr -d '[:space:]' 2>/dev/null < "${PROPS_DIR}/https_enabled.txt" || true)"
  [ "${value:-true}" != "false" ]
}

# 公開の port（cloud-init の props。無ければ 80 / 443）。
http_port() {
  local value
  value="$(tr -d '[:space:]' 2>/dev/null < "${PROPS_DIR}/http_port.txt" || true)"
  printf '%s\n' "${value:-80}"
}

https_port() {
  local value
  value="$(tr -d '[:space:]' 2>/dev/null < "${PROPS_DIR}/https_port.txt" || true)"
  printf '%s\n' "${value:-443}"
}

# 公開の port が使えるか（SSH・よく使われる service・Compute の中で使う port を拒む）を、配備の前に確かめる。
validate_ports() {
  suite_validate_public_port http_port "$(http_port)" || return 1
  if https_enabled; then
    suite_validate_public_port https_port "$(https_port)" || return 1
  fi
}

# 公開する port（http_port と、HTTPS が on なら https_port。同じ値なら 1 つ）。
public_ports() {
  local http https
  http="$(http_port)"
  printf '%s\n' "${http}"
  if https_enabled; then
    https="$(https_port)"
    if [ "${https}" != "${http}" ]; then
      printf '%s\n' "${https}"
    fi
  fi
}

suite_dnf() {
  local attempt status=0
  for attempt in 1 2 3 4 5; do
    if dnf -y "$@"; then
      return 0
    fi
    status="$?"
    log "dnf failed with status ${status} on attempt ${attempt}/5: dnf -y $*"
    sleep $((attempt * 5))
  done
  return "${status}"
}

# OS の package（Nginx・firewalld・SELinux の道具など）。Nginx は AppStream の module の stream を固定して入れる。
install_os_packages() {
  log "Installing OS packages: ${SUITE_OS_PACKAGES[*]} (nginx:${NGINX_MODULE_STREAM})."
  suite_dnf module enable "nginx:${NGINX_MODULE_STREAM}"
  suite_dnf install "${SUITE_OS_PACKAGES[@]}"
}

# OS の firewall（firewalld）で公開の port を開ける。subnet の security list は stack の外で管理する。
configure_firewall() {
  local port
  systemctl enable --now firewalld
  while IFS= read -r port; do
    firewall-cmd --permanent --add-port="${port}/tcp"
  done < <(public_ports)
  firewall-cmd --reload
  log "firewalld: opened $(public_ports | tr '\n' ' ')(tcp)."
}

# SELinux（Oracle Linux 9 の既定の enforcing のまま動かす）。無効の環境では何もしない。
selinux_enabled() {
  command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled
}

# 製品の配備（venv・systemd の unit）の前に行う SELinux の設定。file context の規則は、同じ規則があれば
# semanage が置き換える（再実行してよい）。
configure_selinux() {
  local port product
  if ! selinux_enabled; then
    log "SELinux is disabled; skipping SELinux contexts."
    return 0
  fi
  log "Configuring SELinux contexts (repository usr_t, frontend dist httpd_sys_content_t, certificates cert_t)."
  semanage fcontext -a -t usr_t "${SUITE_REPO_DIR}(/.*)?"
  for product in "${SUITE_PRODUCTS_ORDER[@]}"; do
    semanage fcontext -a -t httpd_sys_content_t "${SUITE_REPO_DIR}/${product}/frontend/dist(/.*)?"
  done
  semanage fcontext -a -t cert_t "${SSL_DIR}(/.*)?"
  restorecon -R "${SUITE_REPO_DIR}"
  # Nginx から 127.0.0.1 の各製品の backend へ proxy する。
  setsebool -P httpd_can_network_connect 1
  while IFS= read -r port; do
    case "${port}" in
      80 | 443) ;;
      # 既に別の type（例: 8080 の http_cache_port_t）なら semanage が http_port_t に置き換える。
      *) semanage port -a -t http_port_t -p tcp "${port}" ;;
    esac
  done < <(public_ports)
}

# Nginx が読むファイルの context を戻す（frontend の build は dist を作り直す。証明書は configure_tls が作る）。
restore_nginx_contexts() {
  local product paths=()
  selinux_enabled || return 0
  for product in "${SUITE_PRODUCTS[@]}"; do
    if [ -d "${SUITE_REPO_DIR}/${product}/frontend/dist" ]; then
      paths+=("${SUITE_REPO_DIR}/${product}/frontend/dist")
    fi
  done
  if [ -d "${SSL_DIR}" ]; then
    paths+=("${SSL_DIR}")
  fi
  if [ "${#paths[@]}" -gt 0 ]; then
    restorecon -R "${paths[@]}"
  fi
}

# 製品の init_script.sh に渡す環境変数（1 行に 1 つ）。
product_init_env() {
  local product="$1"
  local skip_platform_ui_build="$2"
  printf '%s\n' \
    "APP_ROOT=${APP_ROOT}" \
    "APP_USER=${APP_USER}" \
    "APP_GROUP=${APP_GROUP}" \
    "FRONTEND_BASE_PATH=/${product}/" \
    "BACKEND_ENV_SOURCE=${PROPS_DIR}/${product}.backend.env" \
    "PR_SUITE_SKIP_PLATFORM_UI_BUILD=${skip_platform_ui_build}"
  if [ "${product}" = "rag" ]; then
    printf '%s\n' \
      "SERVICE_USER=${APP_USER}" \
      "SERVICE_GROUP=${APP_GROUP}" \
      "SERVICE_HOME=${APP_HOME}"
  fi
}

run_product_init() {
  local product="$1"
  local skip_platform_ui_build="$2"
  local init_script="${SUITE_REPO_DIR}/${product}/init_script.sh"
  local env_args=()
  if [ ! -f "${init_script}" ]; then
    log "Missing ${init_script}."
    return 1
  fi
  mapfile -t env_args < <(product_init_env "${product}" "${skip_platform_ui_build}")
  log "Deploying ${product} via ${init_script} (${env_args[*]})."
  env "${env_args[@]}" bash "${init_script}"
}

# 製品を順に配備する。1 つが失敗しても残りの製品と Nginx は設定し、最後に失敗として返す。
deploy_products() {
  local product skip_platform_ui_build=false
  for product in "${SUITE_PRODUCTS[@]}"; do
    if run_product_init "${product}" "${skip_platform_ui_build}"; then
      # 共有 UI（platform）は最初に成功した製品が build した。後の製品は build し直さない。
      skip_platform_ui_build=true
    else
      log "WARNING: ${product} deployment failed. See /var/log/${product}-init.log."
      SUITE_FAILED_PRODUCTS+=("${product}")
    fi
  done
}

# 製品の API の Nginx の本文の上限: backend の <製品>_MAX_UPLOAD_BYTES（backend/.env。無ければ既定値）+
# multipart の余白（Refs #306・#1597）。$1: 製品（rag / nl2sql / agent）
product_client_max_body_size() {
  local product="$1"
  local prefix="${1^^}"
  local env_file="${SUITE_REPO_DIR}/${product}/backend/.env"
  local default_name="${prefix}_DEFAULT_MAX_UPLOAD_BYTES"
  local bytes
  bytes="$(sed -n "s/^${prefix}_MAX_UPLOAD_BYTES=//p" "${env_file}" 2>/dev/null | tail -n 1 | tr -d "[:space:]'\"")"
  bytes="${bytes:-${!default_name}}"
  if ! printf '%s\n' "${bytes}" | grep -Eq '^[1-9][0-9]*$'; then
    log "${prefix}_MAX_UPLOAD_BYTES must be a positive integer: ${bytes}"
    return 1
  fi
  printf '%sM\n' "$(( (bytes + 1048575) / 1048576 + NGINX_UPLOAD_MARGIN_MIB ))"
}

configure_tls() {
  local private_ip public_ip
  if ! https_enabled; then
    log "HTTPS is disabled; serving HTTP on port 80."
    systemctl disable --now "${TLS_RENEW_UNIT}.timer" 2>/dev/null || true
    rm -f -- "${SYSTEMD_UNIT_DIR:?}/${TLS_RENEW_UNIT}.service" "${SYSTEMD_UNIT_DIR:?}/${TLS_RENEW_UNIT}.timer"
    return 0
  fi
  private_ip="$(suite_tls_detect_private_ip || true)"
  public_ip="$(suite_tls_detect_public_ip || true)"
  if [ -n "${public_ip}" ]; then
    printf '%s\n' "${public_ip}" > "${PROPS_DIR}/public_ip.txt"
  fi
  log "Preparing HTTPS certificates (public IP: ${public_ip:-unknown}, private IP: ${private_ip:-unknown})."
  SUITE_TLS_PROPS_DIR="${PROPS_DIR}" suite_tls_ensure "${SSL_DIR}" "${public_ip}" "${private_ip}" false
  write_tls_renew_units
}

# サーバー証明書（397 日）を期限の 30 日前に作り直す timer。
write_tls_renew_units() {
  cat > "${SYSTEMD_UNIT_DIR}/${TLS_RENEW_UNIT}.service" <<EOF
# platform/deploy/suite-init.sh が生成する（手で編集しない）。#1316
[Unit]
Description=Renew the Production Ready Suite HTTPS server certificate when it is about to expire

[Service]
Type=oneshot
Environment=SUITE_TLS_DIR=${SSL_DIR}
Environment=SUITE_TLS_PROPS_DIR=${PROPS_DIR}
ExecStart=/bin/bash ${SUITE_DEPLOY_DIR}/suite-tls.sh renew-if-needed
EOF
  cat > "${SYSTEMD_UNIT_DIR}/${TLS_RENEW_UNIT}.timer" <<EOF
# platform/deploy/suite-init.sh が生成する（手で編集しない）。#1316
[Unit]
Description=Daily check of the Production Ready Suite HTTPS server certificate

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "${TLS_RENEW_UNIT}.timer"
}

configure_nginx() {
  local template_dir="${SUITE_REPO_DIR}/platform/templates/nginx"
  local site="${NGINX_CONF_DIR}/${SUITE_SITE_NAME}.conf"
  local https=false rag_body nl2sql_body agent_body
  rag_body="$(product_client_max_body_size rag)"
  nl2sql_body="$(product_client_max_body_size nl2sql)"
  agent_body="$(product_client_max_body_size agent)"
  if https_enabled; then
    https=true
  fi
  mkdir -p "${NGINX_CONF_DIR}"
  # package の既定の server（port 80）を外した nginx.conf（suite の site が公開の port の default_server になる）。
  install -m 0644 "${template_dir}/nginx.conf" "${NGINX_MAIN_CONF}"
  install -m 0644 "${template_dir}/logging.conf" "${NGINX_CONF_DIR}/production-ready-logging.conf"
  install -m 0644 "${template_dir}/login-rate-limit.conf" "${NGINX_CONF_DIR}/production-ready-login-rate-limit.conf"
  log "Configuring Nginx (HTTPS: ${https}, products: ${SUITE_PRODUCTS[*]})."
  SUITE_RAG_CLIENT_MAX_BODY_SIZE="${rag_body}" SUITE_NL2SQL_CLIENT_MAX_BODY_SIZE="${nl2sql_body}" \
    SUITE_AGENT_CLIENT_MAX_BODY_SIZE="${agent_body}" \
    suite_nginx_site "${https}" "${SUITE_REPO_DIR}" "${SSL_DIR}" "$(http_port)" "$(https_port)" \
    "${SUITE_PRODUCTS[@]}" > "${site}.tmp"
  mv -f "${site}.tmp" "${site}"
  # Nginx の worker（nginx ユーザー）が frontend の dist を読めるよう、repository までの directory を辿れるようにする。
  chmod o+x "${APP_ROOT}" "${SUITE_REPO_DIR}" 2>/dev/null || true
  restore_nginx_contexts
  nginx -t
  systemctl enable nginx
  # 初回は Nginx が止まっている（reload は「not active, cannot reload」で失敗する）。止まっていれば起動する。
  systemctl reload-or-restart nginx
}

# 利用者が開く URL の起点（既定の port 80 / 443 は書かない）。
public_base_url() {
  local host="$1"
  local scheme=http port
  port="$(http_port)"
  if https_enabled; then
    scheme=https
    port="$(https_port)"
  fi
  if { [ "${scheme}" = "http" ] && [ "${port}" = "80" ]; } || { [ "${scheme}" = "https" ] && [ "${port}" = "443" ]; }; then
    printf '%s://%s\n' "${scheme}" "${host}"
  else
    printf '%s://%s:%s\n' "${scheme}" "${host}" "${port}"
  fi
}

report_urls() {
  local host base product
  host="$(tr -d '[:space:]' 2>/dev/null < "${PROPS_DIR}/public_ip.txt" || true)"
  host="${host:-$(suite_tls_detect_private_ip || printf '<compute-ip>')}"
  base="$(public_base_url "${host}")"
  # 入口（/）。転送先は Nginx の site と同じ規則で決める（#1561）。
  log "  suite: ${base}/ (entry point; redirects to $(suite_root_redirect_target "${SUITE_PRODUCTS[@]}"))"
  for product in "${SUITE_PRODUCTS[@]}"; do
    log "  ${product}: ${base}/${product}/"
  done
  if https_enabled; then
    log "  CA certificate: ${base}/platform/ca.crt (import it as a trusted root CA on client PCs)"
  fi
}

main() {
  log "Starting single-Compute deployment."
  load_products
  validate_ports
  install_os_packages
  configure_firewall
  configure_selinux
  deploy_products
  configure_tls
  configure_nginx
  log "Deployment finished. Log in as system_admin (PLATFORM_ADMIN_LOGIN_USER_PASSWORD)."
  report_urls
  # 配備全体の確認（service・backend と Nginx を通した health・Nginx。#1642）。log の最後の check の行が結果。
  local check_status=0
  suite_check_deployment "$(suite_local_nginx_base_url "${PROPS_DIR}")" "${SUITE_PRODUCTS[@]}" || check_status=$?
  if [ "${#SUITE_FAILED_PRODUCTS[@]}" -gt 0 ]; then
    log "Failed products: ${SUITE_FAILED_PRODUCTS[*]}"
    return 1
  fi
  return "${check_status}"
}

if [ "${SUITE_INIT_TEST_MODE:-false}" != "true" ]; then
  main "$@"
fi
