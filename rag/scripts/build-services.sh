#!/usr/bin/env bash
# parser / preprocess マイクロサービスの Docker イメージをビルドする。
#
# dev(サービス管理画面)の「起動」は --no-build で行うため、事前にこのスクリプトで
# イメージをビルドしておく必要がある。dev 起動と同じ compose ファイル群でビルドし、
# イメージ名(例 no1-production-ready-rag-parser-docling)を一致させる。
#
# 使い方:
#   scripts/build-services.sh                  # 既定: 全サービスをビルド(CPU + GPU + 前処理)
#   scripts/build-services.sh --cpu            # CPU parser のみ(docling/marker/unstructured)
#   scripts/build-services.sh --gpu            # GPU parser のみ(unlimited-ocr/mineru/dots-ocr/glm-ocr。GPU は実行時に必要)
#   scripts/build-services.sh --preprocess     # 前処理サービスのみ
#   scripts/build-services.sh --cpu --preprocess  # 選択したグループのみ(組み合わせ可)
#   scripts/build-services.sh parser-docling   # サービス名を直接指定(GPU 名は自動で --profile gpu)
#   scripts/build-services.sh --all            # 全サービス(既定と同じ)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.dev.yml)
CPU_PARSERS=(parser-docling parser-marker parser-unstructured)
GPU_PARSERS=(parser-unlimited-ocr parser-mineru parser-dots-ocr parser-glm-ocr)
PREPROCESS=(
  preprocess-office-to-pdf
  preprocess-pdf-to-page-images
  preprocess-csv-to-json
  preprocess-excel-to-json
)

usage() {
  # shebang を除く先頭コメントブロックをそのまま使い方として表示する。
  awk 'NR==1{next} /^#/{sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"
}

if ! command -v docker >/dev/null 2>&1; then
  echo "[build-services] docker が見つかりません。Docker をインストールしてください。" >&2
  exit 1
fi

include_cpu=0
include_gpu=0
include_preprocess=0
any_group=0
targets=()
while [ $# -gt 0 ]; do
  case "$1" in
    --cpu) include_cpu=1; any_group=1 ;;
    --gpu) include_gpu=1; any_group=1 ;;
    --preprocess) include_preprocess=1; any_group=1 ;;
    --all) include_cpu=1; include_gpu=1; include_preprocess=1; any_group=1 ;;
    -h|--help) usage; exit 0 ;;
    -*)
      echo "[build-services] 不明なオプション: $1" >&2
      usage >&2
      exit 2
      ;;
    *) targets+=("$1") ;;
  esac
  shift
done

# サービス名の直接指定が無いときは、選択グループから対象を組み立てる。
# グループフラグも無ければ既定で全サービス(CPU + GPU + 前処理)をビルドする。
if [ "${#targets[@]}" -eq 0 ]; then
  if [ "${any_group}" -eq 0 ]; then
    include_cpu=1
    include_gpu=1
    include_preprocess=1
  fi
  if [ "${include_cpu}" -eq 1 ]; then
    targets+=("${CPU_PARSERS[@]}")
  fi
  if [ "${include_preprocess}" -eq 1 ]; then
    targets+=("${PREPROCESS[@]}")
  fi
  if [ "${include_gpu}" -eq 1 ]; then
    targets+=("${GPU_PARSERS[@]}")
  fi
fi

# GPU サービスが対象に含まれるなら compose の profile gate を越える。
profile_args=()
for t in "${targets[@]}"; do
  case "${t}" in
    parser-unlimited-ocr|parser-mineru|parser-dots-ocr|parser-glm-ocr) profile_args=(--profile gpu) ;;
  esac
done

echo "[build-services] ビルド対象: ${targets[*]}"
echo "[build-services] docker compose ${COMPOSE_FILES[*]} ${profile_args[*]:-} build ${targets[*]}"
docker compose "${COMPOSE_FILES[@]}" ${profile_args[@]+"${profile_args[@]}"} build "${targets[@]}"
echo "[build-services] 完了。サービス管理画面の「起動」で立ち上げられます。"
