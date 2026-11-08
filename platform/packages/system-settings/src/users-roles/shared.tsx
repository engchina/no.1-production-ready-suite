// ユーザー管理・ロール管理（と製品の権限管理など）で使う一覧 → エディタ（A 型。#4635）の部品と補助関数。
// NL2SQL の features/security/SecurityManagementShared.tsx などを移設した（#206）。
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, type LucideIcon } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Button,
  Card,
  ClearActionButton,
  EmptyState,
  PAGE_SIZE_OPTIONS,
  Pagination,
  RowTitleButton,
  SearchField,
  FormSkeleton,
  TimedLoadingState,
  cn,
  usePagination,
  useUrlSelection,
  type RowTitleButtonProps,
  type UrlSelection,
} from "@production-ready/ui";

import { usePersistedListPaging, type ListPagingKey } from "../lists/listPaging";
import { t } from "./messages";
import type { ApiErrorDetails, ApiFieldProblem, DescribeApiError } from "./types";

// 一覧の表示密度（表示行数・行の高さ・スクロール）は @production-ready/ui の INFORMATION_* を使う（#265 で一本化）。

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

/** エディタの対象の URL の値のうち、新規作成を表す値（`?user=new` / `?role=new`。#4635）。 */
export const SECURITY_NEW_ENTITY_ID = "new";

/**
 * 一覧 → 全幅のエディタ（A 型。page-archetypes.md §1 A。#4635）の対象を URL の検索パラメータに持つ。
 * `null` = 一覧、`"new"` = 新規作成、`<id>` = その対象のエディタ。再読込・戻る / 進む・リンクの共有で同じ画面が開く。
 *
 * - `open`: 一覧からエディタへ入る（履歴に積む。ブラウザの「戻る」でも一覧へ戻る）。
 * - `close`: 一覧へ戻る（この画面で積んだときは `history.back()`、URL から直接開いたときは置き換える）。
 * - `replace`: 作成の後に作った対象へ移る・削除の後に一覧へ戻るなど、履歴に積まずに置き換える。
 * - `hrefFor`: 一覧の行の名前のリンク（新しいタブで開ける。#583）。
 */
export function useSecurityEditorSelection(param: string): Omit<UrlSelection, "select" | "clear"> & {
  open: (id: string) => void;
  close: () => void;
  hrefFor: (id: string) => string;
} {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // 「一覧へ戻る」の履歴の戻りは router を通す（ブラウザの戻ると同じ。MemoryRouter の画面でも動く）。
  const goBack = useCallback(() => navigate(-1), [navigate]);
  const selection = useUrlSelection({ param, searchParams, setSearchParams, singlePane: true, goBack });
  const hrefFor = (id: string) => {
    const next = new URLSearchParams(searchParams);
    next.set(param, id);
    return `?${next.toString()}`;
  };
  return {
    selectedId: selection.selectedId,
    open: selection.select,
    close: selection.clear,
    replace: selection.replace,
    hrefFor,
  };
}

/**
 * エディタから一覧へ戻ったとき、開いていた対象の行の名前（`RowTitleButton`）へフォーカスを戻す（A 型。#4635）。
 * `active` は一覧を出しているか。行には `data-security-row-id` を付ける。
 */
export function useReturnFocusToRow(active: boolean, lastOpenedId: string | null) {
  const previousActive = useRef(active);
  useLayoutEffect(() => {
    const wasActive = previousActive.current;
    previousActive.current = active;
    if (!active || wasActive || !lastOpenedId) return;
    const target = document.querySelector<HTMLElement>(
      `[data-security-row-id="${CSS.escape(lastOpenedId)}"]`,
    );
    target?.focus();
  });
}

/**
 * 一覧の画面のページング（#1891）。クライアント側で全件を持つ一覧を 25 件ずつ（表示件数は 10 / 25 / 50 / 100）
 * に分ける。開いている対象は URL（`useSecurityEditorSelection`）が持つ。
 *
 * - ページと表示件数は作業状態（`pagingKey`。このタブの sessionStorage）に残す。別の画面へ移って戻っても、
 *   再読込しても同じページ・件数に戻る（`lists/listPaging.ts`）。
 * - 検索語・並べ替え（`resetKey`）か表示件数が変わったら 1 ページ目へ戻す。
 * - 選択が変わったとき（URL から開いた・作成 / 保存の後）・一覧へ戻ったとき・読み込みが終わったときに、選んだ行が
 *   今のページの外なら、その行のあるページへ移して見せる。利用者がページを移ったときは選択の行へ引き戻さない。
 */
export function usePagedSelectionList<T>({
  items,
  getKey,
  selectedId,
  active,
  ready,
  resetKey,
  pagingKey,
}: {
  /** 絞り込み・並べ替えの後の全件。 */
  items: readonly T[];
  getKey: (item: T) => string;
  /** 開いている対象（URL の検索パラメータ）。一覧へ戻ったときにその行のあるページを出す。 */
  selectedId: string | null;
  /** 一覧を表示しているか（作成・編集の画面ではページを動かさない）。 */
  active: boolean;
  /** 読み込みが終わっているか（読み込み中はページを動かさない）。 */
  ready: boolean;
  /** 1 ページ目へ戻す契機（検索語・並べ替え）。 */
  resetKey: unknown;
  /** ページ番号と表示件数を残す作業状態の名前。 */
  pagingKey: ListPagingKey;
}) {
  const { page, setPage, pageSize, setPageSize } = usePersistedListPaging(pagingKey);
  const pagination = usePagination(items, pageSize, { page, onPageChange: setPage, resetKey });
  const revealRequested = useValuesChanged([selectedId, active, ready]);

  if (revealRequested && active && ready && selectedId) {
    // 選んだ行のあるページへ移す（render 中に自分の state を直す。次の render でそのページを出す）。
    const index = items.findIndex((item) => getKey(item) === selectedId);
    const targetPage = index >= 0 ? Math.floor(index / pageSize) + 1 : null;
    if (targetPage !== null && targetPage !== pagination.page) setPage(targetPage);
  }

  return {
    ...pagination,
    pageSize,
    /** 表示件数を変える（1 ページ目へ戻す）。 */
    changePageSize: setPageSize,
  };
}

/**
 * 一覧の画面のページ送り（表の直下。#1891）。`usePagedSelectionList` の戻り値を渡す。
 * 1 ページに収まるときは部品が範囲と表示件数の選択だけを出す（件数は `ListToolbar` の `ListCount`）。
 */
export function SecurityListPagination({
  list,
  ariaLabel,
  testId,
  disabled = false,
}: {
  list: Pick<
    ReturnType<typeof usePagedSelectionList>,
    "page" | "totalPages" | "range" | "pageSize" | "setPage" | "changePageSize"
  >;
  ariaLabel: string;
  testId?: string;
  disabled?: boolean;
}) {
  return (
    <Pagination
      page={list.page}
      totalPages={list.totalPages}
      range={list.range}
      onPageChange={list.setPage}
      pageSize={list.pageSize}
      pageSizeOptions={PAGE_SIZE_OPTIONS}
      onPageSizeChange={list.changePageSize}
      ariaLabel={ariaLabel}
      testId={testId}
      disabled={disabled}
    />
  );
}

/** 一覧の件数（`ListToolbar` の `count`）。検索語があるときだけ一致した件数を添える（#1891）。 */
export function securityListCount(total: number, matched: number, search: string) {
  return search.trim() ? { total, matched } : { total };
}

/**
 * ユーザーの編集の保存で送るロール（#1050）。画面はロールを 1 つ選ぶ形のため、編集の開始時に選ばれていた
 * ロール（baseline）だけを選び直したロールに置き換え、ほかのロール（2 つ目以降・アーカイブ済み・参照できないもの）は
 * 残す。選び直していなければ今のロールをそのまま返す。重複は 1 つにまとめる。
 */
export function nextUserRoleIds(
  currentRoleIds: readonly string[],
  baselineRoleId: string,
  selectedRoleId: string,
): string[] {
  if (!selectedRoleId || selectedRoleId === baselineRoleId) return [...currentRoleIds];
  const next =
    baselineRoleId && currentRoleIds.includes(baselineRoleId)
      ? currentRoleIds.map((roleId) => (roleId === baselineRoleId ? selectedRoleId : roleId))
      : [...currentRoleIds, selectedRoleId];
  return Array.from(new Set(next));
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

/** 作成・編集のフォームのパネル（カード 1 枚）。 */
export function SecurityManagementPanelShell({
  id,
  labelledBy,
  ariaLabel,
  idPrefix,
  className = "",
  children,
}: {
  id: string;
  labelledBy?: string;
  ariaLabel?: string;
  idPrefix: string;
  className?: string;
  children: ReactNode;
}) {
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
      {children}
    </section>
  );
}

/**
 * エディタの対象（URL の `?user=` / `?role=`）を読み込んでいる間と、見つからないときの本文（A 型。#4635）。
 * 読み込み中はフォームの形の Skeleton、読み込んだ後に無ければ「見つかりません」と一覧へ戻る導線を出す
 * （別の対象へ置き換えない）。
 */
export function SecurityEditorFallback({
  loading,
  loadingLabel,
  title,
  hint,
  onBack,
  testId,
}: {
  loading: boolean;
  loadingLabel: string;
  title: string;
  hint: string;
  onBack: () => void;
  testId: string;
}) {
  if (loading) {
    return (
      <TimedLoadingState label={loadingLabel} operationKey={`${testId}-loading`} placement="page" testId={`${testId}-loading`}>
        <FormSkeleton />
      </TimedLoadingState>
    );
  }
  return (
    <Card data-testid={testId}>
      <EmptyState
        title={title}
        hint={hint}
        action={
          <Button variant="secondary" icon={ArrowLeft} onClick={onBack}>
            {t("security.common.backToList")}
          </Button>
        }
      />
    </Card>
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
