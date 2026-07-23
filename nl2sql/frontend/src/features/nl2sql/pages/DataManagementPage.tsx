import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Code2, Database, Eye, FileSpreadsheet, RefreshCw, Search, Table2, Upload } from "lucide-react";

import { Button, EmptyState, StatusBadge, toast } from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { PageNotice } from "@/components/page-notice";
import { ErrorState } from "@/components/StateViews";
import { apiFetch, apiGet, apiPost, isTimeoutError } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import {
  ExecutionConfirmationField,
  FileInputControl,
  QueryResultsTable,
  StatementRunnerCard,
  downloadBlob,
  fileToBase64,
} from "../components/DbAdminShared";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import {
  buildDeleteTemplate,
  buildInsertTemplate,
  buildMergeTemplate,
  buildMultiInsertTemplate,
  buildUpdateTemplate,
} from "../sqlTemplates";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import {
  useDbAdminObjects,
  useSchemaRefreshJob,
  useStartSchemaRefresh,
} from "../incrementalQueries";
import type {
  DbAdminCsvUploadData,
  DbAdminDataPreviewData,
  DbAdminObjectsData,
  SchemaRefreshJob,
  SelectAiDbProfile,
  SelectAiDbProfileDetailData,
  SelectAiDbProfilesData,
  SyntheticDataOperationData,
  SyntheticDataOperationStatusData,
  SyntheticDataResultsData,
} from "../types";

type ActiveView = "preview" | "csv" | "sql" | "synthetic";
type CsvStep = "file" | "execute";
type CsvMode = "insert" | "truncate_insert";
type PreviewObjectKind = "table" | "view";
type PreviewObjectKindFilter = "all" | PreviewObjectKind;
type PreviewObjectRowFilter = "all" | "with_rows" | "empty_rows" | "unknown_rows";
type SyntheticLoading = "" | "tables" | "generate" | "status" | "results";

const DATA_MANAGEMENT_ID = "data-management";

interface PreviewObject {
  name: string;
  kind: PreviewObjectKind;
  owner: string;
  rowCount?: number | null;
  comment: string;
}

function resolveBusinessSelectAiProfileName(current: string, profiles: SelectAiDbProfile[]) {
  if (current && profiles.some((profile) => profile.name === current)) return current;
  return profiles[0]?.name ?? "";
}

export function DataManagementPage() {
  const [activeView, setActiveView] = useState<ActiveView>("preview");
  const [previewObject, setPreviewObject] = useState("");
  const [previewObjectSearch, setPreviewObjectSearch] = useState("");
  const [previewObjectKindFilter, setPreviewObjectKindFilter] = useState<PreviewObjectKindFilter>("all");
  const [previewObjectRowFilter, setPreviewObjectRowFilter] = useState<PreviewObjectRowFilter>("all");
  const [previewLimit, setPreviewLimit] = useState(100);
  const [previewWhere, setPreviewWhere] = useState("");
  const [preview, setPreview] = useState<DbAdminDataPreviewData | null>(null);
  const [csvTable, setCsvTable] = useState("");
  const [csvFilename, setCsvFilename] = useState("");
  const [csvBase64, setCsvBase64] = useState("");
  const [csvMode, setCsvMode] = useState<CsvMode>("insert");
  const [csvStep, setCsvStep] = useState<CsvStep>("file");
  const [csvConfirmation, setCsvConfirmation] = useState("");
  const [csvUploadResult, setCsvUploadResult] = useState<DbAdminCsvUploadData | null>(null);
  const [syntheticData, setSyntheticData] = useState<SyntheticDataOperationData | null>(null);
  const [syntheticDataStatus, setSyntheticDataStatus] =
    useState<SyntheticDataOperationStatusData | null>(null);
  const [syntheticDataResults, setSyntheticDataResults] = useState<SyntheticDataResultsData | null>(null);
  const [syntheticProfileName, setSyntheticProfileName] = useState("");
  const [syntheticAvailableTables, setSyntheticAvailableTables] = useState<string[]>([]);
  const [syntheticSelectedTables, setSyntheticSelectedTables] = useState<string[]>([]);
  const [syntheticPrompt, setSyntheticPrompt] = useState("");
  const [syntheticConfirmation, setSyntheticConfirmation] = useState("");
  const [syntheticRows, setSyntheticRows] = useState(1);
  const [syntheticSampleRows, setSyntheticSampleRows] = useState(5);
  const [syntheticUseComments, setSyntheticUseComments] = useState(true);
  const [syntheticResultTable, setSyntheticResultTable] = useState("");
  const [syntheticResultLimit, setSyntheticResultLimit] = useState(100);
  const [previewLoadingObject, setPreviewLoadingObject] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [exportLoading, setExportLoading] = useState(false);
  const [exportError, setExportError] = useState("");
  const [csvUploading, setCsvUploading] = useState(false);
  const [csvUploadError, setCsvUploadError] = useState("");
  const [syntheticLoading, setSyntheticLoading] = useState<SyntheticLoading>("");
  const [syntheticError, setSyntheticError] = useState("");
  const [syntheticErrorOperation, setSyntheticErrorOperation] = useState<SyntheticLoading>("");
  const [schemaJobId, setSchemaJobId] = useState("");
  const [schemaJobError, setSchemaJobError] = useState("");
  const completedSchemaJob = useRef("");
  const previewRequestSequence = useRef(0);
  const debouncedObjectSearch = useDebouncedValue(previewObjectSearch, 250);
  const baseObjectsQuery = useDbAdminObjects("", "all", "all");
  const previewObjectsQuery = useDbAdminObjects(
    debouncedObjectSearch,
    previewObjectKindFilter,
    previewObjectRowFilter
  );
  const startSchemaRefresh = useStartSchemaRefresh();
  const schemaJobQuery = useSchemaRefreshJob(schemaJobId);
  const selectAiProfilesQuery = useQuery({
    queryKey: ["nl2sql", "select-ai", "db-profiles", "business"],
    queryFn: ({ signal }) =>
      apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL, {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveList,
      }),
    enabled: activeView === "synthetic",
    staleTime: 5_000,
    retry: false,
  });
  const selectAiDbProfiles = selectAiProfilesQuery.data ?? null;
  const baseObjectPages = baseObjectsQuery.data?.pages ?? [];
  const baseObjectItems = baseObjectPages.flatMap((page) => page.items);
  const dbAdminTables: DbAdminObjectsData = {
    runtime: baseObjectPages[0]?.runtime ?? "",
    items: baseObjectItems.filter((item) => item.object_type === "table"),
    refreshed_at: baseObjectPages[0]?.refreshed_at ?? "",
    warnings: baseObjectPages.flatMap((page) => page.warnings),
  };

  const previewObjects = useMemo<PreviewObject[]>(() => {
    return (previewObjectsQuery.data?.pages ?? []).flatMap((page) =>
      page.items.map((item) => ({
        name: item.name,
        kind: item.object_type === "view" ? ("view" as const) : ("table" as const),
        owner: item.owner,
        rowCount: item.row_count,
        comment: item.comment,
      }))
    );
  }, [previewObjectsQuery.data]);

  const filteredPreviewObjects = useMemo(
    () =>
      filterPreviewObjects(
        previewObjects,
        previewObjectSearch,
        previewObjectKindFilter,
        previewObjectRowFilter
      ),
    [previewObjects, previewObjectKindFilter, previewObjectRowFilter, previewObjectSearch]
  );

  const selectedSyntheticProfile = useMemo(
    () => selectAiDbProfiles?.profiles.find((profile) => profile.name === syntheticProfileName) ?? null,
    [selectAiDbProfiles, syntheticProfileName]
  );
  // 対象名確認: CSV は選択テーブル名、synthetic は単一テーブル指定時のみ対象名
  // (複数テーブル指定は単一対象名が無いため ADMIN_EXECUTE)。backend の検証と一致させる。
  const csvConfirmed = Boolean(csvTable.trim()) && csvConfirmation.trim() === csvTable.trim();
  const canUploadCsv = Boolean(csvTable && csvBase64 && csvConfirmed);
  const syntheticExpectedConfirmation =
    syntheticSelectedTables.length === 1 ? syntheticSelectedTables[0] : "ADMIN_EXECUTE";
  const syntheticDataConfirmed = syntheticConfirmation.trim() === syntheticExpectedConfirmation;
  const canGenerateSyntheticData = Boolean(
    syntheticProfileName.trim() && syntheticSelectedTables.length > 0 && syntheticDataConfirmed
  );

  const clearSyntheticProfileTargets = () => {
    setSyntheticAvailableTables([]);
    setSyntheticSelectedTables([]);
    setSyntheticResultTable("");
    setSyntheticData(null);
    setSyntheticDataStatus(null);
    setSyntheticDataResults(null);
  };

  const changeSyntheticProfileName = (value: string) => {
    if (value === syntheticProfileName) return;
    setSyntheticProfileName(value);
    clearSyntheticProfileTargets();
  };

  useEffect(() => {
    const profiles = selectAiProfilesQuery.data;
    if (!profiles) return;
    const nextName = resolveBusinessSelectAiProfileName(syntheticProfileName, profiles.profiles);
    setSyntheticProfileName(nextName);
    if (syntheticProfileName && syntheticProfileName !== nextName) {
      clearSyntheticProfileTargets();
    }
  }, [selectAiProfilesQuery.data]);

  useEffect(() => {
    const firstObject = previewObjects[0]?.name ?? "";
    setPreviewObject((current) =>
      current && previewObjects.some((item) => item.name === current) ? current : firstObject
    );
  }, [previewObjects]);

  useEffect(() => {
    const firstTable = dbAdminTables.items[0]?.name ?? "";
    setCsvTable((current) =>
      current && dbAdminTables.items.some((item) => item.name === current) ? current : firstTable
    );
  }, [baseObjectsQuery.data]);

  useEffect(() => {
    const job = schemaJobQuery.data;
    if (!job || completedSchemaJob.current === `${job.job_id}:${job.status}`) return;
    if (job.status === "done") {
      completedSchemaJob.current = `${job.job_id}:${job.status}`;
      setSchemaJobError("");
      toast.success(t("dataMgmt.schemaJob.done"));
      void baseObjectsQuery.refetch();
      void previewObjectsQuery.refetch();
    } else if (job.status === "error") {
      completedSchemaJob.current = `${job.job_id}:${job.status}`;
      setSchemaJobError(t("dataMgmt.schemaJob.error"));
    }
  }, [schemaJobQuery.data]);

  const refreshObjects = async (announce = false) => {
    setSchemaJobError("");
    const results = await Promise.all([
      baseObjectsQuery.refetch(),
      previewObjectsQuery.refetch(),
    ]);
    if (announce && results.every((result) => !result.isError)) {
      toast.success(t("common.action.refreshed"));
    }
  };

  const submitSchemaRefresh = async () => {
    setSchemaJobError("");
    try {
      const job = await startSchemaRefresh.mutateAsync();
      setSchemaJobId(job.job_id);
      toast.success(t("dataMgmt.schemaJob.accepted"));
      if (!job.job_id && job.status === "done") await refreshObjects();
    } catch (error) {
      setSchemaJobError(apiErrorMessage(error, "dataMgmt.schemaJob.submitError"));
    }
  };

  const selectPreviewObject = (objectName: string) => {
    previewRequestSequence.current += 1;
    setPreviewLoadingObject("");
    setPreviewObject(objectName);
    setPreview(null);
    setPreviewError("");
    setExportError("");
  };

  const showPreview = async (objectName: string) => {
    if (!objectName) return;
    const sequence = previewRequestSequence.current + 1;
    previewRequestSequence.current = sequence;
    setPreviewObject(objectName);
    setPreview(null);
    setPreviewLoadingObject(objectName);
    setPreviewError("");
    setExportError("");
    try {
      const result = await apiPost<DbAdminDataPreviewData>(
        "/api/nl2sql/db-admin/preview-data",
        {
          object_name: objectName,
          limit: previewLimit,
          where_clause: previewWhere,
        },
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail }
      );
      if (sequence !== previewRequestSequence.current) return;
      setPreview(result);
    } catch (err) {
      if (sequence !== previewRequestSequence.current) return;
      setPreviewError(apiErrorMessage(err, "dataMgmt.error.preview"));
    } finally {
      if (sequence === previewRequestSequence.current) setPreviewLoadingObject("");
    }
  };

  const downloadPreviewXlsx = async () => {
    if (!previewObject) return;
    setExportLoading(true);
    setExportError("");
    try {
      const response = await apiFetch("/api/nl2sql/db-admin/preview-data/export.xlsx", {
        method: "POST",
        headers: {
          Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          object_name: previewObject,
          limit: previewLimit,
          where_clause: previewWhere,
        }),
        signal: AbortSignal.timeout(API_TIMEOUT_MS.interactiveDetail),
      });
      if (!response.ok) {
        throw new Error(await previewExportError(response));
      }
      const filename = `${previewObject.toLowerCase()}_preview.xlsx`;
      downloadBlob(filename, await response.blob());
      toast.success(t("common.action.downloaded"));
    } catch (err) {
      setExportError(apiErrorMessage(err, "dataMgmt.error.previewExport"));
    } finally {
      setExportLoading(false);
    }
  };

  const pickCsvFile = async (file: File | undefined) => {
    if (!file) return;
    setCsvFilename(file.name);
    setCsvBase64(await fileToBase64(file));
    setCsvUploadResult(null);
    setCsvStep("file");
  };

  const clearCsvFile = () => {
    setCsvFilename("");
    setCsvBase64("");
    setCsvUploadResult(null);
    setCsvStep("file");
  };

  const uploadCsv = async () => {
    if (!canUploadCsv) return;
    setCsvUploading(true);
    setCsvUploadError("");
    try {
      const result = await apiPost<DbAdminCsvUploadData>("/api/nl2sql/db-admin/upload-csv", {
        table_name: csvTable,
        content_base64: csvBase64,
        filename: csvFilename || "upload.csv",
        mode: csvMode,
        confirmation: csvConfirmation,
        reason: "ui-data-management-csv",
      }, { timeoutMs: API_TIMEOUT_MS.interactiveDetail });
      setCsvUploadResult(result);
      setCsvStep("execute");
      if (result.executed) {
        toast.success(t("dataMgmt.csv.successToast"));
        await baseObjectsQuery.refetch();
        await previewObjectsQuery.refetch();
      }
    } catch (err) {
      setCsvUploadError(apiErrorMessage(err, "dataMgmt.error.csvUpload"));
    } finally {
      setCsvUploading(false);
    }
  };

  const refreshSyntheticTables = async () => {
    const profileName = syntheticProfileName.trim();
    if (!profileName) return;
    setSyntheticLoading("tables");
    setSyntheticError("");
    setSyntheticErrorOperation("");
    try {
      let profile = selectedSyntheticProfile;
      try {
        const detail = await apiGet<SelectAiDbProfileDetailData>(
          `/api/nl2sql/select-ai/db-profiles/${encodeURIComponent(profileName)}`,
          { timeoutMs: API_TIMEOUT_MS.interactiveDetail }
        );
        profile = detail.profile;
      } catch {
        profile = selectedSyntheticProfile;
      }
      const profileTables = profileObjectNames(profile);
      const nextTables = profileTables;
      setSyntheticAvailableTables(nextTables);
      setSyntheticSelectedTables((current) => current.filter((tableName) => nextTables.includes(tableName)));
      setSyntheticResultTable((current) => (current && nextTables.includes(current) ? current : (nextTables[0] ?? "")));
      setSyntheticData(null);
      setSyntheticDataStatus(null);
      setSyntheticDataResults(null);
    } catch (err) {
      setSyntheticError(apiErrorMessage(err, "dataTools.error.load"));
      setSyntheticErrorOperation("tables");
    } finally {
      setSyntheticLoading("");
    }
  };

  const generateSyntheticData = async () => {
    if (!canGenerateSyntheticData) return;
    const selectedTables = syntheticSelectedTables;
    const singleTable = selectedTables.length === 1;
    setSyntheticLoading("generate");
    setSyntheticError("");
    setSyntheticErrorOperation("");
    try {
      setSyntheticDataStatus(null);
      setSyntheticDataResults(null);
      const result = await apiPost<SyntheticDataOperationData>("/api/nl2sql/synthetic-data/generate", {
        table_name: singleTable ? selectedTables[0] : "",
        object_list: singleTable ? [] : selectedTables,
        row_count: syntheticRows,
        rows_per_table: syntheticRows,
        profile_name: syntheticProfileName,
        user_prompt: syntheticPrompt,
        sample_rows: syntheticSampleRows,
        use_comments: syntheticUseComments,
        confirmation: syntheticConfirmation,
        reason: "ui-synthetic-data",
      }, { timeoutMs: API_TIMEOUT_MS.jobControl });
      setSyntheticData(result);
      setSyntheticResultTable((current) => {
        const allowedTables = syntheticAvailableTables;
        if (current && allowedTables.includes(current)) return current;
        if (result.table_name && allowedTables.includes(result.table_name)) return result.table_name;
        return selectedTables.find((tableName) => allowedTables.includes(tableName)) ?? allowedTables[0] ?? "";
      });
      const operationId = result.operation_id.trim();
      if (operationId) {
        setSyntheticDataStatus(await fetchSyntheticDataStatus(operationId));
      }
    } catch (err) {
      setSyntheticError(apiErrorMessage(err, "dataTools.error.syntheticData"));
      setSyntheticErrorOperation("generate");
    } finally {
      setSyntheticLoading("");
    }
  };

  const checkSyntheticDataStatus = async () => {
    const operationId = syntheticData?.operation_id.trim();
    if (!operationId) return;
    setSyntheticLoading("status");
    setSyntheticError("");
    setSyntheticErrorOperation("");
    try {
      setSyntheticDataStatus(await fetchSyntheticDataStatus(operationId));
    } catch (err) {
      setSyntheticError(apiErrorMessage(err, "dataTools.error.syntheticStatus"));
      setSyntheticErrorOperation("status");
    } finally {
      setSyntheticLoading("");
    }
  };

  const loadSyntheticDataResults = async () => {
    const tableName = syntheticResultTable.trim();
    if (!tableName || !syntheticAvailableTables.includes(tableName)) return;
    setSyntheticLoading("results");
    setSyntheticError("");
    setSyntheticErrorOperation("");
    try {
      setSyntheticDataResults(
        await apiGet<SyntheticDataResultsData>(
          `/api/nl2sql/synthetic-data/results?table_name=${encodeURIComponent(tableName)}&limit=${syntheticResultLimit}`,
          { timeoutMs: API_TIMEOUT_MS.interactiveDetail }
        )
      );
    } catch (err) {
      setSyntheticError(apiErrorMessage(err, "dataTools.error.syntheticResults"));
      setSyntheticErrorOperation("results");
    } finally {
      setSyntheticLoading("");
    }
  };

  const objectQueryError = previewObjectsQuery.error ?? baseObjectsQuery.error;
  const objectErrorMessage = objectQueryError
    ? apiErrorMessage(
        objectQueryError,
        "dataMgmt.objectList.error",
        "dataMgmt.objectList.timeout"
      )
    : "";
  const firstObjectPage = baseObjectPages[0];
  const schemaJob = schemaJobQuery.data ?? null;
  const visibleSchemaJobError = schemaJobQuery.error
    ? apiErrorMessage(schemaJobQuery.error, "dataMgmt.schemaJob.error")
    : schemaJobError;
  const schemaRefreshing =
    !schemaJobQuery.error &&
    (startSchemaRefresh.isPending || schemaJob?.status === "pending" || schemaJob?.status === "running");

  return (
    <>
      <PageHeader
        title={t("nav.dataManagement")}
        subtitle={t("dataMgmt.subtitle")}
        meta={
          firstObjectPage?.refreshed_at
            ? t("common.schemaRefreshedAt", {
                date: formatDateTime(firstObjectPage.refreshed_at),
              })
            : undefined
        }
        status={
          schemaJob ? (
            <span aria-live="polite" aria-atomic="true">
              <StatusBadge
                variant={
                  schemaJob.status === "done"
                    ? "success"
                    : schemaJob.status === "error"
                      ? "danger"
                      : "info"
                }
                label={schemaJobLabel(schemaJob)}
              />
            </span>
          ) : undefined
        }
        actions={[
          {
            id: "refresh-data",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: baseObjectsQuery.isFetching || previewObjectsQuery.isFetching,
            onClick: () => void refreshObjects(true),
          },
          {
            id: "refresh-data-schema",
            kind: "utility",
            label: t("common.action.schemaRefresh"),
            icon: RefreshCw,
            loading: schemaRefreshing,
            disabled: schemaRefreshing,
            onClick: () => void submitSchemaRefresh(),
          },
        ]}
        actionsTestId="data-management-actions"
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={
            visibleSchemaJobError
              ? { tone: "danger", message: visibleSchemaJobError }
              : null
          }
        />

        <DbObjectManagementTabs
          activeView={activeView}
          tabs={[
            { id: "preview", label: t("dataMgmt.preview.title"), icon: Table2 },
            { id: "csv", label: t("dataMgmt.csv.title"), icon: Upload },
            { id: "sql", label: t("dataMgmt.sql.title"), icon: Code2 },
            { id: "synthetic", label: t("dataTools.synthetic.title"), icon: Database },
          ] satisfies Array<DbObjectTab<ActiveView>>}
          idPrefix={DATA_MANAGEMENT_ID}
          ariaLabel={t("dataMgmt.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "preview" && (
          <DbObjectManagementPanelShell
            id="data-management-panel-preview"
            labelledBy="data-management-tab-preview"
            idPrefix={DATA_MANAGEMENT_ID}
            ariaLabel={t("dataMgmt.workspace.preview")}
            splitId="data-management-preview"
            preferredWidePane="right"
            minLeftPaneWidthPx={520}
            minRightPaneWidthPx={560}
          >
            <PreviewControlsPanel
              previewObjects={previewObjects}
              filteredPreviewObjects={filteredPreviewObjects}
              previewObject={previewObject}
              previewObjectSearch={previewObjectSearch}
              previewObjectKindFilter={previewObjectKindFilter}
              previewObjectRowFilter={previewObjectRowFilter}
              previewLimit={previewLimit}
              previewWhere={previewWhere}
              loadingObjectName={previewLoadingObject}
              initialLoading={previewObjectsQuery.isPending && !previewObjectsQuery.data}
              error={objectErrorMessage}
              hasNextPage={Boolean(previewObjectsQuery.hasNextPage)}
              loadingNextPage={previewObjectsQuery.isFetchingNextPage}
              onPreviewObjectChange={selectPreviewObject}
              onPreviewObjectSearchChange={setPreviewObjectSearch}
              onPreviewObjectKindFilterChange={setPreviewObjectKindFilter}
              onPreviewObjectRowFilterChange={setPreviewObjectRowFilter}
              onPreviewLimitChange={setPreviewLimit}
              onPreviewWhereChange={setPreviewWhere}
              onShowPreview={(objectName) => void showPreview(objectName)}
              onRetry={() => void refreshObjects()}
              onLoadMore={() => void previewObjectsQuery.fetchNextPage()}
            />
            <PreviewResultsPanel
              preview={preview}
              loading={Boolean(previewLoadingObject)}
              exporting={exportLoading}
              previewError={previewError}
              exportError={exportError}
              onRetryPreview={() => void showPreview(previewObject)}
              onDownload={() => void downloadPreviewXlsx()}
            />
          </DbObjectManagementPanelShell>
        )}

        {activeView === "csv" && (
          <DbObjectManagementPanelShell
            id="data-management-panel-csv"
            labelledBy="data-management-tab-csv"
            idPrefix={DATA_MANAGEMENT_ID}
            ariaLabel={t("dataMgmt.workspace.csv")}
          >
            <CsvUploadWorkspace
              tables={dbAdminTables.items}
              table={csvTable}
              filename={csvFilename}
              fileReady={Boolean(csvBase64)}
              mode={csvMode}
              step={csvStep}
              confirmation={csvConfirmation}
              confirmed={csvConfirmed}
              canUpload={canUploadCsv}
              result={csvUploadResult}
              loading={csvUploading}
              error={csvUploadError}
              hasNextPage={Boolean(baseObjectsQuery.hasNextPage)}
              loadingNextPage={baseObjectsQuery.isFetchingNextPage}
              onTableChange={(value) => {
                setCsvTable(value);
                setCsvUploadResult(null);
                setCsvStep("file");
              }}
              onModeChange={(value) => {
                setCsvMode(value);
                setCsvUploadResult(null);
                setCsvStep("file");
              }}
              onFilePick={(file) => void pickCsvFile(file)}
              onFileClear={clearCsvFile}
              onConfirmationChange={(value) => {
                setCsvConfirmation(value);
                if (value.trim()) setCsvStep("execute");
              }}
              onUpload={() => void uploadCsv()}
              onRetry={() => void uploadCsv()}
              onLoadMore={() => void baseObjectsQuery.fetchNextPage()}
            />
          </DbObjectManagementPanelShell>
        )}

        {activeView === "sql" && (
          <DbObjectManagementPanelShell
            id="data-management-panel-sql"
            labelledBy="data-management-tab-sql"
            idPrefix={DATA_MANAGEMENT_ID}
            ariaLabel={t("dataMgmt.workspace.sql")}
          >
            <StatementRunnerCard
              policy="data_dml"
              title={t("dataMgmt.sql.title")}
              description={t("dataMgmt.section.sqlHint")}
              placeholder={t("dataMgmt.sql.placeholder")}
              templates={[
                { label: t("dataMgmt.template.insert"), build: () => buildInsertTemplate(null) },
                { label: t("dataMgmt.template.insertMulti"), build: () => buildMultiInsertTemplate(null) },
                { label: t("dataMgmt.template.update"), build: () => buildUpdateTemplate(null) },
                { label: t("dataMgmt.template.delete"), build: () => buildDeleteTemplate(null) },
                { label: t("dataMgmt.template.merge"), build: () => buildMergeTemplate(null) },
              ]}
              confirmationTitle={t("dataMgmt.sql.executeTitle")}
              executeOnly
              framed={false}
              onExecuted={() => {
                void baseObjectsQuery.refetch();
                void previewObjectsQuery.refetch();
              }}
            />
            <p className="px-1 text-xs text-muted">{t("dataMgmt.sql.note")}</p>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "synthetic" && (
          <DbObjectManagementPanelShell
            id="data-management-panel-synthetic"
            labelledBy="data-management-tab-synthetic"
            idPrefix={DATA_MANAGEMENT_ID}
            ariaLabel={t("dataMgmt.workspace.synthetic")}
          >
            {selectAiProfilesQuery.isPending ? (
              <DbManagementLoadingSkeleton
                idPrefix="data-synthetic-profiles"
                ariaLabel={t("dataTools.syntheticData.profilesLoading")}
                variant="detail"
              />
            ) : selectAiProfilesQuery.error ? (
              <ErrorState
                message={apiErrorMessage(selectAiProfilesQuery.error, "dataMgmt.profiles.error")}
                onRetry={() => void selectAiProfilesQuery.refetch()}
              />
            ) : (
            <SyntheticWorkspace
              selectAiDbProfiles={selectAiDbProfiles}
              selectedSyntheticProfile={selectedSyntheticProfile}
              syntheticData={syntheticData}
              syntheticDataStatus={syntheticDataStatus}
              syntheticDataResults={syntheticDataResults}
              syntheticProfileName={syntheticProfileName}
              syntheticAvailableTables={syntheticAvailableTables}
              syntheticSelectedTables={syntheticSelectedTables}
              syntheticPrompt={syntheticPrompt}
              syntheticConfirmation={syntheticConfirmation}
              syntheticDataConfirmed={syntheticDataConfirmed}
              canGenerateSyntheticData={canGenerateSyntheticData}
              syntheticRows={syntheticRows}
              syntheticSampleRows={syntheticSampleRows}
              syntheticUseComments={syntheticUseComments}
              syntheticResultTable={syntheticResultTable}
              syntheticResultLimit={syntheticResultLimit}
              loading={syntheticLoading}
              error={syntheticError}
              onRefreshTables={() => void refreshSyntheticTables()}
              onSyntheticProfileNameChange={changeSyntheticProfileName}
              onSyntheticTableToggle={(tableName, selected) => {
                const nextTables = selected
                  ? uniqueStrings([...syntheticSelectedTables, tableName])
                  : syntheticSelectedTables.filter((item) => item !== tableName);
                setSyntheticSelectedTables(nextTables);
                setSyntheticResultTable((currentTable) =>
                  currentTable && nextTables.includes(currentTable) ? currentTable : (nextTables[0] ?? "")
                );
                setSyntheticData(null);
                setSyntheticDataStatus(null);
                setSyntheticDataResults(null);
              }}
              onSyntheticPromptChange={setSyntheticPrompt}
              onSyntheticConfirmationChange={setSyntheticConfirmation}
              onSyntheticRowsChange={(value) => setSyntheticRows(clampNumber(value, 1, 100))}
              onSyntheticSampleRowsChange={(value) => setSyntheticSampleRows(clampNumber(value, 0, 100))}
              onSyntheticUseCommentsChange={setSyntheticUseComments}
              onSyntheticResultTableChange={(value) => {
                if (!syntheticAvailableTables.includes(value)) return;
                setSyntheticResultTable(value);
                setSyntheticDataResults(null);
              }}
              onSyntheticResultLimitChange={(value) => setSyntheticResultLimit(clampNumber(value, 1, 10000))}
              onGenerateSyntheticData={() => void generateSyntheticData()}
              onCheckSyntheticDataStatus={() => void checkSyntheticDataStatus()}
              onLoadSyntheticDataResults={() => void loadSyntheticDataResults()}
              onRetry={() => {
                if (syntheticErrorOperation === "tables") void refreshSyntheticTables();
                else if (syntheticErrorOperation === "status") void checkSyntheticDataStatus();
                else if (syntheticErrorOperation === "results") void loadSyntheticDataResults();
                else void generateSyntheticData();
              }}
            />
            )}
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}

function PreviewControlsPanel({
  previewObjects,
  filteredPreviewObjects,
  previewObject,
  previewObjectSearch,
  previewObjectKindFilter,
  previewObjectRowFilter,
  previewLimit,
  previewWhere,
  loadingObjectName,
  initialLoading,
  error,
  hasNextPage,
  loadingNextPage,
  onPreviewObjectChange,
  onPreviewObjectSearchChange,
  onPreviewObjectKindFilterChange,
  onPreviewObjectRowFilterChange,
  onPreviewLimitChange,
  onPreviewWhereChange,
  onShowPreview,
  onRetry,
  onLoadMore,
}: {
  previewObjects: PreviewObject[];
  filteredPreviewObjects: PreviewObject[];
  previewObject: string;
  previewObjectSearch: string;
  previewObjectKindFilter: PreviewObjectKindFilter;
  previewObjectRowFilter: PreviewObjectRowFilter;
  previewLimit: number;
  previewWhere: string;
  loadingObjectName: string;
  initialLoading: boolean;
  error: string;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  onPreviewObjectChange: (value: string) => void;
  onPreviewObjectSearchChange: (value: string) => void;
  onPreviewObjectKindFilterChange: (value: PreviewObjectKindFilter) => void;
  onPreviewObjectRowFilterChange: (value: PreviewObjectRowFilter) => void;
  onPreviewLimitChange: (value: number) => void;
  onPreviewWhereChange: (value: string) => void;
  onShowPreview: (objectName: string) => void;
  onRetry: () => void;
  onLoadMore: () => void;
}) {
  const tableCount = previewObjects.filter((item) => item.kind === "table").length;
  const viewCount = previewObjects.filter((item) => item.kind === "view").length;
  const selectedObject = previewObjects.find((item) => item.name === previewObject) ?? null;
  const hasActiveFilter =
    Boolean(previewObjectSearch.trim()) ||
    previewObjectKindFilter !== "all" ||
    previewObjectRowFilter !== "all";

  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="data-preview-controls-heading">
      <DbObjectPanelHeader
        headingId="data-preview-controls-heading"
        icon={Table2}
        title={t("dataMgmt.preview.title")}
        description={t("dataMgmt.preview.controlsHint")}
        action={
          <>
            <StatusBadge variant="info" label={t("dataMgmt.preview.objectTotalCount", { count: previewObjects.length })} />
            <StatusBadge variant="neutral" label={t("dataMgmt.preview.objectTableCount", { count: tableCount })} />
            <StatusBadge variant="neutral" label={t("dataMgmt.preview.objectViewCount", { count: viewCount })} />
          </>
        }
      />

      <div className="grid gap-3 rounded-md border border-border bg-background p-3">
        <div className="grid gap-2">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="grid min-w-0 flex-1 gap-1 text-sm font-medium text-foreground">
              <span>{t("dataMgmt.preview.search")}</span>
              <span className="relative block min-w-0">
                <Search
                  size={16}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                  aria-hidden="true"
                />
                <input
                  type="search"
                  value={previewObjectSearch}
                  onChange={(event) => onPreviewObjectSearchChange(event.currentTarget.value)}
                  placeholder={t("dataMgmt.preview.searchPlaceholder")}
                  className="min-h-11 w-full min-w-0 rounded-md border border-border bg-card py-2 pl-9 pr-3 focus:border-primary focus:ring-2 focus:ring-ring/40"
                />
              </span>
            </label>
            <label className="grid gap-1 text-sm font-medium text-foreground sm:w-36">
              <span>{t("dataMgmt.preview.kindFilter")}</span>
              <select
                value={previewObjectKindFilter}
                onChange={(event) => onPreviewObjectKindFilterChange(event.currentTarget.value as PreviewObjectKindFilter)}
                className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
              >
                <option value="all">{t("dataMgmt.preview.kindFilterAll")}</option>
                <option value="table">{t("dataMgmt.preview.kindFilterTable")}</option>
                <option value="view">{t("dataMgmt.preview.kindFilterView")}</option>
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-foreground sm:w-36">
              <span>{t("dataMgmt.preview.rowFilter")}</span>
              <select
                value={previewObjectRowFilter}
                onChange={(event) => onPreviewObjectRowFilterChange(event.currentTarget.value as PreviewObjectRowFilter)}
                className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
              >
                <option value="all">{t("dataMgmt.preview.rowFilterAll")}</option>
                <option value="with_rows">{t("dataMgmt.preview.rowFilterWithRows")}</option>
                <option value="empty_rows">{t("dataMgmt.preview.rowFilterEmptyRows")}</option>
                <option value="unknown_rows">{t("dataMgmt.preview.rowFilterUnknownRows")}</option>
              </select>
            </label>
          </div>
          {initialLoading ? (
            <DbManagementLoadingSkeleton
              idPrefix="data-preview-object"
              ariaLabel={t("dataMgmt.objectList.loading")}
              variant="list"
              rows={6}
            />
          ) : error ? (
            <ErrorState message={error} onRetry={onRetry} />
          ) : (
            <>
              <p className="text-xs text-muted" aria-live="polite">
                {t("dataMgmt.preview.filteredObjectCount", {
                  filtered: filteredPreviewObjects.length,
                  total: previewObjects.length,
                })}
              </p>
              <PreviewObjectList
                objects={filteredPreviewObjects}
                selectedObject={previewObject}
                hasActiveFilter={hasActiveFilter}
                loadingObjectName={loadingObjectName}
                onSelect={onPreviewObjectChange}
                onShowPreview={onShowPreview}
              />
              {hasNextPage && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  loading={loadingNextPage}
                  onClick={onLoadMore}
                >
                  {t("dataMgmt.objectList.loadMore")}
                </Button>
              )}
            </>
          )}
        </div>

        <div className="grid gap-3 border-t border-border pt-3">
          {selectedObject && (
            <div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border border-primary/20 bg-card px-3 py-2 text-sm text-foreground">
              <span className="font-medium text-foreground">{t("dataMgmt.preview.selectedObject")}</span>
              <span className="break-all font-mono text-xs font-semibold text-primary">{selectedObject.name}</span>
              <StatusBadge variant="neutral" label={previewObjectKindLabel(selectedObject.kind)} />
            </div>
          )}
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("dataMgmt.preview.limit")}</span>
            <input
              type="number"
              min={1}
              max={10000}
              value={previewLimit}
              onChange={(event) =>
                onPreviewLimitChange(Math.min(10000, Math.max(1, Number(event.currentTarget.value) || 1)))
              }
              className="min-h-11 rounded-md border border-border px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
            />
          </label>
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("dataMgmt.preview.where")}</span>
            <textarea
              value={previewWhere}
              onChange={(event) => onPreviewWhereChange(event.currentTarget.value)}
              rows={4}
              placeholder={t("dataMgmt.preview.wherePlaceholder")}
              className="min-h-28 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 focus:border-primary focus:ring-2 focus:ring-ring/40"
            />
          </label>
        </div>
      </div>
    </section>
  );
}

function PreviewObjectList({
  objects,
  selectedObject,
  hasActiveFilter,
  loadingObjectName,
  onSelect,
  onShowPreview,
}: {
  objects: PreviewObject[];
  selectedObject: string;
  hasActiveFilter: boolean;
  loadingObjectName: string;
  onSelect: (value: string) => void;
  onShowPreview: (objectName: string) => void;
}) {
  if (objects.length === 0) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <EmptyState
          title={hasActiveFilter ? t("dataMgmt.preview.noObjectsTitle") : t("dataMgmt.preview.emptyObjectsTitle")}
          hint={hasActiveFilter ? t("dataMgmt.preview.noObjectsHint") : t("dataMgmt.preview.emptyObjectsHint")}
        />
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card" data-testid="data-preview-object-list">
      <div
        className="hidden grid-cols-[minmax(0,1.35fr)_5.25rem_5.25rem_minmax(4.5rem,0.75fr)_8.5rem] gap-2 border-b border-border bg-background px-3 py-2 text-xs font-semibold text-muted md:grid"
        aria-hidden="true"
      >
        <span>{t("dataMgmt.preview.objectName")}</span>
        <span>{t("dataMgmt.preview.objectKind")}</span>
        <span>{t("dataMgmt.preview.objectRows")}</span>
        <span>{t("dataMgmt.preview.objectOwner")}</span>
        <span className="text-right">{t("dataMgmt.preview.actions")}</span>
      </div>
      <div className="max-h-80 overflow-auto" role="list" aria-label={t("dataMgmt.preview.object")}>
        {objects.map((item) => {
          const selected = item.name === selectedObject;
          const loading = item.name === loadingObjectName;
          return (
            <div
              key={`${item.kind}-${item.name}`}
              role="listitem"
              className={[
                "grid w-full min-w-0 gap-2 border-b border-border px-3 py-3 text-left text-sm transition-colors last:border-b-0 hover:bg-background",
                "md:grid-cols-[minmax(0,1.35fr)_5.25rem_5.25rem_minmax(4.5rem,0.75fr)_8.5rem] md:items-center md:py-2",
                selected ? "bg-primary/10" : "bg-card",
              ].join(" ")}
            >
              <button
                type="button"
                aria-current={selected ? "true" : undefined}
                aria-label={t("dataMgmt.preview.selectObject", { name: item.name })}
                className="flex min-h-11 w-full min-w-0 flex-col justify-center text-left focus:outline-none focus:ring-2 focus:ring-ring/40 md:min-h-0"
                onClick={() => onSelect(item.name)}
              >
                <span className="break-all font-mono text-xs font-semibold text-primary">{item.name}</span>
                {item.comment && <span className="mt-1 block break-words text-xs text-muted md:hidden">{item.comment}</span>}
              </button>
              <span className="flex items-center gap-2 md:block">
                <span className="text-xs font-medium text-muted md:hidden">{t("dataMgmt.preview.objectKind")}</span>
                <StatusBadge variant={item.kind === "view" ? "info" : "neutral"} label={previewObjectKindLabel(item.kind)} />
              </span>
              <span className="flex items-center gap-2 font-mono text-xs text-foreground md:block">
                <span className="font-sans font-medium text-muted md:hidden">{t("dataMgmt.preview.objectRows")}</span>
                {previewObjectRowCountLabel(item.rowCount)}
              </span>
              <span className="flex min-w-0 items-center gap-2 font-mono text-xs text-muted md:block">
                <span className="font-sans font-medium text-muted md:hidden">{t("dataMgmt.preview.objectOwner")}</span>
                <span className="break-all">{item.owner || "-"}</span>
              </span>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="w-full whitespace-nowrap md:w-auto"
                aria-label={t("dataMgmt.preview.showObject", { name: item.name })}
                loading={loading}
                disabled={Boolean(loadingObjectName) && !loading}
                onClick={() => onShowPreview(item.name)}
              >
                <Eye size={15} aria-hidden="true" />
                <span>{t("dataMgmt.preview.show")}</span>
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function filterPreviewObjects(
  objects: PreviewObject[],
  search: string,
  kindFilter: PreviewObjectKindFilter,
  rowFilter: PreviewObjectRowFilter
) {
  const q = search.trim().toLowerCase();
  return objects.filter((item) => {
    if (kindFilter !== "all" && item.kind !== kindFilter) return false;
    if (!previewObjectMatchesRowFilter(item, rowFilter)) return false;
    if (!q) return true;
    return [item.name, item.comment, item.owner, previewObjectKindLabel(item.kind)]
      .join(" ")
      .toLowerCase()
      .includes(q);
  });
}

function useDebouncedValue<T>(value: T, delayMs: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeoutId);
  }, [delayMs, value]);
  return debounced;
}

function apiErrorMessage(
  error: unknown,
  fallbackKey: Parameters<typeof t>[0],
  timeoutKey: Parameters<typeof t>[0] = "dataMgmt.operation.timeout"
) {
  if (isTimeoutError(error)) return t(timeoutKey);
  return error instanceof Error ? error.message : t(fallbackKey);
}

function schemaJobLabel(job: SchemaRefreshJob | null) {
  if (!job) return "";
  const phase = job.phase ?? (job.status === "pending" ? "queued" : job.status);
  const progress = job.total_objects
    ? ` ${formatNumber(job.processed_objects ?? 0)}/${formatNumber(job.total_objects)}`
    : "";
  return t("dataMgmt.schemaJob.progress", { phase: t(`dataMgmt.schemaJob.phase.${phase}`), progress });
}

function previewObjectMatchesRowFilter(item: PreviewObject, filter: PreviewObjectRowFilter) {
  if (filter === "all") return true;
  if (filter === "with_rows") return typeof item.rowCount === "number" && item.rowCount > 0;
  if (filter === "empty_rows") return item.rowCount === 0;
  return item.rowCount == null;
}

function previewObjectKindLabel(kind: PreviewObjectKind) {
  return kind === "view" ? t("dataMgmt.preview.kindFilterView") : t("dataMgmt.preview.kindFilterTable");
}

function previewObjectRowCountLabel(rowCount?: number | null) {
  if (rowCount == null) return t("dataMgmt.preview.rowUnknown");
  return t("dbAdmin.list.rows", { count: rowCount });
}

function PreviewResultsPanel({
  preview,
  loading,
  exporting,
  previewError,
  exportError,
  onRetryPreview,
  onDownload,
}: {
  preview: DbAdminDataPreviewData | null;
  loading: boolean;
  exporting: boolean;
  previewError: string;
  exportError: string;
  onRetryPreview: () => void;
  onDownload: () => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4" aria-labelledby="data-preview-results-heading">
      <DbObjectPanelHeader
        headingId="data-preview-results-heading"
        icon={FileSpreadsheet}
        title={t("dataMgmt.preview.resultsTitle")}
        description={t("dataMgmt.preview.resultsHint")}
        action={
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={exporting}
            disabled={!preview || preview.results.rows.length === 0}
            onClick={onDownload}
          >
            <FileSpreadsheet size={15} aria-hidden="true" />
            <span>{t("dataMgmt.preview.exportXlsx")}</span>
          </Button>
        }
      />
      {loading ? (
        <DbManagementLoadingSkeleton
          idPrefix="data-preview-results"
          ariaLabel={t("dataMgmt.preview.loading")}
          variant="detail"
        />
      ) : previewError ? (
        <ErrorState message={previewError} onRetry={onRetryPreview} />
      ) : preview ? (
        <div className="grid gap-2">
          {exportError && <ErrorState message={exportError} onRetry={onDownload} />}
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <StatusBadge variant="neutral" label={preview.runtime} />
            <StatusBadge variant="info" label={t("tableMgmt.importWizard.rows", { count: preview.results.total })} />
            <span className="break-all font-mono text-xs text-muted">{preview.sql}</span>
          </div>
          {preview.warnings.map((warning) => (
            <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
              {warning}
            </p>
          ))}
          <QueryResultsTable results={preview.results} />
        </div>
      ) : (
        <>
          {exportError && <ErrorState message={exportError} onRetry={onDownload} />}
          <EmptyState title={t("dataMgmt.preview.emptyTitle")} hint={t("dataMgmt.preview.emptyHint")} />
        </>
      )}
    </section>
  );
}

function CsvUploadWorkspace({
  tables,
  table,
  filename,
  fileReady,
  mode,
  step,
  confirmation,
  confirmed,
  canUpload,
  result,
  loading,
  error,
  hasNextPage,
  loadingNextPage,
  onTableChange,
  onModeChange,
  onFilePick,
  onFileClear,
  onConfirmationChange,
  onUpload,
  onRetry,
  onLoadMore,
}: {
  tables: DbAdminObjectsData["items"];
  table: string;
  filename: string;
  fileReady: boolean;
  mode: CsvMode;
  step: CsvStep;
  confirmation: string;
  confirmed: boolean;
  canUpload: boolean;
  result: DbAdminCsvUploadData | null;
  loading: boolean;
  error: string;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  onTableChange: (value: string) => void;
  onModeChange: (value: CsvMode) => void;
  onFilePick: (file: File) => void;
  onFileClear: () => void;
  onConfirmationChange: (value: string) => void;
  onUpload: () => void;
  onRetry: () => void;
  onLoadMore: () => void;
}) {
  const activeIndex = step === "execute" ? 1 : 0;
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Upload}
        title={t("dataMgmt.csv.title")}
        description={t("dataMgmt.section.csvHint")}
      />

      <DbObjectStepIndicator
        steps={[t("dataMgmt.csv.stepFile"), t("dataMgmt.csv.stepExecute")]}
        activeIndex={activeIndex}
        ariaLabel={t("dataMgmt.csv.steps")}
        dataTestId="data-csv-steps"
      />

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="grid min-w-0 gap-2">
          <label className="grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground">
            <span>{t("dataMgmt.csv.table")}</span>
            <select
              value={table}
              onChange={(event) => onTableChange(event.currentTarget.value)}
              className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
            >
              {tables.length === 0 && <option value="">{t("tableMgmt.list.emptyTitle")}</option>}
              {tables.map((item) => (
                <option key={`${item.owner}.${item.name}`} value={item.name}>{item.name}</option>
              ))}
            </select>
          </label>
          {hasNextPage && (
            <Button type="button" variant="secondary" size="sm" loading={loadingNextPage} onClick={onLoadMore}>
              {t("dataMgmt.objectList.loadMore")}
            </Button>
          )}
        </div>
        <label className="grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground">
          <span>{t("dataMgmt.csv.mode")}</span>
          <select
            value={mode}
            onChange={(event) => onModeChange(event.currentTarget.value as CsvMode)}
            className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
          >
            <option value="insert">{t("dataMgmt.csv.mode.insert")}</option>
            <option value="truncate_insert">{t("dataMgmt.csv.mode.truncateInsert")}</option>
          </select>
        </label>
      </div>

      {error && <ErrorState message={error} onRetry={onRetry} />}

      <FileInputControl
        label={t("dataMgmt.csv.file")}
        accept=".csv"
        filename={filename}
        selectedText={filename ? t("tableMgmt.importWizard.selectedFile", { filename }) : ""}
        emptyText={t("dataMgmt.csv.noFile")}
        pickText={t("dataMgmt.csv.filePick")}
        replaceText={t("dataMgmt.csv.fileReplace")}
        clearAriaLabel={t("dataMgmt.csv.clearFile")}
        icon="spreadsheet"
        dataTestId="data-csv-file-field"
        onPick={onFilePick}
        onClear={onFileClear}
      />

      <fieldset className="grid gap-3 rounded-md border border-border bg-card p-3">
        <legend className="px-1 text-sm font-semibold text-foreground">{t("dataMgmt.csv.executeTitle")}</legend>
        <ExecutionConfirmationField
          value={confirmation}
          onChange={onConfirmationChange}
          confirmed={confirmed}
          placeholder={table}
          expectedLabel={table || "-"}
          helper={t(
            mode === "truncate_insert"
              ? "dbAdmin.confirmation.helper.danger"
              : "dbAdmin.confirmation.helper.execute",
            { phrase: table || "-" }
          )}
          tone={mode === "truncate_insert" ? "danger" : "neutral"}
          actions={
            <Button
              type="button"
              variant={mode === "truncate_insert" ? "danger" : "primary"}
              size="sm"
              className="w-full sm:w-auto"
              loading={loading}
              disabled={!canUpload}
              onClick={onUpload}
            >
              <Upload size={15} aria-hidden="true" />
              <span>{t("dataMgmt.csv.upload")}</span>
            </Button>
          }
        />
      </fieldset>

      {result && (
        <section className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm" aria-label={t("dataMgmt.csv.result")}>
          <div className="flex flex-wrap gap-2">
            <StatusBadge variant={result.executed ? "success" : "neutral"} label={result.executed ? "executed" : "not executed"} />
            <StatusBadge variant="neutral" label={result.runtime} />
            <StatusBadge variant="neutral" label={result.mode} />
            <StatusBadge variant="info" label={t("tableMgmt.importWizard.rows", { count: result.row_count })} />
            {result.executed && (
              <>
                <StatusBadge variant="success" label={`${t("dataMgmt.csv.success")} ${result.success_count}`} />
                <StatusBadge
                  variant={result.error_count > 0 ? "danger" : "neutral"}
                  label={`${t("dataMgmt.csv.failed")} ${result.error_count}`}
                />
              </>
            )}
          </div>
          {result.warnings.map((warning) => (
            <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
              {warning}
            </p>
          ))}
          <p className="text-foreground">
            {t("dataMgmt.csv.matched")}: <span className="font-mono text-xs">{result.matched_columns.join(", ") || "-"}</span>
          </p>
          {result.unmatched_csv_columns.length > 0 && (
            <p className="text-foreground">
              {t("dataMgmt.csv.unmatched")}: <span className="font-mono text-xs">{result.unmatched_csv_columns.join(", ")}</span>
            </p>
          )}
          {result.row_errors.length > 0 && (
            <div className="grid gap-1">
              <p className="font-semibold text-foreground">{t("dataMgmt.csv.rowErrors")}</p>
              {result.row_errors.map((error) => (
                <p key={error} className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-danger">
                  {error}
                </p>
              ))}
            </div>
          )}
          {result.hint && (
            <p className="rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-primary">{result.hint}</p>
          )}
          {result.sample_rows.length > 0 && (
            <div className="grid gap-1">
              <p className="font-semibold text-foreground">{t("dataMgmt.csv.preview")}</p>
              <QueryResultsTable
                results={{
                  columns: Object.keys(result.sample_rows[0] ?? {}),
                  rows: result.sample_rows,
                  total: result.sample_rows.length,
                }}
              />
            </div>
          )}
        </section>
      )}
      {!fileReady && <p className="text-xs text-muted">{t("dataMgmt.csv.noFile")}</p>}
    </div>
  );
}

function SyntheticWorkspace({
  selectAiDbProfiles,
  selectedSyntheticProfile,
  syntheticData,
  syntheticDataStatus,
  syntheticDataResults,
  syntheticProfileName,
  syntheticAvailableTables,
  syntheticSelectedTables,
  syntheticPrompt,
  syntheticConfirmation,
  syntheticDataConfirmed,
  canGenerateSyntheticData,
  syntheticRows,
  syntheticSampleRows,
  syntheticUseComments,
  syntheticResultTable,
  syntheticResultLimit,
  loading,
  error,
  onRefreshTables,
  onSyntheticProfileNameChange,
  onSyntheticTableToggle,
  onSyntheticPromptChange,
  onSyntheticConfirmationChange,
  onSyntheticRowsChange,
  onSyntheticSampleRowsChange,
  onSyntheticUseCommentsChange,
  onSyntheticResultTableChange,
  onSyntheticResultLimitChange,
  onGenerateSyntheticData,
  onCheckSyntheticDataStatus,
  onLoadSyntheticDataResults,
  onRetry,
}: {
  selectAiDbProfiles: SelectAiDbProfilesData | null;
  selectedSyntheticProfile: SelectAiDbProfile | null;
  syntheticData: SyntheticDataOperationData | null;
  syntheticDataStatus: SyntheticDataOperationStatusData | null;
  syntheticDataResults: SyntheticDataResultsData | null;
  syntheticProfileName: string;
  syntheticAvailableTables: string[];
  syntheticSelectedTables: string[];
  syntheticPrompt: string;
  syntheticConfirmation: string;
  syntheticDataConfirmed: boolean;
  canGenerateSyntheticData: boolean;
  syntheticRows: number;
  syntheticSampleRows: number;
  syntheticUseComments: boolean;
  syntheticResultTable: string;
  syntheticResultLimit: number;
  loading: SyntheticLoading;
  error: string;
  onRefreshTables: () => void;
  onSyntheticProfileNameChange: (value: string) => void;
  onSyntheticTableToggle: (tableName: string, selected: boolean) => void;
  onSyntheticPromptChange: (value: string) => void;
  onSyntheticConfirmationChange: (value: string) => void;
  onSyntheticRowsChange: (value: number) => void;
  onSyntheticSampleRowsChange: (value: number) => void;
  onSyntheticUseCommentsChange: (value: boolean) => void;
  onSyntheticResultTableChange: (value: string) => void;
  onSyntheticResultLimitChange: (value: number) => void;
  onGenerateSyntheticData: () => void;
  onCheckSyntheticDataStatus: () => void;
  onLoadSyntheticDataResults: () => void;
  onRetry: () => void;
}) {
  const activeStep = syntheticDataResults ? 3 : syntheticData || syntheticDataStatus ? 1 : 0;
  const operationId = syntheticData?.operation_id.trim() ?? "";
  // 親の syntheticDataConfirmed と同じ規則(単一テーブル=対象名 / 複数=ADMIN_EXECUTE)。
  const syntheticExpectedConfirmation =
    syntheticSelectedTables.length === 1 ? syntheticSelectedTables[0] : "ADMIN_EXECUTE";
  const resultTableOptions = syntheticAvailableTables;
  const hasValidResultTable = resultTableOptions.includes(syntheticResultTable);

  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Database}
        title={t("dataTools.synthetic.title")}
        description={t("dataMgmt.section.syntheticHint")}
      />
      {error && <ErrorState message={error} onRetry={onRetry} />}

      <DbObjectStepIndicator
        steps={[
          t("dataTools.syntheticData.stepTarget"),
          t("dataTools.syntheticData.stepStatus"),
          t("dataTools.syntheticData.stepResults"),
        ]}
        activeIndex={activeStep}
        ariaLabel={t("dataTools.syntheticData.steps")}
        dataTestId="data-synthetic-steps"
      />

      <section className="grid min-w-0 gap-3 rounded-md border border-border bg-background p-3" aria-labelledby="synthetic-target-heading">
        <DbObjectPanelHeader
          headingId="synthetic-target-heading"
          icon={Database}
          title={t("dataTools.syntheticData.stepTarget")}
          description={t("dataTools.syntheticData.targetHint")}
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="w-full sm:w-auto"
              loading={loading === "tables"}
              disabled={!syntheticProfileName}
              onClick={onRefreshTables}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("dataTools.syntheticData.refreshTables")}</span>
            </Button>
          }
        />

        <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(0,1fr)_10rem]">
          <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
            <span>{t("dataTools.syntheticData.profile")}</span>
            <select
              value={syntheticProfileName}
              onChange={(event) => onSyntheticProfileNameChange(event.currentTarget.value)}
              className="h-11 w-full min-w-0 rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
            >
              {(selectAiDbProfiles?.profiles ?? []).length === 0 && (
                <option value="">{t("dataTools.syntheticData.noProfiles")}</option>
              )}
              {(selectAiDbProfiles?.profiles ?? []).map((profile) => (
                <option key={profile.name} value={profile.name}>
                  {profile.name}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("dataTools.syntheticData.rowsPerTable")}</span>
            <input
              type="number"
              min={1}
              max={100}
              value={syntheticRows}
              onChange={(event) => onSyntheticRowsChange(Number(event.currentTarget.value) || 1)}
              className="h-11 rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
            />
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge
            variant={syntheticSelectedTables.length > 0 ? "info" : "neutral"}
            label={t("dataTools.syntheticData.selectedCount", { count: syntheticSelectedTables.length })}
          />
          {selectedSyntheticProfile?.owner && <StatusBadge variant="neutral" label={selectedSyntheticProfile.owner} />}
          {selectedSyntheticProfile?.status && <StatusBadge variant="neutral" label={selectedSyntheticProfile.status} />}
          {selectAiDbProfiles?.warnings.map((warning) => (
            <span
              key={warning}
              className="rounded-md border border-warning/30 bg-warning-bg px-2 py-1 text-xs text-warning"
            >
              {warning}
            </span>
          ))}
        </div>

        <div className="grid min-w-0 gap-2">
          <div className="text-sm font-medium text-foreground">{t("dataTools.syntheticData.tables")}</div>
          {loading === "tables" ? (
            <DbManagementLoadingSkeleton
              idPrefix="data-synthetic-tables"
              ariaLabel={t("dataTools.syntheticData.tablesLoading")}
              variant="list"
              rows={4}
            />
          ) : syntheticAvailableTables.length > 0 ? (
            <div className="max-h-56 overflow-auto rounded-md border border-border bg-card" role="group" aria-label={t("dataTools.syntheticData.tables")}>
              <div className="grid divide-y divide-border/70">
                {syntheticAvailableTables.map((tableName) => {
                  const selected = syntheticSelectedTables.includes(tableName);
                  return (
                    <label
                      key={tableName}
                      className="flex min-h-11 min-w-0 items-center gap-3 px-3 py-2 text-sm text-foreground hover:bg-background"
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(event) => onSyntheticTableToggle(tableName, event.currentTarget.checked)}
                        className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
                        aria-label={t("dataTools.syntheticData.tableOption", { name: tableName })}
                      />
                      <span className="min-w-0 break-all font-mono text-xs">{tableName}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          ) : (
            <EmptyState
              title={t("dataTools.syntheticData.noTablesTitle")}
              hint={t("dataTools.syntheticData.noTablesHint")}
            />
          )}
        </div>

        <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
            <span>{t("dataTools.syntheticData.prompt")}</span>
            <textarea
              value={syntheticPrompt}
              onChange={(event) => onSyntheticPromptChange(event.currentTarget.value)}
              rows={5}
              placeholder={t("dataTools.syntheticData.promptPlaceholder")}
              className="min-h-40 rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
            />
          </label>
          <fieldset className="grid content-start gap-3 rounded-md border border-border bg-card p-3">
            <legend className="px-1 text-sm font-semibold text-foreground">{t("dataTools.syntheticData.options")}</legend>
            <label className="grid gap-1 text-sm font-medium text-foreground">
              <span>{t("dataTools.syntheticData.sampleRows")}</span>
              <input
                type="number"
                min={0}
                max={100}
                value={syntheticSampleRows}
                onChange={(event) => onSyntheticSampleRowsChange(Number(event.currentTarget.value) || 0)}
                className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
              />
            </label>
            <label className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground">
              <input
                type="checkbox"
                checked={syntheticUseComments}
                onChange={(event) => onSyntheticUseCommentsChange(event.currentTarget.checked)}
                className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
              />
              <span>{t("dataTools.syntheticData.useComments")}</span>
            </label>
          </fieldset>
        </div>

        <fieldset className="grid gap-3 rounded-md border border-border bg-card p-3">
          <legend className="px-1 text-sm font-semibold text-foreground">{t("dataTools.syntheticData.executeTitle")}</legend>
          <ExecutionConfirmationField
            value={syntheticConfirmation}
            onChange={onSyntheticConfirmationChange}
            confirmed={syntheticDataConfirmed}
            placeholder={syntheticExpectedConfirmation}
            expectedLabel={syntheticExpectedConfirmation}
            helper={t("dbAdmin.confirmation.helper.danger", {
              phrase: syntheticExpectedConfirmation,
            })}
            tone="danger"
            actions={
              <Button
                type="button"
                variant="danger"
                size="sm"
                className="w-full sm:w-auto"
                loading={loading === "generate"}
                disabled={!canGenerateSyntheticData}
                onClick={onGenerateSyntheticData}
              >
                <Database size={15} aria-hidden="true" />
                <span>{t("dataTools.syntheticData.generate")}</span>
              </Button>
            }
          />
        </fieldset>
      </section>

      <section className="grid min-w-0 gap-3 rounded-md border border-border bg-background p-3" aria-labelledby="synthetic-status-heading">
        <DbObjectPanelHeader
          headingId="synthetic-status-heading"
          icon={RefreshCw}
          title={t("dataTools.syntheticData.stepStatus")}
          description={t("dataTools.syntheticData.statusHint")}
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="w-full sm:w-auto"
              loading={loading === "status"}
              disabled={!operationId}
              onClick={onCheckSyntheticDataStatus}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("dataTools.syntheticData.status")}</span>
            </Button>
          }
        />

        <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
          <span>{t("dataTools.syntheticData.operationId")}</span>
          <input
            value={operationId || "-"}
            readOnly
            className="h-11 rounded-md border border-border bg-card px-3 font-mono text-sm text-foreground"
          />
        </label>

        {syntheticData ? (
          <div className="grid gap-2 rounded-md border border-border bg-card p-3 text-sm" aria-label={t("dataTools.syntheticData.operationResult")}>
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant={syntheticData.executed ? "success" : "neutral"} label={syntheticData.status} />
              <StatusBadge variant="neutral" label={syntheticData.runtime} />
              {operationId && <StatusBadge variant="info" label={operationId} />}
            </div>
            <p className="text-foreground">{syntheticData.message || "-"}</p>
            {syntheticData.warnings.map((warning) => (
              <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
                {warning}
              </p>
            ))}
          </div>
        ) : (
          <EmptyState title={t("dataTools.syntheticData.noOperationTitle")} hint={t("dataTools.syntheticData.noOperationHint")} />
        )}

        {loading === "status" ? (
          <DbManagementLoadingSkeleton
            idPrefix="data-synthetic-status"
            ariaLabel={t("dataTools.syntheticData.statusLoading")}
            variant="compact"
          />
        ) : syntheticDataStatus ? (
          <div className="grid gap-2 rounded-md border border-border bg-card p-3 text-sm" aria-label={t("dataTools.syntheticData.statusResult")}>
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant="info" label={syntheticDataStatus.status} />
              <StatusBadge variant="neutral" label={syntheticDataStatus.runtime} />
            </div>
            <p className="text-foreground">{syntheticDataStatus.message || "-"}</p>
            {syntheticDataStatus.warnings.map((warning) => (
              <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
                {warning}
              </p>
            ))}
            {Object.keys(syntheticDataStatus.result).length > 0 && (
              <pre className="max-h-52 overflow-auto rounded-md border border-border bg-code p-3 text-sm leading-6 text-code-fg">
                <code>{JSON.stringify(syntheticDataStatus.result, null, 2)}</code>
              </pre>
            )}
          </div>
        ) : null}
      </section>

      <section className="grid min-w-0 gap-3 rounded-md border border-border bg-background p-3" aria-labelledby="synthetic-results-heading">
        <DbObjectPanelHeader
          headingId="synthetic-results-heading"
          icon={Eye}
          title={t("dataTools.syntheticData.stepResults")}
          description={t("dataTools.syntheticData.resultsHint")}
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="w-full sm:w-auto"
              loading={loading === "results"}
              disabled={!hasValidResultTable}
              onClick={onLoadSyntheticDataResults}
            >
              <Eye size={15} aria-hidden="true" />
              <span>{t("dataTools.syntheticData.results")}</span>
            </Button>
          }
        />

        <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
          <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
            <span>{t("dataTools.syntheticData.resultTable")}</span>
            <select
              data-testid="synthetic-result-table-select"
              value={hasValidResultTable ? syntheticResultTable : ""}
              onChange={(event) => onSyntheticResultTableChange(event.currentTarget.value)}
              className="h-11 w-full min-w-0 rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
            >
              {resultTableOptions.length === 0 && <option value="">{t("dataTools.syntheticData.noResultTables")}</option>}
              {resultTableOptions.map((tableName) => (
                <option key={tableName} value={tableName}>
                  {tableName}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("dataTools.syntheticData.resultLimit")}</span>
            <input
              type="number"
              min={1}
              max={10000}
              value={syntheticResultLimit}
              onChange={(event) => onSyntheticResultLimitChange(Number(event.currentTarget.value) || 1)}
              className="h-11 rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
            />
          </label>
        </div>

        {loading === "results" ? (
          <DbManagementLoadingSkeleton
            idPrefix="data-synthetic-results"
            ariaLabel={t("dataTools.syntheticData.resultsLoading")}
            variant="detail"
          />
        ) : syntheticDataResults ? (
          <div className="grid min-w-0 gap-2">
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant="neutral" label={syntheticDataResults.runtime} />
              <StatusBadge variant="info" label={syntheticDataResults.table_name} />
            </div>
            {syntheticDataResults.warnings.map((warning) => (
              <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
                {warning}
              </p>
            ))}
            <QueryResultsTable results={syntheticDataResults.results} />
          </div>
        ) : (
          <EmptyState title={t("dataTools.syntheticData.noResultsTitle")} hint={t("dataTools.syntheticData.noResultsHint")} />
        )}
      </section>
    </div>
  );
}

async function fetchSyntheticDataStatus(operationId: string) {
  return apiGet<SyntheticDataOperationStatusData>(
    `/api/nl2sql/synthetic-data/operations/${encodeURIComponent(operationId)}`,
    { timeoutMs: API_TIMEOUT_MS.jobControl }
  );
}

async function previewExportError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: unknown; error?: unknown };
    return String(payload.error || payload.detail || t("dataMgmt.error.previewExport"));
  } catch {
    return t("dataMgmt.error.previewExport");
  }
}

function profileObjectNames(profile: SelectAiDbProfile | null | undefined) {
  if (!profile) return [];
  const rawItems = [
    ...collectProfileObjectListItems(profile.object_list, true),
    ...collectProfileObjectListItems(profile.attributes, false),
  ];
  return uniqueStrings(
    rawItems.flatMap((item) => {
      const name = profileObjectName(item);
      return name ? [name] : [];
    })
  );
}

function collectProfileObjectListItems(value: unknown, candidateScope: boolean, depth = 0): unknown[] {
  if (value === null || value === undefined || depth > 6) return [];
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return [];
    try {
      return collectProfileObjectListItems(JSON.parse(trimmed) as unknown, candidateScope, depth + 1);
    } catch {
      return candidateScope ? [trimmed] : [];
    }
  }
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectProfileObjectListItems(item, true, depth + 1));
  }
  if (!isRecord(value)) return [];
  if (candidateScope && profileObjectName(value)) return [value];

  const rawItems: unknown[] = [];
  for (const key of ["object_list", "OBJECT_LIST", "objectList", "objects", "OBJECTS", "tables", "TABLES"]) {
    if (key in value) {
      rawItems.push(...collectProfileObjectListItems(value[key], true, depth + 1));
    }
  }
  for (const key of [
    "attributes",
    "ATTRIBUTES",
    "profile_attributes",
    "PROFILE_ATTRIBUTES",
    "profileAttributes",
    "params",
    "PARAMS",
  ]) {
    if (key in value) {
      rawItems.push(...collectProfileObjectListItems(value[key], false, depth + 1));
    }
  }
  return rawItems;
}

function profileObjectName(item: unknown) {
  if (typeof item === "string") return item.trim();
  if (!isRecord(item)) return "";
  const name =
    item.name ??
    item.NAME ??
    item.object_name ??
    item.OBJECT_NAME ??
    item.table_name ??
    item.TABLE_NAME ??
    item.objectName ??
    item.tableName;
  return typeof name === "string" ? name.trim() : "";
}

function uniqueStrings(values: string[]) {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].sort((left, right) =>
    left.localeCompare(right)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}
