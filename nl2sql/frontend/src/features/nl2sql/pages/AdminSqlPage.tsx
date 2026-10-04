import { useWorkspaceState, useWorkspaceRevalidation, useResetExecutionConsent } from "@/components/WorkspaceState";
import { useCallback, useEffect, useState } from "react";
import { useValuesChanged } from "@/lib/render-sync";
import { useQueryClient } from "@tanstack/react-query";
import { Play, RefreshCw, X } from "lucide-react";

import {
  Button,
  PageHeader,
  PageBody,
  ActionResultRegion,
  ExecutionConfirmationField,
  TextareaField,
} from "@engchina/production-ready-ui";

import {
  ExecutionActivityPanel,
  type ExecutionActivityStatus,
} from "@/components/ExecutionActivityPanel";
import { PageNotice } from "@/components/page-notice";
import { apiPost } from "@/lib/api";
import type { OperationTimestamp } from "@/lib/operationTiming";
import { t } from "@/lib/i18n";
import {
  DbAdminExecutionResult,
  SqlFileInput,
  dbAdminExecutionActivityStatus,
} from "../components/DbAdminShared";
import { DEFAULT_SQL_ROW_LIMIT, RowLimitField, parseSqlRowLimit } from "../components/SqlRowLimitControls";
import {
  SchemaRefreshHeaderStatus,
  SchemaRefreshProcessing,
} from "../components/SchemaRefreshFeedback";
import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  nl2sqlIncrementalKeys,
  useSchemaRefreshJob,
} from "../incrementalQueries";
import type { DbAdminExecuteData, SchemaRefreshJob } from "../types";

const ADMIN_EXECUTE_CONFIRMATION = "ADMIN_EXECUTE";
/** 失敗の案内の「スキーマを更新」の起点（SchemaRefreshCoordinator の start(origin)。#821）。 */
const ADMIN_SQL_SCHEMA_REFRESH_NOTICE = "admin-sql-notice";
const MUTATING_SQL_TOKEN =
  /\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|begin|declare|call)\b/i;
const Q_QUOTE_CLOSERS: Record<string, string> = {
  "[": "]",
  "(": ")",
  "{": "}",
  "<": ">",
};

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
  const stripped = stripLeadingSqlComments(sql);
  const masked = maskSqlLiteralsAndComments(stripped);
  const normalized = masked.trim().replace(/;+$/g, "").trim();
  if (!normalized || normalized.includes(";")) return false;
  if (MUTATING_SQL_TOKEN.test(normalized)) return false;
  const head = normalized.replace(/^\(+/u, "").trimStart();
  return /^(select|with)\b/i.test(head);
}

function maskSqlLiteralsAndComments(sql: string): string {
  const text = String(sql || "");
  let out = "";
  let index = 0;
  while (index < text.length) {
    const char = text[index] ?? "";
    const nextChar = text[index + 1] ?? "";
    if (char === "-" && nextChar === "-") {
      const newline = text.indexOf("\n", index);
      const end = newline >= 0 ? newline : text.length;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    if (char === "/" && nextChar === "*") {
      const close = text.indexOf("*/", index + 2);
      if (close < 0) {
        out += text.slice(index);
        break;
      }
      const end = close + 2;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    const previous = index > 0 ? text[index - 1] ?? "" : "";
    if (
      (char === "q" || char === "Q") &&
      nextChar === "'" &&
      index + 2 < text.length &&
      !/[A-Za-z0-9_$#]/u.test(previous)
    ) {
      const opener = text[index + 2] ?? "";
      const closer = Q_QUOTE_CLOSERS[opener] ?? opener;
      const close = text.indexOf(`${closer}'`, index + 3);
      if (close < 0) {
        out += text.slice(index);
        break;
      }
      const end = close + 2;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    if (char === "'" || char === '"') {
      let end = index + 1;
      let closed = false;
      while (end < text.length) {
        if (text[end] !== char) {
          end += 1;
          continue;
        }
        if (char === "'" && text[end + 1] === "'") {
          end += 2;
          continue;
        }
        closed = true;
        break;
      }
      if (!closed) {
        out += text.slice(index);
        break;
      }
      end += 1;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    out += char;
    index += 1;
  }
  return out;
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

function schemaRefreshRequiresFull(job: SchemaRefreshJob | null) {
  if (!job) return false;
  return (
    Boolean(job.requires_full_refresh) ||
    job.error_code === "schema_refresh_full_required" ||
    job.error_code === "schema_refresh_target_unresolved"
  );
}

function schemaRefreshRequiredMessage(reasonCode = "") {
  if (reasonCode === "schema_refresh_target_unresolved") {
    return t("dataMgmt.schemaJob.targetUnresolved");
  }
  return t("dataMgmt.schemaJob.fullRequired");
}

function schemaRefreshErrorMessage(job: SchemaRefreshJob) {
  if (schemaRefreshRequiresFull(job)) {
    return schemaRefreshRequiredMessage(job.error_code);
  }
  return job.error_code
    ? `${t("dataMgmt.schemaJob.error")} (${job.error_code})`
    : t("dataMgmt.schemaJob.error");
}

/** 管理者向け SQL 実行ページ。更新系 SQL は確認語・RBAC・監査を必須とする。 */
export function AdminSqlPage() {
  const queryClient = useQueryClient();
  useWorkspaceRevalidation();
  const [sqlText, setSqlText] = useWorkspaceState("sqlText", "");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [confirmation, setConfirmation] = useState("");
  const [sqlError, setSqlError] = useState("");
  const [result, setResult] = useState<DbAdminExecuteData | null>(null);
  const [rowLimitInput, setRowLimitInput] = useWorkspaceState("rowLimitInput", String(DEFAULT_SQL_ROW_LIMIT));
  const [executedRowLimit, setExecutedRowLimit] = useState<number | null>(null);
  const [executionRun, setExecutionRun] = useState<ExecutionRunState | null>(null);
  const [schemaRefreshJobId, setSchemaRefreshJobId] = useState("");
  const [schemaRefreshError, setSchemaRefreshError] = useState("");
  const [schemaRefreshNeedsFull, setSchemaRefreshNeedsFull] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useResetExecutionConsent(() => { setConfirmation(""); }, JSON.stringify([sqlText, rowLimitInput]));
  // 報告済みの schema refresh の終端（`<job_id>:<status>` / `<job_id>:query-error`）。
  const [reportedSchemaRefresh, setReportedSchemaRefresh] = useState("");
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  const noticeSchemaRefreshStarting = sharedSchemaRefresh.startingOrigin === ADMIN_SQL_SCHEMA_REFRESH_NOTICE;
  const visibleSchemaRefreshError = schemaRefreshJobQuery.error
    ? schemaRefreshJobQuery.error instanceof Error
      ? schemaRefreshJobQuery.error.message
      : t("dataMgmt.schemaJob.error")
    : schemaRefreshError || sharedSchemaRefresh.error;
  const trimmedSql = sqlText.trim();
  const requiresConfirmation = Boolean(trimmedSql) && !isSingleSelectSql(trimmedSql);
  const confirmed = confirmation.trim() === ADMIN_EXECUTE_CONFIRMATION;
  const rowLimit = parseSqlRowLimit(rowLimitInput);
  const rowLimitError =
    !requiresConfirmation && rowLimit === null ? t("queryResults.rowLimit.error") : "";
  // 管理 SQL の未入力は押せないボタンだけにせず、押したときに欄の直下へ理由を出す（#541）。
  // 取得件数上限と確認語は、欄の直下（説明）に理由を出したうえで押せなくする。
  const canExecute =
    !loading &&
    (!trimmedSql ||
      ((requiresConfirmation || rowLimit !== null) && (!requiresConfirmation || confirmed)));
  const canClear = Boolean(
    sqlText || rowLimitInput !== String(DEFAULT_SQL_ROW_LIMIT) || result || executionRun
  );

  // queryClient だけに依存する固定の関数にする（終端の effect の deps に入れても発火の条件は変わらない）。
  const refreshSchemaReadModels = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["schema", "objects"] });
    void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin", "objects"] });
    void queryClient.invalidateQueries({ queryKey: nl2sqlIncrementalKeys.schemaHead });
  }, [queryClient]);

  const trackSchemaRefreshResult = (data: {
    schema_refresh_job_id?: string;
    schema_refresh_required?: boolean;
    schema_refresh_reason_code?: string;
  }) => {
    if (data.schema_refresh_job_id) {
      setReportedSchemaRefresh("");
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      setSchemaRefreshJobId(data.schema_refresh_job_id);
      sharedSchemaRefresh.track(data.schema_refresh_job_id);
      return true;
    }
    if (data.schema_refresh_required) {
      setSchemaRefreshError(schemaRefreshRequiredMessage(data.schema_refresh_reason_code));
      setSchemaRefreshNeedsFull(true);
      return true;
    }
    return false;
  };

  const refreshSchema = async () => {
    setReportedSchemaRefresh("");
    try {
      const job = await sharedSchemaRefresh.start(ADMIN_SQL_SCHEMA_REFRESH_NOTICE);
      setSchemaRefreshJobId(job.job_id);
      if (!job.job_id && job.status === "done") {
        setSchemaRefreshError("");
        setSchemaRefreshNeedsFull(false);
        refreshSchemaReadModels();
      }
    } catch (err) {
      setSchemaRefreshError(err instanceof Error ? err.message : t("dataMgmt.schemaJob.submitError"));
      setSchemaRefreshNeedsFull(true);
    }
  };

  // job の終端・取得失敗を初めて見たレンダーで、error 表示を直す（effect で setState しない）。
  // 読み取りモデルの再取得は、終端を報告した後の effect で行う。
  const schemaRefreshJob = schemaRefreshJobQuery.data;
  const schemaRefreshJobChanged = useValuesChanged([schemaRefreshJob]);
  const schemaRefreshQueryErrorChanged = useValuesChanged([
    schemaRefreshJobId,
    schemaRefreshJobQuery.error,
  ]);
  let nextReportedSchemaRefresh = reportedSchemaRefresh;
  if (
    schemaRefreshJobChanged &&
    schemaRefreshJob &&
    (schemaRefreshJob.status === "done" || schemaRefreshJob.status === "error")
  ) {
    const reportKey = `${schemaRefreshJob.job_id}:${schemaRefreshJob.status}`;
    if (nextReportedSchemaRefresh !== reportKey) {
      nextReportedSchemaRefresh = reportKey;
      if (schemaRefreshJob.status === "done") {
        setSchemaRefreshError("");
        setSchemaRefreshNeedsFull(false);
      } else {
        setSchemaRefreshNeedsFull(schemaRefreshRequiresFull(schemaRefreshJob));
        setSchemaRefreshError(schemaRefreshErrorMessage(schemaRefreshJob));
      }
    }
  }
  if (schemaRefreshQueryErrorChanged && schemaRefreshJobQuery.error && schemaRefreshJobId) {
    const reportKey = `${schemaRefreshJobId}:query-error`;
    if (nextReportedSchemaRefresh !== reportKey) {
      nextReportedSchemaRefresh = reportKey;
      setSchemaRefreshError(
        schemaRefreshJobQuery.error instanceof Error
          ? schemaRefreshJobQuery.error.message
          : t("dataMgmt.schemaJob.error"),
      );
      setSchemaRefreshNeedsFull(false);
    }
  }
  if (nextReportedSchemaRefresh !== reportedSchemaRefresh) {
    setReportedSchemaRefresh(nextReportedSchemaRefresh);
  }

  useEffect(() => {
    if (reportedSchemaRefresh.endsWith(":done")) refreshSchemaReadModels();
  }, [reportedSchemaRefresh, refreshSchemaReadModels]);

  const execute = async () => {
    if (!canExecute) return;
    if (!trimmedSql) {
      setSqlError(t("nl2sql.adminSqlRunner.error.required"));
      document.getElementById("admin-sql-input")?.focus();
      return;
    }
    setSqlError("");
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
      const schemaRefreshTracked = trackSchemaRefreshResult(data);
      if (data.committed && !schemaRefreshTracked) {
        void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin", "objects"] });
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
    setSqlError("");
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
        onClick={() => void execute()} icon={Play}>
        <span>{t("nl2sql.action.executeSql")}</span>
      </Button>
      <Button
        type="button"
        variant="secondary"
        size="lg"
        className="w-full sm:w-auto"
        disabled={!canClear || loading}
        onClick={clear} icon={X}>
        <span>{t("nl2sql.action.clearSql")}</span>
      </Button>
    </>
  );

  return (
    <>
      <PageHeader wide
        title={t("nav.adminSql")}
        subtitle={t("nl2sql.adminSqlRunner.description")}
        status={<SchemaRefreshHeaderStatus testId="admin-sql-schema-refresh-status" />}
      />
      <PageBody wide className="grid gap-4" data-testid="nl2sql-admin-sql">
        <PageNotice
          notice={
            visibleSchemaRefreshError
              ? { tone: "danger", message: visibleSchemaRefreshError }
              : null
          }
          action={
            schemaRefreshNeedsFull ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                // 押したときの送信の間だけ回し、job の間は無効にするだけ（スピナーは進行の表示。#821）。
                loading={noticeSchemaRefreshStarting}
                disabled={schemaRefreshing && !noticeSchemaRefreshStarting}
                onClick={() => void refreshSchema()} icon={RefreshCw}>
                <span>{t("common.action.schemaRefresh")}</span>
              </Button>
            ) : null
          }
        />
        <section className="grid gap-4 rounded-md border border-border bg-surface p-4">
          <TextareaField
            id="admin-sql-input"
            label={t("nl2sql.adminSqlRunner.label")}
            required
            error={sqlError || undefined}
            value={sqlText}
            onChange={(event) => {
              setSqlText(event.currentTarget.value);
              setSqlError("");
            }}
            disabled={loading}
            rows={12}
            monospace
            placeholder={t("nl2sql.adminSqlRunner.placeholder")}
          />
          <SqlFileInput
            resetSignal={sqlFileResetSignal}
            disabled={loading}
            onLoad={(text) => {
              setSqlText(text);
              // 手入力と同じく、読み込んだら未入力のエラーを消す（#933）。
              setSqlError("");
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
              disabled={loading}
              actions={actionButtons}
            />
          ) : (
            // 取得件数上限は短い数値の幅（xs。#613）。操作ボタンはヘルパーテキストの下の行に置く。
            <div className="grid grid-cols-1 gap-3 border-t border-border pt-4">
              <RowLimitField
                value={rowLimitInput}
                onChange={setRowLimitInput}
                disabled={loading}
                error={rowLimitError}
              />
              <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                {actionButtons}
              </div>
            </div>
          )}
          {executionRun && (
            <ExecutionActivityPanel
              inputSignature={JSON.stringify([sqlText, requiresConfirmation ? null : rowLimit])}
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
          {schemaRefreshing ? (
            <SchemaRefreshProcessing testId="admin-sql-schema-refresh-processing" />
          ) : null}
        </section>
      </PageBody>
    </>
  );
}
