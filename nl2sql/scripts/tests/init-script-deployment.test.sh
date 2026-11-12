#!/usr/bin/env bash
set -euo pipefail

TEST_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${TEST_SCRIPT_DIR}/../.." && pwd)"
TEST_TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEST_TMP_DIR}"' EXIT

run_initialization_case() (
  local scenario="$1"
  local fail_pattern="$2"
  local case_dir="${TEST_TMP_DIR}/${scenario}"
  mkdir -p "${case_dir}/app/no.1-production-ready-suite/nl2sql/backend"
  export APP_ROOT="${case_dir}/app"
  export NL2SQL_INIT_TEST_MODE=true
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

WORKER_UNITS=(
  production-ready-nl2sql-schema-refresh-worker.service
  production-ready-nl2sql-synthetic-worker.service
  production-ready-nl2sql-quality-evaluation-worker.service
  production-ready-nl2sql-ontology-worker.service
)

run_systemd_case() (
  local scenario="$1" ready="$2" initial="${3:-new}" repeats="${4:-1}"
  local case_dir="${TEST_TMP_DIR}/${scenario}" unit count
  mkdir -p "${case_dir}/app/no.1-production-ready-suite/nl2sql/backend" "${case_dir}/units" "${case_dir}/state"
  export APP_ROOT="${case_dir}/app"
  export NL2SQL_INIT_TEST_MODE=true
  export APPLICATION_PORT=80
  export SYSTEMD_UNIT_DIR="${case_dir}/units"
  # shellcheck source=/dev/null
  source "${REPO_DIR}/init_script.sh"

  if [ "${initial}" != "new" ]; then
    for unit in "${WORKER_UNITS[@]}"; do
      touch "${case_dir}/units/${unit}" "${case_dir}/state/${unit}.active" "${case_dir}/state/${unit}.enabled"
      printf '1\n' > "${case_dir}/state/${unit}.generation"
    done
    if [ "${initial}" = "mixed" ]; then
      rm "${case_dir}/state/${WORKER_UNITS[1]}.active" \
        "${case_dir}/state/${WORKER_UNITS[2]}.active" \
        "${case_dir}/state/${WORKER_UNITS[2]}.enabled" \
        "${case_dir}/state/${WORKER_UNITS[3]}.enabled"
    fi
  fi

  systemctl() {
    printf '%s\n' "$*" >> "${case_dir}/systemctl.log"
    local action="$1" now=false target generation
    shift
    case "${action}" in
      is-enabled)
        [ "${1:-}" != "--quiet" ] || shift
        test -f "${case_dir}/state/${1}.enabled"
        ;;
      enable|disable|restart|stop)
        if [ "${1:-}" = "--now" ]; then now=true; shift; fi
        for target in "$@"; do
          case "${action}" in
            enable) touch "${case_dir}/state/${target}.enabled" ;;
            disable) rm -f "${case_dir}/state/${target}.enabled" ;;
          esac
          if [ "${action}" = "restart" ] || { [ "${action}" = "enable" ] && [ "${now}" = "true" ] && [ ! -f "${case_dir}/state/${target}.active" ]; }; then
            generation="$(cat "${case_dir}/state/${target}.generation" 2>/dev/null || echo 0)"
            printf '%s\n' "$((generation + 1))" > "${case_dir}/state/${target}.generation"
            touch "${case_dir}/state/${target}.active"
          elif [ "${action}" = "stop" ] || { [ "${action}" = "disable" ] && [ "${now}" = "true" ]; }; then
            rm -f "${case_dir}/state/${target}.active"
          fi
        done
        ;;
      daemon-reload) ;;
      *) echo "unexpected systemctl action: ${action}" >&2; return 1 ;;
    esac
  }

  DATABASE_INITIALIZATION_READY="${ready}"
  for ((count = 0; count < repeats; count++)); do configure_systemd; done
)

run_initialization_case success ""
test "$(cat "${TEST_TMP_DIR}/success/ready")" = "true"
grep -q 'nl2sql_system_schema --initialize' "${TEST_TMP_DIR}/success/commands.log"
grep -q 'app_security_migrate --apply --skip-bootstrap' "${TEST_TMP_DIR}/success/commands.log"
test "$(grep -n 'nl2sql_system_schema' "${TEST_TMP_DIR}/success/commands.log" | cut -d: -f1)" -lt \
  "$(grep -n 'app_security_migrate' "${TEST_TMP_DIR}/success/commands.log" | cut -d: -f1)"

run_initialization_case degraded-security app_security_migrate
test "$(cat "${TEST_TMP_DIR}/degraded-security/ready")" = "false"

run_initialization_case degraded-system nl2sql_system_schema
test "$(cat "${TEST_TMP_DIR}/degraded-system/ready")" = "false"

# 新規配備とその繰り返し: 起動状態を保ち、各配備で新しいプロセスへ置き換える。
run_systemd_case workers-new true new 2
for unit in "${WORKER_UNITS[@]}"; do
  test "$(cat "${TEST_TMP_DIR}/workers-new/state/${unit}.generation")" = 2
  test -f "${TEST_TMP_DIR}/workers-new/state/${unit}.enabled"
  test -f "${TEST_TMP_DIR}/workers-new/state/${unit}.active"
  test "$(grep -cxF "enable ${unit}" "${TEST_TMP_DIR}/workers-new/systemctl.log")" = 1
  test "$(grep -cxF "restart ${unit}" "${TEST_TMP_DIR}/workers-new/systemctl.log")" = 2
done

# 既に active な worker も enable --now で据え置かず、全て restart する。
run_systemd_case workers-existing true enabled
for unit in "${WORKER_UNITS[@]}"; do
  test "$(cat "${TEST_TMP_DIR}/workers-existing/state/${unit}.generation")" = 2
  grep -qxF "restart ${unit}" "${TEST_TMP_DIR}/workers-existing/systemctl.log"
done

# enabled だが inactive の worker は再開し、disabled の worker は active でも停止を維持する。
run_systemd_case workers-mixed true mixed 2
for index in 0 1; do
  unit="${WORKER_UNITS[index]}"
  test "$(cat "${TEST_TMP_DIR}/workers-mixed/state/${unit}.generation")" = 3
  test -f "${TEST_TMP_DIR}/workers-mixed/state/${unit}.enabled"
  test -f "${TEST_TMP_DIR}/workers-mixed/state/${unit}.active"
done
for index in 2 3; do
  unit="${WORKER_UNITS[index]}"
  test "$(cat "${TEST_TMP_DIR}/workers-mixed/state/${unit}.generation")" = 1
  test ! -f "${TEST_TMP_DIR}/workers-mixed/state/${unit}.enabled"
  test ! -f "${TEST_TMP_DIR}/workers-mixed/state/${unit}.active"
  test "$(grep -cxF "stop ${unit}" "${TEST_TMP_DIR}/workers-mixed/systemctl.log")" = 2
done

# DB 未準備では既に動いていた worker も停止し、繰り返しても起動しない。
run_systemd_case workers-degraded false enabled 2
for unit in "${WORKER_UNITS[@]}"; do
  test "$(cat "${TEST_TMP_DIR}/workers-degraded/state/${unit}.generation")" = 1
  test ! -f "${TEST_TMP_DIR}/workers-degraded/state/${unit}.enabled"
  test ! -f "${TEST_TMP_DIR}/workers-degraded/state/${unit}.active"
done
if grep -qE '^(enable|restart) .*worker' "${TEST_TMP_DIR}/workers-degraded/systemctl.log"; then
  echo "DB 未準備の worker が起動されました。" >&2
  exit 1
fi

run_systemd_case workers-degraded-new false new 2
for unit in "${WORKER_UNITS[@]}"; do
  test ! -f "${TEST_TMP_DIR}/workers-degraded-new/state/${unit}.generation"
  test ! -f "${TEST_TMP_DIR}/workers-degraded-new/state/${unit}.enabled"
  test ! -f "${TEST_TMP_DIR}/workers-degraded-new/state/${unit}.active"
done

grep -Fq 'WALLET_DIR="${APP_ROOT}/wallet"' "${REPO_DIR}/init_script.sh"
grep -Fq 'chown "root:${APP_GROUP}" "${APP_ROOT}"' "${REPO_DIR}/init_script.sh"
grep -Fq 'chmod 0775 "${APP_ROOT}"' "${REPO_DIR}/init_script.sh"
grep -Fq 'install -d -m 0700 -o "${APP_USER}" -g "${APP_GROUP}" "${WALLET_DIR}"' \
  "${REPO_DIR}/init_script.sh"
grep -Fq 'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \;' "${REPO_DIR}/init_script.sh"

echo "init_script deployment behavior verified."
