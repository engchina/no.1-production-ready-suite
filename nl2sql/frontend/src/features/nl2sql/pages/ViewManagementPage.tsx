import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Code2, Eye, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { EmptyState, toast } from "@engchina/production-ready-ui";

import { ContentActionBar } from "@/components/ContentActionBar";
import { StatusBadge } from "@/components/ui/status-badge";
import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { apiFetch, apiPost, isTimeoutError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import { selectedVisibleStringKey } from "@/lib/visible-selection";
import {
  DbManagementLoadingSkeleton,
  DbObjectDetailPanel,
  DbObjectGrid,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
  DropDbObjectDialog,
  dbAdminObjectQualifiedName,
  dbObjectSortValue,
  parseDbAdminObjectTarget,
  type DbObjectDetailTab,
  type DbObjectOwnerPrefix,
  type DbObjectSortKey,
  type DbObjectSortState,
} from "../components/DbObjectManagementShared";
import { StatementRunnerCard, downloadBlob } from "../components/DbAdminShared";
import type {
  DbAdminExecuteData,
  DbAdminJoinWhereData,
  DbAdminJoinWherePromptProfile,
  DbAdminObjectDetail,
  SchemaRefreshJob,
} from "../types";
import { useDbAdminObjects, useSchemaRefreshJob } from "../incrementalQueries";
import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  SchemaRefreshHeaderStatus,
  SchemaRefreshProcessing,
} from "../components/SchemaRefreshFeedback";
import { dbAdminObjectCountsFromPage } from "../dbAdminObjectCounts";
import { useDbObjectDetailRequest } from "../useDbObjectDetailRequest";

type ActiveView = "list" | "create" | "joinWhere";

const VIEW_MANAGEMENT_ID = "view-management";
const JOIN_WHERE_PROMPT_PROFILE: DbAdminJoinWherePromptProfile = "sql_structure";
const JOIN_WHERE_OUTPUT_SCOPE_KEYS = ["join", "where", "structure"] as const;

const useDebouncedValue = <T,>(value: T, delayMs: number) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeoutId);
  }, [delayMs, value]);
  return debounced;
};

function joinWherePromptProfileLabel() {
  return t("viewMgmt.joinWhere.profile.sqlStructure");
}

function joinWherePromptProfileDescription() {
  return t("viewMgmt.joinWhere.profile.sqlStructureHint");
}

function ViewJoinWherePanel({
  detail,
  result,
  loading,
  ddlLoading,
  ddlError,
  onExtract,
  onRetryDdl,
}: {
  detail: DbAdminObjectDetail | null;
  result: DbAdminJoinWhereData | null;
  loading: boolean;
  ddlLoading: boolean;
  ddlError: string;
  onExtract: () => void;
  onRetryDdl: () => void;
}) {
  const ddlStatusId = "view-join-where-ddl-status";
  const ddlReady = Boolean(detail?.ddl);
  const ddlStatus = !detail
    ? t("viewMgmt.joinWhere.ddlStatus.noView")
    : ddlLoading
      ? t("viewMgmt.joinWhere.ddlStatus.loading")
      : ddlError
        ? t("viewMgmt.joinWhere.ddlStatus.failed", { message: ddlError })
        : ddlReady
          ? t("viewMgmt.joinWhere.ddlStatus.ready")
          : t("viewMgmt.joinWhere.ddlStatus.missing");

  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Sparkles}
        title={t("viewMgmt.joinWhere.title")}
        description={t("viewMgmt.joinWhere.subtitle")}
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

      <section
        aria-label={t("viewMgmt.joinWhere.advancedSettings")}
        className="grid gap-3 rounded-md border border-border bg-background p-3"
        data-testid="view-join-where-advanced-settings"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid min-w-0 gap-1">
            <h3 className="text-sm font-semibold text-foreground">
              {t("viewMgmt.joinWhere.advancedSettings")}
            </h3>
            <p className="max-w-prose text-xs leading-5 text-muted">
              {t("viewMgmt.joinWhere.advancedSettingsHint")}
            </p>
          </div>
          <StatusBadge variant="info" label={joinWherePromptProfileLabel()} />
        </div>
        <div className="grid gap-2 rounded-md border border-border bg-card p-3">
          <p className="text-sm font-medium text-foreground">
            {t("viewMgmt.joinWhere.profileLabel")}
          </p>
          <p className="max-w-prose text-xs leading-5 text-muted">
            {joinWherePromptProfileDescription()}
          </p>
          <div
            role="list"
            className="flex flex-wrap gap-2"
            aria-label={t("viewMgmt.joinWhere.outputScope")}
          >
            {JOIN_WHERE_OUTPUT_SCOPE_KEYS.map((scope) => (
              <span
                key={scope}
                role="listitem"
                className="inline-flex min-h-8 items-center rounded-full border border-info/30 bg-info-bg px-3 text-xs font-medium text-info"
              >
                {t(`viewMgmt.joinWhere.outputScope.${scope}`)}
              </span>
            ))}
          </div>
        </div>
      </section>

      <ContentActionBar
        ariaLabel={t("viewMgmt.joinWhere.actions")}
        title={t("viewMgmt.joinWhere.ddlStatus.title")}
        description={
          <span
            id={ddlStatusId}
            className={ddlError ? "text-warning" : undefined}
            aria-live={ddlLoading || ddlError ? "polite" : undefined}
          >
            {ddlStatus}
          </span>
        }
        actionsClassName="w-full sm:w-auto"
        testId="view-join-where-actions"
      >
        {detail && ddlError ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="w-full sm:w-auto"
            disabled={ddlLoading}
            onClick={onRetryDdl}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("viewMgmt.joinWhere.ddlRetry")}</span>
          </Button>
        ) : null}
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="w-full sm:w-auto"
          loading={loading}
          disabled={!ddlReady || ddlLoading}
          aria-describedby={ddlStatusId}
          onClick={onExtract}
        >
          <Sparkles size={15} aria-hidden="true" />
          <span>{t("viewMgmt.joinWhere.extract")}</span>
        </Button>
      </ContentActionBar>

      {loading ? (
        <DbManagementLoadingSkeleton
          idPrefix="view-join-where-result"
          ariaLabel={t("viewMgmt.joinWhere.loading")}
          variant="detail"
          placement="result"
        />
      ) : result ? (
        <section className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm" aria-label={t("viewMgmt.joinWhere.result")}>
          <div className="flex flex-wrap gap-2">
            <StatusBadge variant={result.source === "oci_enterprise_ai" ? "success" : "neutral"} label={result.source} />
            <StatusBadge
              variant="info"
              label={joinWherePromptProfileLabel()}
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
            <details className="group/disclosure rounded-md border border-border bg-card p-3">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden">
                <span>{t("viewMgmt.joinWhere.structureResult")}</span>
                <DisclosureChevron expanded="group" size={15} className="text-muted" />
              </summary>
              <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-code p-3 font-mono text-sm leading-6 text-code-fg">
                {result.structure_markdown}
              </pre>
            </details>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function schemaRefreshRequiresFull(job: SchemaRefreshJob | null) {
  if (!job) return false;
  return (
    Boolean(job.requires_full_refresh) ||
    job.error_code === "schema_refresh_full_required" ||
    job.error_code === "schema_refresh_target_unresolved"
  );
}

function schemaRefreshRequiredMessage(reasonCode = "") {
  if (reasonCode === "schema_refresh_target_unresolved") {
    return t("dataMgmt.schemaJob.targetUnresolved");
  }
  return t("dataMgmt.schemaJob.fullRequired");
}

function schemaRefreshErrorMessage(job: SchemaRefreshJob) {
  if (schemaRefreshRequiresFull(job)) {
    return schemaRefreshRequiredMessage(job.error_code);
  }
  return job.error_code
    ? `${t("dataMgmt.schemaJob.error")} (${job.error_code})`
    : t("dataMgmt.schemaJob.error");
}

function objectListErrorMessage(error: unknown, fallbackKey: Parameters<typeof t>[0]) {
  if (isTimeoutError(error)) {
    return t("dataMgmt.objectList.timeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
    });
  }
  return error instanceof Error ? error.message : t(fallbackKey);
}

function objectListLoadMoreErrorMessage(error: unknown, fallbackKey: Parameters<typeof t>[0]) {
  if (isTimeoutError(error)) {
    return t("objectSelector.loadMoreTimeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
    });
  }
  return error instanceof Error ? error.message : t(fallbackKey);
}

export function ViewManagementPage() {
  const [detailTab, setDetailTab] = useState<DbObjectDetailTab>("columns");
  const [activeView, setActiveView] = useState<ActiveView>("list");
  const [viewSearch, setViewSearch] = useState("");
  const [viewOwnerPrefix, setViewOwnerPrefix] = useState<DbObjectOwnerPrefix>("");
  const [viewSort, setViewSort] = useState<DbObjectSortState>({ key: "name", direction: "asc" });
  const [dropTargetName, setDropTargetName] = useState("");
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [joinWhere, setJoinWhere] = useState<DbAdminJoinWhereData | null>(null);
  const [schemaRefreshJobId, setSchemaRefreshJobId] = useState("");
  const [schemaRefreshError, setSchemaRefreshError] = useState("");
  const [schemaRefreshNeedsFull, setSchemaRefreshNeedsFull] = useState(false);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const completedSchemaRefreshJob = useRef("");
  const autoJoinWhereDdlName = useRef("");
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const debouncedViewSearch = useDebouncedValue(viewSearch, 250);
  const debouncedViewOwnerPrefix = useDebouncedValue(viewOwnerPrefix, 250);
  const viewObjectsQuery = useDbAdminObjects(
    debouncedViewSearch,
    "view",
    "all",
    debouncedViewOwnerPrefix,
    "name_comment"
  );
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  const visibleSchemaRefreshError = schemaRefreshError || sharedSchemaRefresh.error;
  const viewItems = useMemo(
    () => (viewObjectsQuery.data?.pages ?? []).flatMap((page) => page.items),
    [viewObjectsQuery.data]
  );
  const firstViewPage = viewObjectsQuery.data?.pages[0];
  const totalViewCount = dbAdminObjectCountsFromPage(firstViewPage, viewItems).totalCount;
  const detailRequest = useDbObjectDetailRequest({
    collectionPath: "/api/nl2sql/db-admin/views",
    loadErrorMessage: t("viewMgmt.error.detail"),
    timeoutErrorMessage: t("dbAdmin.detail.timeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveDetail),
    }),
  });
  const {
    selectedName: selectedViewName,
    detail,
  } = detailRequest;
  const selectedViewManualSelection = useRef(false);

  const fetchDetail = async (name: string, options: { manualSelection?: boolean } = {}) => {
    if (options.manualSelection) selectedViewManualSelection.current = true;
    autoJoinWhereDdlName.current = "";
    setDetailTab("columns");
    setJoinWhere(null);
    await detailRequest.load(name);
  };

  // DDL は重い GET_DDL を伴うため列タブでは取得せず、DDL タブ初回表示時に後追いで取得する。
  const handleDetailTabChange = (nextTab: DbObjectDetailTab) => {
    setDetailTab(nextTab);
    if (nextTab !== "ddl" || !detail || detail.ddl) return;
    void detailRequest.loadDdl(dbAdminObjectQualifiedName(detail));
  };

  const returnToList = () => {
    setActiveView("list");
    setDetailTab("columns");
  };

  const refreshObjects = async (announce = false) => {
    setMessage("");
    const result = await viewObjectsQuery.refetch();
    if (result.error) {
      setMessage(result.error instanceof Error ? result.error.message : t("viewMgmt.error.load"));
      return;
    }
    if (announce) {
      toast.success(t("common.action.refreshed"));
    }
  };

  const refreshSchema = async () => {
    setLoading("schema-refresh");
    setMessage("");
    setSchemaRefreshError("");
    setSchemaRefreshNeedsFull(false);
    try {
      // 列サンプル値は詳細 API が返すため catalog 全取得はしない。schema-refresh 時のみ
      // サーバ側 catalog を再構築してから一覧(refreshed_at を含む)を取り直す。
      const job = await sharedSchemaRefresh.start();
      if (job.job_id) {
        completedSchemaRefreshJob.current = "";
        setSchemaRefreshJobId(job.job_id);
      }
    } catch (err) {
      setMessage(
        err instanceof Error
          ? err.message
          : t("dataMgmt.schemaJob.submitError")
      );
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    const job = schemaRefreshJobQuery.data;
    if (!job) return;
    const reportKey = `${job.job_id}:${job.status}`;
    if (completedSchemaRefreshJob.current === reportKey) return;
    if (job.status === "done") {
      completedSchemaRefreshJob.current = reportKey;
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      void refreshObjects();
    } else if (job.status === "error") {
      completedSchemaRefreshJob.current = reportKey;
      const needsFull = schemaRefreshRequiresFull(job);
      setSchemaRefreshNeedsFull(needsFull);
      setSchemaRefreshError(schemaRefreshErrorMessage(job));
    }
  }, [schemaRefreshJobQuery.data]);

  useEffect(() => {
    if (
      activeView !== "joinWhere" ||
      !detail ||
      detail.ddl ||
      detailRequest.ddlLoading ||
      detailRequest.ddlError ||
      autoJoinWhereDdlName.current === dbAdminObjectQualifiedName(detail)
    ) {
      return;
    }
    autoJoinWhereDdlName.current = dbAdminObjectQualifiedName(detail);
    void detailRequest.loadDdl(dbAdminObjectQualifiedName(detail));
  }, [
    activeView,
    detail?.name,
    detail?.ddl,
    detailRequest.ddlLoading,
    detailRequest.ddlError,
    detailRequest.loadDdl,
  ]);

  const reloadAfterMutation = (result: {
    schema_refresh_job_id?: string;
    schema_refresh_required?: boolean;
    schema_refresh_reason_code?: string;
  }) => {
    if (result.schema_refresh_job_id) {
      sharedSchemaRefresh.track(result.schema_refresh_job_id);
      completedSchemaRefreshJob.current = "";
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      setSchemaRefreshJobId(result.schema_refresh_job_id);
    } else if (result.schema_refresh_required) {
      setSchemaRefreshError(schemaRefreshRequiredMessage(result.schema_refresh_reason_code));
      setSchemaRefreshNeedsFull(true);
    }
    void refreshObjects();
  };

  const filteredViews = useMemo(() => {
    const q = viewSearch.trim().toLowerCase();
    const ownerPrefixKey = viewOwnerPrefix.trim().toUpperCase();
    return viewItems
      .filter((item) => {
        if (ownerPrefixKey && !item.owner.toUpperCase().startsWith(ownerPrefixKey)) return false;
        if (!q) return true;
        return (
          item.name.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = dbObjectSortValue(left, viewSort.key);
        const b = dbObjectSortValue(right, viewSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return viewSort.direction === "asc" ? result : -result;
      });
  }, [viewItems, viewOwnerPrefix, viewSearch, viewSort]);

  useEffect(() => {
    if (activeView !== "list") return;
    if (viewObjectsQuery.isPending || detailRequest.loading) return;
    if (viewObjectsQuery.error && !viewObjectsQuery.data) return;
    const nextViewName = selectedVisibleStringKey(
      filteredViews,
      selectedViewName,
      dbAdminObjectQualifiedName,
      { preserveSelected: selectedViewManualSelection.current }
    );
    if (!nextViewName) {
      selectedViewManualSelection.current = false;
      if (selectedViewName) detailRequest.clear();
      return;
    }
    if (nextViewName === selectedViewName) return;
    selectedViewManualSelection.current = false;
    void fetchDetail(nextViewName);
  }, [
    activeView,
    filteredViews,
    viewObjectsQuery.isPending,
    viewObjectsQuery.error,
    viewObjectsQuery.data,
    selectedViewName,
    detailRequest.loading,
    detailRequest.clear,
  ]);

  const toggleSort = (key: DbObjectSortKey) => {
    setViewSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const downloadColumnsXlsx = async (name: string) => {
    const target = parseDbAdminObjectTarget(name);
    const params = new URLSearchParams();
    if (target.owner) params.set("owner", target.owner);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    setLoading("view-export");
    setMessage("");
    try {
      const response = await apiFetch(`/api/nl2sql/db-admin/views/${encodeURIComponent(target.name)}/export.xlsx${suffix}`, {
        headers: {
          Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
      });
      if (!response.ok) {
        throw new Error(t("viewMgmt.error.export"));
      }
      downloadBlob(`${target.qualifiedName.toLowerCase().replace(".", "_")}_columns.xlsx`, await response.blob());
      toast.success(t("common.action.downloaded"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("viewMgmt.error.export"));
    } finally {
      setLoading("");
    }
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
        view_name: parseDbAdminObjectTarget(dropTargetName).name,
        owner: parseDbAdminObjectTarget(dropTargetName).owner,
        confirmation: dropConfirmation,
        reason: "ui-view-management-drop",
      });
      if (result.executed) {
        const dropped = dropTargetName;
        setDropTargetName("");
        setDropConfirmation("");
        toast.success(t("viewMgmt.drop.success", { name: dropped }));
        await reloadAfterMutation(result);
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
          prompt_profile: JOIN_WHERE_PROMPT_PROFILE,
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
        footerProcessing={
          schemaRefreshing ? (
            <SchemaRefreshProcessing testId="view-create-schema-refresh-processing" />
          ) : null
        }
        executeOnly
        framed={false}
        onExecuted={reloadAfterMutation}
      />
    ) : activeView === "joinWhere" ? (
      <ViewJoinWherePanel
        detail={detail}
        result={joinWhere}
        loading={loading === "join-where"}
        ddlLoading={detailRequest.ddlLoading}
        ddlError={detailRequest.ddlError}
        onExtract={() => void extractJoinWhere()}
        onRetryDdl={() => {
          if (detail) {
            autoJoinWhereDdlName.current = "";
            void detailRequest.loadDdl(dbAdminObjectQualifiedName(detail));
          }
        }}
      />
    ) : null;

  return (
    <>
      <PageHeader
        title={t("nav.viewManagement")}
        subtitle={t("viewMgmt.subtitle")}
        meta={
          firstViewPage?.refreshed_at
            ? t("common.schemaRefreshedAt", { date: formatDateTime(firstViewPage.refreshed_at) })
            : undefined
        }
        status={<SchemaRefreshHeaderStatus testId="view-schema-refresh-status" />}
        actionsAriaLabel={t("viewMgmt.tabs.label")}
        actionsTestId="view-management-actions"
        actions={
          activeView === "list"
            ? [
                {
                  id: "create-view",
                  kind: "primary",
                  label: t("viewMgmt.create.title"),
                  icon: Code2,
                  onClick: () => setActiveView("create"),
                },
                {
                  id: "extract-view-conditions",
                  kind: "secondary",
                  label: t("viewMgmt.joinWhere.title"),
                  icon: Sparkles,
                  onClick: () => setActiveView("joinWhere"),
                },
                {
                  id: "refresh-view-list",
                  kind: "utility",
                  label: t("common.action.refresh"),
                  icon: RefreshCw,
                  loading: viewObjectsQuery.isFetching && !viewObjectsQuery.isFetchingNextPage,
                  onClick: () => void refreshObjects(true),
                },
                {
                  id: "refresh-view-schema",
                  kind: "utility",
                  label: t("common.action.schemaRefresh"),
                  icon: RefreshCw,
                  loading: sharedSchemaRefresh.isStarting,
                  disabled: schemaRefreshing,
                  onClick: () => void refreshSchema(),
                },
              ]
            : []
        }
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={
            message
              ? { tone: "danger", message: `${message} ${t("viewMgmt.error.retryHint")}` }
              : visibleSchemaRefreshError
                ? { tone: "danger", message: visibleSchemaRefreshError }
                : null
          }
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={
                schemaRefreshNeedsFull || Boolean(sharedSchemaRefresh.error)
                  ? () => void refreshSchema()
                  : () => void refreshObjects()
              }
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>
                {schemaRefreshNeedsFull || Boolean(sharedSchemaRefresh.error)
                  ? t("common.action.schemaRefresh")
                  : t("viewMgmt.action.refresh")}
              </span>
            </Button>
          }
        />
        {activeView === "list" ? (
          <>
            <DbObjectManagementPanelShell
              id="view-management-panel-list"
              role="region"
              idPrefix={VIEW_MANAGEMENT_ID}
              ariaLabel={t("viewMgmt.workspace.label")}
              splitId="view-management-list"
              preferredWidePane="right"
              processing={
                schemaRefreshing ? (
                  <SchemaRefreshProcessing testId="view-management-workspace-processing" />
                ) : viewObjectsQuery.data &&
                  viewObjectsQuery.isFetching && !viewObjectsQuery.isFetchingNextPage ? (
                  <ProcessingIndicator
                    active
                    label={t("viewMgmt.workspace.refreshing")}
                    operationKey="view-list-refresh"
                    placement="workspace"
                    className="rounded-md border border-border bg-background px-3 py-2"
                    testId="view-management-workspace-processing"
                    activityIcon="none"
                  />
                ) : undefined
              }
            >
            <DbObjectGrid
              idPrefix={VIEW_MANAGEMENT_ID}
              headingId="view-grid-heading"
              icon={Eye}
              items={filteredViews}
              selectedName={selectedViewName}
              loading={viewObjectsQuery.isPending && !viewObjectsQuery.data}
              error={
                viewObjectsQuery.error && !viewObjectsQuery.data
                  ? objectListErrorMessage(viewObjectsQuery.error, "viewMgmt.error.load")
                  : ""
              }
              search={viewSearch}
              ownerPrefix={viewOwnerPrefix}
              sort={viewSort}
              totalCount={totalViewCount}
              hasNextPage={Boolean(viewObjectsQuery.hasNextPage)}
              loadingNextPage={viewObjectsQuery.isFetchingNextPage}
              loadMoreError={
                viewObjectsQuery.isFetchNextPageError && viewObjectsQuery.error
                  ? objectListLoadMoreErrorMessage(viewObjectsQuery.error, "viewMgmt.error.load")
                  : ""
              }
              labels={{
                title: t("viewMgmt.list.title"),
                hint: t("viewMgmt.grid.hint"),
                count: t("viewMgmt.grid.count", { count: totalViewCount }),
                loading: t("viewMgmt.list.loading"),
                emptyTitle: t("viewMgmt.list.emptyTitle"),
                emptyHint: t("viewMgmt.list.emptyHint"),
                noResultsTitle: t("viewMgmt.list.noResultsTitle"),
                noResultsHint: t("viewMgmt.list.noResultsHint"),
                objectName: t("viewMgmt.grid.viewName"),
                rows: t("viewMgmt.grid.rows"),
                owner: t("viewMgmt.grid.owner"),
                actions: t("viewMgmt.grid.actions"),
                detail: t("viewMgmt.grid.detail"),
                drop: t("viewMgmt.grid.drop"),
                showObject: (name) => t("viewMgmt.grid.showView", { name }),
              }}
              onSearchChange={setViewSearch}
              onOwnerPrefixChange={setViewOwnerPrefix}
              onSortChange={toggleSort}
              onSelect={(name) => void fetchDetail(name, { manualSelection: true })}
              onDrop={openDropDialog}
              onLoadMore={() => void viewObjectsQuery.fetchNextPage()}
              onRetryLoadMore={() => void viewObjectsQuery.fetchNextPage()}
              onRetry={() => void refreshObjects()}
            />
            <DbObjectDetailPanel
              idPrefix={VIEW_MANAGEMENT_ID}
              operationKey={selectedViewName}
              headingId="view-detail-heading"
              detail={detail}
              loading={detailRequest.loading || (viewObjectsQuery.isPending && !viewObjectsQuery.data)}
              ddlLoading={detailRequest.ddlLoading}
              error={detailRequest.error}
              ddlError={detailRequest.ddlError}
              exporting={loading === "view-export"}
              tab={detailTab}
              labels={{
                actions: t("viewMgmt.grid.actions"),
                loading: t("viewMgmt.detail.loading"),
                ddlLoading: t("viewMgmt.detail.ddlLoading"),
                tabsLabel: t("viewMgmt.detailTabs.label"),
                columns: t("viewMgmt.detailTabs.columns"),
                ddl: t("viewMgmt.detailTabs.ddl"),
                export: t("viewMgmt.export"),
                exportAria: t("viewMgmt.exportColumns"),
                drop: t("viewMgmt.grid.drop"),
              }}
              onTabChange={handleDetailTabChange}
              onRetry={() => void fetchDetail(selectedViewName)}
              onRetryDdl={() => {
                if (detail) void detailRequest.loadDdl(dbAdminObjectQualifiedName(detail));
              }}
              onCancel={detailRequest.cancel}
              onExport={(name) => void downloadColumnsXlsx(name)}
              onDrop={openDropDialog}
            />
            </DbObjectManagementPanelShell>
          </>
        ) : (
          <>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={returnToList}>
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
