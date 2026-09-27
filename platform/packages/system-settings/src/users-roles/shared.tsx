// ユーザー管理・ロール管理（と製品の権限管理など）で使う一覧＋詳細レイアウトの部品と補助関数。
// NL2SQL の features/security/SecurityManagementShared.tsx などを移設した（#206）。
import { Children, useState, type ReactNode } from "react";
import { Search, type LucideIcon } from "lucide-react";
import { FixedSplitPane, cn, type FixedSplitWidePane } from "@engchina/production-ready-ui";

import { t } from "./messages";
import type { ApiErrorDetails, ApiFieldProblem, DescribeApiError } from "./types";

// ---- 一覧の表示密度（NL2SQL の lib/list-density.ts と同じ値） ----

/** 共有 DataTable の visibleRows に渡す一覧の表示行数（md 未満 5 行・md 以上 8 行）。 */
export const SECURITY_TABLE_VISIBLE_ROWS = { base: 5, md: 8 } as const;
/** 表の行の最小高さ。1 行セルでも行の高さをそろえる。 */
export const SECURITY_TABLE_ROW_CLASS = "h-[3.5rem]";
export const SECURITY_LIST_SCROLL_CLASS = "max-h-[17.5rem] overflow-auto md:max-h-[28rem]";
export const SECURITY_LIST_FOCUS_CLASS =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";

// ---- ID と表示名 ----

function cleaned(value?: string | null) {
  return value?.trim() ?? "";
}

/** 補助表示する表示名。空、または ID と同じなら重複行を出さないため空文字を返す。 */
export function identitySecondaryName(id: string, name?: string | null) {
  const secondary = cleaned(name);
  return secondary && secondary !== cleaned(id) ? secondary : "";
}

/** ID と表示名を 1 つの文字列で併記するときの表記（例: `data_user（データユーザー）`）。 */
export function identityInlineLabel(id: string, name?: string | null) {
  const secondary = identitySecondaryName(id, name);
  return secondary ? `${id}（${secondary}）` : id;
}

// ---- 選択・render 中の同期 ----

/** 値が前回のレンダーから変わったか（初回は true）。effect を使わず render 中で state を直すため。 */
export function useValuesChanged(values: readonly unknown[]): boolean {
  const [previous, setPrevious] = useState<readonly unknown[] | null>(null);
  const changed =
    previous === null ||
    previous.length !== values.length ||
    values.some((value, index) => !Object.is(value, previous[index]));
  if (changed) setPrevious(values);
  return changed;
}

/** 表示中の行から選択を決める。選択行が見えていればそのまま、なければ先頭行。 */
export function selectedVisibleKey<T, K extends string | number>(
  items: readonly T[],
  selectedKey: K | null | undefined,
  getKey: (item: T) => K,
  options: { preserveSelected?: boolean } = {},
) {
  const preserveSelected = options.preserveSelected ?? true;
  const keys = items.map(getKey);
  if (preserveSelected && selectedKey != null && keys.includes(selectedKey)) return selectedKey;
  return keys[0] ?? null;
}

export function isAbortError(cause: unknown): boolean {
  return cause instanceof Error && cause.name === "AbortError";
}

// ---- API の入力エラー ----

export type FieldErrorMap<FieldName extends string> = Partial<Record<FieldName, string>>;

export function withoutFieldError<FieldName extends string>(
  errors: FieldErrorMap<FieldName>,
  field: FieldName,
): FieldErrorMap<FieldName> {
  if (!errors[field]) return errors;
  const next = { ...errors };
  delete next[field];
  return next;
}

/** JSON Pointer を各画面が宣言した field 名へ結び付ける。表示文言の解析はしない。 */
export function mapFieldErrors<FieldName extends string>(
  details: ApiErrorDetails | undefined,
  pointerToField: Readonly<Record<string, FieldName>>,
  messageFor?: (problem: ApiFieldProblem, details: ApiErrorDetails) => string,
): FieldErrorMap<FieldName> {
  const mapped: FieldErrorMap<FieldName> = {};
  if (!details) return mapped;
  for (const problem of details.fieldErrors ?? []) {
    const fieldName = pointerToField[problem.pointer];
    if (!fieldName) continue;
    mapped[fieldName] = messageFor?.(problem, details) ?? problem.message;
  }
  return mapped;
}

/** すべての問題が field に結び付いた場合は、重複する FormStatus を出さない。 */
export function unmappedErrorMessage<FieldName extends string>(
  cause: unknown,
  details: ApiErrorDetails | undefined,
  pointerToField: Readonly<Record<string, FieldName>>,
  fallback: string,
): string {
  const fieldErrors = details?.fieldErrors ?? [];
  if (fieldErrors.length > 0 && fieldErrors.every((problem) => pointerToField[problem.pointer])) {
    return "";
  }
  return errorMessage(cause, details, fallback);
}

export function errorMessage(
  cause: unknown,
  details: ApiErrorDetails | undefined,
  fallback: string,
): string {
  if (details?.message?.trim()) return details.message;
  return cause instanceof Error && cause.message.trim() ? cause.message : fallback;
}

/** 製品が describeError を渡さない場合の既定（Error の message だけを使う）。 */
export const describeErrorMessageOnly: DescribeApiError = (error) =>
  error instanceof Error ? { message: error.message } : undefined;

// ---- レイアウト部品 ----

export function SecurityManagementPanelShell({
  id,
  labelledBy,
  ariaLabel,
  idPrefix,
  className = "",
  splitId,
  splitStoragePrefix,
  preferredWidePane = "right",
  children,
}: {
  id: string;
  labelledBy?: string;
  ariaLabel?: string;
  idPrefix: string;
  className?: string;
  splitId?: string;
  /** 分割比率を保存する localStorage key の前置き（製品ごと）。 */
  splitStoragePrefix?: string;
  preferredWidePane?: FixedSplitWidePane;
  children: ReactNode;
}) {
  const panelChildren = Children.toArray(children);
  const splitPaneId = splitId && panelChildren.length === 2 ? splitId : null;

  return (
    <section
      id={id}
      role="region"
      aria-labelledby={labelledBy}
      aria-label={ariaLabel}
      className={cn("grid gap-4 rounded-md border border-border bg-surface p-4 shadow-sm", className)}
      data-testid="security-management-panel-shell"
      data-management-id={idPrefix}
    >
      {splitPaneId ? (
        <FixedSplitPane
          storagePrefix={splitStoragePrefix}
          splitId={splitPaneId}
          preferredWidePane={preferredWidePane}
          left={panelChildren[0]}
          right={panelChildren[1]}
        />
      ) : (
        children
      )}
    </section>
  );
}

export function SecurityPanelHeader({
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
        <h2 id={headingId} className="flex items-center gap-2 text-base font-semibold text-fg">
          <Icon size={20} aria-hidden="true" />
          <span className="min-w-0 break-words">{title}</span>
        </h2>
        {description ? <p className="mt-1 text-sm leading-6 text-fg-muted">{description}</p> : null}
      </div>
      {action ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">{action}</div>
      ) : null}
    </div>
  );
}

export interface SecurityManagementMetric {
  label: string;
  value: string;
  emphasis?: boolean;
  testId?: string;
}

export function SecurityManagementStatusBar({
  ariaLabel,
  metrics,
  actions,
}: {
  ariaLabel: string;
  metrics: SecurityManagementMetric[];
  actions?: ReactNode;
}) {
  return (
    <section
      className="rounded-md border border-border bg-surface px-4 py-3 shadow-sm"
      aria-label={ariaLabel}
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <dl className="grid gap-3 sm:grid-cols-3 xl:flex xl:flex-wrap xl:items-center">
          {metrics.map((metric) => (
            <div
              key={`${metric.label}-${metric.value}`}
              className="rounded-md border border-border bg-surface-sunken px-3 py-2"
            >
              <dt className="text-xs font-medium text-fg-muted">{metric.label}</dt>
              <dd
                className={cn("mt-1 font-semibold tabular-nums text-fg", metric.emphasis && "text-lg")}
                data-testid={metric.testId}
              >
                {metric.value}
              </dd>
            </div>
          ))}
        </dl>
        {actions ? (
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">{actions}</div>
        ) : null}
      </div>
    </section>
  );
}

export function SecuritySearchField({
  label,
  placeholder,
  value,
  testId,
  disabled = false,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string;
  testId?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-fg">
      <span>{label}</span>
      <span className="relative">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
          aria-hidden="true"
        />
        <input
          type="search"
          value={value}
          data-testid={testId}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
          className="min-h-11 w-full rounded-md border border-border-control bg-surface py-2 pl-9 pr-3 outline-none focus:border-focus-ring focus:ring-2 focus:ring-focus-ring disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-fg-disabled"
          placeholder={placeholder}
        />
      </span>
    </label>
  );
}

export function SecurityDetailField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2">
      <dt className="text-xs font-medium text-fg-muted">{label}</dt>
      <dd className="mt-1 min-w-0 break-words text-sm font-medium text-fg">{children}</dd>
    </div>
  );
}

/**
 * ID/コードと表示名の 2 段表示。1 行目 = ID（主表示・mono）、2 行目 = 表示名（muted）。
 * 文字色は親（通常 `text-fg`、選択行は `text-accent-fg`）を継承する。
 */
export function SecurityIdentityLines({
  id,
  name,
  className,
}: {
  id: string;
  name?: string | null;
  className?: string;
}) {
  const secondary = identitySecondaryName(id, name);
  return (
    <>
      <span className={cn("block break-all font-mono font-medium", className)}>{id}</span>
      {secondary ? (
        <span className="block break-words text-xs text-fg-muted">{secondary}</span>
      ) : null}
    </>
  );
}

export function SecurityEmptySelection({ title, hint }: { title: string; hint: string }) {
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-surface-sunken p-4">
      <div className="py-10 text-center">
        <p className="text-sm font-semibold text-fg">{title}</p>
        <p className="mt-1 text-sm leading-6 text-fg-muted">{hint}</p>
      </div>
    </section>
  );
}

export function securityFilteredCount(filtered: number, total: number) {
  return t("security.common.filteredCount", { filtered, total });
}
