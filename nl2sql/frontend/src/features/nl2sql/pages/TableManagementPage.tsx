import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  ArrowDownUp,
  Code2,
  Download,
  RefreshCw,
  Search,
  Table2,
  Trash2,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";

import {
  Button,
  EmptyState,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  ExecutionConfirmationField,
  FileInputControl,
  QueryResultsTable,
  StatementRunnerCard,
  downloadBlob,
  downloadText,
  fileToBase64,
} from "../components/DbAdminShared";
import type {
  DbAdminExecuteData,
  DbAdminImportTabularData,
  DbAdminObjectDetail,
  DbAdminObjectSummary,
  DbAdminObjectsData,
  SchemaCatalog,
} from "../types";

type ActiveView = "list" | "create" | "import";
type DetailTab = "columns" | "ddl";
type ImportStep = "file" | "execute";
type TableFilter = "all" | "with_rows" | "empty_rows";
type SortKey = "name" | "row_count" | "owner";
type SortDirection = "asc" | "desc";

interface SortState {
  key: SortKey;
  direction: SortDirection;
}

function focusTabElement(id: string) {
  window.requestAnimationFrame(() => document.getElementById(id)?.focus());
}

function tableSortValue(item: DbAdminObjectSummary, key: SortKey) {
  if (key === "row_count") return item.row_count ?? -1;
  if (key === "name") return item.name.toLowerCase();
  return item.owner.toLowerCase();
}

function rowCountLabel(rowCount?: number | null) {
  return rowCount == null ? "-" : t("dbAdmin.list.rows", { count: rowCount });
}

function SkeletonBlock({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-slate-100 ${className}`} aria-hidden="true" />;
}

const importFieldClass = "grid min-w-0 gap-1 text-sm font-medium leading-5 text-slate-800";
const importControlClass =
  "h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-sky-600 focus:outline-none focus:ring-2 focus:ring-sky-200";

function TableManagementPanelShell({
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
      className={`grid gap-4 rounded-md border border-slate-200 bg-white p-4 shadow-sm ${className}`}
      data-testid="table-management-panel-shell"
    >
      {children}
    </section>
  );
}

function PanelHeader({
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

function StepIndicator({
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

function TableListSkeleton() {
  return (
    <div className="grid gap-2" data-testid="table-management-list-skeleton">
      <SkeletonBlock className="h-11" />
      {Array.from({ length: 8 }, (_, index) => (
        <SkeletonBlock key={index} className="h-12" />
      ))}
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="grid gap-3" data-testid="table-management-detail-skeleton">
      <SkeletonBlock className="h-16" />
      <SkeletonBlock className="h-10" />
      <SkeletonBlock className="h-72" />
    </div>
  );
}

function StatusBar({
  tableCount,
  runtime,
  refreshedAt,
  loading,
  onRefresh,
  onSchemaRefresh,
}: {
  tableCount: number;
  runtime: string;
  refreshedAt: string;
  loading: string;
  onRefresh: () => void;
  onSchemaRefresh: () => void;
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white px-4 py-3 shadow-sm" aria-label={t("tableMgmt.toolbar.status")}>
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <dl className="grid gap-3 sm:grid-cols-3 xl:flex xl:flex-wrap xl:items-center">
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
            <dt className="text-xs font-medium text-slate-500">{t("tableMgmt.metric.tables")}</dt>
            <dd className="mt-1 text-lg font-semibold text-slate-950">{formatNumber(tableCount)}</dd>
          </div>
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
            <dt className="text-xs font-medium text-slate-500">{t("tableMgmt.metric.runtime")}</dt>
            <dd className="mt-1 font-semibold text-slate-950">{runtime}</dd>
          </div>
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
            <dt className="text-xs font-medium text-slate-500">{t("tableMgmt.metric.schemaRefreshed")}</dt>
            <dd className="mt-1 font-semibold text-slate-950">{formatDateTime(refreshedAt)}</dd>
          </div>
        </dl>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={loading === "load"}
            onClick={onRefresh}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("tableMgmt.action.refresh")}</span>
          </Button>
          <Button
            type="button"
            variant="primary"
            size="sm"
            loading={loading === "schema-refresh"}
            onClick={onSchemaRefresh}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("tableMgmt.action.schemaRefresh")}</span>
          </Button>
        </div>
      </div>
    </section>
  );
}

function SortButton({
  label,
  sortKey,
  sort,
  onToggle,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState;
  onToggle: (key: SortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <button
      type="button"
      className="inline-flex whitespace-nowrap items-center gap-1 text-left font-semibold text-slate-700 hover:text-slate-950 focus:outline-none focus:ring-2 focus:ring-sky-200"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <ArrowDownUp size={13} className={active ? "text-sky-700" : "text-slate-400"} aria-hidden="true" />
    </button>
  );
}

function TableGrid({
  items,
  selectedName,
  loading,
  search,
  filter,
  sort,
  onSearchChange,
  onFilterChange,
  onSortChange,
  onSelect,
  onDrop,
}: {
  items: DbAdminObjectSummary[];
  selectedName: string;
  loading: boolean;
  search: string;
  filter: TableFilter;
  sort: SortState;
  onSearchChange: (value: string) => void;
  onFilterChange: (value: TableFilter) => void;
  onSortChange: (key: SortKey) => void;
  onSelect: (name: string) => void;
  onDrop: (name: string) => void;
}) {
  const hasActiveFilter = Boolean(search.trim()) || filter !== "all";
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="table-grid-heading">
      <PanelHeader
        headingId="table-grid-heading"
        icon={Table2}
        title={t("tableMgmt.list.title")}
        description={t("tableMgmt.grid.hint")}
        action={<StatusBadge variant="info" label={t("tableMgmt.grid.count", { count: items.length })} />}
      />

      <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_13rem]">
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("dbAdmin.search.label")}</span>
            <span className="relative">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
              <input
                value={search}
                onChange={(event) => onSearchChange(event.currentTarget.value)}
                className="min-h-11 w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                placeholder={t("dbAdmin.search.placeholder")}
              />
            </span>
          </label>
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("tableMgmt.toolbar.filter")}</span>
            <select
              value={filter}
              onChange={(event) => onFilterChange(event.currentTarget.value as TableFilter)}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
            >
              <option value="all">{t("tableMgmt.toolbar.filterAll")}</option>
              <option value="with_rows">{t("tableMgmt.toolbar.filterWithRows")}</option>
              <option value="empty_rows">{t("tableMgmt.toolbar.filterEmptyRows")}</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? (
        <TableListSkeleton />
      ) : items.length === 0 ? (
        <EmptyState
          title={hasActiveFilter ? t("tableMgmt.list.noResultsTitle") : t("tableMgmt.list.emptyTitle")}
          hint={hasActiveFilter ? t("tableMgmt.list.noResultsHint") : t("tableMgmt.list.emptyHint")}
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          <div className="max-h-[42rem] overflow-auto" data-testid="db-admin-object-list">
            <table className="w-full min-w-[28rem] table-fixed divide-y divide-slate-200 text-left text-sm" data-testid="table-management-grid">
              <colgroup>
                <col className="w-[9.5rem]" />
                <col className="w-[4.25rem]" />
                <col className="w-[4.25rem]" />
                <col className="w-[10rem]" />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="whitespace-nowrap px-3 py-2">
                    <SortButton label={t("tableMgmt.grid.tableName")} sortKey="name" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="whitespace-nowrap px-3 py-2">
                    <SortButton label={t("tableMgmt.grid.rows")} sortKey="row_count" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="hidden whitespace-nowrap px-3 py-2 lg:table-cell">
                    <SortButton label={t("tableMgmt.grid.owner")} sortKey="owner" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="whitespace-nowrap px-3 py-2 text-right">{t("tableMgmt.grid.actions")}</th>
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
                          aria-label={t("tableMgmt.grid.showTable", { name: item.name })}
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
                            <span className="whitespace-nowrap">{t("tableMgmt.grid.detail")}</span>
                          </Button>
                          <Button type="button" variant="danger" size="sm" className="min-w-16 whitespace-nowrap" onClick={() => onDrop(item.name)}>
                            <Trash2 size={15} aria-hidden="true" />
                            <span className="whitespace-nowrap">{t("tableMgmt.grid.drop")}</span>
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

function TableDetailPanel({
  detail,
  catalog,
  loading,
  exporting,
  tab,
  onTabChange,
  onExport,
  onDrop,
}: {
  detail: DbAdminObjectDetail | null;
  catalog: SchemaCatalog | null;
  loading: boolean;
  exporting: boolean;
  tab: DetailTab;
  onTabChange: (tab: DetailTab) => void;
  onExport: (name: string) => void;
  onDrop: (name: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const sampleByColumn = useMemo(() => {
    if (!detail || !catalog) return new Map<string, string>();
    const table = catalog.tables.find((item) => item.table_name.toUpperCase() === detail.name.toUpperCase());
    return new Map((table?.columns ?? []).map((column) => [column.column_name.toUpperCase(), column.sample_values.join(", ")]));
  }, [detail, catalog]);

  if (loading) return <DetailSkeleton />;

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
    { id: "columns", label: t("tableMgmt.detailTabs.columns"), icon: Table2 },
    { id: "ddl", label: t("tableMgmt.detailTabs.ddl"), icon: Code2 },
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
    focusTabElement(`table-detail-tab-${nextTab.id}`);
  };

  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-4" aria-labelledby="table-detail-heading">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="table-detail-heading" className="break-all font-mono text-base font-semibold text-slate-950">
              {detail.name}
            </h2>
            <StatusBadge variant="neutral" label={detail.object_type} />
            <StatusBadge variant="neutral" label={t("dbAdmin.detail.columnCount", { count: detail.columns.length })} />
            {detail.row_count != null && <StatusBadge variant="info" label={rowCountLabel(detail.row_count)} />}
          </div>
          {detail.comment && <p className="mt-2 text-sm leading-6 text-slate-700">{detail.comment}</p>}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap xl:justify-end">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={exporting}
            aria-label={t("tableMgmt.exportColumns")}
            onClick={() => onExport(detail.name)}
          >
            <Download size={15} aria-hidden="true" />
            <span>{t("tableMgmt.export")}</span>
          </Button>
          <Button type="button" variant="danger" size="sm" onClick={() => onDrop(detail.name)}>
            <Trash2 size={15} aria-hidden="true" />
            <span>{t("tableMgmt.grid.drop")}</span>
          </Button>
        </div>
      </div>

      {detail.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {warning}
        </p>
      ))}

      <div className="overflow-x-auto border-b border-slate-200" role="tablist" aria-label={t("tableMgmt.detailTabs.label")}>
        <div className="flex min-w-max gap-1">
          {detailTabs.map((item, index) => {
            const Icon = item.icon;
            const selected = tab === item.id;
            return (
              <button
                key={item.id}
                id={`table-detail-tab-${item.id}`}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`table-detail-panel-${item.id}`}
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
          id="table-detail-panel-columns"
          role="tabpanel"
          aria-labelledby="table-detail-tab-columns"
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
          id="table-detail-panel-ddl"
          role="tabpanel"
          aria-labelledby="table-detail-tab-ddl"
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

function ImportWizard({
  table,
  sheet,
  filename,
  fileReady,
  mode,
  result,
  step,
  confirmation,
  loading,
  onTableChange,
  onSheetChange,
  onModeChange,
  onFilePick,
  onFileClear,
  onExecute,
  onConfirmationChange,
}: {
  table: string;
  sheet: string;
  filename: string;
  fileReady: boolean;
  mode: string;
  result: DbAdminImportTabularData | null;
  step: ImportStep;
  confirmation: string;
  loading: boolean;
  onTableChange: (value: string) => void;
  onSheetChange: (value: string) => void;
  onModeChange: (value: string) => void;
  onFilePick: (file: File | undefined) => void;
  onFileClear: () => void;
  onExecute: () => void;
  onConfirmationChange: (value: string) => void;
}) {
  const steps: Array<{ id: ImportStep; label: string }> = [
    { id: "file", label: t("tableMgmt.importWizard.stepFile") },
    { id: "execute", label: t("tableMgmt.importWizard.stepExecute") },
  ];
  const activeIndex = steps.findIndex((item) => item.id === step);
  const isConfirmed = confirmation.trim() === "ADMIN_EXECUTE";
  const canExecute = Boolean(table.trim()) && fileReady && isConfirmed;

  return (
    <div className="grid gap-4">
      <PanelHeader
        icon={Upload}
        title={t("dataTools.dbAdmin.importTitle")}
        description={t("tableMgmt.import.note")}
        action={
          <Button
            type="button"
            variant="danger"
            size="sm"
            className="w-full sm:w-auto"
            loading={loading}
            disabled={!canExecute}
            onClick={onExecute}
          >
            <Upload size={15} aria-hidden="true" />
            <span>{t("dataTools.dbAdmin.import")}</span>
          </Button>
        }
      />

      <StepIndicator
        steps={steps.map((item) => item.label)}
        activeIndex={activeIndex}
        ariaLabel={t("tableMgmt.importWizard.steps")}
        dataTestId="table-import-steps"
      />

      <div className="grid gap-3 lg:grid-cols-2">
          <label className={importFieldClass}>
            <span>{t("dataTools.dbAdmin.tableName")}</span>
            <input
              value={table}
              onChange={(event) => onTableChange(event.currentTarget.value)}
              className={`${importControlClass} py-2`}
              placeholder="IMPORTED_ORDERS"
            />
          </label>
          <label className={importFieldClass}>
            <span>{t("dataTools.dbAdmin.sheet")}</span>
            <input
              value={sheet}
              onChange={(event) => onSheetChange(event.currentTarget.value)}
              className={`${importControlClass} py-2`}
              placeholder="Sheet1"
            />
          </label>
        </div>

        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem]">
          <FileInputControl
            label={t("dataTools.dbAdmin.file")}
            accept=".csv,.xlsx,.xls"
            filename={filename}
            selectedText={filename ? t("tableMgmt.importWizard.selectedFile", { filename }) : ""}
            emptyText={t("tableMgmt.importWizard.noFile")}
            pickText={t("tableMgmt.importWizard.filePick")}
            replaceText={t("tableMgmt.importWizard.fileReplace")}
            clearAriaLabel={t("tableMgmt.importWizard.clearFile")}
            icon="spreadsheet"
            className="min-w-0"
            dataTestId="table-import-file-field"
            onPick={onFilePick}
            onClear={onFileClear}
          />
          <label className={importFieldClass} data-testid="table-import-mode-field">
            <span>{t("dataTools.dbAdmin.mode")}</span>
            <select
              value={mode}
              onChange={(event) => onModeChange(event.currentTarget.value)}
              className={`${importControlClass} py-2`}
            >
              <option value="create">{t("dataTools.dbAdmin.mode.create")}</option>
              <option value="append">{t("dataTools.dbAdmin.mode.append")}</option>
              <option value="truncate">{t("dataTools.dbAdmin.mode.truncate")}</option>
              <option value="replace">{t("dataTools.dbAdmin.mode.replace")}</option>
            </select>
          </label>
        </div>

        {result && (
          <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm" aria-label={t("tableMgmt.importWizard.result")}>
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant={result.executed ? "success" : "neutral"} label={result.executed ? "executed" : "not executed"} />
              <StatusBadge variant="info" label={result.table_name} />
              <StatusBadge variant="info" label={t("tableMgmt.importWizard.rows", { count: result.row_count })} />
              <StatusBadge variant="neutral" label={result.mode} />
            </div>
            {result.warnings.map((warning) => (
              <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                {warning}
              </p>
            ))}
            <pre className="overflow-auto rounded-md border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-800">
              <code>{`${result.ddl}\n\n${result.insert_sql}`}</code>
            </pre>
            {result.sample_rows.length > 0 && (
              <QueryResultsTable
                results={{
                  columns: Object.keys(result.sample_rows[0] ?? {}),
                  rows: result.sample_rows,
                  total: result.sample_rows.length,
                }}
              />
            )}
          </section>
        )}

      <fieldset className="grid gap-3 rounded-md border border-slate-200 bg-white p-3">
        <legend className="px-1 text-sm font-semibold text-slate-900">{t("tableMgmt.importWizard.executeTitle")}</legend>
        <ExecutionConfirmationField
          value={confirmation}
          onChange={onConfirmationChange}
          confirmed={isConfirmed}
          placeholder="ADMIN_EXECUTE"
          expectedLabel="ADMIN_EXECUTE"
          helper={t("tableMgmt.importWizard.executeHint")}
        />
      </fieldset>
    </div>
  );
}

function TableManagementTabs({
  activeView,
  onViewChange,
}: {
  activeView: ActiveView;
  onViewChange: (view: ActiveView) => void;
}) {
  const views: Array<{ id: ActiveView; label: string }> = [
    { id: "list", label: t("tableMgmt.list.title") },
    { id: "create", label: t("tableMgmt.create.title") },
    { id: "import", label: t("dataTools.dbAdmin.importTitle") },
  ];
  const tabIcons = {
    list: Table2,
    create: Code2,
    import: Upload,
  } satisfies Record<ActiveView, LucideIcon>;
  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keyMap: Record<string, number | undefined> = {
      ArrowRight: (index + 1) % views.length,
      ArrowLeft: (index - 1 + views.length) % views.length,
      Home: 0,
      End: views.length - 1,
    };
    const nextIndex = keyMap[event.key];
    if (nextIndex === undefined) return;
    event.preventDefault();
    const nextView = views[nextIndex];
    onViewChange(nextView.id);
    focusTabElement(`table-management-tab-${nextView.id}`);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="overflow-x-auto p-1.5">
        <div className="flex max-w-full min-w-max gap-1 rounded-md bg-slate-100/80 p-1" role="tablist" aria-label={t("tableMgmt.tabs.label")}>
        {views.map((view, index) => {
          const Icon = tabIcons[view.id];
          const selected = activeView === view.id;
          return (
          <button
            key={view.id}
            id={`table-management-tab-${view.id}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`table-management-panel-${view.id}`}
            className={`group inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded-md border px-4 text-sm font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-sky-200 ${
              selected
                ? "border-sky-200 bg-white text-sky-950 shadow-sm ring-1 ring-sky-100"
                : "border-transparent text-slate-600 hover:bg-white/80 hover:text-slate-950"
            }`}
            onClick={() => onViewChange(view.id)}
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
            <span>{view.label}</span>
          </button>
          );
        })}
        </div>
      </div>
    </div>
  );
}

function DropTableDialog({
  tableName,
  confirmation,
  loading,
  onConfirmationChange,
  onExecute,
  onClose,
}: {
  tableName: string;
  confirmation: string;
  loading: boolean;
  onConfirmationChange: (value: string) => void;
  onExecute: () => void;
  onClose: () => void;
}) {
  const canExecute = confirmation.trim() === tableName;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/50 p-3 sm:items-center" role="presentation">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="drop-table-dialog-title"
        className="max-h-[90dvh] w-full max-w-3xl overflow-auto rounded-md border border-red-200 bg-white shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-red-100 bg-red-50 px-4 py-3">
          <div>
            <h2 id="drop-table-dialog-title" className="text-base font-semibold text-red-950">
              {t("tableMgmt.dropDialog.title")}
            </h2>
            <p className="mt-1 text-sm text-red-800">{t("tableMgmt.dropDialog.subtitle")}</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            <X size={15} aria-hidden="true" />
            <span>{t("tableMgmt.dropDialog.close")}</span>
          </Button>
        </div>
        <div className="grid gap-4 p-4">
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2">
            <p className="text-xs font-semibold text-red-800">{t("tableMgmt.dropDialog.target")}</p>
            <p className="mt-1 break-all font-mono text-sm font-semibold text-red-950">{tableName}</p>
          </div>
          <fieldset className="grid gap-3 rounded-md border border-red-200 bg-red-50/70 p-3">
            <legend className="px-1 text-sm font-semibold text-red-950">{t("tableMgmt.dropDialog.executeTitle")}</legend>
            <ExecutionConfirmationField
              value={confirmation}
              onChange={onConfirmationChange}
              confirmed={canExecute}
              placeholder={tableName}
              expectedLabel={tableName}
              helper={t("tableMgmt.dropDialog.executeHint")}
              tone="danger"
            />
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <Button type="button" variant="danger" size="sm" loading={loading} disabled={!canExecute} onClick={onExecute}>
                <Trash2 size={15} aria-hidden="true" />
                <span>{t("tableMgmt.drop.run")}</span>
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                <span>{t("tableMgmt.dropDialog.cancel")}</span>
              </Button>
            </div>
          </fieldset>
        </div>
      </section>
    </div>
  );
}

export function TableManagementPage() {
  const [tables, setTables] = useState<DbAdminObjectsData | null>(null);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [selectedTableName, setSelectedTableName] = useState("");
  const [detail, setDetail] = useState<DbAdminObjectDetail | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("columns");
  const [activeView, setActiveView] = useState<ActiveView>("list");
  const [tableSearch, setTableSearch] = useState("");
  const [tableFilter, setTableFilter] = useState<TableFilter>("all");
  const [tableSort, setTableSort] = useState<SortState>({ key: "name", direction: "asc" });
  const [dropTargetName, setDropTargetName] = useState("");
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [importTable, setImportTable] = useState("");
  const [importFilename, setImportFilename] = useState("");
  const [importBase64, setImportBase64] = useState("");
  const [importSheet, setImportSheet] = useState("");
  const [importMode, setImportMode] = useState("create");
  const [importStep, setImportStep] = useState<ImportStep>("file");
  const [importConfirmation, setImportConfirmation] = useState("");
  const [importResult, setImportResult] = useState<DbAdminImportTabularData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const fetchDetail = async (name: string) => {
    setLoading(`detail-${name}`);
    setMessage("");
    setSelectedTableName(name);
    setDetail(null);
    setDetailTab("columns");
    try {
      setDetail(await apiGet<DbAdminObjectDetail>(`/api/nl2sql/db-admin/tables/${encodeURIComponent(name)}`));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  const load = async (refreshSchema = false) => {
    setLoading(refreshSchema ? "schema-refresh" : "load");
    setMessage("");
    try {
      const [tableData, catalogData] = await Promise.all([
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
        refreshSchema ? apiPost<SchemaCatalog>("/api/schema/refresh") : apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setTables(tableData);
      setCatalog(catalogData);
      const nextSelected =
        tableData.items.find((item) => item.name === selectedTableName)?.name || tableData.items[0]?.name || "";
      setSelectedTableName(nextSelected);
      if (nextSelected) {
        setDetail(await apiGet<DbAdminObjectDetail>(`/api/nl2sql/db-admin/tables/${encodeURIComponent(nextSelected)}`));
      } else {
        setDetail(null);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const filteredTables = useMemo(() => {
    const q = tableSearch.trim().toLowerCase();
    return (tables?.items ?? [])
      .filter((item) => {
        if (tableFilter === "with_rows" && !(item.row_count != null && item.row_count > 0)) return false;
        if (tableFilter === "empty_rows" && item.row_count !== 0) return false;
        if (!q) return true;
        return (
          item.name.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q) ||
          item.owner.toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = tableSortValue(left, tableSort.key);
        const b = tableSortValue(right, tableSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return tableSort.direction === "asc" ? result : -result;
      });
  }, [tables, tableSearch, tableFilter, tableSort]);

  const toggleSort = (key: SortKey) => {
    setTableSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const pickImportFile = async (file: File | undefined) => {
    if (!file) return;
    setImportFilename(file.name);
    setImportBase64("");
    setImportBase64(await fileToBase64(file));
    setImportResult(null);
    setImportStep("file");
  };

  const clearImportFile = () => {
    setImportFilename("");
    setImportBase64("");
    setImportResult(null);
    setImportStep("file");
  };

  const importTabular = async () => {
    if (!importTable.trim() || !importBase64) return;
    setLoading("import-tabular");
    setMessage("");
    try {
      const result = await apiPost<DbAdminImportTabularData>("/api/nl2sql/db-admin/import-tabular", {
        table_name: importTable,
        content_base64: importBase64,
        filename: importFilename || "upload.csv",
        sheet_name: importSheet,
        mode: importMode,
        execute: true,
        confirmation: importConfirmation,
        reason: "ui-table-management-import-tabular",
      });
      setImportResult(result);
      setImportStep("execute");
      if (result.executed) {
        await load(true);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.import"));
    } finally {
      setLoading("");
    }
  };

  const downloadColumnsXlsx = async (name: string) => {
    setLoading("table-export");
    setMessage("");
    try {
      const response = await fetch(`/api/nl2sql/db-admin/tables/${encodeURIComponent(name)}/export.xlsx`, {
        headers: {
          Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
      });
      if (!response.ok) {
        throw new Error(t("tableMgmt.error.export"));
      }
      downloadBlob(`${name.toLowerCase()}_columns.xlsx`, await response.blob());
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.export"));
    } finally {
      setLoading("");
    }
  };

  const openDropDialog = (name: string) => {
    setDropTargetName(name);
    setDropConfirmation("");
  };

  const dropTable = async () => {
    if (!dropTargetName) return;
    setLoading("drop");
    setMessage("");
    try {
      const result = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/drop-table", {
        table_name: dropTargetName,
        execute: true,
        confirmation: dropConfirmation,
        reason: "ui-table-management-drop",
      });
      if (result.executed) {
        setDropTargetName("");
        setDropConfirmation("");
        await load(true);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.drop"));
    } finally {
      setLoading("");
    }
  };

  const taskContent =
    activeView === "create" ? (
      <StatementRunnerCard
        policy="table_ddl"
        target="table"
        title={t("tableMgmt.create.title")}
        description={t("tableMgmt.create.note")}
        placeholder={t("tableMgmt.create.placeholder")}
        progress={({ hasSql }) => (
          <StepIndicator
            steps={[t("tableMgmt.create.stepSql"), t("tableMgmt.create.stepExecute")]}
            activeIndex={hasSql ? 1 : 0}
            ariaLabel={t("tableMgmt.create.steps")}
            dataTestId="table-create-steps"
          />
        )}
        confirmationTitle={t("tableMgmt.create.executeTitle")}
        executeOnly
        framed={false}
        onExecuted={() => load(true)}
      />
    ) : activeView === "import" ? (
      <ImportWizard
        table={importTable}
        sheet={importSheet}
        filename={importFilename}
        fileReady={Boolean(importBase64)}
        mode={importMode}
        result={importResult}
        step={importStep}
        confirmation={importConfirmation}
        loading={loading === "import-tabular"}
        onTableChange={(value) => {
          setImportTable(value);
          setImportResult(null);
          setImportStep("file");
        }}
        onSheetChange={(value) => {
          setImportSheet(value);
          setImportResult(null);
          setImportStep("file");
        }}
        onModeChange={(value) => {
          setImportMode(value);
          setImportResult(null);
          setImportStep("file");
        }}
        onFilePick={(file) => void pickImportFile(file)}
        onFileClear={clearImportFile}
        onExecute={() => void importTabular()}
        onConfirmationChange={(value) => {
          setImportConfirmation(value);
          if (value.trim()) setImportStep("execute");
        }}
      />
    ) : null;

  return (
    <>
      <PageHeader
        title={t("nav.tableManagement")}
        subtitle={t("tableMgmt.subtitle")}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {message && (
          <div className="flex flex-col gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 sm:flex-row sm:items-center sm:justify-between" role="alert">
            <span>{message} {t("tableMgmt.error.retryHint")}</span>
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("tableMgmt.action.refresh")}</span>
            </Button>
          </div>
        )}

        <StatusBar
          tableCount={tables?.items.length ?? 0}
          runtime={tables?.runtime ?? "deterministic"}
          refreshedAt={catalog?.refreshed_at ?? ""}
          loading={loading}
          onRefresh={() => void load()}
          onSchemaRefresh={() => void load(true)}
        />

        <TableManagementTabs activeView={activeView} onViewChange={setActiveView} />

        {activeView === "list" ? (
          <TableManagementPanelShell
            id="table-management-panel-list"
            labelledBy="table-management-tab-list"
            aria-label={t("tableMgmt.workspace.label")}
            className="xl:grid-cols-[minmax(28rem,0.9fr)_minmax(0,1.2fr)]"
          >
            <TableGrid
              items={filteredTables}
              selectedName={selectedTableName}
              loading={loading === "load" && !tables}
              search={tableSearch}
              filter={tableFilter}
              sort={tableSort}
              onSearchChange={setTableSearch}
              onFilterChange={setTableFilter}
              onSortChange={toggleSort}
              onSelect={(name) => void fetchDetail(name)}
              onDrop={openDropDialog}
            />
            <TableDetailPanel
              detail={detail}
              catalog={catalog}
              loading={loading.startsWith("detail-") || (loading === "load" && !detail)}
              exporting={loading === "table-export"}
              tab={detailTab}
              onTabChange={setDetailTab}
              onExport={(name) => void downloadColumnsXlsx(name)}
              onDrop={openDropDialog}
            />
          </TableManagementPanelShell>
        ) : (
          <TableManagementPanelShell
            id={`table-management-panel-${activeView}`}
            labelledBy={`table-management-tab-${activeView}`}
            aria-label={t("tableMgmt.toolbar.taskPanel")}
          >
            {taskContent}
          </TableManagementPanelShell>
        )}
      </main>

      {dropTargetName && (
        <DropTableDialog
          tableName={dropTargetName}
          confirmation={dropConfirmation}
          loading={loading === "drop"}
          onConfirmationChange={setDropConfirmation}
          onExecute={() => void dropTable()}
          onClose={() => setDropTargetName("")}
        />
      )}
    </>
  );
}
