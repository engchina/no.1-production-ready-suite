import { useEffect, useId, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  Code2,
  Download,
  FileSpreadsheet,
  FileText,
  Play,
  Search,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DEFAULT_PAGE_SIZE,
  DataTable,
  EmptyState,
  Pagination,
  StatusBadge,
  usePagination,
} from "@engchina/production-ready-ui";

import { apiPost } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { t } from "@/lib/i18n";
import type {
  DbAdminExecuteData,
  DbAdminObjectDetail,
  DbAdminObjectSummary,
  DbAdminStatementResult,
  DbAdminStatementPolicy,
  QueryResults,
  SchemaCatalog,
} from "../types";

/** テキストファイルを SQL としてダウンロードする。 */
export function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/sql;charset=utf-8" });
  downloadBlob(filename, blob);
}

export { downloadBlob } from "@/lib/download";

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

export function PageMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-foreground">{value}</p>
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
      ? "border-danger/30 bg-danger-bg/70 text-danger marker:text-danger"
      : "border-border bg-card text-foreground marker:text-muted";

  return (
    <details className={`rounded-md border ${toneClass}`}>
      <summary className="cursor-pointer px-4 py-3 focus:outline-none focus:ring-2 focus:ring-ring/40">
        <span className="font-semibold">{title}</span>
        <span className="mt-1 block text-sm font-normal text-muted">{description}</span>
      </summary>
      <div className="border-t border-current/10 bg-card p-3">{children}</div>
    </details>
  );
}

export function focusManagementTabElement(id: string) {
  window.requestAnimationFrame(() => document.getElementById(id)?.focus());
}

export function ManagementPanelShell({
  id,
  labelledBy,
  ariaLabel,
  className = "",
  children,
}: {
  id: string;
  labelledBy: string;
  ariaLabel?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      role="tabpanel"
      aria-labelledby={labelledBy}
      aria-label={ariaLabel}
      className={`grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm ${className}`}
      data-testid="management-panel-shell"
    >
      {children}
    </section>
  );
}

export function ManagementPanelHeader({
  title,
  description,
  icon: Icon,
  headingId,
  action,
}: {
  title: string;
  description?: string;
  icon: LucideIcon;
  headingId?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h2 id={headingId} className="flex items-center gap-2 text-base font-semibold text-foreground">
          <Icon size={18} aria-hidden="true" />
          {title}
        </h2>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {action && <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">{action}</div>}
    </div>
  );
}

export function StepIndicator({
  steps,
  activeIndex,
  ariaLabel,
  dataTestId,
}: {
  steps: string[];
  activeIndex: number;
  ariaLabel: string;
  dataTestId?: string;
}) {
  return (
    <ol className="grid gap-2 md:grid-cols-2" aria-label={ariaLabel} data-testid={dataTestId}>
      {steps.map((label, index) => (
        <li
          key={label}
          className={`rounded-md border px-3 py-2 text-sm font-semibold ${
            index <= activeIndex ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-card text-muted"
          }`}
        >
          {index + 1}. {label}
        </li>
      ))}
    </ol>
  );
}

export function ManagementTabs<TView extends string>({
  activeView,
  tabs,
  idPrefix,
  ariaLabel,
  onViewChange,
}: {
  activeView: TView;
  tabs: Array<{ id: TView; label: string; icon: LucideIcon }>;
  idPrefix: string;
  ariaLabel: string;
  onViewChange: (view: TView) => void;
}) {
  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keyMap: Record<string, number | undefined> = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    };
    const nextIndex = keyMap[event.key];
    if (nextIndex === undefined) return;
    event.preventDefault();
    const nextView = tabs[nextIndex];
    onViewChange(nextView.id);
    focusManagementTabElement(`${idPrefix}-tab-${nextView.id}`);
  };

  // 下線タブ(管理コンソールの定石)へ統一。DbObjectManagementTabs / 詳細タブと同一様式。
  return (
    <div className="overflow-x-auto border-b border-border" role="tablist" aria-label={ariaLabel}>
      <div className="flex min-w-max gap-1">
        {tabs.map((view, index) => {
          const Icon = view.icon;
          const selected = activeView === view.id;
          return (
            <button
              key={view.id}
              id={`${idPrefix}-tab-${view.id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${idPrefix}-panel-${view.id}`}
              className={`group inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-4 text-sm font-semibold transition-colors focus:outline-none focus-visible:bg-primary/10 focus-visible:shadow-[inset_0_-3px_0_0_var(--primary)] ${
                selected
                  ? "border-primary bg-card text-primary"
                  : "border-transparent text-muted hover:border-border hover:bg-card hover:text-foreground"
              }`}
              onClick={() => onViewChange(view.id)}
              onKeyDown={(event) => handleKeyDown(event, index)}
            >
              <Icon
                size={15}
                aria-hidden="true"
                className={selected ? "text-primary" : "text-muted group-hover:text-muted"}
              />
              <span>{view.label}</span>
            </button>
          );
        })}
      </div>
    </div>
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
  actions,
}: {
  value: string;
  onChange: (value: string) => void;
  confirmed: boolean;
  placeholder: string;
  expectedLabel: string;
  helper: string;
  tone?: "neutral" | "danger";
  disabled?: boolean;
  /** 確認語入力の直下に描画する実行/キャンセル等のアクションバー。primary/danger → secondary の順で渡す。 */
  actions?: ReactNode;
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
    isDanger ? "border-danger/30 bg-danger-bg/70" : "border-border bg-background",
  ].join(" ");
  const inputClass = [
    "h-11 w-full rounded-md border bg-card px-3 py-2 text-sm outline-none transition-colors placeholder:text-muted disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted",
    isDanger
      ? "border-danger/30 focus:border-danger focus:ring-2 focus:ring-danger/40"
      : "border-border focus:border-primary focus:ring-2 focus:ring-ring/40",
  ].join(" ");
  const statusClass = [
    "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 text-xs font-semibold",
    confirmed
      ? "border-success/30 bg-success-bg text-success"
      : value.trim()
        ? "border-danger/30 bg-danger-bg text-danger"
        : "border-border bg-card text-muted",
  ].join(" ");

  return (
    <div className={containerClass} data-testid="execution-confirmation-field">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <label htmlFor={id} className={`text-sm font-semibold ${isDanger ? "text-danger" : "text-foreground"}`}>
          {t("dbAdmin.confirmation.label")}
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <span className="max-w-full break-all rounded-md bg-card px-2 py-1 font-mono text-xs text-foreground">
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
      <p id={helperId} className={`text-xs leading-5 ${isDanger ? "text-danger" : "text-muted"}`}>
        {helper}
      </p>
      {actions && (
        <div
          className={`flex flex-col gap-2 border-t pt-3 sm:flex-row sm:flex-wrap sm:items-center ${
            isDanger ? "border-danger/20" : "border-border"
          }`}
        >
          {actions}
        </div>
      )}
    </div>
  );
}

export function QueryResultsTable({ results }: { results: QueryResults }) {
  const { page, setPage, totalPages, pageItems, range } = usePagination(results.rows, DEFAULT_PAGE_SIZE);

  return (
    <div className="grid gap-2">
      <DataTable
        testId="query-results-table"
        columns={results.columns.map((column) => ({
          key: column,
          header: column,
          className: "max-w-56",
          render: (row: Record<string, unknown>) => String(row[column] ?? ""),
        }))}
        rows={pageItems}
        getRowKey={(_, index) => (range.start === 0 ? 0 : range.start - 1) + index}
        empty={t("queryResults.emptyRows")}
      />
      <Pagination
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        summary={t("queryResults.pageSummary", { start: range.start, end: range.end, total: range.total })}
        pageIndicator={t("queryResults.page", { page, total: totalPages })}
        prevLabel={t("queryResults.prev")}
        nextLabel={t("queryResults.next")}
        testId="query-results-pagination"
      />
    </div>
  );
}

function runtimeLabel(runtime: string) {
  if (runtime === "oracle") return "Oracle";
  if (runtime === "deterministic") return t("dbAdmin.result.runtime.deterministic");
  return runtime;
}

type ResultStatusVariant = "success" | "neutral" | "danger" | "info";

function statusVariant(status: string): ResultStatusVariant {
  if (["success", "executed", "applied_to_local_state", "submitted"].includes(status)) return "success";
  if (["error", "blocked"].includes(status)) return "danger";
  if (["confirmation_required", "requires_oracle"].includes(status)) return "info";
  return "neutral";
}

function statusLabel(status: string) {
  const key = `dbAdmin.result.status.${status}`;
  const label = t(key);
  return label === key ? status : label;
}

function resultSummary(result: DbAdminExecuteData): { variant: ResultStatusVariant; label: string } {
  const statuses = result.statements.map((statement) => statement.status);
  if (result.executed) return { variant: "success", label: t("dbAdmin.result.summary.executed") };
  if (statuses.includes("error")) return { variant: "danger", label: t("dbAdmin.result.summary.error") };
  if (statuses.includes("blocked")) return { variant: "danger", label: t("dbAdmin.result.summary.blocked") };
  if (statuses.includes("confirmation_required")) return { variant: "info", label: t("dbAdmin.result.summary.confirmation") };
  if (statuses.includes("requires_oracle")) return { variant: "info", label: t("dbAdmin.result.summary.requiresOracle") };
  return { variant: "neutral", label: t("dbAdmin.result.summary.notExecuted") };
}

function oracleErrorGuidance(code: string | null) {
  if (code === "ORA-00922") {
    return {
      cause: t("dbAdmin.result.error.ora00922.cause"),
      actions: [
        t("dbAdmin.result.error.ora00922.action.split"),
        t("dbAdmin.result.error.ora00922.action.syntax"),
        t("dbAdmin.result.error.action.refresh"),
      ],
    };
  }
  if (code === "ORA-00955") {
    return {
      cause: t("dbAdmin.result.error.ora00955.cause"),
      actions: [
        t("dbAdmin.result.error.ora00955.action.delete"),
        t("dbAdmin.result.error.ora00955.action.refresh"),
      ],
    };
  }
  if (code === "ORA-00942") {
    return {
      cause: t("dbAdmin.result.error.ora00942.cause"),
      actions: [
        t("dbAdmin.result.error.ora00942.action.target"),
        t("dbAdmin.result.error.action.refresh"),
      ],
    };
  }
  if (code === "ORA-11548") {
    return {
      cause: t("dbAdmin.result.error.ora11548.cause"),
      actions: [
        t("dbAdmin.result.error.ora11548.action.name"),
        t("dbAdmin.result.error.ora11548.action.quote"),
        t("dbAdmin.result.error.ora11548.action.regenerate"),
      ],
    };
  }
  return {
    cause: t("dbAdmin.result.error.generic.cause"),
    actions: [
      t("dbAdmin.result.error.generic.action.sql"),
      t("dbAdmin.result.error.action.refresh"),
    ],
  };
}

function parseDbAdminError(message: string) {
  const helpUrl = message.match(/https?:\/\/\S+/)?.[0] ?? "";
  const summary = message.replace(/\s*Help:\s*https?:\/\/\S+/i, "").trim();
  const code = summary.match(/\bORA-\d{5}\b/)?.[0] ?? null;
  const guidance = oracleErrorGuidance(code);
  return { code, summary: summary || message, helpUrl, ...guidance };
}

function DbAdminStatementError({ statement }: { statement: DbAdminStatementResult }) {
  const error = parseDbAdminError(statement.error_message);
  return (
    <div className="mt-3 grid gap-3 rounded-md border border-danger/30 bg-danger-bg p-3 text-danger" role="alert">
      <div className="grid gap-1">
        <p className="text-xs font-semibold text-danger">{t("dbAdmin.result.error.summary")}</p>
        <p className="break-words text-sm font-semibold">{error.summary}</p>
      </div>
      <div className="grid gap-1">
        <p className="text-xs font-semibold text-danger">{t("dbAdmin.result.error.cause")}</p>
        <p className="text-sm leading-6">{error.cause}</p>
      </div>
      <div className="grid gap-1">
        <p className="text-xs font-semibold text-danger">{t("dbAdmin.result.error.nextAction")}</p>
        <ul className="grid gap-1 text-sm leading-6">
          {error.actions.map((action) => (
            <li key={action} className="flex gap-2">
              <span aria-hidden="true">-</span>
              <span>{action}</span>
            </li>
          ))}
        </ul>
      </div>
      <details className="rounded-md border border-danger/30 bg-card/70">
        <summary className="cursor-pointer px-3 py-2 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-danger/40">
          {t("dbAdmin.result.error.detail")}
        </summary>
        <div className="grid gap-2 border-t border-danger/20 p-3">
          {error.helpUrl && (
            <a className="text-sm font-semibold text-danger underline" href={error.helpUrl} target="_blank" rel="noreferrer">
              {t("dbAdmin.result.error.help")}
            </a>
          )}
          <code className="block break-words text-sm leading-6 text-danger">{statement.error_message}</code>
        </div>
      </details>
    </div>
  );
}

export function DbAdminExecutionResult({ result }: { result: DbAdminExecuteData }) {
  const summary = resultSummary(result);
  return (
    <section className="grid gap-2 rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant={summary.variant} label={summary.label} />
        <StatusBadge variant="neutral" label={runtimeLabel(result.runtime)} />
        {result.committed && <StatusBadge variant="success" label={t("dbAdmin.result.summary.committed")} />}
        {result.rolled_back && <StatusBadge variant="danger" label={t("dbAdmin.result.summary.rolledBack")} />}
      </div>
      {result.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
          {warning}
        </p>
      ))}
      {result.select_result && <QueryResultsTable results={result.select_result} />}
      <div className="grid max-h-[32rem] gap-2 overflow-y-auto">
        {result.statements.map((statement) => (
          <div key={`${statement.index}-${statement.sql}`} className="rounded-md bg-card p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={statement.statement_type} />
                <StatusBadge variant={statusVariant(statement.status)} label={statusLabel(statement.status)} />
              </div>
              <span className="text-xs text-muted">{statement.elapsed_ms}ms</span>
            </div>
            <code className="mt-2 block break-words text-xs text-foreground">{statement.sql}</code>
            {statement.error_message && <DbAdminStatementError statement={statement} />}
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
    <div className={`grid min-w-0 gap-1 text-sm font-medium text-foreground ${className}`} data-testid={dataTestId}>
      <span>{label}</span>
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-2">
        <label
          htmlFor={inputId}
          className={`group flex h-11 min-w-0 items-center gap-2 rounded-md border bg-card px-3 py-1 text-left transition-colors focus-within:ring-2 focus-within:ring-ring/40 ${
            disabled
              ? "cursor-not-allowed border-border opacity-60"
              : "cursor-pointer border-border hover:border-primary/40 hover:bg-primary/10"
          }`}
        >
          <span
            className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
              hasFile ? "bg-primary/10 text-primary" : "bg-muted/30 text-muted group-hover:text-primary"
            }`}
            aria-hidden="true"
          >
            <Icon size={16} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-foreground">
              {hasFile ? selectedText ?? filename : pickText}
            </span>
          </span>
          <span
            className={`hidden max-w-56 shrink-0 truncate rounded-md border px-2 py-1 text-xs font-semibold sm:inline-block ${
              hasFile ? "border-primary/30 bg-primary/10 text-primary" : "border-border bg-background text-muted"
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

export function SelectionListPanel({
  title,
  selectedCountLabel,
  items,
  selectedItems,
  emptyTitle,
  emptyHint,
  ariaLabel,
  dataTestId,
  onToggle,
}: {
  title: string;
  selectedCountLabel: string;
  items: string[];
  selectedItems: string[];
  emptyTitle: string;
  emptyHint: string;
  ariaLabel: string;
  dataTestId: string;
  onToggle: (name: string) => void;
}) {
  const selectedSet = useMemo(() => new Set(selectedItems), [selectedItems]);

  return (
    <section className="grid min-w-0 gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <StatusBadge
          variant={selectedItems.length > 0 ? "info" : "neutral"}
          label={selectedCountLabel}
        />
      </div>
      <div
        role="group"
        aria-label={ariaLabel}
        data-testid={dataTestId}
        className="grid h-[28rem] overflow-y-auto rounded-md border border-border bg-card"
      >
        {items.length === 0 ? (
          <div className="grid min-h-full place-items-center p-4">
            <EmptyState title={emptyTitle} hint={emptyHint} />
          </div>
        ) : (
          <div className="grid content-start gap-2 p-2 md:grid-cols-2">
            {items.map((name) => {
              const selected = selectedSet.has(name);
              return (
                <label
                  key={name}
                  className={`flex min-h-11 min-w-0 cursor-pointer items-center gap-2 rounded-md border p-3 text-sm transition-colors ${
                    selected
                      ? "border-primary/30 bg-primary/10 text-primary"
                      : "border-border text-foreground hover:border-primary/30 hover:bg-primary/10"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => onToggle(name)}
                    className="h-4 w-4 shrink-0 rounded border-border text-primary focus:ring-ring/40"
                  />
                  <span className="min-w-0 break-all font-mono text-xs font-semibold text-foreground">
                    {name}
                  </span>
                </label>
              );
            })}
          </div>
        )}
      </div>
    </section>
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

/** 文種 whitelist 付き SQL 実行カード(テーブル/ビュー作成・データ SQL 共用)。 */
export function StatementRunnerCard({
  policy,
  title,
  description,
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
    if (!confirmation.trim()) return;
    setLoading(true);
    setMessage("");
    try {
      const data = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/statements", {
        sql,
        policy,
        confirmation,
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

  const isConfirmed = confirmation.trim() === "ADMIN_EXECUTE";
  const canRun = Boolean(sql.trim()) && isConfirmed;
  const progressNode = progress?.({ hasSql: Boolean(sql.trim()), isConfirmed, canRun });

  const header = executeOnly ? (
    <div className="min-w-0">
      <h2 className="flex items-center gap-2 text-base font-semibold text-foreground">
        <Code2 size={18} aria-hidden="true" />
        {title}
      </h2>
      {description && <p className="mt-1 text-sm text-muted">{description}</p>}
    </div>
  ) : (
    <CardTitle className="flex items-center gap-2">
      <Code2 size={18} aria-hidden="true" />
      {title}
    </CardTitle>
  );

  const runButton = (
    <Button
      type="button"
      variant="danger"
      size="sm"
      className="w-full sm:w-auto"
      loading={loading}
      disabled={!canRun}
      onClick={() => void run()}
    >
      <Play size={15} aria-hidden="true" />
      <span>{t("dbAdmin.runner.run")}</span>
    </Button>
  );

  const confirmationField = (
    <ExecutionConfirmationField
      value={confirmation}
      onChange={setConfirmation}
      confirmed={isConfirmed}
      placeholder="ADMIN_EXECUTE"
      expectedLabel="ADMIN_EXECUTE"
      helper={t("dbAdmin.confirmation.adminHelper")}
      actions={runButton}
    />
  );

  const content = (
    <>
      {executeOnly && header}
      {progressNode}
      {message && (
        <p className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger" role="alert">
          {message}
        </p>
      )}
      {templates && templates.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted">{t("dbAdmin.runner.templates")}</span>
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
      <label className="grid gap-1 text-sm font-medium text-foreground">
        <span>{t("dbAdmin.runner.sqlLabel")}</span>
        <textarea
          value={sql}
          onChange={(event) => setSql(event.currentTarget.value)}
          rows={9}
          placeholder={placeholder}
          className="min-h-52 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 focus:border-primary focus:ring-2 focus:ring-ring/40"
        />
      </label>
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
      <div className="grid gap-3">
        {executeOnly ? (
          <fieldset className="grid gap-3 rounded-md border border-border bg-card p-3">
            <legend className="px-1 text-sm font-semibold text-foreground">
              {confirmationTitle ?? t("dbAdmin.runner.confirmation")}
            </legend>
            {confirmationField}
          </fieldset>
        ) : (
          confirmationField
        )}
      </div>
      {result && <DbAdminExecutionResult result={result} />}
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
      <label className="grid gap-1 text-sm font-medium text-foreground">
        <span>{t("dbAdmin.search.label")}</span>
        <span className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
            className="min-h-11 w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
            placeholder={t("dbAdmin.search.placeholder")}
          />
        </span>
      </label>
      <p className="text-xs text-muted">
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
              className={`min-h-16 cursor-pointer rounded-md border p-3 text-left text-sm transition focus:outline-none focus:ring-2 focus:ring-ring/40 ${
                item.name === selectedName
                  ? "border-primary bg-primary/10"
                  : "border-border bg-card hover:bg-background"
              }`}
            >
              <span className="break-all font-mono text-xs font-semibold text-primary">{item.name}</span>
              <span className="mt-1 block text-xs text-muted">
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
          <p className="break-all font-mono text-sm font-semibold text-foreground">{detail.name}</p>
          <StatusBadge variant="neutral" label={detail.object_type} />
          <StatusBadge variant="neutral" label={t("dbAdmin.detail.columnCount", { count: detail.columns.length })} />
          {detail.row_count != null && (
            <StatusBadge variant="info" label={t("dbAdmin.list.rows", { count: detail.row_count })} />
          )}
        </div>
        {actions}
      </div>
      {detail.comment && <p className="text-sm text-foreground">{detail.comment}</p>}
      {detail.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
          {warning}
        </p>
      ))}
      <div>
        <p className="mb-1 text-sm font-semibold text-foreground">{t("dbAdmin.detail.columns")}</p>
        <div data-testid="db-admin-detail-columns" className="min-w-0 max-w-full overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[42rem] table-fixed divide-y divide-border text-sm">
            <colgroup>
              <col className="w-[18%]" />
              <col className="w-[12%]" />
              <col className="w-[16%]" />
              <col className="w-[10%]" />
              <col />
            </colgroup>
            <thead className="bg-background">
              <tr>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.physical")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.logical")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.type")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.nullable")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.sample")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {detail.columns.map((column) => (
                <tr key={column.column_name}>
                  <td className="px-3 py-2 font-mono text-xs">{column.column_name}</td>
                  <td className="px-3 py-2">{column.logical_name}</td>
                  <td className="px-3 py-2">{column.data_type}</td>
                  <td className="px-3 py-2">{column.nullable ? "YES" : "NO"}</td>
                  <td className="break-words px-3 py-2 font-mono text-xs text-muted">
                    {sampleByColumn.get(column.column_name.toUpperCase()) || "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <details className="rounded-md border border-border bg-background">
        <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-foreground marker:text-muted focus:outline-none focus:ring-2 focus:ring-ring/40">
          {t("dbAdmin.detail.ddl")}
          <span className="ml-2 text-xs font-normal text-muted">{t("dbAdmin.detail.ddlHint")}</span>
        </summary>
        <div className="grid gap-2 border-t border-border bg-card p-3">
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
          <pre className="max-h-72 overflow-auto rounded-md border border-border bg-code p-3 text-sm leading-6 text-code-fg">
            <code>{detail.ddl || "-"}</code>
          </pre>
        </div>
      </details>
    </section>
  );
}
