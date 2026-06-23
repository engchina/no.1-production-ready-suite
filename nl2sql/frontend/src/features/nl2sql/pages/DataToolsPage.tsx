import { useEffect, useMemo, useState } from "react";
import { Code2, Database, Download, Eye, FileSpreadsheet, Play, RefreshCw, Table2, Trash2, Wand2 } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { csvImportPayload, defaultCsvImportForm, type CsvImportFormState } from "../csvImportState";
import type {
  AnnotationApplyData,
  AnnotationApplyItem,
  AnnotationSuggestionData,
  CommentApplyData,
  CommentApplyItem,
  CommentSuggestionData,
  CsvImportData,
  DbAdminExecuteData,
  DbAdminImportTabularData,
  DbAdminObjectDetail,
  DbAdminObjectsData,
  Nl2SqlProfile,
  QueryResults,
  SampleDataInfo,
  SampleDataMutationData,
  SchemaCatalog,
  SyntheticCasesData,
  SyntheticDataOperationData,
  SyntheticDataOperationStatusData,
  SyntheticDataResultsData,
} from "../types";
import { CsvImportResult } from "./SettingsPages";

export function DataToolsPage() {
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [csvForm, setCsvForm] = useState<CsvImportFormState>(() => defaultCsvImportForm());
  const [csvResult, setCsvResult] = useState<CsvImportData | null>(null);
  const [dbAdminTables, setDbAdminTables] = useState<DbAdminObjectsData | null>(null);
  const [dbAdminViews, setDbAdminViews] = useState<DbAdminObjectsData | null>(null);
  const [dbAdminDetail, setDbAdminDetail] = useState<DbAdminObjectDetail | null>(null);
  const [dbAdminSql, setDbAdminSql] = useState("");
  const [dbAdminExecute, setDbAdminExecute] = useState(false);
  const [dbAdminConfirmation, setDbAdminConfirmation] = useState("");
  const [dbAdminResult, setDbAdminResult] = useState<DbAdminExecuteData | null>(null);
  const [dbAdminImportTable, setDbAdminImportTable] = useState("");
  const [dbAdminImportFilename, setDbAdminImportFilename] = useState("");
  const [dbAdminImportBase64, setDbAdminImportBase64] = useState("");
  const [dbAdminImportSheet, setDbAdminImportSheet] = useState("");
  const [dbAdminImportMode, setDbAdminImportMode] = useState("create");
  const [dbAdminImportExecute, setDbAdminImportExecute] = useState(false);
  const [dbAdminImportConfirmation, setDbAdminImportConfirmation] = useState("");
  const [dbAdminImportResult, setDbAdminImportResult] = useState<DbAdminImportTabularData | null>(null);
  const [dbAdminDropExecute, setDbAdminDropExecute] = useState(false);
  const [dbAdminDropConfirmation, setDbAdminDropConfirmation] = useState("");
  const [sampleInfo, setSampleInfo] = useState<SampleDataInfo | null>(null);
  const [sampleStep, setSampleStep] = useState<"tables" | "views" | "data" | "all">("all");
  const [sampleExecute, setSampleExecute] = useState(false);
  const [sampleConfirmation, setSampleConfirmation] = useState("");
  const [sampleResult, setSampleResult] = useState<SampleDataMutationData | null>(null);
  const [comments, setComments] = useState<CommentSuggestionData | null>(null);
  const [commentApply, setCommentApply] = useState<CommentApplyData | null>(null);
  const [commentExecute, setCommentExecute] = useState(false);
  const [commentConfirmation, setCommentConfirmation] = useState("");
  const [commentUseLlm, setCommentUseLlm] = useState(false);
  const [annotations, setAnnotations] = useState<AnnotationSuggestionData | null>(null);
  const [annotationApply, setAnnotationApply] = useState<AnnotationApplyData | null>(null);
  const [annotationExecute, setAnnotationExecute] = useState(false);
  const [annotationConfirmation, setAnnotationConfirmation] = useState("");
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

  const canSubmitCsv = csvForm.tableName.trim().length > 0 && csvForm.csvText.trim().length > 0;
  const annotationSql = useMemo(() => buildCommentSql(catalog, comments), [catalog, comments]);
  const annotationStatementCount = annotationSql ? annotationSql.split("\n").filter(Boolean).length : 0;
  const annotationDdl = useMemo(() => buildAnnotationSql(annotations), [annotations]);
  const sampleSqlPreview = useMemo(() => {
    if (!sampleInfo) return "";
    const steps = sampleStep === "all" ? ["tables", "views", "data"] : [sampleStep];
    return steps
      .flatMap((step) => sampleInfo.sql[step] ?? [])
      .join(";\n\n");
  }, [sampleInfo, sampleStep]);

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [catalogData, profileData, adminTables, adminViews, sampleData] = await Promise.all([
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
        apiGet<SampleDataInfo>("/api/nl2sql/sample-data"),
      ]);
      setCatalog(catalogData);
      setProfiles(profileData);
      setDbAdminTables(adminTables);
      setDbAdminViews(adminViews);
      setSampleInfo(sampleData);
      setProfileId((current) => current || profileData[0]?.id || "");
      setSyntheticTable((current) => current || adminTables.items[0]?.name || catalogData.tables[0]?.table_name || "");
      setDbAdminImportTable((current) => current || `NL2SQL_IMPORT_${Date.now()}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const updateCsvForm = <K extends keyof CsvImportFormState>(key: K, value: CsvImportFormState[K]) => {
    setCsvForm((current) => ({ ...current, [key]: value }));
  };

  const importCsv = async () => {
    if (!canSubmitCsv) return;
    setLoading("csv");
    setMessage("");
    try {
      const result = await apiPost<CsvImportData>("/api/schema/import-csv", csvImportPayload(csvForm));
      setCsvResult(result);
      if (result.executed) {
        setCatalog(await apiGet<SchemaCatalog>("/api/schema/catalog"));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.csv"));
    } finally {
      setLoading("");
    }
  };

  const loadDbAdminObject = async (name: string, objectType: "tables" | "views") => {
    setLoading(`db-admin-detail-${name}`);
    setMessage("");
    try {
      setDbAdminDetail(
        await apiGet<DbAdminObjectDetail>(
          `/api/nl2sql/db-admin/${objectType}/${encodeURIComponent(name)}`
        )
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.dbAdmin"));
    } finally {
      setLoading("");
    }
  };

  const runDbAdminSql = async () => {
    if (!dbAdminSql.trim()) return;
    setLoading("db-admin-sql");
    setMessage("");
    try {
      setDbAdminResult(
        await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/execute", {
          sql: dbAdminSql,
          execute: dbAdminExecute,
          confirmation: dbAdminExecute ? dbAdminConfirmation : "",
          reason: "ui-db-admin",
        })
      );
      if (dbAdminExecute) {
        setDbAdminTables(await apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.dbAdmin"));
    } finally {
      setLoading("");
    }
  };

  const dropDbAdminTable = async (tableName: string) => {
    setLoading(`db-admin-drop-${tableName}`);
    setMessage("");
    try {
      setDbAdminResult(
        await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/drop-table", {
          table_name: tableName,
          execute: dbAdminDropExecute,
          confirmation: dbAdminDropExecute ? dbAdminDropConfirmation : "",
          reason: "ui-db-admin-drop",
        })
      );
      setDbAdminTables(await apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.dbAdmin"));
    } finally {
      setLoading("");
    }
  };

  const importDbAdminFile = async (file: File | undefined) => {
    if (!file) return;
    setDbAdminImportFilename(file.name);
    setDbAdminImportBase64(await fileToBase64(file));
  };

  const importDbAdminTabular = async () => {
    if (!dbAdminImportTable.trim() || !dbAdminImportBase64) return;
    setLoading("db-admin-import");
    setMessage("");
    try {
      setDbAdminImportResult(
        await apiPost<DbAdminImportTabularData>("/api/nl2sql/db-admin/import-tabular", {
          table_name: dbAdminImportTable,
          filename: dbAdminImportFilename || "upload.csv",
          content_base64: dbAdminImportBase64,
          sheet_name: dbAdminImportSheet,
          mode: dbAdminImportMode,
          execute: dbAdminImportExecute,
          confirmation: dbAdminImportExecute ? dbAdminImportConfirmation : "",
          reason: "ui-db-admin-import",
        })
      );
      if (dbAdminImportExecute) {
        setDbAdminTables(await apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.dbAdminImport"));
    } finally {
      setLoading("");
    }
  };

  const reloadSampleState = async () => {
    const [catalogData, profileData, adminTables, adminViews, sampleData] = await Promise.all([
      apiGet<SchemaCatalog>("/api/schema/catalog"),
      apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
      apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
      apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/views"),
      apiGet<SampleDataInfo>("/api/nl2sql/sample-data"),
    ]);
    setCatalog(catalogData);
    setProfiles(profileData);
    setDbAdminTables(adminTables);
    setDbAdminViews(adminViews);
    setSampleInfo(sampleData);
  };

  const importSampleData = async () => {
    setLoading("sample-import");
    setMessage("");
    try {
      setSampleResult(
        await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/import", {
          step: sampleStep,
          execute: sampleExecute,
          confirmation: sampleExecute ? sampleConfirmation : "",
          reason: "ui-sample-import",
        })
      );
      if (sampleExecute) {
        await reloadSampleState();
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      setLoading("");
    }
  };

  const deleteSampleData = async () => {
    setLoading("sample-delete");
    setMessage("");
    try {
      setSampleResult(
        await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/delete", {
          step: "all",
          execute: sampleExecute,
          confirmation: sampleExecute ? sampleConfirmation : "",
          reason: "ui-sample-delete",
        })
      );
      if (sampleExecute) {
        await reloadSampleState();
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      setLoading("");
    }
  };

  const suggestComments = async () => {
    setLoading("comments");
    setMessage("");
    try {
      setComments(
        await apiPost<CommentSuggestionData>("/api/nl2sql/comments/suggest", {
          use_llm: commentUseLlm,
          max_items: 120,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.comments"));
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

  const downloadAnnotationSql = () => {
    if (!annotationSql) return;
    downloadText("nl2sql_comment_annotations.sql", annotationSql);
  };

  const applyComments = async () => {
    if (!catalog) return;
    setLoading("comments-apply");
    setMessage("");
    try {
      const result = await apiPost<CommentApplyData>("/api/nl2sql/comments/apply", {
        items: buildCommentItems(catalog, comments),
        execute: commentExecute,
        confirmation: commentExecute ? commentConfirmation : "",
        reason: "ui-comment-apply",
      });
      setCommentApply(result);
      if (result.executed) {
        setCatalog(await apiGet<SchemaCatalog>("/api/schema/catalog"));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.applyComments"));
    } finally {
      setLoading("");
    }
  };

  const generateAnnotations = async () => {
    setLoading("annotations");
    setMessage("");
    try {
      setAnnotations(await apiPost<AnnotationSuggestionData>("/api/nl2sql/annotations/generate"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.annotations"));
    } finally {
      setLoading("");
    }
  };

  const applyAnnotations = async () => {
    if (!annotations) return;
    setLoading("annotations-apply");
    setMessage("");
    try {
      setAnnotationApply(
        await apiPost<AnnotationApplyData>("/api/nl2sql/annotations/apply", {
          items: buildAnnotationItems(annotations),
          execute: annotationExecute,
          confirmation: annotationExecute ? annotationConfirmation : "",
          reason: "ui-annotation-apply",
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.applyAnnotations"));
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
        title={t("nav.dataTools")}
        subtitle={t("dataTools.subtitle")}
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
          <Metric label={t("settings.database.tables")} value={String(catalog?.tables.length ?? 0)} />
          <Metric
            label={t("settings.database.columns")}
            value={String(catalog?.tables.reduce((sum, table) => sum + table.columns.length, 0) ?? 0)}
          />
          <Metric label={t("dataTools.metric.profiles")} value={String(profiles.length)} />
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database size={18} aria-hidden="true" />
              {t("dataTools.sample.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(16rem,0.45fr)_minmax(0,1fr)]">
            <section className="grid content-start gap-3">
              <p className="text-sm leading-6 text-slate-600">{t("dataTools.sample.subtitle")}</p>
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={sampleInfo?.runtime ?? "deterministic"} />
                <StatusBadge
                  variant={(sampleInfo?.imported_objects.length ?? 0) > 0 ? "success" : "neutral"}
                  label={`${t("dataTools.sample.imported")} ${sampleInfo?.imported_objects.length ?? 0}`}
                />
              </div>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataTools.sample.step")}</span>
                <select
                  value={sampleStep}
                  onChange={(event) => setSampleStep(event.currentTarget.value as typeof sampleStep)}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                >
                  {["all", "tables", "views", "data"].map((step) => (
                    <option key={step} value={step}>{step}</option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataTools.sample.confirmation")}</span>
                <input
                  value={sampleConfirmation}
                  onChange={(event) => setSampleConfirmation(event.currentTarget.value)}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={sampleInfo?.confirmation ?? "SQL_ASSIST_SAMPLE"}
                />
              </label>
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={sampleExecute}
                  onChange={(event) => setSampleExecute(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                />
                <span>{t("dataTools.sample.execute")}</span>
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant={sampleExecute ? "danger" : "secondary"}
                  size="sm"
                  loading={loading === "sample-import"}
                  onClick={() => void importSampleData()}
                >
                  <FileSpreadsheet size={15} aria-hidden="true" />
                  <span>{sampleExecute ? t("dataTools.sample.import") : t("dataTools.sample.importDryRun")}</span>
                </Button>
                <Button
                  type="button"
                  variant={sampleExecute ? "danger" : "secondary"}
                  size="sm"
                  loading={loading === "sample-delete"}
                  onClick={() => void deleteSampleData()}
                >
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{sampleExecute ? t("dataTools.sample.delete") : t("dataTools.sample.deleteDryRun")}</span>
                </Button>
              </div>
            </section>
            <section className="grid min-w-0 gap-3">
              <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <p className="font-semibold text-slate-900">{t("dataTools.sample.objects")}</p>
                <div className="flex flex-wrap gap-2">
                  {(sampleInfo?.objects ?? []).map((objectName) => (
                    <StatusBadge
                      key={objectName}
                      variant={sampleInfo?.imported_objects.includes(objectName) ? "success" : "neutral"}
                      label={objectName}
                    />
                  ))}
                </div>
              </div>
              <div className="grid gap-2">
                <p className="font-semibold text-slate-900">{t("dataTools.sample.sqlPreview")}</p>
                <pre className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
                  <code>{sampleSqlPreview || "-"}</code>
                </pre>
              </div>
              {sampleResult && (
                <DbAdminExecutionResult
                  result={{
                    executed: sampleResult.executed,
                    runtime: sampleResult.runtime,
                    select_result: null,
                    statements: sampleResult.statements,
                    committed: false,
                    rolled_back: false,
                    warnings: sampleResult.warnings,
                    timing: sampleResult.timing,
                  }}
                />
              )}
            </section>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Table2 size={18} aria-hidden="true" />
              {t("dataTools.dbAdmin.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <section className="grid content-start gap-3">
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={dbAdminTables?.runtime ?? "deterministic"} />
                <StatusBadge variant="neutral" label={`${dbAdminTables?.items.length ?? 0} tables`} />
                <StatusBadge variant="neutral" label={`${dbAdminViews?.items.length ?? 0} views`} />
              </div>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataTools.dbAdmin.dropConfirmation")}</span>
                <input
                  value={dbAdminDropConfirmation}
                  onChange={(event) => setDbAdminDropConfirmation(event.currentTarget.value)}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder="TABLE_NAME / ADMIN_EXECUTE"
                />
              </label>
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={dbAdminDropExecute}
                  onChange={(event) => setDbAdminDropExecute(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                />
                <span>{t("dataTools.dbAdmin.dropExecute")}</span>
              </label>
              <div className="grid max-h-96 gap-2 overflow-auto pr-1">
                {(dbAdminTables?.items ?? []).slice(0, 30).map((item) => (
                  <section key={item.name} className="rounded-md border border-slate-200 p-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <button
                        type="button"
                        className="break-all text-left font-mono text-xs font-semibold text-sky-800 underline-offset-2 hover:underline"
                        onClick={() => void loadDbAdminObject(item.name, "tables")}
                      >
                        {item.name}
                      </button>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          loading={loading === `db-admin-detail-${item.name}`}
                          onClick={() => void loadDbAdminObject(item.name, "tables")}
                        >
                          <Eye size={15} aria-hidden="true" />
                          <span>{t("dataTools.dbAdmin.detail")}</span>
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => window.open(`/api/nl2sql/db-admin/tables/${encodeURIComponent(item.name)}/export.xlsx`, "_blank", "noopener,noreferrer")}
                        >
                          <Download size={15} aria-hidden="true" />
                          <span>{t("dataTools.dbAdmin.export")}</span>
                        </Button>
                        <Button
                          type="button"
                          variant={dbAdminDropExecute ? "danger" : "secondary"}
                          size="sm"
                          loading={loading === `db-admin-drop-${item.name}`}
                          onClick={() => void dropDbAdminTable(item.name)}
                        >
                          <Trash2 size={15} aria-hidden="true" />
                          <span>{dbAdminDropExecute ? t("dataTools.dbAdmin.drop") : t("dataTools.dbAdmin.dropDryRun")}</span>
                        </Button>
                      </div>
                    </div>
                    <p className="mt-2 text-xs text-slate-600">
                      {item.comment || "-"} / rows: {item.row_count ?? "-"}
                    </p>
                  </section>
                ))}
                {(dbAdminViews?.items ?? []).slice(0, 12).map((item) => (
                  <button
                    key={item.name}
                    type="button"
                    className="rounded-md border border-slate-200 bg-slate-50 p-3 text-left text-sm"
                    onClick={() => void loadDbAdminObject(item.name, "views")}
                  >
                    <span className="break-all font-mono text-xs font-semibold text-slate-800">{item.name}</span>
                    <span className="ml-2 text-xs text-slate-500">VIEW</span>
                  </button>
                ))}
              </div>
              {dbAdminDetail && (
                <section className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="break-all font-mono text-xs font-semibold text-slate-900">{dbAdminDetail.name}</p>
                    <StatusBadge variant="neutral" label={dbAdminDetail.object_type} />
                  </div>
                  <p className="text-slate-700">{dbAdminDetail.comment || "-"}</p>
                  <pre className="max-h-52 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
                    <code>{dbAdminDetail.ddl || "-"}</code>
                  </pre>
                </section>
              )}
            </section>

            <section className="grid content-start gap-4">
              <div className="grid gap-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="font-semibold text-slate-900">{t("dataTools.dbAdmin.sqlTitle")}</p>
                  <Button
                    type="button"
                    variant={dbAdminExecute ? "danger" : "secondary"}
                    size="sm"
                    loading={loading === "db-admin-sql"}
                    disabled={!dbAdminSql.trim()}
                    onClick={() => void runDbAdminSql()}
                  >
                    <Code2 size={15} aria-hidden="true" />
                    <span>{dbAdminExecute ? t("dataTools.dbAdmin.execute") : t("dataTools.dbAdmin.dryRun")}</span>
                  </Button>
                </div>
                <textarea
                  value={dbAdminSql}
                  onChange={(event) => setDbAdminSql(event.currentTarget.value)}
                  rows={7}
                  className="min-h-44 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                    <input
                      type="checkbox"
                      checked={dbAdminExecute}
                      onChange={(event) => setDbAdminExecute(event.currentTarget.checked)}
                      className="mt-1 h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                    />
                    <span>{t("dataTools.dbAdmin.sqlExecute")}</span>
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("dataTools.dbAdmin.confirmation")}</span>
                    <input
                      value={dbAdminConfirmation}
                      onChange={(event) => setDbAdminConfirmation(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      placeholder="ADMIN_EXECUTE"
                    />
                  </label>
                </div>
                {dbAdminResult && <DbAdminExecutionResult result={dbAdminResult} />}
              </div>

              <div className="grid gap-3 border-t border-slate-200 pt-4">
                <p className="font-semibold text-slate-900">{t("dataTools.dbAdmin.importTitle")}</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("settings.database.import.tableName")}</span>
                    <input
                      value={dbAdminImportTable}
                      onChange={(event) => setDbAdminImportTable(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    />
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("dataTools.dbAdmin.mode")}</span>
                    <select
                      value={dbAdminImportMode}
                      onChange={(event) => setDbAdminImportMode(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    >
                      {["create", "append", "replace", "truncate"].map((mode) => (
                        <option key={mode} value={mode}>{mode}</option>
                      ))}
                    </select>
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("dataTools.dbAdmin.sheet")}</span>
                    <input
                      value={dbAdminImportSheet}
                      onChange={(event) => setDbAdminImportSheet(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    />
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("dataTools.dbAdmin.confirmation")}</span>
                    <input
                      value={dbAdminImportConfirmation}
                      onChange={(event) => setDbAdminImportConfirmation(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      placeholder="TABLE_NAME / ADMIN_EXECUTE"
                    />
                  </label>
                </div>
                <div className="flex flex-wrap gap-2">
                  <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 focus-within:ring-2 focus-within:ring-sky-200">
                    <FileSpreadsheet size={15} aria-hidden="true" />
                    <span>{dbAdminImportFilename || t("dataTools.dbAdmin.file")}</span>
                    <input
                      className="sr-only"
                      type="file"
                      accept=".csv,.txt,.xlsx,.xlsm"
                      onChange={(event) => void importDbAdminFile(event.currentTarget.files?.[0])}
                    />
                  </label>
                  <label className="flex min-h-9 items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-800">
                    <input
                      type="checkbox"
                      checked={dbAdminImportExecute}
                      onChange={(event) => setDbAdminImportExecute(event.currentTarget.checked)}
                      className="h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                    />
                    <span>{t("dataTools.dbAdmin.importExecute")}</span>
                  </label>
                  <Button
                    type="button"
                    variant={dbAdminImportExecute ? "danger" : "secondary"}
                    size="sm"
                    loading={loading === "db-admin-import"}
                    disabled={!dbAdminImportBase64 || !dbAdminImportTable.trim()}
                    onClick={() => void importDbAdminTabular()}
                  >
                    <FileSpreadsheet size={15} aria-hidden="true" />
                    <span>{dbAdminImportExecute ? t("dataTools.dbAdmin.import") : t("dataTools.dbAdmin.importDryRun")}</span>
                  </Button>
                </div>
                {dbAdminImportResult && (
                  <CsvImportResult
                    result={{
                      table_name: dbAdminImportResult.table_name,
                      columns: dbAdminImportResult.columns,
                      row_count: dbAdminImportResult.row_count,
                      dry_run: dbAdminImportResult.dry_run,
                      executed: dbAdminImportResult.executed,
                      ddl: dbAdminImportResult.ddl,
                      insert_sql: dbAdminImportResult.insert_sql,
                      warnings: dbAdminImportResult.warnings,
                      sample_rows: dbAdminImportResult.sample_rows,
                      timing: dbAdminImportResult.timing,
                    }}
                  />
                )}
              </div>
            </section>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileSpreadsheet size={18} aria-hidden="true" />
              {t("settings.database.import.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5">
            <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
              <div className="grid content-start gap-4">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("settings.database.import.tableName")}</span>
                  <input
                    value={csvForm.tableName}
                    onChange={(event) => updateCsvForm("tableName", event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder={t("settings.database.import.tableNamePlaceholder")}
                  />
                </label>
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={csvForm.execute}
                    onChange={(event) => updateCsvForm("execute", event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("settings.database.import.execute")}</span>
                </label>
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={csvForm.replaceExisting}
                    onChange={(event) => updateCsvForm("replaceExisting", event.currentTarget.checked)}
                    disabled={!csvForm.execute}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500 disabled:opacity-50"
                  />
                  <span>{t("settings.database.import.replace")}</span>
                </label>
                <Button
                  type="button"
                  variant={csvForm.execute && csvForm.replaceExisting ? "danger" : "primary"}
                  loading={loading === "csv"}
                  disabled={!canSubmitCsv || loading === "csv"}
                  onClick={() => void importCsv()}
                >
                  {csvForm.execute ? <Play size={16} aria-hidden="true" /> : <FileSpreadsheet size={16} aria-hidden="true" />}
                  <span>
                    {csvForm.execute
                      ? t("settings.database.import.actionExecute")
                      : t("settings.database.import.actionDryRun")}
                  </span>
                </Button>
              </div>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("settings.database.import.csv")}</span>
                <textarea
                  value={csvForm.csvText}
                  onChange={(event) => updateCsvForm("csvText", event.currentTarget.value)}
                  rows={10}
                  className="min-h-72 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={t("settings.database.import.csvPlaceholder")}
                />
              </label>
            </div>
            {csvResult && <CsvImportResult result={csvResult} />}
          </CardContent>
        </Card>

        <div className="grid gap-5 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <CardTitle className="flex items-center gap-2">
                  <Wand2 size={18} aria-hidden="true" />
                  {t("dataTools.comments.title")}
                </CardTitle>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "comments"}
                    onClick={() => void suggestComments()}
                  >
                    <Wand2 size={15} aria-hidden="true" />
                    <span>{t("dataTools.action.suggestComments")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={!annotationSql}
                    onClick={downloadAnnotationSql}
                  >
                    <Download size={15} aria-hidden="true" />
                    <span>{t("dataTools.action.downloadComments")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant={commentExecute ? "danger" : "secondary"}
                    size="sm"
                    loading={loading === "comments-apply"}
                    disabled={!annotationSql || loading === "comments-apply"}
                    onClick={() => void applyComments()}
                  >
                    <Play size={15} aria-hidden="true" />
                    <span>
                      {commentExecute
                        ? t("dataTools.action.applyComments")
                        : t("dataTools.action.dryRunComments")}
                    </span>
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="grid gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Metric
                  label={t("dataTools.comments.statementCount")}
                  value={String(annotationStatementCount)}
                />
                <Metric
                  label={t("dataTools.comments.suggestionCount")}
                  value={String(comments?.suggestions.length ?? 0)}
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge variant="neutral" label={comments?.source ?? "deterministic"} />
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={commentUseLlm}
                    onChange={(event) => setCommentUseLlm(event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("dataTools.comments.useLlm")}</span>
                </label>
              </div>
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={commentExecute}
                  onChange={(event) => setCommentExecute(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                />
                <span>{t("dataTools.comments.execute")}</span>
              </label>
              {commentExecute && (
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.confirmation")}</span>
                  <input
                    value={commentConfirmation}
                    onChange={(event) => setCommentConfirmation(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder="ADMIN_EXECUTE"
                  />
                </label>
              )}
              {comments && comments.suggestions.length > 0 ? (
                comments.suggestions.slice(0, 12).map((item) => (
                  <section
                    key={item.object_name}
                    data-testid="comment-suggestion-row"
                    className="rounded-md border border-slate-200 p-3 text-sm"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="break-all font-mono text-xs text-slate-500">{item.object_name}</span>
                      <StatusBadge variant="neutral" label={item.object_type} />
                    </div>
                    <p className="mt-2 text-slate-800">{item.suggested_comment}</p>
                  </section>
                ))
              ) : (
                <EmptyState title={t("dataTools.comments.emptyTitle")} hint={t("dataTools.comments.emptyHint")} />
              )}
              {(comments?.warnings ?? []).map((warning) => (
                <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {warning}
                </p>
              ))}
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataTools.comments.annotationSql")}</span>
                <textarea
                  readOnly
                  value={annotationSql || t("dataTools.comments.noAnnotationSql")}
                  rows={8}
                  className="min-h-48 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
                />
              </label>
              {commentApply && (
                <section className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold text-slate-900">{t("dataTools.comments.applyResult")}</p>
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge
                        variant={commentApply.executed ? "success" : "neutral"}
                        label={
                          commentApply.executed
                            ? t("dataTools.comments.executed")
                            : t("dataTools.comments.dryRun")
                        }
                      />
                      <StatusBadge variant="neutral" label={commentApply.runtime} />
                    </div>
                  </div>
                  {commentApply.warnings.map((warning) => (
                    <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                      {warning}
                    </p>
                  ))}
                  <div className="grid gap-2">
                    {commentApply.statements.slice(0, 8).map((statement) => (
                      <div key={statement.sql} className="rounded-md bg-slate-50 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="break-all font-mono text-xs text-slate-500">
                            {statement.object_name}
                          </span>
                          <StatusBadge variant={statement.status === "applied" ? "success" : "neutral"} label={statement.status} />
                        </div>
                        <code className="mt-2 block break-words text-xs text-slate-700">{statement.sql}</code>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              <section className="grid gap-3 border-t border-slate-200 pt-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="font-semibold text-slate-900">{t("dataTools.annotations.title")}</p>
                    <p className="mt-1 text-sm text-slate-600">{t("dataTools.annotations.subtitle")}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      loading={loading === "annotations"}
                      onClick={() => void generateAnnotations()}
                    >
                      <Wand2 size={15} aria-hidden="true" />
                      <span>{t("dataTools.action.generateAnnotations")}</span>
                    </Button>
                    <Button
                      type="button"
                      variant={annotationExecute ? "danger" : "secondary"}
                      size="sm"
                      loading={loading === "annotations-apply"}
                      disabled={!annotations || loading === "annotations-apply"}
                      onClick={() => void applyAnnotations()}
                    >
                      <Play size={15} aria-hidden="true" />
                      <span>
                        {annotationExecute
                          ? t("dataTools.action.applyAnnotations")
                          : t("dataTools.action.dryRunAnnotations")}
                      </span>
                    </Button>
                  </div>
                </div>
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={annotationExecute}
                    onChange={(event) => setAnnotationExecute(event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("dataTools.annotations.execute")}</span>
                </label>
                {annotationExecute && (
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("dataTools.confirmation")}</span>
                    <input
                      value={annotationConfirmation}
                      onChange={(event) => setAnnotationConfirmation(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      placeholder="ADMIN_EXECUTE"
                    />
                  </label>
                )}
                <textarea
                  readOnly
                  value={annotationDdl || t("dataTools.annotations.empty")}
                  rows={6}
                  className="min-h-36 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
                />
                {annotationApply && (
                  <div className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge
                        variant={annotationApply.executed ? "success" : "neutral"}
                        label={annotationApply.executed ? t("dataTools.comments.executed") : t("dataTools.comments.dryRun")}
                      />
                      <StatusBadge variant="neutral" label={annotationApply.runtime} />
                    </div>
                    {annotationApply.warnings.map((warning) => (
                      <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                        {warning}
                      </p>
                    ))}
                    {annotationApply.statements.slice(0, 6).map((statement) => (
                      <code key={statement.sql} className="block break-words rounded-md bg-slate-50 p-2 text-xs text-slate-700">
                        {statement.sql}
                      </code>
                    ))}
                  </div>
                )}
              </section>
            </CardContent>
          </Card>

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
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("dataTools.syntheticData.table")}</span>
                    <select
                      value={syntheticTable}
                      onChange={(event) => setSyntheticTable(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
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
                {syntheticDataResults && (
                  <QueryResultsTable results={syntheticDataResults.results} />
                )}
              </section>
            </CardContent>
          </Card>
        </div>
      </main>
    </>
  );
}

function quoteIdentifier(value: string) {
  return `"${value.replace(/"/g, '""')}"`;
}

function quoteSqlString(value: string) {
  return `'${value.replace(/'/g, "''")}'`;
}

function buildCommentSql(catalog: SchemaCatalog | null, comments: CommentSuggestionData | null) {
  if (!catalog) return "";
  const suggestionMap = new Map(
    comments?.suggestions.map((item) => [item.object_name.toUpperCase(), item.suggested_comment]) ?? []
  );
  const statements: string[] = [];
  for (const table of catalog.tables) {
    const tableComment = suggestionMap.get(table.table_name.toUpperCase()) ?? table.comment;
    if (tableComment) {
      statements.push(`COMMENT ON TABLE ${quoteIdentifier(table.table_name)} IS ${quoteSqlString(tableComment)};`);
    }
    for (const column of table.columns) {
      const objectName = `${table.table_name}.${column.column_name}`;
      const columnComment = suggestionMap.get(objectName.toUpperCase()) ?? column.comment;
      if (columnComment) {
        statements.push(
          `COMMENT ON COLUMN ${quoteIdentifier(table.table_name)}.${quoteIdentifier(column.column_name)} IS ${quoteSqlString(columnComment)};`
        );
      }
    }
  }
  return statements.join("\n");
}

function buildCommentItems(catalog: SchemaCatalog, comments: CommentSuggestionData | null): CommentApplyItem[] {
  const suggestionMap = new Map(
    comments?.suggestions.map((item) => [item.object_name.toUpperCase(), item.suggested_comment]) ?? []
  );
  const items: CommentApplyItem[] = [];
  for (const table of catalog.tables) {
    const tableComment = suggestionMap.get(table.table_name.toUpperCase()) ?? table.comment;
    if (tableComment) {
      items.push({ object_name: table.table_name, object_type: "table", comment: tableComment });
    }
    for (const column of table.columns) {
      const objectName = `${table.table_name}.${column.column_name}`;
      const columnComment = suggestionMap.get(objectName.toUpperCase()) ?? column.comment;
      if (columnComment) {
        items.push({ object_name: objectName, object_type: "column", comment: columnComment });
      }
    }
  }
  return items;
}

function buildAnnotationSql(annotations: AnnotationSuggestionData | null) {
  if (!annotations) return "";
  return annotations.suggestions
    .map((item) => {
      const annotationName = item.annotation_name.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
      const value = quoteSqlString(item.annotation_value);
      if (item.object_type === "column") {
        const [tableName = "", columnName = ""] = item.object_name.split(".");
        return `ALTER TABLE ${quoteIdentifier(tableName)} MODIFY ${quoteIdentifier(columnName)} ANNOTATIONS (${annotationName} ${value});`;
      }
      const ddlKind = item.object_type === "view" ? "VIEW" : "TABLE";
      return `ALTER ${ddlKind} ${quoteIdentifier(item.object_name)} ANNOTATIONS (${annotationName} ${value});`;
    })
    .join("\n");
}

function buildAnnotationItems(annotations: AnnotationSuggestionData): AnnotationApplyItem[] {
  return annotations.suggestions.map((item) => ({
    object_name: item.object_name,
    object_type: item.object_type,
    annotation_name: item.annotation_name,
    annotation_value: item.annotation_value,
  }));
}

function splitObjectList(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function fileToBase64(file: File) {
  const buffer = await file.arrayBuffer();
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary);
}

function DbAdminExecutionResult({ result }: { result: DbAdminExecuteData }) {
  return (
    <section className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant={result.executed ? "success" : "neutral"} label={result.executed ? "executed" : "dry-run"} />
        <StatusBadge variant="neutral" label={result.runtime} />
        {result.committed && <StatusBadge variant="success" label="committed" />}
        {result.rolled_back && <StatusBadge variant="danger" label="rolled back" />}
      </div>
      {result.warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-950">
          {warning}
        </p>
      ))}
      {result.select_result && <QueryResultsTable results={result.select_result} />}
      <div className="grid gap-2">
        {result.statements.map((statement) => (
          <div key={`${statement.index}-${statement.sql}`} className="rounded-md bg-white p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={statement.statement_type} />
                <StatusBadge
                  variant={statement.status === "success" || statement.status === "executed" ? "success" : statement.status === "error" ? "danger" : "neutral"}
                  label={statement.status}
                />
              </div>
              <span className="text-xs text-slate-500">{statement.elapsed_ms}ms</span>
            </div>
            <code className="mt-2 block break-words text-xs text-slate-700">{statement.sql}</code>
            {statement.error_message && (
              <p className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800">
                {statement.error_message}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function QueryResultsTable({ results }: { results: QueryResults }) {
  return (
    <div className="overflow-auto rounded-md border border-slate-200 bg-white">
      <table className="min-w-full divide-y divide-slate-200 text-left text-xs">
        <thead className="bg-slate-50 text-slate-600">
          <tr>
            {results.columns.map((column) => (
              <th key={column} className="px-3 py-2 font-semibold">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 text-slate-800">
          {results.rows.slice(0, 20).map((row, index) => (
            <tr key={index}>
              {results.columns.map((column) => (
                <td key={column} className="max-w-56 break-words px-3 py-2">
                  {String(row[column] ?? "")}
                </td>
              ))}
            </tr>
          ))}
          {results.rows.length === 0 && (
            <tr>
              <td className="px-3 py-4 text-slate-500" colSpan={Math.max(results.columns.length, 1)}>
                0 rows
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/sql;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}
