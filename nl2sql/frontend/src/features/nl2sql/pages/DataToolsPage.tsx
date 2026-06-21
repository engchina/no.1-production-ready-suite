import { useEffect, useMemo, useState } from "react";
import { Database, Download, FileSpreadsheet, Play, RefreshCw, Wand2 } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { csvImportPayload, defaultCsvImportForm, type CsvImportFormState } from "../csvImportState";
import type {
  CommentApplyData,
  CommentApplyItem,
  CommentSuggestionData,
  CsvImportData,
  Nl2SqlProfile,
  SchemaCatalog,
  SyntheticCasesData,
} from "../types";
import { CsvImportResult } from "./SettingsPages";

export function DataToolsPage() {
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [csvForm, setCsvForm] = useState<CsvImportFormState>(() => defaultCsvImportForm());
  const [csvResult, setCsvResult] = useState<CsvImportData | null>(null);
  const [comments, setComments] = useState<CommentSuggestionData | null>(null);
  const [commentApply, setCommentApply] = useState<CommentApplyData | null>(null);
  const [commentExecute, setCommentExecute] = useState(false);
  const [synthetic, setSynthetic] = useState<SyntheticCasesData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const canSubmitCsv = csvForm.tableName.trim().length > 0 && csvForm.csvText.trim().length > 0;
  const annotationSql = useMemo(() => buildCommentSql(catalog, comments), [catalog, comments]);
  const annotationStatementCount = annotationSql ? annotationSql.split("\n").filter(Boolean).length : 0;

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [catalogData, profileData] = await Promise.all([
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
      ]);
      setCatalog(catalogData);
      setProfiles(profileData);
      setProfileId((current) => current || profileData[0]?.id || "");
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

  const suggestComments = async () => {
    setLoading("comments");
    setMessage("");
    try {
      setComments(await apiPost<CommentSuggestionData>("/api/nl2sql/comments/suggest"));
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
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={commentExecute}
                  onChange={(event) => setCommentExecute(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                />
                <span>{t("dataTools.comments.execute")}</span>
              </label>
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
