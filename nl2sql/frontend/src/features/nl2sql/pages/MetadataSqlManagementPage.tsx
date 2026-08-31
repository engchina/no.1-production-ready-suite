import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownUp,
  Code2,
  FileText,
  RefreshCw,
  Table2,
  Wand2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState, toast } from "@engchina/production-ready-ui";

import { StatusBadge } from "@/components/ui/status-badge";

import { BulkSelectionActions } from "@/components/BulkSelectionActions";
import { ContentActionBar } from "@/components/ContentActionBar";
import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { ErrorState } from "@/components/StateViews";
import { apiGet, apiPost, isTimeoutError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { INFORMATION_TABLE_FIVE_ROW_SCROLL_CLASS } from "@/lib/list-density";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import {
  DB_OBJECT_GRID_ROW_CLASS,
  DbManagementLoadingSkeleton,
  DbManagementSelectField,
  DbObjectSelectorFooter,
  DbObjectSelectorToolbar,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
  dbAdminObjectQualifiedName,
  parseDbAdminObjectTarget,
} from "../components/DbObjectManagementShared";
import { StatementRunnerCard } from "../components/DbAdminShared";
import { buildMetadataInputTexts } from "../metadataSql";
import { useDbAdminObjects, useSchemaRefreshJob } from "../incrementalQueries";
import { dbAdminObjectCountsFromPage } from "../dbAdminObjectCounts";
import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  SchemaRefreshHeaderStatus,
  SchemaRefreshProcessing,
} from "../components/SchemaRefreshFeedback";

// タブではなく 1 画面スクロール + トップステッパー。各工程セクションの共通カード枠。
const PANEL_CLASS = "grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm";
import type {
  DbAdminObjectDetail,
  DbAdminExecuteData,
  DbAdminObjectSummary,
  DbAdminStatementPolicy,
  MetadataSqlGenerateData,
  MetadataSqlGeneratePayload,
  MetadataSqlSampleData,
  MetadataSqlSamplePayload,
  MetadataSqlTarget,
  SchemaRefreshJob,
} from "../types";

type MetadataMode = "comment" | "annotation";
type TargetFilter = "all" | "table" | "view";
type TargetSortKey = "name" | "object_type" | "owner";
type TargetSortDirection = "asc" | "desc";

interface TargetSortState {
  key: TargetSortKey;
  direction: TargetSortDirection;
}

interface MetadataTargetItem extends MetadataSqlTarget {
  key: string;
  qualifiedName: string;
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

const useDebouncedValue = <T,>(value: T, delayMs: number) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeoutId);
  }, [delayMs, value]);
  return debounced;
};

export function CommentManagementPage() {
  return <MetadataSqlManagementPage mode="comment" />;
}

export function AnnotationManagementPage() {
  return <MetadataSqlManagementPage mode="annotation" />;
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

function MetadataSqlManagementPage({ mode }: { mode: MetadataMode }) {
  const pageId = mode === "comment" ? "comment-management" : "annotation-management";
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [details, setDetails] = useState<DbAdminObjectDetail[]>([]);
  const [sampleLimit, setSampleLimit] = useState(10);
  const [refreshedSampleText, setRefreshedSampleText] = useState<string | null>(null);
  const [extraText, setExtraText] = useState(mode === "annotation" ? ANNOTATION_EXTRA_TEXT : "");
  const [generated, setGenerated] = useState<MetadataSqlGenerateData | null>(null);
  const [targetSearch, setTargetSearch] = useState("");
  const [targetOwnerPrefix, setTargetOwnerPrefix] = useState("");
  const [targetFilter, setTargetFilter] = useState<TargetFilter>("all");
  const [targetSort, setTargetSort] = useState<TargetSortState>({ key: "name", direction: "asc" });
  const debouncedTargetSearch = useDebouncedValue(targetSearch, 250);
  const debouncedTargetOwnerPrefix = useDebouncedValue(targetOwnerPrefix, 250);
  const objectsQuery = useDbAdminObjects(
    debouncedTargetSearch,
    targetFilter,
    "all",
    debouncedTargetOwnerPrefix,
    "name_comment"
  );
  const objectItems = useMemo(
    () => (objectsQuery.data?.pages ?? []).flatMap((page) => page.items),
    [objectsQuery.data]
  );
  const firstObjectPage = objectsQuery.data?.pages[0];
  const totalTargetCount = dbAdminObjectCountsFromPage(firstObjectPage, objectItems).totalCount;
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const [schemaRefreshJobId, setSchemaRefreshJobId] = useState("");
  const [schemaRefreshError, setSchemaRefreshError] = useState("");
  const [schemaRefreshNeedsFull, setSchemaRefreshNeedsFull] = useState(false);
  const completedSchemaRefreshJob = useRef("");
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  const visibleSchemaRefreshError = schemaRefreshError || sharedSchemaRefresh.error;

  const allTargets = useMemo(
    () => targetItemsFromObjects(objectItems),
    [objectItems]
  );
  const selectedTargets = useMemo(
    () => selectedKeys.map((key) => targetFromKey(key)).filter(Boolean) as MetadataSqlTarget[],
    [selectedKeys]
  );
  const inputTexts = useMemo(
    () => buildMetadataInputTexts(details, sampleLimit),
    [details, sampleLimit]
  );
  const policy: DbAdminStatementPolicy = mode === "comment" ? "comment_sql" : "annotation_sql";
  const stepIndex = generated ? 2 : details.length > 0 ? 1 : 0;

  const filteredTargets = useMemo(() => {
    const q = targetSearch.trim().toLowerCase();
    const ownerPrefixKey = targetOwnerPrefix.trim().toUpperCase();
    return allTargets
      .filter((item) => {
        if (ownerPrefixKey && !item.owner.toUpperCase().startsWith(ownerPrefixKey)) return false;
        if (targetFilter !== "all" && item.object_type !== targetFilter) return false;
        if (!q) return true;
        return (
          item.object_name.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = targetSortValue(left, targetSort.key);
        const b = targetSortValue(right, targetSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return targetSort.direction === "asc" ? result : -result;
      });
  }, [allTargets, targetFilter, targetOwnerPrefix, targetSearch, targetSort]);

  const refreshObjects = async (announce = false) => {
    setMessage("");
    const result = await objectsQuery.refetch();
    if (result.error) {
      setMessage(result.error instanceof Error ? result.error.message : t("metadataSql.error.load"));
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

  const reloadAfterMutation = (result: DbAdminExecuteData) => {
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

  const toggleTarget = (target: MetadataSqlTarget) => {
    const key = targetKey(target);
    setSelectedKeys((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );
    setDetails([]);
    setRefreshedSampleText(null);
    setGenerated(null);
  };

  const bulkSelectTargets = (targets: MetadataTargetItem[], selected: boolean) => {
    const targetKeys = targets.map((target) => target.key);
    const targetKeySet = new Set(targetKeys);
    setSelectedKeys((current) =>
      selected
        ? [...new Set([...current, ...targetKeys])]
        : current.filter((key) => !targetKeySet.has(key))
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
        selectedTargets.map((target) => {
          const params = new URLSearchParams();
          if (target.owner) params.set("owner", target.owner);
          const suffix = params.toString() ? `?${params.toString()}` : "";
          return apiGet<DbAdminObjectDetail>(
            target.object_type === "view"
              ? `/api/nl2sql/db-admin/views/${encodeURIComponent(target.object_name)}${suffix}`
              : `/api/nl2sql/db-admin/tables/${encodeURIComponent(target.object_name)}${suffix}`
          );
        })
      );
      setDetails(nextDetails);
      setRefreshedSampleText(null);
      setGenerated(null);
      toast.success(t("metadataSql.toast.detailsLoaded", { count: nextDetails.length }));
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
          owner: detail.owner,
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
      toast.success(t("metadataSql.toast.generated"));
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
        meta={
          firstObjectPage?.refreshed_at
            ? t("common.schemaRefreshedAt", {
                date: formatDateTime(firstObjectPage.refreshed_at),
              })
            : undefined
        }
        status={<SchemaRefreshHeaderStatus testId={`${pageId}-schema-refresh-status`} />}
        actions={[
            {
              id: "refresh",
              kind: "utility",
              label: t("common.action.refresh"),
              icon: RefreshCw,
              onClick: () => void refreshObjects(true),
              loading: objectsQuery.isFetching && !objectsQuery.isFetchingNextPage,
            },
          {
            id: "schema-refresh",
            kind: "utility",
            label: t("common.action.schemaRefresh"),
            icon: RefreshCw,
            onClick: () => void refreshSchema(),
            loading: sharedSchemaRefresh.isStarting,
            disabled: schemaRefreshing,
          },
        ]}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={
            message
              ? { tone: "danger", message: `${message} ${t("metadataSql.error.retryHint")}` }
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
                  : t("tableMgmt.action.refresh")}
              </span>
            </Button>
          }
        />
        {schemaRefreshing ? (
          <SchemaRefreshProcessing testId={`${pageId}-workspace-processing`} />
        ) : objectsQuery.isFetching && !objectsQuery.isFetchingNextPage && Boolean(objectsQuery.data) ? (
          <ProcessingIndicator
            active
            label={t("common.processing.refreshing")}
            operationKey="metadata-objects-refresh"
            placement="workspace"
            className="rounded-md border border-border bg-card px-3 py-2 shadow-sm"
            testId={`${pageId}-workspace-processing`}
            activityIcon="none"
          />
        ) : null}

        <DbObjectStepIndicator
          steps={[
            t("metadataSql.steps.targets"),
            t("metadataSql.steps.input"),
            t("metadataSql.steps.execute"),
          ]}
          activeIndex={stepIndex}
          ariaLabel={t("metadataSql.steps.label")}
          dataTestId={`${pageId}-steps`}
        />

        <section id={`${pageId}-panel-targets`} aria-labelledby={`${pageId}-targets-heading`} className={PANEL_CLASS}>
          <MetadataTargetGrid
            pageId={pageId}
            items={filteredTargets}
            totalCount={totalTargetCount}
            selectedKeys={selectedKeys}
            loading={objectsQuery.isPending && !objectsQuery.data}
            error={
              objectsQuery.error && !objectsQuery.data
                ? objectListErrorMessage(objectsQuery.error, "metadataSql.error.load")
                : ""
            }
            search={targetSearch}
            ownerPrefix={targetOwnerPrefix}
            filter={targetFilter}
            sort={targetSort}
            hasNextPage={Boolean(objectsQuery.hasNextPage)}
            loadingNextPage={objectsQuery.isFetchingNextPage}
            loadMoreError={
              objectsQuery.isFetchNextPageError && objectsQuery.error
                ? objectListLoadMoreErrorMessage(objectsQuery.error, "metadataSql.error.load")
                : ""
            }
            onSearchChange={setTargetSearch}
            onOwnerPrefixChange={setTargetOwnerPrefix}
            onFilterChange={setTargetFilter}
            onSortChange={toggleSort}
            onToggle={toggleTarget}
            onBulkSelect={bulkSelectTargets}
            onRetry={() => void refreshObjects()}
            onLoadMore={() => void objectsQuery.fetchNextPage()}
            onRetryLoadMore={() => void objectsQuery.fetchNextPage()}
            onFetchDetails={() => void fetchDetails()}
            fetchingDetails={loading === "details"}
          />
        </section>

        <section id={`${pageId}-panel-input`} className={PANEL_CLASS}>
          <MetadataInputPanel
            pageId={pageId}
            inputTexts={inputTexts}
            detailsReady={details.length > 0}
            detailsLoading={loading === "details"}
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
        </section>

        <section id={`${pageId}-panel-execute`} className={PANEL_CLASS}>
          <MetadataExecutePanel
            pageId={pageId}
            mode={mode}
            generated={generated}
            loading={loading === "generate"}
            policy={policy}
            onExecuted={reloadAfterMutation}
          />
        </section>
      </main>
    </>
  );
}

function MetadataTargetGrid({
  pageId,
  items,
  totalCount,
  selectedKeys,
  loading,
  error,
  search,
  ownerPrefix,
  filter,
  sort,
  hasNextPage,
  loadingNextPage,
  loadMoreError,
  fetchingDetails,
  onSearchChange,
  onOwnerPrefixChange,
  onFilterChange,
  onSortChange,
  onToggle,
  onBulkSelect,
  onRetry,
  onLoadMore,
  onRetryLoadMore,
  onFetchDetails,
}: {
  pageId: string;
  items: MetadataTargetItem[];
  totalCount: number;
  selectedKeys: string[];
  loading: boolean;
  error: string;
  search: string;
  ownerPrefix: string;
  filter: TargetFilter;
  sort: TargetSortState;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  loadMoreError: string;
  fetchingDetails: boolean;
  onSearchChange: (value: string) => void;
  onOwnerPrefixChange: (value: string) => void;
  onFilterChange: (value: TargetFilter) => void;
  onSortChange: (key: TargetSortKey) => void;
  onToggle: (target: MetadataSqlTarget) => void;
  onBulkSelect: (targets: MetadataTargetItem[], selected: boolean) => void;
  onRetry: () => void;
  onLoadMore: () => void;
  onRetryLoadMore: () => void;
  onFetchDetails: () => void;
}) {
  const hasActiveFilter = Boolean(search.trim()) || Boolean(ownerPrefix.trim()) || filter !== "all";
  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);
  const selectedVisibleCount = items.filter((item) => selectedSet.has(item.key)).length;
  const allVisibleSelected = items.length > 0 && selectedVisibleCount === items.length;

  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby={`${pageId}-targets-heading`}>
      <DbObjectPanelHeader
        headingId={`${pageId}-targets-heading`}
        icon={Table2}
        title={t("metadataSql.targets.title")}
        description={t("metadataSql.targets.hint")}
        action={
          <>
            <StatusBadge
              variant="neutral"
              label={t("command.count", { count: totalCount })}
            />
            <StatusBadge variant="info" label={t("metadataSql.targets.selected", { count: selectedKeys.length })} />
          </>
        }
      />

      <DbObjectSelectorToolbar
        searchLabel={t("dbAdmin.search.label")}
        searchPlaceholder={t("dbAdmin.search.placeholder")}
        searchValue={search}
        onSearchChange={onSearchChange}
        resultLabel={t("objectSelector.resultCountWithSelected", {
          visible: items.length,
          total: totalCount,
          selected: selectedKeys.length,
        })}
        dataTestId={`${pageId}-target-toolbar`}
        ownerPrefixField={{
          label: t("dbAdmin.owner.label"),
          placeholder: t("dbAdmin.ownerPrefix.placeholder"),
          value: ownerPrefix,
          onChange: onOwnerPrefixChange,
        }}
      >
        <DbManagementSelectField
          label={t("metadataSql.targets.typeFilter")}
          value={filter}
          options={[
            { value: "all", label: t("metadataSql.targets.typeFilterAll") },
            { value: "table", label: t("metadataSql.targets.typeFilterTables") },
            { value: "view", label: t("metadataSql.targets.typeFilterViews") },
          ]}
          className="sm:w-48"
          onChange={onFilterChange}
        />
      </DbObjectSelectorToolbar>

      {!loading && items.length > 0 ? (
        <BulkSelectionActions
          selectLabel={t("common.selection.selectVisible")}
          clearLabel={t("common.selection.clearVisible")}
          selectDisabled={allVisibleSelected}
          clearDisabled={selectedVisibleCount === 0}
          dataTestId={`${pageId}-target-selection-actions`}
          onSelectAll={() => onBulkSelect(items, true)}
          onClearAll={() => onBulkSelect(items, false)}
        />
      ) : null}

      {loading ? (
        <DbManagementLoadingSkeleton
          idPrefix={`${pageId}-target`}
          ariaLabel={t("metadataSql.targets.loading")}
          variant="list"
        />
      ) : error ? (
        <ErrorState message={error} onRetry={onRetry} />
      ) : items.length === 0 ? (
        <EmptyState
          title={hasActiveFilter ? t("metadataSql.targets.noResultsTitle") : t("metadataSql.targets.emptyTitle")}
          hint={hasActiveFilter ? t("metadataSql.targets.noResultsHint") : t("metadataSql.targets.emptyHint")}
        />
      ) : (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className={INFORMATION_TABLE_FIVE_ROW_SCROLL_CLASS} data-testid="db-admin-object-list">
            <table className="w-full min-w-[42rem] table-fixed divide-y divide-border text-left text-sm" data-testid={`${pageId}-target-grid`}>
              <colgroup>
                <col className="w-[16rem]" />
                <col className="w-[6rem]" />
                <col className="w-[7rem]" />
                <col />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
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
              <tbody className="divide-y divide-border/70">
                {items.map((item) => {
                  const selected = selectedSet.has(item.key);
                  return (
                    <tr
                      key={item.key}
                      className={`${DB_OBJECT_GRID_ROW_CLASS} ${selected ? "bg-primary/10" : "hover:bg-background"}`}
                    >
                      <td className="px-3 py-1 align-top">
                        <label className="flex min-h-11 cursor-pointer items-start gap-3 text-foreground">
                          <input
                            type="checkbox"
                            checked={selected}
                            onChange={() => onToggle(item)}
                            className="mt-1 h-4 w-4 shrink-0 rounded border-border text-primary focus:ring-ring/40"
                          />
                          <span className="min-w-0">
                            <span className="block break-all font-mono text-xs font-semibold text-primary">
                              {item.qualifiedName}
                            </span>
                            <span className="sr-only">
                              {t("metadataSql.targets.grid.toggleHint")}
                            </span>
                          </span>
                        </label>
                      </td>
                      <td className="whitespace-nowrap px-3 py-1 align-top">
                        <StatusBadge variant="neutral" label={targetTypeLabel(item.object_type)} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-1 align-top font-mono text-xs text-muted">
                        {item.owner || "-"}
                      </td>
                      <td className="break-words px-3 py-1 align-top text-sm text-foreground">
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
      {!loading && (
        <DbObjectSelectorFooter
          visibleCount={items.length}
          totalCount={totalCount}
          selectedCount={selectedKeys.length}
          hasNextPage={hasNextPage}
          loadingNextPage={loadingNextPage}
          loadMoreError={loadMoreError}
          dataTestId={`${pageId}-target-footer`}
          onLoadMore={onLoadMore}
          onRetryLoadMore={onRetryLoadMore}
        />
      )}
      {!loading && (
        <ContentActionBar
          ariaLabel={t("metadataSql.targets.actions")}
          title={t("metadataSql.targets.fetchActionTitle")}
          description={
            selectedKeys.length > 0
              ? t("metadataSql.targets.fetchActionReady", { count: selectedKeys.length })
              : t("metadataSql.targets.fetchActionDisabled")
          }
          actionsClassName="w-full sm:w-auto"
          testId={`${pageId}-target-actions`}
        >
          <Button
            type="button"
            variant="primary"
            size="sm"
            className="w-full sm:w-auto"
            loading={fetchingDetails}
            disabled={selectedKeys.length === 0}
            onClick={onFetchDetails}
          >
            <span>{t("metadataSql.action.fetchInfo")}</span>
          </Button>
        </ContentActionBar>
      )}
    </section>
  );
}

function MetadataInputPanel({
  pageId,
  inputTexts,
  detailsReady,
  detailsLoading,
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
  detailsLoading: boolean;
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
      />

      {detailsLoading ? (
        <DbManagementLoadingSkeleton
          idPrefix={`${pageId}-input`}
          ariaLabel={t("metadataSql.input.loading")}
          variant="detail"
          placement="result"
        />
      ) : (
        <>
          {!detailsReady && (
            <EmptyState title={t("metadataSql.input.emptyTitle")} hint={t("metadataSql.input.emptyHint")} />
          )}

          <div className="grid gap-3 rounded-md border border-border bg-background p-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
              <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground sm:w-44">
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
                  className="min-h-11 w-full rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
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

          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("metadataSql.input.extra")}</span>
            <textarea
              value={extraText}
              onChange={(event) => onExtraTextChange(event.currentTarget.value)}
              rows={6}
              className="min-h-32 rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 focus:border-primary focus:ring-2 focus:ring-ring/40"
            />
          </label>

          <ContentActionBar
            ariaLabel={t("metadataSql.input.actions")}
            title={t("metadataSql.input.generateActionTitle")}
            description={
              detailsReady
                ? t("metadataSql.input.generateActionReady", { count: selectedCount })
                : t("metadataSql.input.generateActionDisabled")
            }
            actionsClassName="w-full sm:w-auto"
            testId={`${pageId}-input-actions`}
          >
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
          </ContentActionBar>
        </>
      )}
    </div>
  );
}

function MetadataExecutePanel({
  pageId,
  mode,
  generated,
  loading,
  policy,
  onExecuted,
}: {
  pageId: string;
  mode: MetadataMode;
  generated: MetadataSqlGenerateData | null;
  loading: boolean;
  policy: DbAdminStatementPolicy;
  onExecuted: (result: DbAdminExecuteData) => void | Promise<void>;
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

      {loading ? (
        <DbManagementLoadingSkeleton
          idPrefix={`${pageId}-execute-result`}
          ariaLabel={t("metadataSql.execute.loading")}
          variant="detail"
          placement="result"
        />
      ) : (
        <>
          {!generated && (
            <EmptyState title={t("metadataSql.execute.emptyTitle")} hint={t("metadataSql.execute.emptyHint")} />
          )}

          {generated?.warnings.map((warning) => (
            <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
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
        </>
      )}
    </div>
  );
}

function MetadataTextarea({ label, value, rows }: { label: string; value: string; rows: number }) {
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
      <span>{label}</span>
      <textarea
        readOnly
        value={value}
        rows={rows}
        className="rounded-md border border-border bg-background px-3 py-2 font-mono text-sm leading-6 text-foreground"
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
      className="inline-flex items-center gap-1 whitespace-nowrap text-left font-semibold text-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
      aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <ArrowDownUp size={13} className={active ? "text-primary" : "text-muted"} aria-hidden="true" />
    </button>
  );
}

function targetItemsFromObjects(items: DbAdminObjectSummary[]) {
  return items.map((item): MetadataTargetItem => {
    const objectType: MetadataSqlTarget["object_type"] = item.object_type === "view" ? "view" : "table";
    const qualifiedName = dbAdminObjectQualifiedName(item);
    const target: MetadataSqlTarget = {
      owner: item.owner,
      object_name: item.name,
      object_type: objectType,
    };
    return {
      ...target,
      key: targetKey(target),
      qualifiedName,
      owner: item.owner,
      row_count: item.row_count,
      comment: item.comment,
    };
  });
}

function targetSortValue(item: MetadataTargetItem, key: TargetSortKey) {
  if (key === "object_type") return item.object_type;
  if (key === "owner") return item.owner.toLowerCase();
  return item.qualifiedName.toLowerCase();
}

function targetTypeLabel(objectType: MetadataSqlTarget["object_type"]) {
  return objectType === "view" ? t("metadataSql.targets.type.view") : t("metadataSql.targets.type.table");
}

function targetKey(target: MetadataSqlTarget) {
  const qualifiedName = parseDbAdminObjectTarget(target.object_name, target.owner).qualifiedName;
  return `${target.object_type}:${qualifiedName}`;
}

function targetFromKey(key: string): MetadataSqlTarget | null {
  const [objectType, ...nameParts] = key.split(":");
  const qualifiedName = nameParts.join(":");
  if ((objectType !== "table" && objectType !== "view") || !qualifiedName) return null;
  const target = parseDbAdminObjectTarget(qualifiedName);
  return { owner: target.owner, object_name: target.name, object_type: objectType };
}
