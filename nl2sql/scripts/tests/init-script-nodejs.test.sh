#!/usr/bin/env bash
set -euo pipefail

TEST_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${TEST_SCRIPT_DIR}/../.." && pwd)"
TEST_TMP_DIR="$(mktemp -d)"
LAST_SCENARIO_DIR=""
trap 'rm -rf -- "${TEST_TMP_DIR}"' EXIT

link_system_command() {
  local command_name="$1"
  local target
  target="$(command -v "${command_name}")"
  ln -s "${target}" "${MOCK_BIN_DIR}/${command_name}"
}

write_mock_commands() {
  local command_name

  for command_name in awk bash cat chmod dirname grep install mktemp rm seq sleep; do
    link_system_command "${command_name}"
  done

  cat > "${MOCK_BIN_DIR}/fuser" <<'MOCK'
#!/usr/bin/env bash
exit 1
MOCK

  cat > "${MOCK_BIN_DIR}/curl" <<'MOCK'
#!/usr/bin/env bash
printf 'mock-nodesource-key\n'
MOCK

  cat > "${MOCK_BIN_DIR}/gpg" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
out=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      out="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
cat >/dev/null
printf 'mock-keyring\n' > "${out}"
MOCK

  cat > "${MOCK_BIN_DIR}/dpkg" <<'MOCK'
#!/usr/bin/env bash
if [ "${1:-}" = "--print-architecture" ]; then
  printf 'amd64\n'
  exit 0
fi
exit 1
MOCK

  cat > "${MOCK_BIN_DIR}/apt-cache" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'apt-cache %s\n' "$*" >> "${MOCK_CALL_LOG}"
if [ "${1:-}" = "policy" ] && [ "${2:-}" = "nodejs" ]; then
  printf 'nodejs:\n'
  printf '  Installed: (none)\n'
  printf '  Candidate: %s\n' "$(cat "${MOCK_STATE_DIR}/candidate")"
  exit 0
fi
exit 1
MOCK

  cat > "${MOCK_BIN_DIR}/apt-get" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'apt-get %s\n' "$*" >> "${MOCK_CALL_LOG}"
if [ "${1:-}" = "install" ] && [ "${2:-}" = "-y" ] && [ "${3:-}" = "nodejs" ]; then
  printf '%s\n' "${MOCK_INSTALLED_NODE_VERSION:-v24.13.0}" > "${MOCK_STATE_DIR}/node-version"
  if [ "${MOCK_APT_INSTALL_CREATES_NPM:-1}" = "1" ]; then
    cat > "${MOCK_BIN_DIR}/npm" <<'NPM'
#!/usr/bin/env bash
printf '%s\n' "${MOCK_NPM_VERSION:-11.6.2}"
NPM
    chmod +x "${MOCK_BIN_DIR}/npm"
  fi
fi
MOCK

  cat > "${MOCK_BIN_DIR}/node" <<'MOCK'
#!/usr/bin/env bash
cat "${MOCK_STATE_DIR}/node-version"
MOCK

  chmod +x \
    "${MOCK_BIN_DIR}/apt-cache" \
    "${MOCK_BIN_DIR}/apt-get" \
    "${MOCK_BIN_DIR}/curl" \
    "${MOCK_BIN_DIR}/dpkg" \
    "${MOCK_BIN_DIR}/fuser" \
    "${MOCK_BIN_DIR}/gpg" \
    "${MOCK_BIN_DIR}/node"
}

run_install_nodejs_case() {
  local scenario="$1"
  local initial_node_version="$2"
  local candidate_version="$3"
  local install_creates_npm="$4"

  LAST_SCENARIO_DIR="${TEST_TMP_DIR}/${scenario}"
  MOCK_BIN_DIR="${LAST_SCENARIO_DIR}/bin"
  mkdir -p "${MOCK_BIN_DIR}" "${LAST_SCENARIO_DIR}/state" "${LAST_SCENARIO_DIR}/keyrings" "${LAST_SCENARIO_DIR}/sources"
  MOCK_STATE_DIR="${LAST_SCENARIO_DIR}/state"
  MOCK_CALL_LOG="${LAST_SCENARIO_DIR}/calls.log"
  : > "${MOCK_CALL_LOG}"
  printf '%s\n' "${initial_node_version}" > "${MOCK_STATE_DIR}/node-version"
  printf '%s\n' "${candidate_version}" > "${MOCK_STATE_DIR}/candidate"
  write_mock_commands

  (
    export APPLICATION_PORT=80
    export APP_ROOT="${LAST_SCENARIO_DIR}/app-root"
    export NL2SQL_INIT_TEST_MODE=true
    export NODESOURCE_KEYRING_PATH="${LAST_SCENARIO_DIR}/keyrings/nodesource.gpg"
    export NODESOURCE_SOURCE_PATH="${LAST_SCENARIO_DIR}/sources/nodesource.sources"
    export MOCK_APT_INSTALL_CREATES_NPM="${install_creates_npm}"
    export MOCK_BIN_DIR
    export MOCK_CALL_LOG
    export MOCK_INSTALLED_NODE_VERSION="v24.13.0"
    export MOCK_NPM_VERSION="11.6.2"
    export MOCK_STATE_DIR

    # init_script prepends system paths for production; reset PATH afterward so
    # tests never see this host's real node/npm/apt.
    # shellcheck source=/dev/null
    source "${REPO_DIR}/init_script.sh"
    PATH="${MOCK_BIN_DIR}"
    install_nodejs
  )
}

run_install_nodejs_case "recovers-node18-without-npm" "v18.19.1" "24.13.0-1nodesource1" "1"
grep -q 'node_24.x' "${LAST_SCENARIO_DIR}/sources/nodesource.sources"
grep -q "Signed-By: ${LAST_SCENARIO_DIR}/keyrings/nodesource.gpg" "${LAST_SCENARIO_DIR}/sources/nodesource.sources"
grep -q 'apt-get update' "${LAST_SCENARIO_DIR}/calls.log"
grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"
test "$(cat "${LAST_SCENARIO_DIR}/state/node-version")" = "v24.13.0"
test -x "${LAST_SCENARIO_DIR}/bin/npm"

if run_install_nodejs_case "rejects-non-node24-candidate" "v18.19.1" "18.19.1+dfsg-6ubuntu5" "1"; then
  echo "Node.js 24 以外の apt 候補を受け入れてしまいました。" >&2
  exit 1
fi
if grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"; then
  echo "Node.js 24 以外の候補で nodejs install が実行されました。" >&2
  exit 1
fi

if run_install_nodejs_case "rejects-missing-npm-after-install" "v18.19.1" "24.13.0-1nodesource1" "0"; then
  echo "npm 欠落状態を成功扱いしてしまいました。" >&2
  exit 1
fi
grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"
test ! -e "${LAST_SCENARIO_DIR}/bin/npm"

echo "init_script Node.js 24 install test: ok"
