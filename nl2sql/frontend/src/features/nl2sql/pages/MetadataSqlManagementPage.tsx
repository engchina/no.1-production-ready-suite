import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownUp,
  Code2,
  FileText,
  RefreshCw,
  Search,
  Table2,
  Wand2,
} from "lucide-react";

import {
  Button,
  EmptyState,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  DbObjectStatusBar,
  DbObjectStepIndicator,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { StatementRunnerCard } from "../components/DbAdminShared";
import { buildMetadataInputTexts } from "../metadataSql";
import type {
  DbAdminObjectDetail,
  DbAdminObjectsData,
  DbAdminObjectSummary,
  DbAdminStatementPolicy,
  MetadataSqlGenerateData,
  MetadataSqlGeneratePayload,
  MetadataSqlSampleData,
  MetadataSqlSamplePayload,
  MetadataSqlTarget,
  SchemaCatalog,
} from "../types";

type MetadataMode = "comment" | "annotation";
type ActiveView = "targets" | "input" | "execute";
type TargetFilter = "all" | "table" | "view";
type TargetSortKey = "name" | "object_type" | "owner";
type TargetSortDirection = "asc" | "desc";

interface TargetSortState {
  key: TargetSortKey;
  direction: TargetSortDirection;
}

interface MetadataTargetItem extends MetadataSqlTarget {
  key: string;
  owner: string;
  row_count?: number | null;
  comment: string;
}

const ANNOTATION_EXTRA_TEXT =
  "ANNOTATIONSの安全な適用ガイド:\n" +
  "- DROPとADDは同一文で混在させず、別々のALTER文に分割\n" +
  "- 重複名を避けるため、可能ならADD IF NOT EXISTSを使う\n" +
  "- COMMENT: は入力項目名であり、説明用annotation名にはUI_Displayを使う\n" +
  "- 値内の'は''へエスケープし、予約語や空白を含むannotation名は二重引用符で囲む\n" +
  "例(表): ALTER TABLE USERS ANNOTATIONS (ADD IF NOT EXISTS UI_Display 'Users');\n" +
  "例(列): ALTER TABLE USERS MODIFY (ID ANNOTATIONS (ADD IF NOT EXISTS UI_Display 'ID'));";

export function CommentManagementPage() {
  return <MetadataSqlManagementPage mode="comment" />;
}

export function AnnotationManagementPage() {
  return <MetadataSqlManagementPage mode="annotation" />;
}

function MetadataSqlManagementPage({ mode }: { mode: MetadataMode }) {
  const pageId = mode === "comment" ? "comment-management" : "annotation-management";
  const [tables, setTables] = useState<DbAdminObjectsData | null>(null);
  const [views, setViews] = useState<DbAdminObjectsData | null>(null);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [details, setDetails] = useState<DbAdminObjectDetail[]>([]);
  const [sampleLimit, setSampleLimit] = useState(10);
  const [refreshedSampleText, setRefreshedSampleText] = useState<string | null>(null);
  const [extraText, setExtraText] = useState(mode === "annotation" ? ANNOTATION_EXTRA_TEXT : "");
  const [generated, setGenerated] = useState<MetadataSqlGenerateData | null>(null);
  const [activeView, setActiveView] = useState<ActiveView>("targets");
  const [targetSearch, setTargetSearch] = useState("");
  const [targetFilter, setTargetFilter] = useState<TargetFilter>("all");
  const [targetSort, setTargetSort] = useState<TargetSortState>({ key: "name", direction: "asc" });
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const allTargets = useMemo(
    () => [
      ...targetItemsFromObjects(tables?.items ?? [], "table"),
      ...targetItemsFromObjects(views?.items ?? [], "view"),
    ],
    [tables, views]
  );
  const selectedTargets = useMemo(
    () => selectedKeys.map((key) => targetFromKey(key)).filter(Boolean) as MetadataSqlTarget[],
    [selectedKeys]
  );
  const inputTexts = useMemo(
    () => buildMetadataInputTexts(details, catalog, sampleLimit),
    [catalog, details, sampleLimit]
  );
  const policy: DbAdminStatementPolicy = mode === "comment" ? "comment_sql" : "annotation_sql";
  const tabs: Array<DbObjectTab<ActiveView>> = [
    { id: "targets", label: t("metadataSql.tabs.targets"), icon: Table2 },
    { id: "input", label: t("metadataSql.tabs.input"), icon: FileText },
    { id: "execute", label: t("metadataSql.tabs.execute"), icon: Code2 },
  ];

  const filteredTargets = useMemo(() => {
    const q = targetSearch.trim().toLowerCase();
    return allTargets
      .filter((item) => {
        if (targetFilter !== "all" && item.object_type !== targetFilter) return false;
        if (!q) return true;
        return (
          item.object_name.toLowerCase().includes(q) ||
          item.owner.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q) ||
          targetTypeLabel(item.object_type).toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = targetSortValue(left, targetSort.key);
        const b = targetSortValue(right, targetSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return targetSort.direction === "asc" ? result : -result;
      });
  }, [allTargets, targetFilter, targetSearch, targetSort]);

  const load = async (refreshSchema = false) => {
    setLoading(refreshSchema ? "schema-refresh" : "load");
    setMessage("");
    try {
      const [tableData, viewData, catalogData] = await Promise.all([
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
        refreshSchema
          ? apiPost<SchemaCatalog>("/api/schema/refresh")
          : apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setTables(tableData);
      setViews(viewData);
      setCatalog(catalogData);
      const availableKeys = new Set([
        ...targetItemsFromObjects(tableData.items, "table").map((item) => item.key),
        ...targetItemsFromObjects(viewData.items, "view").map((item) => item.key),
      ]);
      setSelectedKeys((current) => current.filter((key) => availableKeys.has(key)));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("metadataSql.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const toggleTarget = (target: MetadataSqlTarget) => {
    const key = targetKey(target);
    setSelectedKeys((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );
    setDetails([]);
    setRefreshedSampleText(null);
    setGenerated(null);
  };

  const toggleSort = (key: TargetSortKey) => {
    setTargetSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const fetchDetails = async () => {
    if (selectedTargets.length === 0) {
      setMessage(t("metadataSql.error.noTarget"));
      return;
    }
    setLoading("details");
    setMessage("");
    try {
      const nextDetails = await Promise.all(
        selectedTargets.map((target) =>
          apiGet<DbAdminObjectDetail>(
            target.object_type === "view"
              ? `/api/nl2sql/db-admin/views/${encodeURIComponent(target.object_name)}`
              : `/api/nl2sql/db-admin/tables/${encodeURIComponent(target.object_name)}`
          )
        )
      );
      setDetails(nextDetails);
      setRefreshedSampleText(null);
      setGenerated(null);
      setActiveView("input");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("metadataSql.error.details"));
    } finally {
      setLoading("");
    }
  };

  const generateSql = async () => {
    if (selectedTargets.length === 0) {
      setMessage(t("metadataSql.error.noTarget"));
      return;
    }
    setLoading("generate");
    setMessage("");
    try {
      const samplePayload: MetadataSqlSamplePayload = {
        targets: details.map((detail) => ({
          object_name: detail.name,
          object_type: detail.object_type === "view" ? "view" : "table",
          columns: detail.columns.map((column) => column.column_name),
        })),
        sample_limit: sampleLimit,
      };
      const samples = await apiPost<MetadataSqlSampleData>("/api/nl2sql/metadata-samples", samplePayload);
      setRefreshedSampleText(samples.sample_text);
      const payload: MetadataSqlGeneratePayload = {
        targets: selectedTargets,
        structure_text: inputTexts.structureText,
        primary_key_text: inputTexts.primaryKeyText,
        foreign_key_text: inputTexts.foreignKeyText,
        sample_text: samples.sample_text,
        extra_text: extraText,
      };
      const path =
        mode === "comment"
          ? "/api/nl2sql/comments/generate-sql"
          : "/api/nl2sql/annotations/generate-sql";
      const generatedSql = await apiPost<MetadataSqlGenerateData>(path, payload);
      setGenerated({ ...generatedSql, warnings: [...samples.warnings, ...generatedSql.warnings] });
      setActiveView("execute");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("metadataSql.error.generate"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader
        title={t(mode === "comment" ? "nav.commentManagement" : "nav.annotationManagement")}
        subtitle={t(
          mode === "comment"
            ? "metadataSql.comment.subtitle"
            : "metadataSql.annotation.subtitle"
        )}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {message && (
          <div
            className="flex flex-col gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 sm:flex-row sm:items-center sm:justify-between"
            role="alert"
          >
            <span>
              {message} {t("metadataSql.error.retryHint")}
            </span>
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("tableMgmt.action.refresh")}</span>
            </Button>
          </div>
        )}

        <DbObjectStatusBar
          count={allTargets.length}
          runtime={metadataRuntime(tables, views)}
          refreshedAt={catalog?.refreshed_at ?? ""}
          loading={loading}
          labels={{
            ariaLabel: t("metadataSql.status.label"),
            count: t("metadataSql.status.targets"),
            runtime: t("tableMgmt.metric.runtime"),
            refreshedAt: t("tableMgmt.metric.schemaRefreshed"),
            refresh: t("tableMgmt.action.refresh"),
            schemaRefresh: t("tableMgmt.action.schemaRefresh"),
          }}
          onRefresh={() => void load()}
          onSchemaRefresh={() => void load(true)}
        />

        <DbObjectManagementTabs
          idPrefix={pageId}
          tabs={tabs}
          activeView={activeView}
          ariaLabel={t("metadataSql.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "targets" ? (
          <DbObjectManagementPanelShell
            id={`${pageId}-panel-targets`}
            labelledBy={`${pageId}-tab-targets`}
            idPrefix={pageId}
            ariaLabel={t("metadataSql.workspace.targets")}
          >
            <MetadataTargetGrid
              pageId={pageId}
              items={filteredTargets}
              selectedKeys={selectedKeys}
              loading={loading === "load" && !tables && !views}
              search={targetSearch}
              filter={targetFilter}
              sort={targetSort}
              onSearchChange={setTargetSearch}
              onFilterChange={setTargetFilter}
              onSortChange={toggleSort}
              onToggle={toggleTarget}
              onFetchDetails={() => void fetchDetails()}
              fetchingDetails={loading === "details"}
            />
          </DbObjectManagementPanelShell>
        ) : activeView === "input" ? (
          <DbObjectManagementPanelShell
            id={`${pageId}-panel-input`}
            labelledBy={`${pageId}-tab-input`}
            idPrefix={pageId}
            ariaLabel={t("metadataSql.workspace.input")}
          >
            <MetadataInputPanel
              pageId={pageId}
              inputTexts={inputTexts}
              detailsReady={details.length > 0}
              selectedCount={selectedTargets.length}
              sampleLimit={sampleLimit}
              sampleText={refreshedSampleText ?? inputTexts.sampleText}
              extraText={extraText}
              loading={loading === "generate"}
              onSampleLimitChange={(value) => {
                setSampleLimit(value);
                setRefreshedSampleText(null);
              }}
              onExtraTextChange={setExtraText}
              onGenerate={() => void generateSql()}
            />
          </DbObjectManagementPanelShell>
        ) : (
          <DbObjectManagementPanelShell
            id={`${pageId}-panel-execute`}
            labelledBy={`${pageId}-tab-execute`}
            idPrefix={pageId}
            ariaLabel={t("metadataSql.workspace.execute")}
          >
            <MetadataExecutePanel
              pageId={pageId}
              mode={mode}
              generated={generated}
              policy={policy}
              onExecuted={() => load(true)}
            />
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}

function MetadataTargetGrid({
  pageId,
  items,
  selectedKeys,
  loading,
  search,
  filter,
  sort,
  fetchingDetails,
  onSearchChange,
  onFilterChange,
  onSortChange,
  onToggle,
  onFetchDetails,
}: {
  pageId: string;
  items: MetadataTargetItem[];
  selectedKeys: string[];
  loading: boolean;
  search: string;
  filter: TargetFilter;
  sort: TargetSortState;
  fetchingDetails: boolean;
  onSearchChange: (value: string) => void;
  onFilterChange: (value: TargetFilter) => void;
  onSortChange: (key: TargetSortKey) => void;
  onToggle: (target: MetadataSqlTarget) => void;
  onFetchDetails: () => void;
}) {
  const hasActiveFilter = Boolean(search.trim()) || filter !== "all";
  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);

  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby={`${pageId}-targets-heading`}>
      <DbObjectPanelHeader
        headingId={`${pageId}-targets-heading`}
        icon={Table2}
        title={t("metadataSql.targets.title")}
        description={t("metadataSql.targets.hint")}
        action={
          <>
            <StatusBadge variant="info" label={t("metadataSql.targets.selected", { count: selectedKeys.length })} />
            <Button
              type="button"
              variant="primary"
              size="sm"
              loading={fetchingDetails}
              disabled={selectedKeys.length === 0}
              onClick={onFetchDetails}
            >
              <span>{t("metadataSql.action.fetchInfo")}</span>
            </Button>
          </>
        }
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
                placeholder={t("metadataSql.targets.searchPlaceholder")}
              />
            </span>
          </label>
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("metadataSql.targets.typeFilter")}</span>
            <select
              value={filter}
              onChange={(event) => onFilterChange(event.currentTarget.value as TargetFilter)}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
            >
              <option value="all">{t("metadataSql.targets.typeFilterAll")}</option>
              <option value="table">{t("metadataSql.targets.typeFilterTables")}</option>
              <option value="view">{t("metadataSql.targets.typeFilterViews")}</option>
            </select>
          </label>
        </div>
      </div>

      {loading ? (
        <MetadataTargetListSkeleton pageId={pageId} />
      ) : items.length === 0 ? (
        <EmptyState
          title={hasActiveFilter ? t("metadataSql.targets.noResultsTitle") : t("metadataSql.targets.emptyTitle")}
          hint={hasActiveFilter ? t("metadataSql.targets.noResultsHint") : t("metadataSql.targets.emptyHint")}
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          <div className="max-h-[42rem] overflow-auto" data-testid="db-admin-object-list">
            <table className="w-full min-w-[42rem] table-fixed divide-y divide-slate-200 text-left text-sm" data-testid={`${pageId}-target-grid`}>
              <colgroup>
                <col className="w-[16rem]" />
                <col className="w-[6rem]" />
                <col className="w-[7rem]" />
                <col />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-slate-50 text-xs text-slate-600">
                <tr>
                  <th className="whitespace-nowrap px-3 py-2">
                    <TargetSortButton label={t("metadataSql.targets.grid.objectName")} sortKey="name" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="whitespace-nowrap px-3 py-2">
                    <TargetSortButton label={t("metadataSql.targets.grid.type")} sortKey="object_type" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="whitespace-nowrap px-3 py-2">
                    <TargetSortButton label={t("metadataSql.targets.grid.owner")} sortKey="owner" sort={sort} onToggle={onSortChange} />
                  </th>
                  <th className="whitespace-nowrap px-3 py-2">{t("metadataSql.targets.grid.comment")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => {
                  const selected = selectedSet.has(item.key);
                  return (
                    <tr key={item.key} className={selected ? "bg-sky-50" : "hover:bg-slate-50"}>
                      <td className="px-3 py-2 align-top">
                        <label className="flex min-h-11 cursor-pointer items-start gap-3 text-slate-800">
                          <input
                            type="checkbox"
                            checked={selected}
                            onChange={() => onToggle(item)}
                            className="mt-1 h-4 w-4 shrink-0 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                          />
                          <span className="min-w-0">
                            <span className="block break-all font-mono text-xs font-semibold text-sky-800">
                              {item.object_name}
                            </span>
                            <span className="mt-1 block text-xs text-slate-500">
                              {t("metadataSql.targets.grid.toggleHint")}
                            </span>
                          </span>
                        </label>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 align-top">
                        <StatusBadge variant="neutral" label={targetTypeLabel(item.object_type)} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 align-top font-mono text-xs text-slate-600">
                        {item.owner || "-"}
                      </td>
                      <td className="break-words px-3 py-2 align-top text-sm text-slate-700">
                        {item.comment || "-"}
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

function MetadataInputPanel({
  pageId,
  inputTexts,
  detailsReady,
  selectedCount,
  sampleLimit,
  sampleText,
  extraText,
  loading,
  onSampleLimitChange,
  onExtraTextChange,
  onGenerate,
}: {
  pageId: string;
  inputTexts: ReturnType<typeof buildMetadataInputTexts>;
  detailsReady: boolean;
  selectedCount: number;
  sampleLimit: number;
  sampleText: string;
  extraText: string;
  loading: boolean;
  onSampleLimitChange: (value: number) => void;
  onExtraTextChange: (value: string) => void;
  onGenerate: () => void;
}) {
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={FileText}
        title={t("metadataSql.input.title")}
        description={t("metadataSql.input.hint")}
        action={
          <Button
            type="button"
            variant="primary"
            size="sm"
            className="w-full sm:w-auto"
            loading={loading}
            disabled={!detailsReady}
            onClick={onGenerate}
          >
            <Wand2 size={15} aria-hidden="true" />
            <span>{t("metadataSql.action.generate")}</span>
          </Button>
        }
      />

      <DbObjectStepIndicator
        steps={[
          t("metadataSql.steps.targets"),
          t("metadataSql.steps.input"),
          t("metadataSql.steps.execute"),
        ]}
        activeIndex={detailsReady ? 1 : 0}
        ariaLabel={t("metadataSql.steps.label")}
        dataTestId={`${pageId}-input-steps`}
      />

      {!detailsReady && (
        <EmptyState title={t("metadataSql.input.emptyTitle")} hint={t("metadataSql.input.emptyHint")} />
      )}

      <div className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <label className="grid min-w-0 gap-1 text-sm font-medium text-slate-800 sm:w-44">
            <span>{t("metadataSql.input.sampleLimit")}</span>
            <input
              type="number"
              min={0}
              max={100}
              value={sampleLimit}
              onChange={(event) => {
                const value = Number(event.currentTarget.value);
                onSampleLimitChange(Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0);
              }}
              className="min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
            />
          </label>
          <StatusBadge variant={detailsReady ? "info" : "neutral"} label={t("metadataSql.targets.selected", { count: selectedCount })} />
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <MetadataTextarea label={t("metadataSql.input.structure")} value={inputTexts.structureText} rows={8} />
        <MetadataTextarea label={t("metadataSql.input.sample")} value={sampleText} rows={8} />
        <MetadataTextarea label={t("metadataSql.input.pk")} value={inputTexts.primaryKeyText} rows={5} />
        <MetadataTextarea label={t("metadataSql.input.fk")} value={inputTexts.foreignKeyText} rows={5} />
      </div>

      <label className="grid gap-1 text-sm font-medium text-slate-800">
        <span>{t("metadataSql.input.extra")}</span>
        <textarea
          value={extraText}
          onChange={(event) => onExtraTextChange(event.currentTarget.value)}
          rows={6}
          className="min-h-32 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
        />
      </label>
    </div>
  );
}

function MetadataExecutePanel({
  pageId,
  mode,
  generated,
  policy,
  onExecuted,
}: {
  pageId: string;
  mode: MetadataMode;
  generated: MetadataSqlGenerateData | null;
  policy: DbAdminStatementPolicy;
  onExecuted: () => Promise<void>;
}) {
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Code2}
        title={t("metadataSql.execute.title")}
        description={t("metadataSql.execute.hint")}
        action={
          generated ? (
            <StatusBadge variant={generated.source === "oci_enterprise_ai" ? "success" : "neutral"} label={generated.source} />
          ) : null
        }
      />

      <DbObjectStepIndicator
        steps={[
          t("metadataSql.steps.targets"),
          t("metadataSql.steps.input"),
          t("metadataSql.steps.execute"),
        ]}
        activeIndex={generated ? 2 : 1}
        ariaLabel={t("metadataSql.steps.label")}
        dataTestId={`${pageId}-execute-steps`}
      />

      {!generated && (
        <EmptyState title={t("metadataSql.execute.emptyTitle")} hint={t("metadataSql.execute.emptyHint")} />
      )}

      {generated?.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {warning}
        </p>
      ))}

      <StatementRunnerCard
        policy={policy}
        title={t(mode === "comment" ? "metadataSql.comment.runner" : "metadataSql.annotation.runner")}
        placeholder={t(
          mode === "comment"
            ? "metadataSql.comment.placeholder"
            : "metadataSql.annotation.placeholder"
        )}
        initialSql={generated?.sql ?? ""}
        executeOnly
        framed={false}
        onExecuted={onExecuted}
      />
    </div>
  );
}

function MetadataTextarea({ label, value, rows }: { label: string; value: string; rows: number }) {
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-slate-800">
      <span>{label}</span>
      <textarea
        readOnly
        value={value}
        rows={rows}
        className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800"
      />
    </label>
  );
}

function TargetSortButton({
  label,
  sortKey,
  sort,
  onToggle,
}: {
  label: string;
  sortKey: TargetSortKey;
  sort: TargetSortState;
  onToggle: (key: TargetSortKey) => void;
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

function MetadataTargetListSkeleton({ pageId }: { pageId: string }) {
  return (
    <div className="grid gap-2" data-testid={`${pageId}-target-list-skeleton`}>
      <SkeletonBlock className="h-11" />
      {Array.from({ length: 8 }, (_, index) => (
        <SkeletonBlock key={index} className="h-12" />
      ))}
    </div>
  );
}

function SkeletonBlock({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-slate-100 ${className}`} aria-hidden="true" />;
}

function targetItemsFromObjects(items: DbAdminObjectSummary[], objectType: MetadataSqlTarget["object_type"]) {
  return items.map((item): MetadataTargetItem => {
    const target: MetadataSqlTarget = {
      object_name: item.name,
      object_type: objectType,
    };
    return {
      ...target,
      key: targetKey(target),
      owner: item.owner,
      row_count: item.row_count,
      comment: item.comment,
    };
  });
}

function targetSortValue(item: MetadataTargetItem, key: TargetSortKey) {
  if (key === "object_type") return item.object_type;
  if (key === "owner") return item.owner.toLowerCase();
  return item.object_name.toLowerCase();
}

function targetTypeLabel(objectType: MetadataSqlTarget["object_type"]) {
  return objectType === "view" ? t("metadataSql.targets.type.view") : t("metadataSql.targets.type.table");
}

function metadataRuntime(tables: DbAdminObjectsData | null, views: DbAdminObjectsData | null) {
  if (tables?.runtime && views?.runtime && tables.runtime !== views.runtime) {
    return `${tables.runtime} / ${views.runtime}`;
  }
  return tables?.runtime ?? views?.runtime ?? "deterministic";
}

function targetKey(target: MetadataSqlTarget) {
  return `${target.object_type}:${target.object_name}`;
}

function targetFromKey(key: string): MetadataSqlTarget | null {
  const [objectType, ...nameParts] = key.split(":");
  const objectName = nameParts.join(":");
  if ((objectType !== "table" && objectType !== "view") || !objectName) return null;
  return { object_name: objectName, object_type: objectType };
}
