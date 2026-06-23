export const DEFAULT_ANALYZE_SQL = "";

export function normalizeAnalyzeRowLimit(rowLimit: number): number {
  if (!Number.isFinite(rowLimit)) return 100;
  return Math.min(5000, Math.max(1, Math.trunc(rowLimit)));
}

export function sqlAnalyzePayload(sql: string, rowLimit: number) {
  return {
    sql: sql.trim(),
    row_limit: normalizeAnalyzeRowLimit(rowLimit),
  };
}
