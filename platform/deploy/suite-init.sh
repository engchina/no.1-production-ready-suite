#!/usr/bin/env bash
# 1 台の Compute に、選んだ製品（RAG / NL2SQL / Agent）を配備する（#1316）。
# OCI Resource Manager の統合 stack（terraform/stack）の cloud-init から呼ぶ。
#
# - 各製品の配備（uv の venv・systemd の unit・DB の初期化・frontend の build）は、今までどおり各製品の
#   init_script.sh が持つ。ここからは PR_SUITE_MODE=true で呼び、製品の Nginx の site は書かせない。
#   frontend は /<製品>/ を base に build させる（FRONTEND_BASE_PATH）。backend・worker・port は製品ごとに独立
#   （RAG 8000・NL2SQL 8010・Agent 8020。RAG の前処理 / parser は 18010〜18028。すべて 127.0.0.1）。
# - Nginx の site は 1 つだけ（platform/deploy/suite-nginx.sh）。/rag/ /nl2sql/ /agent/ と、/ → /agent/。
# - HTTPS（既定 on）: 自作の Root CA と IP のサーバー証明書（platform/deploy/suite-tls.sh）で 443。80 は 301 で https へ。
#   CA の証明書は /platform/ca.crt で配る（CA の秘密鍵は Compute の中だけ）。
#
# cloud-init が /u01/aipoc/props に置くもの:
#   products.txt（配備する製品。空白区切り）・https_enabled.txt（true / false）・assign_public_ip.txt（true / false）
#   platform.env（3 製品で共通の platform/.env）・<製品>.backend.env（各製品の backend/.env）・rag_services.txt・wallet.zip など
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if [ "${SUITE_INIT_TEST_MODE:-false}" != "true" ]; then
  exec > >(tee -a /var/log/suite-init.log) 2>&1
fi

SUITE_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=suite-nginx.sh
source "${SUITE_DEPLOY_DIR}/suite-nginx.sh"
# shellcheck source=suite-tls.sh
source "${SUITE_DEPLOY_DIR}/suite-tls.sh"

APP_ROOT="${APP_ROOT:-/u01/aipoc}"
# 3 製品のプロセスは同じユーザーで動かす（共通の platform/.env・model-settings.json・Wallet・~/.oci を
# 画面から読み書きするため。#1316。製品ごとの Compute では RAG だけ専用の ragsvc で動かしていた）。
APP_USER="${APP_USER:-ubuntu}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
APP_HOME="${APP_HOME:-/home/${APP_USER}}"
SUITE_REPO_DIR="${SUITE_REPO_DIR:-${APP_ROOT}/no.1-production-ready-suite}"
PROPS_DIR="${PROPS_DIR:-${APP_ROOT}/props}"
SSL_DIR="${SUITE_SSL_DIR:-${APP_ROOT}/ssl}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx/conf.d}"
NGINX_SITES_AVAILABLE_DIR="${NGINX_SITES_AVAILABLE_DIR:-/etc/nginx/sites-available}"
NGINX_SITES_ENABLED_DIR="${NGINX_SITES_ENABLED_DIR:-/etc/nginx/sites-enabled}"
SUITE_SITE_NAME="production-ready-suite"
TLS_RENEW_UNIT="production-ready-suite-tls-renew"
# RAG の backend の RAG_MAX_UPLOAD_BYTES の既定値と、Nginx の multipart の余白（rag/init_script.sh と同じ値。
# verify_stack_contract.py が照合する）。
RAG_DEFAULT_MAX_UPLOAD_BYTES=209715200
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

# OS の firewall で 80（と HTTPS が on なら 443）を開ける。subnet の security list は stack の外で管理する。
configure_firewall() {
  local port ports=(80)
  if https_enabled; then
    ports+=(443)
  fi
  if ! command -v iptables >/dev/null 2>&1; then
    return 0
  fi
  for port in "${ports[@]}"; do
    if ! iptables -C INPUT -m state --state NEW -p tcp --dport "${port}" -j ACCEPT 2>/dev/null; then
      iptables -I INPUT 6 -m state --state NEW -p tcp --dport "${port}" -j ACCEPT || true
    fi
  done
  netfilter-persistent save || true
}

# 製品の init_script.sh に渡す環境変数（1 行に 1 つ）。
product_init_env() {
  local product="$1"
  local skip_platform_ui_build="$2"
  printf '%s\n' \
    "PR_SUITE_MODE=true" \
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

# RAG の backend の RAG_MAX_UPLOAD_BYTES（backend/.env。無ければ既定値）+ multipart の余白（Refs #306）。
rag_client_max_body_size() {
  local env_file="${SUITE_REPO_DIR}/rag/backend/.env"
  local bytes
  bytes="$(sed -n 's/^RAG_MAX_UPLOAD_BYTES=//p' "${env_file}" 2>/dev/null | tail -n 1 | tr -d "[:space:]'\"")"
  bytes="${bytes:-${RAG_DEFAULT_MAX_UPLOAD_BYTES}}"
  if ! printf '%s\n' "${bytes}" | grep -Eq '^[1-9][0-9]*$'; then
    log "RAG_MAX_UPLOAD_BYTES must be a positive integer: ${bytes}"
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
  local site="${NGINX_SITES_AVAILABLE_DIR}/${SUITE_SITE_NAME}"
  local body_size https=false product
  body_size="$(rag_client_max_body_size)"
  if https_enabled; then
    https=true
  fi
  mkdir -p "${NGINX_CONF_DIR}" "${NGINX_SITES_AVAILABLE_DIR}" "${NGINX_SITES_ENABLED_DIR}"
  install -m 0644 "${template_dir}/logging.conf" "${NGINX_CONF_DIR}/production-ready-logging.conf"
  install -m 0644 "${template_dir}/login-rate-limit.conf" "${NGINX_CONF_DIR}/production-ready-login-rate-limit.conf"
  log "Configuring Nginx (HTTPS: ${https}, products: ${SUITE_PRODUCTS[*]})."
  suite_nginx_site "${https}" "${SUITE_REPO_DIR}" "${SSL_DIR}" "${body_size}" "${SUITE_PRODUCTS[@]}" > "${site}.tmp"
  mv -f "${site}.tmp" "${site}"
  ln -sfn "${site}" "${NGINX_SITES_ENABLED_DIR}/${SUITE_SITE_NAME}"
  # 製品ごとの Compute の site（製品の init_script.sh が書いていたもの）と既定の site は使わない。
  rm -f -- "${NGINX_SITES_ENABLED_DIR:?}/default"
  for product in "${SUITE_PRODUCTS_ORDER[@]}"; do
    rm -f -- "${NGINX_SITES_ENABLED_DIR:?}/production-ready-${product}"
  done
  # Nginx の worker が frontend の dist を読めるよう、repository までの directory を辿れるようにする。
  chmod o+x "${APP_ROOT}" "${SUITE_REPO_DIR}" 2>/dev/null || true
  nginx -t
  systemctl enable nginx
  systemctl reload nginx || systemctl restart nginx
}

report_urls() {
  local host scheme=http product
  if https_enabled; then
    scheme=https
  fi
  host="$(tr -d '[:space:]' 2>/dev/null < "${PROPS_DIR}/public_ip.txt" || true)"
  host="${host:-$(suite_tls_detect_private_ip || printf '<compute-ip>')}"
  for product in "${SUITE_PRODUCTS[@]}"; do
    log "  ${product}: ${scheme}://${host}/${product}/"
  done
  if https_enabled; then
    log "  CA certificate: ${scheme}://${host}/platform/ca.crt (import it as a trusted root CA on client PCs)"
  fi
}

main() {
  log "Starting single-Compute deployment."
  load_products
  configure_firewall
  deploy_products
  configure_tls
  configure_nginx
  log "Deployment finished. Log in as system_admin (PLATFORM_ADMIN_LOGIN_USER_PASSWORD)."
  report_urls
  if [ "${#SUITE_FAILED_PRODUCTS[@]}" -gt 0 ]; then
    log "Failed products: ${SUITE_FAILED_PRODUCTS[*]}"
    return 1
  fi
}

if [ "${SUITE_INIT_TEST_MODE:-false}" != "true" ]; then
  main "$@"
fi
