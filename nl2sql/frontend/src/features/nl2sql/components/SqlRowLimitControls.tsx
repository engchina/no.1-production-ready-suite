import { useId } from "react";

import { FieldError } from "@/components/ui/field-error";
import { StatusBadge } from "@/components/ui/status-badge";
import { t } from "@/lib/i18n";
import type { QueryResults } from "../types";

export const DEFAULT_SQL_ROW_LIMIT = 100;

export function parseSqlRowLimit(value: string): number | null {
  const normalized = value.trim();
  if (!/^\d+$/.test(normalized)) return null;
  const rowLimit = Number(normalized);
  if (!Number.isFinite(rowLimit) || !Number.isInteger(rowLimit) || rowLimit < 0) return null;
  return rowLimit;
}

export function RowLimitField({
  value,
  onChange,
  disabled = false,
  error = "",
  className = "",
  min = 0,
  max,
  helper = t("queryResults.rowLimit.helper"),
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string;
  className?: string;
  /** 許容最小値。0 = 無制限を許す画面(db-admin)は既定のまま、/execute 系は 1 を渡す。 */
  min?: number;
  max?: number;
  helper?: string;
}) {
  const id = useId();
  const helperId = `${id}-helper`;
  const errorId = `${id}-error`;
  const describedBy = error ? `${helperId} ${errorId}` : helperId;

  return (
    <label className={`grid min-w-0 gap-1 text-sm font-medium text-foreground ${className}`}>
      <span>{t("queryResults.rowLimit.label")}</span>
      <input
        id={id}
        type="number"
        min={min}
        max={max}
        step={1}
        inputMode="numeric"
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        disabled={disabled}
        aria-describedby={describedBy}
        aria-invalid={error ? "true" : undefined}
        className="h-10 rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted focus:border-primary focus:ring-2 focus:ring-ring/40"
      />
      <p id={helperId} className="text-xs leading-5 text-muted">
        {helper}
      </p>
      <FieldError id={errorId} message={error} />
    </label>
  );
}

export function QueryResultSummary({
  results,
  rowLimit,
}: {
  results: QueryResults;
  rowLimit?: number | null;
}) {
  const hasRowLimit = typeof rowLimit === "number";
  const reachedRowLimit = hasRowLimit && rowLimit > 0 && results.total === rowLimit;
  const executionContext = results.execution_context ?? "deterministic";
  const showExecutionContext =
    executionContext !== "deterministic" || Boolean(results.vpd_context_enforced);

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="query-result-summary">
      <StatusBadge variant="neutral" label={t("queryResults.fetchedCount", { count: results.total })} />
      {showExecutionContext ? (
        <StatusBadge
          variant={results.vpd_context_enforced ? "info" : "neutral"}
          label={t(`queryResults.executionContext.${executionContext}`)}
        />
      ) : null}
      {hasRowLimit && (
        <StatusBadge
          variant="neutral"
          label={
            rowLimit === 0
              ? t("queryResults.rowLimit.unlimited")
              : t("queryResults.rowLimit.value", { count: rowLimit })
          }
        />
      )}
      {reachedRowLimit && (
        <StatusBadge variant="warning" label={t("queryResults.rowLimit.reached")} />
      )}
    </div>
  );
}
