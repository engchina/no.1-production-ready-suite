#!/usr/bin/env bash
# rag/init_script.sh（OCI Resource Manager stack の Compute 初期化）の振る舞いを外部依存なしで検証する。
# docker compose が使える環境（CI の ubuntu-latest を含む）では、生成した override も compose で検証する。
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
    "${case_dir}/app/no.1-production-ready-suite/rag/backend" \
    "${case_dir}/app/no.1-production-ready-suite/platform" \
    "${case_dir}/units" \
    "${case_dir}/sites-available" \
    "${case_dir}/sites-enabled" \
    "${case_dir}/bin"
  export APP_ROOT="${case_dir}/app"
  export RAG_INIT_TEST_MODE=true
  export SYSTEMD_UNIT_DIR="${case_dir}/units"
  export NGINX_SITES_AVAILABLE_DIR="${case_dir}/sites-available"
  export NGINX_SITES_ENABLED_DIR="${case_dir}/sites-enabled"
  export DEPLOY_DIR="${case_dir}/deploy"
  export COMPOSE_WRAPPER="${case_dir}/bin/rag-compose"
}

run_compose_services_case() (
  local scenario="$1"
  local services="$2"
  local case_dir="${TEST_TMP_DIR}/${scenario}"
  prepare_case "${case_dir}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"
  trap - ERR

  printf '%s\n' "${services}" > "${APP_ROOT}/props/compose_services.txt"
  if load_compose_services > "${case_dir}/load.log" 2>&1; then
    printf '%s\n' "${COMPOSE_SERVICES[*]}" > "${case_dir}/services"
    echo ok > "${case_dir}/result"
  else
    echo rejected > "${case_dir}/result"
  fi
)

run_initialization_case() (
  local scenario="$1"
  local fail_pattern="$2"
  local case_dir="${TEST_TMP_DIR}/${scenario}"
  prepare_case "${case_dir}"
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

run_runtime_env_case() (
  local case_dir="${TEST_TMP_DIR}/runtime-env"
  prepare_case "${case_dir}"
  export APP_USER
  APP_USER="$(id -un)"
  export APP_GROUP
  APP_GROUP="$(id -gn)"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  cat > "${APP_ROOT}/props/backend.env" <<'EOF'
RAG_AUTH_MODE=production
RAG_AUTH_PASSWORD='Ab$cd#Ef12345'
RAG_AUTH_SESSION_SECRET=
RAG_AUDIT_CONTEXT_HASH_SALT=
EOF
  cat > "${APP_ROOT}/props/platform.env" <<'EOF'
PLATFORM_ORACLE_USER='rag_app'
PLATFORM_ORACLE_PASSWORD='Db$Pass#123'
EOF
  install_runtime_env
  cp "${BACKEND_DIR}/.env" "${case_dir}/first.env"
  cp "${PLATFORM_ENV_FILE}" "${case_dir}/first-platform.env"
  # 画面から保存した値（API key 等）は再実行で消えない。
  printf 'PLATFORM_OCI_ENTERPRISE_AI_API_KEY=saved-on-screen\n' >> "${PLATFORM_ENV_FILE}"
  install_runtime_env
  cp "${BACKEND_DIR}/.env" "${case_dir}/second.env"
  cp "${PLATFORM_ENV_FILE}" "${case_dir}/second-platform.env"
)

run_compose_files_case() (
  local case_dir="${TEST_TMP_DIR}/compose-files"
  prepare_case "${case_dir}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"
  COMPOSE_SERVICES=(backend ingestion-worker parser-unstructured parser-docling)

  systemctl() {
    printf '%s\n' "$*" >> "${case_dir}/systemctl.log"
  }

  write_compose_override
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

# --- compose の service（CPU / OCI だけ。GPU と未知の service は拒否する） ---
run_compose_services_case services-default "backend ingestion-worker preprocess-office-to-pdf parser-unstructured parser-docling"
test "$(cat "${TEST_TMP_DIR}/services-default/result")" = "ok" || fail "既定の service が拒否された"
test "$(cat "${TEST_TMP_DIR}/services-default/services")" = \
  "backend ingestion-worker preprocess-office-to-pdf parser-unstructured parser-docling" \
  || fail "service の一覧が compose_services.txt と一致しない"
run_compose_services_case services-gpu "backend ingestion-worker parser-unstructured parser-asr"
test "$(cat "${TEST_TMP_DIR}/services-gpu/result")" = "rejected" || fail "GPU の service（parser-asr）を拒否していない"
run_compose_services_case services-unknown "backend ingestion-worker parser-unstructured frontend"
test "$(cat "${TEST_TMP_DIR}/services-unknown/result")" = "rejected" || fail "許可していない service を拒否していない"
run_compose_services_case services-glob "backend ingestion-worker parser-unstructured *"
test "$(cat "${TEST_TMP_DIR}/services-glob/result")" = "rejected" || fail "glob が展開された、または拒否されていない"
run_compose_services_case services-missing-required "backend ingestion-worker parser-docling"
test "$(cat "${TEST_TMP_DIR}/services-missing-required/result")" = "rejected" \
  || fail "parser-unstructured が無い構成を拒否していない"

# --- DB 初期化（アプリの system schema CLI で適用する） ---
run_initialization_case success ""
test "$(cat "${TEST_TMP_DIR}/success/ready")" = "true" || fail "schema 初期化成功時に ready にならない"
grep -Fq "run --rm --no-deps -T backend uv run --no-sync python -m app.rag.system_schema_cli initialize | attempts=5" \
  "${TEST_TMP_DIR}/success/commands.log" || fail "system schema CLI が retry 付きで実行されていない"

run_initialization_case degraded 'system_schema_cli initialize'
test "$(cat "${TEST_TMP_DIR}/degraded/ready")" = "false" || fail "schema 初期化失敗時に ready=false にならない"

# --- backend/.env（生成する secret は1回だけ作り、再実行でも同じ値を使う） ---
run_runtime_env_case
first="${TEST_TMP_DIR}/runtime-env/first.env"
second="${TEST_TMP_DIR}/runtime-env/second.env"
grep -Eq '^RAG_AUTH_SESSION_SECRET=[0-9a-f]{64}$' "${first}" || fail "RAG_AUTH_SESSION_SECRET が生成されていない"
grep -Eq '^RAG_AUDIT_CONTEXT_HASH_SALT=[0-9a-f]{64}$' "${first}" || fail "RAG_AUDIT_CONTEXT_HASH_SALT が生成されていない"
cmp -s "${first}" "${second}" || fail "再実行で生成済みの secret が変わった"
grep -Fqx "RAG_AUTH_PASSWORD='Ab\$cd#Ef12345'" "${first}" || fail "入力の password が変更された"
test "$(stat -c '%a' "${TEST_TMP_DIR}/runtime-env/app/no.1-production-ready-suite/rag/backend/.env")" = "600" \
  || fail "backend/.env の permission が 0600 ではない"
test "$(stat -c '%a' "${TEST_TMP_DIR}/runtime-env/app/props/auth_session_secret")" = "600" \
  || fail "生成した secret の permission が 0600 ではない"

# --- platform/.env（3製品共通の設定。PLATFORM_*） ---
grep -Fqx "PLATFORM_ORACLE_PASSWORD='Db\$Pass#123'" "${TEST_TMP_DIR}/runtime-env/first-platform.env" \
  || fail "共通 .env に入力の DB password が入っていない"
if grep -q '^PLATFORM_' "${first}"; then
  fail "backend/.env に共通の設定（PLATFORM_*）が入っている"
fi
test "$(stat -c '%a' "${TEST_TMP_DIR}/runtime-env/app/no.1-production-ready-suite/platform/.env")" = "600" \
  || fail "platform/.env の permission が 0600 ではない"
grep -Fqx 'PLATFORM_OCI_ENTERPRISE_AI_API_KEY=saved-on-screen' "${TEST_TMP_DIR}/runtime-env/second-platform.env" \
  || fail "再実行で画面から保存した共通 .env の値が消えた"

# --- compose override / wrapper / systemd ---
run_compose_files_case
override="${TEST_TMP_DIR}/compose-files/deploy/docker-compose.oci.yml"
wrapper="${TEST_TMP_DIR}/compose-files/bin/rag-compose"
unit="${TEST_TMP_DIR}/compose-files/units/production-ready-rag.service"
wallet_dir="${TEST_TMP_DIR}/compose-files/app/wallet"
grep -Fq 'ports: !override' "${override}" || fail "backend の公開 port を置き換えていない"
grep -Fq '"127.0.0.1:8000:8000"' "${override}" || fail "backend が 127.0.0.1 だけに bind されていない"
test "$(grep -c "${wallet_dir}:${wallet_dir}" "${override}")" = "2" \
  || fail "Wallet が backend と ingestion-worker の両方に mount されていない"
test "$(grep -c 'restart: unless-stopped' "${override}")" = "2" || fail "restart policy が付いていない"
grep -Fq -- '--project-name production-ready-rag' "${wrapper}" || fail "compose の project 名が固定されていない"
grep -Fq -- "-f ${TEST_TMP_DIR}/compose-files/app/no.1-production-ready-suite/rag/docker-compose.yml" "${wrapper}" \
  || fail "wrapper が rag/docker-compose.yml を使っていない"
grep -Fq -- "-f ${override}" "${wrapper}" || fail "wrapper が override を重ねていない"
test -x "${wrapper}" || fail "wrapper が実行可能ではない"
grep -Fq "ExecStart=${wrapper} up -d --no-build backend ingestion-worker parser-unstructured parser-docling" "${unit}" \
  || fail "systemd unit が選んだ service だけを起動していない"
grep -Fq 'Requires=docker.service' "${unit}" || fail "systemd unit が docker に依存していない"
grep -q '^enable production-ready-rag.service$' "${TEST_TMP_DIR}/compose-files/systemctl.log" \
  || fail "compose の unit が enable されていない"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  config="$(
    docker compose --project-name production-ready-rag --project-directory "${REPO_DIR}" \
      -f "${REPO_DIR}/docker-compose.yml" -f "${override}" config backend ingestion-worker
  )" || fail "生成した override を docker compose が読めない"
  printf '%s\n' "${config}" | grep -Fq 'host_ip: 127.0.0.1' || fail "compose の結果で backend が 127.0.0.1 に bind されていない"
  test "$(printf '%s\n' "${config}" | grep -c 'published: "8000"')" = "1" \
    || fail "compose の結果に 0.0.0.0:8000 の公開が残っている"
else
  echo "SKIP: docker compose が無いため、override の compose 検証を省略した。"
fi

# --- Nginx（認証は backend の login。Nginx は配信と proxy だけ） ---
run_nginx_case
site="${TEST_TMP_DIR}/nginx/sites-available/production-ready-rag"
grep -Fq 'listen 8080;' "${site}" || fail "application port で listen していない"
grep -Fq 'proxy_pass http://127.0.0.1:8000;' "${site}" || fail "/api/ が backend 8000 へ proxy されていない"
grep -Fq 'root /' "${site}" || fail "frontend の静的 build を配信していない"
grep -Fq '/rag/frontend/dist;' "${site}" || fail "rag/frontend/dist を配信していない"
awk '/location \/api\/ \{/,/\}/' "${site}" | grep -Fq 'proxy_buffering off;' || fail "SSE のため proxy buffering を無効にしていない"
grep -Fq 'client_max_body_size 210M;' "${site}" || fail "upload の上限が backend の RAG_MAX_UPLOAD_BYTES に合っていない"
awk '/location = \/health \{/,/\}/' "${site}" | grep -Fq '/api/health;' || fail "/health が backend の /api/health を返していない"
if grep -Fq 'auth_basic' "${site}"; then
  fail "RAG は backend の login を使う（Nginx の Basic 認証は置かない）"
fi
test -L "${TEST_TMP_DIR}/nginx/sites-enabled/production-ready-rag" || fail "site が有効化されていない"
test ! -e "${TEST_TMP_DIR}/nginx/sites-enabled/default" || fail "default site が残っている"
grep -q '^nginx -t$' "${TEST_TMP_DIR}/nginx/systemctl.log" || fail "nginx -t が実行されていない"

# --- 静的な不変条件 ---
init_script="${REPO_DIR}/init_script.sh"
grep -Fq 'WALLET_DIR="${APP_ROOT}/wallet"' "${init_script}" || fail "Wallet の配置先が変わった"
grep -Fq 'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;' "${init_script}" || fail "Wallet の file が 0600 ではない"
if grep -Eiq '^\s*[^#]*\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|USER)\b' "${init_script}"; then
  fail "init_script.sh に DDL を埋め込んでいる"
fi
if grep -Eq -- '--profile[ =]gpu|parser-asr|nvidia' "${init_script}"; then
  fail "init_script.sh が GPU の service を扱っている"
fi

echo "rag init_script deployment behavior verified."
