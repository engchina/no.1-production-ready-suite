import { Children, type KeyboardEvent, type ReactNode } from "react";
import {
  ArrowDownUp,
  Check,
  Code2,
  Download,
  RefreshCw,
  Table2,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Banner, EmptyState, toast } from "@engchina/production-ready-ui";

import { ContentActionBar } from "@/components/ContentActionBar";
import { DialogOverlayPortal } from "@/components/ui/dialog-overlay";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  DbManagementSearchField,
  DbObjectSearchOwnerFields,
  DbOwnerPrefixFilterField,
  type DbObjectFilterFieldProps,
} from "@/components/DbObjectFilterFields";
import { isInteractiveRowTarget } from "@/components/MasterDetailDataTable";
import {
  ObjectActionBar,
  RowActionMenu,
  type EntityAction,
  type EntityActionTone,
} from "@/components/ObjectActions";
import {
  TimedLoadingState,
  type ProcessingActivityIcon,
  type ProcessingPlacement,
} from "@/components/ProcessingState";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { ErrorState } from "@/components/StateViews";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { toastError } from "@/lib/toast";
import {
  INFORMATION_LIST_ROW_CLASS,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_LIST_SHORT_SCROLL_CLASS,
  INFORMATION_LIST_VISIBLE_ROWS,
  INFORMATION_TABLE_FOCUS_CLASS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_SCROLL_CLASS,
} from "@/lib/list-density";
import type { FixedSplitWidePane } from "@/lib/fixed-split-pane";
import type { DbAdminObjectDetail, DbAdminObjectSummary } from "../types";
import { ExecutionConfirmationField, downloadText } from "./DbAdminShared";

export {
  DbManagementSearchField,
  DbManagementSelectField,
  DbObjectSearchOwnerFields,
  DbOwnerPrefixFilterField,
} from "@/components/DbObjectFilterFields";

export type DbObjectDetailTab = "columns" | "ddl";
export type DbObjectOwnerPrefix = string;
export type DbObjectSortKey = "name" | "row_count" | "owner";
export type DbObjectSortDirection = "asc" | "desc";
export type DbObjectPickerSortKey = "name" | "kind" | "row_count" | "owner";
export type DbObjectPickerSortDirection = "asc" | "desc";

export interface DbObjectSortState {
  key: DbObjectSortKey;
  direction: DbObjectSortDirection;
}

export interface DbObjectPickerSortState {
  key: DbObjectPickerSortKey;
  direction: DbObjectPickerSortDirection;
}

export interface DbObjectGridLabels {
  title: string;
  hint: string;
  count: string;
  loading: string;
  emptyTitle: string;
  emptyHint: string;
  noResultsTitle: string;
  noResultsHint: string;
  objectName: string;
  rows: string;
  owner: string;
  showObject: (name: string) => string;
}

export interface DbObjectDetailLabels {
  actions: string;
  loading: string;
  ddlLoading: string;
  tabsLabel: string;
  columns: string;
  ddl: string;
  export?: string;
  exportAria?: string;
  exactCount?: string;
  exactCountAria?: string;
  drop: string;
}

export interface DbObjectStatusBarLabels {
  ariaLabel: string;
  count: string;
  runtime: string;
  refreshedAt: string;
  refresh: string;
  schemaRefresh: string;
}

export interface DbObjectDropDialogLabels {
  title: string;
  subtitle: string;
  close: string;
  target: string;
  executeTitle: string;
  executeHint: string;
  cancel: string;
  run: string;
}

export interface DbObjectTab<T extends string> {
  id: T;
  label: string;
  icon: LucideIcon;
}

export interface DbObjectStatusMetric {
  label: string;
  value: string;
  testId?: string;
  emphasis?: boolean;
}

export function focusDbObjectTabElement(id: string) {
  window.requestAnimationFrame(() => document.getElementById(id)?.focus({ preventScroll: true }));
}

export function dbObjectSortValue(item: DbAdminObjectSummary, key: DbObjectSortKey) {
  if (key === "row_count") return item.row_count ?? -1;
  if (key === "name") return dbAdminObjectQualifiedName(item).toLowerCase();
  return item.owner.toLowerCase();
}

export function rowCountLabel(rowCount?: number | null) {
  return rowCount == null ? "-" : t("dbAdmin.list.rows", { count: rowCount });
}

export interface DbAdminObjectTarget {
  owner: string;
  name: string;
  qualifiedName: string;
}

export function dbAdminObjectQualifiedName(item: {
  name: string;
  owner?: string;
  qualified_name?: string;
}) {
  const qualifiedName = (item.qualified_name ?? "").trim();
  if (qualifiedName) return qualifiedName.toUpperCase();
  const owner = (item.owner ?? "").trim().toUpperCase();
  const name = item.name.trim().toUpperCase();
  return owner ? `${owner}.${name}` : name;
}

export function parseDbAdminObjectTarget(value: string, owner = ""): DbAdminObjectTarget {
  const normalizedOwner = owner.trim().replaceAll('"', "").toUpperCase();
  const raw = value.trim().replaceAll('"', "").toUpperCase();
  const dotIndex = raw.indexOf(".");
  if (dotIndex >= 0) {
    const parsedOwner = raw.slice(0, dotIndex);
    const name = raw.slice(dotIndex + 1);
    return {
      owner: parsedOwner,
      name,
      qualifiedName: parsedOwner && name ? `${parsedOwner}.${name}` : raw,
    };
  }
  return {
    owner: normalizedOwner,
    name: raw,
    qualifiedName: normalizedOwner ? `${normalizedOwner}.${raw}` : raw,
  };
}

export type DbManagementLoadingSkeletonVariant = "list" | "detail" | "compact";

// 5/8 行の上限を共有し、表形式のみ sticky header 分を含める。
export const DB_OBJECT_GRID_ROW_CLASS = INFORMATION_TABLE_ROW_CLASS;
export const DB_OBJECT_PICKER_ROW_CLASS = INFORMATION_LIST_ROW_CLASS;
export const DB_OBJECT_GRID_SCROLL_CLASS = INFORMATION_TABLE_SCROLL_CLASS;
export const DB_OBJECT_PICKER_SCROLL_CLASS = INFORMATION_LIST_SCROLL_CLASS;
export const DB_OBJECT_PICKER_SHORT_SCROLL_CLASS = INFORMATION_LIST_SHORT_SCROLL_CLASS;
export const DB_OBJECT_LIST_VISIBLE_ROWS = INFORMATION_LIST_VISIBLE_ROWS;

function SkeletonBlock({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-muted/30 motion-reduce:animate-none ${className}`}
      aria-hidden="true"
      data-testid="db-management-skeleton-block"
    />
  );
}

/**
 * データ準備系の管理画面で共有する読込スケルトン。
 * detail はテーブル／ビュー詳細の既存寸法を正本として維持する。
 */
export function DbManagementLoadingSkeleton({
  idPrefix,
  ariaLabel,
  variant = "detail",
  rows = 8,
  operationKey,
  onCancel,
  placement = "panel",
  testId,
  activityIcon,
}: {
  idPrefix: string;
  ariaLabel: string;
  variant?: DbManagementLoadingSkeletonVariant;
  rows?: number;
  operationKey?: string | number | null;
  onCancel?: () => void;
  placement?: ProcessingPlacement;
  testId?: string;
  activityIcon?: ProcessingActivityIcon;
}) {
  if (variant === "list") {
    return (
      <TimedLoadingState
        label={ariaLabel}
        operationKey={operationKey}
        onCancel={onCancel}
        placement={placement}
        testId={testId ?? `${idPrefix}-list-skeleton`}
        activityIcon={activityIcon}
      >
        <div className="grid gap-2">
          <SkeletonBlock className="h-11" />
          {Array.from({ length: rows }, (_, index) => (
            <SkeletonBlock key={index} className="h-[3.5rem]" />
          ))}
        </div>
      </TimedLoadingState>
    );
  }

  if (variant === "compact") {
    return (
      <TimedLoadingState
        label={ariaLabel}
        operationKey={operationKey}
        onCancel={onCancel}
        placement={placement}
        testId={testId ?? `${idPrefix}-compact-skeleton`}
        activityIcon={activityIcon}
      >
        <SkeletonBlock className="h-10" />
        <SkeletonBlock className="h-24" />
      </TimedLoadingState>
    );
  }

  return (
    <TimedLoadingState
      label={ariaLabel}
      operationKey={operationKey}
      onCancel={onCancel}
      placement={placement}
      testId={testId ?? `${idPrefix}-detail-skeleton`}
      activityIcon={activityIcon}
    >
      <SkeletonBlock className="h-[64px]" />
      <SkeletonBlock className="h-[40px]" />
      <SkeletonBlock className="h-[288px]" />
    </TimedLoadingState>
  );
}

export function DbObjectManagementPanelShell({
  id,
  labelledBy,
  ariaLabel,
  idPrefix,
  className = "",
  splitId,
  preferredWidePane = "right",
  minLeftPaneWidthPx,
  minRightPaneWidthPx,
  role = "tabpanel",
  processing,
  topContent,
  children,
}: {
  id: string;
  /** タブ連携時のみ指定。list+actions 等タブ非連携では省略し role="region" を使う。 */
  labelledBy?: string;
  ariaLabel?: string;
  idPrefix: string;
  className?: string;
  splitId?: string;
  preferredWidePane?: FixedSplitWidePane;
  minLeftPaneWidthPx?: number;
  minRightPaneWidthPx?: number;
  /** タブ配下は "tabpanel"(既定)、タブ非連携の独立領域は "region"。 */
  role?: "tabpanel" | "region";
  /** 既存内容を保持する明示的な再取得など、作業領域に属する処理状態。 */
  processing?: ReactNode;
  /** 左右分割の外側に置く、画面全幅の補助コンテンツ。 */
  topContent?: ReactNode;
  children: ReactNode;
}) {
  const panelChildren = Children.toArray(children);
  const splitPaneId = splitId && panelChildren.length === 2 ? splitId : null;

  return (
    <section
      id={id}
      role={role}
      aria-labelledby={labelledBy}
      aria-label={ariaLabel}
      aria-busy={processing ? true : undefined}
      className={`grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm ${className}`}
      data-testid="management-panel-shell"
      data-management-id={idPrefix}
    >
      {processing}
      {topContent}
      {splitPaneId ? (
        <FixedSplitPane
          splitId={splitPaneId}
          preferredWidePane={preferredWidePane}
          minLeftPaneWidthPx={minLeftPaneWidthPx}
          minRightPaneWidthPx={minRightPaneWidthPx}
          left={panelChildren[0]}
          right={panelChildren[1]}
        />
      ) : (
        children
      )}
    </section>
  );
}

export function DbObjectPanelHeader({
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

export function DbObjectSelectorToolbar({
  searchLabel,
  searchPlaceholder,
  searchValue,
  onSearchChange,
  resultLabel,
  dataTestId,
  className = "",
  ownerPrefixField,
  children,
}: {
  searchLabel: string;
  searchPlaceholder: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  resultLabel?: string;
  dataTestId?: string;
  className?: string;
  ownerPrefixField?: DbObjectFilterFieldProps;
  children?: ReactNode;
}) {
  return (
    <div
      className={`grid gap-2 rounded-md border border-border bg-background p-3 ${className}`}
      data-testid={dataTestId}
    >
      {ownerPrefixField && children ? (
        <div className="grid min-w-0 gap-2 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] xl:items-end">
          <DbManagementSearchField
            label={searchLabel}
            placeholder={searchPlaceholder}
            value={searchValue}
            onChange={onSearchChange}
            disabled={ownerPrefixField.disabled}
          />
          <DbOwnerPrefixFilterField
            label={ownerPrefixField.label}
            placeholder={ownerPrefixField.placeholder}
            value={ownerPrefixField.value}
            onChange={ownerPrefixField.onChange}
            disabled={ownerPrefixField.disabled}
          />
          <div className="grid min-w-0 gap-2 sm:grid-flow-col sm:auto-cols-max sm:items-end">
            {children}
          </div>
        </div>
      ) : ownerPrefixField ? (
        <DbObjectSearchOwnerFields
          searchLabel={searchLabel}
          searchPlaceholder={searchPlaceholder}
          searchValue={searchValue}
          onSearchChange={onSearchChange}
          ownerLabel={ownerPrefixField.label}
          ownerPlaceholder={ownerPrefixField.placeholder}
          ownerValue={ownerPrefixField.value}
          onOwnerChange={ownerPrefixField.onChange}
          disabled={ownerPrefixField.disabled}
        />
      ) : (
        <div className={children ? "grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]" : "grid gap-2"}>
          <DbManagementSearchField
            label={searchLabel}
            placeholder={searchPlaceholder}
            value={searchValue}
            onChange={onSearchChange}
          />
          {children && (
            <div className="grid min-w-0 gap-2 sm:grid-flow-col sm:auto-cols-max sm:items-end">
              {children}
            </div>
          )}
        </div>
      )}
      {resultLabel && (
        <p className="text-xs text-muted" aria-live="polite">
          {resultLabel}
        </p>
      )}
    </div>
  );
}

export function DbObjectSelectorFooter({
  visibleCount,
  totalCount,
  selectedCount,
  hasNextPage,
  loadingNextPage = false,
  loadMoreError = "",
  loadMoreLabel = t("objectSelector.loadMore"),
  dataTestId,
  onLoadMore,
  onRetryLoadMore,
}: {
  visibleCount: number;
  totalCount: number;
  selectedCount?: number;
  hasNextPage?: boolean;
  loadingNextPage?: boolean;
  loadMoreError?: string;
  loadMoreLabel?: string;
  dataTestId?: string;
  onLoadMore?: () => void;
  onRetryLoadMore?: () => void;
}) {
  return (
    <div
      className="grid min-h-10 gap-2 rounded-md border border-border bg-card px-3 py-2"
      data-testid={dataTestId}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-muted" aria-live="polite">
          {selectedCount == null
            ? t("objectSelector.resultCount", { visible: visibleCount, total: totalCount })
            : t("objectSelector.resultCountWithSelected", {
                visible: visibleCount,
                total: totalCount,
                selected: selectedCount,
              })}
        </p>
        {hasNextPage && onLoadMore && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="w-full sm:w-auto"
            loading={loadingNextPage}
            onClick={onLoadMore}
          >
            {loadMoreLabel}
          </Button>
        )}
      </div>
      {loadMoreError && (
        <Banner
          severity="danger"
          action={onRetryLoadMore ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="w-full sm:w-auto"
              loading={loadingNextPage}
              onClick={onRetryLoadMore}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("common.retry")}</span>
            </Button>
          ) : undefined}
        >
          {loadMoreError}
        </Banner>
      )}
    </div>
  );
}

export function DbObjectSelectionSummary({
  label,
  value,
  badge,
}: {
  label: string;
  value: string;
  badge?: ReactNode;
}) {
  if (!value) return null;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border border-primary/20 bg-card px-3 py-2 text-sm text-foreground">
      <span className="font-medium text-foreground">{label}</span>
      <span className="break-all font-mono text-xs font-semibold text-primary">{value}</span>
      {badge}
    </div>
  );
}

export interface DbObjectPickerItem {
  key: string;
  name: string;
  kind?: string;
  kindLabel?: string;
  kindVariant?: "neutral" | "info" | "success" | "warning" | "danger";
  rowCount?: number | null;
  rowCountLabel?: string;
  owner?: string;
  comment?: string;
}

export function dbObjectPickerSortValue(item: DbObjectPickerItem, key: DbObjectPickerSortKey) {
  if (key === "row_count") return item.rowCount ?? -1;
  if (key === "kind") return (item.kind ?? item.kindLabel ?? "").toLowerCase();
  if (key === "owner") return (item.owner ?? "").toLowerCase();
  return item.name.toLowerCase();
}

export function sortDbObjectPickerItems<T extends DbObjectPickerItem>(
  items: T[],
  sort: DbObjectPickerSortState
) {
  return items.slice().sort((left, right) => {
    const a = dbObjectPickerSortValue(left, sort.key);
    const b = dbObjectPickerSortValue(right, sort.key);
    const comparison =
      typeof a === "number" && typeof b === "number"
        ? a - b
        : String(a).localeCompare(String(b), "ja");
    return sort.direction === "asc" ? comparison : -comparison;
  });
}

function PickerSortHeader({
  label,
  sortKey,
  sort,
  onSortChange,
}: {
  label: string;
  sortKey: DbObjectPickerSortKey;
  sort?: DbObjectPickerSortState;
  onSortChange?: (key: DbObjectPickerSortKey) => void;
}) {
  const active = sort?.key === sortKey;
  const direction = active
    ? sort.direction === "asc"
      ? t("objectSelector.sort.asc")
      : t("objectSelector.sort.desc")
    : t("objectSelector.sort.inactive");
  const ariaSort =
    !sort || !onSortChange
      ? undefined
      : active
        ? sort.direction === "asc"
          ? "ascending"
          : "descending"
        : "none";

  return (
    <span role="columnheader" aria-sort={ariaSort}>
      {sort && onSortChange ? (
        <button
          type="button"
          className="inline-flex items-center gap-1 whitespace-nowrap text-left font-semibold text-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
          aria-label={t("objectSelector.sort.button", { label, direction })}
          onClick={() => onSortChange(sortKey)}
        >
          <span>{label}</span>
          <ArrowDownUp size={13} className={active ? "text-primary" : "text-muted"} aria-hidden="true" />
        </button>
      ) : (
        label
      )}
    </span>
  );
}

export function DbSingleObjectPickerList({
  items,
  selectedKey,
  hasActiveFilter,
  loadingKey = "",
  listLabel,
  emptyTitle,
  emptyHint,
  noResultsTitle,
  noResultsHint,
  dataTestId,
  maxHeightClass = DB_OBJECT_PICKER_SCROLL_CLASS,
  onSelect,
  selectAriaLabel,
  selectDisabled,
  sort,
  onSortChange,
  action,
}: {
  items: DbObjectPickerItem[];
  selectedKey: string;
  hasActiveFilter: boolean;
  loadingKey?: string;
  listLabel: string;
  emptyTitle: string;
  emptyHint: string;
  noResultsTitle: string;
  noResultsHint: string;
  dataTestId?: string;
  maxHeightClass?: string;
  onSelect: (item: DbObjectPickerItem) => void;
  selectAriaLabel?: (item: DbObjectPickerItem) => string;
  selectDisabled?: (item: DbObjectPickerItem) => boolean;
  sort?: DbObjectPickerSortState;
  onSortChange?: (key: DbObjectPickerSortKey) => void;
  action?: {
    id: string;
    label: string;
    icon?: LucideIcon;
    tone?: EntityActionTone;
    ariaLabel: (item: DbObjectPickerItem) => string;
    visible?: (item: DbObjectPickerItem) => boolean;
    disabled?: (item: DbObjectPickerItem) => boolean;
    onClick: (item: DbObjectPickerItem) => void;
  };
}) {
  if (items.length === 0) {
    return (
      <div className="rounded-md border border-border bg-card p-4" data-testid={dataTestId}>
        <EmptyState
          title={hasActiveFilter ? noResultsTitle : emptyTitle}
          hint={hasActiveFilter ? noResultsHint : emptyHint}
        />
      </div>
    );
  }

  const headerClass = action
    ? "hidden grid-cols-[minmax(0,1.35fr)_5.25rem_5.25rem_minmax(4.5rem,0.75fr)_3.5rem] gap-2 border-b border-border bg-background px-3 py-2 text-xs font-semibold text-muted md:grid"
    : "hidden grid-cols-[minmax(0,1.45fr)_5.25rem_5.25rem_minmax(4.5rem,0.8fr)] gap-2 border-b border-border bg-background px-3 py-2 text-xs font-semibold text-muted md:grid";
  const rowClass = action
    ? "md:grid-cols-[minmax(0,1.35fr)_5.25rem_5.25rem_minmax(4.5rem,0.75fr)_3.5rem]"
    : "md:grid-cols-[minmax(0,1.45fr)_5.25rem_5.25rem_minmax(4.5rem,0.8fr)]";

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card" data-testid={dataTestId}>
      <div className={headerClass}>
        <PickerSortHeader
          label={t("objectSelector.column.name")}
          sortKey="name"
          sort={sort}
          onSortChange={onSortChange}
        />
        <PickerSortHeader
          label={t("objectSelector.column.kind")}
          sortKey="kind"
          sort={sort}
          onSortChange={onSortChange}
        />
        <PickerSortHeader
          label={t("objectSelector.column.rows")}
          sortKey="row_count"
          sort={sort}
          onSortChange={onSortChange}
        />
        <PickerSortHeader
          label={t("objectSelector.column.owner")}
          sortKey="owner"
          sort={sort}
          onSortChange={onSortChange}
        />
        {action && <span role="columnheader" className="text-right">{t("objectSelector.column.actions")}</span>}
      </div>
      <div className={maxHeightClass} role="list" aria-label={listLabel}>
        {items.map((item) => {
          const selected = item.key === selectedKey || item.name === selectedKey;
          const selectionDisabled = Boolean(selectDisabled?.(item));
          const actionLoading = Boolean(action && loadingKey === item.key);
          const rowActions: EntityAction[] = action
            ? [
                {
                  id: action.id,
                  label: action.label,
                  ariaLabel: action.ariaLabel(item),
                  icon: action.icon,
                  tone: action.tone,
                  visible: action.visible?.(item),
                  loading: actionLoading,
                  disabled: action.disabled?.(item),
                  onSelect: () => action.onClick(item),
                },
              ]
            : [];
          const hasRowActions = rowActions.some((rowAction) => rowAction.visible !== false);
          const rowActionsDisabled =
            rowActions.length > 0 && rowActions.every((rowAction) => rowAction.disabled);
          return (
            <div
              key={item.key}
              role="listitem"
              aria-current={selected ? "true" : undefined}
              className={[
                `grid w-full min-w-0 gap-2 border-b border-border px-3 py-3 text-left text-sm transition-colors last:border-b-0 ${DB_OBJECT_PICKER_ROW_CLASS}`,
                `${rowClass} md:items-center md:py-2`,
                selectionDisabled ? "cursor-not-allowed" : "cursor-pointer",
                selected ? "bg-primary/10" : selectionDisabled ? "bg-card" : "bg-card hover:bg-background",
              ].join(" ")}
              onClick={(event) => {
                if (isInteractiveRowTarget(event.target)) return;
                if (selectionDisabled) return;
                onSelect(item);
              }}
            >
              <button
                type="button"
                aria-current={selected ? "true" : undefined}
                aria-label={selectAriaLabel?.(item) ?? t("objectSelector.selectObject", { name: item.name })}
                disabled={selectionDisabled}
                className="flex min-h-11 w-full min-w-0 flex-col justify-center text-left focus:outline-none focus:ring-2 focus:ring-ring/40 md:min-h-0"
                onClick={() => {
                  if (selectionDisabled) return;
                  onSelect(item);
                }}
              >
                <span className="break-all font-mono text-xs font-semibold text-primary">{item.name}</span>
                {item.comment && <span className="mt-1 block break-words text-xs text-muted md:hidden">{item.comment}</span>}
              </button>
              <span className="flex items-center gap-2 md:block">
                <span className="text-xs font-medium text-muted md:hidden">{t("objectSelector.column.kind")}</span>
                {item.kindLabel ? (
                  <StatusBadge variant={item.kindVariant ?? "neutral"} label={item.kindLabel} />
                ) : (
                  <span className="text-xs text-muted">-</span>
                )}
              </span>
              <span className="flex items-center gap-2 font-mono text-xs text-foreground md:block">
                <span className="font-sans font-medium text-muted md:hidden">{t("objectSelector.column.rows")}</span>
                {item.rowCountLabel || "-"}
              </span>
              <span className="flex min-w-0 items-center gap-2 font-mono text-xs text-muted md:block">
                <span className="font-sans font-medium text-muted md:hidden">{t("objectSelector.column.owner")}</span>
                <span className="break-all">{item.owner || "-"}</span>
              </span>
              {action && hasRowActions && (
                <span className="flex items-center justify-between gap-2 md:justify-end">
                  <span className="text-xs font-medium text-muted md:hidden">
                    {t("objectSelector.column.actions")}
                  </span>
                  <RowActionMenu
                    actions={rowActions}
                    ariaLabel={`${t("objectSelector.column.actions")}: ${item.name}`}
                    loading={actionLoading}
                    disabled={rowActionsDisabled}
                    testId={dataTestId ? `${dataTestId}-row-actions-${item.key}` : undefined}
                  />
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function DbObjectStepIndicator({
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
  // 水平ステッパー(Material/Ant 標準): 丸番号(完了は ✓) + 連結線 + 丸の下に中央ラベル。
  // 状態は色 + アイコン/番号で伝達(color-not-only)。等幅分配 + ラベル折返しで 375px でも横溢れなし。
  return (
    <ol className="flex items-start" aria-label={ariaLabel} data-testid={dataTestId}>
      {steps.map((label, index) => {
        const complete = index < activeIndex;
        const current = index === activeIndex;
        const isFirst = index === 0;
        const isLast = index === steps.length - 1;
        return (
          <li
            key={label}
            className="flex flex-1 flex-col items-center gap-2"
            aria-current={current ? "step" : undefined}
          >
            <div className="flex w-full items-center">
              <span
                aria-hidden="true"
                className={`h-0.5 flex-1 rounded-full ${
                  isFirst ? "opacity-0" : index <= activeIndex ? "bg-primary" : "bg-border"
                }`}
              />
              <span
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-colors ${
                  complete
                    ? "border-primary bg-primary text-primary-foreground"
                    : current
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border bg-card text-muted"
                }`}
              >
                {complete ? <Check size={16} aria-hidden="true" /> : <span className="tnum">{index + 1}</span>}
              </span>
              <span
                aria-hidden="true"
                className={`h-0.5 flex-1 rounded-full ${
                  isLast ? "opacity-0" : index < activeIndex ? "bg-primary" : "bg-border"
                }`}
              />
            </div>
            <span
              className={`px-1 text-center text-xs font-medium leading-snug ${
                complete || current ? "text-foreground" : "text-muted"
              }`}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function DbObjectStatusMetricItem({
  label,
  value,
  testId,
  emphasis = false,
  density = "default",
}: DbObjectStatusMetric & { density?: "default" | "compact" }) {
  if (density === "compact") {
    return (
      <div className="flex min-w-0 items-baseline gap-2 py-1">
        <dt className="shrink-0 text-xs font-medium text-muted">{label}</dt>
        <dd
          className={`min-w-0 break-words font-semibold tabular-nums text-foreground [overflow-wrap:anywhere] ${
            emphasis ? "text-base" : "text-sm"
          }`}
          data-testid={testId}
        >
          {value}
        </dd>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd
        className={`mt-1 font-semibold text-foreground ${emphasis ? "text-lg" : ""}`}
        data-testid={testId}
      >
        {value}
      </dd>
    </div>
  );
}

export function DbObjectManagementStatusBar({
  ariaLabel,
  metrics,
  actions,
  metricColumnsClass = "sm:grid-cols-3",
  density = "default",
}: {
  ariaLabel: string;
  metrics: DbObjectStatusMetric[];
  actions?: ReactNode;
  metricColumnsClass?: string;
  density?: "default" | "compact";
}) {
  if (density === "compact") {
    return (
      <section
        className="rounded-md border border-border bg-card px-3 py-2 shadow-sm"
        aria-label={ariaLabel}
        data-density="compact"
      >
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <dl className="flex min-w-0 flex-1 flex-wrap items-center gap-x-5 gap-y-1">
            {metrics.map((metric) => (
              <DbObjectStatusMetricItem
                key={`${metric.label}-${metric.value}`}
                {...metric}
                density="compact"
              />
            ))}
          </dl>
          {actions && <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
        </div>
      </section>
    );
  }

  return (
    <section
      className="rounded-md border border-border bg-card px-4 py-3 shadow-sm"
      aria-label={ariaLabel}
      data-density="default"
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <dl className={`grid gap-3 ${metricColumnsClass} xl:flex xl:flex-wrap xl:items-center`}>
          {metrics.map((metric) => (
            <DbObjectStatusMetricItem key={`${metric.label}-${metric.value}`} {...metric} />
          ))}
        </dl>
        {actions && <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">{actions}</div>}
      </div>
    </section>
  );
}

export function DbObjectStatusBar({
  count,
  runtime,
  refreshedAt,
  loading,
  labels,
  onRefresh,
  onSchemaRefresh,
}: {
  count: number;
  runtime: string;
  refreshedAt: string;
  loading: string;
  labels: DbObjectStatusBarLabels;
  onRefresh: () => void;
  onSchemaRefresh: () => void;
}) {
  return (
    <DbObjectManagementStatusBar
      ariaLabel={labels.ariaLabel}
      metrics={[
        { label: labels.count, value: formatNumber(count), emphasis: true },
        { label: labels.runtime, value: runtime },
        { label: labels.refreshedAt, value: formatDateTime(refreshedAt) },
      ]}
      actions={
        <>
          <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={onRefresh}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{labels.refresh}</span>
          </Button>
          <Button
            type="button"
            variant="primary"
            size="sm"
            loading={loading === "schema-refresh"}
            onClick={onSchemaRefresh}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{labels.schemaRefresh}</span>
          </Button>
        </>
      }
    />
  );
}

function SortButton({
  label,
  sortKey,
  sort,
  onToggle,
}: {
  label: string;
  sortKey: DbObjectSortKey;
  sort: DbObjectSortState;
  onToggle: (key: DbObjectSortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 whitespace-nowrap text-left font-semibold text-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <ArrowDownUp size={13} className={active ? "text-primary" : "text-muted"} aria-hidden="true" />
    </button>
  );
}

export function DbObjectGrid({
  idPrefix,
  headingId,
  icon,
  items,
  selectedName,
  loading,
  search,
  ownerPrefix,
  sort,
  labels,
  totalCount,
  hasNextPage,
  loadingNextPage = false,
  loadMoreError = "",
  error = "",
  onSearchChange,
  onOwnerPrefixChange,
  onSortChange,
  onSelect,
  onLoadMore,
  onRetryLoadMore,
  onRetry,
}: {
  idPrefix: string;
  headingId: string;
  icon: LucideIcon;
  items: DbAdminObjectSummary[];
  selectedName: string;
  loading: boolean;
  search: string;
  ownerPrefix: DbObjectOwnerPrefix;
  sort: DbObjectSortState;
  labels: DbObjectGridLabels;
  totalCount?: number;
  hasNextPage?: boolean;
  loadingNextPage?: boolean;
  loadMoreError?: string;
  error?: string;
  onSearchChange: (value: string) => void;
  onOwnerPrefixChange: (value: DbObjectOwnerPrefix) => void;
  onSortChange: (key: DbObjectSortKey) => void;
  onSelect: (name: string) => void;
  onLoadMore?: () => void;
  onRetryLoadMore?: () => void;
  onRetry?: () => void;
}) {
  const hasActiveFilter = Boolean(search.trim()) || Boolean(ownerPrefix.trim());
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby={headingId}>
      <DbObjectPanelHeader
        headingId={headingId}
        icon={icon}
        title={labels.title}
        description={labels.hint}
        action={<StatusBadge variant="info" label={labels.count} />}
      />

      <div className="grid gap-2 rounded-md border border-border bg-background p-3">
        <DbObjectSearchOwnerFields
          searchLabel={t("dbAdmin.search.label")}
          searchPlaceholder={t("dbAdmin.search.placeholder")}
          searchValue={search}
          onSearchChange={onSearchChange}
          ownerLabel={t("dbAdmin.owner.label")}
          ownerPlaceholder={t("dbAdmin.ownerPrefix.placeholder")}
          ownerValue={ownerPrefix}
          onOwnerChange={onOwnerPrefixChange}
        />
      </div>

      {loading ? (
        <DbManagementLoadingSkeleton
          idPrefix={idPrefix}
          ariaLabel={labels.loading}
          variant="list"
        />
      ) : error ? (
        <ErrorState message={error} onRetry={onRetry} />
      ) : items.length === 0 ? (
        <EmptyState
          title={hasActiveFilter ? labels.noResultsTitle : labels.emptyTitle}
          hint={hasActiveFilter ? labels.noResultsHint : labels.emptyHint}
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className={DB_OBJECT_GRID_SCROLL_CLASS} data-testid="db-admin-object-list">
            <table className="w-full min-w-[24rem] table-fixed divide-y divide-border text-left text-sm" data-testid={`${idPrefix}-grid`}>
              <colgroup>
                <col className="w-[55%]" />
                <col className="w-[7.5rem]" />
                <col className="w-[7.5rem]" />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
                <tr>
                  <th className="whitespace-nowrap px-3 py-2">
                    <SortButton label={labels.objectName} sortKey="name" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="whitespace-nowrap px-3 py-2">
                    <SortButton label={labels.rows} sortKey="row_count" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="hidden whitespace-nowrap px-3 py-2 lg:table-cell">
                    <SortButton label={labels.owner} sortKey="owner" sort={sort} onToggle={onSortChange} />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {items.map((item) => {
                  const qualifiedName = dbAdminObjectQualifiedName(item);
                  const selected = qualifiedName === selectedName;
                  return (
                    <tr
                      key={qualifiedName}
                      data-selected={selected ? "true" : "false"}
                      aria-current={selected ? "true" : undefined}
                      className={[
                        DB_OBJECT_GRID_ROW_CLASS,
                        "cursor-pointer transition-colors",
                        selected ? "bg-primary/10" : "hover:bg-background",
                      ].join(" ")}
                      onClick={(event) => {
                        if (isInteractiveRowTarget(event.target)) return;
                        onSelect(qualifiedName);
                      }}
                    >
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          aria-label={labels.showObject(qualifiedName)}
                          aria-current={selected ? "true" : undefined}
                          className="break-all font-mono text-xs font-semibold text-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
                          onClick={() => onSelect(qualifiedName)}
                        >
                          {qualifiedName}
                        </button>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-foreground">{rowCountLabel(item.row_count)}</td>
                      <td className="hidden whitespace-nowrap px-3 py-2 font-mono text-xs text-muted lg:table-cell">{item.owner || "-"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {!loading && !error && (
        <DbObjectSelectorFooter
          visibleCount={items.length}
          totalCount={totalCount ?? items.length}
          hasNextPage={hasNextPage}
          loadingNextPage={loadingNextPage}
          loadMoreError={loadMoreError}
          dataTestId={`${idPrefix}-footer`}
          onLoadMore={onLoadMore}
          onRetryLoadMore={onRetryLoadMore}
        />
      )}
    </section>
  );
}

export function DbObjectDetailPanel({
  idPrefix,
  operationKey,
  headingId,
  detail,
  loading,
  ddlLoading = false,
  error,
  ddlError = "",
  exporting = false,
  countingRows = false,
  tab,
  labels,
  onTabChange,
  onRetry,
  onRetryDdl,
  onCancel,
  onExport,
  onExactCount,
  onDrop,
}: {
  idPrefix: string;
  operationKey?: string | number | null;
  headingId: string;
  detail: DbAdminObjectDetail | null;
  loading: boolean;
  ddlLoading?: boolean;
  error: string;
  ddlError?: string;
  exporting?: boolean;
  countingRows?: boolean;
  tab: DbObjectDetailTab;
  labels: DbObjectDetailLabels;
  onTabChange: (tab: DbObjectDetailTab) => void;
  onRetry: () => void;
  onRetryDdl?: () => void;
  onCancel?: () => void;
  onExport?: (name: string) => void;
  onExactCount?: (name: string) => void;
  onDrop: (name: string) => void;
}) {
  if (loading) {
    return (
      <DbManagementLoadingSkeleton
        idPrefix={idPrefix}
        ariaLabel={labels.loading}
        variant="detail"
        operationKey={operationKey ?? idPrefix}
        onCancel={onCancel}
      />
    );
  }

  if (error) {
    return (
      <section
        className="grid min-w-0 content-start rounded-md border border-border bg-background p-4"
        data-testid={`${idPrefix}-detail-error`}
      >
        <ErrorState message={error} onRetry={onRetry} />
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4">
        <EmptyState title={t("dbAdmin.detail.emptyTitle")} hint={t("dbAdmin.detail.emptyHint")} />
      </section>
    );
  }

  const copyDdl = async () => {
    try {
      await navigator.clipboard.writeText(detail.ddl);
      toast.success(t("common.action.copied"));
    } catch {
      toastError(t("common.action.copyFailed"));
    }
  };
  const detailTabs = [
    { id: "columns", label: labels.columns, icon: Table2 },
    { id: "ddl", label: labels.ddl, icon: Code2 },
  ] as const;
  const detailQualifiedName = dbAdminObjectQualifiedName(detail);
  const detailActions: EntityAction[] = [
    {
      id: "exact-count",
      label: labels.exactCount ?? "",
      ariaLabel: labels.exactCountAria,
      visible: Boolean(onExactCount && labels.exactCount && detail.object_type === "table"),
      loading: countingRows,
      onSelect: () => {
        if (onExactCount) onExactCount(detailQualifiedName);
      },
    },
    {
      id: "export",
      label: labels.export ?? "",
      ariaLabel: labels.exportAria,
      icon: Download,
      visible: Boolean(onExport && labels.export && labels.exportAria),
      loading: exporting,
      onSelect: () => {
        if (onExport) onExport(detailQualifiedName);
      },
    },
    {
      id: "drop",
      label: labels.drop,
      icon: Trash2,
      tone: "danger",
      onSelect: () => onDrop(detailQualifiedName),
    },
  ];
  const handleDetailTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keyMap: Record<string, number | undefined> = {
      ArrowRight: (index + 1) % detailTabs.length,
      ArrowLeft: (index - 1 + detailTabs.length) % detailTabs.length,
      Home: 0,
      End: detailTabs.length - 1,
    };
    const nextIndex = keyMap[event.key];
    if (nextIndex === undefined) return;
    event.preventDefault();
    const nextTab = detailTabs[nextIndex];
    onTabChange(nextTab.id);
    focusDbObjectTabElement(`${idPrefix}-detail-tab-${nextTab.id}`);
  };
  const showRowCountBadge = detail.object_type === "table" || detail.row_count != null;

  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4" aria-labelledby={headingId}>
      <div
        className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between"
        data-testid={`${idPrefix}-detail-header`}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id={headingId} className="break-all font-mono text-base font-semibold text-foreground">
              {detailQualifiedName}
            </h2>
            <StatusBadge variant="neutral" label={detail.object_type} />
            <StatusBadge variant="neutral" label={t("dbAdmin.detail.columnCount", { count: detail.columns.length })} />
            {showRowCountBadge && (
              <StatusBadge
                variant={detail.row_count != null ? "info" : "neutral"}
                label={rowCountLabel(detail.row_count)}
                className="min-w-[4.5rem]"
              />
            )}
          </div>
          {detail.comment && <p className="mt-2 text-sm leading-6 text-foreground">{detail.comment}</p>}
        </div>
        <ObjectActionBar
          actions={detailActions}
          ariaLabel={`${labels.actions}: ${detailQualifiedName}`}
          testId={`${idPrefix}-detail-actions`}
        />
      </div>

      {detail.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
          {warning}
        </p>
      ))}

      <div className="overflow-x-auto border-b border-border" role="tablist" aria-label={labels.tabsLabel}>
        <div className="flex min-w-max gap-1">
          {detailTabs.map((item, index) => {
            const Icon = item.icon;
            const selected = tab === item.id;
            return (
              <button
                key={item.id}
                id={`${idPrefix}-detail-tab-${item.id}`}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`${idPrefix}-detail-panel-${item.id}`}
                className={`group inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-4 text-sm font-semibold transition-colors focus:outline-none focus-visible:bg-primary/10 focus-visible:shadow-[inset_0_-3px_0_0_var(--primary)] ${
                  selected
                    ? "border-primary bg-card text-primary"
                    : "border-transparent text-muted hover:border-border hover:bg-card hover:text-foreground"
                }`}
                onClick={() => onTabChange(item.id)}
                onKeyDown={(event) => handleDetailTabKeyDown(event, index)}
              >
                <Icon
                  size={15}
                  aria-hidden="true"
                  className={selected ? "text-primary" : "text-muted group-hover:text-muted"}
                />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {tab === "columns" ? (
        <div
          id={`${idPrefix}-detail-panel-columns`}
          role="tabpanel"
          aria-labelledby={`${idPrefix}-detail-tab-columns`}
          data-testid="db-admin-detail-columns"
          tabIndex={0}
          className={`min-w-0 rounded-md border border-border bg-card ${INFORMATION_TABLE_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
        >
          <table className="w-full min-w-[52rem] table-fixed divide-y divide-border text-sm">
            <colgroup>
              <col className="w-[18%]" />
              <col className="w-[18%]" />
              <col className="w-[20%]" />
              <col className="w-[14%]" />
              <col className="w-[10%]" />
              <col />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-background">
              <tr className="h-10">
                <th className="whitespace-nowrap px-3 py-2 text-left">{t("dbAdmin.col.physical")}</th>
                <th className="whitespace-nowrap px-3 py-2 text-left">{t("dbAdmin.col.logical")}</th>
                <th className="whitespace-nowrap px-3 py-2 text-left">{t("dbAdmin.col.comment")}</th>
                <th className="whitespace-nowrap px-3 py-2 text-left">{t("dbAdmin.col.type")}</th>
                <th className="whitespace-nowrap px-3 py-2 text-left">{t("dbAdmin.col.nullable")}</th>
                <th className="whitespace-nowrap px-3 py-2 text-left">{t("dbAdmin.col.sample")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {detail.columns.map((column) => (
                <tr key={column.column_name} className={INFORMATION_TABLE_ROW_CLASS}>
                  <td className="px-3 py-2 font-mono text-xs">{column.column_name}</td>
                  <td className="break-words px-3 py-2">{(column.logical_name ?? "").trim() || "-"}</td>
                  <td className="break-words px-3 py-2 text-muted">{(column.comment ?? "").trim() || "-"}</td>
                  <td className="px-3 py-2">{column.data_type}</td>
                  <td className="px-3 py-2">{column.nullable ? "YES" : "NO"}</td>
                  <td className="break-words px-3 py-2 font-mono text-xs text-muted">
                    {column.sample_values.join(", ") || "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : ddlLoading ? (
        <TimedLoadingState
          label={labels.ddlLoading}
          operationKey={`${idPrefix}-${detailQualifiedName}-ddl`}
          onCancel={onCancel}
          placement="tab"
          testId={`${idPrefix}-ddl-skeleton`}
        >
          <SkeletonBlock className="h-[40px]" />
          <SkeletonBlock className="h-[288px]" />
        </TimedLoadingState>
      ) : ddlError ? (
        <section
          id={`${idPrefix}-detail-panel-ddl`}
          role="tabpanel"
          aria-labelledby={`${idPrefix}-detail-tab-ddl`}
          className="rounded-md border border-border bg-card p-3"
          data-testid={`${idPrefix}-ddl-error`}
        >
          <ErrorState message={ddlError} onRetry={onRetryDdl} />
        </section>
      ) : (
        <section
          id={`${idPrefix}-detail-panel-ddl`}
          role="tabpanel"
          aria-labelledby={`${idPrefix}-detail-tab-ddl`}
          className="grid gap-3 rounded-md border border-border bg-card p-3"
        >
          <ContentActionBar
            ariaLabel={`${labels.ddl}: ${labels.actions}`}
            testId={`${idPrefix}-ddl-actions`}
          >
            <Button type="button" variant="secondary" size="sm" disabled={!detail.ddl} onClick={() => void copyDdl()}>
              {t("dbAdmin.detail.copy")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={!detail.ddl}
              onClick={() => {
                try {
                  downloadText(`${detailQualifiedName.toLowerCase().replace(".", "_")}_ddl.sql`, detail.ddl);
                  toast.success(t("common.action.downloaded"));
                } catch {
                  toastError(t("common.action.downloadFailed"));
                }
              }}
            >
              <Download size={15} aria-hidden="true" />
              <span>{t("dbAdmin.detail.download")}</span>
            </Button>
          </ContentActionBar>
          <pre className="max-h-96 overflow-auto rounded-md border border-border bg-code p-3 text-sm leading-6 text-code-fg">
            <code>{detail.ddl || "-"}</code>
          </pre>
        </section>
      )}
    </section>
  );
}

export function DbObjectManagementTabs<T extends string>({
  idPrefix,
  tabs,
  activeView,
  ariaLabel,
  onViewChange,
}: {
  idPrefix: string;
  tabs: Array<DbObjectTab<T>>;
  activeView: T;
  ariaLabel: string;
  onViewChange: (view: T) => void;
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
    focusDbObjectTabElement(`${idPrefix}-tab-${nextView.id}`);
  };

  // 下線タブ(管理コンソールの定石)。詳細タブ(列情報/DDL)と同一様式に統一し、
  // セグメント型ピルの過剰装飾を排する。role/aria/キーボード操作の意味論は不変。
  return (
    <div className="overflow-x-auto border-b border-border" role="tablist" aria-label={ariaLabel}>
      <div className="flex min-w-max gap-1">
        {tabs.map((tab, index) => {
          const Icon = tab.icon;
          const selected = activeView === tab.id;
          return (
            <button
              key={tab.id}
              id={`${idPrefix}-tab-${tab.id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${idPrefix}-panel-${tab.id}`}
              className={`group inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-4 text-sm font-semibold transition-colors focus:outline-none focus-visible:bg-primary/10 focus-visible:shadow-[inset_0_-3px_0_0_var(--primary)] ${
                selected
                  ? "border-primary bg-card text-primary"
                  : "border-transparent text-muted hover:border-border hover:bg-card hover:text-foreground"
              }`}
              onClick={() => onViewChange(tab.id)}
              onKeyDown={(event) => handleKeyDown(event, index)}
            >
              <Icon
                size={15}
                aria-hidden="true"
                className={selected ? "text-primary" : "text-muted group-hover:text-muted"}
              />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function DropDbObjectDialog({
  objectName,
  confirmation,
  loading,
  error,
  labels,
  onConfirmationChange,
  onExecute,
  onClose,
}: {
  objectName: string;
  confirmation: string;
  loading: boolean;
  error?: string;
  labels: DbObjectDropDialogLabels;
  onConfirmationChange: (value: string) => void;
  onExecute: () => void;
  onClose: () => void;
}) {
  const canExecute = confirmation.trim() === objectName;
  return (
    <DialogOverlayPortal className="p-3 sm:items-center">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="drop-db-object-dialog-title"
        className="max-h-[90dvh] w-full max-w-3xl overflow-auto rounded-md border border-border bg-card shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border bg-card px-4 py-3">
          <div>
            <h2 id="drop-db-object-dialog-title" className="text-base font-semibold text-danger">
              {labels.title}
            </h2>
            <p className="mt-1 text-sm text-muted">{labels.subtitle}</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            <X size={15} aria-hidden="true" />
            <span>{labels.close}</span>
          </Button>
        </div>
        <div className="grid gap-4 p-4">
          <div className="rounded-md border border-border bg-background px-3 py-2">
            <p className="text-xs font-semibold text-foreground">{labels.target}</p>
            <p className="mt-1 break-all font-mono text-sm font-semibold text-foreground">{objectName}</p>
          </div>
          {error && (
            <Banner severity="danger">
              {error}
            </Banner>
          )}
          <fieldset className="grid gap-3 rounded-md border border-border bg-background p-3">
            <legend className="px-1 text-sm font-semibold text-foreground">{labels.executeTitle}</legend>
            <ExecutionConfirmationField
              value={confirmation}
              onChange={onConfirmationChange}
              confirmed={canExecute}
              placeholder={objectName}
              expectedLabel={objectName}
              helper={labels.executeHint}
              tone="danger"
              actions={
                <>
                  <Button type="button" variant="danger" size="sm" loading={loading} disabled={!canExecute} onClick={onExecute}>
                    <Trash2 size={15} aria-hidden="true" />
                    <span>{labels.run}</span>
                  </Button>
                  <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                    <span>{labels.cancel}</span>
                  </Button>
                </>
              }
            />
          </fieldset>
        </div>
      </section>
    </DialogOverlayPortal>
  );
}
