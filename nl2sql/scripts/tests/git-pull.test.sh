#!/usr/bin/env bash
set -euo pipefail

TEST_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PULL_SCRIPT="${TEST_SCRIPT_DIR}/../git-pull.sh"
TEST_TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "${TEST_TMP_DIR}"' EXIT
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_AUTHOR_NAME='Pull Test' GIT_AUTHOR_EMAIL='pull-test@example.invalid'
export GIT_COMMITTER_NAME="${GIT_AUTHOR_NAME}" GIT_COMMITTER_EMAIL="${GIT_AUTHOR_EMAIL}"

fail_test() { printf 'git-pull test failed: %s\n' "$*" >&2; exit 1; }

setup_case() {
  CASE_DIR="${TEST_TMP_DIR}/$1"
  APP_DIR="${CASE_DIR}/no.1-production-ready-nl2sql"
  PLATFORM_DIR="${CASE_DIR}/no.1-production-ready-platform"
  mkdir -p "${CASE_DIR}"
  local name dest
  for name in platform app; do
    git init --bare --quiet --initial-branch=main "${CASE_DIR}/${name}.git"
    git init --quiet --initial-branch=main "${CASE_DIR}/${name}-seed"
    printf 'initial\n' > "${CASE_DIR}/${name}-seed/version.txt"
    if [ "${name}" = app ]; then
      mkdir -p "${CASE_DIR}/${name}-seed/scripts"
      cp "${PULL_SCRIPT}" "${CASE_DIR}/${name}-seed/scripts/git-pull.sh"
    fi
    git -C "${CASE_DIR}/${name}-seed" add .
    git -C "${CASE_DIR}/${name}-seed" commit --quiet -m initial
    git -C "${CASE_DIR}/${name}-seed" remote add origin "${CASE_DIR}/${name}.git"
    git -C "${CASE_DIR}/${name}-seed" push --quiet -u origin main
    dest="${PLATFORM_DIR}"
    if [ "${name}" = app ]; then dest="${APP_DIR}"; fi
    git clone --quiet "${CASE_DIR}/${name}.git" "${dest}"
  done
  INITIAL_APP="$(git -C "${APP_DIR}" rev-parse HEAD)"
  INITIAL_PLATFORM="$(git -C "${PLATFORM_DIR}" rev-parse HEAD)"
}

advance_remote() {
  local name="$1"
  printf 'remote update\n' >> "${CASE_DIR}/${name}-seed/version.txt"
  if [ "${name}" = app ]; then
    # 実行中の pull スクリプト自体が更新される場合も正常終了する。
    printf '\n# updated script\n' >> "${CASE_DIR}/app-seed/scripts/git-pull.sh"
  fi
  git -C "${CASE_DIR}/${name}-seed" add .
  git -C "${CASE_DIR}/${name}-seed" commit --quiet -m remote-update
  git -C "${CASE_DIR}/${name}-seed" push --quiet origin main
}

run_pull() (
  cd "${TEST_TMP_DIR}"
  # 呼出元 directory に依存せず、スクリプト位置から両 repository を解決する。
  unset APP_REPO_DIR PLATFORM_REPO_DIR
  bash "${APP_DIR}/scripts/git-pull.sh"
)

expect_failure() {
  if run_pull >"${CASE_DIR}/output" 2>&1 && touch "${CASE_DIR}/deployed"; then
    fail_test 'failed pull allowed deployment'
  fi
  test ! -e "${CASE_DIR}/deployed"
  if grep -Fq '両 repository の更新が完了' "${CASE_DIR}/output"; then
    fail_test 'failed pull reported success'
  fi
}

assert_unchanged() {
  [ "$(git -C "${APP_DIR}" rev-parse HEAD)" = "${INITIAL_APP}" ]
  [ "$(git -C "${PLATFORM_DIR}" rev-parse HEAD)" = "${INITIAL_PLATFORM}" ]
}

setup_case success
advance_remote platform
advance_remote app
mkdir -p "${APP_DIR}/backend"
printf 'keep lock\n' > "${APP_DIR}/backend/..env.lock"
run_pull >"${CASE_DIR}/output" 2>&1
for name in platform app; do
  dest="${PLATFORM_DIR}"
  if [ "${name}" = app ]; then dest="${APP_DIR}"; fi
  [ "$(git -C "${dest}" rev-parse HEAD)" = "$(git -C "${CASE_DIR}/${name}-seed" rev-parse HEAD)" ]
done
grep -Fxq 'keep lock' "${APP_DIR}/backend/..env.lock"
grep -Fq '両 repository の更新が完了' "${CASE_DIR}/output"
platform_line="$(grep -n 'platform: origin/main' "${CASE_DIR}/output" | cut -d: -f1)"
app_line="$(grep -n 'NL2SQL: origin/main' "${CASE_DIR}/output" | cut -d: -f1)"
[ "${platform_line}" -lt "${app_line}" ]
run_pull >"${CASE_DIR}/noop-output" 2>&1
grep -Fq '両 repository の更新が完了' "${CASE_DIR}/noop-output"

for scenario in dirty staged branch detached missing missing-origin; do
  setup_case "${scenario}"
  advance_remote platform
  case "${scenario}" in
    dirty) printf 'local edit\n' >> "${APP_DIR}/version.txt" ;;
    staged)
      printf 'local edit\n' >> "${APP_DIR}/version.txt"
      git -C "${APP_DIR}" add version.txt
      ;;
    branch) git -C "${APP_DIR}" switch --quiet -c local-work ;;
    detached) git -C "${APP_DIR}" checkout --quiet --detach ;;
    missing) mv "${APP_DIR}/.git" "${CASE_DIR}/saved-git" ;;
    missing-origin) git -C "${APP_DIR}" remote remove origin ;;
  esac
  expect_failure
  [ "$(git -C "${PLATFORM_DIR}" rev-parse HEAD)" = "${INITIAL_PLATFORM}" ]
  case "${scenario}" in
    dirty|staged) grep -Fq 'local edit' "${APP_DIR}/version.txt" ;;
  esac
done

setup_case platform-fetch-failure
advance_remote app
git -C "${PLATFORM_DIR}" remote set-url origin "${CASE_DIR}/unavailable.git"
expect_failure
assert_unchanged
grep -Fq 'platform: pull に失敗' "${CASE_DIR}/output"

setup_case app-fetch-failure
advance_remote platform
git -C "${APP_DIR}" remote set-url origin "${CASE_DIR}/unavailable.git"
expect_failure
[ "$(git -C "${PLATFORM_DIR}" rev-parse HEAD)" = "$(git -C "${CASE_DIR}/platform-seed" rev-parse HEAD)" ]
[ "$(git -C "${APP_DIR}" rev-parse HEAD)" = "${INITIAL_APP}" ]
grep -Fq 'NL2SQL: pull に失敗' "${CASE_DIR}/output"

for scenario in diverged ahead; do
  setup_case "${scenario}"
  printf 'local commit\n' > "${PLATFORM_DIR}/local.txt"
  git -C "${PLATFORM_DIR}" add local.txt
  git -C "${PLATFORM_DIR}" commit --quiet -m local-commit
  local_head="$(git -C "${PLATFORM_DIR}" rev-parse HEAD)"
  if [ "${scenario}" = diverged ]; then advance_remote platform; fi
  expect_failure
  [ "$(git -C "${PLATFORM_DIR}" rev-parse HEAD)" = "${local_head}" ]
  [ "$(git -C "${APP_DIR}" rev-parse HEAD)" = "${INITIAL_APP}" ]
done

setup_case untracked-conflict
printf 'remote file\n' > "${CASE_DIR}/platform-seed/conflict.txt"
advance_remote platform
printf 'local file\n' > "${PLATFORM_DIR}/conflict.txt"
expect_failure
assert_unchanged
grep -Fxq 'local file' "${PLATFORM_DIR}/conflict.txt"

bash "${PULL_SCRIPT}" --help >"${TEST_TMP_DIR}/help"
grep -Fq 'Usage:' "${TEST_TMP_DIR}/help"
if bash "${PULL_SCRIPT}" --unknown >"${TEST_TMP_DIR}/invalid" 2>&1; then
  fail_test 'unknown argument succeeded'
fi
printf 'git-pull: success, no-op and 11 failure scenarios verified.\n'
