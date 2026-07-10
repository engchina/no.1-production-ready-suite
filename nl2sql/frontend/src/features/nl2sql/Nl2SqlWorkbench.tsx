import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpenText, Code2, Eye, History, Play, RefreshCw, RotateCcw, Sparkles, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  PageHeader,
} from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { SqlFileInput } from "./components/DbAdminShared";
import { EngineSelector } from "./components/EngineSelector";
import { FeedbackPanel } from "./components/FeedbackPanel";
import { GeneratedSqlPanel } from "./components/GeneratedSqlPanel";
import { Nl2SqlResultTable } from "./components/Nl2SqlResultTable";
import { OperationStatusStrip } from "./components/OperationStatusStrip";
import { SchemaReferencePanel } from "./components/SchemaReferencePanel";
import { isJobInFlight } from "./jobPersistence";
import { previewExecutePayload, previewToGeneratedSqlPanelData, sqlExecutePayload } from "./previewState";
import { prefillFromSearchParams } from "./queryPrefillState";
import type {
  GeneratedSqlPanelData,
  HistoryData,
  HistoryItem,
  JobCreateData,
  Nl2SqlEngine,
  Nl2SqlProfile,
  Nl2SqlResult,
  PreviewData,
  ProfileRecommendationData,
  ProfileRecommendationCandidate,
  QueryResults,
  RewriteData,
  SchemaCatalog,
  SimilarHistoryData,
  SimilarHistoryItem,
} from "./types";
import { useNl2SqlJobPolling } from "./useNl2SqlJobPolling";
import { useOperationTimer } from "./useOperationTimer";
import {
  emptySelection,
  insertTextAtRange,
  toAllowedObjects,
  toSchemaSelection,
  toggleColumnSelection,
  toggleTableSelection,
  type SchemaSelection,
} from "./workbenchState";

const QUICK_PROMPTS = [
  "登録済みの表から主要な列を一覧して",
  "社員と部署の一覧を確認したい",
  "部署別のプロジェクト数を集計して",
];

type InputMode = "natural" | "sql";

function lastMatchingHistory(history: HistoryItem[], result: Nl2SqlResult | null) {
  if (!result) return null;
  return history.find((item) => item.generated_sql === result.generated_sql) ?? null;
}

export function Nl2SqlWorkbench() {
  const [searchParams] = useSearchParams();
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [engine, setEngine] = useState<Nl2SqlEngine>("auto");
  const [inputMode, setInputMode] = useState<InputMode>("natural");
  const [profileId, setProfileId] = useState("default");
  const [rowLimit, setRowLimit] = useState(100);
  const [question, setQuestion] = useState("");
  const [sqlText, setSqlText] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [selection, setSelection] = useState<SchemaSelection>(() => emptySelection());
  const [result, setResult] = useState<Nl2SqlResult | null>(null);
  const [previewResult, setPreviewResult] = useState<GeneratedSqlPanelData | null>(null);
  const [previewExecutionResults, setPreviewExecutionResults] = useState<QueryResults | null>(null);
  const [sqlExecutionResults, setSqlExecutionResults] = useState<QueryResults | null>(null);
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [similarHistory, setSimilarHistory] = useState<SimilarHistoryItem[]>([]);
  const [similarHistoryLoading, setSimilarHistoryLoading] = useState(false);
  const [rewriteData, setRewriteData] = useState<RewriteData | null>(null);
  const [rewriteLoading, setRewriteLoading] = useState(false);
  const [rewriteUseGlossary, setRewriteUseGlossary] = useState(true);
  const [rewriteUseSchema, setRewriteUseSchema] = useState(true);
  const [rewriteExtraPrompt, setRewriteExtraPrompt] = useState("");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewExecuteLoading, setPreviewExecuteLoading] = useState(false);
  const [sqlExecuteLoading, setSqlExecuteLoading] = useState(false);
  const [error, setError] = useState("");
  const questionTextareaRef = useRef<HTMLTextAreaElement>(null);
  const sqlTextareaRef = useRef<HTMLTextAreaElement>(null);

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true);
    try {
      const [catalogData, profilesData, historyData] = await Promise.all([
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<HistoryData>("/api/nl2sql/history"),
      ]);
      setCatalog(catalogData);
      setProfiles(profilesData);
      setHistory(historyData.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.loadFailed"));
    } finally {
      setLoadingCatalog(false);
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    const historyData = await apiGet<HistoryData>("/api/nl2sql/history");
    setHistory(historyData.items);
  }, []);

  const handleJobResult = useCallback((data: Nl2SqlResult) => {
    setPreviewResult(null);
    setPreviewExecutionResults(null);
    setResult(data);
  }, []);

  const handleJobError = useCallback((message: string) => {
    setError(message);
  }, []);

  const { job, jobStartedAt, pollJob, trackJob, clearTrackedJob } = useNl2SqlJobPolling({
    onResult: handleJobResult,
    onError: handleJobError,
    onHistoryRefresh: refreshHistory,
  });
  const jobActive = isJobInFlight(job?.status) || submitting;
  const active = jobActive || previewLoading || previewExecuteLoading || sqlExecuteLoading;
  const elapsedSeconds = useOperationTimer(jobActive, jobStartedAt);
  const latestHistory = useMemo(() => lastMatchingHistory(history, result), [history, result]);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    const prefill = prefillFromSearchParams(searchParams);
    if (prefill.question) setQuestion(prefill.question);
    if (prefill.engine) setEngine(prefill.engine);
    if (prefill.profileId) setProfileId(prefill.profileId);
  }, [searchParams]);

  useEffect(() => {
    const trimmed = question.trim();
    if (inputMode !== "natural" || trimmed.length < 4 || active || profiles.length === 0) {
      setRecommendation(null);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setRecommendationLoading(true);
      void apiPost<ProfileRecommendationData>("/api/nl2sql/recommend-profile", {
        question: trimmed,
        current_profile_id: profileId || null,
      })
        .then((data) => {
          if (!cancelled) setRecommendation(data);
        })
        .catch(() => {
          if (!cancelled) setRecommendation(null);
        })
        .finally(() => {
          if (!cancelled) setRecommendationLoading(false);
        });
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [active, inputMode, profileId, profiles.length, question]);

  useEffect(() => {
    const trimmed = question.trim();
    if (inputMode !== "natural" || trimmed.length < 4 || active) {
      setSimilarHistory([]);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSimilarHistoryLoading(true);
      void apiPost<SimilarHistoryData>("/api/nl2sql/similar-history", {
        question: trimmed,
        profile_id: profileId || null,
        limit: 3,
      })
        .then((data) => {
          if (!cancelled) setSimilarHistory(data.items);
        })
        .catch(() => {
          if (!cancelled) setSimilarHistory([]);
        })
        .finally(() => {
          if (!cancelled) setSimilarHistoryLoading(false);
        });
    }, 650);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [active, inputMode, profileId, question]);

  const insertSchemaText = (text: string) => {
    const el = inputMode === "sql" ? sqlTextareaRef.current : questionTextareaRef.current;
    if (!el) {
      if (inputMode === "sql") {
        setSqlText((current) => `${current}${text}`);
      } else {
        setQuestion((current) => `${current}${text}`);
      }
      return;
    }
    const source = inputMode === "sql" ? sqlText : question;
    const start = el.selectionStart ?? source.length;
    const end = el.selectionEnd ?? source.length;
    const nextValue = insertTextAtRange(source, text, start, end);
    if (inputMode === "sql") {
      setSqlText(nextValue);
    } else {
      setQuestion(nextValue);
    }
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + text.length, start + text.length);
    });
  };

  const toggleTable = (tableName: string) => {
    setSelection((current) => toggleTableSelection(current, tableName));
  };

  const toggleColumn = (tableName: string, columnName: string) => {
    setSelection((current) => toggleColumnSelection(current, tableName, columnName));
  };

  const applyRecommendation = () => {
    if (!recommendation) return;
    setProfileId(recommendation.recommended_profile_id);
    setSelection(toSchemaSelection(recommendation.recommended_allowed_objects));
  };

  const applyRecommendationCandidate = (candidate: ProfileRecommendationCandidate) => {
    setProfileId(candidate.profile_id);
    setSelection(toSchemaSelection({ table_names: candidate.allowed_tables, columns: {} }));
  };

  const previewSql = async () => {
    const trimmed = question.trim();
    if (!trimmed || active) return;
    setPreviewLoading(true);
    setError("");
    setResult(null);
    setSqlExecutionResults(null);
    try {
      const data = await apiPost<PreviewData>("/api/nl2sql/preview", {
        question: trimmed,
        engine,
        profile_id: profileId || null,
        allowed_objects: toAllowedObjects(selection),
        row_limit: rowLimit,
      });
      setPreviewResult(previewToGeneratedSqlPanelData(data));
      setPreviewExecutionResults(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.previewFailed"));
      setPreviewResult(null);
    } finally {
      setPreviewLoading(false);
    }
  };

  const executePreviewSql = async () => {
    const sql = previewResult?.executable_sql || previewResult?.generated_sql || "";
    if (!sql.trim() || active) return;
    setPreviewExecuteLoading(true);
    setError("");
    setSqlExecutionResults(null);
    try {
      const data = await apiPost<QueryResults>("/api/nl2sql/execute", {
        ...previewExecutePayload(sql, profileId || null, toAllowedObjects(selection), rowLimit),
      });
      setPreviewExecutionResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.executePreviewFailed"));
      setPreviewExecutionResults(null);
    } finally {
      setPreviewExecuteLoading(false);
    }
  };

  const executeSqlText = async () => {
    const trimmed = sqlText.trim();
    if (!trimmed || active) return;
    setSqlExecuteLoading(true);
    setError("");
    setResult(null);
    setPreviewResult(null);
    setPreviewExecutionResults(null);
    setSqlExecutionResults(null);
    try {
      const data = await apiPost<QueryResults>("/api/nl2sql/execute", {
        ...sqlExecutePayload(trimmed, profileId || null, toAllowedObjects(selection), rowLimit),
      });
      setSqlExecutionResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.executeSqlFailed"));
      setSqlExecutionResults(null);
    } finally {
      setSqlExecuteLoading(false);
    }
  };

  const rewriteQuestion = async () => {
    const trimmed = question.trim();
    if (!trimmed || active) return;
    setRewriteLoading(true);
    setError("");
    try {
      const data = await apiPost<RewriteData>("/api/nl2sql/rewrite", {
        question: trimmed,
        profile_id: profileId || null,
        use_glossary: rewriteUseGlossary,
        use_schema: rewriteUseSchema,
        extra_prompt: rewriteExtraPrompt,
      });
      setRewriteData(data);
      setQuestion(data.rewritten_question);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.rewriteFailed"));
      setRewriteData(null);
    } finally {
      setRewriteLoading(false);
    }
  };

  const submit = async () => {
    const trimmed = question.trim();
    if (!trimmed || active) return;
    setSubmitting(true);
    setError("");
    setResult(null);
    setPreviewResult(null);
    setPreviewExecutionResults(null);
    setSqlExecutionResults(null);
    const startedAt = Date.now();
    try {
      const data = await apiPost<JobCreateData>("/api/nl2sql/jobs", {
        question: trimmed,
        engine,
        profile_id: profileId || null,
        allowed_objects: toAllowedObjects(selection),
        row_limit: rowLimit,
      });
      trackJob(data, startedAt);
      await pollJob(data.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.submitFailed"));
      clearTrackedJob();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.query")}
        subtitle={t("page.query.subtitle")}
        actions={
          <Button type="button" variant="secondary" size="sm" onClick={() => void loadCatalog()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("nl2sql.action.refresh")}</span>
          </Button>
        }
      />

      <main className="grid gap-5 p-4 lg:grid-cols-[minmax(22rem,0.9fr)_minmax(0,1.4fr)] lg:p-8">
        <section className="space-y-5">
          <SchemaReferencePanel
            catalog={catalog}
            loading={loadingCatalog}
            selection={selection}
            disabled={active}
            insertMode={inputMode === "sql" ? "physical" : "logical"}
            onToggleTable={toggleTable}
            onToggleColumn={toggleColumn}
            onInsert={insertSchemaText}
          />
        </section>

        <section className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>{t("nl2sql.workbench.title")}</CardTitle>
              <CardDescription>{t("nl2sql.workbench.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div
                className="grid gap-2 rounded-md bg-slate-100 p-1 text-sm sm:grid-cols-2"
                role="tablist"
                aria-label={t("nl2sql.mode.label")}
              >
                {(["natural", "sql"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    role="tab"
                    aria-selected={inputMode === mode}
                    disabled={active}
                    onClick={() => setInputMode(mode)}
                    className={[
                      "min-h-11 rounded-md px-3 py-2 font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-sky-300 disabled:opacity-50",
                      inputMode === mode
                        ? "bg-white text-slate-950 shadow-sm"
                        : "text-slate-600 hover:bg-white/70",
                    ].join(" ")}
                  >
                    {t(mode === "natural" ? "nl2sql.mode.natural" : "nl2sql.mode.sql")}
                  </button>
                ))}
              </div>

              {inputMode === "natural" && (
                <EngineSelector value={engine} onChange={setEngine} disabled={active} />
              )}

              <div className="grid gap-4 md:grid-cols-[1fr_10rem]">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("nl2sql.profile.label")}</span>
                  <select
                    value={profileId}
                    onChange={(event) => setProfileId(event.currentTarget.value)}
                    disabled={active}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  >
                    {profiles.map((profile) => (
                      <option key={profile.id} value={profile.id}>
                        {profile.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("nl2sql.rowLimit.label")}</span>
                  <input
                    type="number"
                    min={1}
                    max={5000}
                    value={rowLimit}
                    onChange={(event) => setRowLimit(Number(event.currentTarget.value) || 100)}
                    disabled={active}
                    className="min-h-11 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
              </div>

              {inputMode === "natural" ? (
                <>
                  <label className="grid gap-2 text-sm font-medium text-slate-800">
                    <span>{t("nl2sql.question.label")}</span>
                    <textarea
                      ref={questionTextareaRef}
                      value={question}
                      onChange={(event) => setQuestion(event.currentTarget.value)}
                      disabled={active}
                      rows={5}
                      className="min-h-36 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      placeholder={t("nl2sql.question.placeholder")}
                    />
                  </label>

                  <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="font-semibold text-slate-900">{t("nl2sql.rewrite.title")}</p>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        loading={rewriteLoading}
                        disabled={!question.trim() || active}
                        onClick={() => void rewriteQuestion()}
                      >
                        <Sparkles size={15} aria-hidden="true" />
                        <span>{t("nl2sql.rewrite.action")}</span>
                      </Button>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2">
                      <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 bg-white p-3 text-slate-800">
                        <input
                          type="checkbox"
                          checked={rewriteUseGlossary}
                          onChange={(event) => setRewriteUseGlossary(event.currentTarget.checked)}
                          className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                        />
                        <span>{t("nl2sql.rewrite.useGlossary")}</span>
                      </label>
                      <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 bg-white p-3 text-slate-800">
                        <input
                          type="checkbox"
                          checked={rewriteUseSchema}
                          onChange={(event) => setRewriteUseSchema(event.currentTarget.checked)}
                          className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                        />
                        <span>{t("nl2sql.rewrite.useSchema")}</span>
                      </label>
                    </div>
                    <label className="grid gap-1 font-medium text-slate-800">
                      <span>{t("nl2sql.rewrite.extraPrompt")}</span>
                      <input
                        value={rewriteExtraPrompt}
                        onChange={(event) => setRewriteExtraPrompt(event.currentTarget.value)}
                        disabled={active}
                        className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      />
                    </label>
                    {rewriteData && (
                      <div className="flex flex-wrap gap-2">
                        <span className="rounded-md bg-white px-2 py-1 text-xs font-medium text-slate-700">
                          {rewriteData.source}
                        </span>
                        {rewriteData.model && (
                          <span className="rounded-md bg-white px-2 py-1 text-xs font-medium text-slate-700">
                            {rewriteData.model}
                          </span>
                        )}
                      </div>
                    )}
                  </section>

                  {(recommendation || recommendationLoading) && (
                    <div className="grid gap-3 rounded-md border border-sky-200 bg-sky-50 p-3 text-sm text-slate-800">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="flex items-start gap-2">
                          <Sparkles size={16} className="mt-0.5 text-sky-700" aria-hidden="true" />
                          <div>
                            <p className="font-semibold text-slate-900">
                              {recommendationLoading
                                ? t("nl2sql.recommend.loading")
                                : t("nl2sql.recommend.title")}
                            </p>
                            {recommendation && (
                              <p className="mt-1 text-slate-700">
                                {t("nl2sql.recommend.profile", {
                                  name: recommendation.recommended_profile_name,
                                  confidence: Math.round(recommendation.confidence * 100),
                                })}
                              </p>
                            )}
                          </div>
                        </div>
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          disabled={!recommendation || active}
                          onClick={applyRecommendation}
                        >
                          {t("nl2sql.recommend.apply")}
                        </Button>
                      </div>
                      {recommendation && (
                        <div className="grid gap-2">
                          <p>{recommendation.reason}</p>
                          <dl className="grid gap-1 rounded-md bg-white/70 p-2">
                            <div className="flex flex-wrap justify-between gap-2">
                              <dt className="font-medium">{t("nl2sql.recommend.rewritten")}</dt>
                              <dd className="text-right">{recommendation.rewritten_question}</dd>
                            </div>
                            <div className="flex flex-wrap justify-between gap-2">
                              <dt className="font-medium">{t("nl2sql.recommend.tables")}</dt>
                              <dd className="font-mono text-xs">
                                {recommendation.recommended_allowed_objects.table_names.join(", ") || "-"}
                              </dd>
                            </div>
                          </dl>
                          {recommendation.candidates.length > 0 && (
                            <section className="grid gap-2">
                              <p className="text-xs font-semibold uppercase tracking-normal text-slate-500">
                                {t("nl2sql.recommend.candidates")}
                              </p>
                              <div className="grid gap-2">
                                {recommendation.candidates.map((candidate) => (
                                  <div
                                    key={candidate.profile_id}
                                    className="grid gap-2 rounded-md border border-sky-100 bg-white/80 p-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start"
                                  >
                                    <div className="min-w-0 space-y-1">
                                      <div className="flex flex-wrap items-center gap-2">
                                        <p className="font-semibold text-slate-900">{candidate.profile_name}</p>
                                        <span className="rounded-md bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-900">
                                          {t("nl2sql.recommend.candidateScore", {
                                            score: Math.round(candidate.score * 100),
                                          })}
                                        </span>
                                      </div>
                                      <dl className="grid gap-1 text-xs text-slate-600">
                                        <div className="grid gap-1 sm:grid-cols-[5rem_1fr]">
                                          <dt className="font-medium">{t("nl2sql.recommend.matchedTerms")}</dt>
                                          <dd className="break-words">
                                            {candidate.matched_terms.join(", ") || "-"}
                                          </dd>
                                        </div>
                                        <div className="grid gap-1 sm:grid-cols-[5rem_1fr]">
                                          <dt className="font-medium">{t("nl2sql.recommend.allowedTables")}</dt>
                                          <dd className="break-words font-mono">
                                            {candidate.allowed_tables.join(", ") || "-"}
                                          </dd>
                                        </div>
                                      </dl>
                                    </div>
                                    <Button
                                      type="button"
                                      variant="secondary"
                                      size="sm"
                                      disabled={active}
                                      onClick={() => applyRecommendationCandidate(candidate)}
                                    >
                                      {t("nl2sql.recommend.applyCandidate")}
                                    </Button>
                                  </div>
                                ))}
                              </div>
                            </section>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {(similarHistory.length > 0 || similarHistoryLoading) && (
                    <div className="grid gap-3 rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-800">
                      <div className="flex items-center gap-2">
                        <BookOpenText size={16} className="text-slate-700" aria-hidden="true" />
                        <p className="font-semibold text-slate-900">
                          {similarHistoryLoading
                            ? t("nl2sql.similar.loading")
                            : t("nl2sql.similar.title")}
                        </p>
                      </div>
                      {similarHistory.slice(0, 2).map((entry) => (
                        <article key={entry.item.id} className="grid gap-2 rounded-md bg-slate-50 p-3">
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div>
                              <p className="font-medium text-slate-900">{entry.item.question}</p>
                              <p className="mt-1 text-xs text-slate-600">{entry.reason}</p>
                            </div>
                            <span className="rounded-md bg-white px-2 py-1 text-xs font-medium text-slate-700">
                              {t("nl2sql.similar.score", {
                                score: Math.round(entry.score * 100),
                              })}
                            </span>
                          </div>
                          <pre className="max-h-28 overflow-auto rounded-md border border-slate-200 bg-white p-2 text-xs leading-5 text-slate-800">
                            <code>{entry.item.executable_sql || entry.item.generated_sql}</code>
                          </pre>
                        </article>
                      ))}
                    </div>
                  )}

                  <div className="flex flex-wrap gap-2">
                    {QUICK_PROMPTS.map((prompt) => (
                      <Button
                        key={prompt}
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={active}
                        onClick={() => setQuestion(prompt)}
                      >
                        {prompt}
                      </Button>
                    ))}
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <History size={15} aria-hidden="true" />
                      <span>{t("nl2sql.history.count", { count: history.length })}</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="md"
                        disabled={active}
                        onClick={() => {
                          setSelection(emptySelection());
                          setQuestion("");
                          setSqlText("");
                          setResult(null);
                          setPreviewResult(null);
                          setPreviewExecutionResults(null);
                          setSqlExecutionResults(null);
                          setRewriteData(null);
                          setError("");
                        }}
                      >
                        <RotateCcw size={16} aria-hidden="true" />
                        <span>{t("nl2sql.action.reset")}</span>
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
                        size="md"
                        loading={previewLoading}
                        disabled={!question.trim() || active}
                        onClick={() => void previewSql()}
                      >
                        <Eye size={16} aria-hidden="true" />
                        <span>{t("nl2sql.action.preview")}</span>
                      </Button>
                      <Button
                        type="button"
                        variant="primary"
                        size="md"
                        loading={jobActive}
                        disabled={!question.trim() || active}
                        onClick={() => void submit()}
                      >
                        <Play size={16} aria-hidden="true" />
                        <span>{t("nl2sql.action.run")}</span>
                      </Button>
                    </div>
                  </div>
                </>
              ) : (
                <section className="grid gap-4">
                  <div className="flex items-start gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                    <Code2 size={16} className="mt-0.5 text-slate-700" aria-hidden="true" />
                    <p>{t("nl2sql.sqlRunner.description")}</p>
                  </div>
                  <label className="grid gap-2 text-sm font-medium text-slate-800">
                    <span>{t("nl2sql.sqlRunner.label")}</span>
                    <textarea
                      aria-label={t("nl2sql.sqlRunner.label")}
                      ref={sqlTextareaRef}
                      value={sqlText}
                      onChange={(event) => setSqlText(event.currentTarget.value)}
                      disabled={active}
                      rows={9}
                      className="min-h-56 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      placeholder={t("nl2sql.sqlRunner.placeholder")}
                    />
                  </label>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                      <SqlFileInput
                        resetSignal={sqlFileResetSignal}
                        disabled={active}
                        onLoad={(text) => {
                          setSqlText(text);
                          setSqlExecutionResults(null);
                          setError("");
                        }}
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="min-h-11 sm:self-end"
                        disabled={!sqlText || active}
                        onClick={() => {
                          setSqlText("");
                          setSqlExecutionResults(null);
                          setError("");
                          setSqlFileResetSignal((value) => value + 1);
                        }}
                      >
                        <X size={15} aria-hidden="true" />
                        <span>{t("nl2sql.action.clearSql")}</span>
                      </Button>
                    </div>
                    <Button
                      type="button"
                      variant="primary"
                      size="md"
                      loading={sqlExecuteLoading}
                      disabled={!sqlText.trim() || active}
                      onClick={() => void executeSqlText()}
                    >
                      <Play size={16} aria-hidden="true" />
                      <span>{t("nl2sql.action.executeSql")}</span>
                    </Button>
                  </div>
                </section>
              )}
            </CardContent>
          </Card>

          {inputMode === "natural" && (
            <OperationStatusStrip job={job} elapsedSeconds={elapsedSeconds} />
          )}

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
              {error}
            </div>
          )}

          {inputMode === "natural" && (
            <GeneratedSqlPanel
              result={result ?? previewResult}
              mode={result ? "result" : previewResult ? "preview" : "result"}
              executeLoading={previewExecuteLoading}
              onExecute={previewResult ? () => void executePreviewSql() : undefined}
            />
          )}
          <Nl2SqlResultTable
            results={inputMode === "sql" ? sqlExecutionResults : result?.results ?? previewExecutionResults}
          />
          {inputMode === "natural" && (
            <FeedbackPanel result={result} history={latestHistory} onSaved={refreshHistory} />
          )}
        </section>
      </main>
    </>
  );
}
