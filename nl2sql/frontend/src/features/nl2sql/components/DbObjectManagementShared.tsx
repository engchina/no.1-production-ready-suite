import { Children, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  ArrowDownUp,
  Check,
  Code2,
  Download,
  RefreshCw,
  Search,
  Table2,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";

import { Button, EmptyState, StatusBadge } from "@engchina/production-ready-ui";

import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import type { FixedSplitWidePane } from "@/lib/fixed-split-pane";
import type { DbAdminObjectDetail, DbAdminObjectSummary } from "../types";
import { ExecutionConfirmationField, downloadText } from "./DbAdminShared";

export type DbObjectDetailTab = "columns" | "ddl";
export type DbObjectFilter = "all" | "with_rows" | "empty_rows";
export type DbObjectSortKey = "name" | "row_count" | "owner";
export type DbObjectSortDirection = "asc" | "desc";

export interface DbObjectSortState {
  key: DbObjectSortKey;
  direction: DbObjectSortDirection;
}

export interface DbObjectGridLabels {
  title: string;
  hint: string;
  count: string;
  emptyTitle: string;
  emptyHint: string;
  noResultsTitle: string;
  noResultsHint: string;
  filter: string;
  filterAll: string;
  filterWithRows: string;
  filterEmptyRows: string;
  objectName: string;
  rows: string;
  owner: string;
  actions: string;
  detail: string;
  drop: string;
  showObject: (name: string) => string;
}

export interface DbObjectDetailLabels {
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
  window.requestAnimationFrame(() => document.getElementById(id)?.focus());
}

export function dbObjectSortValue(item: DbAdminObjectSummary, key: DbObjectSortKey) {
  if (key === "row_count") return item.row_count ?? -1;
  if (key === "name") return item.name.toLowerCase();
  return item.owner.toLowerCase();
}

export function rowCountLabel(rowCount?: number | null) {
  return rowCount == null ? "-" : t("dbAdmin.list.rows", { count: rowCount });
}

function SkeletonBlock({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-muted/30 ${className}`} aria-hidden="true" />;
}

export function DbObjectManagementPanelShell({
  id,
  labelledBy,
  ariaLabel,
  idPrefix,
  className = "",
  splitId,
  preferredWidePane = "right",
  role = "tabpanel",
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
  /** タブ配下は "tabpanel"(既定)、タブ非連携の独立領域は "region"。 */
  role?: "tabpanel" | "region";
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
      className={`grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm ${className}`}
      data-testid="management-panel-shell"
      data-management-id={idPrefix}
    >
      {splitPaneId ? (
        <FixedSplitPane
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

export function DbManagementSearchField({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
      <span>{label}</span>
      <span className="relative">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
          aria-hidden="true"
        />
        <input
          type="search"
          value={value}
          onChange={(event) => onChange(event.currentTarget.value)}
          className="min-h-11 w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
          placeholder={placeholder}
        />
      </span>
    </label>
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

function DbObjectListSkeleton({ idPrefix }: { idPrefix: string }) {
  return (
    <div className="grid gap-2" data-testid={`${idPrefix}-list-skeleton`}>
      <SkeletonBlock className="h-11" />
      {Array.from({ length: 8 }, (_, index) => (
        <SkeletonBlock key={index} className="h-12" />
      ))}
    </div>
  );
}

function DbObjectDetailSkeleton({ idPrefix }: { idPrefix: string }) {
  return (
    <div className="grid gap-3" data-testid={`${idPrefix}-detail-skeleton`}>
      <SkeletonBlock className="h-16" />
      <SkeletonBlock className="h-10" />
      <SkeletonBlock className="h-72" />
    </div>
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
  filter,
  sort,
  labels,
  onSearchChange,
  onFilterChange,
  onSortChange,
  onSelect,
  onDrop,
}: {
  idPrefix: string;
  headingId: string;
  icon: LucideIcon;
  items: DbAdminObjectSummary[];
  selectedName: string;
  loading: boolean;
  search: string;
  filter: DbObjectFilter;
  sort: DbObjectSortState;
  labels: DbObjectGridLabels;
  onSearchChange: (value: string) => void;
  onFilterChange: (value: DbObjectFilter) => void;
  onSortChange: (key: DbObjectSortKey) => void;
  onSelect: (name: string) => void;
  onDrop: (name: string) => void;
}) {
  const hasActiveFilter = Boolean(search.trim()) || filter !== "all";
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
        <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_13rem]">
          <DbManagementSearchField
            label={t("dbAdmin.search.label")}
            placeholder={t("dbAdmin.search.placeholder")}
            value={search}
            onChange={onSearchChange}
          />
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{labels.filter}</span>
            <select
              value={filter}
              onChange={(event) => onFilterChange(event.currentTarget.value as DbObjectFilter)}
              className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
            >
              <option value="all">{labels.filterAll}</option>
              <option value="with_rows">{labels.filterWithRows}</option>
              <option value="empty_rows">{labels.filterEmptyRows}</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? (
        <DbObjectListSkeleton idPrefix={idPrefix} />
      ) : items.length === 0 ? (
        <EmptyState
          title={hasActiveFilter ? labels.noResultsTitle : labels.emptyTitle}
          hint={hasActiveFilter ? labels.noResultsHint : labels.emptyHint}
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className="max-h-[42rem] overflow-auto" data-testid="db-admin-object-list">
            <table className="w-full min-w-[28rem] table-fixed divide-y divide-border text-left text-sm" data-testid={`${idPrefix}-grid`}>
              <colgroup>
                <col className="w-[9.5rem]" />
                <col className="w-[4.25rem]" />
                <col className="w-[4.25rem]" />
                <col className="w-[10rem]" />
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
                  <th className="whitespace-nowrap px-3 py-2 text-right">{labels.actions}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {items.map((item) => {
                  const selected = item.name === selectedName;
                  return (
                    <tr key={item.name} className={selected ? "bg-primary/10" : "hover:bg-background"}>
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          aria-label={labels.showObject(item.name)}
                          aria-current={selected ? "true" : undefined}
                          className="break-all font-mono text-xs font-semibold text-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
                          onClick={() => onSelect(item.name)}
                        >
                          {item.name}
                        </button>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-foreground">{rowCountLabel(item.row_count)}</td>
                      <td className="hidden whitespace-nowrap px-3 py-2 font-mono text-xs text-muted lg:table-cell">{item.owner || "-"}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right align-top">
                        <div className="flex flex-nowrap justify-end gap-2">
                          <Button type="button" variant="secondary" size="sm" className="min-w-14 whitespace-nowrap" onClick={() => onSelect(item.name)}>
                            <span className="whitespace-nowrap">{labels.detail}</span>
                          </Button>
                          <Button type="button" variant="danger" size="sm" className="min-w-16 whitespace-nowrap" onClick={() => onDrop(item.name)}>
                            <Trash2 size={15} aria-hidden="true" />
                            <span className="whitespace-nowrap">{labels.drop}</span>
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

export function DbObjectDetailPanel({
  idPrefix,
  headingId,
  detail,
  loading,
  exporting = false,
  countingRows = false,
  tab,
  labels,
  onTabChange,
  onExport,
  onExactCount,
  onDrop,
}: {
  idPrefix: string;
  headingId: string;
  detail: DbAdminObjectDetail | null;
  loading: boolean;
  exporting?: boolean;
  countingRows?: boolean;
  tab: DbObjectDetailTab;
  labels: DbObjectDetailLabels;
  onTabChange: (tab: DbObjectDetailTab) => void;
  onExport?: (name: string) => void;
  onExactCount?: (name: string) => void;
  onDrop: (name: string) => void;
}) {
  const [copied, setCopied] = useState(false);

  if (loading) return <DbObjectDetailSkeleton idPrefix={idPrefix} />;

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
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      downloadText(`${detail.name.toLowerCase()}_ddl.sql`, detail.ddl);
    }
  };
  const detailTabs = [
    { id: "columns", label: labels.columns, icon: Table2 },
    { id: "ddl", label: labels.ddl, icon: Code2 },
  ] as const;
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

  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4" aria-labelledby={headingId}>
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id={headingId} className="break-all font-mono text-base font-semibold text-foreground">
              {detail.name}
            </h2>
            <StatusBadge variant="neutral" label={detail.object_type} />
            <StatusBadge variant="neutral" label={t("dbAdmin.detail.columnCount", { count: detail.columns.length })} />
            {detail.row_count != null && <StatusBadge variant="info" label={rowCountLabel(detail.row_count)} />}
            {onExactCount && labels.exactCount && detail.object_type === "table" && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                loading={countingRows}
                aria-label={labels.exactCountAria ?? labels.exactCount}
                onClick={() => onExactCount(detail.name)}
              >
                {labels.exactCount}
              </Button>
            )}
          </div>
          {detail.comment && <p className="mt-2 text-sm leading-6 text-foreground">{detail.comment}</p>}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap xl:justify-end">
          {onExport && labels.export && labels.exportAria && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={exporting}
              aria-label={labels.exportAria}
              onClick={() => onExport(detail.name)}
            >
              <Download size={15} aria-hidden="true" />
              <span>{labels.export}</span>
            </Button>
          )}
          <Button type="button" variant="danger" size="sm" onClick={() => onDrop(detail.name)}>
            <Trash2 size={15} aria-hidden="true" />
            <span>{labels.drop}</span>
          </Button>
        </div>
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
          className="min-w-0 max-w-full overflow-x-auto rounded-md border border-border bg-card"
        >
          <table className="w-full min-w-[52rem] table-fixed divide-y divide-border text-sm">
            <colgroup>
              <col className="w-[18%]" />
              <col className="w-[18%]" />
              <col className="w-[22%]" />
              <col className="w-[14%]" />
              <col className="w-[8%]" />
              <col />
            </colgroup>
            <thead className="bg-background">
              <tr>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.physical")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.logical")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.comment")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.type")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.nullable")}</th>
                <th className="px-3 py-2 text-left">{t("dbAdmin.col.sample")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {detail.columns.map((column) => (
                <tr key={column.column_name}>
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
      ) : (
        <section
          id={`${idPrefix}-detail-panel-ddl`}
          role="tabpanel"
          aria-labelledby={`${idPrefix}-detail-tab-ddl`}
          className="grid gap-3 rounded-md border border-border bg-card p-3"
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
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
  labels,
  onConfirmationChange,
  onExecute,
  onClose,
}: {
  objectName: string;
  confirmation: string;
  loading: boolean;
  labels: DbObjectDropDialogLabels;
  onConfirmationChange: (value: string) => void;
  onExecute: () => void;
  onClose: () => void;
}) {
  const canExecute = confirmation.trim() === objectName;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-3 sm:items-center" role="presentation">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="drop-db-object-dialog-title"
        className="max-h-[90dvh] w-full max-w-3xl overflow-auto rounded-md border border-danger/30 bg-card shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-danger/20 bg-danger-bg px-4 py-3">
          <div>
            <h2 id="drop-db-object-dialog-title" className="text-base font-semibold text-danger">
              {labels.title}
            </h2>
            <p className="mt-1 text-sm text-danger">{labels.subtitle}</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            <X size={15} aria-hidden="true" />
            <span>{labels.close}</span>
          </Button>
        </div>
        <div className="grid gap-4 p-4">
          <div className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2">
            <p className="text-xs font-semibold text-danger">{labels.target}</p>
            <p className="mt-1 break-all font-mono text-sm font-semibold text-danger">{objectName}</p>
          </div>
          <fieldset className="grid gap-3 rounded-md border border-danger/30 bg-danger-bg/70 p-3">
            <legend className="px-1 text-sm font-semibold text-danger">{labels.executeTitle}</legend>
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
    </div>
  );
}
