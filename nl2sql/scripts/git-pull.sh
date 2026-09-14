#!/usr/bin/env bash
# 共有 platform と NL2SQL の main を取得し、成功後にだけ再デプロイへ進める。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_REPO_DIR="${APP_REPO_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
PLATFORM_REPO_DIR="${PLATFORM_REPO_DIR:-$(dirname "${APP_REPO_DIR}")/no.1-production-ready-platform}"

log() { printf '[nl2sql-pull] %s\n' "$*"; }
fail() { printf '[nl2sql-pull] ERROR: %s\n' "$*" >&2; return 1; }

usage() {
  cat <<'EOF'
Usage: ./scripts/git-pull.sh

通常のデプロイユーザーで実行し、platform → NL2SQL の順に main を更新します。
両 repository は main branch で、追跡ファイルに未保存変更がないことが前提です。
git pull --ff-only origin main を使い、取得した commit と HEAD の一致を確認します。
失敗時は非ゼロで終了します。途中まで成功した repository は元に戻しません。
未追跡ファイルは保持します。取得ファイルと衝突する場合は Git が更新を拒否します。

Environment overrides:
  APP_REPO_DIR       NL2SQL repository (default: このスクリプトの親)
  PLATFORM_REPO_DIR  共有 platform repository (default: NL2SQL と同じ親 directory)

再デプロイまで行う場合:
  ./scripts/git-pull.sh && sudo ./scripts/update-after-pull.sh
EOF
}

check_repository() {
  local label="$1" repo="$2" root branch changes
  root="$(git -C "${repo}" rev-parse --show-toplevel)" || {
    fail "${label}: Git repository を確認できません: ${repo}"; return 1;
  }
  [ "${root}" = "$(cd "${repo}" && pwd -P)" ] || {
    fail "${label}: repository の root を指定してください: ${repo}"; return 1;
  }
  branch="$(git -C "${repo}" symbolic-ref --quiet --short HEAD)" || {
    fail "${label}: detached HEAD です。main branch を確認してください。"; return 1;
  }
  [ "${branch}" = "main" ] || {
    fail "${label}: 現在の branch は ${branch} です。main が必要です。"; return 1;
  }
  changes="$(git -C "${repo}" status --porcelain --untracked-files=no)" || {
    fail "${label}: 作業ツリーの状態を確認できません。"; return 1;
  }
  if [ -n "${changes}" ]; then
    fail "${label}: 追跡ファイルに未保存変更があります。保存・整理してから再実行してください。"
    return 1
  fi
  git -C "${repo}" remote get-url origin >/dev/null || {
    fail "${label}: origin remote がありません。"; return 1;
  }
}

pull_repository() {
  local label="$1" repo="$2" head fetched
  log "${label}: origin/main を取得します (${repo})。"
  if ! git -C "${repo}" -c merge.autostash=false -c rebase.autoStash=false \
    pull --ff-only --no-rebase origin main; then
    fail "${label}: pull に失敗しました。上記の Git エラーを確認してください。再デプロイは行わないでください。"
    return 1
  fi
  head="$(git -C "${repo}" rev-parse HEAD)"
  fetched="$(git -C "${repo}" rev-parse FETCH_HEAD)"
  if [ "${head}" != "${fetched}" ]; then
    fail "${label}: HEAD が取得した origin/main と一致しません。ローカル commit を確認してください。"
    return 1
  fi
  log "${label}: $(git -C "${repo}" log -1 --format='%h %cs %s')"
}

main() {
  if [ "$#" -eq 1 ] && { [ "$1" = "--help" ] || [ "$1" = "-h" ]; }; then
    usage
    return 0
  fi
  if [ "$#" -ne 0 ]; then usage >&2; return 2; fi
  command -v git >/dev/null || { fail "git が見つかりません。"; return 1; }
  # 両方のローカル状態を先に確認し、既知の問題があればどちらも更新しない。
  check_repository platform "${PLATFORM_REPO_DIR}" || return 1
  check_repository NL2SQL "${APP_REPO_DIR}" || return 1
  pull_repository platform "${PLATFORM_REPO_DIR}" || return 1
  pull_repository NL2SQL "${APP_REPO_DIR}" || return 1
  log "両 repository の更新が完了しました。再デプロイへ進めます。"
}

# pull によって実行中のファイルが更新されても、読込済みの関数だけで最後まで実行する。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
  exit $?
fi
