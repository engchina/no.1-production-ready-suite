import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Code2,
  Download,
  FileSpreadsheet,
  FileText,
  Play,
  Search,
  Sparkles,
  Upload,
  X,
} from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, StatusBadge } from "@engchina/production-ready-ui";

import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import type {
  DbAdminAiAnalysisData,
  DbAdminExecuteData,
  DbAdminObjectDetail,
  DbAdminObjectSummary,
  DbAdminStatementPolicy,
  QueryResults,
  SchemaCatalog,
} from "../types";

/** テキストファイルを SQL としてダウンロードする。 */
export function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/sql;charset=utf-8" });
  downloadBlob(filename, blob);
}

export function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** File を base64 文字列に変換する(tabular import 用)。 */
export async function fileToBase64(file: File) {
  const buffer = await file.arrayBuffer();
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary);
}

/** UTF-8 で読めない場合は Shift_JIS を試すテキスト読込(SQL/CSV ファイル用)。 */
export async function readTextFileSmart(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const utf8 = new TextDecoder("utf-8", { fatal: false }).decode(buffer);
  if (!utf8.includes("�")) return utf8;
  try {
    return new TextDecoder("shift_jis").decode(buffer);
  } catch {
    return utf8;
  }
}

/** 実行結果を AI 分析へ渡すテキストに整形する。 */
export function executionResultText(result: DbAdminExecuteData | null): string {
  if (!result) return "";
  return result.statements
    .map((statement) =>
      `#${statement.index} [${statement.status}] ${statement.message || ""} ${statement.error_message || ""}`.trim()
    )
    .concat(result.warnings)
    .join("\n");
}

export function PageMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

export function WorkSection({
  title,
  description,
  tone = "neutral",
  children,
}: {
  title: string;
  description: string;
  tone?: "neutral" | "danger";
  children: ReactNode;
}) {
  const toneClass =
    tone === "danger"
      ? "border-red-200 bg-red-50/70 text-red-950 marker:text-red-700"
      : "border-slate-200 bg-white text-slate-900 marker:text-slate-500";

  return (
    <details className={`rounded-md border ${toneClass}`}>
      <summary className="cursor-pointer px-4 py-3 focus:outline-none focus:ring-2 focus:ring-sky-200">
        <span className="font-semibold">{title}</span>
        <span className="mt-1 block text-sm font-normal text-slate-600">{description}</span>
      </summary>
      <div className="border-t border-current/10 bg-white p-3">{children}</div>
    </details>
  );
}

export function ExecutionConfirmationField({
  value,
  onChange,
  confirmed,
  placeholder,
  expectedLabel,
  helper,
  tone = "neutral",
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  confirmed: boolean;
  placeholder: string;
  expectedLabel: string;
  helper: string;
  tone?: "neutral" | "danger";
  disabled?: boolean;
}) {
  const id = useId();
  const helperId = `${id}-helper`;
  const statusLabel = confirmed
    ? t("dbAdmin.confirmation.status.confirmed")
    : value.trim()
      ? t("dbAdmin.confirmation.status.mismatch")
      : t("dbAdmin.confirmation.status.pending");
  const isDanger = tone === "danger";
  const containerClass = [
    "grid gap-2 rounded-md border p-3",
    isDanger ? "border-red-200 bg-red-50/70" : "border-slate-200 bg-slate-50",
  ].join(" ");
  const inputClass = [
    "h-11 w-full rounded-md border bg-white px-3 py-2 text-sm outline-none transition-colors placeholder:text-slate-400 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500",
    isDanger
      ? "border-red-200 focus:border-red-600 focus:ring-2 focus:ring-red-200"
      : "border-slate-300 focus:border-sky-600 focus:ring-2 focus:ring-sky-200",
  ].join(" ");
  const statusClass = [
    "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 text-xs font-semibold",
    confirmed
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : value.trim()
        ? "border-red-200 bg-red-50 text-red-800"
        : "border-slate-200 bg-white text-slate-600",
  ].join(" ");

  return (
    <div className={containerClass} data-testid="execution-confirmation-field">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <label htmlFor={id} className={`text-sm font-semibold ${isDanger ? "text-red-950" : "text-slate-900"}`}>
          {t("dbAdmin.confirmation.label")}
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <span className="max-w-full break-all rounded-md bg-white px-2 py-1 font-mono text-xs text-slate-700">
            {t("dbAdmin.confirmation.expected", { phrase: expectedLabel })}
          </span>
          <span className={statusClass} aria-live="polite">
            {statusLabel}
          </span>
        </div>
      </div>
      <div className="grid gap-2">
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.currentTarget.value)}
          className={inputClass}
          placeholder={placeholder}
          disabled={disabled}
          aria-describedby={helperId}
          aria-invalid={value.trim() && !confirmed ? "true" : undefined}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
      </div>
      <p id={helperId} className={`text-xs leading-5 ${isDanger ? "text-red-800" : "text-slate-600"}`}>
        {helper}
      </p>
    </div>
  );
}

const QUERY_RESULTS_PAGE_SIZE = 10;

export function QueryResultsTable({ results }: { results: QueryResults }) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(results.rows.length / QUERY_RESULTS_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const start = (currentPage - 1) * QUERY_RESULTS_PAGE_SIZE;
  const rows = results.rows.slice(start, start + QUERY_RESULTS_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [results]);

  return (
    <div className="grid gap-2">
      <div className="overflow-auto rounded-md border border-slate-200 bg-white" data-testid="query-results-table">
        <table className="min-w-full divide-y divide-slate-200 text-left text-xs">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              {results.columns.map((column) => (
                <th key={column} className="px-3 py-2 font-semibold">{column}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 text-slate-800">
            {rows.map((row, index) => (
              <tr key={start + index}>
                {results.columns.map((column) => (
                  <td key={column} className="max-w-56 break-words px-3 py-2">
                    {String(row[column] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
            {results.rows.length === 0 && (
              <tr>
                <td className="px-3 py-4 text-slate-500" colSpan={Math.max(results.columns.length, 1)}>
                  {t("queryResults.emptyRows")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {results.rows.length > QUERY_RESULTS_PAGE_SIZE && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600" data-testid="query-results-pagination">
          <span>
            {t("queryResults.pageSummary", {
              start: start + 1,
              end: Math.min(start + QUERY_RESULTS_PAGE_SIZE, results.rows.length),
              total: results.rows.length,
            })}
          </span>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              <ChevronLeft size={15} aria-hidden="true" />
              <span>{t("queryResults.prev")}</span>
            </Button>
            <span className="inline-flex min-h-9 items-center rounded-md border border-slate-200 px-3 text-slate-700">
              {t("queryResults.page", { page: currentPage, total: totalPages })}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={currentPage >= totalPages}
              onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
            >
              <span>{t("queryResults.next")}</span>
              <ChevronRight size={15} aria-hidden="true" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function DbAdminExecutionResult({ result }: { result: DbAdminExecuteData }) {
  return (
    <section className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant={result.executed ? "success" : "neutral"} label={result.executed ? "executed" : "dry-run"} />
        <StatusBadge variant="neutral" label={result.runtime} />
        {result.committed && <StatusBadge variant="success" label="committed" />}
        {result.rolled_back && <StatusBadge variant="danger" label="rolled back" />}
      </div>
      {result.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-950">
          {warning}
        </p>
      ))}
      {result.select_result && <QueryResultsTable results={result.select_result} />}
      <div className="grid gap-2">
        {result.statements.map((statement) => (
          <div key={`${statement.index}-${statement.sql}`} className="rounded-md bg-white p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={statement.statement_type} />
                <StatusBadge
                  variant={statement.status === "success" || statement.status === "executed" ? "success" : statement.status === "error" || statement.status === "blocked" ? "danger" : "neutral"}
                  label={statement.status}
                />
              </div>
              <span className="text-xs text-slate-500">{statement.elapsed_ms}ms</span>
            </div>
            <code className="mt-2 block break-words text-xs text-slate-700">{statement.sql}</code>
            {statement.error_message && (
              <p className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800">
                {statement.error_message}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

type FileInputIcon = "file" | "spreadsheet" | "upload";

const fileInputIcons: Record<FileInputIcon, typeof FileText> = {
  file: FileText,
  spreadsheet: FileSpreadsheet,
  upload: Upload,
};

export function FileInputControl({
  label,
  ariaLabel = label,
  accept,
  filename,
  selectedText,
  emptyText,
  pickText,
  replaceText,
  clearText = t("dbAdmin.runner.clear"),
  clearAriaLabel,
  icon = "upload",
  disabled = false,
  clearDisabled,
  className = "",
  dataTestId,
  onPick,
  onClear,
}: {
  label: string;
  ariaLabel?: string;
  accept: string;
  filename: string;
  selectedText?: string;
  emptyText: string;
  pickText: string;
  replaceText?: string;
  clearText?: string;
  clearAriaLabel?: string;
  icon?: FileInputIcon;
  disabled?: boolean;
  clearDisabled?: boolean;
  className?: string;
  dataTestId?: string;
  onPick: (file: File) => void | Promise<void>;
  onClear?: () => void;
}) {
  const inputId = useId();
  const Icon = fileInputIcons[icon];
  const hasFile = Boolean(filename);
  const clearIsDisabled = clearDisabled ?? !hasFile;

  return (
    <div className={`grid min-w-0 gap-1 text-sm font-medium text-slate-800 ${className}`} data-testid={dataTestId}>
      <span>{label}</span>
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-2">
        <label
          htmlFor={inputId}
          className={`group flex h-11 min-w-0 items-center gap-2 rounded-md border bg-white px-3 py-1 text-left transition-colors focus-within:ring-2 focus-within:ring-sky-200 ${
            disabled
              ? "cursor-not-allowed border-slate-200 opacity-60"
              : "cursor-pointer border-slate-300 hover:border-sky-300 hover:bg-sky-50/40"
          }`}
        >
          <span
            className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
              hasFile ? "bg-sky-100 text-sky-700" : "bg-slate-100 text-slate-500 group-hover:text-sky-700"
            }`}
            aria-hidden="true"
          >
            <Icon size={16} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-slate-900">
              {hasFile ? selectedText ?? filename : pickText}
            </span>
          </span>
          <span
            className={`hidden max-w-56 shrink-0 truncate rounded-md border px-2 py-1 text-xs font-semibold sm:inline-block ${
              hasFile ? "border-sky-200 bg-sky-50 text-sky-800" : "border-slate-200 bg-slate-50 text-slate-600"
            }`}
          >
            {hasFile ? replaceText ?? pickText : emptyText}
          </span>
          <input
            id={inputId}
            className="sr-only"
            type="file"
            accept={accept}
            disabled={disabled}
            aria-label={ariaLabel}
            onChange={(event) => {
              const input = event.currentTarget;
              const file = input.files?.[0];
              if (!file) return;
              void Promise.resolve(onPick(file)).finally(() => {
                input.value = "";
              });
            }}
          />
        </label>
        {onClear && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="min-h-11 whitespace-nowrap"
            disabled={clearIsDisabled || disabled}
            aria-label={clearAriaLabel ?? clearText}
            onClick={onClear}
          >
            <X size={15} aria-hidden="true" />
            <span>{clearText}</span>
          </Button>
        )}
      </div>
    </div>
  );
}

/** .sql/.txt ファイルを読み込んで textarea へ流し込むボタン。 */
export function SqlFileInput({
  onLoad,
  resetSignal = 0,
  disabled = false,
}: {
  onLoad: (text: string) => void;
  resetSignal?: number;
  disabled?: boolean;
}) {
  const [filename, setFilename] = useState("");

  useEffect(() => {
    setFilename("");
  }, [resetSignal]);

  return (
    <FileInputControl
      label={t("dbAdmin.runner.filePick")}
      accept=".sql,.txt"
      filename={filename}
      selectedText={filename}
      emptyText={t("dbAdmin.runner.fileHint")}
      pickText={t("dbAdmin.runner.filePickAction")}
      replaceText={t("dbAdmin.runner.fileReplaceAction")}
      icon="file"
      disabled={disabled}
      onPick={(file) => {
        setFilename(file.name);
        void readTextFileSmart(file).then(onLoad);
      }}
    />
  );
}

/** SQL + 実行結果を OCI Enterprise AI で分析するパネル。 */
export function AiAnalysisPanel({
  sql,
  resultText,
  target,
}: {
  sql: string;
  resultText: string;
  target: "table" | "view" | "data" | "comment" | "annotation";
}) {
  const [analysis, setAnalysis] = useState<DbAdminAiAnalysisData | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const analyze = async () => {
    setLoading(true);
    setMessage("");
    try {
      setAnalysis(
        await apiPost<DbAdminAiAnalysisData>("/api/nl2sql/db-admin/analyze-error", {
          sql,
          result_text: resultText,
          target,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dbAdmin.error.analyze"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="grid gap-2 border-t border-slate-200 pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-900">{t("dbAdmin.ai.title")}</p>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          loading={loading}
          disabled={!sql.trim() || !resultText.trim()}
          onClick={() => void analyze()}
        >
          <Sparkles size={15} aria-hidden="true" />
          <span>{t("dbAdmin.ai.analyze")}</span>
        </Button>
      </div>
      {message && (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">
          {message}
        </p>
      )}
      {analysis ? (
        <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
          <StatusBadge
            variant={analysis.source === "oci_enterprise_ai" ? "success" : "neutral"}
            label={analysis.source}
          />
          {analysis.warnings.map((warning) => (
            <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
              {warning}
            </p>
          ))}
          <pre className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-800">{analysis.analysis}</pre>
        </div>
      ) : (
        <p className="text-sm text-slate-500">{t("dbAdmin.ai.empty")}</p>
      )}
    </section>
  );
}

/** 文種 whitelist 付き SQL 実行カード(テーブル/ビュー作成・データ SQL 共用)。 */
export function StatementRunnerCard({
  policy,
  title,
  description,
  target,
  initialSql,
  placeholder,
  templates,
  progress,
  confirmationTitle,
  executeOnly = false,
  framed = true,
  onExecuted,
}: {
  policy: DbAdminStatementPolicy;
  title: string;
  description?: string;
  target: "table" | "view" | "data" | "comment" | "annotation";
  initialSql?: string;
  placeholder?: string;
  templates?: Array<{ label: string; build: () => string }>;
  progress?: (state: { hasSql: boolean; isConfirmed: boolean; canRun: boolean }) => ReactNode;
  confirmationTitle?: string;
  executeOnly?: boolean;
  framed?: boolean;
  onExecuted?: () => void | Promise<void>;
}) {
  const [sql, setSql] = useState("");
  const [execute, setExecute] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [result, setResult] = useState<DbAdminExecuteData | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);

  useEffect(() => {
    if (initialSql !== undefined) setSql(initialSql);
  }, [initialSql]);

  const run = async () => {
    if (!sql.trim()) return;
    const shouldExecute = executeOnly || execute;
    if (shouldExecute && !confirmation.trim()) return;
    setLoading(true);
    setMessage("");
    try {
      const data = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/statements", {
        sql,
        policy,
        execute: shouldExecute,
        confirmation: shouldExecute ? confirmation : "",
        reason: `ui-db-admin-${policy}`,
      });
      setResult(data);
      if (data.executed) {
        await onExecuted?.();
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dbAdmin.error.statements"));
    } finally {
      setLoading(false);
    }
  };

  const shouldExecute = executeOnly || execute;
  const isConfirmed = confirmation.trim() === "ADMIN_EXECUTE";
  const canRun = Boolean(sql.trim()) && (!shouldExecute || isConfirmed);
  const progressNode = progress?.({ hasSql: Boolean(sql.trim()), isConfirmed, canRun });

  const header = executeOnly ? (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 text-base font-semibold text-slate-950">
          <Code2 size={18} aria-hidden="true" />
          {title}
        </h2>
        {description && <p className="mt-1 text-sm text-slate-600">{description}</p>}
      </div>
      <Button
        type="button"
        variant={shouldExecute ? "danger" : "secondary"}
        size="sm"
        className="w-full sm:w-auto"
        loading={loading}
        disabled={!canRun}
        onClick={() => void run()}
      >
        <Play size={15} aria-hidden="true" />
        <span>{shouldExecute ? t("dbAdmin.runner.run") : t("dbAdmin.runner.dryRun")}</span>
      </Button>
    </div>
  ) : (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <CardTitle className="flex items-center gap-2">
        <Code2 size={18} aria-hidden="true" />
        {title}
      </CardTitle>
      <Button
        type="button"
        variant={shouldExecute ? "danger" : "secondary"}
        size="sm"
        loading={loading}
        disabled={!canRun}
        onClick={() => void run()}
      >
        <Play size={15} aria-hidden="true" />
        <span>{shouldExecute ? t("dbAdmin.runner.run") : t("dbAdmin.runner.dryRun")}</span>
      </Button>
    </div>
  );

  const confirmationField = (
    <ExecutionConfirmationField
      value={confirmation}
      onChange={setConfirmation}
      confirmed={isConfirmed}
      placeholder="ADMIN_EXECUTE"
      expectedLabel="ADMIN_EXECUTE"
      helper={shouldExecute ? t("dbAdmin.confirmation.adminHelper") : t("dbAdmin.confirmation.dryRunHelper")}
      disabled={!shouldExecute}
    />
  );

  const content = (
    <>
      {executeOnly && header}
      {progressNode}
      {message && (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">
          {message}
        </p>
      )}
      {templates && templates.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-slate-500">{t("dbAdmin.runner.templates")}</span>
          {templates.map((template) => (
            <Button
              key={template.label}
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setSql(template.build())}
            >
              {template.label}
            </Button>
          ))}
        </div>
      )}
      <label className="grid gap-1 text-sm font-medium text-slate-800">
        <span>{t("dbAdmin.runner.sqlLabel")}</span>
        <textarea
          value={sql}
          onChange={(event) => setSql(event.currentTarget.value)}
          rows={9}
          placeholder={placeholder}
          className="min-h-52 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
        />
      </label>
      {!executeOnly && (
        <p className="text-xs leading-5 text-slate-500">
          {t("dbAdmin.runner.previewHint")}
        </p>
      )}
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <SqlFileInput
          resetSignal={sqlFileResetSignal}
          onLoad={(text) => {
            setSql(text);
            setResult(null);
            setMessage("");
          }}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="min-h-11 sm:self-end"
          disabled={!sql}
          onClick={() => {
            setSql("");
            setResult(null);
            setSqlFileResetSignal((value) => value + 1);
          }}
        >
          <X size={15} aria-hidden="true" />
          <span>{t("dbAdmin.runner.clear")}</span>
        </Button>
      </div>
      <div className={executeOnly ? "grid gap-3" : "grid gap-3 sm:grid-cols-2"}>
        {!executeOnly && (
          <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
            <input
              type="checkbox"
              checked={execute}
              onChange={(event) => setExecute(event.currentTarget.checked)}
              className="mt-1 h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
            />
            <span>{t("dbAdmin.runner.execute")}</span>
          </label>
        )}
        {executeOnly ? (
          <fieldset className="grid gap-3 rounded-md border border-slate-200 bg-white p-3">
            <legend className="px-1 text-sm font-semibold text-slate-900">
              {confirmationTitle ?? t("dbAdmin.runner.confirmation")}
            </legend>
            {confirmationField}
          </fieldset>
        ) : (
          confirmationField
        )}
      </div>
      {result && <DbAdminExecutionResult result={result} />}
      {!executeOnly && <AiAnalysisPanel sql={sql} resultText={executionResultText(result)} target={target} />}
    </>
  );

  if (!framed) {
    return <div className="grid gap-4">{!executeOnly && header}{content}</div>;
  }

  return (
    <Card>
      {!executeOnly && (
        <CardHeader>
          {header}
        </CardHeader>
      )}
      <CardContent className={`grid gap-3 ${executeOnly ? "p-4" : ""}`}>
        {content}
      </CardContent>
    </Card>
  );
}

/** 検索フィルタ付きテーブル/ビュー一覧パネル。 */
export function ObjectListPanel({
  items,
  selectedName,
  onSelect,
  emptyTitle,
  emptyHint,
}: {
  items: DbAdminObjectSummary[];
  selectedName?: string;
  onSelect: (item: DbAdminObjectSummary) => void;
  emptyTitle: string;
  emptyHint: string;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (item) => item.name.toLowerCase().includes(q) || item.comment.toLowerCase().includes(q)
    );
  }, [items, query]);

  return (
    <div className="grid content-start gap-3">
      <label className="grid gap-1 text-sm font-medium text-slate-800">
        <span>{t("dbAdmin.search.label")}</span>
        <span className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
            className="min-h-11 w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
            placeholder={t("dbAdmin.search.placeholder")}
          />
        </span>
      </label>
      <p className="text-xs text-slate-500">
        {t("dbAdmin.search.resultCount", { filtered: filtered.length, total: items.length })}
      </p>
      {filtered.length === 0 ? (
        <EmptyState title={emptyTitle} hint={emptyHint} />
      ) : (
        <div className="grid max-h-[44.5rem] gap-2 overflow-auto pr-1" role="list" data-testid="db-admin-object-list">
          {filtered.map((item) => (
            <button
              key={item.name}
              type="button"
              role="listitem"
              aria-current={item.name === selectedName ? "true" : undefined}
              onClick={() => onSelect(item)}
              className={`min-h-16 cursor-pointer rounded-md border p-3 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-sky-200 ${
                item.name === selectedName
                  ? "border-sky-400 bg-sky-50"
                  : "border-slate-200 bg-white hover:bg-slate-50"
              }`}
            >
              <span className="break-all font-mono text-xs font-semibold text-sky-800">{item.name}</span>
              <span className="mt-1 block text-xs text-slate-600">
                {item.comment.trim() || "-"}
                {item.row_count != null && ` / ${t("dbAdmin.list.rows", { count: item.row_count })}`}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 列情報 + DDL の詳細パネル(サンプル値は catalog から突合)。 */
export function ObjectDetailPanel({
  detail,
  catalog,
  actions,
}: {
  detail: DbAdminObjectDetail | null;
  catalog?: SchemaCatalog | null;
  actions?: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const sampleByColumn = useMemo(() => {
    if (!detail || !catalog) return new Map<string, string>();
    const table = catalog.tables.find(
      (item) => item.table_name.toUpperCase() === detail.name.toUpperCase()
    );
    return new Map(
      (table?.columns ?? []).map((column) => [
        column.column_name.toUpperCase(),
        column.sample_values.join(", "),
      ])
    );
  }, [detail, catalog]);

  if (!detail) {
    return <EmptyState title={t("dbAdmin.detail.emptyTitle")} hint={t("dbAdmin.detail.emptyHint")} />;
  }

  const copyDdl = async () => {
    try {
      await navigator.clipboard.writeText(detail.ddl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard 不可の環境ではダウンロードを使う
    }
  };

  return (
    <section className="grid min-w-0 content-start gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="break-all font-mono text-sm font-semibold text-slate-900">{detail.name}</p>
          <StatusBadge variant="neutral" label={detail.object_type} />
          <StatusBadge variant="neutral" label={t("dbAdmin.detail.columnCount", { count: detail.columns.length })} />
          {detail.row_count != null && (
            <StatusBadge variant="info" label={t("dbAdmin.list.rows", { count: detail.row_count })} />
          )}
        </div>
        {actions}
      </div>
      {detail.comment && <p className="text-sm text-slate-700">{detail.comment}</p>}
      {detail.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {warning}
        </p>
      ))}
      <div>
        <p className="mb-1 text-sm font-semibold text-slate-900">{t("dbAdmin.detail.columns")}</p>
        <div data-testid="db-admin-detail-columns" className="min-w-0 max-w-full overflow-x-auto rounded-md border border-slate-200">
          <table className="w-full min-w-[42rem] table-fixed divide-y divide-slate-200 text-sm">
            <colgroup>
              <col className="w-[18%]" />
              <col className="w-[12%]" />
              <col className="w-[16%]" />
              <col className="w-[10%]" />
              <col />
            </colgroup>
            <thead className="bg-slate-50">
              <tr>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.physical")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.logical")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.type")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.nullable")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.sample")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {detail.columns.map((column) => (
                <tr key={column.column_name}>
                  <td className="px-3 py-2 font-mono text-xs">{column.column_name}</td>
                  <td className="px-3 py-2">{column.logical_name}</td>
                  <td className="px-3 py-2">{column.data_type}</td>
                  <td className="px-3 py-2">{column.nullable ? "YES" : "NO"}</td>
                  <td className="break-words px-3 py-2 font-mono text-xs text-slate-600">
                    {sampleByColumn.get(column.column_name.toUpperCase()) || "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <details className="rounded-md border border-slate-200 bg-slate-50">
        <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-slate-900 marker:text-slate-500 focus:outline-none focus:ring-2 focus:ring-sky-200">
          {t("dbAdmin.detail.ddl")}
          <span className="ml-2 text-xs font-normal text-slate-500">{t("dbAdmin.detail.ddlHint")}</span>
        </summary>
        <div className="grid gap-2 border-t border-slate-200 bg-white p-3">
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm" disabled={!detail.ddl} onClick={() => void copyDdl()}>
              {copied ? t("dbAdmin.detail.copied") : t("dbAdmin.detail.copy")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!detail.ddl}
              onClick={() => downloadText(`${detail.name.toLowerCase()}_ddl.sql`, detail.ddl)}
            >
              <Download size={15} aria-hidden="true" />
              <span>{t("dbAdmin.detail.download")}</span>
            </Button>
          </div>
          <pre className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
            <code>{detail.ddl || "-"}</code>
          </pre>
        </div>
      </details>
    </section>
  );
}
