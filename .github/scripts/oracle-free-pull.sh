#!/usr/bin/env bash
# Oracle AI Database Free の image を、時間切れと再試行を付けて取得する（#4708）。
# docker run が取得を兼ねると、取得が固まったときに job の時間切れまで待たされる。
# registry が遅いときも普通は完了するので、1 回の時間は長めにする（取得済みの layer は次の試行で再利用される）。
#
# 使い方: ORACLE_FREE_IMAGE=<image> bash .github/scripts/oracle-free-pull.sh
#   ORACLE_PULL_ATTEMPTS（既定 3）・ORACLE_PULL_TIMEOUT_SECONDS（既定 480）・
#   ORACLE_PULL_RETRY_DELAY_SECONDS（既定 10）で変えられる（DOCKER= で docker を置き換えられる）。
set -euo pipefail

: "${ORACLE_FREE_IMAGE:?ORACLE_FREE_IMAGE が必要です}"
docker_cmd="${DOCKER:-docker}"
attempts="${ORACLE_PULL_ATTEMPTS:-3}"
timeout_seconds="${ORACLE_PULL_TIMEOUT_SECONDS:-480}"
retry_delay="${ORACLE_PULL_RETRY_DELAY_SECONDS:-10}"

for ((attempt = 1; attempt <= attempts; attempt++)); do
  if timeout --kill-after=10 "${timeout_seconds}" "${docker_cmd}" pull "${ORACLE_FREE_IMAGE}"; then
    exit 0
  fi
  echo "::warning::Oracle の image の取得に失敗しました（${attempt}/${attempts} 回目）。"
  if [ "${attempt}" -lt "${attempts}" ]; then
    sleep "${retry_delay}"
  fi
done

echo "::error::Oracle の image を取得できませんでした（${attempts} 回）。"
exit 1
