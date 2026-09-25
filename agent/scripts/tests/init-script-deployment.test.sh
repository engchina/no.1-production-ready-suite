#!/usr/bin/env bash
# agent/init_script.sh（OCI Resource Manager stack の Compute 初期化）の振る舞いを外部依存なしで検証する。
set -euo pipefail

TEST_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${TEST_SCRIPT_DIR}/../.." && pwd)"
TEST_TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEST_TMP_DIR}"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

prepare_case() {
  local case_dir="$1"
  mkdir -p \
    "${case_dir}/app/props" \
    "${case_dir}/app/no.1-production-ready-suite/agent/backend" \
    "${case_dir}/units" \
    "${case_dir}/sites-available" \
    "${case_dir}/sites-enabled" \
    "${case_dir}/etc-nginx"
  export APP_ROOT="${case_dir}/app"
  export AGENT_INIT_TEST_MODE=true
  export SYSTEMD_UNIT_DIR="${case_dir}/units"
  export NGINX_SITES_AVAILABLE_DIR="${case_dir}/sites-available"
  export NGINX_SITES_ENABLED_DIR="${case_dir}/sites-enabled"
  export NGINX_HTPASSWD_PATH="${case_dir}/etc-nginx/production-ready-agent.htpasswd"
  NGINX_GROUP="$(id -gn)"
  export NGINX_GROUP
}

run_initialization_case() (
  local scenario="$1"
  local fail_pattern="$2"
  local case_dir="${TEST_TMP_DIR}/${scenario}"
  prepare_case "${case_dir}"
  export APPLICATION_PORT=80
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  retry_command() {
    local attempts="$1"
    shift
    printf '%s | attempts=%s\n' "$*" "${attempts}" >> "${case_dir}/commands.log"
    if [ -n "${fail_pattern}" ] && printf '%s\n' "$*" | grep -q "${fail_pattern}"; then
      return 1
    fi
    return 0
  }

  initialize_database_schema
  printf '%s\n' "${DATABASE_INITIALIZATION_READY}" > "${case_dir}/ready"
)

run_systemd_case() (
  local case_dir="${TEST_TMP_DIR}/systemd"
  prepare_case "${case_dir}"
  export APPLICATION_PORT=80
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  systemctl() {
    printf '%s\n' "$*" >> "${case_dir}/systemctl.log"
  }

  configure_systemd
)

run_nginx_case() (
  local case_dir="${TEST_TMP_DIR}/nginx"
  prepare_case "${case_dir}"
  export APPLICATION_PORT=8080
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  printf 'agent_admin\n' > "${APP_ROOT}/props/basic_auth_user.txt"
  printf '%s' 'Test-Passw0rd-Value' > "${APP_ROOT}/props/basic_auth_password"
  touch "${NGINX_SITES_ENABLED_DIR}/default"

  systemctl() {
    printf '%s\n' "$*" >> "${case_dir}/systemctl.log"
  }
  nginx() {
    printf 'nginx %s\n' "$*" >> "${case_dir}/systemctl.log"
  }

  configure_basic_auth
  configure_nginx
)

run_public_base_url_case() (
  local scenario="$1"
  local port="$2"
  local initial_value="$3"
  local case_dir="${TEST_TMP_DIR}/${scenario}"
  prepare_case "${case_dir}"
  export APPLICATION_PORT="${port}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  curl() {
    printf '%s\n' '[{"vnicId":"ocid1.vnic.oc1..test","privateIp":"10.0.1.23","subnetCidrBlock":"10.0.1.0/24"}]'
  }

  printf 'AGENT_RUNTIME_DISPATCH_MODE=in_process\nAGENT_CONTROL_PLANE_PUBLIC_BASE_URL=%s\n' \
    "${initial_value}" > "${case_dir}/backend.env"
  resolve_public_base_url "${case_dir}/backend.env"
)

# --- DB 初期化（Runtime repository の import で table を作る） ---
run_initialization_case success ""
test "$(cat "${TEST_TMP_DIR}/success/ready")" = "true" || fail "DB 初期化成功時に ready にならない"
grep -Fq "uv run python -c 'import app.features.agent.runtime'" "${TEST_TMP_DIR}/success/commands.log" \
  || fail "Runtime repository の初期化 command が実行されていない"
grep -q 'attempts=5' "${TEST_TMP_DIR}/success/commands.log" || fail "DB 初期化が retry されていない"

run_initialization_case degraded 'import app.features.agent.runtime'
test "$(cat "${TEST_TMP_DIR}/degraded/ready")" = "false" || fail "DB 初期化失敗時に ready=false にならない"

# --- systemd: backend は 127.0.0.1:8020、1 worker ---
run_systemd_case
unit="${TEST_TMP_DIR}/systemd/units/production-ready-agent-backend.service"
test -f "${unit}" || fail "backend unit が作成されていない"
grep -Fq -- '--bind 127.0.0.1:8020 --workers 1' "${unit}" || fail "backend の bind/worker 数が想定と異なる"
grep -Fq 'User=ubuntu' "${unit}" || fail "backend が ubuntu user で動かない"
grep -q '^enable production-ready-agent-backend.service$' "${TEST_TMP_DIR}/systemd/systemctl.log" \
  || fail "backend unit が enable されていない"
if grep -q 'dispatcher' "${unit}"; then
  fail "stack は外部 dispatcher を起動しない"
fi

# --- Nginx + Basic 認証 ---
run_nginx_case
site="${TEST_TMP_DIR}/nginx/sites-available/production-ready-agent"
htpasswd="${TEST_TMP_DIR}/nginx/etc-nginx/production-ready-agent.htpasswd"
grep -Fq 'listen 8080;' "${site}" || fail "application port で listen していない"
grep -Fq 'proxy_pass http://127.0.0.1:8020;' "${site}" || fail "/api/ が backend 8020 へ proxy されていない"
grep -Fq "auth_basic_user_file ${htpasswd};" "${site}" || fail "Basic 認証の password file が設定されていない"
awk '/location \/api\/mcp\/ \{/,/\}/' "${site}" | grep -Fq 'auth_basic off;' \
  || fail "Binding MCP endpoint が Basic 認証の対象外になっていない"
awk '/location = \/health \{/,/\}/' "${site}" | grep -Fq 'auth_basic off;' \
  || fail "/health が Basic 認証の対象外になっていない"
if awk '/location \/api\/ \{/,/\}/' "${site}" | grep -Fq 'auth_basic off;'; then
  fail "/api/ が Basic 認証の対象外になっている"
fi
test -L "${TEST_TMP_DIR}/nginx/sites-enabled/production-ready-agent" || fail "site が有効化されていない"
test ! -e "${TEST_TMP_DIR}/nginx/sites-enabled/default" || fail "default site が残っている"
grep -q '^nginx -t$' "${TEST_TMP_DIR}/nginx/systemctl.log" || fail "nginx -t が実行されていない"
grep -Eq '^agent_admin:\$6\$' "${htpasswd}" || fail "htpasswd が SHA-512 crypt で作成されていない"
if grep -Fq 'Test-Passw0rd-Value' "${htpasswd}"; then
  fail "htpasswd に平文 password が残っている"
fi
test "$(stat -c '%a' "${htpasswd}")" = "640" || fail "htpasswd の permission が 0640 ではない"
hash_field="$(cut -d: -f2 "${htpasswd}")"
salt="$(printf '%s' "${hash_field}" | cut -d'$' -f3)"
test "$(printf '%s' 'Test-Passw0rd-Value' | openssl passwd -6 -salt "${salt}" -stdin)" = "${hash_field}" \
  || fail "htpasswd の hash が入力 password と一致しない"

# --- AGENT_CONTROL_PLANE_PUBLIC_BASE_URL ---
run_public_base_url_case public-url-default 80 ""
grep -qx 'AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=http://10.0.1.23/api' \
  "${TEST_TMP_DIR}/public-url-default/backend.env" || fail "private IP から public base URL を補っていない"
run_public_base_url_case public-url-port 8080 ""
grep -qx 'AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=http://10.0.1.23:8080/api' \
  "${TEST_TMP_DIR}/public-url-port/backend.env" || fail "port 付きの public base URL になっていない"
run_public_base_url_case public-url-explicit 80 "https://agent.example.com/api"
grep -qx 'AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=https://agent.example.com/api' \
  "${TEST_TMP_DIR}/public-url-explicit/backend.env" || fail "明示した public base URL が上書きされた"

# --- 静的な不変条件 ---
grep -Fq 'WALLET_DIR="${APP_ROOT}/wallet"' "${REPO_DIR}/init_script.sh"
grep -Fq 'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;' "${REPO_DIR}/init_script.sh"
if grep -Eq '^\s*(docker|docker-compose)\b' "${REPO_DIR}/init_script.sh"; then
  fail "直接配備に Docker を要求している"
fi
if grep -Eiq '^\s*[^#]*\b(CREATE|ALTER|DROP)\s+TABLE\b' "${REPO_DIR}/init_script.sh"; then
  fail "init_script.sh に DDL を埋め込んでいる"
fi

echo "agent init_script deployment behavior verified."
