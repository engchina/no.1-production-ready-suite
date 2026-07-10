import { useEffect, useMemo, useState } from "react";
import { Code2, Eye, RefreshCw, Sparkles } from "lucide-react";

import { Button, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  DbObjectDetailPanel,
  DbObjectGrid,
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  DbObjectStatusBar,
  DbObjectStepIndicator,
  DropDbObjectDialog,
  dbObjectSortValue,
  type DbObjectDetailTab,
  type DbObjectFilter,
  type DbObjectSortKey,
  type DbObjectSortState,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { StatementRunnerCard } from "../components/DbAdminShared";
import type {
  DbAdminExecuteData,
  DbAdminJoinWhereData,
  DbAdminJoinWherePromptProfile,
  DbAdminObjectDetail,
  DbAdminObjectsData,
  SchemaCatalog,
} from "../types";

type ActiveView = "list" | "create" | "joinWhere";

const VIEW_MANAGEMENT_ID = "view-management";
const JOIN_WHERE_PROMPT_PROFILES: DbAdminJoinWherePromptProfile[] = [
  "join_where_strict",
  "sql_structure",
];

function joinWherePromptProfileLabel(profile: DbAdminJoinWherePromptProfile) {
  return profile === "sql_structure"
    ? t("viewMgmt.joinWhere.profile.sqlStructure")
    : t("viewMgmt.joinWhere.profile.strict");
}

function joinWherePromptProfileDescription(profile: DbAdminJoinWherePromptProfile) {
  return profile === "sql_structure"
    ? t("viewMgmt.joinWhere.profile.sqlStructureHint")
    : t("viewMgmt.joinWhere.profile.strictHint");
}

function ViewJoinWherePanel({
  detail,
  result,
  loading,
  profile,
  onProfileChange,
  onExtract,
}: {
  detail: DbAdminObjectDetail | null;
  result: DbAdminJoinWhereData | null;
  loading: boolean;
  profile: DbAdminJoinWherePromptProfile;
  onProfileChange: (profile: DbAdminJoinWherePromptProfile) => void;
  onExtract: () => void;
}) {
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Sparkles}
        title={t("viewMgmt.joinWhere.title")}
        description={t("viewMgmt.joinWhere.subtitle")}
        action={
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="w-full sm:w-auto"
            loading={loading}
            disabled={!detail?.ddl}
            onClick={onExtract}
          >
            <Sparkles size={15} aria-hidden="true" />
            <span>{t("viewMgmt.joinWhere.extract")}</span>
          </Button>
        }
      />

      <DbObjectStepIndicator
        steps={[t("viewMgmt.joinWhere.stepSelect"), t("viewMgmt.joinWhere.stepExtract")]}
        activeIndex={result ? 1 : detail ? 0 : -1}
        ariaLabel={t("viewMgmt.joinWhere.steps")}
        dataTestId="view-join-where-steps"
      />

      {detail ? (
        <section
          className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3"
          data-testid="view-join-where-selected-view"
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge variant="neutral" label={detail.object_type} />
            <StatusBadge variant="info" label={detail.name} />
          </div>
          <p className="text-sm text-slate-600">{t("viewMgmt.joinWhere.selectedHint")}</p>
        </section>
      ) : (
        <EmptyState title={t("viewMgmt.joinWhere.emptyTitle")} hint={t("viewMgmt.joinWhere.empty")} />
      )}

      <fieldset className="grid gap-2">
        <legend className="text-sm font-semibold text-slate-800">
          {t("viewMgmt.joinWhere.profileLabel")}
        </legend>
        <div
          role="radiogroup"
          aria-label={t("viewMgmt.joinWhere.profileAria")}
          className="grid gap-2 sm:grid-cols-2"
        >
          {JOIN_WHERE_PROMPT_PROFILES.map((option) => {
            const selected = profile === option;
            return (
              <label
                key={option}
                className={[
                  "flex min-h-24 cursor-pointer gap-3 rounded-md border p-3 text-left transition-colors",
                  selected
                    ? "border-blue-300 bg-blue-50 text-blue-950"
                    : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                ].join(" ")}
              >
                <input
                  type="radio"
                  name="view-join-where-prompt-profile"
                  value={option}
                  checked={selected}
                  onChange={() => onProfileChange(option)}
                  className="mt-1 h-4 w-4 flex-none accent-blue-600"
                />
                <span className="grid min-w-0 gap-1">
                  <span className="text-sm font-semibold">
                    {joinWherePromptProfileLabel(option)}
                  </span>
                  <span className="text-xs leading-5 text-slate-600">
                    {joinWherePromptProfileDescription(option)}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {result && (
        <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm" aria-label={t("viewMgmt.joinWhere.result")}>
          <div className="flex flex-wrap gap-2">
            <StatusBadge variant={result.source === "oci_enterprise_ai" ? "success" : "neutral"} label={result.source} />
            <StatusBadge
              variant="info"
              label={joinWherePromptProfileLabel(result.prompt_profile ?? "join_where_strict")}
            />
          </div>
          {result.warnings.map((warning) => (
            <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
              {warning}
            </p>
          ))}
          <div className="grid gap-3 lg:grid-cols-2">
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("viewMgmt.joinWhere.join")}</span>
              <textarea
                readOnly
                value={result.join_text}
                rows={5}
                className="min-h-32 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("viewMgmt.joinWhere.where")}</span>
              <textarea
                readOnly
                value={result.where_text}
                rows={5}
                className="min-h-32 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
              />
            </label>
          </div>
          {result.structure_markdown ? (
            <details className="rounded-md border border-slate-200 bg-white p-3">
              <summary className="cursor-pointer text-sm font-semibold text-slate-800">
                {t("viewMgmt.joinWhere.structureResult")}
              </summary>
              <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-200 bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-50">
                {result.structure_markdown}
              </pre>
            </details>
          ) : null}
        </section>
      )}
    </div>
  );
}

export function ViewManagementPage() {
  const [views, setViews] = useState<DbAdminObjectsData | null>(null);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [selectedViewName, setSelectedViewName] = useState("");
  const [detail, setDetail] = useState<DbAdminObjectDetail | null>(null);
  const [detailTab, setDetailTab] = useState<DbObjectDetailTab>("columns");
  const [activeView, setActiveView] = useState<ActiveView>("list");
  const [viewSearch, setViewSearch] = useState("");
  const [viewFilter, setViewFilter] = useState<DbObjectFilter>("all");
  const [viewSort, setViewSort] = useState<DbObjectSortState>({ key: "name", direction: "asc" });
  const [dropTargetName, setDropTargetName] = useState("");
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [joinWhereProfile, setJoinWhereProfile] =
    useState<DbAdminJoinWherePromptProfile>("join_where_strict");
  const [joinWhere, setJoinWhere] = useState<DbAdminJoinWhereData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const fetchDetail = async (name: string) => {
    setLoading(`detail-${name}`);
    setMessage("");
    setSelectedViewName(name);
    setDetail(null);
    setDetailTab("columns");
    setJoinWhere(null);
    try {
      setDetail(await apiGet<DbAdminObjectDetail>(`/api/nl2sql/db-admin/views/${encodeURIComponent(name)}`));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  const load = async (refreshSchema = false) => {
    setLoading(refreshSchema ? "schema-refresh" : "load");
    setMessage("");
    try {
      const [viewData, catalogData] = await Promise.all([
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
        refreshSchema ? apiPost<SchemaCatalog>("/api/schema/refresh") : apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setViews(viewData);
      setCatalog(catalogData);
      const nextSelected =
        viewData.items.find((item) => item.name === selectedViewName)?.name || viewData.items[0]?.name || "";
      setSelectedViewName(nextSelected);
      setJoinWhere(null);
      if (nextSelected) {
        setDetail(await apiGet<DbAdminObjectDetail>(`/api/nl2sql/db-admin/views/${encodeURIComponent(nextSelected)}`));
      } else {
        setDetail(null);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const filteredViews = useMemo(() => {
    const q = viewSearch.trim().toLowerCase();
    return (views?.items ?? [])
      .filter((item) => {
        if (viewFilter === "with_rows" && !(item.row_count != null && item.row_count > 0)) return false;
        if (viewFilter === "empty_rows" && item.row_count !== 0) return false;
        if (!q) return true;
        return (
          item.name.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q) ||
          item.owner.toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = dbObjectSortValue(left, viewSort.key);
        const b = dbObjectSortValue(right, viewSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return viewSort.direction === "asc" ? result : -result;
      });
  }, [views, viewSearch, viewFilter, viewSort]);

  const toggleSort = (key: DbObjectSortKey) => {
    setViewSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const openDropDialog = (name: string) => {
    setDropTargetName(name);
    setDropConfirmation("");
  };

  const dropView = async () => {
    if (!dropTargetName) return;
    setLoading("drop");
    setMessage("");
    try {
      const result = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/drop-view", {
        view_name: dropTargetName,
        execute: true,
        confirmation: dropConfirmation,
        reason: "ui-view-management-drop",
      });
      if (result.executed) {
        setDropTargetName("");
        setDropConfirmation("");
        await load(true);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.drop"));
    } finally {
      setLoading("");
    }
  };

  const extractJoinWhere = async () => {
    if (!detail?.ddl) return;
    setLoading("join-where");
    setMessage("");
    try {
      setJoinWhere(
        await apiPost<DbAdminJoinWhereData>("/api/nl2sql/db-admin/extract-join-where", {
          ddl: detail.ddl,
          prompt_profile: joinWhereProfile,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.extract"));
    } finally {
      setLoading("");
    }
  };

  const tabs: Array<DbObjectTab<ActiveView>> = [
    { id: "list", label: t("viewMgmt.list.title"), icon: Eye },
    { id: "create", label: t("viewMgmt.create.title"), icon: Code2 },
    { id: "joinWhere", label: t("viewMgmt.joinWhere.title"), icon: Sparkles },
  ];

  const taskContent =
    activeView === "create" ? (
      <StatementRunnerCard
        policy="view_ddl"
        title={t("viewMgmt.create.title")}
        description={t("viewMgmt.create.note")}
        placeholder={t("viewMgmt.create.placeholder")}
        progress={({ hasSql }) => (
          <DbObjectStepIndicator
            steps={[t("viewMgmt.create.stepSql"), t("viewMgmt.create.stepExecute")]}
            activeIndex={hasSql ? 1 : 0}
            ariaLabel={t("viewMgmt.create.steps")}
            dataTestId="view-create-steps"
          />
        )}
        confirmationTitle={t("viewMgmt.create.executeTitle")}
        executeOnly
        framed={false}
        onExecuted={() => load(true)}
      />
    ) : activeView === "joinWhere" ? (
      <ViewJoinWherePanel
        detail={detail}
        result={joinWhere}
        loading={loading === "join-where"}
        profile={joinWhereProfile}
        onProfileChange={(nextProfile) => {
          setJoinWhereProfile(nextProfile);
          setJoinWhere(null);
        }}
        onExtract={() => void extractJoinWhere()}
      />
    ) : null;

  return (
    <>
      <PageHeader title={t("nav.viewManagement")} subtitle={t("viewMgmt.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        {message && (
          <div
            className="flex flex-col gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 sm:flex-row sm:items-center sm:justify-between"
            role="alert"
          >
            <span>
              {message} {t("viewMgmt.error.retryHint")}
            </span>
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("viewMgmt.action.refresh")}</span>
            </Button>
          </div>
        )}

        <DbObjectStatusBar
          count={views?.items.length ?? 0}
          runtime={views?.runtime ?? "deterministic"}
          refreshedAt={catalog?.refreshed_at ?? ""}
          loading={loading}
          labels={{
            ariaLabel: t("viewMgmt.toolbar.status"),
            count: t("viewMgmt.metric.views"),
            runtime: t("tableMgmt.metric.runtime"),
            refreshedAt: t("tableMgmt.metric.schemaRefreshed"),
            refresh: t("viewMgmt.action.refresh"),
            schemaRefresh: t("viewMgmt.action.schemaRefresh"),
          }}
          onRefresh={() => void load()}
          onSchemaRefresh={() => void load(true)}
        />

        <DbObjectManagementTabs
          idPrefix={VIEW_MANAGEMENT_ID}
          tabs={tabs}
          activeView={activeView}
          ariaLabel={t("viewMgmt.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "list" ? (
          <DbObjectManagementPanelShell
            id="view-management-panel-list"
            labelledBy="view-management-tab-list"
            idPrefix={VIEW_MANAGEMENT_ID}
            ariaLabel={t("viewMgmt.workspace.label")}
            className="xl:grid-cols-[minmax(28rem,0.9fr)_minmax(0,1.2fr)]"
          >
            <DbObjectGrid
              idPrefix={VIEW_MANAGEMENT_ID}
              headingId="view-grid-heading"
              icon={Eye}
              items={filteredViews}
              selectedName={selectedViewName}
              loading={loading === "load" && !views}
              search={viewSearch}
              filter={viewFilter}
              sort={viewSort}
              labels={{
                title: t("viewMgmt.list.title"),
                hint: t("viewMgmt.grid.hint"),
                count: t("viewMgmt.grid.count", { count: filteredViews.length }),
                emptyTitle: t("viewMgmt.list.emptyTitle"),
                emptyHint: t("viewMgmt.list.emptyHint"),
                noResultsTitle: t("viewMgmt.list.noResultsTitle"),
                noResultsHint: t("viewMgmt.list.noResultsHint"),
                filter: t("viewMgmt.toolbar.filter"),
                filterAll: t("viewMgmt.toolbar.filterAll"),
                filterWithRows: t("viewMgmt.toolbar.filterWithRows"),
                filterEmptyRows: t("viewMgmt.toolbar.filterEmptyRows"),
                objectName: t("viewMgmt.grid.viewName"),
                rows: t("viewMgmt.grid.rows"),
                owner: t("viewMgmt.grid.owner"),
                actions: t("viewMgmt.grid.actions"),
                detail: t("viewMgmt.grid.detail"),
                drop: t("viewMgmt.grid.drop"),
                showObject: (name) => t("viewMgmt.grid.showView", { name }),
              }}
              onSearchChange={setViewSearch}
              onFilterChange={setViewFilter}
              onSortChange={toggleSort}
              onSelect={(name) => void fetchDetail(name)}
              onDrop={openDropDialog}
            />
            <DbObjectDetailPanel
              idPrefix={VIEW_MANAGEMENT_ID}
              headingId="view-detail-heading"
              detail={detail}
              catalog={catalog}
              loading={loading.startsWith("detail-") || (loading === "load" && !detail)}
              tab={detailTab}
              labels={{
                tabsLabel: t("viewMgmt.detailTabs.label"),
                columns: t("viewMgmt.detailTabs.columns"),
                ddl: t("viewMgmt.detailTabs.ddl"),
                drop: t("viewMgmt.grid.drop"),
              }}
              onTabChange={setDetailTab}
              onDrop={openDropDialog}
            />
          </DbObjectManagementPanelShell>
        ) : (
          <DbObjectManagementPanelShell
            id={`view-management-panel-${activeView}`}
            labelledBy={`view-management-tab-${activeView}`}
            idPrefix={VIEW_MANAGEMENT_ID}
            ariaLabel={t("viewMgmt.toolbar.taskPanel")}
          >
            {taskContent}
          </DbObjectManagementPanelShell>
        )}
      </main>

      {dropTargetName && (
        <DropDbObjectDialog
          objectName={dropTargetName}
          confirmation={dropConfirmation}
          loading={loading === "drop"}
          labels={{
            title: t("viewMgmt.dropDialog.title"),
            subtitle: t("viewMgmt.dropDialog.subtitle"),
            close: t("viewMgmt.dropDialog.close"),
            target: t("viewMgmt.dropDialog.target"),
            executeTitle: t("viewMgmt.dropDialog.executeTitle"),
            executeHint: t("viewMgmt.dropDialog.executeHint"),
            cancel: t("viewMgmt.dropDialog.cancel"),
            run: t("viewMgmt.drop.run"),
          }}
          onConfirmationChange={setDropConfirmation}
          onExecute={() => void dropView()}
          onClose={() => setDropTargetName("")}
        />
      )}
    </>
  );
}
