import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Code2, RefreshCw, Table2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusBadge, toast } from "@engchina/production-ready-ui";

import { PageHeader, PageHeaderStatusBadge } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { FileDropzone } from "@/components/ui/file-dropzone";
import { FieldLabel, RequiredFieldsNote } from "@/components/ui/required-field";
import { apiFetch, apiGet, apiPost, isAbortError, isTimeoutError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useSchemaOwners } from "@/lib/queries";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import { CORE_TABULAR_FILE_FORMATS } from "@/lib/tabular-file-formats";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  ExecutionConfirmationField,
  QueryResultsTable,
  StatementRunnerCard,
  DbAdminErrorNotice,
  downloadBlob,
  fileToBase64,
} from "../components/DbAdminShared";
import {
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
  type DbObjectOwnerFilter,
  type DbObjectSortKey,
  type DbObjectSortState,
} from "../components/DbObjectManagementShared";
import type {
  DbAdminExecuteData,
  DbAdminImportTabularData,
  DbAdminObjectDetail,
  SchemaRefreshJob,
} from "../types";
import {
  useDbAdminObjects,
  useSchemaRefreshJob,
  waitForSchemaRefreshJob,
} from "../incrementalQueries";
import { dbAdminObjectCountsFromPage } from "../dbAdminObjectCounts";
import { useDbObjectDetailRequest } from "../useDbObjectDetailRequest";

type ActiveView = "list" | "create" | "import";
type ImportStep = "file" | "execute";

const importFieldClass = "grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground";
const importControlClass =
  "h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40";

const useDebouncedValue = <T,>(value: T, delayMs: number) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timeoutId);
  }, [delayMs, value]);
  return debounced;
};

function ImportResultPanel({ result }: { result: DbAdminImportTabularData }) {
  return (
    <section
      className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm"
      aria-label={t("tableMgmt.importWizard.result")}
      data-testid="table-import-result-panel"
    >
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant={result.executed ? "success" : "neutral"} label={result.executed ? "executed" : "not executed"} />
        <StatusBadge variant="info" label={result.table_name} />
        <StatusBadge variant="info" label={t("tableMgmt.importWizard.rows", { count: result.row_count })} />
        <StatusBadge variant="neutral" label={result.mode} />
      </div>
      {result.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
          {warning}
        </p>
      ))}
      <pre className="overflow-auto rounded-md border border-border bg-card p-3 font-mono text-sm leading-6 text-foreground">
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
  );
}

function ImportWizard({
  table,
  sheet,
  filename,
  fileReady,
  result,
  step,
  confirmation,
  loading,
  error,
  schemaRefreshJob,
  schemaRefreshing,
  schemaRefreshError,
  schemaRefreshNeedsFull,
  onTableChange,
  onSheetChange,
  onFilePick,
  onFileClear,
  onExecute,
  onConfirmationChange,
  onReturnToList,
  onSchemaRefresh,
}: {
  table: string;
  sheet: string;
  filename: string;
  fileReady: boolean;
  result: DbAdminImportTabularData | null;
  step: ImportStep;
  confirmation: string;
  loading: boolean;
  error: unknown;
  schemaRefreshJob: SchemaRefreshJob | null;
  schemaRefreshing: boolean;
  schemaRefreshError: string;
  schemaRefreshNeedsFull: boolean;
  onTableChange: (value: string) => void;
  onSheetChange: (value: string) => void;
  onFilePick: (file: File | undefined) => void;
  onFileClear: () => void;
  onExecute: () => void;
  onConfirmationChange: (value: string) => void;
  onReturnToList: () => void;
  onSchemaRefresh: () => void;
}) {
  const steps: Array<{ id: ImportStep; label: string }> = [
    { id: "file", label: t("tableMgmt.importWizard.stepFile") },
    { id: "execute", label: t("tableMgmt.importWizard.stepExecute") },
  ];
  const activeIndex = steps.findIndex((item) => item.id === step);
  const isConfirmed = confirmation.trim() === "ADMIN_EXECUTE";
  const lowerFilename = filename.trim().toLowerCase();
  const sheetRequired = lowerFilename ? !lowerFilename.endsWith(".csv") : true;
  const canExecute =
    Boolean(table.trim()) &&
    fileReady &&
    (!sheetRequired || Boolean(sheet.trim())) &&
    isConfirmed;

  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Upload}
        title={t("dataTools.dbAdmin.importTitle")}
        description={t("tableMgmt.import.note")}
      />

      <DbObjectStepIndicator
        steps={steps.map((item) => item.label)}
        activeIndex={activeIndex}
        ariaLabel={t("tableMgmt.importWizard.steps")}
        dataTestId="table-import-steps"
      />

      <RequiredFieldsNote />

      <div className="grid gap-3 lg:grid-cols-2">
        <div className={importFieldClass}>
          <FieldLabel
            htmlFor="table-import-table-name"
            label={t("dataTools.dbAdmin.tableName")}
            required
          />
          <input
            id="table-import-table-name"
            value={table}
            required
            aria-required="true"
            onChange={(event) => onTableChange(event.currentTarget.value)}
            className={`${importControlClass} py-2`}
            placeholder="IMPORTED_ORDERS"
          />
        </div>
        <div className={importFieldClass}>
          <FieldLabel
            htmlFor="table-import-sheet-name"
            label={t("dataTools.dbAdmin.sheet")}
            required={sheetRequired}
          />
          <input
            id="table-import-sheet-name"
            value={sheet}
            required={sheetRequired}
            aria-required={sheetRequired}
            onChange={(event) => onSheetChange(event.currentTarget.value)}
            className={`${importControlClass} py-2`}
            placeholder="Sheet1"
          />
        </div>
      </div>

      <FileDropzone
        label={t("dataTools.dbAdmin.file")}
        accept={CORE_TABULAR_FILE_FORMATS.accept}
        selectedText={filename ? t("tableMgmt.importWizard.selectedFile", { filename }) : ""}
        formatLabel={CORE_TABULAR_FILE_FORMATS.formatLabel}
        required
        actionText={t("common.fileDropzone.action")}
        replaceText={t("tableMgmt.importWizard.fileReplace")}
        clearAriaLabel={t("tableMgmt.importWizard.clearFile")}
        icon="spreadsheet"
        className="w-full"
        dataTestId="table-import-file-field"
        onFiles={([file]) => onFilePick(file)}
        onClear={onFileClear}
      />

      <fieldset
        className="grid gap-3 rounded-md border border-border bg-card p-3"
        data-testid="table-import-execution-fieldset"
      >
        <legend className="px-1 text-sm font-semibold text-foreground">{t("tableMgmt.importWizard.executeTitle")}</legend>
        <ExecutionConfirmationField
          value={confirmation}
          onChange={onConfirmationChange}
          confirmed={isConfirmed}
          placeholder="ADMIN_EXECUTE"
          expectedLabel="ADMIN_EXECUTE"
          helper={t("tableMgmt.importWizard.executeHint")}
          actions={
            <div className="flex min-w-0 flex-wrap items-center gap-2">
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
              <div className="w-full">
                {Boolean(error) && <DbAdminErrorNotice error={error} onReturnToList={onReturnToList} />}
              </div>
            </div>
          }
        />
        {result && <ImportResultPanel result={result} />}
        {schemaRefreshing && (
          <ProcessingIndicator
            active
            label={schemaRefreshProcessingLabel(schemaRefreshJob, t("common.processing.schemaRefreshing"))}
            operationKey={schemaRefreshJob?.job_id ?? "table-import-schema-refresh"}
            placement="job"
            className="rounded-md border border-border bg-background px-3 py-2"
            testId="table-import-schema-refresh-processing"
          />
        )}
        {schemaRefreshError && (
          <div
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger"
            role="alert"
          >
            <span>{schemaRefreshError}</span>
            {schemaRefreshNeedsFull && (
              <Button type="button" variant="secondary" size="sm" onClick={onSchemaRefresh}>
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("common.action.schemaRefresh")}</span>
              </Button>
            )}
          </div>
        )}
      </fieldset>
    </div>
  );
}

function schemaRefreshJobLabel(job: SchemaRefreshJob | null) {
  if (!job) return "";
  const phase = job.phase ?? (job.status === "pending" ? "queued" : job.status);
  const progress = job.total_objects ? ` ${job.processed_objects ?? 0}/${job.total_objects}` : "";
  return t(job.mode === "targeted" ? "dataMgmt.schemaJob.deltaProgress" : "dataMgmt.schemaJob.progress", {
    phase: t(`dataMgmt.schemaJob.phase.${phase}`),
    progress,
  });
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

function schemaRefreshProcessingLabel(job: SchemaRefreshJob | null, fullLabel: string) {
  return job?.mode === "targeted" ? t("common.processing.schemaDeltaSyncing") : fullLabel;
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

export function TableManagementPage() {
  const [detailTab, setDetailTab] = useState<DbObjectDetailTab>("columns");
  const [activeView, setActiveView] = useState<ActiveView>("list");
  const [tableSearch, setTableSearch] = useState("");
  const [tableOwnerFilter, setTableOwnerFilter] = useState<DbObjectOwnerFilter>("all");
  const [tableSort, setTableSort] = useState<DbObjectSortState>({ key: "name", direction: "asc" });
  const [dropTargetName, setDropTargetName] = useState("");
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [importTable, setImportTable] = useState("");
  const [importFilename, setImportFilename] = useState("");
  const [importBase64, setImportBase64] = useState("");
  const [importSheet, setImportSheet] = useState("");
  const [importStep, setImportStep] = useState<ImportStep>("file");
  const [importConfirmation, setImportConfirmation] = useState("");
  const [importResult, setImportResult] = useState<DbAdminImportTabularData | null>(null);
  const [importError, setImportError] = useState<unknown>(null);
  const [schemaRefreshJobId, setSchemaRefreshJobId] = useState("");
  const [schemaRefreshError, setSchemaRefreshError] = useState("");
  const [schemaRefreshNeedsFull, setSchemaRefreshNeedsFull] = useState(false);
  const [importSchemaRefreshJobId, setImportSchemaRefreshJobId] = useState("");
  const [importSchemaRefreshError, setImportSchemaRefreshError] = useState("");
  const [importSchemaRefreshNeedsFull, setImportSchemaRefreshNeedsFull] = useState(false);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const loadSequence = useRef(0);
  const completedSchemaRefreshJob = useRef("");
  const completedImportSchemaRefreshJob = useRef("");
  const { abortAll, run: runScopedRequest } = useRequestScope();
  const debouncedTableSearch = useDebouncedValue(tableSearch, 250);
  const tableOwnerQuery = tableOwnerFilter === "all" ? "" : tableOwnerFilter;
  const tableObjectsQuery = useDbAdminObjects(debouncedTableSearch, "table", "all", tableOwnerQuery);
  const schemaOwnersQuery = useSchemaOwners();
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshJob = schemaRefreshJobQuery.data ?? null;
  const schemaRefreshing =
    !schemaRefreshJobQuery.error &&
    (schemaRefreshJob?.status === "pending" || schemaRefreshJob?.status === "running");
  const visibleSchemaRefreshError = schemaRefreshJobQuery.error
    ? schemaRefreshJobQuery.error instanceof Error
      ? schemaRefreshJobQuery.error.message
      : t("dataMgmt.schemaJob.error")
    : schemaRefreshError;
  const importSchemaRefreshJobQuery = useSchemaRefreshJob(importSchemaRefreshJobId);
  const importSchemaRefreshJob = importSchemaRefreshJobQuery.data ?? null;
  const importSchemaRefreshing =
    !importSchemaRefreshJobQuery.error &&
    (importSchemaRefreshJob?.status === "pending" || importSchemaRefreshJob?.status === "running");
  const visibleImportSchemaRefreshError = importSchemaRefreshJobQuery.error
    ? importSchemaRefreshJobQuery.error instanceof Error
      ? importSchemaRefreshJobQuery.error.message
      : t("dataMgmt.schemaJob.error")
    : importSchemaRefreshError;
  const tableItems = useMemo(
    () => (tableObjectsQuery.data?.pages ?? []).flatMap((page) => page.items),
    [tableObjectsQuery.data]
  );
  const firstTablePage = tableObjectsQuery.data?.pages[0];
  const totalTableCount = dbAdminObjectCountsFromPage(firstTablePage, tableItems).totalCount;
  const tableOwnerOptions = useMemo(
    () =>
      (schemaOwnersQuery.data?.owners ?? [])
        .filter((item) => item.table_count > 0)
        .map((item) => item.owner.trim())
        .filter(Boolean)
        .sort((left, right) => left.localeCompare(right, "ja")),
    [schemaOwnersQuery.data],
  );
  const detailRequest = useDbObjectDetailRequest({
    collectionPath: "/api/nl2sql/db-admin/tables",
    loadErrorMessage: t("tableMgmt.error.detail"),
    timeoutErrorMessage: t("dbAdmin.detail.timeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveDetail),
    }),
  });
  const {
    selectedName: selectedTableName,
    detail,
    setDetail,
  } = detailRequest;

  const fetchDetail = async (name: string) => {
    setDetailTab("columns");
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

  // 行数バッジは既定で num_rows 統計(一覧と統一・高速)。正確な件数は明示操作で COUNT(*)。
  const handleExactCount = async (name: string) => {
    setLoading("count");
    try {
      const target = parseDbAdminObjectTarget(name);
      const params = new URLSearchParams({ include_ddl: "0", exact_count: "1" });
      if (target.owner) params.set("owner", target.owner);
      const full = await apiGet<DbAdminObjectDetail>(
        `/api/nl2sql/db-admin/tables/${encodeURIComponent(target.name)}?${params.toString()}`,
      );
      setDetail((current) =>
        current && dbAdminObjectQualifiedName(current) === target.qualifiedName
          ? { ...current, row_count: full.row_count }
          : current,
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.exactCount"));
    } finally {
      setLoading("");
    }
  };

  const refreshObjects = async (announce = false) => {
    setMessage("");
    const result = await tableObjectsQuery.refetch();
    if (result.error) {
      setMessage(result.error instanceof Error ? result.error.message : t("tableMgmt.error.load"));
      return;
    }
    if (announce) {
      toast.success(t("common.action.refreshed"));
    }
  };

  const refreshSchema = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading("schema-refresh");
    setMessage("");
    setSchemaRefreshError("");
    setSchemaRefreshNeedsFull(false);
    try {
      await runScopedRequest(async (signal) => {
        // 列サンプル値は詳細 API が返すため catalog 全取得はしない。schema-refresh 時のみ
        // サーバ側 catalog を再構築してから一覧(refreshed_at を含む)を取り直す。
        const job = await apiPost<SchemaRefreshJob>("/api/schema/refresh-jobs", undefined, {
          signal,
          timeoutMs: API_TIMEOUT_MS.jobControl,
        });
        if (job.job_id) {
          completedSchemaRefreshJob.current = "";
          setSchemaRefreshJobId(job.job_id);
          const completedJob = await waitForSchemaRefreshJob(job.job_id, signal, {
            maxWaitMs: API_TIMEOUT_MS.interactiveDetail,
          });
          if (completedJob.status === "done") {
            completedSchemaRefreshJob.current = `${completedJob.job_id}:${completedJob.status}`;
          }
        }
        if (signal.aborted || sequence !== loadSequence.current) return;
      });
      if (sequence !== loadSequence.current) return;
      const result = await tableObjectsQuery.refetch();
      if (result.error) throw result.error;
      if (announce && sequence === loadSequence.current) {
        toast.success(t("common.action.schemaRefreshed"));
      }
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(
        isTimeoutError(err)
          ? t("dataMgmt.schemaJob.timeout")
          : err instanceof Error
            ? err.message
            : t("tableMgmt.error.load")
      );
    } finally {
      if (sequence === loadSequence.current) setLoading("");
    }
  };

  useEffect(() => {
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, []);

  useEffect(() => {
    const job = schemaRefreshJobQuery.data;
    if (!job) return;
    const reportKey = `${job.job_id}:${job.status}`;
    if (completedSchemaRefreshJob.current === reportKey) return;
    if (job.status === "done") {
      completedSchemaRefreshJob.current = reportKey;
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      toast.success(t("common.action.schemaRefreshed"));
      void refreshObjects();
    } else if (job.status === "error") {
      completedSchemaRefreshJob.current = reportKey;
      const needsFull = schemaRefreshRequiresFull(job);
      setSchemaRefreshNeedsFull(needsFull);
      setSchemaRefreshError(schemaRefreshErrorMessage(job));
      toast.error(needsFull ? schemaRefreshRequiredMessage(job.error_code) : t("dataMgmt.schemaJob.error"));
    }
  }, [schemaRefreshJobQuery.data]);

  useEffect(() => {
    if (!schemaRefreshJobQuery.error || !schemaRefreshJobId) return;
    const reportKey = `${schemaRefreshJobId}:query-error`;
    if (completedSchemaRefreshJob.current === reportKey) return;
    completedSchemaRefreshJob.current = reportKey;
    const error =
      schemaRefreshJobQuery.error instanceof Error
        ? schemaRefreshJobQuery.error.message
        : t("dataMgmt.schemaJob.error");
    setSchemaRefreshError(error);
    setSchemaRefreshNeedsFull(false);
    toast.error(t("dataMgmt.schemaJob.error"));
  }, [schemaRefreshJobId, schemaRefreshJobQuery.error]);

  useEffect(() => {
    const job = importSchemaRefreshJobQuery.data;
    if (!job) return;
    const reportKey = `${job.job_id}:${job.status}`;
    if (completedImportSchemaRefreshJob.current === reportKey) return;
    if (job.status === "done") {
      completedImportSchemaRefreshJob.current = reportKey;
      setImportSchemaRefreshError("");
      setImportSchemaRefreshNeedsFull(false);
      toast.success(t("common.action.schemaRefreshed"));
      void refreshObjects();
    } else if (job.status === "error") {
      completedImportSchemaRefreshJob.current = reportKey;
      const needsFull = schemaRefreshRequiresFull(job);
      setImportSchemaRefreshNeedsFull(needsFull);
      setImportSchemaRefreshError(schemaRefreshErrorMessage(job));
      toast.error(needsFull ? schemaRefreshRequiredMessage(job.error_code) : t("dataMgmt.schemaJob.error"));
    }
  }, [importSchemaRefreshJobQuery.data]);

  useEffect(() => {
    if (!importSchemaRefreshJobQuery.error || !importSchemaRefreshJobId) return;
    const reportKey = `${importSchemaRefreshJobId}:query-error`;
    if (completedImportSchemaRefreshJob.current === reportKey) return;
    completedImportSchemaRefreshJob.current = reportKey;
    const error =
      importSchemaRefreshJobQuery.error instanceof Error
        ? importSchemaRefreshJobQuery.error.message
        : t("dataMgmt.schemaJob.error");
    setImportSchemaRefreshError(error);
    setImportSchemaRefreshNeedsFull(false);
    toast.error(t("dataMgmt.schemaJob.error"));
  }, [importSchemaRefreshJobId, importSchemaRefreshJobQuery.error]);

  useEffect(() => {
    if (activeView !== "list") return;
    if (tableObjectsQuery.isPending || detailRequest.loading) return;
    if (tableObjectsQuery.error && !tableObjectsQuery.data) return;
    if (tableItems.length === 0) {
      if (selectedTableName) detailRequest.clear();
      return;
    }
    if (selectedTableName && tableItems.some((item) => dbAdminObjectQualifiedName(item) === selectedTableName)) return;
    void fetchDetail(dbAdminObjectQualifiedName(tableItems[0]));
  }, [
    activeView,
    tableItems,
    tableObjectsQuery.isPending,
    tableObjectsQuery.error,
    tableObjectsQuery.data,
    selectedTableName,
    detailRequest.loading,
  ]);

  const trackSchemaRefreshJob = (jobId: string) => {
    if (activeView === "import") {
      completedImportSchemaRefreshJob.current = "";
      setImportSchemaRefreshError("");
      setImportSchemaRefreshNeedsFull(false);
      setImportSchemaRefreshJobId(jobId);
      return;
    }
    completedSchemaRefreshJob.current = "";
    setSchemaRefreshError("");
    setSchemaRefreshNeedsFull(false);
    setSchemaRefreshJobId(jobId);
  };

  const reloadAfterMutation = (result: {
    schema_refresh_job_id?: string;
    schema_refresh_required?: boolean;
    schema_refresh_reason_code?: string;
  }) => {
    if (result.schema_refresh_job_id) {
      trackSchemaRefreshJob(result.schema_refresh_job_id);
    } else if (result.schema_refresh_required) {
      const messageText = schemaRefreshRequiredMessage(result.schema_refresh_reason_code);
      if (activeView === "import") {
        setImportSchemaRefreshError(messageText);
        setImportSchemaRefreshNeedsFull(true);
      } else {
        setSchemaRefreshError(messageText);
        setSchemaRefreshNeedsFull(true);
      }
    }
    void refreshObjects();
  };

  const filteredTables = useMemo(() => {
    const q = tableSearch.trim().toLowerCase();
    const ownerKey = tableOwnerFilter.trim().toUpperCase();
    return tableItems
      .filter((item) => {
        if (ownerKey && ownerKey !== "ALL" && item.owner.toUpperCase() !== ownerKey) return false;
        if (!q) return true;
        return (
          dbAdminObjectQualifiedName(item).toLowerCase().includes(q) ||
          item.name.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q) ||
          item.owner.toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = dbObjectSortValue(left, tableSort.key);
        const b = dbObjectSortValue(right, tableSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return tableSort.direction === "asc" ? result : -result;
      });
  }, [tableItems, tableOwnerFilter, tableSearch, tableSort]);

  const toggleSort = (key: DbObjectSortKey) => {
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
    setImportError(null);
    setImportSchemaRefreshJobId("");
    setImportSchemaRefreshError("");
    setImportSchemaRefreshNeedsFull(false);
    completedImportSchemaRefreshJob.current = "";
    setImportStep("file");
  };

  const clearImportFile = () => {
    setImportFilename("");
    setImportBase64("");
    setImportResult(null);
    setImportError(null);
    setImportSchemaRefreshJobId("");
    setImportSchemaRefreshError("");
    setImportSchemaRefreshNeedsFull(false);
    completedImportSchemaRefreshJob.current = "";
    setImportStep("file");
  };

  const importTabular = async () => {
    if (!importTable.trim() || !importBase64) return;
    setLoading("import-tabular");
    setMessage("");
    setImportError(null);
    setImportResult(null);
    setImportSchemaRefreshJobId("");
    setImportSchemaRefreshError("");
    completedImportSchemaRefreshJob.current = "";
    try {
      const result = await apiPost<DbAdminImportTabularData>("/api/nl2sql/db-admin/import-tabular", {
        table_name: importTable,
        content_base64: importBase64,
        filename: importFilename || "upload.csv",
        sheet_name: importSheet,
        mode: "create",
        confirmation: importConfirmation,
        reason: "ui-table-management-import-tabular",
      });
      setImportResult(result);
      setImportStep("execute");
      if (result.executed) {
        if (result.schema_refresh_job_id) {
          setImportSchemaRefreshJobId(result.schema_refresh_job_id);
          setImportSchemaRefreshNeedsFull(false);
        } else if (result.schema_refresh_required) {
          setImportSchemaRefreshError(schemaRefreshRequiredMessage(result.schema_refresh_reason_code));
          setImportSchemaRefreshNeedsFull(true);
        } else {
          void refreshObjects();
        }
      }
    } catch (err) {
      setImportError(err instanceof Error ? err : new Error(t("dataTools.error.import")));
    } finally {
      setLoading("");
    }
  };

  const downloadColumnsXlsx = async (name: string) => {
    const target = parseDbAdminObjectTarget(name);
    const params = new URLSearchParams();
    if (target.owner) params.set("owner", target.owner);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    setLoading("table-export");
    setMessage("");
    try {
      const response = await apiFetch(`/api/nl2sql/db-admin/tables/${encodeURIComponent(target.name)}/export.xlsx${suffix}`, {
        headers: {
          Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
      });
      if (!response.ok) {
        throw new Error(t("tableMgmt.error.export"));
      }
      downloadBlob(`${target.qualifiedName.toLowerCase().replace(".", "_")}_columns.xlsx`, await response.blob());
      toast.success(t("common.action.downloaded"));
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
        table_name: parseDbAdminObjectTarget(dropTargetName).name,
        owner: parseDbAdminObjectTarget(dropTargetName).owner,
        confirmation: dropConfirmation,
        reason: "ui-table-management-drop",
      });
      if (result.executed) {
        const dropped = dropTargetName;
        setDropTargetName("");
        setDropConfirmation("");
        toast.success(t("tableMgmt.drop.success", { name: dropped }));
        await reloadAfterMutation(result);
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
        title={t("tableMgmt.create.title")}
        description={t("tableMgmt.create.note")}
        placeholder={t("tableMgmt.create.placeholder")}
        progress={({ hasSql }) => (
          <DbObjectStepIndicator
            steps={[t("tableMgmt.create.stepSql"), t("tableMgmt.create.stepExecute")]}
            activeIndex={hasSql ? 1 : 0}
            ariaLabel={t("tableMgmt.create.steps")}
            dataTestId="table-create-steps"
          />
        )}
        confirmationTitle={t("tableMgmt.create.executeTitle")}
        executeOnly
        framed={false}
        onExecuted={reloadAfterMutation}
      />
    ) : activeView === "import" ? (
      <ImportWizard
        table={importTable}
        sheet={importSheet}
        filename={importFilename}
        fileReady={Boolean(importBase64)}
        result={importResult}
        step={importStep}
        confirmation={importConfirmation}
        loading={loading === "import-tabular"}
        error={importError}
        schemaRefreshJob={importSchemaRefreshJob}
        schemaRefreshing={importSchemaRefreshing}
        schemaRefreshError={visibleImportSchemaRefreshError}
        schemaRefreshNeedsFull={importSchemaRefreshNeedsFull}
        onTableChange={(value) => {
          setImportTable(value);
          setImportResult(null);
          setImportError(null);
          setImportSchemaRefreshJobId("");
          setImportSchemaRefreshError("");
          setImportSchemaRefreshNeedsFull(false);
          completedImportSchemaRefreshJob.current = "";
          setImportStep("file");
        }}
        onSheetChange={(value) => {
          setImportSheet(value);
          setImportResult(null);
          setImportError(null);
          setImportSchemaRefreshJobId("");
          setImportSchemaRefreshError("");
          setImportSchemaRefreshNeedsFull(false);
          completedImportSchemaRefreshJob.current = "";
          setImportStep("file");
        }}
        onFilePick={(file) => void pickImportFile(file)}
        onFileClear={clearImportFile}
        onExecute={() => void importTabular()}
        onConfirmationChange={(value) => {
          setImportConfirmation(value);
          setImportError(null);
          if (value.trim()) setImportStep("execute");
        }}
        onReturnToList={returnToList}
        onSchemaRefresh={() => {
          setImportSchemaRefreshError("");
          setImportSchemaRefreshNeedsFull(false);
          void refreshSchema(true);
        }}
      />
    ) : null;

  return (
    <>
      <PageHeader
        title={t("nav.tableManagement")}
        subtitle={t("tableMgmt.subtitle")}
        meta={
          firstTablePage?.refreshed_at
            ? t("common.schemaRefreshedAt", { date: formatDateTime(firstTablePage.refreshed_at) })
            : undefined
        }
        status={
          schemaRefreshJob && schemaRefreshJob.status !== "done" ? (
            <PageHeaderStatusBadge
              variant={schemaRefreshJob.status === "error" ? "danger" : "info"}
              label={schemaRefreshJobLabel(schemaRefreshJob)}
            />
          ) : undefined
        }
        actionsAriaLabel={t("tableMgmt.tabs.label")}
        actionsTestId="table-management-actions"
        actions={
          activeView === "list"
            ? [
                {
                  id: "create-table",
                  kind: "primary",
                  label: t("tableMgmt.create.title"),
                  icon: Code2,
                  onClick: () => setActiveView("create"),
                },
                {
                  id: "import-table",
                  kind: "secondary",
                  label: t("dataTools.dbAdmin.importTitle"),
                  icon: Upload,
                  onClick: () => setActiveView("import"),
                },
                {
                  id: "refresh-table-list",
                  kind: "utility",
                  label: t("common.action.refresh"),
                  icon: RefreshCw,
                  loading: tableObjectsQuery.isFetching && !tableObjectsQuery.isFetchingNextPage,
                  onClick: () => void refreshObjects(true),
                },
                {
                  id: "refresh-table-schema",
                  kind: "utility",
                  label: t("common.action.schemaRefresh"),
                  icon: RefreshCw,
                  loading: loading === "schema-refresh" || schemaRefreshing,
                  disabled: loading === "schema-refresh" || schemaRefreshing,
                  onClick: () => void refreshSchema(true),
                },
              ]
            : []
        }
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={
            message
              ? { tone: "danger", message: `${message} ${t("tableMgmt.error.retryHint")}` }
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
                schemaRefreshNeedsFull
                  ? () => void refreshSchema(true)
                  : () => void refreshObjects()
              }
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>
                {schemaRefreshNeedsFull
                  ? t("common.action.schemaRefresh")
                  : t("tableMgmt.action.refresh")}
              </span>
            </Button>
          }
        />
        {activeView === "list" ? (
          <>
            <DbObjectManagementPanelShell
              id="table-management-panel-list"
              role="region"
              idPrefix="table-management"
              ariaLabel={t("tableMgmt.workspace.label")}
              splitId="table-management-list"
              preferredWidePane="right"
              processing={
                tableObjectsQuery.data &&
                ((tableObjectsQuery.isFetching && !tableObjectsQuery.isFetchingNextPage) ||
                  loading === "schema-refresh" ||
                  schemaRefreshing) ? (
                  <ProcessingIndicator
                    active
                    label={
                      loading === "schema-refresh" || schemaRefreshing
                        ? schemaRefreshProcessingLabel(
                            schemaRefreshJob,
                            t("tableMgmt.workspace.schemaRefreshing"),
                          )
                        : t("tableMgmt.workspace.refreshing")
                    }
                    operationKey={schemaRefreshing ? schemaRefreshJobId : loading}
                    placement="workspace"
                    className="rounded-md border border-border bg-background px-3 py-2"
                    testId="table-management-workspace-processing"
                    activityIcon="none"
                  />
                ) : undefined
              }
            >
            <DbObjectGrid
              idPrefix="table-management"
              headingId="table-grid-heading"
              icon={Table2}
              items={filteredTables}
              selectedName={selectedTableName}
              loading={tableObjectsQuery.isPending && !tableObjectsQuery.data}
              error={
                tableObjectsQuery.error && !tableObjectsQuery.data
                  ? objectListErrorMessage(tableObjectsQuery.error, "tableMgmt.error.load")
                  : ""
              }
              search={tableSearch}
              ownerFilter={tableOwnerFilter}
              ownerOptions={tableOwnerOptions}
              sort={tableSort}
              totalCount={totalTableCount}
              hasNextPage={Boolean(tableObjectsQuery.hasNextPage)}
              loadingNextPage={tableObjectsQuery.isFetchingNextPage}
              loadMoreError={
                tableObjectsQuery.isFetchNextPageError && tableObjectsQuery.error
                  ? objectListLoadMoreErrorMessage(tableObjectsQuery.error, "tableMgmt.error.load")
                  : ""
              }
              labels={{
                title: t("tableMgmt.list.title"),
                hint: t("tableMgmt.grid.hint"),
                count: t("tableMgmt.grid.count", { count: totalTableCount }),
                loading: t("tableMgmt.list.loading"),
                emptyTitle: t("tableMgmt.list.emptyTitle"),
                emptyHint: t("tableMgmt.list.emptyHint"),
                noResultsTitle: t("tableMgmt.list.noResultsTitle"),
                noResultsHint: t("tableMgmt.list.noResultsHint"),
                ownerFilter: t("tableMgmt.toolbar.filter"),
                ownerFilterAll: t("tableMgmt.toolbar.filterAll"),
                objectName: t("tableMgmt.grid.tableName"),
                rows: t("tableMgmt.grid.rows"),
                owner: t("tableMgmt.grid.owner"),
                actions: t("tableMgmt.grid.actions"),
                detail: t("tableMgmt.grid.detail"),
                drop: t("tableMgmt.grid.drop"),
                showObject: (name) => t("tableMgmt.grid.showTable", { name }),
              }}
              onSearchChange={setTableSearch}
              onOwnerFilterChange={setTableOwnerFilter}
              onSortChange={toggleSort}
              onSelect={(name) => void fetchDetail(name)}
              onDrop={openDropDialog}
              onLoadMore={() => void tableObjectsQuery.fetchNextPage()}
              onRetryLoadMore={() => void tableObjectsQuery.fetchNextPage()}
              onRetry={() => void refreshObjects()}
            />
            <DbObjectDetailPanel
              idPrefix="table-management"
              operationKey={selectedTableName}
              headingId="table-detail-heading"
              detail={detail}
              loading={detailRequest.loading || (tableObjectsQuery.isPending && !tableObjectsQuery.data)}
              ddlLoading={detailRequest.ddlLoading}
              error={detailRequest.error}
              ddlError={detailRequest.ddlError}
              exporting={loading === "table-export"}
              countingRows={loading === "count"}
              tab={detailTab}
              labels={{
                actions: t("tableMgmt.grid.actions"),
                loading: t("tableMgmt.detail.loading"),
                ddlLoading: t("tableMgmt.detail.ddlLoading"),
                tabsLabel: t("tableMgmt.detailTabs.label"),
                columns: t("tableMgmt.detailTabs.columns"),
                ddl: t("tableMgmt.detailTabs.ddl"),
                export: t("tableMgmt.export"),
                exportAria: t("tableMgmt.exportColumns"),
                exactCount: t("tableMgmt.exactCount"),
                exactCountAria: t("tableMgmt.exactCountAria"),
                drop: t("tableMgmt.grid.drop"),
              }}
              onTabChange={handleDetailTabChange}
              onRetry={() => void fetchDetail(selectedTableName)}
              onRetryDdl={() => {
                if (detail) void detailRequest.loadDdl(dbAdminObjectQualifiedName(detail));
              }}
              onCancel={detailRequest.cancel}
              onExport={(name) => void downloadColumnsXlsx(name)}
              onExactCount={(name) => void handleExactCount(name)}
              onDrop={openDropDialog}
            />
            </DbObjectManagementPanelShell>
          </>
        ) : (
          <>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={returnToList}>
                <ArrowLeft size={15} aria-hidden="true" />
                <span>{t("tableMgmt.action.backToList")}</span>
              </Button>
            </div>
            <DbObjectManagementPanelShell
              id={`table-management-panel-${activeView}`}
              role="region"
              idPrefix="table-management"
              ariaLabel={t("tableMgmt.toolbar.taskPanel")}
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
            title: t("tableMgmt.dropDialog.title"),
            subtitle: t("tableMgmt.dropDialog.subtitle"),
            close: t("tableMgmt.dropDialog.close"),
            target: t("tableMgmt.dropDialog.target"),
            executeTitle: t("tableMgmt.dropDialog.executeTitle"),
            executeHint: t("tableMgmt.dropDialog.executeHint"),
            cancel: t("tableMgmt.dropDialog.cancel"),
            run: t("tableMgmt.drop.run"),
          }}
          onConfirmationChange={setDropConfirmation}
          onExecute={() => void dropTable()}
          onClose={() => setDropTargetName("")}
        />
      )}
    </>
  );
}
