#!/usr/bin/env bash
# Agent Runtime の production validation evidence を収集・検証する。
# 既定は dry-run。live 実行は VALIDATION_MODE=live を明示する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"

VALIDATION_MODE="${VALIDATION_MODE:-dry-run}"
VALIDATION_ENVIRONMENT="${VALIDATION_ENVIRONMENT:-staging}"
VALIDATION_VALIDATOR="${VALIDATION_VALIDATOR:-$(whoami 2>/dev/null || echo local)}"
VALIDATION_ROTATION_INTERVAL_SECONDS="${VALIDATION_ROTATION_INTERVAL_SECONDS:-0}"
VALIDATION_ORACLE_RUNS="${VALIDATION_ORACLE_RUNS:-1000}"
VALIDATION_ORACLE_AUDIT_ITERATIONS="${VALIDATION_ORACLE_AUDIT_ITERATIONS:-20}"
VALIDATION_CONTAINER_RUNTIME="${VALIDATION_CONTAINER_RUNTIME:-docker}"
VALIDATION_CONTAINER_IMAGE="${VALIDATION_CONTAINER_IMAGE:-busybox:latest}"
VALIDATION_CONTAINER_USERNS="${VALIDATION_CONTAINER_USERNS:-private}"
VALIDATION_CONTAINER_USER="${VALIDATION_CONTAINER_USER:-65532:65532}"
VALIDATION_REVIEW_JSON="${VALIDATION_REVIEW_JSON:-}"
VALIDATION_BUNDLE_JSON="${VALIDATION_BUNDLE_JSON:-}"
VALIDATION_RETENTION_DAYS="${VALIDATION_RETENTION_DAYS:-365}"
VALIDATION_ARCHIVE_LOCATION="${VALIDATION_ARCHIVE_LOCATION:-release-record/evidence-bundle}"
VALIDATION_ARCHIVE_OWNER="${VALIDATION_ARCHIVE_OWNER:-${VALIDATION_VALIDATOR}}"
VALIDATION_ARCHIVE_ROOT="${VALIDATION_ARCHIVE_ROOT:-}"
VALIDATION_OCI_UPLOAD_BUCKET_NAME="${VALIDATION_OCI_UPLOAD_BUCKET_NAME:-}"
VALIDATION_OCI_UPLOAD_NAMESPACE="${VALIDATION_OCI_UPLOAD_NAMESPACE:-}"
VALIDATION_OCI_UPLOAD_OBJECT_PREFIX="${VALIDATION_OCI_UPLOAD_OBJECT_PREFIX:-agent-runtime/release-archives}"
VALIDATION_OCI_UPLOAD_EXECUTE="${VALIDATION_OCI_UPLOAD_EXECUTE:-0}"
VALIDATION_OCI_RETENTION_CONFIRMED="${VALIDATION_OCI_RETENTION_CONFIRMED:-0}"

if [ "${VALIDATION_MODE}" != "dry-run" ] && [ "${VALIDATION_MODE}" != "live" ]; then
  echo "[validate-production-evidence] VALIDATION_MODE must be dry-run or live" >&2
  exit 2
fi

run_backend_python() {
  if [ -x "${BACKEND_DIR}/.venv/bin/python" ]; then
    (cd "${BACKEND_DIR}" && ".venv/bin/python" "$@")
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    (cd "${BACKEND_DIR}" && uv run python "$@")
    return
  fi
  echo "[validate-production-evidence] backend python を実行できません。.venv または uv を用意してください。" >&2
  exit 1
}

evidence_json="${BACKEND_DIR}/validation-evidence.${VALIDATION_ENVIRONMENT}.json"
evidence_md="${BACKEND_DIR}/validation-evidence.${VALIDATION_ENVIRONMENT}.md"
runner_readiness_json="${BACKEND_DIR}/validation-runner-readiness.${VALIDATION_ENVIRONMENT}.json"
runner_readiness_md="${BACKEND_DIR}/validation-runner-readiness.${VALIDATION_ENVIRONMENT}.md"
preflight_json="${BACKEND_DIR}/validation-preflight.${VALIDATION_ENVIRONMENT}.json"
preflight_md="${BACKEND_DIR}/validation-preflight.${VALIDATION_ENVIRONMENT}.md"
manifest_json="${ROOT_DIR}/docs/agent-runtime-production-validation.manifest.json"
review_json=""
bundle_json="${BACKEND_DIR}/validation-bundle.${VALIDATION_ENVIRONMENT}.json"
bundle_md="${BACKEND_DIR}/validation-bundle.${VALIDATION_ENVIRONMENT}.md"
archive_record_json="${BACKEND_DIR}/validation-archive.${VALIDATION_ENVIRONMENT}.json"
archive_record_md="${BACKEND_DIR}/validation-archive.${VALIDATION_ENVIRONMENT}.md"
archive_dir_json="${BACKEND_DIR}/validation-archive-dir.${VALIDATION_ENVIRONMENT}.json"
archive_dir_md="${BACKEND_DIR}/validation-archive-dir.${VALIDATION_ENVIRONMENT}.md"
upload_manifest_json="${BACKEND_DIR}/validation-upload.${VALIDATION_ENVIRONMENT}.json"
upload_manifest_md="${BACKEND_DIR}/validation-upload.${VALIDATION_ENVIRONMENT}.md"
chain_json="${BACKEND_DIR}/validation-chain.${VALIDATION_ENVIRONMENT}.json"
chain_md="${BACKEND_DIR}/validation-chain.${VALIDATION_ENVIRONMENT}.md"

if [ -n "${VALIDATION_REVIEW_JSON}" ]; then
  if [[ "${VALIDATION_REVIEW_JSON}" = /* ]]; then
    review_json="${VALIDATION_REVIEW_JSON}"
  else
    review_json="${ROOT_DIR}/${VALIDATION_REVIEW_JSON}"
  fi
fi

if [ -n "${VALIDATION_BUNDLE_JSON}" ]; then
  if [[ "${VALIDATION_BUNDLE_JSON}" = /* ]]; then
    bundle_json="${VALIDATION_BUNDLE_JSON}"
  else
    bundle_json="${ROOT_DIR}/${VALIDATION_BUNDLE_JSON}"
  fi
fi
case "${bundle_json}" in
  *.json) bundle_md="${bundle_json%.json}.md" ;;
  *) bundle_md="${bundle_json}.md" ;;
esac

if [ -n "${review_json}" ] && [ "${VALIDATION_MODE}" != "live" ]; then
  echo "[validate-production-evidence] VALIDATION_REVIEW_JSON requires VALIDATION_MODE=live" >&2
  exit 2
fi

echo "[validate-production-evidence] mode=${VALIDATION_MODE} environment=${VALIDATION_ENVIRONMENT}"

if [ "${VALIDATION_MODE}" = "live" ]; then
  set -o pipefail
  runner_readiness_args=(
    scripts/agent_runtime_runner_readiness_check.py
    --environment "${VALIDATION_ENVIRONMENT}"
    --container-runtime "${VALIDATION_CONTAINER_RUNTIME}"
    --archive-root "${VALIDATION_ARCHIVE_ROOT}"
    --upload-bucket-name "${VALIDATION_OCI_UPLOAD_BUCKET_NAME}"
    --upload-namespace "${VALIDATION_OCI_UPLOAD_NAMESPACE}"
    --upload-object-prefix "${VALIDATION_OCI_UPLOAD_OBJECT_PREFIX}"
    --manifest "${manifest_json}"
    --output "${runner_readiness_json}"
    --summary-markdown "${runner_readiness_md}"
    --generated-by "validate-production-evidence:${VALIDATION_VALIDATOR}"
  )
  if [ "${VALIDATION_OCI_UPLOAD_EXECUTE}" = "1" ] || [ "${VALIDATION_OCI_UPLOAD_EXECUTE}" = "true" ]; then
    runner_readiness_args+=(--execute-upload)
  fi
  if [ "${VALIDATION_OCI_RETENTION_CONFIRMED}" = "1" ] || [ "${VALIDATION_OCI_RETENTION_CONFIRMED}" = "true" ]; then
    runner_readiness_args+=(--retention-confirmed)
  fi
  run_backend_python "${runner_readiness_args[@]}"
  run_backend_python scripts/agent_runtime_validation_preflight.py \
    --environment "${VALIDATION_ENVIRONMENT}" \
    --container-runtime "${VALIDATION_CONTAINER_RUNTIME}" \
    --summary-markdown "${preflight_md}" \
    | tee "${preflight_json}"
fi

run_backend_python scripts/agent_runtime_collect_validation_evidence.py \
  --mode "${VALIDATION_MODE}" \
  --environment "${VALIDATION_ENVIRONMENT}" \
  --validator "${VALIDATION_VALIDATOR}" \
  --rotation-interval-seconds "${VALIDATION_ROTATION_INTERVAL_SECONDS}" \
  --oracle-runs "${VALIDATION_ORACLE_RUNS}" \
  --oracle-audit-iterations "${VALIDATION_ORACLE_AUDIT_ITERATIONS}" \
  --container-runtime "${VALIDATION_CONTAINER_RUNTIME}" \
  --container-image "${VALIDATION_CONTAINER_IMAGE}" \
  --container-userns "${VALIDATION_CONTAINER_USERNS}" \
  --container-user "${VALIDATION_CONTAINER_USER}" \
  --output "${evidence_json}" \
  --summary-markdown "${evidence_md}"

validator_args=("${evidence_json}" "--manifest" "${manifest_json}")
if [ "${VALIDATION_MODE}" = "dry-run" ]; then
  validator_args+=("--allow-dry-run")
fi
run_backend_python scripts/agent_runtime_validate_evidence.py "${validator_args[@]}"

if [ -n "${review_json}" ]; then
  run_backend_python scripts/agent_runtime_release_gate_check.py \
    "${evidence_json}" \
    "${review_json}" \
    --manifest "${manifest_json}" \
    --environment "${VALIDATION_ENVIRONMENT}"
  run_backend_python scripts/agent_runtime_build_release_bundle.py \
    "${evidence_json}" \
    "${review_json}" \
    --runner-readiness "${runner_readiness_json}" \
    --preflight "${preflight_json}" \
    --summary-markdown "${evidence_md}" \
    --manifest "${manifest_json}" \
    --environment "${VALIDATION_ENVIRONMENT}" \
    --output "${bundle_json}" \
    --bundle-summary-markdown "${bundle_md}" \
    --retention-days "${VALIDATION_RETENTION_DAYS}" \
    --archive-location "${VALIDATION_ARCHIVE_LOCATION}" \
    --archive-owner "${VALIDATION_ARCHIVE_OWNER}" \
    --generated-by "validate-production-evidence:${VALIDATION_VALIDATOR}"
  if [ -n "${VALIDATION_ARCHIVE_ROOT}" ]; then
    run_backend_python scripts/agent_runtime_archive_release_bundle.py \
      "${bundle_json}" \
      --bundle-summary-markdown "${bundle_md}" \
      --archive-root "${VALIDATION_ARCHIVE_ROOT}" \
      --output "${archive_record_json}" \
      --archive-summary-markdown "${archive_record_md}" \
      --generated-by "validate-production-evidence:${VALIDATION_VALIDATOR}"
    run_backend_python scripts/agent_runtime_verify_release_archive_dir.py \
      "${archive_record_json}" \
      --environment "${VALIDATION_ENVIRONMENT}" \
      --output "${archive_dir_json}" \
      --summary-markdown "${archive_dir_md}" \
      --generated-by "validate-production-evidence:${VALIDATION_VALIDATOR}"
    if [ -n "${VALIDATION_OCI_UPLOAD_BUCKET_NAME}" ]; then
      upload_args=(
        scripts/agent_runtime_upload_release_archive.py
        "${archive_record_json}"
        --bucket-name "${VALIDATION_OCI_UPLOAD_BUCKET_NAME}"
        --namespace "${VALIDATION_OCI_UPLOAD_NAMESPACE}"
        --object-prefix "${VALIDATION_OCI_UPLOAD_OBJECT_PREFIX}"
        --output "${upload_manifest_json}"
        --upload-summary-markdown "${upload_manifest_md}"
        --generated-by "validate-production-evidence:${VALIDATION_VALIDATOR}"
      )
      if [ "${VALIDATION_OCI_UPLOAD_EXECUTE}" = "1" ] || [ "${VALIDATION_OCI_UPLOAD_EXECUTE}" = "true" ]; then
        upload_args+=(--execute-upload)
      fi
      if [ "${VALIDATION_OCI_RETENTION_CONFIRMED}" = "1" ] || [ "${VALIDATION_OCI_RETENTION_CONFIRMED}" = "true" ]; then
        upload_args+=(--retention-confirmed)
      fi
      run_backend_python "${upload_args[@]}"
    fi
  fi
  chain_args=(
    scripts/agent_runtime_verify_release_chain.py
    --environment "${VALIDATION_ENVIRONMENT}"
    --runner-readiness "${runner_readiness_json}"
    --preflight "${preflight_json}"
    --evidence "${evidence_json}"
    --evidence-summary "${evidence_md}"
    --review "${review_json}"
    --bundle "${bundle_json}"
    --bundle-summary "${bundle_md}"
    --manifest "${manifest_json}"
    --output "${chain_json}"
    --summary-markdown "${chain_md}"
    --generated-by "validate-production-evidence:${VALIDATION_VALIDATOR}"
  )
  if [ -n "${VALIDATION_ARCHIVE_ROOT}" ]; then
    chain_args+=(--archive-record "${archive_record_json}" --require-archive)
    chain_args+=(
      --archive-dir-verification "${archive_dir_json}"
      --require-archive-dir-verification
    )
  fi
  if [ -n "${VALIDATION_OCI_UPLOAD_BUCKET_NAME}" ]; then
    chain_args+=(--upload-manifest "${upload_manifest_json}" --require-upload)
    if [ "${VALIDATION_OCI_UPLOAD_EXECUTE}" = "1" ] || [ "${VALIDATION_OCI_UPLOAD_EXECUTE}" = "true" ]; then
      chain_args+=(--require-upload-executed)
    fi
  fi
  run_backend_python "${chain_args[@]}"
fi

if [ "${VALIDATION_MODE}" = "live" ]; then
  echo "[validate-production-evidence] runner readiness JSON: ${runner_readiness_json}"
  echo "[validate-production-evidence] runner readiness Markdown: ${runner_readiness_md}"
  echo "[validate-production-evidence] preflight JSON: ${preflight_json}"
  echo "[validate-production-evidence] preflight Markdown: ${preflight_md}"
fi
echo "[validate-production-evidence] evidence JSON: ${evidence_json}"
echo "[validate-production-evidence] evidence Markdown: ${evidence_md}"
echo "[validate-production-evidence] manifest JSON: ${manifest_json}"
if [ -n "${review_json}" ]; then
  echo "[validate-production-evidence] review JSON: ${review_json}"
  echo "[validate-production-evidence] bundle JSON: ${bundle_json}"
  echo "[validate-production-evidence] bundle Markdown: ${bundle_md}"
  echo "[validate-production-evidence] chain JSON: ${chain_json}"
  echo "[validate-production-evidence] chain Markdown: ${chain_md}"
  if [ -n "${VALIDATION_ARCHIVE_ROOT}" ]; then
    echo "[validate-production-evidence] archive root: ${VALIDATION_ARCHIVE_ROOT}"
    echo "[validate-production-evidence] archive record JSON: ${archive_record_json}"
    echo "[validate-production-evidence] archive record Markdown: ${archive_record_md}"
    echo "[validate-production-evidence] archive dir verification JSON: ${archive_dir_json}"
    echo "[validate-production-evidence] archive dir verification Markdown: ${archive_dir_md}"
    if [ -n "${VALIDATION_OCI_UPLOAD_BUCKET_NAME}" ]; then
      echo "[validate-production-evidence] upload manifest JSON: ${upload_manifest_json}"
      echo "[validate-production-evidence] upload manifest Markdown: ${upload_manifest_md}"
    fi
  fi
fi
