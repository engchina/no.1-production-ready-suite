// ユーザー管理・ロール管理（と製品の権限管理など）で使う一覧＋詳細レイアウトの部品と補助関数。
// NL2SQL の features/security/SecurityManagementShared.tsx などを移設した（#206）。
import { Children, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";
import {
  ClearActionButton,
  FixedSplitPane,
  RowTitleButton,
  SearchField,
  cn,
  type FixedSplitWidePane,
  type RowTitleButtonProps,
} from "@engchina/production-ready-ui";

import { t } from "./messages";
import type { ApiErrorDetails, ApiFieldProblem, DescribeApiError } from "./types";

// 一覧の表示密度（表示行数・行の高さ・スクロール）は @engchina/production-ready-ui の INFORMATION_* を使う（#265 で一本化）。

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

/**
 * 送信のエラーの後に、最初の不正な欄へフォーカスを移す（WCAG の focus management）。
 * 送信中は欄が disabled のため、requestAnimationFrame で移すと、送信の終わり（busy = false）の commit より先に
 * focus() が走り、disabled の欄への focus が無視されることがあった（#424）。
 * 移す処理を予約し、欄が操作できる状態（ready）を commit した後の effect で実行する。
 */
export function useFocusAfterCommit(ready: boolean): (focus: () => void) => void {
  const pending = useRef<(() => void) | null>(null);
  // deps なし: 予約の後のどの commit でも、操作できる状態になっていれば一度だけ実行する。
  useEffect(() => {
    if (!ready || !pending.current) return;
    const focus = pending.current;
    pending.current = null;
    focus();
  });
  return (focus) => {
    pending.current = focus;
  };
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
  resultCountLabel,
  onChange,
}: {
  label: string;
  placeholder: string;
  /** 適用中の検索語。 */
  value: string;
  testId?: string;
  disabled?: boolean;
  /** 絞り込んだ件数の文言（検索語があるときだけ読み上げる）。 */
  resultCountLabel?: string;
  /** 検索語が確定したとき（入力が止まって 300ms・Enter・消去。IME の変換中は呼ばない）。 */
  onChange: (value: string) => void;
}) {
  const id = useId();
  // 一覧の絞り込みは共有の SearchField（debounce・IME 対応・消去・件数の読み上げ）で作る（#535）。
  return (
    <SearchField
      id={`security-search-${id}`}
      label={label}
      value={value}
      data-testid={testId}
      disabled={disabled}
      onSearch={onChange}
      clearLabel={t("security.common.clearSearch")}
      resultCountLabel={resultCountLabel}
      placeholder={placeholder}
      className="min-w-0"
    />
  );
}

/** 絞り込みの結果が 0 件のときの「検索語をクリア」（空の状態の action に置く。#535）。 */
export function SecurityClearSearchAction({ onClear }: { onClear: () => void }) {
  return <ClearActionButton label={t("security.common.clearSearch")} onClick={onClear} />;
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
 * 一覧の行の題名のボタン（共有の `RowTitleButton`、#421）に、ID と表示名の 2 段表示を載せたもの。
 * 1 行目 = ID（主表示・mono）、2 行目 = 表示名（muted。ID と同じなら出さない）。選択の見た目は行が持つ。
 */
export function SecurityIdentityRowTitleButton({
  id,
  name,
  ...props
}: { id: string; name?: string | null } & Omit<RowTitleButtonProps, "title" | "subtitle">) {
  return (
    <RowTitleButton
      {...props}
      title={<span className="break-all font-mono">{id}</span>}
      subtitle={identitySecondaryName(id, name) || undefined}
    />
  );
}

/**
 * ID/コードと表示名の 2 段表示。1 行目 = ID（主表示・mono）、2 行目 = 表示名（muted）。
 * 文字色は親を継承する。一覧の行の題名には `SecurityIdentityRowTitleButton` を使う。
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

/** 件数は桁区切りで出す（権限の対象は数千件になりうる。#608）。 */
const formatCount = (value: number) => value.toLocaleString("ja-JP");

export function securityFilteredCount(filtered: number, total: number) {
  return t("security.common.filteredCount", { filtered: formatCount(filtered), total: formatCount(total) });
}

/** 候補の一覧（ListPicker）のフッターの件数（表示中 / 全件と選択数。#600）。 */
export function securityFilteredCountWithSelected(filtered: number, total: number, selected: number) {
  return t("security.common.filteredCountWithSelected", {
    filtered: formatCount(filtered),
    total: formatCount(total),
    selected: formatCount(selected),
  });
}
