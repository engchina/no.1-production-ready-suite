#!/usr/bin/env bash
# rag/init_script.sh（OCI Resource Manager stack の Compute 初期化）の振る舞いを外部依存なしで検証する。
# RAG は Docker を使わず、uv の venv + systemd + Nginx で動かす（#286）。systemctl / nginx / runuser は
# fake にし、手元の systemd は触らない。visudo があれば、生成した sudoers の構文も確かめる。
set -euo pipefail

TEST_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${TEST_SCRIPT_DIR}/../.." && pwd)"
TEST_TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEST_TMP_DIR:?}"' EXIT

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
    "${case_dir}/sudoers.d" \
    "${case_dir}/sites-available" \
    "${case_dir}/sites-enabled" \
    "${case_dir}/bin" \
    "${case_dir}/service-home"
  export APP_ROOT="${case_dir}/app"
  export RAG_INIT_TEST_MODE=true
  export SYSTEMD_UNIT_DIR="${case_dir}/units"
  export SUDOERS_DIR="${case_dir}/sudoers.d"
  export NGINX_LOGGING_CONF_DIR="${case_dir}/conf.d"
  export DATA_DIR="${case_dir}/data"
  export SERVICE_HOME="${case_dir}/service-home"
  export LEGACY_COMPOSE_WRAPPER="${case_dir}/bin/rag-compose"
  export DOCKER_VOLUMES_DIR="${case_dir}/docker-volumes"
  export APP_USER SERVICE_USER APP_GROUP SERVICE_GROUP
  APP_USER="$(id -un)"
  SERVICE_USER="$(id -un)"
  APP_GROUP="$(id -gn)"
  SERVICE_GROUP="$(id -gn)"
}

# systemctl の fake。enable / disable は ${FAKE_CASE_DIR}/enabled/<unit> で持ち、呼び出しを記録する。
install_fake_systemctl() {
  mkdir -p "$1/enabled"
  FAKE_CASE_DIR="$1"
  systemctl() {
    printf '%s\n' "$*" >> "${FAKE_CASE_DIR}/systemctl.log"
    local unit
    case "$1" in
      enable)
        shift
        [ "${1:-}" = "--now" ] && shift
        for unit in "$@"; do touch "${FAKE_CASE_DIR}/enabled/${unit}"; done
        ;;
      disable)
        shift
        [ "${1:-}" = "--now" ] && shift
        for unit in "$@"; do rm -f -- "${FAKE_CASE_DIR:?}/enabled/${unit:?}"; done
        ;;
      is-enabled)
        shift
        [ "${1:-}" = "--quiet" ] && shift
        [ -e "${FAKE_CASE_DIR}/enabled/$1" ]
        ;;
    esac
  }
}

run_services_case() (
  local scenario="$1"
  local services="$2"
  local file_name="${3:-rag_services.txt}"
  local case_dir="${TEST_TMP_DIR}/${scenario}"
  prepare_case "${case_dir}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"
  trap - ERR

  printf '%s\n' "${services}" > "${APP_ROOT}/props/${file_name}"
  if load_rag_services > "${case_dir}/load.log" 2>&1; then
    printf '%s\n' "${RAG_SERVICES[*]}" > "${case_dir}/services"
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

run_venv_case() (
  local case_dir="${TEST_TMP_DIR}/venvs"
  prepare_case "${case_dir}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"
  RAG_SERVICES=(preprocess-pii-redact parser-docling parser-unstructured)

  retry_command() {
    local attempts="$1"
    shift
    printf '%s | attempts=%s\n' "$*" "${attempts}" >> "${case_dir}/commands.log"
    # docling のモデル取得は失敗させ、配備が止まらないことを確かめる。
    if printf '%s\n' "$*" | grep -q 'docling-tools models download'; then
      return 1
    fi
    return 0
  }

  install_service_venvs > "${case_dir}/venvs.log" 2>&1
)

run_runtime_env_case() (
  local case_dir="${TEST_TMP_DIR}/runtime-env"
  prepare_case "${case_dir}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  cat > "${APP_ROOT}/props/backend.env" <<'EOF'
RAG_AUTH_MODE=production
RAG_PARSER_ADAPTER_BACKEND='unst$ructured'
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

# configure_systemd を 2 回（初回の配備・再配備）実行する。再配備の前に、利用者が画面で
# parser-unstructured を停止した（disable --now）状態にし、stack から parser-oci-genai-vision を外す。
run_systemd_case() (
  local case_dir="${TEST_TMP_DIR}/systemd"
  prepare_case "${case_dir}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"
  install_fake_systemctl "${case_dir}"
  if ! command -v visudo >/dev/null 2>&1; then
    visudo() {
      echo "SKIP: visudo が無いため sudoers の構文検査を省略した。" >&2
    }
  fi

  RAG_SERVICES=(preprocess-office-to-pdf parser-docling parser-unstructured parser-oci-genai-vision)
  configure_systemd > "${case_dir}/first.log" 2>&1
  cp "${case_dir}/systemctl.log" "${case_dir}/first-systemctl.log"
  cp "${SUDOERS_DIR}/production-ready-rag-services" "${case_dir}/first-sudoers"

  # 画面で parser-unstructured を停止した（app.services.systemd の disable --now）。
  systemctl disable --now production-ready-rag-parser-unstructured.service
  : > "${case_dir}/systemctl.log"
  RAG_SERVICES=(preprocess-office-to-pdf parser-docling parser-unstructured)
  configure_systemd > "${case_dir}/second.log" 2>&1
  cp "${case_dir}/systemctl.log" "${case_dir}/second-systemctl.log"
)

run_migration_case() (
  local case_dir="${TEST_TMP_DIR}/migration"
  prepare_case "${case_dir}"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"
  install_fake_systemctl "${case_dir}"

  printf '[Unit]\nDescription=legacy compose\n' > "${SYSTEMD_UNIT_DIR}/production-ready-rag.service"
  cat > "${LEGACY_COMPOSE_WRAPPER}" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "${case_dir}/compose.log"
EOF
  chmod +x "${LEGACY_COMPOSE_WRAPPER}"
  mkdir -p "${DOCKER_VOLUMES_DIR}/production-ready-rag_backend-local-storage/_data/uploads" \
    "${DOCKER_VOLUMES_DIR}/production-ready-rag_oci-config/_data"
  printf 'original\n' > "${DOCKER_VOLUMES_DIR}/production-ready-rag_backend-local-storage/_data/uploads/a.pdf"
  printf '[DEFAULT]\n' > "${DOCKER_VOLUMES_DIR}/production-ready-rag_oci-config/_data/config"

  migrate_from_docker_compose > "${case_dir}/migration.log" 2>&1
  # 2 回目は何もしない（既にデータがある場所は上書きしない）。
  printf 'changed\n' > "${DOCKER_VOLUMES_DIR}/production-ready-rag_backend-local-storage/_data/uploads/a.pdf"
  migrate_from_docker_compose >> "${case_dir}/migration.log" 2>&1
)

# --- 配備する前処理 / parser（CPU / OCI だけ。GPU と未知の service は拒否する） ---
run_services_case services-default "preprocess-office-to-pdf parser-unstructured parser-docling"
test "$(cat "${TEST_TMP_DIR}/services-default/result")" = "ok" || fail "既定の service が拒否された"
test "$(cat "${TEST_TMP_DIR}/services-default/services")" = \
  "preprocess-office-to-pdf parser-unstructured parser-docling" \
  || fail "service の一覧が rag_services.txt と一致しない"
run_services_case services-legacy-file "backend ingestion-worker parser-unstructured parser-docling" compose_services.txt
test "$(cat "${TEST_TMP_DIR}/services-legacy-file/result")" = "ok" \
  || fail "以前の stack の compose_services.txt を読めない"
test "$(cat "${TEST_TMP_DIR}/services-legacy-file/services")" = "parser-unstructured parser-docling" \
  || fail "以前の stack の backend / ingestion-worker を前処理 / parser として扱った"
run_services_case services-gpu "parser-docling parser-asr"
test "$(cat "${TEST_TMP_DIR}/services-gpu/result")" = "rejected" || fail "GPU の service（parser-asr）を拒否していない"
run_services_case services-unknown "parser-docling frontend"
test "$(cat "${TEST_TMP_DIR}/services-unknown/result")" = "rejected" || fail "許可していない service を拒否していない"
run_services_case services-removed-marker "parser-docling parser-marker"
test "$(cat "${TEST_TMP_DIR}/services-removed-marker/result")" = "rejected" \
  || fail "削除した parser-marker（#270）を拒否していない"
run_services_case services-glob "parser-docling *"
test "$(cat "${TEST_TMP_DIR}/services-glob/result")" = "rejected" || fail "glob が展開された、または拒否されていない"
run_services_case services-missing-required "parser-unstructured preprocess-office-to-pdf"
test "$(cat "${TEST_TMP_DIR}/services-missing-required/result")" = "rejected" \
  || fail "既定の解析エンジン（parser-docling）が無い構成を拒否していない"

# --- DB 初期化（アプリの system schema CLI を SERVICE_USER で実行する） ---
run_initialization_case success ""
test "$(cat "${TEST_TMP_DIR}/success/ready")" = "true" || fail "schema 初期化成功時に ready にならない"
grep -Fq "run_as_service_user_in_dir ${TEST_TMP_DIR}/success/app/no.1-production-ready-suite/rag/backend .venv/bin/python -m app.rag.system_schema_cli initialize | attempts=5" \
  "${TEST_TMP_DIR}/success/commands.log" || fail "system schema CLI が SERVICE_USER・retry 付きで実行されていない"
if grep -q 'docker\|compose' "${TEST_TMP_DIR}/success/commands.log"; then
  fail "system schema CLI を docker で実行している"
fi
run_initialization_case degraded 'system_schema_cli initialize'
test "$(cat "${TEST_TMP_DIR}/degraded/ready")" = "false" || fail "schema 初期化失敗時に ready=false にならない"

# --- サービスごとの uv の venv（Python 3.12・lock どおり） ---
run_venv_case
venv_commands="${TEST_TMP_DIR}/venvs/commands.log"
rag_dir="${TEST_TMP_DIR}/venvs/app/no.1-production-ready-suite/rag"
grep -Fq "run_as_app_user_in_dir ${rag_dir}/backend uv sync --locked --no-dev --python 3.12 | attempts=3" "${venv_commands}" \
  || fail "backend の venv を uv sync --locked --no-dev --python 3.12 で作っていない"
grep -Fq "run_as_app_user_in_dir ${rag_dir}/services/parsers/docling uv sync --locked --no-dev --python 3.12 | attempts=3" "${venv_commands}" \
  || fail "parser-docling の venv をサービスのディレクトリで作っていない"
grep -Fq "run_as_app_user_in_dir ${rag_dir}/services/parsers/unstructured uv sync --locked --no-dev --python 3.12 | attempts=3" "${venv_commands}" \
  || fail "parser-unstructured の venv をサービスのディレクトリで作っていない"
grep -Fq "run_as_app_user_in_dir ${rag_dir}/services/preprocess/pii_redact uv sync --locked --no-dev --python 3.12 --inexact | attempts=3" "${venv_commands}" \
  || fail "pii_redact の venv が spaCy のモデルを残す --inexact で作られていない"
grep -Fq "spacy download ja_core_news_lg" "${venv_commands}" || fail "pii_redact の日本語 NER モデルを入れていない"
grep -Fq "run_as_service_user_in_dir ${rag_dir}/services/parsers/docling .venv/bin/docling-tools models download" "${venv_commands}" \
  || fail "docling のモデルを SERVICE_USER の ~/.cache に取得していない"
grep -Fq "WARNING: parser-docling could not download its models now" "${TEST_TMP_DIR}/venvs/venvs.log" \
  || fail "docling のモデル取得の失敗で配備が止まった、または警告を出していない"

# --- backend/.env（生成する secret は1回だけ作り、再実行でも同じ値を使う） ---
run_runtime_env_case
first="${TEST_TMP_DIR}/runtime-env/first.env"
second="${TEST_TMP_DIR}/runtime-env/second.env"
grep -Eq '^RAG_AUDIT_CONTEXT_HASH_SALT=[0-9a-f]{64}$' "${first}" || fail "RAG_AUDIT_CONTEXT_HASH_SALT が生成されていない"
cmp -s "${first}" "${second}" || fail "再実行で生成済みの secret が変わった"
grep -Fqx "RAG_PARSER_ADAPTER_BACKEND='unst\$ructured'" "${first}" || fail "入力の値が変更された"
test "$(stat -c '%a' "${TEST_TMP_DIR}/runtime-env/app/no.1-production-ready-suite/rag/backend/.env")" = "600" \
  || fail "backend/.env の permission が 0600 ではない"
test "$(stat -c '%a' "${TEST_TMP_DIR}/runtime-env/app/props/audit_context_hash_salt")" = "600" \
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

# --- systemd の unit・状態の保持・sudoers ---
run_systemd_case
units="${TEST_TMP_DIR}/systemd/units"
rag_dir="${TEST_TMP_DIR}/systemd/app/no.1-production-ready-suite/rag"
service_user="$(id -un)"
backend_unit="${units}/production-ready-rag-backend.service"
worker_unit="${units}/production-ready-rag-ingestion-worker.service"
docling_unit="${units}/production-ready-rag-parser-docling.service"
vision_unit="${units}/production-ready-rag-parser-oci-genai-vision.service"
first_log="${TEST_TMP_DIR}/systemd/first-systemctl.log"
second_log="${TEST_TMP_DIR}/systemd/second-systemctl.log"
grep -Fqx "ExecStart=${rag_dir}/backend/.venv/bin/gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 127.0.0.1:8000 --workers 2 --timeout 60 --graceful-timeout 30 --keep-alive 5 --error-logfile - --no-control-socket" "${backend_unit}" \
  || fail "backend が venv の gunicorn で 127.0.0.1:8000 だけに bind していない"
grep -Fqx "User=${service_user}" "${backend_unit}" || fail "backend が SERVICE_USER で動いていない"
grep -Fqx 'Environment=RAG_ENVIRONMENT=prod' "${backend_unit}" || fail "backend の RAG_ENVIRONMENT が prod ではない"
grep -Fqx 'Environment=RAG_INGESTION_QUEUE_INPROCESS_WORKER_ENABLED=false' "${backend_unit}" \
  || fail "backend の in-process worker を無効にしていない（取込ジョブを二重に実行しうる）"
grep -Fqx "ExecStart=${rag_dir}/backend/.venv/bin/python -m app.rag.ingestion_worker" "${worker_unit}" \
  || fail "ingestion-worker の unit が app.rag.ingestion_worker を起動していない"
grep -Fqx 'KillMode=mixed' "${worker_unit}" || fail "ingestion-worker の KillMode が mixed ではない（#305）"
grep -Fqx 'TimeoutStopSec=90' "${worker_unit}" || fail "ingestion-worker の TimeoutStopSec が 90 秒ではない"
grep -Fqx 'Environment=RAG_INGESTION_QUEUE_SHUTDOWN_GRACE_SECONDS=60' "${worker_unit}" \
  || fail "ingestion-worker の停止の grace が unit で 60 秒になっていない（#357）"
# 停止の grace + 子の停止待ち（SIGTERM から SIGKILL まで 10 秒）より TimeoutStopSec を長くする（#357）。
worker_grace="$(sed -n 's/^Environment=RAG_INGESTION_QUEUE_SHUTDOWN_GRACE_SECONDS=//p' "${worker_unit}")"
worker_stop_timeout="$(sed -n 's/^TimeoutStopSec=//p' "${worker_unit}")"
[ "${worker_stop_timeout}" -gt $((worker_grace + 10)) ] \
  || fail "ingestion-worker の TimeoutStopSec が停止の grace + 10 秒より短い（job を QUEUED に戻す前に SIGKILL される）"
grep -Fqx 'Restart=on-failure' "${worker_unit}" || fail "ingestion-worker の Restart が on-failure ではない"
if grep -Fq 'INPROCESS_WORKER' "${worker_unit}"; then
  fail "ingestion-worker の unit が in-process worker の設定を持っている"
fi
grep -Fqx "ExecStart=${rag_dir}/services/parsers/docling/.venv/bin/gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 127.0.0.1:18020 --workers 1 --timeout 300 --graceful-timeout 30 --error-logfile -" "${docling_unit}" \
  || fail "parser-docling が自分の venv で 127.0.0.1:18020 に bind していない"
grep -Fqx "WorkingDirectory=${rag_dir}/services/parsers/docling" "${docling_unit}" || fail "parser-docling の作業ディレクトリが違う"
grep -Fqx "EnvironmentFile=-${rag_dir}/backend/service-runtime.env" "${docling_unit}" \
  || fail "parser-docling がサービス実行用の env（HF / OCI Enterprise AI）を読まない"
grep -Fqx "Environment=HOME=${TEST_TMP_DIR}/systemd/service-home" "${docling_unit}" \
  || fail "parser-docling の HOME（モデルのキャッシュ）が SERVICE_HOME ではない"
grep -Fqx "User=${service_user}" "${docling_unit}" || fail "parser-docling が SERVICE_USER で動いていない"
if grep -Fq "platform/.env" "${docling_unit}"; then
  fail "OCI 以外の parser に共通 .env（secret）を渡している"
fi
if grep -Eiq 'docker|compose' "${backend_unit}" "${worker_unit}" "${docling_unit}"; then
  fail "systemd の unit が docker を使っている"
fi
# 初回の配備: 既定のサービスを起動し、backend / worker を enable + restart する。
grep -qx 'enable production-ready-rag-backend.service production-ready-rag-ingestion-worker.service' "${first_log}" \
  || fail "backend / ingestion-worker を enable していない"
grep -qx 'restart production-ready-rag-backend.service production-ready-rag-ingestion-worker.service' "${first_log}" \
  || fail "backend / ingestion-worker を restart していない"
for unit in preprocess-office-to-pdf parser-docling parser-unstructured parser-oci-genai-vision; do
  grep -qx "enable --now production-ready-rag-${unit}.service" "${first_log}" \
    || fail "初回の配備で ${unit} を起動していない"
done
grep -Fqx "EnvironmentFile=-${TEST_TMP_DIR}/systemd/app/no.1-production-ready-suite/platform/.env" "${vision_unit}.first" 2>/dev/null \
  || true
# 再配備: 最後に「起動」した unit は restart、最後に「停止」した unit は停止のまま。
grep -qx 'restart production-ready-rag-parser-docling.service' "${second_log}" \
  || fail "再配備で起動中の parser-docling を restart していない"
if grep -Eq '^(enable --now|restart) production-ready-rag-parser-unstructured.service$' "${second_log}"; then
  fail "利用者が停止した parser-unstructured を再配備で起動した"
fi
grep -qx 'stop production-ready-rag-parser-unstructured.service' "${second_log}" \
  || fail "利用者が停止した parser-unstructured を停止のままにしていない"
test ! -e "${TEST_TMP_DIR}/systemd/enabled/production-ready-rag-parser-unstructured.service" \
  || fail "再配備で parser-unstructured が enable に戻った"
# stack から外したサービスの unit は止めて消す。
grep -qx 'disable --now production-ready-rag-parser-oci-genai-vision.service' "${second_log}" \
  || fail "stack から外した parser-oci-genai-vision を止めていない"
test ! -e "${vision_unit}" || fail "stack から外した parser-oci-genai-vision の unit が残っている"
test -e "${backend_unit}" || fail "backend の unit を消した"
# sudoers: 画面から操作する unit の systemctl / journalctl だけ（引数まで固定）。backend / worker は含めない。
sudoers="${TEST_TMP_DIR}/systemd/sudoers.d/production-ready-rag-services"
test "$(stat -c '%a' "${sudoers}")" = "440" || fail "sudoers の permission が 0440 ではない"
grep -Fq '/usr/bin/systemctl enable --now production-ready-rag-parser-docling.service' "${sudoers}" \
  || fail "sudoers が parser-docling の起動（enable --now）を許可していない"
grep -Fq '/usr/bin/systemctl disable --now production-ready-rag-parser-docling.service' "${sudoers}" \
  || fail "sudoers が parser-docling の停止（disable --now）を許可していない"
grep -Fq '/usr/bin/systemctl restart production-ready-rag-parser-docling.service' "${sudoers}" \
  || fail "sudoers が parser-docling の再起動を許可していない"
grep -Fq '/usr/bin/journalctl -u production-ready-rag-parser-docling.service -n 1000 --no-pager -o short-iso' "${sudoers}" \
  || fail "sudoers が parser-docling のログ（backend と同じ argv）を許可していない"
grep -Fqx "${service_user} ALL=(root) NOPASSWD: RAG_SERVICE_CONTROL" "${sudoers}" \
  || fail "sudoers が SERVICE_USER に限定されていない"
if grep -Eq 'backend\.service|ingestion-worker\.service|oci-genai-vision|NOPASSWD: ALL|\*' "${sudoers}"; then
  fail "sudoers が画面の対象外の unit・任意のコマンドを許可している"
fi
grep -Fq 'production-ready-rag-parser-oci-genai-vision' "${TEST_TMP_DIR}/systemd/first-sudoers" \
  || fail "初回の sudoers に配備した parser-oci-genai-vision が入っていない"
if command -v visudo >/dev/null 2>&1; then
  visudo -cf "${sudoers}" >/dev/null || fail "生成した sudoers を visudo が受け付けない"
fi

# --- 以前の Docker Compose の配備からの移行 ---
run_migration_case
migration_dir="${TEST_TMP_DIR}/migration"
grep -qx 'disable --now production-ready-rag.service' "${migration_dir}/systemctl.log" \
  || fail "以前の compose の unit を止めていない"
test ! -e "${migration_dir}/units/production-ready-rag.service" || fail "以前の compose の unit が残っている"
grep -qx 'down --remove-orphans' "${migration_dir}/compose.log" || fail "以前のコンテナを消していない"
test -x "${migration_dir}/bin/rag-compose.legacy-docker" || fail "以前の rag-compose を退避していない"
test "$(cat "${migration_dir}/data/uploads/a.pdf")" = "original" \
  || fail "アップロード原本を docker volume から移していない、または 2 回目で上書きした"
test -f "${migration_dir}/service-home/.oci/config" || fail "OCI の設定を docker volume から移していない"

# --- Nginx: RAG は単独では配備しない（#1316）。site は platform/deploy/suite-nginx.sh の 1 つだけ ---
# （/rag/ の location・回答生成と MCP の待ち時間・SSE・ログインの上限・upload の上限は platform/deploy/tests/ が確かめる）
for unexpected in configure_nginx nginx_client_max_body_size sites-available proxy_pass APPLICATION_PORT; do
  if grep -Fq "${unexpected}" "${REPO_DIR}/init_script.sh"; then
    fail "単独の配備の Nginx の設定（${unexpected}）が残っている"
  fi
done
grep -Fq 'FRONTEND_BASE_PATH="${FRONTEND_BASE_PATH:-/rag/}"' "${REPO_DIR}/init_script.sh" \
  || fail "frontend を /rag/ を base に build しない"
suite_nginx="${REPO_DIR}/../platform/deploy/suite-nginx.sh"
grep -Fq 'location ~ ^/rag/api/(search|search/stream|search/answers/[^/]+/evaluation|evaluation/run|evaluation/compare|chat/conversations/[^/]+/messages/stream|chat/conversations/[^/]+/messages/[^/]+/stream|mcp)\$ {' "${suite_nginx}" \
  || fail "評価・回答生成・MCP の待ち時間を延ばす location がない"
grep -Fq 'proxy_read_timeout 660s;' "${suite_nginx}" \
  || fail "評価・回答生成の待ち時間が backend（600 秒）・画面（630 秒）より長くない"
# backend の回答生成の上限（RAG_ANSWER_TIMEOUT_SECONDS の設定の上限）が Nginx（660 秒）・画面（630 秒）より短いこと。
answer_timeout_max="$(sed -n 's/^OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS = \([0-9]*\)\.0$/\1/p' "${REPO_DIR}/backend/app/config.py")"
test "${answer_timeout_max}" = "600" || fail "LLM 1 回の timeout の上限（回答生成の上限）が 600 秒ではない"
awk '/rag_answer_timeout_seconds: float = Field\(/ {found = 1} found {print} found && /^    \)$/ {exit}' \
  "${REPO_DIR}/backend/app/config.py" | grep -Fq 'le=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,' \
  || fail "回答生成の上限（RAG_ANSWER_TIMEOUT_SECONDS）が LLM 1 回の timeout の上限で抑えられていない"
# 上限の値は suite の Nginx の 1 か所だけにする（固定値を持つ別の Nginx 設定を置かない。#306）。
test ! -e "${REPO_DIR}/frontend/nginx.conf.template" \
  || fail "使われない frontend/nginx.conf.template が固定の upload の上限を持ったまま残っている"

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
# Docker を入れない・使わない（以前の compose の配備を止める rag-compose の呼び出しだけは残す）。
if grep -Eq '^\s*(docker|docker-compose)\b|docker-ce|docker-compose-plugin|download\.docker\.com|docker run|docker compose' "${init_script}"; then
  fail "init_script.sh が Docker を入れる、または使っている"
fi
grep -Fq 'uv sync --locked --no-dev --python ${RAG_PYTHON_VERSION}' "${init_script}" \
  || fail "backend の venv を Python 3.12 の lock どおりに作っていない"
# Nginx の既定の上限の元（suite-init.sh の既定値）は backend の max_upload_bytes の既定値と同じ（#1316）。
grep -Fq 'RAG_DEFAULT_MAX_UPLOAD_BYTES=209715200' "${REPO_DIR}/../platform/deploy/suite-init.sh" \
  || fail "RAG_MAX_UPLOAD_BYTES の既定値が変わった"
grep -Fq 'max_upload_bytes: int = Field(default=200 * 1024 * 1024' "${REPO_DIR}/backend/app/config.py" \
  || fail "backend の RAG_MAX_UPLOAD_BYTES の既定値が suite-init.sh と合っていない"

# --- 開発環境（scripts/rag-services.sh）も本番と同じ unit と sudoers を作る ---
dev_dir="${TEST_TMP_DIR}/dev-render"
bash "${REPO_DIR}/scripts/rag-services.sh" render "${dev_dir}" > "${TEST_TMP_DIR}/dev-render.log" 2>&1 \
  || fail "scripts/rag-services.sh render が失敗した"
test -f "${dev_dir}/production-ready-rag-parser-docling.service" || fail "開発環境の parser-docling の unit を作っていない"
test ! -e "${dev_dir}/production-ready-rag-parser-asr.service" || fail "開発環境の既定（--cpu）に GPU の parser を含めた"
test ! -e "${dev_dir}/production-ready-rag-parser-unstructured.service" \
  || fail "開発環境の既定（--cpu）に Unstructured を含めた（Docling が既定。--unstructured で足す）"
grep -Fqx "ExecStart=${REPO_DIR}/services/parsers/docling/.venv/bin/gunicorn app.main:app --worker-class uvicorn.workers.UvicornWorker --bind 127.0.0.1:18020 --workers 1 --timeout 300 --graceful-timeout 30 --error-logfile -" \
  "${dev_dir}/production-ready-rag-parser-docling.service" || fail "開発環境の unit が本番と同じ起動方法ではない"
grep -Fqx "User=$(id -un)" "${dev_dir}/production-ready-rag-parser-docling.service" \
  || fail "開発環境の unit が現在のユーザーで動かない"
grep -Fqx "$(id -un) ALL=(root) NOPASSWD: RAG_SERVICE_CONTROL" "${dev_dir}/production-ready-rag-services" \
  || fail "開発環境の sudoers が現在のユーザーに限定されていない"
if command -v visudo >/dev/null 2>&1; then
  visudo -cf "${dev_dir}/production-ready-rag-services" >/dev/null || fail "開発環境の sudoers を visudo が受け付けない"
fi
if bash "${REPO_DIR}/scripts/rag-services.sh" render "${TEST_TMP_DIR}/dev-unknown" 'parser-docling;id' >/dev/null 2>&1; then
  fail "scripts/rag-services.sh が未知のサービス名を受け付けた"
fi
test ! -e "${REPO_DIR}/scripts/build-services.sh" || fail "Docker のイメージを build するスクリプトが残っている"

# --- RAG_SERVICES_TORCH=cpu: CUDA の wheel を入れず、lock と同じ版の CPU 版 torch を入れる（#366） ---
# スクリプトと lock だけを一時の rag/ に写し、手元の venv やモデルに触れないようにする（uv は fake）。
torch_rag_dir="${TEST_TMP_DIR}/torch-cpu/rag"
mkdir -p "${torch_rag_dir}/scripts" "${torch_rag_dir}/services/parsers/docling" "${torch_rag_dir}/services/preprocess/csv_to_json" \
  "${TEST_TMP_DIR}/torch-cpu/bin"
cp "${REPO_DIR}/scripts/rag-services.sh" "${REPO_DIR}/scripts/rag-systemd.sh" "${torch_rag_dir}/scripts/"
cp "${REPO_DIR}/services/parsers/docling/uv.lock" "${torch_rag_dir}/services/parsers/docling/"
cp "${REPO_DIR}/services/preprocess/csv_to_json/uv.lock" "${torch_rag_dir}/services/preprocess/csv_to_json/"
cat > "${TEST_TMP_DIR}/torch-cpu/bin/uv" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_UV_LOG}"
EOF
chmod +x "${TEST_TMP_DIR}/torch-cpu/bin/uv"
torch_uv_log="${TEST_TMP_DIR}/torch-cpu/uv.log"
FAKE_UV_LOG="${torch_uv_log}" PATH="${TEST_TMP_DIR}/torch-cpu/bin:${PATH}" RAG_SERVICES_TORCH=cpu \
  bash "${torch_rag_dir}/scripts/rag-services.sh" sync parser-docling preprocess-csv-to-json \
  > "${TEST_TMP_DIR}/torch-cpu/sync.log" 2>&1 || fail "RAG_SERVICES_TORCH=cpu の sync が失敗した"
docling_sync="$(grep -m1 '^sync ' "${torch_uv_log}")"
for package in torch torchvision triton nvidia-cublas nvidia-cudnn-cu13 cuda-toolkit; do
  [[ " ${docling_sync} " == *" --no-install-package ${package} "* ]] \
    || fail "RAG_SERVICES_TORCH=cpu で ${package} を uv sync から外していない: ${docling_sync}"
done
[[ "${docling_sync}" == "sync --locked --no-dev --python 3.12 "* ]] || fail "CPU 版でも lock どおりに sync していない: ${docling_sync}"
torch_version="$(awk '$0 == "name = \"torch\"" { getline; gsub(/^version = "|"$/, ""); print; exit }' "${torch_rag_dir}/services/parsers/docling/uv.lock")"
grep -Fqx "pip install --python .venv/bin/python --no-deps --index-url https://download.pytorch.org/whl/cpu torch==${torch_version} torchvision==$(awk '$0 == "name = \"torchvision\"" { getline; gsub(/^version = "|"$/, ""); print; exit }' "${torch_rag_dir}/services/parsers/docling/uv.lock")" \
  "${torch_uv_log}" || fail "lock と同じ版の CPU 版 torch を入れていない"
grep -Fqx "sync --locked --no-dev --python 3.12" "${torch_uv_log}" \
  || fail "torch を持たないサービス（preprocess-csv-to-json）の sync を変えた"
test "$(grep -c '^pip install' "${torch_uv_log}")" -eq 1 || fail "torch を持たないサービスにも CPU 版 torch を入れた"
: > "${torch_uv_log}"
FAKE_UV_LOG="${torch_uv_log}" PATH="${TEST_TMP_DIR}/torch-cpu/bin:${PATH}" \
  bash "${torch_rag_dir}/scripts/rag-services.sh" sync parser-docling > /dev/null 2>&1 || fail "既定の sync が失敗した"
grep -Fqx "sync --locked --no-dev --python 3.12" "${torch_uv_log}" || fail "既定（RAG_SERVICES_TORCH 未設定）で lock どおりに sync していない"
! grep -q '^pip install' "${torch_uv_log}" || fail "既定で CPU 版 torch を入れた"
if FAKE_UV_LOG="${torch_uv_log}" PATH="${TEST_TMP_DIR}/torch-cpu/bin:${PATH}" RAG_SERVICES_TORCH=cuda \
  bash "${torch_rag_dir}/scripts/rag-services.sh" sync parser-docling > /dev/null 2>&1; then
  fail "RAG_SERVICES_TORCH に cpu 以外を受け付けた"
fi

echo "rag init_script deployment behavior verified."
