#!/usr/bin/env bash
set -euo pipefail

TEST_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${TEST_SCRIPT_DIR}/../.." && pwd)"
TEST_TMP_DIR="$(mktemp -d)"
LAST_SCENARIO_DIR=""
MOCK_BIN_DIR=""
MOCK_OFFICIAL_SHASUMS=""
MOCK_OFFICIAL_TARBALL=""
trap 'rm -rf -- "${TEST_TMP_DIR}"' EXIT

link_system_command() {
  local command_name="$1"
  local target
  target="$(command -v "${command_name}")"
  ln -s "${target}" "${MOCK_BIN_DIR}/${command_name}"
}

write_mock_commands() {
  local command_name

  for command_name in awk bash cat chmod cp dirname grep gzip install ln mktemp rm seq sha256sum sleep tar; do
    link_system_command "${command_name}"
  done

  cat > "${MOCK_BIN_DIR}/fuser" <<'MOCK'
#!/usr/bin/env bash
exit 1
MOCK

  cat > "${MOCK_BIN_DIR}/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
output=""
url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      output="$2"
      shift 2
      ;;
    -*)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
printf 'curl %s\n' "${url}" >> "${MOCK_CALL_LOG}"

write_response() {
  local source_path="$1"
  if [ -n "${output}" ]; then
    cp "${source_path}" "${output}"
  else
    cat "${source_path}"
  fi
}

case "${url}" in
  *nodesource-repo.gpg.key)
    if [ -n "${output}" ]; then
      printf 'mock-nodesource-key\n' > "${output}"
    else
      printf 'mock-nodesource-key\n'
    fi
    ;;
  */SHASUMS256.txt)
    write_response "${MOCK_OFFICIAL_SHASUMS}"
    ;;
  */node-v24.*.tar.gz)
    write_response "${MOCK_OFFICIAL_TARBALL}"
    ;;
  *)
    echo "unexpected curl URL: ${url}" >&2
    exit 1
    ;;
esac
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
  if [ "${MOCK_DPKG_FAIL:-0}" = "1" ]; then
    exit 1
  fi
  printf '%s\n' "${MOCK_DPKG_ARCH:-amd64}"
  exit 0
fi
exit 1
MOCK

  cat > "${MOCK_BIN_DIR}/uname" <<'MOCK'
#!/usr/bin/env bash
if [ "${1:-}" = "-m" ]; then
  printf '%s\n' "${MOCK_UNAME_MACHINE:-x86_64}"
  exit 0
fi
exit 1
MOCK

  cat > "${MOCK_BIN_DIR}/apt-cache" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'apt-cache %s\n' "$*" >> "${MOCK_CALL_LOG}"
if [ "${MOCK_APT_CACHE_FAIL:-0}" = "1" ]; then
  exit 1
fi
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
    "${MOCK_BIN_DIR}/node" \
    "${MOCK_BIN_DIR}/uname"
}

create_official_node_fixture() {
  local mode="$1"
  local node_dir="node-v24.19.0-linux-x64"
  local official_dir="${LAST_SCENARIO_DIR}/official-release"
  local tarball_path="${official_dir}/${node_dir}.tar.gz"

  mkdir -p "${official_dir}/${node_dir}/bin"
  cat > "${official_dir}/${node_dir}/bin/node" <<'NODE'
#!/usr/bin/env bash
printf 'v24.19.0\n'
NODE
  if [ "${mode}" != "missing_npm" ]; then
    cat > "${official_dir}/${node_dir}/bin/npm" <<'NPM'
#!/usr/bin/env bash
printf '11.6.2\n'
NPM
  fi
  cat > "${official_dir}/${node_dir}/bin/npx" <<'NPX'
#!/usr/bin/env bash
printf '11.6.2\n'
NPX
  cat > "${official_dir}/${node_dir}/bin/corepack" <<'COREPACK'
#!/usr/bin/env bash
printf '0.34.0\n'
COREPACK
  chmod +x "${official_dir}/${node_dir}/bin/"*
  tar -czf "${tarball_path}" -C "${official_dir}" "${node_dir}"

  MOCK_OFFICIAL_SHASUMS="${official_dir}/SHASUMS256.txt"
  MOCK_OFFICIAL_TARBALL="${tarball_path}"
  case "${mode}" in
    valid | missing_npm)
      (cd "${official_dir}" && sha256sum "${node_dir}.tar.gz" > SHASUMS256.txt)
      ;;
    checksum_mismatch)
      printf '0000000000000000000000000000000000000000000000000000000000000000  %s.tar.gz\n' "${node_dir}" > "${MOCK_OFFICIAL_SHASUMS}"
      ;;
    missing_match)
      (cd "${official_dir}" && sha256sum "${node_dir}.tar.gz" | sed 's/linux-x64/linux-s390x/' > SHASUMS256.txt)
      ;;
    *)
      echo "unknown official fixture mode: ${mode}" >&2
      exit 1
      ;;
  esac
}

run_nodejs_case() {
  local scenario="$1"
  local initial_node_version="$2"
  local candidate_version="$3"
  local install_creates_npm="$4"
  local entrypoint="$5"
  local apt_cache_fail="$6"
  local official_mode="$7"
  local dpkg_arch="$8"

  LAST_SCENARIO_DIR="${TEST_TMP_DIR}/${scenario}"
  MOCK_BIN_DIR="${LAST_SCENARIO_DIR}/bin"
  mkdir -p "${MOCK_BIN_DIR}" "${LAST_SCENARIO_DIR}/state" "${LAST_SCENARIO_DIR}/keyrings" "${LAST_SCENARIO_DIR}/sources"
  MOCK_STATE_DIR="${LAST_SCENARIO_DIR}/state"
  MOCK_CALL_LOG="${LAST_SCENARIO_DIR}/calls.log"
  MOCK_OFFICIAL_SHASUMS="${LAST_SCENARIO_DIR}/missing-SHASUMS256.txt"
  MOCK_OFFICIAL_TARBALL="${LAST_SCENARIO_DIR}/missing-node.tar.gz"
  : > "${MOCK_CALL_LOG}"
  printf '%s\n' "${initial_node_version}" > "${MOCK_STATE_DIR}/node-version"
  printf '%s\n' "${candidate_version}" > "${MOCK_STATE_DIR}/candidate"
  if [ "${official_mode}" != "none" ]; then
    create_official_node_fixture "${official_mode}"
  fi
  write_mock_commands

  (
    export APPLICATION_PORT=80
    export APP_ROOT="${LAST_SCENARIO_DIR}/app-root"
    export NL2SQL_INIT_TEST_MODE=true
    export NODESOURCE_KEYRING_PATH="${LAST_SCENARIO_DIR}/keyrings/nodesource.gpg"
    export NODESOURCE_SOURCE_PATH="${LAST_SCENARIO_DIR}/sources/nodesource.sources"
    export NODEJS_OFFICIAL_RELEASE_BASE_URL="https://nodejs.test/download/release/latest-v24.x"
    export NODEJS_OFFICIAL_INSTALL_DIR="${LAST_SCENARIO_DIR}/official-install"
    export NODEJS_OFFICIAL_BIN_DIR="${MOCK_BIN_DIR}"
    export MOCK_APT_CACHE_FAIL="${apt_cache_fail}"
    export MOCK_APT_INSTALL_CREATES_NPM="${install_creates_npm}"
    export MOCK_BIN_DIR
    export MOCK_CALL_LOG
    export MOCK_DPKG_ARCH="${dpkg_arch}"
    export MOCK_INSTALLED_NODE_VERSION="v24.13.0"
    export MOCK_NPM_VERSION="11.6.2"
    export MOCK_OFFICIAL_SHASUMS
    export MOCK_OFFICIAL_TARBALL
    export MOCK_STATE_DIR
    export MOCK_UNAME_MACHINE="x86_64"

    # init_script prepends system paths for production; reset PATH afterward so
    # tests never see this host's real node/npm/apt.
    # shellcheck source=/dev/null
    source "${REPO_DIR}/init_script.sh"
    PATH="${MOCK_BIN_DIR}"
    "${entrypoint}"
  )
}

run_nodejs_case "nodesource-recovers-node18-without-npm" "v18.19.1" "24.13.0-1nodesource1" "1" "install_nodejs" "0" "none" "amd64"
grep -q 'node_24.x' "${LAST_SCENARIO_DIR}/sources/nodesource.sources"
grep -q "Signed-By: ${LAST_SCENARIO_DIR}/keyrings/nodesource.gpg" "${LAST_SCENARIO_DIR}/sources/nodesource.sources"
grep -q 'apt-get update' "${LAST_SCENARIO_DIR}/calls.log"
grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"
test "$(cat "${LAST_SCENARIO_DIR}/state/node-version")" = "v24.13.0"
test -x "${LAST_SCENARIO_DIR}/bin/npm"

if run_nodejs_case "nodesource-only-rejects-missing-npm" "v18.19.1" "24.13.0-1nodesource1" "0" "install_nodejs_from_nodesource" "0" "none" "amd64"; then
  echo "NodeSource npm 欠落状態を成功扱いしてしまいました。" >&2
  exit 1
fi
grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"
test ! -e "${LAST_SCENARIO_DIR}/bin/npm"

run_nodejs_case "fallback-apt-cache-fails" "v18.19.1" "24.13.0-1nodesource1" "1" "install_nodejs" "1" "valid" "amd64"
grep -q 'apt-cache policy nodejs' "${LAST_SCENARIO_DIR}/calls.log"
if grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"; then
  echo "apt-cache 失敗後に apt-get install が実行されました。" >&2
  exit 1
fi
test "$("${LAST_SCENARIO_DIR}/bin/node" --version)" = "v24.19.0"
test "$("${LAST_SCENARIO_DIR}/bin/npm" --version)" = "11.6.2"

run_nodejs_case "fallback-non-node24-candidate" "v18.19.1" "18.19.1+dfsg-6ubuntu5" "1" "install_nodejs" "0" "valid" "amd64"
if grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"; then
  echo "Node.js 24 以外の候補で apt-get install が実行されました。" >&2
  exit 1
fi
test "$("${LAST_SCENARIO_DIR}/bin/node" --version)" = "v24.19.0"
test "$("${LAST_SCENARIO_DIR}/bin/npm" --version)" = "11.6.2"

run_nodejs_case "fallback-after-nodesource-validation-fails" "v18.19.1" "24.13.0-1nodesource1" "0" "install_nodejs" "0" "valid" "amd64"
grep -q 'apt-get install -y nodejs' "${LAST_SCENARIO_DIR}/calls.log"
test "$("${LAST_SCENARIO_DIR}/bin/node" --version)" = "v24.19.0"
test "$("${LAST_SCENARIO_DIR}/bin/npm" --version)" = "11.6.2"

if run_nodejs_case "rejects-unsupported-official-arch" "v18.19.1" "18.19.1+dfsg-6ubuntu5" "1" "install_nodejs" "0" "valid" "riscv64"; then
  echo "unsupported architecture を成功扱いしてしまいました。" >&2
  exit 1
fi

if run_nodejs_case "rejects-checksum-mismatch" "v18.19.1" "18.19.1+dfsg-6ubuntu5" "1" "install_nodejs" "0" "checksum_mismatch" "amd64"; then
  echo "checksum mismatch を成功扱いしてしまいました。" >&2
  exit 1
fi

if run_nodejs_case "rejects-missing-matching-tarball" "v18.19.1" "18.19.1+dfsg-6ubuntu5" "1" "install_nodejs" "0" "missing_match" "amd64"; then
  echo "matching tarball 欠落を成功扱いしてしまいました。" >&2
  exit 1
fi

if run_nodejs_case "rejects-official-missing-npm" "v18.19.1" "18.19.1+dfsg-6ubuntu5" "1" "install_nodejs" "0" "missing_npm" "amd64"; then
  echo "official tarball npm 欠落状態を成功扱いしてしまいました。" >&2
  exit 1
fi

run_data_dir_migration_case() {
  local scenario="$1"
  local case_dir="${TEST_TMP_DIR}/${scenario}"
  local source_script="${REPO_DIR}/init_script.sh"

  mkdir -p "${case_dir}"
  (
    export APP_USER
    export APPLICATION_PORT=80
    export NL2SQL_INIT_TEST_MODE=true
    APP_USER="$(id -un)"
    # shellcheck source=/dev/null
    source "${source_script}"
    DATA_DIR="${case_dir}/data/production-ready-nl2sql"
    LEGACY_DATA_DIR="${case_dir}/production-ready-nl2sql"
    mkdir -p "$(dirname "${DATA_DIR}")"

    case "${scenario}" in
      data-dir-legacy-missing)
        install -d -m 0755 "${DATA_DIR}"
        migrate_legacy_data_dir
        test -d "${DATA_DIR}"
        test -L "${LEGACY_DATA_DIR}"
        test "$(readlink "${LEGACY_DATA_DIR}")" = "${DATA_DIR}"
        ;;
      data-dir-legacy-copied)
        install -d -m 0755 "${DATA_DIR}" "${LEGACY_DATA_DIR}/nested"
        printf 'legacy-data\n' > "${LEGACY_DATA_DIR}/nested/source.txt"
        migrate_legacy_data_dir
        test "$(cat "${DATA_DIR}/nested/source.txt")" = "legacy-data"
        test -L "${LEGACY_DATA_DIR}"
        test "$(readlink "${LEGACY_DATA_DIR}")" = "${DATA_DIR}"
        local backup_dir
        backup_dir="$(
          find "$(dirname "${LEGACY_DATA_DIR}")" \
            -maxdepth 1 \
            -type d \
            -name "$(basename "${LEGACY_DATA_DIR}").legacy-*" \
            -print \
            -quit
        )"
        test -n "${backup_dir}"
        test "$(cat "${backup_dir}/nested/source.txt")" = "legacy-data"
        ;;
      data-dir-both-populated)
        install -d -m 0755 "${DATA_DIR}" "${LEGACY_DATA_DIR}"
        printf 'new-data\n' > "${DATA_DIR}/current.txt"
        printf 'legacy-data\n' > "${LEGACY_DATA_DIR}/source.txt"
        local output
        output="$(migrate_legacy_data_dir)"
        printf '%s\n' "${output}" | grep -q 'skipping legacy migration to avoid overwrite'
        test ! -L "${LEGACY_DATA_DIR}"
        test "$(cat "${DATA_DIR}/current.txt")" = "new-data"
        test "$(cat "${LEGACY_DATA_DIR}/source.txt")" = "legacy-data"
        ;;
      *)
        echo "unknown data directory migration scenario: ${scenario}" >&2
        exit 1
        ;;
    esac
  )
}

run_data_dir_migration_case "data-dir-legacy-missing"
run_data_dir_migration_case "data-dir-legacy-copied"
run_data_dir_migration_case "data-dir-both-populated"

echo "init_script Node.js 24 install and data directory migration test: ok"
