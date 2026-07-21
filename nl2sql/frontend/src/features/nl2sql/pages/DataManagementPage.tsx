import { useEffect, useMemo, useRef, useState } from "react";
import { Code2, Database, Eye, FileSpreadsheet, RefreshCw, Search, Table2, Upload } from "lucide-react";

import { Button, EmptyState, PageHeader, StatusBadge, toast } from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiFetch, apiGet, apiPost, isAbortError } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  ExecutionConfirmationField,
  FileInputControl,
  QueryResultsTable,
  StatementRunnerCard,
  downloadBlob,
  fileToBase64,
} from "../components/DbAdminShared";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
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
import type {
  DbAdminCsvUploadData,
  DbAdminDataPreviewData,
  DbAdminObjectsData,
  SchemaCatalog,
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
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [selectAiDbProfiles, setSelectAiDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [dbAdminTables, setDbAdminTables] = useState<DbAdminObjectsData | null>(null);
  const [dbAdminViews, setDbAdminViews] = useState<DbAdminObjectsData | null>(null);
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
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const previewObjects = useMemo<PreviewObject[]>(() => {
    const tables = (dbAdminTables?.items ?? []).map((item) => ({
      name: item.name,
      kind: "table" as const,
      owner: item.owner,
      rowCount: item.row_count,
      comment: item.comment,
    }));
    const views = (dbAdminViews?.items ?? []).map((item) => ({
      name: item.name,
      kind: "view" as const,
      owner: item.owner,
      rowCount: item.row_count,
      comment: item.comment,
    }));
    return [...tables, ...views];
  }, [dbAdminTables, dbAdminViews]);

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
  const csvConfirmed = csvConfirmation.trim() === "ADMIN_EXECUTE";
  const canUploadCsv = Boolean(csvTable && csvBase64 && csvConfirmed);
  const syntheticDataConfirmed = syntheticConfirmation.trim() === "ADMIN_EXECUTE";
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

  const load = async (refreshSchema = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(refreshSchema ? "schema-refresh" : "load");
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        const [catalogData, profileData, adminTables, adminViews] = await Promise.all([
          refreshSchema
            ? apiPost<SchemaCatalog>("/api/schema/refresh", undefined, { signal })
            : apiGet<SchemaCatalog>("/api/schema/catalog", { signal }),
          apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL, { signal }),
          apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables", { signal }),
          apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views", { signal }),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        const nextSyntheticProfileName = resolveBusinessSelectAiProfileName(
          syntheticProfileName,
          profileData.profiles
        );
        setCatalog(catalogData);
        setSelectAiDbProfiles(profileData);
        setDbAdminTables(adminTables);
        setDbAdminViews(adminViews);
        setSyntheticProfileName(nextSyntheticProfileName);
        if (syntheticProfileName && syntheticProfileName !== nextSyntheticProfileName) {
          clearSyntheticProfileTargets();
        }
        setPreviewObject(
          (current) => current || adminTables.items[0]?.name || adminViews.items[0]?.name || ""
        );
        setCsvTable((current) => current || adminTables.items[0]?.name || "");
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("dataTools.error.load"));
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

  const showPreview = async () => {
    if (!previewObject) return;
    setLoading("preview");
    setMessage("");
    try {
      setPreview(
        await apiPost<DbAdminDataPreviewData>("/api/nl2sql/db-admin/preview-data", {
          object_name: previewObject,
          limit: previewLimit,
          where_clause: previewWhere,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataMgmt.error.preview"));
    } finally {
      setLoading("");
    }
  };

  const downloadPreviewXlsx = async () => {
    if (!previewObject) return;
    setLoading("preview-export");
    setMessage("");
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
      });
      if (!response.ok) {
        throw new Error(await previewExportError(response));
      }
      const filename = `${previewObject.toLowerCase()}_preview.xlsx`;
      downloadBlob(filename, await response.blob());
      toast.success(t("common.action.downloaded"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataMgmt.error.previewExport"));
    } finally {
      setLoading("");
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
    setLoading("csv-upload");
    setMessage("");
    try {
      const result = await apiPost<DbAdminCsvUploadData>("/api/nl2sql/db-admin/upload-csv", {
        table_name: csvTable,
        content_base64: csvBase64,
        filename: csvFilename || "upload.csv",
        mode: csvMode,
        confirmation: csvConfirmation,
        reason: "ui-data-management-csv",
      });
      setCsvUploadResult(result);
      setCsvStep("execute");
      if (result.executed) {
        await load(true);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataMgmt.error.csvUpload"));
    } finally {
      setLoading("");
    }
  };

  const refreshSyntheticTables = async () => {
    const profileName = syntheticProfileName.trim();
    if (!profileName) return;
    setLoading("synthetic-tables");
    setMessage("");
    try {
      let profile = selectedSyntheticProfile;
      try {
        const detail = await apiGet<SelectAiDbProfileDetailData>(
          `/api/nl2sql/select-ai/db-profiles/${encodeURIComponent(profileName)}`
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
      setMessage(err instanceof Error ? err.message : t("dataTools.error.load"));
    } finally {
      setLoading("");
    }
  };

  const generateSyntheticData = async () => {
    if (!canGenerateSyntheticData) return;
    const selectedTables = syntheticSelectedTables;
    const singleTable = selectedTables.length === 1;
    setLoading("synthetic-data");
    setMessage("");
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
      });
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
      setMessage(err instanceof Error ? err.message : t("dataTools.error.syntheticData"));
    } finally {
      setLoading("");
    }
  };

  const checkSyntheticDataStatus = async () => {
    const operationId = syntheticData?.operation_id.trim();
    if (!operationId) return;
    setLoading("synthetic-status");
    setMessage("");
    try {
      setSyntheticDataStatus(await fetchSyntheticDataStatus(operationId));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.syntheticStatus"));
    } finally {
      setLoading("");
    }
  };

  const loadSyntheticDataResults = async () => {
    const tableName = syntheticResultTable.trim();
    if (!tableName || !syntheticAvailableTables.includes(tableName)) return;
    setLoading("synthetic-results");
    setMessage("");
    try {
      setSyntheticDataResults(
        await apiGet<SyntheticDataResultsData>(
          `/api/nl2sql/synthetic-data/results?table_name=${encodeURIComponent(tableName)}&limit=${syntheticResultLimit}`
        )
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.syntheticResults"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader title={t("nav.dataManagement")} subtitle={t("dataMgmt.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={message ? { tone: "danger", message: `${message} ${t("tableMgmt.error.retryHint")}` } : null}
          action={
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("tableMgmt.action.refresh")}</span>
            </Button>
          }
        />

        <DataStatusBar
          tableCount={dbAdminTables?.items.length ?? catalog?.tables.length ?? 0}
          viewCount={dbAdminViews?.items.length ?? 0}
          profileCount={selectAiDbProfiles?.profiles.length ?? 0}
          runtime={dbAdminTables?.runtime ?? dbAdminViews?.runtime ?? "deterministic"}
          refreshedAt={catalog?.refreshed_at ?? ""}
          loading={loading}
          onRefresh={() => void load()}
          onSchemaRefresh={() => void load(true)}
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
              loading={loading}
              onPreviewObjectChange={(value) => {
                setPreviewObject(value);
                setPreview(null);
              }}
              onPreviewObjectSearchChange={setPreviewObjectSearch}
              onPreviewObjectKindFilterChange={setPreviewObjectKindFilter}
              onPreviewObjectRowFilterChange={setPreviewObjectRowFilter}
              onPreviewLimitChange={setPreviewLimit}
              onPreviewWhereChange={setPreviewWhere}
              onShowPreview={() => void showPreview()}
            />
            <PreviewResultsPanel
              preview={preview}
              loading={loading}
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
              tables={dbAdminTables?.items ?? []}
              table={csvTable}
              filename={csvFilename}
              fileReady={Boolean(csvBase64)}
              mode={csvMode}
              step={csvStep}
              confirmation={csvConfirmation}
              confirmed={csvConfirmed}
              canUpload={canUploadCsv}
              result={csvUploadResult}
              loading={loading === "csv-upload"}
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
              onExecuted={() => load(true)}
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
              loading={loading}
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
            />
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}

function DataStatusBar({
  tableCount,
  viewCount,
  profileCount,
  runtime,
  refreshedAt,
  loading,
  onRefresh,
  onSchemaRefresh,
}: {
  tableCount: number;
  viewCount: number;
  profileCount: number;
  runtime: string;
  refreshedAt: string;
  loading: string;
  onRefresh: () => void;
  onSchemaRefresh: () => void;
}) {
  return (
    <DbObjectManagementStatusBar
      ariaLabel={t("dataMgmt.toolbar.status")}
      metricColumnsClass="sm:grid-cols-2 lg:grid-cols-5"
      metrics={[
        { label: t("tableMgmt.metric.tables"), value: formatNumber(tableCount), emphasis: true },
        { label: t("dataMgmt.metric.views"), value: formatNumber(viewCount), emphasis: true },
        { label: t("dataTools.metric.profiles"), value: formatNumber(profileCount), emphasis: true },
        { label: t("tableMgmt.metric.runtime"), value: runtime },
        { label: t("tableMgmt.metric.schemaRefreshed"), value: formatDateTime(refreshedAt) },
      ]}
      actions={
        <>
          <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={onRefresh}>
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
        </>
      }
    />
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
  loading,
  onPreviewObjectChange,
  onPreviewObjectSearchChange,
  onPreviewObjectKindFilterChange,
  onPreviewObjectRowFilterChange,
  onPreviewLimitChange,
  onPreviewWhereChange,
  onShowPreview,
}: {
  previewObjects: PreviewObject[];
  filteredPreviewObjects: PreviewObject[];
  previewObject: string;
  previewObjectSearch: string;
  previewObjectKindFilter: PreviewObjectKindFilter;
  previewObjectRowFilter: PreviewObjectRowFilter;
  previewLimit: number;
  previewWhere: string;
  loading: string;
  onPreviewObjectChange: (value: string) => void;
  onPreviewObjectSearchChange: (value: string) => void;
  onPreviewObjectKindFilterChange: (value: PreviewObjectKindFilter) => void;
  onPreviewObjectRowFilterChange: (value: PreviewObjectRowFilter) => void;
  onPreviewLimitChange: (value: number) => void;
  onPreviewWhereChange: (value: string) => void;
  onShowPreview: () => void;
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
            <Button
              type="button"
              variant="primary"
              size="sm"
              loading={loading === "preview"}
              disabled={!previewObject}
              onClick={onShowPreview}
            >
              <Eye size={15} aria-hidden="true" />
              <span>{t("dataMgmt.preview.show")}</span>
            </Button>
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
            onSelect={onPreviewObjectChange}
          />
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
  onSelect,
}: {
  objects: PreviewObject[];
  selectedObject: string;
  hasActiveFilter: boolean;
  onSelect: (value: string) => void;
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
        className="hidden grid-cols-[minmax(0,1.5fr)_5.75rem_5.75rem_minmax(4.5rem,0.75fr)] gap-2 border-b border-border bg-background px-3 py-2 text-xs font-semibold text-muted md:grid"
        aria-hidden="true"
      >
        <span>{t("dataMgmt.preview.objectName")}</span>
        <span>{t("dataMgmt.preview.objectKind")}</span>
        <span>{t("dataMgmt.preview.objectRows")}</span>
        <span>{t("dataMgmt.preview.objectOwner")}</span>
      </div>
      <div className="max-h-80 overflow-auto" role="list" aria-label={t("dataMgmt.preview.object")}>
        {objects.map((item) => {
          const selected = item.name === selectedObject;
          return (
            <button
              key={`${item.kind}-${item.name}`}
              type="button"
              aria-current={selected ? "true" : undefined}
              aria-label={t("dataMgmt.preview.selectObject", { name: item.name })}
              className={[
                "grid w-full min-w-0 gap-2 border-b border-border px-3 py-3 text-left text-sm transition-colors last:border-b-0 hover:bg-background focus:outline-none focus:ring-2 focus:ring-inset focus:ring-ring/40",
                "md:grid-cols-[minmax(0,1.5fr)_5.75rem_5.75rem_minmax(4.5rem,0.75fr)] md:items-center md:py-2",
                selected ? "bg-primary/10" : "bg-card",
              ].join(" ")}
              onClick={() => onSelect(item.name)}
            >
              <span className="min-w-0">
                <span className="break-all font-mono text-xs font-semibold text-primary">{item.name}</span>
                {item.comment && <span className="mt-1 block break-words text-xs text-muted md:hidden">{item.comment}</span>}
              </span>
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
            </button>
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
  onDownload,
}: {
  preview: DbAdminDataPreviewData | null;
  loading: string;
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
            loading={loading === "preview-export"}
            disabled={!preview || preview.results.rows.length === 0}
            onClick={onDownload}
          >
            <FileSpreadsheet size={15} aria-hidden="true" />
            <span>{t("dataMgmt.preview.exportXlsx")}</span>
          </Button>
        }
      />
      {preview ? (
        <div className="grid gap-2">
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
        <EmptyState title={t("dataMgmt.preview.emptyTitle")} hint={t("dataMgmt.preview.emptyHint")} />
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
  onTableChange,
  onModeChange,
  onFilePick,
  onFileClear,
  onConfirmationChange,
  onUpload,
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
  onTableChange: (value: string) => void;
  onModeChange: (value: CsvMode) => void;
  onFilePick: (file: File) => void;
  onFileClear: () => void;
  onConfirmationChange: (value: string) => void;
  onUpload: () => void;
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
        <label className="grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground">
          <span>{t("dataMgmt.csv.table")}</span>
          <select
            value={table}
            onChange={(event) => onTableChange(event.currentTarget.value)}
            className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
          >
            {tables.length === 0 && <option value="">{t("tableMgmt.list.emptyTitle")}</option>}
            {tables.map((item) => (
              <option key={item.name} value={item.name}>{item.name}</option>
            ))}
          </select>
        </label>
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
          placeholder="ADMIN_EXECUTE"
          expectedLabel="ADMIN_EXECUTE"
          helper={t("dataMgmt.csv.executeHint")}
          tone="danger"
          actions={
            <Button
              type="button"
              variant="danger"
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
  loading: string;
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
}) {
  const activeStep = syntheticDataResults ? 3 : syntheticData || syntheticDataStatus ? 1 : 0;
  const operationId = syntheticData?.operation_id.trim() ?? "";
  const resultTableOptions = syntheticAvailableTables;
  const hasValidResultTable = resultTableOptions.includes(syntheticResultTable);

  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Database}
        title={t("dataTools.synthetic.title")}
        description={t("dataMgmt.section.syntheticHint")}
      />

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
              loading={loading === "synthetic-tables"}
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
          {syntheticAvailableTables.length > 0 ? (
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
            placeholder="ADMIN_EXECUTE"
            expectedLabel="ADMIN_EXECUTE"
            helper={t("dbAdmin.confirmation.adminHelper")}
            tone="danger"
            actions={
              <Button
                type="button"
                variant="danger"
                size="sm"
                className="w-full sm:w-auto"
                loading={loading === "synthetic-data"}
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
              loading={loading === "synthetic-status"}
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

        {syntheticDataStatus && (
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
        )}
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
              loading={loading === "synthetic-results"}
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

        {syntheticDataResults ? (
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
    `/api/nl2sql/synthetic-data/operations/${encodeURIComponent(operationId)}`
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
