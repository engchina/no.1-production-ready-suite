import { useId } from "react";

import {
  FieldError,
  StatusBadge,
  FieldLabel,
} from "@engchina/production-ready-ui";
import { t } from "@/lib/i18n";
import type { QueryResults } from "../types";

export const DEFAULT_SQL_ROW_LIMIT = 100;
export const MAX_SQL_ROW_LIMIT = 100000;

export function parseSqlRowLimit(value: string): number | null {
  const normalized = value.trim();
  if (!/^\d+$/.test(normalized)) return null;
  const rowLimit = Number(normalized);
  if (!Number.isInteger(rowLimit) || rowLimit < 1 || rowLimit > MAX_SQL_ROW_LIMIT) return null;
  return rowLimit;
}

export function RowLimitField({
  value,
  onChange,
  disabled = false,
  error = "",
  className = "",
  helper = t("queryResults.rowLimit.helper"),
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  error?: string;
  className?: string;
  helper?: string;
}) {
  const id = useId();
  const helperId = `${id}-helper`;
  const errorId = `${id}-error`;
  const describedBy = error ? `${helperId} ${errorId}` : helperId;

  return (
    // 空や範囲外では実行できない（parseSqlRowLimit が null → 実行ボタン無効）ので必須として示す（#531）。
    <div className={`grid w-full min-w-0 gap-1 text-sm font-medium text-fg ${className}`}>
      <FieldLabel htmlFor={id} label={t("queryResults.rowLimit.label")} required />
      <input
        id={id}
        type="number"
        aria-required="true"
        min={1}
        max={MAX_SQL_ROW_LIMIT}
        step={1}
        inputMode="numeric"
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        disabled={disabled}
        aria-describedby={describedBy}
        aria-invalid={error ? "true" : undefined}
        className="h-10 w-full min-w-0 rounded-md border border-border-control bg-surface px-3 text-sm text-fg transition-colors placeholder:text-fg-muted disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-fg-disabled focus:border-focus-ring"
      />
      <p id={helperId} className="overflow-x-auto whitespace-nowrap text-xs leading-5 text-fg-muted">
        {helper}
      </p>
      <FieldError id={errorId} message={error} />
    </div>
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
  const returnedCount =
    typeof results.returned_count === "number" ? results.returned_count : results.total;
  const hasIncompleteResults = Boolean(results.has_more || results.truncated);
  const reachedRowLimit =
    hasRowLimit && rowLimit > 0 && (hasIncompleteResults || returnedCount === rowLimit);
  const hasMoreWithoutLimit = hasIncompleteResults && !reachedRowLimit;
  const executionContext = results.execution_context ?? "deterministic";
  const showExecutionContext =
    executionContext !== "deterministic" || Boolean(results.vpd_context_enforced);

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="query-result-summary">
      <StatusBadge icon={false} variant="neutral" label={t("queryResults.fetchedCount", { count: returnedCount })} />
      {showExecutionContext ? (
        <StatusBadge
          icon={false}
          variant={results.vpd_context_enforced ? "info" : "neutral"}
          label={t(`queryResults.executionContext.${executionContext}`)}
        />
      ) : null}
      {hasRowLimit && (
        <StatusBadge
          icon={false}
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
      {hasMoreWithoutLimit && (
        <StatusBadge variant="warning" label={t("queryResults.rowLimit.partial")} />
      )}
    </div>
  );
}
