import { useEffect, useMemo, useState } from "react";
import { Database, Eye, FileSpreadsheet, RefreshCw, Table2, Upload, Wand2 } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  PageMetric,
  QueryResultsTable,
  StatementRunnerCard,
  WorkSection,
  downloadBlob,
  fileToBase64,
} from "../components/DbAdminShared";
import {
  buildDeleteTemplate,
  buildInsertTemplate,
  buildMergeTemplate,
  buildMultiInsertTemplate,
  buildUpdateTemplate,
} from "../sqlTemplates";
import type {
  DbAdminCsvUploadData,
  DbAdminDataPreviewData,
  DbAdminObjectsData,
  Nl2SqlProfile,
  SchemaCatalog,
  SyntheticCasesData,
  SyntheticDataOperationData,
  SyntheticDataOperationStatusData,
  SyntheticDataResultsData,
} from "../types";

export function DataManagementPage() {
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [dbAdminTables, setDbAdminTables] = useState<DbAdminObjectsData | null>(null);
  const [dbAdminViews, setDbAdminViews] = useState<DbAdminObjectsData | null>(null);
  const [previewObject, setPreviewObject] = useState("");
  const [previewLimit, setPreviewLimit] = useState(100);
  const [previewWhere, setPreviewWhere] = useState("");
  const [preview, setPreview] = useState<DbAdminDataPreviewData | null>(null);
  const [csvTable, setCsvTable] = useState("");
  const [csvFilename, setCsvFilename] = useState("");
  const [csvBase64, setCsvBase64] = useState("");
  const [csvMode, setCsvMode] = useState<"insert" | "truncate_insert">("insert");
  const [csvExecute, setCsvExecute] = useState(false);
  const [csvConfirmation, setCsvConfirmation] = useState("");
  const [csvUploadResult, setCsvUploadResult] = useState<DbAdminCsvUploadData | null>(null);
  const [synthetic, setSynthetic] = useState<SyntheticCasesData | null>(null);
  const [syntheticData, setSyntheticData] = useState<SyntheticDataOperationData | null>(null);
  const [syntheticDataStatus, setSyntheticDataStatus] =
    useState<SyntheticDataOperationStatusData | null>(null);
  const [syntheticDataResults, setSyntheticDataResults] = useState<SyntheticDataResultsData | null>(null);
  const [syntheticTable, setSyntheticTable] = useState("");
  const [syntheticObjects, setSyntheticObjects] = useState("");
  const [syntheticProfileName, setSyntheticProfileName] = useState("");
  const [syntheticPrompt, setSyntheticPrompt] = useState("");
  const [syntheticConfirmation, setSyntheticConfirmation] = useState("");
  const [syntheticRows, setSyntheticRows] = useState(10);
  const [syntheticExecute, setSyntheticExecute] = useState(false);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const previewObjects = useMemo(() => {
    const tables = (dbAdminTables?.items ?? []).map((item) => ({ name: item.name, kind: "TABLE" }));
    const views = (dbAdminViews?.items ?? []).map((item) => ({ name: item.name, kind: "VIEW" }));
    return [...tables, ...views];
  }, [dbAdminTables, dbAdminViews]);

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [catalogData, profileData, adminTables, adminViews] = await Promise.all([
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
      ]);
      setCatalog(catalogData);
      setProfiles(profileData);
      setDbAdminTables(adminTables);
      setDbAdminViews(adminViews);
      setProfileId((current) => current || profileData[0]?.id || "");
      setPreviewObject((current) => current || adminTables.items[0]?.name || "");
      setCsvTable((current) => current || adminTables.items[0]?.name || "");
      setSyntheticTable((current) => current || adminTables.items[0]?.name || catalogData.tables[0]?.table_name || "");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
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
      const response = await fetch("/api/nl2sql/db-admin/preview-data/export.xlsx", {
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
  };

  const uploadCsv = async () => {
    if (!csvTable || !csvBase64) return;
    setLoading("csv-upload");
    setMessage("");
    try {
      const result = await apiPost<DbAdminCsvUploadData>("/api/nl2sql/db-admin/upload-csv", {
        table_name: csvTable,
        content_base64: csvBase64,
        filename: csvFilename || "upload.csv",
        mode: csvMode,
        execute: csvExecute,
        confirmation: csvExecute ? csvConfirmation : "",
        reason: "ui-data-management-csv",
      });
      setCsvUploadResult(result);
      if (result.executed) {
        setDbAdminTables(await apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataMgmt.error.csvUpload"));
    } finally {
      setLoading("");
    }
  };

  const generateSynthetic = async () => {
    setLoading("synthetic");
    setMessage("");
    try {
      const query = new URLSearchParams();
      if (profileId) query.set("profile_id", profileId);
      query.set("limit", "8");
      setSynthetic(await apiPost<SyntheticCasesData>(`/api/nl2sql/synthetic-cases?${query.toString()}`));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.synthetic"));
    } finally {
      setLoading("");
    }
  };

  const generateSyntheticData = async () => {
    if (!syntheticTable.trim()) return;
    setLoading("synthetic-data");
    setMessage("");
    try {
      setSyntheticData(
        await apiPost<SyntheticDataOperationData>("/api/nl2sql/synthetic-data/generate", {
          table_name: syntheticTable,
          object_list: splitObjectList(syntheticObjects),
          row_count: syntheticRows,
          rows_per_table: syntheticRows,
          profile_name: syntheticProfileName,
          user_prompt: syntheticPrompt,
          execute: syntheticExecute,
          confirmation: syntheticExecute ? syntheticConfirmation : "",
          reason: "ui-synthetic-data",
        })
      );
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
      setSyntheticDataStatus(
        await apiGet<SyntheticDataOperationStatusData>(
          `/api/nl2sql/synthetic-data/operations/${encodeURIComponent(operationId)}`
        )
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.syntheticStatus"));
    } finally {
      setLoading("");
    }
  };

  const loadSyntheticDataResults = async () => {
    const tableName = syntheticTable.trim();
    if (!tableName) return;
    setLoading("synthetic-results");
    setMessage("");
    try {
      setSyntheticDataResults(
        await apiGet<SyntheticDataResultsData>(
          `/api/nl2sql/synthetic-data/results?table_name=${encodeURIComponent(tableName)}&limit=20`
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
      <PageHeader
        title={t("nav.dataManagement")}
        subtitle={t("dataMgmt.subtitle")}
        actions={
          <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("dataTools.action.refresh")}</span>
          </Button>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <div className="grid gap-3 md:grid-cols-3">
          <PageMetric label={t("settings.database.tables")} value={String(catalog?.tables.length ?? 0)} />
          <PageMetric
            label={t("settings.database.columns")}
            value={String(catalog?.tables.reduce((sum, table) => sum + table.columns.length, 0) ?? 0)}
          />
          <PageMetric label={t("dataTools.metric.profiles")} value={String(profiles.length)} />
        </div>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2">
                <Table2 size={18} aria-hidden="true" />
                {t("dataMgmt.preview.title")}
              </CardTitle>
              <Button
                type="button"
                variant="primary"
                size="sm"
                loading={loading === "preview"}
                disabled={!previewObject}
                onClick={() => void showPreview()}
              >
                <Eye size={15} aria-hidden="true" />
                <span>{t("dataMgmt.preview.show")}</span>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
              <label className="grid min-w-0 gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataMgmt.preview.object")}</span>
                <select
                  value={previewObject}
                  onChange={(event) => setPreviewObject(event.currentTarget.value)}
                  className="min-h-11 w-full min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                >
                  {previewObjects.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name} ({item.kind})
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataMgmt.preview.limit")}</span>
                <input
                  type="number"
                  min={1}
                  max={10000}
                  value={previewLimit}
                  onChange={(event) =>
                    setPreviewLimit(Math.min(10000, Math.max(1, Number(event.currentTarget.value) || 1)))
                  }
                  className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
            </div>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("dataMgmt.preview.where")}</span>
              <textarea
                value={previewWhere}
                onChange={(event) => setPreviewWhere(event.currentTarget.value)}
                rows={2}
                placeholder={t("dataMgmt.preview.wherePlaceholder")}
                className="min-h-16 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
            {preview ? (
              <div className="grid gap-2">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <StatusBadge variant="neutral" label={preview.runtime} />
                    <StatusBadge variant="info" label={`${preview.results.total} rows`} />
                    <span className="break-all font-mono text-xs text-slate-500">{preview.sql}</span>
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "preview-export"}
                    disabled={preview.results.rows.length === 0}
                    onClick={() => void downloadPreviewXlsx()}
                  >
                    <FileSpreadsheet size={15} aria-hidden="true" />
                    <span>{t("dataMgmt.preview.exportXlsx")}</span>
                  </Button>
                </div>
                {preview.warnings.map((warning) => (
                  <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                    {warning}
                  </p>
                ))}
                <QueryResultsTable results={preview.results} />
              </div>
            ) : (
              <EmptyState title={t("dataMgmt.preview.emptyTitle")} hint={t("dataMgmt.preview.emptyHint")} />
            )}
          </CardContent>
        </Card>

        <WorkSection title={t("dataMgmt.csv.title")} description={t("dataMgmt.section.csvHint")}>
          <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2">
                <Upload size={18} aria-hidden="true" />
                {t("dataMgmt.csv.title")}
              </CardTitle>
              <Button
                type="button"
                variant={csvExecute ? "danger" : "secondary"}
                size="sm"
                loading={loading === "csv-upload"}
                disabled={!csvTable || !csvBase64}
                onClick={() => void uploadCsv()}
              >
                <Upload size={15} aria-hidden="true" />
                <span>{csvExecute ? t("dataMgmt.csv.upload") : t("dataMgmt.csv.dryRun")}</span>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid min-w-0 gap-3 sm:grid-cols-2">
              <label className="grid min-w-0 gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataMgmt.csv.table")}</span>
                <select
                  value={csvTable}
                  onChange={(event) => setCsvTable(event.currentTarget.value)}
                  className="min-h-11 w-full min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                >
                  {(dbAdminTables?.items ?? []).map((item) => (
                    <option key={item.name} value={item.name}>{item.name}</option>
                  ))}
                </select>
              </label>
              <div className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataMgmt.csv.mode")}</span>
                <div className="flex min-h-11 flex-wrap items-center gap-4 rounded-md border border-slate-200 px-3 py-2">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="csv-upload-mode"
                      checked={csvMode === "insert"}
                      onChange={() => setCsvMode("insert")}
                      className="h-4 w-4 border-slate-300 text-sky-700 focus:ring-sky-500"
                    />
                    <span>{t("dataMgmt.csv.mode.insert")}</span>
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="csv-upload-mode"
                      checked={csvMode === "truncate_insert"}
                      onChange={() => setCsvMode("truncate_insert")}
                      className="h-4 w-4 border-slate-300 text-red-700 focus:ring-red-500"
                    />
                    <span>{t("dataMgmt.csv.mode.truncateInsert")}</span>
                  </label>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 focus-within:ring-2 focus-within:ring-sky-200">
                <FileSpreadsheet size={15} aria-hidden="true" />
                <span>{csvFilename || t("dataMgmt.csv.file")}</span>
                <input
                  className="sr-only"
                  type="file"
                  accept=".csv"
                  onChange={(event) => void pickCsvFile(event.currentTarget.files?.[0])}
                />
              </label>
              <label className="flex min-h-9 items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={csvExecute}
                  onChange={(event) => setCsvExecute(event.currentTarget.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                />
                <span>{t("dataMgmt.csv.execute")}</span>
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span className="sr-only">{t("dataMgmt.csv.confirmation")}</span>
                <input
                  value={csvConfirmation}
                  onChange={(event) => setCsvConfirmation(event.currentTarget.value)}
                  className="min-h-9 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={csvTable || t("dataMgmt.csv.confirmation")}
                  aria-label={t("dataMgmt.csv.confirmation")}
                />
              </label>
            </div>
            {csvUploadResult && (
              <section className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <StatusBadge
                    variant={csvUploadResult.executed ? "success" : "neutral"}
                    label={csvUploadResult.executed ? "executed" : "dry-run"}
                  />
                  <StatusBadge variant="neutral" label={csvUploadResult.runtime} />
                  <StatusBadge variant="neutral" label={csvUploadResult.mode} />
                  <StatusBadge variant="info" label={`${t("dataMgmt.csv.rows")} ${csvUploadResult.row_count}`} />
                  {csvUploadResult.executed && (
                    <>
                      <StatusBadge variant="success" label={`${t("dataMgmt.csv.success")} ${csvUploadResult.success_count}`} />
                      <StatusBadge
                        variant={csvUploadResult.error_count > 0 ? "danger" : "neutral"}
                        label={`${t("dataMgmt.csv.failed")} ${csvUploadResult.error_count}`}
                      />
                    </>
                  )}
                </div>
                {csvUploadResult.warnings.map((warning) => (
                  <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                    {warning}
                  </p>
                ))}
                <p className="text-slate-700">
                  {t("dataMgmt.csv.matched")}: <span className="font-mono text-xs">{csvUploadResult.matched_columns.join(", ") || "-"}</span>
                </p>
                {csvUploadResult.unmatched_csv_columns.length > 0 && (
                  <p className="text-slate-700">
                    {t("dataMgmt.csv.unmatched")}: <span className="font-mono text-xs">{csvUploadResult.unmatched_csv_columns.join(", ")}</span>
                  </p>
                )}
                {csvUploadResult.row_errors.length > 0 && (
                  <div className="grid gap-1">
                    <p className="font-semibold text-slate-900">{t("dataMgmt.csv.rowErrors")}</p>
                    {csvUploadResult.row_errors.map((error) => (
                      <p key={error} className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800">
                        {error}
                      </p>
                    ))}
                  </div>
                )}
                {csvUploadResult.hint && (
                  <p className="rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sky-900">{csvUploadResult.hint}</p>
                )}
                {csvUploadResult.sample_rows.length > 0 && (
                  <div className="grid gap-1">
                    <p className="font-semibold text-slate-900">{t("dataMgmt.csv.preview")}</p>
                    <QueryResultsTable
                      results={{
                        columns: Object.keys(csvUploadResult.sample_rows[0] ?? {}),
                        rows: csvUploadResult.sample_rows,
                        total: csvUploadResult.sample_rows.length,
                      }}
                    />
                  </div>
                )}
              </section>
            )}
          </CardContent>
          </Card>
        </WorkSection>

        <WorkSection title={t("dataMgmt.sql.title")} description={t("dataMgmt.section.sqlHint")}>
          <StatementRunnerCard
            policy="data_dml"
            target="data"
            title={t("dataMgmt.sql.title")}
            placeholder={t("dataMgmt.sql.placeholder")}
            templates={[
              { label: t("dataMgmt.template.insert"), build: () => buildInsertTemplate(null) },
              { label: t("dataMgmt.template.insertMulti"), build: () => buildMultiInsertTemplate(null) },
              { label: t("dataMgmt.template.update"), build: () => buildUpdateTemplate(null) },
              { label: t("dataMgmt.template.delete"), build: () => buildDeleteTemplate(null) },
              { label: t("dataMgmt.template.merge"), build: () => buildMergeTemplate(null) },
            ]}
            onExecuted={() => load()}
          />
          <p className="px-1 text-xs text-slate-500">{t("dataMgmt.sql.note")}</p>
        </WorkSection>

        <WorkSection title={t("dataTools.synthetic.title")} description={t("dataMgmt.section.syntheticHint")}>
          <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2">
                <Database size={18} aria-hidden="true" />
                {t("dataTools.synthetic.title")}
              </CardTitle>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={loading === "synthetic"}
                onClick={() => void generateSynthetic()}
              >
                <Wand2 size={15} aria-hidden="true" />
                <span>{t("dataTools.action.synthetic")}</span>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4">
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("nl2sql.profile.label")}</span>
              <select
                value={profileId}
                onChange={(event) => setProfileId(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              >
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            {synthetic && synthetic.cases.length > 0 ? (
              <div className="grid gap-2">
                {synthetic.cases.map((item) => (
                  <section key={`${item.profile_id}-${item.question}`} className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
                    <p className="font-semibold text-slate-900">{item.question}</p>
                    <pre className="overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
                      <code>{item.expected_sql}</code>
                    </pre>
                  </section>
                ))}
              </div>
            ) : (
              <EmptyState title={t("dataTools.synthetic.emptyTitle")} hint={t("dataTools.synthetic.emptyHint")} />
            )}

            <section className="grid gap-3 border-t border-slate-200 pt-4">
              <div>
                <p className="font-semibold text-slate-900">{t("dataTools.syntheticData.title")}</p>
                <p className="mt-1 text-sm text-slate-600">{t("dataTools.syntheticData.subtitle")}</p>
              </div>
              <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
                <label className="grid min-w-0 gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.syntheticData.table")}</span>
                  <select
                    value={syntheticTable}
                    onChange={(event) => setSyntheticTable(event.currentTarget.value)}
                    className="min-h-11 w-full min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  >
                    {(catalog?.tables ?? []).map((table) => (
                      <option key={table.table_name} value={table.table_name}>
                        {table.table_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.syntheticData.rows")}</span>
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    value={syntheticRows}
                    onChange={(event) => setSyntheticRows(Number(event.currentTarget.value) || 1)}
                    className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.syntheticData.objects")}</span>
                  <textarea
                    value={syntheticObjects}
                    onChange={(event) => setSyntheticObjects(event.currentTarget.value)}
                    rows={3}
                    className="min-h-24 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.syntheticData.prompt")}</span>
                  <textarea
                    value={syntheticPrompt}
                    onChange={(event) => setSyntheticPrompt(event.currentTarget.value)}
                    rows={3}
                    className="min-h-24 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.syntheticData.profileName")}</span>
                  <input
                    value={syntheticProfileName}
                    onChange={(event) => setSyntheticProfileName(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.confirmation")}</span>
                  <input
                    value={syntheticConfirmation}
                    onChange={(event) => setSyntheticConfirmation(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder="TABLE_NAME / ADMIN_EXECUTE"
                  />
                </label>
              </div>
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={syntheticExecute}
                  onChange={(event) => setSyntheticExecute(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                />
                <span>{t("dataTools.syntheticData.execute")}</span>
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant={syntheticExecute ? "danger" : "secondary"}
                  size="sm"
                  loading={loading === "synthetic-data"}
                  disabled={!syntheticTable}
                  onClick={() => void generateSyntheticData()}
                >
                  <Database size={15} aria-hidden="true" />
                  <span>
                    {syntheticExecute
                      ? t("dataTools.syntheticData.generate")
                      : t("dataTools.syntheticData.dryRun")}
                  </span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  loading={loading === "synthetic-status"}
                  disabled={!syntheticData?.operation_id}
                  onClick={() => void checkSyntheticDataStatus()}
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>{t("dataTools.syntheticData.status")}</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  loading={loading === "synthetic-results"}
                  disabled={!syntheticTable}
                  onClick={() => void loadSyntheticDataResults()}
                >
                  <Eye size={15} aria-hidden="true" />
                  <span>{t("dataTools.syntheticData.results")}</span>
                </Button>
              </div>
              {syntheticData && (
                <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge variant={syntheticData.executed ? "success" : "neutral"} label={syntheticData.status} />
                    <StatusBadge variant="neutral" label={syntheticData.runtime} />
                    {syntheticData.operation_id && <StatusBadge variant="info" label={syntheticData.operation_id} />}
                  </div>
                  <p className="text-slate-700">{syntheticData.message}</p>
                  {syntheticData.warnings.map((warning) => (
                    <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                      {warning}
                    </p>
                  ))}
                </div>
              )}
              {syntheticDataStatus && (
                <div className="rounded-md border border-slate-200 p-3 text-sm">
                  <p className="font-semibold text-slate-900">{syntheticDataStatus.status}</p>
                  <p className="mt-1 text-slate-700">{syntheticDataStatus.message || "-"}</p>
                </div>
              )}
              {syntheticDataResults && <QueryResultsTable results={syntheticDataResults.results} />}
            </section>
          </CardContent>
          </Card>
        </WorkSection>
      </main>
    </>
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

function splitObjectList(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
