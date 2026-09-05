import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, Database, Eye, FileSpreadsheet, Play, RefreshCw, Table2, Trash2, Upload, X } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Banner, EmptyState, toast } from "@engchina/production-ready-ui";

import { StatusBadge } from "@/components/ui/status-badge";

import { BulkSelectionActions } from "@/components/BulkSelectionActions";
import { ContentActionBar } from "@/components/ContentActionBar";
import { ObjectActionBar, type EntityAction } from "@/components/ObjectActions";
import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { ErrorState } from "@/components/StateViews";
import { FileDropzone } from "@/components/ui/file-dropzone";
import { RequiredFieldsNote, RequiredIndicator } from "@/components/ui/required-field";
import { apiFetch, apiGet, apiPost, isTimeoutError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { toastError } from "@/lib/toast";
import { INFORMATION_COMPACT_LIST_FIVE_ROW_SCROLL_CLASS } from "@/lib/list-density";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import { APP_ROUTES } from "@/lib/routes";
import { CORE_TABULAR_FILE_FORMATS } from "@/lib/tabular-file-formats";
import { selectedVisibleStringKey } from "@/lib/visible-selection";
import {
  ExecutionConfirmationField,
  QueryResultsTable,
  downloadBlob,
  fileToBase64,
} from "../components/DbAdminShared";
import {
  DEFAULT_SQL_ROW_LIMIT,
  RowLimitField,
  parseSqlRowLimit,
} from "../components/SqlRowLimitControls";
import {
  DB_OBJECT_PICKER_SHORT_SCROLL_CLASS,
  DbManagementLoadingSkeleton,
  DbManagementSelectField,
  DropDbObjectDialog,
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  DbObjectSelectionSummary,
  DbObjectSelectorFooter,
  DbObjectSelectorToolbar,
  DbObjectStepIndicator,
  DbSingleObjectPickerList,
  dbAdminObjectQualifiedName,
  parseDbAdminObjectTarget,
  rowCountLabel,
  sortDbObjectPickerItems,
  type DbObjectPickerSortKey,
  type DbObjectPickerSortState,
  type DbObjectTab,
  type DbObjectPickerItem,
} from "../components/DbObjectManagementShared";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  SchemaRefreshHeaderStatus,
  SchemaRefreshProcessing,
} from "../components/SchemaRefreshFeedback";
import {
  nl2sqlIncrementalKeys,
  useDbAdminObjects,
  useSchemaRefreshJob,
  useSelectAiDbProfileRefreshJob,
  useStartSelectAiDbProfileRefresh,
} from "../incrementalQueries";
import { dbAdminObjectCountsFromPage, type DbAdminObjectCounts } from "../dbAdminObjectCounts";
import type {
  DbAdminCsvUploadData,
  DbAdminDataPreviewData,
  DbAdminExecuteData,
  DbAdminObjectSummary,
  SchemaRefreshJob,
  SelectAiDbProfile,
  SelectAiDbProfileDetailData,
  SelectAiDbProfilesData,
  SyntheticDataOperationData,
  SyntheticDataResultsData,
} from "../types";

type ActiveView = "preview" | "csv" | "synthetic";
type CsvStep = "file" | "execute";
type CsvMode = "insert" | "truncate_insert";
type PreviewObjectKind = "table" | "view";
type PreviewObjectKindFilter = "all" | PreviewObjectKind;
type SyntheticLoading = "" | "tables" | "generate" | "results";

const DATA_MANAGEMENT_ID = "data-management";
const DEFAULT_DATA_PREVIEW_ROW_LIMIT = DEFAULT_SQL_ROW_LIMIT;
const DEFAULT_SYNTHETIC_RESULT_LIMIT = DEFAULT_SQL_ROW_LIMIT;
const SYNTHETIC_RESULT_MAX_LIMIT = 10000;
const DEFAULT_OBJECT_PICKER_SORT: DbObjectPickerSortState = { key: "name", direction: "asc" };

interface PreviewObject {
  name: string;
  qualifiedName: string;
  kind: PreviewObjectKind;
  owner: string;
  rowCount?: number | null;
  comment: string;
}

function resolveBusinessSelectAiProfileName(current: string, profiles: SelectAiDbProfile[]) {
  if (current && profiles.some((profile) => profile.name === current)) return current;
  return profiles[0]?.name ?? "";
}

function nextObjectPickerSort(
  current: DbObjectPickerSortState,
  key: DbObjectPickerSortKey
): DbObjectPickerSortState {
  if (current.key === key) {
    return { key, direction: current.direction === "asc" ? "desc" : "asc" };
  }
  return { key, direction: "asc" };
}

export function DataManagementPage() {
  const queryClient = useQueryClient();
  const [activeView, setActiveView] = useState<ActiveView>("preview");
  const [previewObject, setPreviewObject] = useState("");
  const [previewObjectSearch, setPreviewObjectSearch] = useState("");
  const [previewObjectOwnerPrefix, setPreviewObjectOwnerPrefix] = useState("");
  const [previewObjectKindFilter, setPreviewObjectKindFilter] = useState<PreviewObjectKindFilter>("all");
  const [previewObjectSort, setPreviewObjectSort] = useState<DbObjectPickerSortState>(DEFAULT_OBJECT_PICKER_SORT);
  const [previewRowLimitInput, setPreviewRowLimitInput] = useState(String(DEFAULT_DATA_PREVIEW_ROW_LIMIT));
  const [preview, setPreview] = useState<DbAdminDataPreviewData | null>(null);
  const [executedPreviewRowLimit, setExecutedPreviewRowLimit] = useState<number | null>(null);
  const [csvTable, setCsvTable] = useState("");
  const [csvTableSearch, setCsvTableSearch] = useState("");
  const [csvTableSort, setCsvTableSort] = useState<DbObjectPickerSortState>(DEFAULT_OBJECT_PICKER_SORT);
  const [csvFilename, setCsvFilename] = useState("");
  const [csvBase64, setCsvBase64] = useState("");
  const [csvMode, setCsvMode] = useState<CsvMode>("insert");
  const [csvStep, setCsvStep] = useState<CsvStep>("file");
  const [csvConfirmation, setCsvConfirmation] = useState("");
  const [csvUploadResult, setCsvUploadResult] = useState<DbAdminCsvUploadData | null>(null);
  const [syntheticData, setSyntheticData] = useState<SyntheticDataOperationData | null>(null);
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
  const [syntheticResultLimitInput, setSyntheticResultLimitInput] = useState(String(DEFAULT_SYNTHETIC_RESULT_LIMIT));
  const [executedSyntheticResultLimit, setExecutedSyntheticResultLimit] = useState<number | null>(null);
  const [previewLoadingObject, setPreviewLoadingObject] = useState("");
  const [truncateTargetName, setTruncateTargetName] = useState("");
  const [truncateConfirmation, setTruncateConfirmation] = useState("");
  const [truncateLoading, setTruncateLoading] = useState(false);
  const [truncateError, setTruncateError] = useState("");
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
  const [schemaJobNeedsFull, setSchemaJobNeedsFull] = useState(false);
  const [dbProfileRefreshJobId, setDbProfileRefreshJobId] = useState("");
  const [dbProfileRefreshError, setDbProfileRefreshError] = useState("");
  const completedSchemaJob = useRef("");
  const completedDbProfileRefreshJob = useRef("");
  const previewRequestSequence = useRef(0);
  const previewObjectManualSelection = useRef(false);
  const csvTableManualSelection = useRef(false);
  const debouncedObjectSearch = useDebouncedValue(previewObjectSearch, 250);
  const debouncedObjectOwnerPrefix = useDebouncedValue(previewObjectOwnerPrefix, 250);
  const debouncedCsvTableSearch = useDebouncedValue(csvTableSearch, 250);
  const baseObjectsQuery = useDbAdminObjects("", "all", "all");
  const csvTablesQuery = useDbAdminObjects(debouncedCsvTableSearch, "table", "all");
  const previewObjectsQuery = useDbAdminObjects(
    debouncedObjectSearch,
    previewObjectKindFilter,
    "all",
    debouncedObjectOwnerPrefix,
    "name_comment"
  );
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const startDbProfileRefresh = useStartSelectAiDbProfileRefresh();
  const schemaJobQuery = useSchemaRefreshJob(schemaJobId);
  const dbProfileRefreshJobQuery = useSelectAiDbProfileRefreshJob(dbProfileRefreshJobId);
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
  const dbProfileRefreshJob = dbProfileRefreshJobQuery.data ?? null;
  const dbProfileRefreshStatus = dbProfileRefreshJobQuery.isError
    ? "error"
    : (dbProfileRefreshJob?.status ?? "");
  const dbProfileRefreshing =
    dbProfileRefreshStatus === "pending" || dbProfileRefreshStatus === "running";
  const dbProfileRefreshRequired = selectAiDbProfileRefreshRequired(selectAiDbProfiles);
  const baseObjectPages = baseObjectsQuery.data?.pages ?? [];
  const previewObjectItems = useMemo<DbAdminObjectSummary[]>(
    () => (previewObjectsQuery.data?.pages ?? []).flatMap((page) => page.items),
    [previewObjectsQuery.data]
  );
  const firstPreviewObjectPage = previewObjectsQuery.data?.pages[0];
  const previewObjectCounts = dbAdminObjectCountsFromPage(firstPreviewObjectPage, previewObjectItems);
  const csvTableItems = useMemo(
    () =>
      (csvTablesQuery.data?.pages ?? []).flatMap((page) =>
        page.items.filter((item) => item.object_type === "table")
      ),
    [csvTablesQuery.data]
  );
  const firstCsvTablePage = csvTablesQuery.data?.pages[0];
  const csvTableCount = dbAdminObjectCountsFromPage(firstCsvTablePage, csvTableItems).totalCount;

  const previewObjects = useMemo<PreviewObject[]>(() => {
    return previewObjectItems.map((item) => ({
      name: item.name,
      qualifiedName: dbAdminObjectQualifiedName(item),
      kind: item.object_type === "view" ? ("view" as const) : ("table" as const),
      owner: item.owner,
      rowCount: item.row_count,
      comment: item.comment,
    }));
  }, [previewObjectItems]);

  const filteredPreviewObjects = useMemo(
    () =>
      filterPreviewObjects(
        previewObjects,
        previewObjectSearch,
        previewObjectOwnerPrefix,
        previewObjectKindFilter
      ),
    [previewObjects, previewObjectKindFilter, previewObjectOwnerPrefix, previewObjectSearch]
  );
  const previewObjectPickerItems = useMemo(
    () =>
      sortDbObjectPickerItems(
        filteredPreviewObjects.map<DbObjectPickerItem>((item) => ({
          key: item.qualifiedName,
          name: item.qualifiedName,
          kind: item.kind,
          owner: item.owner,
          comment: item.comment,
          kindLabel: previewObjectKindLabel(item.kind),
          kindVariant: item.kind === "view" ? "info" : "neutral",
          rowCount: item.rowCount,
          rowCountLabel: previewObjectRowCountLabel(item.rowCount),
        })),
        previewObjectSort
      ),
    [filteredPreviewObjects, previewObjectSort]
  );
  const selectedPreviewObjectItem = useMemo(
    () => previewObjectPickerItems.find((item) => item.key === previewObject),
    [previewObject, previewObjectPickerItems]
  );
  const parsedPreviewRowLimit = parseSqlRowLimit(previewRowLimitInput);
  const previewRowLimitError =
    parsedPreviewRowLimit === null ? t("queryResults.rowLimit.error") : "";
  const canShowPreview =
    Boolean(previewObject) && !previewLoadingObject && parsedPreviewRowLimit !== null;
  const canClearPreview =
    Boolean(previewLoadingObject) ||
    Boolean(preview) ||
    Boolean(previewError) ||
    Boolean(exportError) ||
    executedPreviewRowLimit !== null ||
    previewRowLimitInput !== String(DEFAULT_DATA_PREVIEW_ROW_LIMIT);
  const csvTablePickerItems = useMemo(
    () =>
      sortDbObjectPickerItems(
        csvTableItems.map<DbObjectPickerItem>((item) => ({
          key: dbAdminObjectQualifiedName(item),
          name: dbAdminObjectQualifiedName(item),
          kind: "table",
          owner: item.owner,
          comment: item.comment,
          kindLabel: t("dataMgmt.preview.kindFilterTable"),
          kindVariant: "neutral",
          rowCount: item.row_count,
          rowCountLabel: rowCountLabel(item.row_count),
        })),
        csvTableSort
      ),
    [csvTableItems, csvTableSort]
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
  const parsedSyntheticResultLimit = parseSqlRowLimit(syntheticResultLimitInput);
  const syntheticResultLimit =
    parsedSyntheticResultLimit !== null &&
    parsedSyntheticResultLimit >= 1 &&
    parsedSyntheticResultLimit <= SYNTHETIC_RESULT_MAX_LIMIT
      ? parsedSyntheticResultLimit
      : null;
  const syntheticResultLimitError =
    syntheticResultLimit === null ? t("dataTools.syntheticData.resultLimitError") : "";
  const syntheticResultError = syntheticErrorOperation === "results" ? syntheticError : "";
  const syntheticWorkspaceError = syntheticErrorOperation === "results" ? "" : syntheticError;
  const canLoadSyntheticDataResults = Boolean(
    syntheticAvailableTables.includes(syntheticResultTable) && syntheticResultLimit !== null && syntheticLoading !== "results"
  );
  const canClearSyntheticDataResults = Boolean(
    syntheticDataResults ||
      syntheticResultError ||
      executedSyntheticResultLimit !== null ||
      syntheticResultLimitInput !== String(DEFAULT_SYNTHETIC_RESULT_LIMIT)
  );

  const clearSyntheticResultState = ({ resetLimit = false }: { resetLimit?: boolean } = {}) => {
    setSyntheticDataResults(null);
    setExecutedSyntheticResultLimit(null);
    if (resetLimit) setSyntheticResultLimitInput(String(DEFAULT_SYNTHETIC_RESULT_LIMIT));
    if (syntheticErrorOperation === "results") {
      setSyntheticError("");
      setSyntheticErrorOperation("");
    }
  };

  const clearSyntheticProfileTargets = () => {
    setSyntheticAvailableTables([]);
    setSyntheticSelectedTables([]);
    setSyntheticResultTable("");
    setSyntheticData(null);
    setSyntheticDataResults(null);
    setExecutedSyntheticResultLimit(null);
    setSyntheticResultLimitInput(String(DEFAULT_SYNTHETIC_RESULT_LIMIT));
    if (syntheticErrorOperation === "results") {
      setSyntheticError("");
      setSyntheticErrorOperation("");
    }
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
    const nextObject = selectedVisibleStringKey(previewObjectPickerItems, previewObject, (item) => item.key, {
      preserveSelected: previewObjectManualSelection.current,
    });
    if (nextObject === previewObject) return;
    previewObjectManualSelection.current = false;
    previewRequestSequence.current += 1;
    setPreviewObject(nextObject);
    setPreview(null);
    setExecutedPreviewRowLimit(null);
    setPreviewLoadingObject("");
    setPreviewError("");
    setExportError("");
  }, [previewObject, previewObjectPickerItems]);

  useEffect(() => {
    const nextTable = selectedVisibleStringKey(csvTablePickerItems, csvTable, (item) => item.key, {
      preserveSelected: csvTableManualSelection.current,
    });
    if (nextTable === csvTable) return;
    csvTableManualSelection.current = false;
    setCsvTable(nextTable);
    setCsvUploadResult(null);
    setCsvStep("file");
  }, [csvTable, csvTablePickerItems]);

  useEffect(() => {
    const job = schemaJobQuery.data;
    if (!job || completedSchemaJob.current === `${job.job_id}:${job.status}`) return;
    if (job.status === "done") {
      completedSchemaJob.current = `${job.job_id}:${job.status}`;
      setSchemaJobError("");
      setSchemaJobNeedsFull(false);
      void baseObjectsQuery.refetch();
      void csvTablesQuery.refetch();
      void previewObjectsQuery.refetch();
    } else if (job.status === "error") {
      completedSchemaJob.current = `${job.job_id}:${job.status}`;
      const needsFull = schemaJobRequiresFull(job);
      setSchemaJobNeedsFull(needsFull);
      setSchemaJobError(schemaJobErrorMessage(job));
    }
  }, [schemaJobQuery.data]);

  useEffect(() => {
    const job = dbProfileRefreshJobQuery.data;
    if (!job || completedDbProfileRefreshJob.current === `${job.job_id}:${job.status}`) return;
    if (job.status === "done") {
      completedDbProfileRefreshJob.current = `${job.job_id}:${job.status}`;
      setDbProfileRefreshError("");
      void queryClient.invalidateQueries({ queryKey: ["nl2sql", "select-ai"] });
      void selectAiProfilesQuery.refetch();
      toast.success(
        t("profiles.dbProfileRefresh.done", {
          changed: job.changed_profiles,
          deleted: job.deleted_profiles,
        })
      );
    } else if (job.status === "error") {
      completedDbProfileRefreshJob.current = `${job.job_id}:${job.status}`;
      setDbProfileRefreshError(dbProfileRefreshErrorMessage(job.error_code, job.error_message));
    }
  }, [dbProfileRefreshJobQuery.data, queryClient, selectAiProfilesQuery]);

  useEffect(() => {
    if (!dbProfileRefreshJobQuery.isError) return;
    const message =
      dbProfileRefreshJobQuery.error instanceof Error
        ? dbProfileRefreshJobQuery.error.message
        : t("profiles.dbProfileRefresh.error");
    setDbProfileRefreshError(message);
  }, [dbProfileRefreshJobQuery.error, dbProfileRefreshJobQuery.isError]);

  const refreshObjects = async (announce = false) => {
    setSchemaJobError("");
    setSchemaJobNeedsFull(false);
    const results = await Promise.all([
      baseObjectsQuery.refetch(),
      csvTablesQuery.refetch(),
      previewObjectsQuery.refetch(),
    ]);
    if (announce && results.every((result) => !result.isError)) {
      toast.success(t("common.action.refreshed"));
    }
  };

  const runDbProfileRefresh = async () => {
    if (dbProfileRefreshing || startDbProfileRefresh.isPending) return;
    try {
      const job = await startDbProfileRefresh.mutateAsync();
      completedDbProfileRefreshJob.current = "";
      setDbProfileRefreshError("");
      setDbProfileRefreshJobId(job.job_id);
      queryClient.setQueryData(nl2sqlIncrementalKeys.selectAiDbProfileRefreshJob(job.job_id), job);
    } catch (err) {
      const message = err instanceof Error ? err.message : t("profiles.dbProfileRefresh.error");
      setDbProfileRefreshError(message);
      toastError(message);
    }
  };

  const submitSchemaRefresh = async () => {
    setSchemaJobError("");
    setSchemaJobNeedsFull(false);
    completedSchemaJob.current = "";
    try {
      const job = await sharedSchemaRefresh.start();
      setSchemaJobId(job.job_id);
      if (!job.job_id && job.status === "done") await refreshObjects();
    } catch (error) {
      setSchemaJobError(apiErrorMessage(error, "dataMgmt.schemaJob.submitError"));
    }
  };

  const selectPreviewObject = (objectName: string, options: { manualSelection?: boolean } = {}) => {
    const target = parseDbAdminObjectTarget(objectName);
    if (options.manualSelection) previewObjectManualSelection.current = true;
    if (target.qualifiedName === previewObject && !previewLoadingObject) return;
    previewRequestSequence.current += 1;
    setPreviewObject(target.qualifiedName);
    setPreview(null);
    setExecutedPreviewRowLimit(null);
    setPreviewLoadingObject("");
    setPreviewError("");
    setExportError("");
  };

  const trackSchemaRefreshResult = (result: {
    schema_refresh_job_id?: string;
    schema_refresh_required?: boolean;
    schema_refresh_reason_code?: string;
  }) => {
    if (result.schema_refresh_job_id) {
      completedSchemaJob.current = "";
      setSchemaJobError("");
      setSchemaJobNeedsFull(false);
      setSchemaJobId(result.schema_refresh_job_id);
      sharedSchemaRefresh.track(result.schema_refresh_job_id);
      return;
    }
    if (result.schema_refresh_required) {
      setSchemaJobError(schemaJobRequiredMessage(result.schema_refresh_reason_code));
      setSchemaJobNeedsFull(true);
    }
  };

  const showPreview = async (objectName: string, options: { manualSelection?: boolean } = {}) => {
    const rowLimit = parsedPreviewRowLimit;
    if (!objectName || previewLoadingObject || rowLimit === null) return;
    const target = parseDbAdminObjectTarget(objectName);
    const sequence = previewRequestSequence.current + 1;
    previewRequestSequence.current = sequence;
    if (options.manualSelection) previewObjectManualSelection.current = true;
    setPreviewObject(target.qualifiedName);
    setPreview(null);
    setExecutedPreviewRowLimit(null);
    setPreviewLoadingObject(target.qualifiedName);
    setPreviewError("");
    setExportError("");
    try {
      const result = await apiPost<DbAdminDataPreviewData>(
        "/api/nl2sql/db-admin/preview-data",
        {
          object_name: target.name,
          owner: target.owner,
          limit: rowLimit,
          where_clause: "",
        },
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail }
      );
      if (sequence !== previewRequestSequence.current) return;
      setPreview(result);
      setExecutedPreviewRowLimit(rowLimit);
    } catch (err) {
      if (sequence !== previewRequestSequence.current) return;
      setPreviewError(apiErrorMessage(err, "dataMgmt.error.preview"));
    } finally {
      if (sequence === previewRequestSequence.current) setPreviewLoadingObject("");
    }
  };

  const openTruncateDialog = (objectName: string) => {
    setTruncateTargetName(objectName);
    setTruncateConfirmation("");
    setTruncateError("");
  };

  const closeTruncateDialog = () => {
    if (truncateLoading) return;
    setTruncateTargetName("");
    setTruncateConfirmation("");
    setTruncateError("");
  };

  const truncateTableData = async () => {
    const tableName = truncateTargetName;
    if (!tableName || truncateLoading) return;
    const target = parseDbAdminObjectTarget(tableName);
    setTruncateLoading(true);
    setTruncateError("");
    try {
      const result = await apiPost<DbAdminExecuteData>(
        "/api/nl2sql/db-admin/truncate-table",
        {
          table_name: target.name,
          owner: target.owner,
          confirmation: truncateConfirmation,
          reason: "ui-data-management-truncate",
        },
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail }
      );
      if (!result.executed) {
        setTruncateError(result.warnings[0] || t("dataMgmt.truncate.error"));
        return;
      }
      setTruncateTargetName("");
      setTruncateConfirmation("");
      toast.success(t("dataMgmt.truncate.success", { name: tableName }));
      await refreshObjects();
      trackSchemaRefreshResult(result);
      await showPreview(tableName);
    } catch (err) {
      setTruncateError(apiErrorMessage(err, "dataMgmt.truncate.error"));
    } finally {
      setTruncateLoading(false);
    }
  };

  const downloadPreviewXlsx = async () => {
    const rowLimit = parsedPreviewRowLimit;
    if (!previewObject || rowLimit === null) return;
    const target = parseDbAdminObjectTarget(previewObject);
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
          object_name: target.name,
          owner: target.owner,
          limit: rowLimit,
          where_clause: "",
        }),
        signal: AbortSignal.timeout(API_TIMEOUT_MS.interactiveDetail),
      });
      if (!response.ok) {
        throw new Error(await previewExportError(response));
      }
      const filename = `${target.qualifiedName.toLowerCase().replace(".", "_")}_preview.xlsx`;
      downloadBlob(filename, await response.blob());
      toast.success(t("common.action.downloaded"));
    } catch (err) {
      setExportError(apiErrorMessage(err, "dataMgmt.error.previewExport"));
    } finally {
      setExportLoading(false);
    }
  };

  const updatePreviewRowLimitInput = (value: string) => {
    setPreviewRowLimitInput(value);
    setPreview(null);
    setExecutedPreviewRowLimit(null);
    setPreviewError("");
    setExportError("");
  };

  const clearPreview = () => {
    previewRequestSequence.current += 1;
    setPreview(null);
    setExecutedPreviewRowLimit(null);
    setPreviewLoadingObject("");
    setPreviewError("");
    setExportError("");
    setPreviewRowLimitInput(String(DEFAULT_DATA_PREVIEW_ROW_LIMIT));
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
    const target = parseDbAdminObjectTarget(csvTable);
    setCsvUploading(true);
    setCsvUploadError("");
    try {
      const result = await apiPost<DbAdminCsvUploadData>("/api/nl2sql/db-admin/upload-csv", {
        table_name: target.name,
        owner: target.owner,
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
        await csvTablesQuery.refetch();
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
      clearSyntheticResultState();
      toast.success(t("dataTools.syntheticData.toast.tablesLoaded", { count: nextTables.length }));
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
    toast.info(t("dataTools.syntheticData.toast.generateStarted"));
    try {
      clearSyntheticResultState();
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
      if (isSyntheticDataExecuted(result)) {
        toast.success(t("dataTools.syntheticData.toast.generated"));
      } else {
        setSyntheticError(syntheticDataOperationMessage(result));
        setSyntheticErrorOperation("generate");
      }
    } catch (err) {
      setSyntheticError(apiErrorMessage(err, "dataTools.error.syntheticData"));
      setSyntheticErrorOperation("generate");
    } finally {
      setSyntheticLoading("");
    }
  };

  const loadSyntheticDataResults = async () => {
    const tableName = syntheticResultTable.trim();
    const rowLimit = syntheticResultLimit;
    if (!tableName || !syntheticAvailableTables.includes(tableName) || rowLimit === null) return;
    setSyntheticLoading("results");
    setSyntheticError("");
    setSyntheticErrorOperation("");
    setSyntheticDataResults(null);
    setExecutedSyntheticResultLimit(null);
    try {
      const result = await apiGet<SyntheticDataResultsData>(
        `/api/nl2sql/synthetic-data/results?table_name=${encodeURIComponent(tableName)}&limit=${rowLimit}`,
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail }
      );
      setSyntheticDataResults(result);
      setExecutedSyntheticResultLimit(rowLimit);
      toast.success(t("dataTools.syntheticData.toast.resultsLoaded", { name: result.table_name }));
    } catch (err) {
      setSyntheticError(apiErrorMessage(err, "dataTools.error.syntheticResults"));
      setSyntheticErrorOperation("results");
    } finally {
      setSyntheticLoading("");
    }
  };

  const previewObjectErrorMessage =
    previewObjectsQuery.error && !previewObjectsQuery.data
      ? objectListErrorMessage(previewObjectsQuery.error)
      : "";
  const previewObjectLoadMoreError =
    previewObjectsQuery.isFetchNextPageError && previewObjectsQuery.error
      ? objectListLoadMoreErrorMessage(previewObjectsQuery.error)
      : "";
  const csvTablesErrorMessage =
    csvTablesQuery.error && !csvTablesQuery.data
      ? objectListErrorMessage(csvTablesQuery.error)
      : "";
  const csvTablesLoadMoreError =
    csvTablesQuery.isFetchNextPageError && csvTablesQuery.error
      ? objectListLoadMoreErrorMessage(csvTablesQuery.error)
      : "";
  const firstObjectPage = baseObjectPages[0];
  const visibleSchemaJobError = schemaJobQuery.error
    ? apiErrorMessage(schemaJobQuery.error, "dataMgmt.schemaJob.error")
    : schemaJobError || sharedSchemaRefresh.error;
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  const objectRefreshing =
    ((baseObjectsQuery.isFetching && !baseObjectsQuery.isFetchingNextPage) ||
      (previewObjectsQuery.isFetching && !previewObjectsQuery.isFetchingNextPage) ||
      (csvTablesQuery.isFetching && !csvTablesQuery.isFetchingNextPage)) &&
    Boolean(baseObjectsQuery.data || previewObjectsQuery.data || csvTablesQuery.data);
  const objectRefreshingFromHeader =
    (baseObjectsQuery.isFetching && !baseObjectsQuery.isFetchingNextPage) ||
    (previewObjectsQuery.isFetching && !previewObjectsQuery.isFetchingNextPage) ||
    (csvTablesQuery.isFetching && !csvTablesQuery.isFetchingNextPage);

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
        status={<SchemaRefreshHeaderStatus testId="data-management-schema-refresh-status" />}
        actions={[
          {
            id: "refresh-data",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: objectRefreshingFromHeader,
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
          action={
            visibleSchemaJobError ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={
                  schemaJobNeedsFull
                    ? () => void submitSchemaRefresh()
                    : () => void refreshObjects()
                }
              >
                <RefreshCw size={15} aria-hidden="true" />
                <span>
                  {schemaJobNeedsFull
                    ? t("common.action.schemaRefresh")
                    : t("common.action.refresh")}
                </span>
              </Button>
            ) : null
          }
        />
        {schemaRefreshing ? (
          <SchemaRefreshProcessing testId="data-management-workspace-processing" />
        ) : objectRefreshing ? (
          <ProcessingIndicator
            active
            label={t("common.processing.refreshing")}
            operationKey="object-refresh"
            placement="workspace"
            className="rounded-md border border-border bg-card px-3 py-2 shadow-sm"
            testId="data-management-workspace-processing"
            activityIcon="none"
          />
        ) : null}

        <DbObjectManagementTabs
          activeView={activeView}
          tabs={[
            { id: "preview", label: t("dataMgmt.preview.title"), icon: Table2 },
            { id: "csv", label: t("dataMgmt.csv.title"), icon: Upload },
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
            topContent={
              <DbObjectStepIndicator
                steps={[t("dataMgmt.preview.stepTarget"), t("dataMgmt.preview.stepResults")]}
                activeIndex={previewObject ? 1 : 0}
                ariaLabel={t("dataMgmt.preview.steps")}
                dataTestId="data-preview-steps"
              />
            }
          >
            <PreviewControlsPanel
              previewObjectCounts={previewObjectCounts}
              previewObjectPickerItems={previewObjectPickerItems}
              previewObject={previewObject}
              previewObjectSearch={previewObjectSearch}
              previewObjectOwnerPrefix={previewObjectOwnerPrefix}
              previewObjectKindFilter={previewObjectKindFilter}
              previewObjectSort={previewObjectSort}
              initialLoading={previewObjectsQuery.isPending && !previewObjectsQuery.data}
              error={previewObjectErrorMessage}
              hasNextPage={Boolean(previewObjectsQuery.hasNextPage)}
              loadingNextPage={previewObjectsQuery.isFetchingNextPage}
              loadMoreError={previewObjectLoadMoreError}
              onPreviewObjectSearchChange={setPreviewObjectSearch}
              onPreviewObjectOwnerPrefixChange={setPreviewObjectOwnerPrefix}
              onPreviewObjectKindFilterChange={setPreviewObjectKindFilter}
              onPreviewObjectSortChange={(key) =>
                setPreviewObjectSort((current) => nextObjectPickerSort(current, key))
              }
              onSelectPreviewObject={(objectName) => selectPreviewObject(objectName, { manualSelection: true })}
              onRetry={() => void refreshObjects()}
              onLoadMore={() => void previewObjectsQuery.fetchNextPage()}
            />
            <PreviewResultsPanel
              preview={preview}
              loading={Boolean(previewLoadingObject)}
              exporting={exportLoading}
              previewError={previewError}
              exportError={exportError}
              rowLimitInput={previewRowLimitInput}
              rowLimitError={previewRowLimitError}
              executedRowLimit={executedPreviewRowLimit}
              canShowPreview={canShowPreview}
              canClearPreview={canClearPreview}
              selectedObjectName={previewObject}
              selectedObjectKind={
                selectedPreviewObjectItem?.kind === "table" ||
                selectedPreviewObjectItem?.kind === "view"
                  ? selectedPreviewObjectItem.kind
                  : undefined
              }
              truncateDisabled={Boolean(previewLoadingObject) || truncateLoading}
              onRowLimitChange={updatePreviewRowLimitInput}
              onShowPreview={() => void showPreview(previewObject)}
              onClearPreview={clearPreview}
              onRetryPreview={() => void showPreview(previewObject)}
              onDownload={() => void downloadPreviewXlsx()}
              onTruncateTable={openTruncateDialog}
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
              tablePickerItems={csvTablePickerItems}
              tableTotalCount={csvTableCount}
              table={csvTable}
              tableSearch={csvTableSearch}
              tableSort={csvTableSort}
              filename={csvFilename}
              mode={csvMode}
              step={csvStep}
              confirmation={csvConfirmation}
              confirmed={csvConfirmed}
              canUpload={canUploadCsv}
              result={csvUploadResult}
              loading={csvUploading}
              error={csvUploadError}
              tablesLoading={csvTablesQuery.isPending && !csvTablesQuery.data}
              tablesError={
                csvTablesErrorMessage
              }
              hasNextPage={Boolean(csvTablesQuery.hasNextPage)}
              loadingNextPage={csvTablesQuery.isFetchingNextPage}
              loadMoreError={csvTablesLoadMoreError}
              onTableSearchChange={setCsvTableSearch}
              onTableSortChange={(key) =>
                setCsvTableSort((current) => nextObjectPickerSort(current, key))
              }
              onTableChange={(value) => {
                csvTableManualSelection.current = true;
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
              onTablesRetry={() => void csvTablesQuery.refetch()}
              onLoadMore={() => void csvTablesQuery.fetchNextPage()}
            />
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
              syntheticResultLimitInput={syntheticResultLimitInput}
              syntheticResultLimitError={syntheticResultLimitError}
              executedSyntheticResultLimit={executedSyntheticResultLimit}
              canLoadSyntheticDataResults={canLoadSyntheticDataResults}
              canClearSyntheticDataResults={canClearSyntheticDataResults}
              loading={syntheticLoading}
              error={syntheticWorkspaceError}
              resultError={syntheticResultError}
              dbProfileRefreshRequired={dbProfileRefreshRequired}
              dbProfileRefreshing={dbProfileRefreshing || startDbProfileRefresh.isPending}
              dbProfileRefreshError={dbProfileRefreshError}
              dbProfileRefreshOperationKey={dbProfileRefreshJobId || "synthetic-db-profile-refresh"}
              onRefreshDbProfiles={() => void runDbProfileRefresh()}
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
                clearSyntheticResultState();
              }}
              onSyntheticTablesBulkChange={(tableNames, selected) => {
                const targetSet = new Set(tableNames);
                const nextTables = selected
                  ? uniqueStrings([...syntheticSelectedTables, ...tableNames])
                  : syntheticSelectedTables.filter((item) => !targetSet.has(item));
                setSyntheticSelectedTables(nextTables);
                setSyntheticResultTable((currentTable) =>
                  currentTable && nextTables.includes(currentTable) ? currentTable : (nextTables[0] ?? "")
                );
                setSyntheticData(null);
                clearSyntheticResultState();
              }}
              onSyntheticPromptChange={setSyntheticPrompt}
              onSyntheticConfirmationChange={setSyntheticConfirmation}
              onSyntheticRowsChange={(value) => setSyntheticRows(clampNumber(value, 1, 100))}
              onSyntheticSampleRowsChange={(value) => setSyntheticSampleRows(clampNumber(value, 0, 100))}
              onSyntheticUseCommentsChange={setSyntheticUseComments}
              onSyntheticResultTableChange={(value) => {
                if (!syntheticAvailableTables.includes(value)) return;
                setSyntheticResultTable(value);
                clearSyntheticResultState();
              }}
              onSyntheticResultLimitChange={(value) => {
                setSyntheticResultLimitInput(value);
                clearSyntheticResultState();
              }}
              onGenerateSyntheticData={() => void generateSyntheticData()}
              onLoadSyntheticDataResults={() => void loadSyntheticDataResults()}
              onClearSyntheticDataResults={() => clearSyntheticResultState({ resetLimit: true })}
              onRetry={() => {
                if (syntheticErrorOperation === "tables") void refreshSyntheticTables();
                else if (syntheticErrorOperation === "results") void loadSyntheticDataResults();
                else void generateSyntheticData();
              }}
            />
            )}
          </DbObjectManagementPanelShell>
        )}
      </main>
      {truncateTargetName && (
        <DropDbObjectDialog
          objectName={truncateTargetName}
          confirmation={truncateConfirmation}
          loading={truncateLoading}
          error={truncateError}
          labels={{
            title: t("dataMgmt.truncateDialog.title"),
            subtitle: t("dataMgmt.truncateDialog.subtitle"),
            close: t("dataMgmt.truncateDialog.close"),
            target: t("dataMgmt.truncateDialog.target"),
            executeTitle: t("dataMgmt.truncateDialog.executeTitle"),
            executeHint: t("dataMgmt.truncateDialog.executeHint"),
            cancel: t("dataMgmt.truncateDialog.cancel"),
            run: t("dataMgmt.truncate.action"),
          }}
          onConfirmationChange={(value) => {
            setTruncateConfirmation(value);
            if (truncateError) setTruncateError("");
          }}
          onExecute={() => void truncateTableData()}
          onClose={closeTruncateDialog}
        />
      )}
    </>
  );
}

function PreviewControlsPanel({
  previewObjectCounts,
  previewObjectPickerItems,
  previewObject,
  previewObjectSearch,
  previewObjectOwnerPrefix,
  previewObjectKindFilter,
  previewObjectSort,
  initialLoading,
  error,
  hasNextPage,
  loadingNextPage,
  loadMoreError,
  onPreviewObjectSearchChange,
  onPreviewObjectOwnerPrefixChange,
  onPreviewObjectKindFilterChange,
  onPreviewObjectSortChange,
  onSelectPreviewObject,
  onRetry,
  onLoadMore,
}: {
  previewObjectCounts: DbAdminObjectCounts;
  previewObjectPickerItems: DbObjectPickerItem[];
  previewObject: string;
  previewObjectSearch: string;
  previewObjectOwnerPrefix: string;
  previewObjectKindFilter: PreviewObjectKindFilter;
  previewObjectSort: DbObjectPickerSortState;
  initialLoading: boolean;
  error: string;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  loadMoreError: string;
  onPreviewObjectSearchChange: (value: string) => void;
  onPreviewObjectOwnerPrefixChange: (value: string) => void;
  onPreviewObjectKindFilterChange: (value: PreviewObjectKindFilter) => void;
  onPreviewObjectSortChange: (key: DbObjectPickerSortKey) => void;
  onSelectPreviewObject: (objectName: string) => void;
  onRetry: () => void;
  onLoadMore: () => void;
}) {
  const hasActiveFilter =
    Boolean(previewObjectSearch.trim()) ||
    Boolean(previewObjectOwnerPrefix.trim()) ||
    previewObjectKindFilter !== "all";

  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="data-preview-controls-heading">
      <DbObjectPanelHeader
        headingId="data-preview-controls-heading"
        icon={Table2}
        title={t("dataMgmt.preview.title")}
        description={t("dataMgmt.preview.controlsHint")}
        action={
          <>
            <StatusBadge variant="info" label={t("dataMgmt.preview.objectTotalCount", { count: previewObjectCounts.totalCount })} />
            <StatusBadge variant="neutral" label={t("dataMgmt.preview.objectTableCount", { count: previewObjectCounts.tableCount })} />
            <StatusBadge variant="neutral" label={t("dataMgmt.preview.objectViewCount", { count: previewObjectCounts.viewCount })} />
          </>
        }
      />

      <div className="grid gap-3 rounded-md border border-border bg-background p-3">
        <div className="grid gap-2">
          <DbObjectSelectorToolbar
            searchLabel={t("dbAdmin.search.label")}
            searchPlaceholder={t("dbAdmin.search.placeholder")}
            searchValue={previewObjectSearch}
            onSearchChange={onPreviewObjectSearchChange}
            dataTestId="data-preview-object-toolbar"
            ownerPrefixField={{
              label: t("dbAdmin.owner.label"),
              placeholder: t("dbAdmin.ownerPrefix.placeholder"),
              value: previewObjectOwnerPrefix,
              onChange: onPreviewObjectOwnerPrefixChange,
            }}
          >
            <DbManagementSelectField
              label={t("dataMgmt.preview.kindFilter")}
              value={previewObjectKindFilter}
              options={[
                { value: "all", label: t("dataMgmt.preview.kindFilterAll") },
                { value: "table", label: t("dataMgmt.preview.kindFilterTable") },
                { value: "view", label: t("dataMgmt.preview.kindFilterView") },
              ]}
              className="sm:w-48"
              onChange={onPreviewObjectKindFilterChange}
            />
          </DbObjectSelectorToolbar>
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
              <DbSingleObjectPickerList
                items={previewObjectPickerItems}
                selectedKey={previewObject}
                hasActiveFilter={hasActiveFilter}
                listLabel={t("dataMgmt.preview.object")}
                emptyTitle={t("dataMgmt.preview.emptyObjectsTitle")}
                emptyHint={t("dataMgmt.preview.emptyObjectsHint")}
                noResultsTitle={t("dataMgmt.preview.noObjectsTitle")}
                noResultsHint={t("dataMgmt.preview.noObjectsHint")}
                dataTestId="data-preview-object-list"
                sort={previewObjectSort}
                onSortChange={onPreviewObjectSortChange}
                onSelect={(item) => onSelectPreviewObject(item.key)}
                selectAriaLabel={(item) => t("dataMgmt.preview.selectObject", { name: item.name })}
              />
              <DbObjectSelectorFooter
                visibleCount={previewObjectPickerItems.length}
                totalCount={previewObjectCounts.totalCount}
                hasNextPage={hasNextPage}
                loadingNextPage={loadingNextPage}
                loadMoreError={loadMoreError}
                loadMoreLabel={t("dataMgmt.objectList.loadMore")}
                dataTestId="data-preview-object-footer"
                onLoadMore={onLoadMore}
                onRetryLoadMore={onLoadMore}
              />
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function filterPreviewObjects(
  objects: PreviewObject[],
  search: string,
  ownerPrefix: string,
  kindFilter: PreviewObjectKindFilter
) {
  const q = search.trim().toLowerCase();
  const ownerPrefixKey = ownerPrefix.trim().toUpperCase();
  return objects.filter((item) => {
    if (ownerPrefixKey && !item.owner.toUpperCase().startsWith(ownerPrefixKey)) return false;
    if (kindFilter !== "all" && item.kind !== kindFilter) return false;
    if (!q) return true;
    return [item.name, item.comment].join(" ").toLowerCase().includes(q);
  });
}

function apiErrorMessage(
  error: unknown,
  fallbackKey: Parameters<typeof t>[0],
  timeoutKey: Parameters<typeof t>[0] = "dataMgmt.operation.timeout",
  timeoutParams?: Record<string, string | number>
) {
  if (isTimeoutError(error)) return t(timeoutKey, timeoutParams);
  return error instanceof Error ? error.message : t(fallbackKey);
}

function objectListErrorMessage(error: unknown) {
  return apiErrorMessage(error, "dataMgmt.objectList.error", "dataMgmt.objectList.timeout", {
    seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
  });
}

function objectListLoadMoreErrorMessage(error: unknown) {
  return apiErrorMessage(error, "dataMgmt.objectList.error", "objectSelector.loadMoreTimeout", {
    seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
  });
}

function isSyntheticDataExecuted(result: SyntheticDataOperationData) {
  return result.executed === true || (result.status ?? "").toLowerCase() === "executed";
}

function syntheticDataOperationMessage(result: SyntheticDataOperationData) {
  const warnings = (result.warnings ?? []).map((warning) => warning.trim()).filter(Boolean);
  if (warnings.length > 0) return warnings.join(" ");
  return result.message?.trim() || t("dataTools.error.syntheticData");
}

function schemaJobRequiresFull(job: SchemaRefreshJob | null) {
  if (!job) return false;
  return (
    Boolean(job.requires_full_refresh) ||
    job.error_code === "schema_refresh_full_required" ||
    job.error_code === "schema_refresh_target_unresolved"
  );
}

function schemaJobRequiredMessage(reasonCode = "") {
  if (reasonCode === "schema_refresh_target_unresolved") {
    return t("dataMgmt.schemaJob.targetUnresolved");
  }
  return t("dataMgmt.schemaJob.fullRequired");
}

function schemaJobErrorMessage(job: SchemaRefreshJob) {
  if (schemaJobRequiresFull(job)) {
    return schemaJobRequiredMessage(job.error_code);
  }
  return job.error_code
    ? `${t("dataMgmt.schemaJob.error")} (${job.error_code})`
    : t("dataMgmt.schemaJob.error");
}

function isSelectAiDbProfileRefreshWarning(warning: string) {
  return warning.includes("DB Profile 一覧") && warning.includes("read model が未初期化");
}

function selectAiDbProfileRefreshRequired(data: SelectAiDbProfilesData | null) {
  return Boolean(data?.profile_list_refresh_required) ||
    Boolean(data?.warnings.some(isSelectAiDbProfileRefreshWarning));
}

function dbProfileRefreshErrorMessage(reasonCode = "", fallback = "") {
  if (reasonCode === "profile_list_refresh_target_unresolved") {
    return t("profiles.dbProfileRefresh.targetUnresolved");
  }
  if (reasonCode === "profile_list_refresh_submit_failed") {
    return t("profiles.dbProfileRefresh.submitFailed");
  }
  if (reasonCode === "profile_list_refresh_full_required") {
    return t("profiles.dbProfileRefresh.fullRequired");
  }
  return fallback || t("profiles.dbProfileRefresh.error");
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
  rowLimitInput,
  rowLimitError,
  executedRowLimit,
  canShowPreview,
  canClearPreview,
  selectedObjectName,
  selectedObjectKind,
  truncateDisabled,
  onRowLimitChange,
  onShowPreview,
  onClearPreview,
  onRetryPreview,
  onDownload,
  onTruncateTable,
}: {
  preview: DbAdminDataPreviewData | null;
  loading: boolean;
  exporting: boolean;
  previewError: string;
  exportError: string;
  rowLimitInput: string;
  rowLimitError: string;
  executedRowLimit: number | null;
  canShowPreview: boolean;
  canClearPreview: boolean;
  selectedObjectName: string;
  selectedObjectKind?: PreviewObjectKind;
  truncateDisabled?: boolean;
  onRowLimitChange: (value: string) => void;
  onShowPreview: () => void;
  onClearPreview: () => void;
  onRetryPreview: () => void;
  onDownload: () => void;
  onTruncateTable: (objectName: string) => void;
}) {
  const showTruncateAction = Boolean(selectedObjectName) && selectedObjectKind === "table";
  const hasRowLimitError = Boolean(rowLimitError);
  const previewActions: EntityAction[] = [
    {
      id: "download-preview-xlsx",
      label: t("dataMgmt.preview.exportXlsx"),
      icon: FileSpreadsheet,
      loading: exporting,
      disabled: !preview || preview.results.rows.length === 0 || hasRowLimitError,
      onSelect: onDownload,
    },
    {
      id: "truncate-preview-table",
      label: t("dataMgmt.truncate.action"),
      ariaLabel: selectedObjectName
        ? t("dataMgmt.truncate.actionObject", { name: selectedObjectName })
        : t("dataMgmt.truncate.action"),
      icon: Trash2,
      tone: "danger",
      visible: showTruncateAction,
      disabled: truncateDisabled,
      onSelect: () => {
        if (selectedObjectName) onTruncateTable(selectedObjectName);
      },
    },
  ];

  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4" aria-labelledby="data-preview-results-heading">
      <DbObjectPanelHeader
        headingId="data-preview-results-heading"
        icon={FileSpreadsheet}
        title={t("dataMgmt.preview.resultsTitle")}
        description={t("dataMgmt.preview.resultsHint")}
        action={
          <ObjectActionBar
            actions={previewActions}
            ariaLabel={t("dataMgmt.preview.actions")}
            testId="data-preview-results-actions"
          />
        }
      />
      <div className="grid gap-3 border-t border-border pt-3">
        <RowLimitField
          value={rowLimitInput}
          onChange={onRowLimitChange}
          disabled={loading}
          error={rowLimitError}
          className="sm:w-48"
        />
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <Button
            type="button"
            variant="primary"
            size="lg"
            className="w-full sm:w-auto"
            loading={loading}
            disabled={!canShowPreview}
            onClick={onShowPreview}
          >
            <Play size={16} aria-hidden="true" />
            <span>{t("dataMgmt.preview.show")}</span>
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="lg"
            className="w-full sm:w-auto"
            disabled={!canClearPreview}
            onClick={onClearPreview}
          >
            <X size={16} aria-hidden="true" />
            <span>{t("dataMgmt.preview.clear")}</span>
          </Button>
        </div>
      </div>
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
          <QueryResultsTable results={preview.results} rowLimit={executedRowLimit} />
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
  tablePickerItems,
  tableTotalCount,
  table,
  tableSearch,
  tableSort,
  filename,
  mode,
  step,
  confirmation,
  confirmed,
  canUpload,
  result,
  loading,
  error,
  tablesLoading,
  tablesError,
  hasNextPage,
  loadingNextPage,
  loadMoreError,
  onTableSearchChange,
  onTableSortChange,
  onTableChange,
  onModeChange,
  onFilePick,
  onFileClear,
  onConfirmationChange,
  onUpload,
  onRetry,
  onTablesRetry,
  onLoadMore,
}: {
  tablePickerItems: DbObjectPickerItem[];
  tableTotalCount: number;
  table: string;
  tableSearch: string;
  tableSort: DbObjectPickerSortState;
  filename: string;
  mode: CsvMode;
  step: CsvStep;
  confirmation: string;
  confirmed: boolean;
  canUpload: boolean;
  result: DbAdminCsvUploadData | null;
  loading: boolean;
  error: string;
  tablesLoading: boolean;
  tablesError: string;
  hasNextPage: boolean;
  loadingNextPage: boolean;
  loadMoreError: string;
  onTableSearchChange: (value: string) => void;
  onTableSortChange: (key: DbObjectPickerSortKey) => void;
  onTableChange: (value: string) => void;
  onModeChange: (value: CsvMode) => void;
  onFilePick: (file: File) => void;
  onFileClear: () => void;
  onConfirmationChange: (value: string) => void;
  onUpload: () => void;
  onRetry: () => void;
  onTablesRetry: () => void;
  onLoadMore: () => void;
}) {
  const activeIndex = step === "execute" ? 1 : 0;
  const hasTableFilter = Boolean(tableSearch.trim());
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

      <RequiredFieldsNote />

      <section
        className="grid min-w-0 gap-3"
        aria-labelledby="data-csv-table-heading"
        data-testid="data-csv-table-section"
      >
        <div>
          <h3 id="data-csv-table-heading" className="text-sm font-semibold text-foreground">
            {t("dataMgmt.csv.table")}
            <RequiredIndicator />
          </h3>
          <p className="mt-1 text-sm text-muted">{t("dataMgmt.csv.tableHint")}</p>
        </div>
        <DbObjectSelectionSummary label={t("objectSelector.selected")} value={table} />
        <DbObjectSelectorToolbar
          searchLabel={t("objectSelector.search")}
          searchPlaceholder={t("dataMgmt.csv.tableSearchPlaceholder")}
          searchValue={tableSearch}
          onSearchChange={onTableSearchChange}
          dataTestId="data-csv-table-toolbar"
        />
        {tablesLoading ? (
          <DbManagementLoadingSkeleton
            idPrefix="data-csv-table"
            ariaLabel={t("dataMgmt.objectList.loading")}
            variant="list"
            rows={5}
          />
        ) : tablesError ? (
          <ErrorState message={tablesError} onRetry={onTablesRetry} />
        ) : (
          <>
            <DbSingleObjectPickerList
              items={tablePickerItems}
              selectedKey={table}
              hasActiveFilter={hasTableFilter}
              listLabel={t("dataMgmt.csv.table")}
              emptyTitle={t("dataMgmt.csv.emptyTablesTitle")}
              emptyHint={t("dataMgmt.csv.emptyTablesHint")}
              noResultsTitle={t("dataMgmt.csv.noTablesTitle")}
              noResultsHint={t("dataMgmt.csv.noTablesHint")}
              dataTestId="data-csv-table-list"
              maxHeightClass={DB_OBJECT_PICKER_SHORT_SCROLL_CLASS}
              sort={tableSort}
              onSortChange={onTableSortChange}
              onSelect={(item) => onTableChange(item.key)}
            />
            <DbObjectSelectorFooter
              visibleCount={tablePickerItems.length}
              totalCount={tableTotalCount}
              hasNextPage={hasNextPage}
              loadingNextPage={loadingNextPage}
              loadMoreError={loadMoreError}
              loadMoreLabel={t("dataMgmt.objectList.loadMore")}
              dataTestId="data-csv-table-footer"
              onLoadMore={onLoadMore}
              onRetryLoadMore={onLoadMore}
            />
          </>
        )}
      </section>

      {error && <ErrorState message={error} onRetry={onRetry} />}

      <FileDropzone
        label={t("dataMgmt.csv.file")}
        accept={CORE_TABULAR_FILE_FORMATS.accept}
        selectedText={filename ? t("tableMgmt.importWizard.selectedFile", { filename }) : ""}
        formatLabel={CORE_TABULAR_FILE_FORMATS.formatLabel}
        actionText={t("common.fileDropzone.action")}
        replaceText={t("dataMgmt.csv.fileReplace")}
        clearAriaLabel={t("dataMgmt.csv.clearFile")}
        icon="spreadsheet"
        required
        dataTestId="data-csv-file-field"
        onFiles={([file]) => onFilePick(file)}
        onClear={onFileClear}
      />

      <label
        className="grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground"
        data-testid="data-csv-mode-field"
      >
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

      <fieldset
        className="grid gap-3 rounded-md border border-border bg-card p-3"
        data-testid="data-csv-execution-fieldset"
      >
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
    </div>
  );
}

function SyntheticWorkspace({
  selectAiDbProfiles,
  selectedSyntheticProfile,
  syntheticData,
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
  syntheticResultLimitInput,
  syntheticResultLimitError,
  executedSyntheticResultLimit,
  canLoadSyntheticDataResults,
  canClearSyntheticDataResults,
  loading,
  error,
  resultError,
  dbProfileRefreshRequired,
  dbProfileRefreshing,
  dbProfileRefreshError,
  dbProfileRefreshOperationKey,
  onRefreshDbProfiles,
  onRefreshTables,
  onSyntheticProfileNameChange,
  onSyntheticTableToggle,
  onSyntheticTablesBulkChange,
  onSyntheticPromptChange,
  onSyntheticConfirmationChange,
  onSyntheticRowsChange,
  onSyntheticSampleRowsChange,
  onSyntheticUseCommentsChange,
  onSyntheticResultTableChange,
  onSyntheticResultLimitChange,
  onGenerateSyntheticData,
  onLoadSyntheticDataResults,
  onClearSyntheticDataResults,
  onRetry,
}: {
  selectAiDbProfiles: SelectAiDbProfilesData | null;
  selectedSyntheticProfile: SelectAiDbProfile | null;
  syntheticData: SyntheticDataOperationData | null;
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
  syntheticResultLimitInput: string;
  syntheticResultLimitError: string;
  executedSyntheticResultLimit: number | null;
  canLoadSyntheticDataResults: boolean;
  canClearSyntheticDataResults: boolean;
  loading: SyntheticLoading;
  error: string;
  resultError: string;
  dbProfileRefreshRequired: boolean;
  dbProfileRefreshing: boolean;
  dbProfileRefreshError: string;
  dbProfileRefreshOperationKey: string;
  onRefreshDbProfiles: () => void;
  onRefreshTables: () => void;
  onSyntheticProfileNameChange: (value: string) => void;
  onSyntheticTableToggle: (tableName: string, selected: boolean) => void;
  onSyntheticTablesBulkChange: (tableNames: string[], selected: boolean) => void;
  onSyntheticPromptChange: (value: string) => void;
  onSyntheticConfirmationChange: (value: string) => void;
  onSyntheticRowsChange: (value: number) => void;
  onSyntheticSampleRowsChange: (value: number) => void;
  onSyntheticUseCommentsChange: (value: boolean) => void;
  onSyntheticResultTableChange: (value: string) => void;
  onSyntheticResultLimitChange: (value: string) => void;
  onGenerateSyntheticData: () => void;
  onLoadSyntheticDataResults: () => void;
  onClearSyntheticDataResults: () => void;
  onRetry: () => void;
}) {
  const activeStep = syntheticData || syntheticDataResults ? 1 : 0;
  const [syntheticTableSearch, setSyntheticTableSearch] = useState("");
  // 親の syntheticDataConfirmed と同じ規則(単一テーブル=対象名 / 複数=ADMIN_EXECUTE)。
  const syntheticExpectedConfirmation =
    syntheticSelectedTables.length === 1 ? syntheticSelectedTables[0] : "ADMIN_EXECUTE";
  const resultTableOptions = syntheticAvailableTables;
  const hasValidResultTable = resultTableOptions.includes(syntheticResultTable);
  const normalizedSyntheticTableSearch = syntheticTableSearch.trim().toLowerCase();
  const filteredSyntheticTables = normalizedSyntheticTableSearch
    ? syntheticAvailableTables.filter((tableName) =>
        tableName.toLowerCase().includes(normalizedSyntheticTableSearch)
      )
    : syntheticAvailableTables;
  const selectedVisibleTableCount = filteredSyntheticTables.filter((tableName) =>
    syntheticSelectedTables.includes(tableName)
  ).length;
  const allVisibleTablesSelected =
    filteredSyntheticTables.length > 0 && selectedVisibleTableCount === filteredSyntheticTables.length;
  const visibleWarnings = (selectAiDbProfiles?.warnings ?? []).filter(
    (warning) => !isSelectAiDbProfileRefreshWarning(warning)
  );

  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Database}
        title={t("dataTools.synthetic.title")}
        description={t("dataMgmt.section.syntheticHint")}
      />
      {error && <ErrorState message={error} onRetry={onRetry} />}
      {dbProfileRefreshing ? (
        <ProcessingIndicator
          active
          label={t("common.processing.dbProfileListRefreshing")}
          operationKey={dbProfileRefreshOperationKey}
          placement="panel"
          className="rounded-md border border-border bg-background px-3 py-2"
          testId="data-synthetic-db-profile-refresh-processing"
          activityIcon="none"
        />
      ) : null}

      <DbObjectStepIndicator
        steps={[
          t("dataTools.syntheticData.stepTarget"),
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
        />

        {dbProfileRefreshRequired || dbProfileRefreshError ? (
          <DbProfileRefreshNotice
            error={dbProfileRefreshError}
            loading={dbProfileRefreshing}
            onRefresh={onRefreshDbProfiles}
          />
        ) : null}

        <div className="grid min-w-0 gap-3 lg:grid-cols-[minmax(0,1fr)_10rem]">
          <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
            <span>{t("dataTools.syntheticData.profile")}</span>
            <select
              value={syntheticProfileName}
              onChange={(event) => onSyntheticProfileNameChange(event.currentTarget.value)}
              disabled={dbProfileRefreshRequired || dbProfileRefreshing}
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
          {visibleWarnings.map((warning) => (
            <span
              key={warning}
              className="rounded-md border border-warning/30 bg-warning-bg px-2 py-1 text-xs text-warning"
            >
              {warning}
            </span>
          ))}
        </div>

        <ContentActionBar
          ariaLabel={t("dataTools.syntheticData.refreshTablesActions")}
          title={t("dataTools.syntheticData.refreshTablesActionTitle")}
          description={
            syntheticProfileName
              ? t("dataTools.syntheticData.refreshTablesActionReady")
              : t("dataTools.syntheticData.refreshTablesActionDisabled")
          }
          actionsClassName="w-full sm:w-auto"
          testId="data-synthetic-refresh-tables-actions"
        >
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="w-full sm:w-auto"
            loading={loading === "tables"}
            disabled={!syntheticProfileName || dbProfileRefreshRequired || dbProfileRefreshing}
            onClick={onRefreshTables}
          >
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("dataTools.syntheticData.refreshTables")}</span>
          </Button>
        </ContentActionBar>

        <div className="grid min-w-0 gap-2">
          <DbObjectSelectorToolbar
            searchLabel={t("dataTools.syntheticData.tables")}
            searchPlaceholder={t("dataTools.syntheticData.tableSearchPlaceholder")}
            searchValue={syntheticTableSearch}
            onSearchChange={setSyntheticTableSearch}
            dataTestId="data-synthetic-table-toolbar"
          />
          {loading !== "tables" && filteredSyntheticTables.length > 0 ? (
            <BulkSelectionActions
              selectLabel={t("common.selection.selectVisible")}
              clearLabel={t("common.selection.clearVisible")}
              selectDisabled={allVisibleTablesSelected}
              clearDisabled={selectedVisibleTableCount === 0}
              dataTestId="data-synthetic-table-selection-actions"
              onSelectAll={() => onSyntheticTablesBulkChange(filteredSyntheticTables, true)}
              onClearAll={() => onSyntheticTablesBulkChange(filteredSyntheticTables, false)}
            />
          ) : null}
          {loading === "tables" ? (
            <DbManagementLoadingSkeleton
              idPrefix="data-synthetic-tables"
              ariaLabel={t("dataTools.syntheticData.tablesLoading")}
              variant="list"
              rows={4}
              placement="result"
            />
          ) : filteredSyntheticTables.length > 0 ? (
            <div
              className={`${INFORMATION_COMPACT_LIST_FIVE_ROW_SCROLL_CLASS} rounded-md border border-border bg-card`}
              role="group"
              aria-label={t("dataTools.syntheticData.tables")}
              data-testid="data-synthetic-table-list"
            >
              <div className="grid divide-y divide-border/70">
                {filteredSyntheticTables.map((tableName) => {
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
              title={
                normalizedSyntheticTableSearch
                  ? t("dataTools.syntheticData.noTableResultsTitle")
                  : t("dataTools.syntheticData.noTablesTitle")
              }
              hint={
                normalizedSyntheticTableSearch
                  ? t("dataTools.syntheticData.noTableResultsHint")
                  : t("dataTools.syntheticData.noTablesHint")
              }
            />
          )}
          {loading !== "tables" && (
            <DbObjectSelectorFooter
              visibleCount={filteredSyntheticTables.length}
              totalCount={syntheticAvailableTables.length}
              selectedCount={syntheticSelectedTables.length}
              dataTestId="data-synthetic-table-footer"
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
                disabled={!canGenerateSyntheticData || dbProfileRefreshRequired || dbProfileRefreshing}
                onClick={onGenerateSyntheticData}
              >
                <Database size={15} aria-hidden="true" />
                <span>{t("dataTools.syntheticData.generate")}</span>
              </Button>
            }
          />
        </fieldset>
      </section>

      <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4" aria-labelledby="synthetic-results-heading">
        <DbObjectPanelHeader
          headingId="synthetic-results-heading"
          icon={Eye}
          title={t("dataTools.syntheticData.resultsActionTitle")}
          description={t("dataTools.syntheticData.resultsActionDisabled")}
        />

        <div className="grid gap-3 border-t border-border pt-3">
          <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
            <span>{t("dataTools.syntheticData.resultTable")}</span>
            <select
              data-testid="synthetic-result-table-select"
              value={hasValidResultTable ? syntheticResultTable : ""}
              onChange={(event) => onSyntheticResultTableChange(event.currentTarget.value)}
              disabled={loading === "results"}
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
          <RowLimitField
            value={syntheticResultLimitInput}
            onChange={onSyntheticResultLimitChange}
            disabled={loading === "results"}
            error={syntheticResultLimitError}
            className="w-full max-w-[22rem]"
            min={1}
            max={SYNTHETIC_RESULT_MAX_LIMIT}
            helper={t("dataTools.syntheticData.resultLimitHelper")}
          />
          <div
            className="flex flex-col gap-2 sm:flex-row sm:flex-wrap"
            role="group"
            aria-label={t("dataTools.syntheticData.resultsActions")}
            data-testid="data-synthetic-results-actions"
          >
            <Button
              type="button"
              variant="primary"
              size="lg"
              className="w-full sm:w-auto"
              loading={loading === "results"}
              disabled={!canLoadSyntheticDataResults}
              onClick={onLoadSyntheticDataResults}
            >
              <Eye size={16} aria-hidden="true" />
              <span>{t("dataTools.syntheticData.results")}</span>
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="lg"
              className="w-full sm:w-auto"
              disabled={!canClearSyntheticDataResults || loading === "results"}
              onClick={onClearSyntheticDataResults}
            >
              <X size={16} aria-hidden="true" />
              <span>{t("dataMgmt.preview.clear")}</span>
            </Button>
          </div>
        </div>

        {loading === "results" ? (
          <DbManagementLoadingSkeleton
            idPrefix="data-synthetic-results"
            ariaLabel={t("dataTools.syntheticData.resultsLoading")}
            variant="detail"
            placement="result"
          />
        ) : resultError ? (
          <ErrorState message={resultError} onRetry={onLoadSyntheticDataResults} />
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
            <QueryResultsTable results={syntheticDataResults.results} rowLimit={executedSyntheticResultLimit} />
          </div>
        ) : (
          <EmptyState title={t("dataTools.syntheticData.noResultsTitle")} hint={t("dataTools.syntheticData.noResultsHint")} />
        )}
      </section>
    </div>
  );
}

function DbProfileRefreshNotice({
  error,
  loading,
  onRefresh,
}: {
  error: string;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <div data-testid="data-synthetic-db-profile-refresh-notice">
      <Banner
        severity={error ? "danger" : "warning"}
        title={t("dataTools.syntheticData.dbProfileRefreshRequiredTitle")}
        action={
          <div
            className="flex min-w-0 flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center"
            aria-label={t("dataTools.syntheticData.dbProfileRefreshActions")}
          >
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="w-full sm:w-auto"
              loading={loading}
              disabled={loading}
              onClick={onRefresh}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("profiles.action.dbProfileRefresh")}</span>
            </Button>
            <Link
              to={APP_ROUTES.profiles}
              className={`${buttonVariants({ variant: "secondary", size: "sm" })} w-full sm:w-auto`}
            >
              <span>{t("dataTools.syntheticData.openProfileManagement")}</span>
              <ArrowRight size={15} aria-hidden="true" />
            </Link>
          </div>
        }
      >
        <p className="leading-6 text-foreground/90">
          {t("dataTools.syntheticData.dbProfileRefreshRequiredHint")}
        </p>
        {error ? (
          <p className="mt-1 leading-6 text-danger">{error}</p>
        ) : null}
      </Banner>
    </div>
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
  const owner = item.owner ?? item.OWNER;
  const name =
    item.name ??
    item.NAME ??
    item.object_name ??
    item.OBJECT_NAME ??
    item.table_name ??
    item.TABLE_NAME ??
    item.objectName ??
    item.tableName;
  if (typeof name !== "string") return "";
  const trimmedName = name.trim();
  if (!trimmedName) return "";
  return typeof owner === "string" && owner.trim()
    ? `${owner.trim()}.${trimmedName}`
    : trimmedName;
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
