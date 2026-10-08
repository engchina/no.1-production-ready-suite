#!/usr/bin/env bash
# PR を main へ merge してよいかを判定する（merge はしない。merge は `gh pr merge <PR> --merge` で明示的に行う）。
#
# 使い方（リポジトリの中で）:
#   scripts/pr-merge-check.sh <PR>             # PR の最新 commit の CI を待ってから判定する
#   scripts/pr-merge-check.sh --update <PR>    # needs-update-branch なら `gh pr update-branch` も行う
#   scripts/pr-merge-check.sh --no-wait <PR>   # CI を待たない（実行中なら ci-pending）
#
# 判定（標準出力の 1 行目と終了コード）:
#   ready-to-merge        0  CI OK が成功し、CI の開始後に main の対象のディレクトリが変わっていない
#   ci-failed             1  CI の run が成功しなかった、または CI OK が成功ではない
#   needs-update-branch   3  CI の開始後に main の対象のディレクトリが変わった。main を取り込んで CI をやり直す
#   ci-pending            4  --no-wait で、CI がまだ終わっていない（または run がまだ無い）
#   (エラー)              2  使い方の誤り・PR が OPEN でない・待ちの時間切れ
#
# 対象のディレクトリ（AGENTS.md「main の取り込み」）: platform/ と .github/ と、PR が触る製品のディレクトリ
# （rag/ nl2sql/ agent/ terraform/）。PR が platform/ か .github/ を触るときは、全製品の job が動くので 4 つすべて。
# 「CI の開始」は PR の最新 commit の CI の run の createdAt で、その時点の main（first-parent）から origin/main までを比べる。
#
# 環境変数: PR_MERGE_CHECK_INTERVAL（待ちの間隔の秒。既定 30）、PR_MERGE_CHECK_TIMEOUT（待ちの上限の秒。既定 5400）、
#           PR_MERGE_CHECK_REPO_DIR（git を実行するディレクトリ。既定はこのリポジトリ）。
set -euo pipefail

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

wait_ci=1
update=0
pr=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --update) update=1 ;;
    --no-wait) wait_ci=0 ;;
    -h | --help) usage ;;
    -*)
      echo "不明なオプションです: $1" >&2
      usage
      ;;
    *)
      [ -z "${pr}" ] || usage
      pr="$1"
      ;;
  esac
  shift
done
[[ "${pr}" =~ ^[0-9]+$ ]] || usage

interval="${PR_MERGE_CHECK_INTERVAL:-30}"
timeout="${PR_MERGE_CHECK_TIMEOUT:-5400}"
repo_dir="${PR_MERGE_CHECK_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

log() { echo "[pr-merge-check] $*" >&2; }

info="$(gh pr view "${pr}" --json state,headRefName,headRefOid -q '"\(.state) \(.headRefName) \(.headRefOid)"')"
read -r state branch head_sha <<<"${info}"
if [ "${state}" != "OPEN" ]; then
  log "PR #${pr} は ${state} です"
  echo "not-open"
  exit 2
fi
log "PR #${pr} ${branch} ${head_sha}"

# PR の最新 commit の CI の run（push の直後はまだ無く、ブランチの最新の run が前の commit のことがある）。
find_run() {
  gh run list --workflow CI --branch "${branch}" --limit 20 \
    --json databaseId,headSha,createdAt,status,conclusion \
    -q ".[] | select(.headSha==\"${head_sha}\") | \"\(.databaseId) \(.createdAt) \(.status) \(.conclusion)\"" |
    head -1
}

deadline=$((SECONDS + timeout))
while :; do
  run="$(find_run)"
  run_status=""
  if [ -n "${run}" ]; then
    read -r run_id created_at run_status conclusion <<<"${run}"
    [ "${run_status}" = "completed" ] && break
  fi
  if [ "${wait_ci}" = 0 ]; then
    log "CI はまだ終わっていません（${run_status:-run がまだありません}）"
    echo "ci-pending"
    exit 4
  fi
  if [ "${SECONDS}" -ge "${deadline}" ]; then
    log "CI の待ちが ${timeout} 秒を超えました（${run_status:-run がまだありません}）"
    echo "timeout"
    exit 2
  fi
  sleep "${interval}"
done
log "CI の run ${run_id}（${created_at} 開始）: ${conclusion}"

ci_ok="$(gh pr view "${pr}" --json statusCheckRollup \
  -q '.statusCheckRollup[] | select(.name=="CI OK") | .conclusion' | sort -u | tr '\n' ' ')"
if [ "${conclusion}" != "success" ] || [ "${ci_ok}" != "SUCCESS " ]; then
  log "CI OK: ${ci_ok:-無し}"
  latest="$(gh pr view "${pr}" --json headRefOid -q .headRefOid)"
  if [ "${latest}" != "${head_sha}" ]; then
    log "待っている間に新しい commit（${latest:0:12}）が push されました。もう一度実行してください"
  fi
  echo "ci-failed"
  exit 1
fi

# PR が触るディレクトリから、main の変更を見るディレクトリを決める。
dirs=(platform/ .github/)
touched="$(gh pr diff "${pr}" --name-only)"
if grep -qE '^(platform|\.github)/' <<<"${touched}"; then
  dirs+=(rag/ nl2sql/ agent/ terraform/)
else
  for product in rag nl2sql agent terraform; do
    if grep -q "^${product}/" <<<"${touched}"; then
      dirs+=("${product}/")
    fi
  done
fi

git -C "${repo_dir}" fetch -q origin main
ci_base="$(git -C "${repo_dir}" rev-list -1 --first-parent --before="${created_at}" origin/main)"
changed="$(git -C "${repo_dir}" diff --name-only "${ci_base}..origin/main" -- "${dirs[@]}")"
if [ -z "${changed}" ]; then
  log "CI の開始後に main の ${dirs[*]} は変わっていません（${ci_base:0:12}..origin/main）"
  echo "ready-to-merge"
  exit 0
fi

log "CI の開始後に main の ${dirs[*]} が変わりました（$(wc -l <<<"${changed}") ファイル。${ci_base:0:12}..origin/main）:"
head -20 <<<"${changed}" | sed 's/^/  /' >&2
if [ "${update}" = 1 ]; then
  gh pr update-branch "${pr}" >&2
  log "main を取り込みました。新しい commit の CI を待って、もう一度実行してください"
fi
echo "needs-update-branch"
exit 3
