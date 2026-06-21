import type { AllowedObjects, GeneratedSqlPanelData, PreviewData, SafetyReport } from "./types";

function previewFallbackSafety(preview: PreviewData): SafetyReport {
  return {
    is_safe: preview.is_safe,
    is_select_only: preview.is_safe,
    row_limit_applied: preview.row_limit,
    blocked_reason: preview.is_safe ? "" : preview.note,
    warnings: [],
    referenced_tables: [],
    referenced_columns: [],
  };
}

export function previewToGeneratedSqlPanelData(preview: PreviewData): GeneratedSqlPanelData {
  return {
    engine: preview.engine,
    engine_meta: preview.engine_meta,
    fallback_reason: preview.fallback_reason,
    generated_sql: preview.sql,
    executable_sql: preview.executable_sql,
    explanation: preview.note,
    safety: preview.safety ?? previewFallbackSafety(preview),
    recommendations: preview.recommendations,
    repaired_sql: preview.repaired_sql,
    optimization_hints: preview.optimization_hints,
    rewritten_question: preview.rewritten_question,
  };
}

export function previewExecutePayload(
  sql: string,
  profileId: string | null,
  allowedObjects: AllowedObjects,
  rowLimit: number
) {
  return {
    sql: sql.trim(),
    profile_id: profileId,
    allowed_objects: allowedObjects,
    row_limit: rowLimit,
  };
}
