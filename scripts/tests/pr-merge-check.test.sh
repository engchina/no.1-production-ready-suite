#!/usr/bin/env bash
# scripts/pr-merge-check.sh の判定を、偽の gh と一時的な git リポジトリで確かめる（GitHub には接続しない）。
set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pr-merge-check.sh"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# --- origin（bare）と clone。main の commit の時刻を固定する。
git init -q --bare -b main "${work}/origin.git"
git clone -q "${work}/origin.git" "${work}/repo" 2>/dev/null
commit() { # commit <ISO 時刻> <path>
  mkdir -p "$(dirname "${work}/repo/$2")"
  echo "$1" >"${work}/repo/$2"
  git -C "${work}/repo" add "$2"
  GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" \
    git -C "${work}/repo" -c user.name=t -c user.email=t@example.invalid commit -q -m "$2"
}
commit 2026-10-01T00:00:00Z README.md
commit 2026-10-01T02:00:00Z docs/note.md
commit 2026-10-01T03:00:00Z rag/x.txt
git -C "${work}/repo" push -q origin HEAD:main
git -C "${work}/repo" fetch -q origin

# --- 偽の gh。応答は FAKE_* の環境変数で決め、呼ばれた操作を log に残す。
mkdir -p "${work}/bin"
cat >"${work}/bin/gh" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"${FAKE_LOG}"
case "$1 $2" in
  "pr view")
    if [[ "$*" == *statusCheckRollup* ]]; then
      printf '%s\n' "${FAKE_CI_OK}"
    elif [[ "$*" == *"-q .headRefOid"* ]]; then
      # 判定の後の今の head（FAKE_HEAD_NOW が無ければ最初と同じ）。
      echo "${FAKE_HEAD_NOW:-${FAKE_PR_INFO##* }}"
    else
      echo "${FAKE_PR_INFO}"
    fi
    ;;
  "run list")
    # FAKE_RUNS は「;」区切りの応答の列。呼ばれるたびに次へ進み、最後の応答を繰り返す。
    n="$(cat "${FAKE_LOG}.runs" 2>/dev/null || echo 0)"
    IFS=';' read -r -a runs <<<"${FAKE_RUNS}"
    i=$((n < ${#runs[@]} ? n : ${#runs[@]} - 1))
    echo $((n + 1)) >"${FAKE_LOG}.runs"
    [ "${#runs[@]}" -gt 0 ] && [ -n "${runs[$i]}" ] && echo "${runs[$i]}"
    exit 0
    ;;
  "pr diff") printf '%s\n' "${FAKE_FILES}" ;;
  "pr update-branch") echo "updated" ;;
  *)
    echo "想定外の gh の呼び出し: $*" >&2
    exit 99
    ;;
esac
EOF
chmod +x "${work}/bin/gh"

failures=0
# run_case <名前> <期待の出力> <期待の終了コード> [引数…]（FAKE_* は呼び出し前に設定する）
run_case() {
  local name="$1" want_out="$2" want_code="$3"
  shift 3
  : >"${work}/gh.log"
  rm -f "${work}/gh.log.runs"
  local out code=0
  out="$(PATH="${work}/bin:${PATH}" FAKE_LOG="${work}/gh.log" PR_MERGE_CHECK_REPO_DIR="${work}/repo" \
    PR_MERGE_CHECK_INTERVAL=0 PR_MERGE_CHECK_TIMEOUT=5 bash "${script}" "$@" 2>"${work}/stderr")" || code=$?
  if [ "${out}" = "${want_out}" ] && [ "${code}" = "${want_code}" ]; then
    echo "ok   ${name}"
  else
    echo "FAIL ${name}: 出力=${out} 終了=${code}（期待 ${want_out} / ${want_code}）"
    sed 's/^/     /' "${work}/stderr"
    failures=$((failures + 1))
  fi
  if grep -q "^pr merge" "${work}/gh.log"; then
    echo "FAIL ${name}: merge を呼んだ"
    failures=$((failures + 1))
  fi
}

export FAKE_PR_INFO="OPEN codex/1-x abc123"
export FAKE_CI_OK="SUCCESS"
export FAKE_RUNS="11 2026-10-01T01:00:00Z completed success"

FAKE_FILES="nl2sql/a.py" run_case "触らない製品だけが main で変わった" ready-to-merge 0 1
FAKE_FILES="AGENTS.md" run_case "製品を触らない PR（platform/ .github/ だけを見る）" ready-to-merge 0 1
FAKE_FILES="rag/a.py" run_case "触る製品が main で変わった" needs-update-branch 3 1
FAKE_FILES="platform/a.ts" run_case "platform を触る PR は全製品を見る" needs-update-branch 3 1
FAKE_FILES="rag/a.py" FAKE_RUNS="11 2026-10-01T04:00:00Z completed success" \
  run_case "CI の開始が main の変更より後" ready-to-merge 0 1

FAKE_FILES="rag/a.py" run_case "--update で update-branch する" needs-update-branch 3 --update 1
grep -q "^pr update-branch 1" "${work}/gh.log" || {
  echo "FAIL --update で gh pr update-branch を呼んでいない"
  failures=$((failures + 1))
}
FAKE_FILES="rag/a.py" run_case "--update が無ければ update-branch しない" needs-update-branch 3 1
if grep -q "^pr update-branch" "${work}/gh.log"; then
  echo "FAIL --update が無いのに update-branch を呼んだ"
  failures=$((failures + 1))
fi

FAKE_FILES="rag/a.py" FAKE_RUNS="11 2026-10-01T04:00:00Z completed failure" \
  run_case "CI の run が失敗" ci-failed 1 1
FAKE_FILES="rag/a.py" FAKE_CI_OK="FAILURE" FAKE_RUNS="11 2026-10-01T04:00:00Z completed success" \
  run_case "CI OK が成功ではない" ci-failed 1 1
FAKE_FILES="rag/a.py" FAKE_CI_OK="" FAKE_RUNS="11 2026-10-01T04:00:00Z completed success" \
  run_case "CI OK が無い" ci-failed 1 1
if grep -q "新しい commit" "${work}/stderr"; then
  echo "FAIL head が変わっていないのに新しい commit の案内を出した"
  failures=$((failures + 1))
fi
FAKE_FILES="rag/a.py" FAKE_CI_OK="" FAKE_HEAD_NOW="def456" \
  FAKE_RUNS="11 2026-10-01T04:00:00Z completed cancelled" \
  run_case "待つ間に push されて run が取り消された" ci-failed 1 1
grep -q "新しい commit（def456）" "${work}/stderr" || {
  echo "FAIL 新しい commit の案内が無い"
  failures=$((failures + 1))
}

FAKE_FILES="nl2sql/a.py" FAKE_RUNS="11 2026-10-01T01:00:00Z in_progress " \
  run_case "--no-wait で実行中" ci-pending 4 --no-wait 1
FAKE_FILES="nl2sql/a.py" FAKE_RUNS="" run_case "--no-wait で run がまだ無い" ci-pending 4 --no-wait 1
FAKE_FILES="nl2sql/a.py" \
  FAKE_RUNS=";11 2026-10-01T01:00:00Z queued ;11 2026-10-01T01:00:00Z completed success" \
  run_case "run が現れて終わるまで待つ" ready-to-merge 0 1

FAKE_PR_INFO="MERGED codex/1-x abc123" FAKE_FILES="" run_case "OPEN でない PR" not-open 2 1
FAKE_FILES="" run_case "PR 番号が無い" "" 2

if [ "${failures}" -gt 0 ]; then
  echo "pr-merge-check: ${failures} 件失敗"
  exit 1
fi
echo "pr-merge-check の判定: pass"
