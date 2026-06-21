#!/usr/bin/env bash
# Agent Runtime production validation / release gate の dry-run rehearsal。
# 実 Oracle / IdP / MCP / container runtime 接続前に evidence→review→bundle の形を検証する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"

REHEARSAL_ENVIRONMENT="${REHEARSAL_ENVIRONMENT:-rehearsal}"
REHEARSAL_VALIDATOR="${REHEARSAL_VALIDATOR:-$(whoami 2>/dev/null || echo local)}"
REHEARSAL_ORACLE_RUNS="${REHEARSAL_ORACLE_RUNS:-2}"
REHEARSAL_ORACLE_AUDIT_ITERATIONS="${REHEARSAL_ORACLE_AUDIT_ITERATIONS:-1}"
REHEARSAL_ROTATION_INTERVAL_SECONDS="${REHEARSAL_ROTATION_INTERVAL_SECONDS:-0}"
REHEARSAL_CONTAINER_RUNTIME="${REHEARSAL_CONTAINER_RUNTIME:-docker}"
REHEARSAL_CONTAINER_IMAGE="${REHEARSAL_CONTAINER_IMAGE:-busybox:latest}"
REHEARSAL_CONTAINER_USERNS="${REHEARSAL_CONTAINER_USERNS:-private}"
REHEARSAL_CONTAINER_USER="${REHEARSAL_CONTAINER_USER:-65532:65532}"
REHEARSAL_RETENTION_DAYS="${REHEARSAL_RETENTION_DAYS:-30}"
REHEARSAL_ARCHIVE_LOCATION="${REHEARSAL_ARCHIVE_LOCATION:-release-rehearsal/evidence-bundle}"
REHEARSAL_ARCHIVE_OWNER="${REHEARSAL_ARCHIVE_OWNER:-${REHEARSAL_VALIDATOR}}"

run_backend_python() {
  if [ -x "${BACKEND_DIR}/.venv/bin/python" ]; then
    (cd "${BACKEND_DIR}" && ".venv/bin/python" "$@")
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    (cd "${BACKEND_DIR}" && uv run python "$@")
    return
  fi
  echo "[rehearse-production-release-chain] backend python を実行できません。.venv または uv を用意してください。" >&2
  exit 1
}

evidence_json="${BACKEND_DIR}/validation-evidence.${REHEARSAL_ENVIRONMENT}.json"
evidence_md="${BACKEND_DIR}/validation-evidence.${REHEARSAL_ENVIRONMENT}.md"
runner_readiness_json="${BACKEND_DIR}/validation-runner-readiness.${REHEARSAL_ENVIRONMENT}.json"
preflight_json="${BACKEND_DIR}/validation-preflight.${REHEARSAL_ENVIRONMENT}.json"
review_json="${BACKEND_DIR}/validation-review.${REHEARSAL_ENVIRONMENT}.json"
bundle_json="${BACKEND_DIR}/validation-bundle.${REHEARSAL_ENVIRONMENT}.json"
bundle_md="${BACKEND_DIR}/validation-bundle.${REHEARSAL_ENVIRONMENT}.md"
manifest_json="${ROOT_DIR}/docs/agent-runtime-production-validation.manifest.json"

echo "[rehearse-production-release-chain] environment=${REHEARSAL_ENVIRONMENT}"

printf '{\n  "ok": true,\n  "mode": "dry-run",\n  "environment": "%s",\n  "note": "synthetic rehearsal preflight; live preflight is required for release"\n}\n' \
  "${REHEARSAL_ENVIRONMENT}" > "${preflight_json}"

printf '{\n  "ok": true,\n  "mode": "dry-run",\n  "environment": "%s",\n  "violations": [],\n  "note": "synthetic rehearsal runner readiness; live runner readiness is required for release"\n}\n' \
  "${REHEARSAL_ENVIRONMENT}" > "${runner_readiness_json}"

run_backend_python scripts/agent_runtime_collect_validation_evidence.py \
  --mode dry-run \
  --environment "${REHEARSAL_ENVIRONMENT}" \
  --validator "${REHEARSAL_VALIDATOR}" \
  --rotation-interval-seconds "${REHEARSAL_ROTATION_INTERVAL_SECONDS}" \
  --oracle-runs "${REHEARSAL_ORACLE_RUNS}" \
  --oracle-audit-iterations "${REHEARSAL_ORACLE_AUDIT_ITERATIONS}" \
  --container-runtime "${REHEARSAL_CONTAINER_RUNTIME}" \
  --container-image "${REHEARSAL_CONTAINER_IMAGE}" \
  --container-userns "${REHEARSAL_CONTAINER_USERNS}" \
  --container-user "${REHEARSAL_CONTAINER_USER}" \
  --output "${evidence_json}" \
  --summary-markdown "${evidence_md}"

run_backend_python scripts/agent_runtime_validate_evidence.py \
  "${evidence_json}" \
  --manifest "${manifest_json}" \
  --allow-dry-run

run_backend_python scripts/agent_runtime_scaffold_release_review.py \
  "${evidence_json}" \
  --manifest "${manifest_json}" \
  --environment "${REHEARSAL_ENVIRONMENT}" \
  --reviewer "${REHEARSAL_VALIDATOR}" \
  --decision approved \
  --mark-checklist-complete \
  --allow-dry-run \
  --note "Dry-run rehearsal only; not valid release approval." \
  --output "${review_json}"

run_backend_python scripts/agent_runtime_release_gate_check.py \
  "${evidence_json}" \
  "${review_json}" \
  --manifest "${manifest_json}" \
  --environment "${REHEARSAL_ENVIRONMENT}" \
  --allow-dry-run

run_backend_python scripts/agent_runtime_build_release_bundle.py \
  "${evidence_json}" \
  "${review_json}" \
  --runner-readiness "${runner_readiness_json}" \
  --preflight "${preflight_json}" \
  --summary-markdown "${evidence_md}" \
  --manifest "${manifest_json}" \
  --environment "${REHEARSAL_ENVIRONMENT}" \
  --output "${bundle_json}" \
  --bundle-summary-markdown "${bundle_md}" \
  --retention-days "${REHEARSAL_RETENTION_DAYS}" \
  --archive-location "${REHEARSAL_ARCHIVE_LOCATION}" \
  --archive-owner "${REHEARSAL_ARCHIVE_OWNER}" \
  --generated-by "release-chain-rehearsal:${REHEARSAL_VALIDATOR}" \
  --allow-dry-run

echo "[rehearse-production-release-chain] runner readiness JSON: ${runner_readiness_json}"
echo "[rehearse-production-release-chain] preflight JSON: ${preflight_json}"
echo "[rehearse-production-release-chain] evidence JSON: ${evidence_json}"
echo "[rehearse-production-release-chain] evidence Markdown: ${evidence_md}"
echo "[rehearse-production-release-chain] review JSON: ${review_json}"
echo "[rehearse-production-release-chain] bundle JSON: ${bundle_json}"
echo "[rehearse-production-release-chain] bundle Markdown: ${bundle_md}"
echo "[rehearse-production-release-chain] manifest JSON: ${manifest_json}"
