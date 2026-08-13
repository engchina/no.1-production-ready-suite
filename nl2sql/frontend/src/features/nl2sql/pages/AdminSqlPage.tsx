import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Play, X } from "lucide-react";

import { Button } from "@/components/ui/button";

import { ActionResultRegion } from "@/components/ActionResultRegion";
import {
  ExecutionActivityPanel,
  type ExecutionActivityStatus,
} from "@/components/ExecutionActivityPanel";
import { PageHeader } from "@/components/PageHeader";
import { apiPost } from "@/lib/api";
import type { OperationTimestamp } from "@/lib/operationTiming";
import { t } from "@/lib/i18n";
import {
  DbAdminExecutionResult,
  ExecutionConfirmationField,
  SqlFileInput,
  dbAdminExecutionActivityStatus,
} from "../components/DbAdminShared";
import { DEFAULT_SQL_ROW_LIMIT, RowLimitField, parseSqlRowLimit } from "../components/SqlRowLimitControls";
import type { DbAdminExecuteData } from "../types";

const ADMIN_EXECUTE_CONFIRMATION = "ADMIN_EXECUTE";
const MUTATING_SQL_TOKEN =
  /\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|begin|declare|call)\b/i;

function stripLeadingSqlComments(sql: string): string {
  let rest = sql.trim();
  let changed = true;
  while (changed) {
    changed = false;
    if (rest.startsWith("--")) {
      const nextLine = rest.indexOf("\n");
      rest = nextLine >= 0 ? rest.slice(nextLine + 1).trimStart() : "";
      changed = true;
    }
    if (rest.startsWith("/*")) {
      const close = rest.indexOf("*/");
      if (close < 0) return rest;
      rest = rest.slice(close + 2).trimStart();
      changed = true;
    }
  }
  return rest;
}

function isSingleSelectSql(sql: string): boolean {
  const normalized = sql.trim().replace(/;+$/g, "").trim();
  if (!normalized || normalized.includes(";")) return false;
  if (MUTATING_SQL_TOKEN.test(normalized)) return false;
  return /^(select|with)\b/i.test(stripLeadingSqlComments(normalized));
}

interface ExecutionRunState {
  operationKey: string | number;
  status: ExecutionActivityStatus;
  startedAt: OperationTimestamp;
  finishedAt?: OperationTimestamp;
  elapsedMs?: number | null;
}

function executionLabel(status: ExecutionActivityStatus) {
  if (status === "success") return t("executionActivity.execute.success");
  if (status === "error") return t("executionActivity.execute.error");
  return t("nl2sql.processing.executeSql");
}

/** 管理者向け SQL 実行ページ。更新系 SQL は確認語・RBAC・監査を必須とする。 */
export function AdminSqlPage() {
  const queryClient = useQueryClient();
  const [sqlText, setSqlText] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [confirmation, setConfirmation] = useState("");
  const [result, setResult] = useState<DbAdminExecuteData | null>(null);
  const [rowLimitInput, setRowLimitInput] = useState(String(DEFAULT_SQL_ROW_LIMIT));
  const [executedRowLimit, setExecutedRowLimit] = useState<number | null>(null);
  const [executionRun, setExecutionRun] = useState<ExecutionRunState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const trimmedSql = sqlText.trim();
  const requiresConfirmation = Boolean(trimmedSql) && !isSingleSelectSql(trimmedSql);
  const confirmed = confirmation.trim() === ADMIN_EXECUTE_CONFIRMATION;
  const rowLimit = parseSqlRowLimit(rowLimitInput);
  const rowLimitError =
    !requiresConfirmation && rowLimit === null ? t("queryResults.rowLimit.error") : "";
  const canExecute =
    Boolean(trimmedSql) &&
    !loading &&
    (requiresConfirmation || rowLimit !== null) &&
    (!requiresConfirmation || confirmed);

  const execute = async () => {
    if (!canExecute) return;
    const executionRowLimit = requiresConfirmation ? DEFAULT_SQL_ROW_LIMIT : rowLimit;
    if (executionRowLimit === null) return;
    const startedAt = Date.now();
    const operationKey = `admin-sql-execute-${startedAt}`;
    setLoading(true);
    setError("");
    setResult(null);
    setExecutedRowLimit(null);
    setExecutionRun({
      operationKey,
      status: "running",
      startedAt,
    });
    try {
      const data = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/execute", {
        sql: trimmedSql,
        row_limit: executionRowLimit,
        confirmation: requiresConfirmation ? confirmation.trim() : "",
        reason: requiresConfirmation ? "admin-sql-admin" : "admin-sql-select",
      });
      const finishedAt = data.timing.finished_at ?? Date.now();
      setResult(data);
      setExecutedRowLimit(requiresConfirmation ? null : executionRowLimit);
      setExecutionRun({
        operationKey,
        status: dbAdminExecutionActivityStatus(data),
        startedAt: data.timing.started_at ?? startedAt,
        finishedAt,
        elapsedMs: data.timing.elapsed_ms,
      });
      if (data.committed) {
        void queryClient.invalidateQueries({
          queryKey: ["nl2sql", "db-admin", "objects"],
        });
      }
    } catch (err) {
      const finishedAt = Date.now();
      setError(err instanceof Error ? err.message : t("nl2sql.error.executeSqlFailed"));
      setResult(null);
      setExecutedRowLimit(null);
      setExecutionRun({
        operationKey,
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
    setConfirmation("");
    setResult(null);
    setExecutedRowLimit(null);
    setExecutionRun(null);
    setRowLimitInput(String(DEFAULT_SQL_ROW_LIMIT));
    setError("");
    setSqlFileResetSignal((value) => value + 1);
  };

  const actionButtons = (
    <>
      <Button
        type="button"
        variant={requiresConfirmation ? "danger" : "primary"}
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
    </>
  );

  return (
    <>
      <PageHeader title={t("nav.adminSql")} subtitle={t("nl2sql.adminSqlRunner.description")} />
      <main className="grid gap-4 p-4 lg:p-8" data-testid="nl2sql-admin-sql">
        <section className="grid gap-4 rounded-md border border-border bg-card p-4">
          <label className="grid gap-2 text-sm font-medium text-foreground">
            <span>{t("nl2sql.adminSqlRunner.label")}</span>
            <textarea
              aria-label={t("nl2sql.adminSqlRunner.label")}
              value={sqlText}
              onChange={(event) => setSqlText(event.currentTarget.value)}
              disabled={loading}
              rows={12}
              className="min-h-64 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
              placeholder={t("nl2sql.adminSqlRunner.placeholder")}
            />
          </label>
          <SqlFileInput
            resetSignal={sqlFileResetSignal}
            disabled={loading}
            onLoad={(text) => {
              setSqlText(text);
              setConfirmation("");
              setResult(null);
              setExecutedRowLimit(null);
              setExecutionRun(null);
              setError("");
            }}
          />
          {requiresConfirmation ? (
            <ExecutionConfirmationField
              value={confirmation}
              onChange={setConfirmation}
              confirmed={confirmed}
              placeholder={ADMIN_EXECUTE_CONFIRMATION}
              expectedLabel={ADMIN_EXECUTE_CONFIRMATION}
              helper={t("nl2sql.adminSqlRunner.adminHelper")}
              tone="danger"
              disabled={loading}
              actions={actionButtons}
            />
          ) : (
            <div className="grid gap-3 border-t border-border pt-4">
              <RowLimitField
                value={rowLimitInput}
                onChange={setRowLimitInput}
                disabled={loading}
                error={rowLimitError}
                className="sm:w-48"
              />
              <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                {actionButtons}
              </div>
            </div>
          )}
          {executionRun && (
            <ExecutionActivityPanel
              status={executionRun.status}
              label={executionLabel(executionRun.status)}
              operationKey={executionRun.operationKey}
              startedAt={executionRun.startedAt}
              finishedAt={executionRun.finishedAt}
              elapsedMs={executionRun.elapsedMs}
              testId="admin-sql-execution-activity"
            />
          )}
          <ActionResultRegion
            loading={loading}
            operationKey="admin-sql-execute"
            loadingLabel={t("nl2sql.processing.executeSql")}
            errorMessage={error}
            testId="admin-sql-processing"
          >
            {result ? (
              <DbAdminExecutionResult result={result} rowLimit={executedRowLimit} />
            ) : null}
          </ActionResultRegion>
        </section>
      </main>
    </>
  );
}
