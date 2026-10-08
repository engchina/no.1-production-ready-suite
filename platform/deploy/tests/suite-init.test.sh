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
    "${case_dir}/conf.d" "${case_dir}/sites-available" "${case_dir}/sites-enabled"
  ln -s "${repo}/platform" "${case_dir}/app/no.1-production-ready-suite/platform"
  export SUITE_INIT_TEST_MODE=true
  export APP_ROOT="${case_dir}/app"
  export SYSTEMD_UNIT_DIR="${case_dir}/units"
  export NGINX_CONF_DIR="${case_dir}/conf.d"
  export NGINX_SITES_AVAILABLE_DIR="${case_dir}/sites-available"
  export NGINX_SITES_ENABLED_DIR="${case_dir}/sites-enabled"
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
    "PR_SUITE_SKIP_PLATFORM_UI_BUILD=false" "SERVICE_USER=ubuntu" "SERVICE_GROUP=ubuntu" "SERVICE_HOME=/home/ubuntu"; do
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

# 4. Nginx: site を 1 つだけ書き、製品ごとの site と default を外す。RAG の上限は backend/.env から。
(
  case_dir="${task_dir}/nginx"
  prepare_case "${case_dir}"
  # shellcheck source=../suite-init.sh
  source "${repo}/platform/deploy/suite-init.sh"
  SUITE_PRODUCTS=(rag agent)
  printf 'true\n' > "${PROPS_DIR}/https_enabled.txt"
  for name in default production-ready-rag production-ready-agent; do
    touch "${NGINX_SITES_ENABLED_DIR}/${name}"
  done
  mkdir -p "${SUITE_REPO_DIR}/rag/backend" 2>/dev/null || true
  nginx() { printf 'nginx %s\n' "$*" >> "${case_dir}/commands.log"; }
  systemctl() { printf 'systemctl %s\n' "$*" >> "${case_dir}/commands.log"; }
  configure_nginx >/dev/null
  site="${NGINX_SITES_AVAILABLE_DIR}/production-ready-suite"
  [ -L "${NGINX_SITES_ENABLED_DIR}/production-ready-suite" ] || fail "site を有効にしない"
  for name in default production-ready-rag production-ready-agent; do
    [ ! -e "${NGINX_SITES_ENABLED_DIR}/${name}" ] || fail "${name} を外さない"
  done
  grep -q 'listen 443 ssl http2 default_server;' "${site}" || fail "HTTPS の site ではない"
  grep -q 'client_max_body_size 210M;' "${site}" || fail "RAG の既定の上限（200 MiB + 10 MiB）"
  grep -q 'return 302 /agent/;' "${site}" || fail "/ は /agent/"
  if grep -q '/nl2sql/' "${site}"; then fail "配備しない NL2SQL の location を書いた"; fi
  [ -f "${NGINX_CONF_DIR}/production-ready-logging.conf" ] || fail "logging.conf を置かない"
  [ -f "${NGINX_CONF_DIR}/production-ready-login-rate-limit.conf" ] || fail "login-rate-limit.conf を置かない"
  grep -qx 'nginx -t' "${case_dir}/commands.log" || fail "nginx -t を実行しない"
  printf 'RAG_MAX_UPLOAD_BYTES=104857600\n' > "${case_dir}/app/no.1-production-ready-suite/rag/backend/.env"
  [ "$(rag_client_max_body_size)" = "110M" ] || fail "RAG_MAX_UPLOAD_BYTES から上限を作る"
  # MiB へ切り上げ、引用符を扱う。不正な値なら失敗する（#306）。
  printf "RAG_MAX_UPLOAD_BYTES='1000000'\n" > "${case_dir}/app/no.1-production-ready-suite/rag/backend/.env"
  [ "$(rag_client_max_body_size)" = "11M" ] || fail "RAG_MAX_UPLOAD_BYTES を MiB へ切り上げていない"
  printf 'RAG_MAX_UPLOAD_BYTES=200MB\n' > "${case_dir}/app/no.1-production-ready-suite/rag/backend/.env"
  if rag_client_max_body_size >/dev/null 2>&1; then fail "不正な RAG_MAX_UPLOAD_BYTES を受け入れた"; fi
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

# 6. 製品の init_script.sh の契約（#1316。単独では配備しない）: Nginx の site を書かない・/<製品>/ を base に build・
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

echo "suite-init: pass"
