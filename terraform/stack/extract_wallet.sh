#!/bin/bash
# Terraform の external data source 用。Wallet の ZIP を小さくして返す（cloud-init の user_data の上限に収める）。
# 入力: stdin の JSON {"wallet_base64":"<Wallet の ZIP の base64>"}
# 出力: stdout に JSON {"wallet_content":"<小さくした ZIP の base64>"} だけ
# 同じ入力からは必ず同じ出力を返す（違うと user_data が変わり、apply のたびに Compute が作り直される。#5945）。
set -euo pipefail
export LC_ALL=C TZ=UTC
umask 022

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# base64 に引用符は含まれないので、JSON の値をそのまま切り出す（jq に依存しない）。
sed -n 's/.*"wallet_base64"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | base64 -d > "${WORK_DIR}/wallet_full.zip"

mkdir "${WORK_DIR}/wallet"
unzip -q -o "${WORK_DIR}/wallet_full.zip" -d "${WORK_DIR}/wallet" >/dev/null

# python-oracledb の thin mode が使わないファイル（README・Java 用）を除く。
rm -f "${WORK_DIR}/wallet"/{README,keystore.jks,truststore.jks,ojdbc.properties,ewallet.p12}

# -X: 拡張属性（アクセス時刻など、実行のたびに変わる値）を入れない。ファイルの順は glob の並び（LC_ALL=C）で決まる。
(cd "${WORK_DIR}/wallet" && zip -q -X ../wallet_small.zip ./*) >/dev/null

printf '{"wallet_content":"%s"}\n' "$(base64 -w 0 "${WORK_DIR}/wallet_small.zip")"
