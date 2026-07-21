import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Code2, Eye, RefreshCw, Sparkles } from "lucide-react";

import { Button, EmptyState, PageHeader, StatusBadge, toast } from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiGet, apiPost, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  DbObjectDetailPanel,
  DbObjectGrid,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
  DbObjectStatusBar,
  DbObjectStepIndicator,
  DropDbObjectDialog,
  dbObjectSortValue,
  type DbObjectDetailTab,
  type DbObjectFilter,
  type DbObjectSortKey,
  type DbObjectSortState,
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
        activeIndex={result ? 2 : detail ? 0 : -1}
        ariaLabel={t("viewMgmt.joinWhere.steps")}
        dataTestId="view-join-where-steps"
      />

      {detail ? (
        <section
          className="grid gap-3 rounded-md border border-border bg-background p-3"
          data-testid="view-join-where-selected-view"
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge variant="neutral" label={detail.object_type} />
            <StatusBadge variant="info" label={detail.name} />
          </div>
          <p className="text-sm text-muted">{t("viewMgmt.joinWhere.selectedHint")}</p>
        </section>
      ) : (
        <EmptyState title={t("viewMgmt.joinWhere.emptyTitle")} hint={t("viewMgmt.joinWhere.empty")} />
      )}

      <fieldset className="grid gap-2">
        <legend className="text-sm font-semibold text-foreground">
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
                    ? "border-primary/40 bg-primary/10 text-primary"
                    : "border-border bg-card text-foreground hover:border-border",
                ].join(" ")}
              >
                <input
                  type="radio"
                  name="view-join-where-prompt-profile"
                  value={option}
                  checked={selected}
                  onChange={() => onProfileChange(option)}
                  className="mt-1 h-4 w-4 flex-none accent-primary"
                />
                <span className="grid min-w-0 gap-1">
                  <span className="text-sm font-semibold">
                    {joinWherePromptProfileLabel(option)}
                  </span>
                  <span className="text-xs leading-5 text-muted">
                    {joinWherePromptProfileDescription(option)}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {result && (
        <section className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm" aria-label={t("viewMgmt.joinWhere.result")}>
          <div className="flex flex-wrap gap-2">
            <StatusBadge variant={result.source === "oci_enterprise_ai" ? "success" : "neutral"} label={result.source} />
            <StatusBadge
              variant="info"
              label={joinWherePromptProfileLabel(result.prompt_profile ?? "join_where_strict")}
            />
          </div>
          {result.warnings.map((warning) => (
            <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
              {warning}
            </p>
          ))}
          <div className="grid gap-3 lg:grid-cols-2">
            <label className="grid gap-1 text-sm font-medium text-foreground">
              <span>{t("viewMgmt.joinWhere.join")}</span>
              <textarea
                readOnly
                value={result.join_text}
                rows={5}
                className="min-h-32 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 text-foreground outline-none"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-foreground">
              <span>{t("viewMgmt.joinWhere.where")}</span>
              <textarea
                readOnly
                value={result.where_text}
                rows={5}
                className="min-h-32 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 text-foreground outline-none"
              />
            </label>
          </div>
          {result.structure_markdown ? (
            <details className="rounded-md border border-border bg-card p-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">
                {t("viewMgmt.joinWhere.structureResult")}
              </summary>
              <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-code p-3 font-mono text-sm leading-6 text-code-fg">
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
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const fetchDetail = async (name: string) => {
    setLoading(`detail-${name}`);
    setMessage("");
    setSelectedViewName(name);
    setDetail(null);
    setDetailTab("columns");
    setJoinWhere(null);
    try {
      setDetail(
        await apiGet<DbAdminObjectDetail>(
          `/api/nl2sql/db-admin/views/${encodeURIComponent(name)}?include_ddl=0`,
        ),
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  // DDL は重い GET_DDL を伴うため列タブでは取得せず、DDL タブ初回表示時に後追いで取得する。
  const handleDetailTabChange = (nextTab: DbObjectDetailTab) => {
    setDetailTab(nextTab);
    if (nextTab !== "ddl" || !detail || detail.ddl) return;
    const name = detail.name;
    void (async () => {
      try {
        const full = await apiGet<DbAdminObjectDetail>(
          `/api/nl2sql/db-admin/views/${encodeURIComponent(name)}?include_ddl=1`,
        );
        setDetail((current) =>
          current && current.name === name ? { ...current, ddl: full.ddl } : current,
        );
      } catch {
        // DDL 取得失敗時は既存の "-" 表示のまま
      }
    })();
  };

  const load = async (refreshSchema = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(refreshSchema ? "schema-refresh" : "load");
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        // 列サンプル値は詳細 API が返すため catalog 全取得はしない。schema-refresh 時のみ
        // サーバ側 catalog を再構築してから一覧(refreshed_at を含む)を取り直す。
        if (refreshSchema) {
          await apiPost<SchemaCatalog>("/api/schema/refresh", undefined, { signal });
        }
        const viewData = await apiGet<DbAdminObjectsData>(
          "/api/nl2sql/db-admin/views",
          { signal }
        );
        if (signal.aborted || sequence !== loadSequence.current) return;
        setViews(viewData);
        const nextSelected =
          viewData.items.find((item) => item.name === selectedViewName)?.name ||
          viewData.items[0]?.name ||
          "";
        setSelectedViewName(nextSelected);
        setJoinWhere(null);
        if (nextSelected) {
          const nextDetail = await apiGet<DbAdminObjectDetail>(
            `/api/nl2sql/db-admin/views/${encodeURIComponent(nextSelected)}?include_ddl=0`,
            { signal }
          );
          if (!signal.aborted && sequence === loadSequence.current) setDetail(nextDetail);
        } else {
          setDetail(null);
        }
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.load"));
    } finally {
      if (sequence === loadSequence.current) setLoading("");
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
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
        confirmation: dropConfirmation,
        reason: "ui-view-management-drop",
      });
      if (result.executed) {
        const dropped = dropTargetName;
        setDropTargetName("");
        setDropConfirmation("");
        toast.success(t("viewMgmt.drop.success", { name: dropped }));
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
        <PageNotice
          notice={
            message
              ? { tone: "danger", message: `${message} ${t("viewMgmt.error.retryHint")}` }
              : null
          }
          action={
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("viewMgmt.action.refresh")}</span>
            </Button>
          }
        />

        <DbObjectStatusBar
          count={views?.items.length ?? 0}
          runtime={views?.runtime ?? "deterministic"}
          refreshedAt={views?.refreshed_at ?? ""}
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

        {activeView === "list" ? (
          <>
            <div
              className="flex flex-wrap items-center justify-end gap-2"
              data-testid="view-management-actions"
              aria-label={t("viewMgmt.tabs.label")}
            >
              <Button type="button" variant="secondary" size="sm" onClick={() => setActiveView("create")}>
                <Code2 size={15} aria-hidden="true" />
                <span>{t("viewMgmt.create.title")}</span>
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => setActiveView("joinWhere")}>
                <Sparkles size={15} aria-hidden="true" />
                <span>{t("viewMgmt.joinWhere.title")}</span>
              </Button>
            </div>
            <DbObjectManagementPanelShell
              id="view-management-panel-list"
              role="region"
              idPrefix={VIEW_MANAGEMENT_ID}
              ariaLabel={t("viewMgmt.workspace.label")}
              splitId="view-management-list"
              preferredWidePane="right"
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
              loading={loading.startsWith("detail-") || (loading === "load" && !detail)}
              tab={detailTab}
              labels={{
                tabsLabel: t("viewMgmt.detailTabs.label"),
                columns: t("viewMgmt.detailTabs.columns"),
                ddl: t("viewMgmt.detailTabs.ddl"),
                drop: t("viewMgmt.grid.drop"),
              }}
              onTabChange={handleDetailTabChange}
              onDrop={openDropDialog}
            />
            </DbObjectManagementPanelShell>
          </>
        ) : (
          <>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => setActiveView("list")}>
                <ArrowLeft size={15} aria-hidden="true" />
                <span>{t("viewMgmt.action.backToList")}</span>
              </Button>
            </div>
            <DbObjectManagementPanelShell
              id={`view-management-panel-${activeView}`}
              role="region"
              idPrefix={VIEW_MANAGEMENT_ID}
              ariaLabel={t("viewMgmt.toolbar.taskPanel")}
            >
              {taskContent}
            </DbObjectManagementPanelShell>
          </>
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
