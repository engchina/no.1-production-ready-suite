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
    "${case_dir}/sites-enabled"
  export APP_ROOT="${case_dir}/app"
  export AGENT_INIT_TEST_MODE=true
  export SYSTEMD_UNIT_DIR="${case_dir}/units"
  export NGINX_SITES_AVAILABLE_DIR="${case_dir}/sites-available"
  export NGINX_SITES_ENABLED_DIR="${case_dir}/sites-enabled"
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

  touch "${NGINX_SITES_ENABLED_DIR}/default"

  systemctl() {
    printf '%s\n' "$*" >> "${case_dir}/systemctl.log"
  }
  nginx() {
    printf 'nginx %s\n' "$*" >> "${case_dir}/systemctl.log"
  }

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

run_install_env_case() (
  local case_dir="${TEST_TMP_DIR}/install-env"
  prepare_case "${case_dir}"
  mkdir -p "${case_dir}/app/no.1-production-ready-suite/platform"
  APP_USER="$(id -un)"
  APP_GROUP="$(id -gn)"
  export APP_USER APP_GROUP APPLICATION_PORT=80
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  curl() {
    printf '%s\n' '[{"vnicId":"ocid1.vnic.oc1..test","privateIp":"10.0.1.23","subnetCidrBlock":"10.0.1.0/24"}]'
  }

  printf 'PLATFORM_ORACLE_DSN=agentdb_high\n' > "${PROPS_DIR}/platform.env"
  printf 'AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=\n' > "${PROPS_DIR}/backend.env"
  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1], "w").writestr("tnsnames.ora", "agentdb_high=\n")' \
    "${PROPS_DIR}/wallet.zip"

  install_runtime_env
)

# --- 共通 .env（platform/.env）と Agent の backend/.env（#211） ---
run_install_env_case
install_suite="${TEST_TMP_DIR}/install-env/app/no.1-production-ready-suite"
grep -qx 'PLATFORM_ORACLE_DSN=agentdb_high' "${install_suite}/platform/.env" \
  || fail "共通 .env が platform/.env に置かれていない"
test "$(stat -c '%a' "${install_suite}/platform/.env")" = "600" || fail "platform/.env の permission が 0600 ではない"
grep -qx 'AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=http://10.0.1.23/api' "${install_suite}/agent/backend/.env" \
  || fail "backend/.env の public base URL が補われていない"
test "$(stat -c '%a' "${install_suite}/agent/backend/.env")" = "600" || fail "backend/.env の permission が 0600 ではない"
if grep -q 'PLATFORM_' "${install_suite}/agent/backend/.env"; then
  fail "backend/.env に共通の設定が入っている"
fi
# 再実行では、画面で保存した共通 .env を上書きしない。
printf 'PLATFORM_ORACLE_DSN=saved_from_ui\n' > "${install_suite}/platform/.env"
run_install_env_case
grep -qx 'PLATFORM_ORACLE_DSN=saved_from_ui' "${install_suite}/platform/.env" \
  || fail "再実行で共通 .env が上書きされた"

# --- DB 初期化（Runtime repository の import と、共通認証・Agent 権限の migration。#215） ---
run_initialization_case success ""
test "$(cat "${TEST_TMP_DIR}/success/ready")" = "true" || fail "DB 初期化成功時に ready にならない"
grep -Fq "uv run python -c 'import app.features.agent.runtime'" "${TEST_TMP_DIR}/success/commands.log" \
  || fail "Runtime repository の初期化 command が実行されていない"
grep -Fq "uv run python -m app.cli.agent_system_schema --initialize" "${TEST_TMP_DIR}/success/commands.log" \
  || fail "システムテーブルの作成・更新が実行されていない"
test "$(grep -c 'attempts=5' "${TEST_TMP_DIR}/success/commands.log")" = "2" \
  || fail "DB 初期化の各 command が retry されていない"
runtime_line="$(grep -n 'import app.features.agent.runtime' "${TEST_TMP_DIR}/success/commands.log" | cut -d: -f1)"
security_line="$(grep -n 'agent_system_schema' "${TEST_TMP_DIR}/success/commands.log" | cut -d: -f1)"
test "${runtime_line}" -lt "${security_line}" || fail "Runtime repository の初期化の後に migration を実行していない"

run_initialization_case degraded 'import app.features.agent.runtime'
test "$(cat "${TEST_TMP_DIR}/degraded/ready")" = "false" || fail "DB 初期化失敗時に ready=false にならない"
grep -Fq "agent_system_schema" "${TEST_TMP_DIR}/degraded/commands.log" \
  || fail "Runtime repository の失敗時も migration を試みていない"

run_initialization_case security-degraded 'agent_system_schema'
test "$(cat "${TEST_TMP_DIR}/security-degraded/ready")" = "false" \
  || fail "migration 失敗時に ready=false にならない"

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

# --- Nginx（認証は backend の共通認証。Basic 認証は使わない。#215） ---
run_nginx_case
site="${TEST_TMP_DIR}/nginx/sites-available/production-ready-agent"
grep -Fq 'listen 8080;' "${site}" || fail "application port で listen していない"
grep -Fq 'proxy_pass http://127.0.0.1:8020;' "${site}" || fail "/api/ が backend 8020 へ proxy されていない"
if grep -Fq 'auth_basic' "${site}"; then
  fail "Nginx に Basic 認証が残っている"
fi
awk '/location \/api\/mcp\/ \{/,/\}/' "${site}" | grep -Fq 'proxy_pass http://127.0.0.1:8020;' \
  || fail "Binding MCP endpoint が backend へ proxy されていない"
awk '/location \/api\/ \{/,/\}/' "${site}" | grep -Fq 'proxy_set_header Host $http_host;' \
  || fail "/api/ が port を含む Host を渡していない（WebSocket の Origin 検証）"
awk '/location \/api\/ \{/,/\}/' "${site}" | grep -Fq 'proxy_set_header Upgrade $http_upgrade;' \
  || fail "/api/ が WebSocket の upgrade を渡していない"
test -L "${TEST_TMP_DIR}/nginx/sites-enabled/production-ready-agent" || fail "site が有効化されていない"
test ! -e "${TEST_TMP_DIR}/nginx/sites-enabled/default" || fail "default site が残っている"
grep -q '^nginx -t$' "${TEST_TMP_DIR}/nginx/systemctl.log" || fail "nginx -t が実行されていない"

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
if grep -Eq 'configure_basic_auth|basic_auth_(user|password)|htpasswd' "${REPO_DIR}/init_script.sh"; then
  fail "Basic 認証の設定が残っている"
fi
grep -Fq 'WALLET_DIR="${APP_ROOT}/wallet"' "${REPO_DIR}/init_script.sh"
grep -Fq 'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;' "${REPO_DIR}/init_script.sh"
if grep -Eq '^\s*(docker|docker-compose)\b' "${REPO_DIR}/init_script.sh"; then
  fail "直接配備に Docker を要求している"
fi
if grep -Eiq '^\s*[^#]*\b(CREATE|ALTER|DROP)\s+TABLE\b' "${REPO_DIR}/init_script.sh"; then
  fail "init_script.sh に DDL を埋め込んでいる"
fi

echo "agent init_script deployment behavior verified."
