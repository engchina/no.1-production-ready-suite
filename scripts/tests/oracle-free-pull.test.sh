#!/usr/bin/env bash
# .github/scripts/oracle-free-pull.sh のテスト（#4708）。偽の docker で、固まる・失敗する取得を試す。Suite / Scripts が実行する。
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
pull="${root}/.github/scripts/oracle-free-pull.sh"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
failures=0

# 偽の docker: 呼ばれた回数を数え、BEHAVIOR の n 番目の語（hang / fail / ok）の動きをする。
cat > "${work}/docker" <<'FAKE'
#!/usr/bin/env bash
count=$(($(cat "${FAKE_COUNT}" 2>/dev/null || echo 0) + 1))
echo "${count}" > "${FAKE_COUNT}"
read -r -a steps <<< "${BEHAVIOR}"
case "${steps[$((count - 1))]:-ok}" in
  hang) sleep 60 ;;
  fail) exit 1 ;;
esac
FAKE
chmod +x "${work}/docker"

expect() {
  local name="$1" behavior="$2" expected="$3" calls="$4"
  local actual=0 count_file="${work}/count-${name}"
  ORACLE_FREE_IMAGE=synthetic/image:1 DOCKER="${work}/docker" BEHAVIOR="${behavior}" FAKE_COUNT="${count_file}" \
    ORACLE_PULL_TIMEOUT_SECONDS=1 ORACLE_PULL_RETRY_DELAY_SECONDS=0 \
    timeout 30 bash "${pull}" > "${work}/out.txt" 2>&1 || actual=$?
  local made
  made="$(cat "${count_file}" 2>/dev/null || echo 0)"
  if [ "${actual}" -ne "${expected}" ] || [ "${made}" -ne "${calls}" ]; then
    echo "not ok - ${name}（終了 ${actual}、期待 ${expected}。呼び出し ${made}、期待 ${calls}）"
    cat "${work}/out.txt"
    failures=$((failures + 1))
  else
    echo "ok - ${name}"
  fi
}

expect first-success "ok" 0 1
expect retry-after-failure "fail ok" 0 2
expect retry-after-hang "hang ok" 0 2
expect all-hang "hang hang hang" 1 3
expect all-fail "fail fail fail" 1 3

exit "$((failures > 0 ? 1 : 0))"
