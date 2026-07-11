import { Children, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  ArrowDownUp,
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
import type { DbAdminObjectDetail, DbAdminObjectSummary, SchemaCatalog } from "../types";
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
  return <div className={`animate-pulse rounded-md bg-slate-100 ${className}`} aria-hidden="true" />;
}

export function DbObjectManagementPanelShell({
  id,
  labelledBy,
  ariaLabel,
  idPrefix,
  className = "",
  splitId,
  preferredWidePane = "right",
  children,
}: {
  id: string;
  labelledBy: string;
  ariaLabel?: string;
  idPrefix: string;
  className?: string;
  splitId?: string;
  preferredWidePane?: FixedSplitWidePane;
  children: ReactNode;
}) {
  const panelChildren = Children.toArray(children);
  const splitPaneId = splitId && panelChildren.length === 2 ? splitId : null;

  return (
    <section
      id={id}
      role="tabpanel"
      aria-labelledby={labelledBy}
      aria-label={ariaLabel}
      className={`grid gap-4 rounded-md border border-slate-200 bg-white p-4 shadow-sm ${className}`}
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
        <h2 id={headingId} className="flex items-center gap-2 text-base font-semibold text-slate-950">
          <Icon size={18} aria-hidden="true" />
          {title}
        </h2>
        {description && <p className="mt-1 text-sm text-slate-600">{description}</p>}
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
    <label className="grid min-w-0 gap-1 text-sm font-medium text-slate-800">
      <span>{label}</span>
      <span className="relative">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
          aria-hidden="true"
        />
        <input
          type="search"
          value={value}
          onChange={(event) => onChange(event.currentTarget.value)}
          className="min-h-11 w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
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
  return (
    <ol className="grid gap-2 md:grid-cols-2" aria-label={ariaLabel} data-testid={dataTestId}>
      {steps.map((label, index) => (
        <li
          key={label}
          className={`rounded-md border px-3 py-2 text-sm font-semibold ${
            index <= activeIndex ? "border-sky-200 bg-sky-50 text-sky-900" : "border-slate-200 bg-white text-slate-500"
          }`}
        >
          {index + 1}. {label}
        </li>
      ))}
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

export function DbObjectStatusMetricItem({ label, value, testId, emphasis = false }: DbObjectStatusMetric) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <dt className="text-xs font-medium text-slate-500">{label}</dt>
      <dd
        className={`mt-1 font-semibold text-slate-950 ${emphasis ? "text-lg" : ""}`}
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
}: {
  ariaLabel: string;
  metrics: DbObjectStatusMetric[];
  actions?: ReactNode;
  metricColumnsClass?: string;
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white px-4 py-3 shadow-sm" aria-label={ariaLabel}>
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
      className="inline-flex items-center gap-1 whitespace-nowrap text-left font-semibold text-slate-700 hover:text-slate-950 focus:outline-none focus:ring-2 focus:ring-sky-200"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <ArrowDownUp size={13} className={active ? "text-sky-700" : "text-slate-400"} aria-hidden="true" />
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

      <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_13rem]">
          <DbManagementSearchField
            label={t("dbAdmin.search.label")}
            placeholder={t("dbAdmin.search.placeholder")}
            value={search}
            onChange={onSearchChange}
          />
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{labels.filter}</span>
            <select
              value={filter}
              onChange={(event) => onFilterChange(event.currentTarget.value as DbObjectFilter)}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
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
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          <div className="max-h-[42rem] overflow-auto" data-testid="db-admin-object-list">
            <table className="w-full min-w-[28rem] table-fixed divide-y divide-slate-200 text-left text-sm" data-testid={`${idPrefix}-grid`}>
              <colgroup>
                <col className="w-[9.5rem]" />
                <col className="w-[4.25rem]" />
                <col className="w-[4.25rem]" />
                <col className="w-[10rem]" />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-slate-50 text-xs text-slate-600">
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
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => {
                  const selected = item.name === selectedName;
                  return (
                    <tr key={item.name} className={selected ? "bg-sky-50" : "hover:bg-slate-50"}>
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          aria-label={labels.showObject(item.name)}
                          aria-current={selected ? "true" : undefined}
                          className="break-all font-mono text-xs font-semibold text-sky-800 hover:text-sky-950 focus:outline-none focus:ring-2 focus:ring-sky-200"
                          onClick={() => onSelect(item.name)}
                        >
                          {item.name}
                        </button>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-700">{rowCountLabel(item.row_count)}</td>
                      <td className="hidden whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-600 lg:table-cell">{item.owner || "-"}</td>
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
  catalog,
  loading,
  exporting = false,
  tab,
  labels,
  onTabChange,
  onExport,
  onDrop,
}: {
  idPrefix: string;
  headingId: string;
  detail: DbAdminObjectDetail | null;
  catalog: SchemaCatalog | null;
  loading: boolean;
  exporting?: boolean;
  tab: DbObjectDetailTab;
  labels: DbObjectDetailLabels;
  onTabChange: (tab: DbObjectDetailTab) => void;
  onExport?: (name: string) => void;
  onDrop: (name: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const sampleByColumn = useMemo(() => {
    if (!detail || !catalog) return new Map<string, string>();
    const table = catalog.tables.find((item) => item.table_name.toUpperCase() === detail.name.toUpperCase());
    return new Map((table?.columns ?? []).map((column) => [column.column_name.toUpperCase(), column.sample_values.join(", ")]));
  }, [detail, catalog]);

  if (loading) return <DbObjectDetailSkeleton idPrefix={idPrefix} />;

  if (!detail) {
    return (
      <section className="grid min-w-0 content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-4">
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
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-4" aria-labelledby={headingId}>
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id={headingId} className="break-all font-mono text-base font-semibold text-slate-950">
              {detail.name}
            </h2>
            <StatusBadge variant="neutral" label={detail.object_type} />
            <StatusBadge variant="neutral" label={t("dbAdmin.detail.columnCount", { count: detail.columns.length })} />
            {detail.row_count != null && <StatusBadge variant="info" label={rowCountLabel(detail.row_count)} />}
          </div>
          {detail.comment && <p className="mt-2 text-sm leading-6 text-slate-700">{detail.comment}</p>}
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
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {warning}
        </p>
      ))}

      <div className="overflow-x-auto border-b border-slate-200" role="tablist" aria-label={labels.tabsLabel}>
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
                className={`group inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-4 text-sm font-semibold transition-colors focus:outline-none focus-visible:bg-sky-50 focus-visible:shadow-[inset_0_-3px_0_0_rgb(2_132_199)] ${
                  selected
                    ? "border-sky-700 bg-white text-sky-900"
                    : "border-transparent text-slate-600 hover:border-slate-300 hover:bg-white hover:text-slate-950"
                }`}
                onClick={() => onTabChange(item.id)}
                onKeyDown={(event) => handleDetailTabKeyDown(event, index)}
              >
                <Icon
                  size={15}
                  aria-hidden="true"
                  className={selected ? "text-sky-700" : "text-slate-400 group-hover:text-slate-600"}
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
          className="min-w-0 max-w-full overflow-x-auto rounded-md border border-slate-200 bg-white"
        >
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
      ) : (
        <section
          id={`${idPrefix}-detail-panel-ddl`}
          role="tabpanel"
          aria-labelledby={`${idPrefix}-detail-tab-ddl`}
          className="grid gap-3 rounded-md border border-slate-200 bg-white p-3"
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
          <pre className="max-h-96 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
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

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="overflow-x-auto p-1.5">
        <div className="flex max-w-full min-w-max gap-1 rounded-md bg-slate-100/80 p-1" role="tablist" aria-label={ariaLabel}>
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
                className={`group inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-md border px-4 text-sm font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-sky-200 ${
                  selected
                    ? "border-sky-200 bg-white text-sky-950 shadow-sm ring-1 ring-sky-100"
                    : "border-transparent text-slate-600 hover:bg-white/80 hover:text-slate-950"
                }`}
                onClick={() => onViewChange(tab.id)}
                onKeyDown={(event) => handleKeyDown(event, index)}
              >
                <span
                  className={`inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors ${
                    selected ? "bg-sky-100 text-sky-700" : "bg-transparent text-slate-400 group-hover:bg-slate-100 group-hover:text-slate-600"
                  }`}
                  aria-hidden="true"
                >
                  <Icon size={16} />
                </span>
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>
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
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/50 p-3 sm:items-center" role="presentation">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="drop-db-object-dialog-title"
        className="max-h-[90dvh] w-full max-w-3xl overflow-auto rounded-md border border-red-200 bg-white shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-red-100 bg-red-50 px-4 py-3">
          <div>
            <h2 id="drop-db-object-dialog-title" className="text-base font-semibold text-red-950">
              {labels.title}
            </h2>
            <p className="mt-1 text-sm text-red-800">{labels.subtitle}</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            <X size={15} aria-hidden="true" />
            <span>{labels.close}</span>
          </Button>
        </div>
        <div className="grid gap-4 p-4">
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2">
            <p className="text-xs font-semibold text-red-800">{labels.target}</p>
            <p className="mt-1 break-all font-mono text-sm font-semibold text-red-950">{objectName}</p>
          </div>
          <fieldset className="grid gap-3 rounded-md border border-red-200 bg-red-50/70 p-3">
            <legend className="px-1 text-sm font-semibold text-red-950">{labels.executeTitle}</legend>
            <ExecutionConfirmationField
              value={confirmation}
              onChange={onConfirmationChange}
              confirmed={canExecute}
              placeholder={objectName}
              expectedLabel={objectName}
              helper={labels.executeHint}
              tone="danger"
            />
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <Button type="button" variant="danger" size="sm" loading={loading} disabled={!canExecute} onClick={onExecute}>
                <Trash2 size={15} aria-hidden="true" />
                <span>{labels.run}</span>
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                <span>{labels.cancel}</span>
              </Button>
            </div>
          </fieldset>
        </div>
      </section>
    </div>
  );
}
