#!/usr/bin/env bash
# 変更後の標準チェックを一括実行する。
# CI（CI=true）の既定: backend format/lint/type/test/security/audit + frontend lint/build/e2e（全件）。
# ローカルの既定は軽くする（#339）: Playwright e2e と pip-audit を省く。e2e の全件は CI の nightly
# （e2e-nightly.yml）、PR は CI の smoke（ci.yml の agent-e2e）、pip-audit の全件は dependency-audit-nightly.yml が実行する。
#   - 関係する spec だけ e2e を実行: SKIP_E2E=0 E2E_ARGS="e2e/auth-login.spec.ts" scripts/check-all.sh
#   - ローカルでも全部を実行: FULL=1 scripts/check-all.sh
# 必要に応じて SKIP_BACKEND=1 / SKIP_FRONTEND=1 / SKIP_E2E=0|1
# SKIP_FORMAT=1 / SKIP_SECURITY=1 / SKIP_AUDIT=0|1 / SKIP_VALIDATION_EVIDENCE=1
# SKIP_RELEASE_REHEARSAL=1 で一部を省略・追加できる。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
PLATFORM_DIR="${ROOT_DIR}/../platform"

# CI か FULL=1 なら全部、ローカルは e2e と pip-audit を既定で省く。
if [ -n "${CI:-}" ] || [ "${FULL:-0}" = "1" ]; then
  local_skip_default=0
else
  local_skip_default=1
fi

SKIP_BACKEND="${SKIP_BACKEND:-0}"
SKIP_FRONTEND="${SKIP_FRONTEND:-0}"
SKIP_E2E="${SKIP_E2E:-${local_skip_default}}"
E2E_ARGS="${E2E_ARGS:-}"
SKIP_FORMAT="${SKIP_FORMAT:-0}"
SKIP_SECURITY="${SKIP_SECURITY:-0}"
SKIP_AUDIT="${SKIP_AUDIT:-${local_skip_default}}"
SKIP_VALIDATION_EVIDENCE="${SKIP_VALIDATION_EVIDENCE:-0}"
SKIP_RELEASE_REHEARSAL="${SKIP_RELEASE_REHEARSAL:-0}"
UV_SYNC_ARGS="${UV_SYNC_ARGS:---locked --dev}"
cleanup_files=()

cleanup() {
  if [ "${#cleanup_files[@]}" -gt 0 ]; then
    rm -f "${cleanup_files[@]}"
  fi
}

trap cleanup EXIT

log() {
  echo ""
  echo "[check-all] $*"
}

run_backend_tool() {
  local tool="$1"
  shift

  if [ -x "${BACKEND_DIR}/.venv/bin/${tool}" ]; then
    (cd "${BACKEND_DIR}" && ".venv/bin/${tool}" "$@")
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    (cd "${BACKEND_DIR}" && uv run "${tool}" "$@")
    return
  fi

  echo "[check-all] backend/${tool} を実行できません。.venv または uv を用意してください。" >&2
  exit 1
}

ensure_platform_sibling() {
  if [ ! -d "${PLATFORM_DIR}" ]; then
    echo "[check-all] 共有 platform repo が見つかりません: ${PLATFORM_DIR}" >&2
    echo "[check-all] monorepo(no.1-production-ready-suite)の agent/ と platform/ がそろっていることを確認してください。" >&2
    exit 1
  fi
}

ensure_backend_deps() {
  if [ -x "${BACKEND_DIR}/.venv/bin/pytest" ]; then
    return
  fi
  if ! command -v uv >/dev/null 2>&1; then
    echo "[check-all] backend 依存を準備できません。uv をインストールしてください。" >&2
    exit 1
  fi
  log "backend dependencies"
  # shellcheck disable=SC2086
  (cd "${BACKEND_DIR}" && uv sync ${UV_SYNC_ARGS})
}

ensure_frontend_deps() {
  if [ -d "${FRONTEND_DIR}/node_modules" ]; then
    return
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "[check-all] npm が見つかりません。Node.js をインストールしてください。" >&2
    exit 1
  fi
  log "frontend dependencies"
  if [ -f "${FRONTEND_DIR}/package-lock.json" ]; then
    (cd "${FRONTEND_DIR}" && npm ci)
  else
    (cd "${FRONTEND_DIR}" && npm install)
  fi
}

ensure_platform_sibling

if [ "${SKIP_BACKEND}" != "1" ]; then
  ensure_backend_deps

  if [ "${SKIP_FORMAT}" != "1" ]; then
    log "backend black"
    run_backend_tool black --check .
  else
    log "backend black skipped"
  fi

  log "backend ruff"
  run_backend_tool ruff check .

  log "backend mypy"
  run_backend_tool mypy .

  log "backend pytest"
  run_backend_tool pytest -q

  if [ "${SKIP_VALIDATION_EVIDENCE}" != "1" ]; then
    log "backend validation evidence dry-run"
    evidence_file="$(mktemp "${TMPDIR:-/tmp}/agent-runtime-evidence.XXXXXX.json")"
    cleanup_files+=("${evidence_file}")
    run_backend_tool python scripts/agent_runtime_collect_validation_evidence.py \
      --mode dry-run \
      --environment ci \
      --validator check-all \
      --oracle-runs 2 \
      --oracle-audit-iterations 1 \
      --rotation-interval-seconds 0 \
      --output "${evidence_file}"
    run_backend_tool python scripts/agent_runtime_validate_evidence.py \
      "${evidence_file}" \
      --manifest ../docs/agent-runtime-production-validation.manifest.json \
      --allow-dry-run
  else
    log "backend validation evidence dry-run skipped"
  fi

  if [ "${SKIP_RELEASE_REHEARSAL}" != "1" ]; then
    log "backend release chain rehearsal"
    rehearsal_env="${RELEASE_REHEARSAL_ENVIRONMENT:-check-all-rehearsal}"
    cleanup_files+=(
      "${BACKEND_DIR}/validation-runner-readiness.${rehearsal_env}.json"
      "${BACKEND_DIR}/validation-preflight.${rehearsal_env}.json"
      "${BACKEND_DIR}/validation-evidence.${rehearsal_env}.json"
      "${BACKEND_DIR}/validation-evidence.${rehearsal_env}.md"
      "${BACKEND_DIR}/validation-review.${rehearsal_env}.json"
      "${BACKEND_DIR}/validation-bundle.${rehearsal_env}.json"
      "${BACKEND_DIR}/validation-bundle.${rehearsal_env}.md"
    )
    REHEARSAL_ENVIRONMENT="${rehearsal_env}" \
      REHEARSAL_VALIDATOR="check-all" \
      REHEARSAL_ORACLE_RUNS=2 \
      REHEARSAL_ORACLE_AUDIT_ITERATIONS=1 \
      REHEARSAL_ROTATION_INTERVAL_SECONDS=0 \
      "${ROOT_DIR}/scripts/rehearse-production-release-chain.sh"
  else
    log "backend release chain rehearsal skipped"
  fi

  if [ "${SKIP_SECURITY}" != "1" ]; then
    log "backend bandit"
    # B608 is skipped because Agent Runtime builds Oracle internal table SQL
    # only from _validate_oracle_identifier-checked schema object names; user
    # values remain bind parameters, and business NL2SQL SQL is never executed
    # by this project.
    run_backend_tool bandit -r app --skip B608

    if [ "${SKIP_AUDIT}" != "1" ]; then
      log "backend pip-audit"
      run_backend_tool pip-audit
    else
      log "backend pip-audit skipped（SKIP_AUDIT=0 で実行。全件は dependency-audit-nightly.yml）"
    fi
  else
    log "backend security checks skipped"
  fi
else
  log "backend checks skipped"
fi

if [ "${SKIP_FRONTEND}" != "1" ]; then
  ensure_frontend_deps

  log "frontend lint"
  (cd "${FRONTEND_DIR}" && npm run lint)

  log "frontend build"
  (cd "${FRONTEND_DIR}" && npm run build)

  if [ "${SKIP_E2E}" != "1" ]; then
    log "frontend Playwright e2e ${E2E_ARGS}"
    # E2E_ARGS は spec のパスや -g を空白区切りで渡すため、意図して分割する。
    # shellcheck disable=SC2086
    (cd "${FRONTEND_DIR}" && npm run test:e2e -- ${E2E_ARGS})
  else
    log "frontend Playwright e2e skipped（SKIP_E2E=0 で実行。CI では常に実行）"
  fi
else
  log "frontend checks skipped"
fi

log "all checks passed"
