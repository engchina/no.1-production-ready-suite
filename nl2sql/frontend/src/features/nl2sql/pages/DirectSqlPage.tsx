import { useState } from "react";
import { Play, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FieldLabel } from "@/components/ui/required-field";

import { ActionResultRegion } from "@/components/ActionResultRegion";
import {
  ExecutionActivityPanel,
  type ExecutionActivityStatus,
} from "@/components/ExecutionActivityPanel";
import { PageHeader } from "@/components/PageHeader";
import { Banner } from "@/components/ui/banner";
import { useAuth } from "@/features/security/AuthProvider";
import { MENU_PERMISSIONS } from "@/features/security/menu-permissions";
import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { SqlFileInput } from "../components/DbAdminShared";
import { Nl2SqlResultTable } from "../components/Nl2SqlResultTable";
import { DEFAULT_SQL_ROW_LIMIT, RowLimitField, parseSqlRowLimit } from "../components/SqlRowLimitControls";
import { sqlExecutePayload } from "../previewState";
import type { QueryResults } from "../types";
import { emptySelection, toAllowedObjects } from "../workbenchState";

interface ExecutionRunState {
  operationKey: number;
  status: ExecutionActivityStatus;
  startedAt: number;
  finishedAt?: number | null;
  elapsedMs?: number | null;
}

function executionLabel(status: ExecutionActivityStatus) {
  if (status === "success") return t("executionActivity.execute.success");
  if (status === "error") return t("executionActivity.execute.error");
  return t("nl2sql.processing.executeSql");
}

/**
 * SELECT/WITH を直接実行する AI 活用ページ。
 * 管理 SQL API へは接続せず、通常の SELECT-only 実行境界を使用する。
 */
export function DirectSqlPage() {
  const { hasPermission } = useAuth();
  const canExecute = hasPermission(MENU_PERMISSIONS.directSql);

  if (!canExecute) {
    return (
      <>
        <PageHeader title={t("nav.directSql")} subtitle={t("nl2sql.sqlRunner.description")} />
        <main className="p-4 lg:p-8">
          <Banner severity="info">{t("nl2sql.permission.executeRequired")}</Banner>
        </main>
      </>
    );
  }

  return <ExecutableDirectSqlPage />;
}

function ExecutableDirectSqlPage() {
  const [sqlText, setSqlText] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [results, setResults] = useState<QueryResults | null>(null);
  const [rowLimitInput, setRowLimitInput] = useState(String(DEFAULT_SQL_ROW_LIMIT));
  const [executedRowLimit, setExecutedRowLimit] = useState<number | null>(null);
  const [executionRun, setExecutionRun] = useState<ExecutionRunState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // /api/nl2sql/execute は 1..5000 のみ受理する(0=無制限は db-admin 専用)。
  const parsedRowLimit = parseSqlRowLimit(rowLimitInput);
  const rowLimit =
    parsedRowLimit !== null && parsedRowLimit >= 1 && parsedRowLimit <= 5000
      ? parsedRowLimit
      : null;
  const rowLimitError = rowLimit === null ? t("queryResults.rowLimit.errorBounded") : "";
  const canExecute = Boolean(sqlText.trim()) && !loading && rowLimit !== null;

  const execute = async () => {
    const trimmedSql = sqlText.trim();
    if (!trimmedSql || loading || rowLimit === null) return;
    const startedAt = Date.now();
    setLoading(true);
    setError("");
    setResults(null);
    setExecutedRowLimit(null);
    setExecutionRun({ operationKey: startedAt, status: "running", startedAt });
    try {
      const data = await apiPost<QueryResults>("/api/nl2sql/execute", {
        ...sqlExecutePayload(trimmedSql, toAllowedObjects(emptySelection()), rowLimit),
      });
      const finishedAt = Date.now();
      setResults(data);
      setExecutedRowLimit(rowLimit);
      setExecutionRun({
        operationKey: startedAt,
        status: "success",
        startedAt,
        finishedAt,
        elapsedMs: finishedAt - startedAt,
      });
    } catch (err) {
      const finishedAt = Date.now();
      setError(err instanceof Error ? err.message : t("nl2sql.error.executeSqlFailed"));
      setResults(null);
      setExecutedRowLimit(null);
      setExecutionRun({
        operationKey: startedAt,
        status: "error",
        startedAt,
        finishedAt,
        elapsedMs: finishedAt - startedAt,
      });
    } finally {
      setLoading(false);
    }
  };

  const clear = () => {
    setSqlText("");
    setResults(null);
    setExecutedRowLimit(null);
    setExecutionRun(null);
    setRowLimitInput(String(DEFAULT_SQL_ROW_LIMIT));
    setError("");
    setSqlFileResetSignal((value) => value + 1);
  };

  return (
    <>
      <PageHeader title={t("nav.directSql")} subtitle={t("nl2sql.sqlRunner.description")} />
      <main className="grid gap-4 p-4 lg:p-8" data-testid="nl2sql-direct-sql">
        <section className="grid gap-4 rounded-md border border-border bg-card p-4">
          <div className="grid gap-2">
            <FieldLabel
              htmlFor="direct-sql-input"
              label={t("nl2sql.sqlRunner.label")}
              required
            />
            <textarea
              id="direct-sql-input"
              value={sqlText}
              onChange={(event) => setSqlText(event.currentTarget.value)}
              disabled={loading}
              rows={12}
              required
              aria-required="true"
              className="min-h-64 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
              placeholder={t("nl2sql.sqlRunner.placeholder")}
            />
          </div>
          <SqlFileInput
            resetSignal={sqlFileResetSignal}
            disabled={loading}
            onLoad={(text) => {
              setSqlText(text);
              setResults(null);
              setExecutedRowLimit(null);
              setExecutionRun(null);
              setError("");
            }}
          />
          <div className="grid gap-3 border-t border-border pt-4">
            <RowLimitField
              value={rowLimitInput}
              onChange={setRowLimitInput}
              disabled={loading}
              error={rowLimitError}
              className="sm:w-48"
              min={1}
              max={5000}
              helper={t("queryResults.rowLimit.helperBounded")}
            />
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <Button
                type="button"
                variant="primary"
                size="lg"
                className="w-full sm:w-auto"
                loading={loading}
                disabled={!canExecute}
                onClick={() => void execute()}
              >
                <Play size={16} aria-hidden="true" />
                <span>{t("nl2sql.action.executeSql")}</span>
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="lg"
                className="w-full sm:w-auto"
                disabled={!sqlText || loading}
                onClick={clear}
              >
                <X size={16} aria-hidden="true" />
                <span>{t("nl2sql.action.clearSql")}</span>
              </Button>
            </div>
          </div>
          {executionRun && (
            <ExecutionActivityPanel
              status={executionRun.status}
              label={executionLabel(executionRun.status)}
              operationKey={executionRun.operationKey}
              startedAt={executionRun.startedAt}
              finishedAt={executionRun.finishedAt}
              elapsedMs={executionRun.elapsedMs}
              testId="direct-sql-execution-activity"
            />
          )}
          <ActionResultRegion
            loading={loading}
            operationKey="direct-sql-execute"
            loadingLabel={t("nl2sql.processing.executeSql")}
            errorMessage={error}
            testId="direct-sql-processing"
          >
            {results ? (
              <Nl2SqlResultTable results={results} rowLimit={executedRowLimit} />
            ) : null}
          </ActionResultRegion>
        </section>
      </main>
    </>
  );
}
