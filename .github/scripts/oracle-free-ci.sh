#!/usr/bin/env bash
# Integration Nightly（integration-nightly.yml）の Oracle AI Database Free の container の準備（#1595）。
#   1. container の log に "DATABASE IS READY TO USE" が出るまで待つ（container が止まったら失敗する）。
#   2. PDB（FREEPDB1）にテストの利用者 APP を作る（NL2SQL の tests/conftest.py が PLATFORM_ORACLE_USER=APP に
#      固定するので、3 製品とも APP を使う）。
#   3. 接続の設定（PLATFORM_ORACLE_*。Wallet なし）を platform/.env と、あれば GITHUB_ENV に書く。
#      NL2SQL の tests/conftest.py は platform/.env を読ませないので、環境変数にも書く。
#
# 使い方: ORACLE_CI_PASSWORD=<password> bash .github/scripts/oracle-free-ci.sh
#   ORACLE_CONTAINER（既定 oracle）・ORACLE_PORT（既定 1521）・ORACLE_ENV_FILE（既定 platform/.env）・
#   ORACLE_READY_TIMEOUT_SECONDS（既定 900）で変えられる（手元では podman でもよい: DOCKER=podman）。
set -euo pipefail

: "${ORACLE_CI_PASSWORD:?ORACLE_CI_PASSWORD が必要です}"
container="${ORACLE_CONTAINER:-oracle}"
port="${ORACLE_PORT:-1521}"
env_file="${ORACLE_ENV_FILE:-platform/.env}"
timeout_seconds="${ORACLE_READY_TIMEOUT_SECONDS:-900}"
docker_cmd="${DOCKER:-docker}"

deadline=$((SECONDS + timeout_seconds))
until "${docker_cmd}" logs "${container}" 2>&1 | grep -q "DATABASE IS READY TO USE"; do
  if [ "$("${docker_cmd}" inspect -f '{{.State.Running}}' "${container}" 2>/dev/null)" != "true" ]; then
    echo "::error::Oracle の container（${container}）が止まりました。"
    "${docker_cmd}" logs "${container}" 2>&1 | tail -n 50
    exit 1
  fi
  if [ "${SECONDS}" -ge "${deadline}" ]; then
    echo "::error::Oracle の起動が ${timeout_seconds} 秒で終わりませんでした。"
    "${docker_cmd}" logs "${container}" 2>&1 | tail -n 50
    exit 1
  fi
  sleep 5
done
echo "Oracle の起動を確認しました（${SECONDS} 秒）。"

# password は標準入力で渡す（process の引数・log に出さない）。DB_DEVELOPER_ROLE は表・view・sequence・
# procedure・SQL ドメインなどの作成の権限をまとめた role。
"${docker_cmd}" exec -i "${container}" sqlplus -s / as sysdba <<SQL
WHENEVER SQLERROR EXIT FAILURE
SET FEEDBACK OFF
ALTER SESSION SET CONTAINER = FREEPDB1;
CREATE USER APP IDENTIFIED BY "${ORACLE_CI_PASSWORD}" QUOTA UNLIMITED ON USERS;
GRANT DB_DEVELOPER_ROLE TO APP;
GRANT CREATE SESSION TO APP;
EXIT
SQL
echo "テストの利用者 APP を作りました。"

settings=(
  "PLATFORM_ORACLE_USER=APP"
  "PLATFORM_ORACLE_PASSWORD=${ORACLE_CI_PASSWORD}"
  "PLATFORM_ORACLE_DSN=localhost:${port}/FREEPDB1"
  "PLATFORM_ORACLE_DRIVER_MODE=thin"
  "PLATFORM_ORACLE_CONNECTION_SECURITY=walletless_tls"
  "PLATFORM_ORACLE_CLIENT_LIB_DIR="
  "PLATFORM_ORACLE_WALLET_DIR="
  "PLATFORM_ORACLE_WALLET_PASSWORD="
)
mkdir -p "$(dirname "${env_file}")"
printf '%s\n' "${settings[@]}" > "${env_file}"
chmod 600 "${env_file}"
if [ -n "${GITHUB_ENV:-}" ]; then
  printf '%s\n' "${settings[@]}" >> "${GITHUB_ENV}"
fi
echo "接続の設定を ${env_file} に書きました。"
