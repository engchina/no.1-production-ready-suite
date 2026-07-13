import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpenText, ChevronDown, Code2, Database, Eye, History, Network, Play, RefreshCw, RotateCcw, Sparkles, Wand2, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  Banner,
  Button,
  PageHeader,
  toast,
} from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { formatNumber } from "@/lib/format";
import { SqlFileInput } from "./components/DbAdminShared";
import { DbObjectManagementStatusBar, DbObjectPanelHeader } from "./components/DbObjectManagementShared";
import { EngineSelector } from "./components/EngineSelector";
import { FeedbackPanel } from "./components/FeedbackPanel";
import { GeneratedSqlPanel } from "./components/GeneratedSqlPanel";
import { Nl2SqlResultTable } from "./components/Nl2SqlResultTable";
import { OperationStatusStrip } from "./components/OperationStatusStrip";
import { SchemaReferencePanel } from "./components/SchemaReferencePanel";
import { SelectAiFeedbackAddPanel } from "./components/SelectAiFeedbackAddPanel";
import { isJobInFlight } from "./jobPersistence";
import { engineLabel } from "./labels";
import { previewExecutePayload, previewToGeneratedSqlPanelData, sqlExecutePayload } from "./previewState";
import { prefillFromSearchParams } from "./queryPrefillState";
import { QUESTION_TEMPLATES } from "./questionTemplates";
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
  QueryResults,
  RewriteData,
  SchemaCatalog,
  SimilarHistoryData,
  SimilarHistoryItem,
} from "./types";
import { useNl2SqlJobPolling } from "./useNl2SqlJobPolling";
import { useOperationTimer } from "./useOperationTimer";
import {
  confirmQuerySessionSql,
  createOntologyImprovementProposal,
  createQuerySession,
  executeQuerySession,
  generateQuerySessionSql,
  getQuerySession,
  patchQuerySessionIntent,
  QuerySessionVersionConflictError,
} from "./ontology/api";
import { QueryOntologyFlow } from "./ontology/QueryOntologyFlow";
import {
  currentIntentVersionForSession,
  type GraphPatch,
  type QuerySession,
  type QuerySessionExecuteRequest,
  type QuerySessionGenerateSqlRequest,
  type QuerySessionSqlConfirmationRequest,
} from "./ontology/types";
import {
  emptySelection,
  insertTextAtRange,
  leadingNewlinePrefix,
  toAllowedObjects,
  toSchemaSelection,
  type SchemaSelection,
} from "./workbenchState";

type ActiveEditor = "natural" | "sql";

function lastMatchingHistory(history: HistoryItem[], result: Nl2SqlResult | null) {
  if (!result) return null;
  return history.find((item) => item.generated_sql === result.generated_sql) ?? null;
}

function queryResultsFromOntologySession(session: QuerySession): QueryResults | null {
  const value = session.result;
  if (!value || !Array.isArray(value.columns) || !Array.isArray(value.rows)) return null;
  return {
    columns: value.columns.map(String),
    rows: value.rows as Array<Record<string, unknown>>,
    total: typeof value.total === "number" ? value.total : value.rows.length,
  };
}

export function Nl2SqlWorkbench() {
  const [searchParams] = useSearchParams();
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [engine, setEngine] = useState<Nl2SqlEngine>("select_ai");
  const [activeEditor, setActiveEditor] = useState<ActiveEditor>("natural");
  const [profileId, setProfileId] = useState("default");
  const [question, setQuestion] = useState("");
  const [sqlText, setSqlText] = useState("");
  const [sqlFileResetSignal, setSqlFileResetSignal] = useState(0);
  const [selection, setSelection] = useState<SchemaSelection>(() => emptySelection());
  const [result, setResult] = useState<Nl2SqlResult | null>(null);
  const [previewResult, setPreviewResult] = useState<GeneratedSqlPanelData | null>(null);
  const [previewExecutionResults, setPreviewExecutionResults] = useState<QueryResults | null>(null);
  const [sqlExecutionResults, setSqlExecutionResults] = useState<QueryResults | null>(null);
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [similarHistory, setSimilarHistory] = useState<SimilarHistoryItem[]>([]);
  const [similarHistoryLoading, setSimilarHistoryLoading] = useState(false);
  const [rewriteData, setRewriteData] = useState<RewriteData | null>(null);
  const [rewriteLoading, setRewriteLoading] = useState(false);
  const [ontologySession, setOntologySession] = useState<QuerySession | null>(null);
  const [ontologyLoading, setOntologyLoading] = useState(false);
  const [ontologyPatch, setOntologyPatch] = useState<GraphPatch | null>(null);
  const [ontologyVersionConflict, setOntologyVersionConflict] = useState<{
    baseVersion: number;
    currentVersion: number;
    message?: string;
  } | null>(null);
  const [ontologyExecutionResults, setOntologyExecutionResults] = useState<QueryResults | null>(null);
  const [rewriteUseGlossary, setRewriteUseGlossary] = useState(false);
  const [rewriteUseSchema, setRewriteUseSchema] = useState(false);
  const [rewriteExtraPrompt, setRewriteExtraPrompt] = useState("");
  const [selectAiAdvancedOpen, setSelectAiAdvancedOpen] = useState(false);
  const [selectAiRoleOverride, setSelectAiRoleOverride] = useState("");
  const [selectAiInstructionsOverride, setSelectAiInstructionsOverride] = useState("");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewExecuteLoading, setPreviewExecuteLoading] = useState(false);
  const [sqlExecuteLoading, setSqlExecuteLoading] = useState(false);
  const [error, setError] = useState("");
  const [importingSample, setImportingSample] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const questionTextareaRef = useRef<HTMLTextAreaElement>(null);
  const sqlTextareaRef = useRef<HTMLTextAreaElement>(null);

  // refresh=true のときは catalog を実データソースから再取得する（oracle モードは Oracle を再クエリ）。
  const loadCatalog = useCallback(async (refresh = false) => {
    setLoadingCatalog(true);
    try {
      const [catalogData, profilesData, historyData] = await Promise.all([
        refresh
          ? apiPost<SchemaCatalog>("/api/schema/refresh", {})
          : apiGet<SchemaCatalog>("/api/schema/catalog"),
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

  // job エラーは OperationStatusStrip（job.error_message）へ一本化して表示する。
  // ページ共通 Banner（catalog/preview/execute 用）には流さず、重複表示を避ける。
  const handleJobError = useCallback(() => {}, []);

  const { job, jobStartedAt, pollJob, trackJob, clearTrackedJob } = useNl2SqlJobPolling({
    onResult: handleJobResult,
    onError: handleJobError,
    onHistoryRefresh: refreshHistory,
  });
  const jobActive = isJobInFlight(job?.status) || submitting;
  const active = jobActive || previewLoading || previewExecuteLoading || sqlExecuteLoading || ontologyLoading;
  const elapsedSeconds = useOperationTimer(jobActive, jobStartedAt);
  const latestHistory = useMemo(() => lastMatchingHistory(history, result), [history, result]);
  const selectAiOverrides = useMemo(() => {
    if (engine !== "select_ai") return null;
    const role = selectAiRoleOverride.trim();
    const additionalInstructions = selectAiInstructionsOverride.trim();
    if (!role && !additionalInstructions) return null;
    return {
      role,
      additional_instructions: additionalInstructions,
    };
  }, [engine, selectAiInstructionsOverride, selectAiRoleOverride]);
  const selectedProfile = profiles.find((profile) => profile.id === profileId) ?? null;
  const selectedProfileName = selectedProfile?.name || t("nl2sql.workspace.profileUnavailable");
  const profileAllowedTableNames = useMemo(() => {
    if (!selectedProfile) return null;
    const names = [...selectedProfile.allowed_tables, ...selectedProfile.allowed_views];
    return names.length > 0 ? names : null;
  }, [selectedProfile]);

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
    if (trimmed.length < 4 || active || profiles.length === 0) {
      setRecommendation(null);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void apiPost<ProfileRecommendationData>("/api/nl2sql/recommend-profile", {
        question: trimmed,
        current_profile_id: profileId || null,
      })
        .then((data) => {
          if (!cancelled) setRecommendation(data);
        })
        .catch(() => {
          if (!cancelled) setRecommendation(null);
        });
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [active, profileId, profiles.length, question]);

  useEffect(() => {
    const trimmed = question.trim();
    if (trimmed.length < 4 || active) {
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
  }, [active, profileId, question]);

  const insertSchemaText = (text: string) => {
    const el = activeEditor === "sql" ? sqlTextareaRef.current : questionTextareaRef.current;
    if (!el) {
      // ref 未取得（フォーカス外）のときは末尾へ追記。各項目を改行区切りにする。
      if (activeEditor === "sql") {
        setSqlText((current) => `${current}${leadingNewlinePrefix(current, current.length)}${text}`);
      } else {
        setQuestion((current) => `${current}${leadingNewlinePrefix(current, current.length)}${text}`);
      }
      return;
    }
    const source = activeEditor === "sql" ? sqlText : question;
    const start = el.selectionStart ?? source.length;
    const end = el.selectionEnd ?? source.length;
    // 各項目を改行区切りにする（先頭・直前が改行のときは付けない）。
    const prefixed = `${leadingNewlinePrefix(source, start)}${text}`;
    const nextValue = insertTextAtRange(source, prefixed, start, end);
    if (activeEditor === "sql") {
      setSqlText(nextValue);
    } else {
      setQuestion(nextValue);
    }
    requestAnimationFrame(() => {
      // preventScroll: 挿入クリックのたびにページが textarea まで飛ばないようにする。
      el.focus({ preventScroll: true });
      el.setSelectionRange(start + prefixed.length, start + prefixed.length);
      // textarea 内部だけ、いま挿入した行へ追従スクロールする。
      // ponytail: 折返し行は無視した近似。挿入項目は 1 行想定で実用十分。
      const caretLine = nextValue.slice(0, start + prefixed.length).split("\n").length;
      const lineHeight = 24; // leading-6
      el.scrollTop = Math.max(0, caretLine * lineHeight - el.clientHeight + lineHeight);
    });
  };

  const applyRecommendation = () => {
    if (!recommendation) return;
    setProfileId(recommendation.recommended_profile_id);
    setSelection(toSchemaSelection(recommendation.recommended_allowed_objects));
  };

  // 質問から業務プロファイルを自動判定（学習済み分類器 → 決定論フォールバック）して選択する。
  const detectProfile = useCallback(async () => {
    const trimmed = question.trim();
    if (!trimmed || active) return;
    setDetecting(true);
    try {
      const data = await apiPost<ProfileRecommendationData>("/api/nl2sql/recommend-profile", {
        question: trimmed,
        current_profile_id: profileId || null,
      });
      setRecommendation(data);
      setProfileId(data.recommended_profile_id);
      setSelection(toSchemaSelection(data.recommended_allowed_objects));
      const sourceLabel =
        data.recommendation_source === "classifier"
          ? t("nl2sql.recommend.sourceClassifier")
          : t("nl2sql.recommend.sourceDeterministic");
      toast.success(
        t("nl2sql.recommend.autoDetectApplied", {
          name: data.recommended_profile_name,
          source: sourceLabel,
          confidence: Math.round(data.confidence * 100),
        })
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("nl2sql.recommend.autoDetectFailed"));
    } finally {
      setDetecting(false);
    }
  }, [active, profileId, question]);

  // schema catalog が空（サンプル未投入）のとき、エラーからワンクリックで投入して解消する。
  const importSampleData = useCallback(async () => {
    setImportingSample(true);
    try {
      await apiPost("/api/nl2sql/sample-data/import", { step: "all", confirmation: "SQL_ASSIST_SAMPLE" });
      toast.success(t("nl2sql.sample.importSuccess"));
      setError("");
      clearTrackedJob();
      await loadCatalog();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("nl2sql.sample.importFailed"));
    } finally {
      setImportingSample(false);
    }
  }, [clearTrackedJob, loadCatalog]);

  const previewSql = async () => {
    const trimmed = question.trim();
    if (!trimmed || active) return;
    setPreviewLoading(true);
    setError("");
    clearTrackedJob();
    setResult(null);
    setSqlExecutionResults(null);
    try {
      const data = await apiPost<PreviewData>("/api/nl2sql/preview", {
        question: trimmed,
        engine,
        profile_id: profileId || null,
        allowed_objects: toAllowedObjects(selection),
        select_ai_overrides: selectAiOverrides,
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
        ...previewExecutePayload(sql, profileId || null, toAllowedObjects(selection)),
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
        ...sqlExecutePayload(trimmed, profileId || null, toAllowedObjects(selection)),
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
      if (ontologySession) {
        setOntologyPatch({
          base_version: currentIntentVersionForSession(ontologySession),
          summary_ja: "提案された質問を現在のセッションへ適用",
          operations: [
            {
              op: "replace",
              path: "/question_effective",
              value: data.rewritten_question,
              reason_ja: "質問の最適化案（適用前）",
            },
          ],
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.rewriteFailed"));
      setRewriteData(null);
    } finally {
      setRewriteLoading(false);
    }
  };

  const updateOntologySession = (session: QuerySession) => {
    setOntologySession(session);
    setOntologyExecutionResults(queryResultsFromOntologySession(session));
    setOntologyVersionConflict(null);
  };

  const startOntologySession = async () => {
    const trimmed = question.trim();
    if (!trimmed || active) return;
    setOntologyLoading(true);
    setError("");
    setOntologyPatch(null);
    setOntologyExecutionResults(null);
    try {
      updateOntologySession(
        await createQuerySession({
          question: trimmed,
          profile_id: profileId,
          allowed_objects: toAllowedObjects(selection),
        })
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.previewFailed"));
    } finally {
      setOntologyLoading(false);
    }
  };

  const applyOntologyPatch = async (patch: GraphPatch) => {
    if (!ontologySession) return;
    setOntologyLoading(true);
    try {
      updateOntologySession(await patchQuerySessionIntent(ontologySession.id, patch));
      setOntologyPatch(null);
    } catch (err) {
      if (err instanceof QuerySessionVersionConflictError) {
        setOntologyVersionConflict({
          baseVersion: patch.base_version,
          currentVersion: err.currentVersion ?? currentIntentVersionForSession(ontologySession),
          message: err.message,
        });
        if (err.session) setOntologySession(err.session);
        return;
      }
      throw err;
    } finally {
      setOntologyLoading(false);
    }
  };

  const confirmOntologyIntent = async (request: QuerySessionGenerateSqlRequest) => {
    if (!ontologySession) return;
    setOntologyLoading(true);
    try {
      updateOntologySession(await generateQuerySessionSql(ontologySession.id, request));
    } finally {
      setOntologyLoading(false);
    }
  };

  const confirmOntologySql = async (request: QuerySessionSqlConfirmationRequest) => {
    if (!ontologySession) return;
    setOntologyLoading(true);
    try {
      updateOntologySession(await confirmQuerySessionSql(ontologySession.id, request));
    } finally {
      setOntologyLoading(false);
    }
  };

  const executeOntologySql = async (request: QuerySessionExecuteRequest) => {
    if (!ontologySession) return;
    setOntologyLoading(true);
    try {
      updateOntologySession(await executeQuerySession(ontologySession.id, request));
    } finally {
      setOntologyLoading(false);
    }
  };

  const reloadOntologySession = async () => {
    if (!ontologySession) return;
    updateOntologySession(await getQuerySession(ontologySession.id));
  };

  const applyRewrittenQuestion = async () => {
    if (!rewriteData) return;
    if (!ontologySession) {
      setQuestion(rewriteData.rewritten_question);
      return;
    }
    const patch: GraphPatch = ontologyPatch ?? {
      base_version: currentIntentVersionForSession(ontologySession),
      summary_ja: "提案された質問を現在のセッションへ適用",
      operations: [
        {
          op: "replace",
          path: "/question_effective",
          value: rewriteData.rewritten_question,
          reason_ja: "ユーザーが質問の最適化案を確認して適用",
        },
      ],
    };
    setOntologyPatch(patch);
    await applyOntologyPatch(patch);
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
        select_ai_overrides: selectAiOverrides,
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
      <PageHeader title={t("nav.query")} subtitle={t("page.query.subtitle")} />

      <div className="grid gap-4 p-4 lg:p-8">
        <DbObjectManagementStatusBar
          ariaLabel={t("nl2sql.workspace.statusLabel")}
          metrics={[
            {
              label: t("nl2sql.workspace.availableTables"),
              value: formatNumber(catalog?.tables.length ?? 0),
              emphasis: true,
            },
            {
              label: t("nl2sql.workspace.selectedTables"),
              value: t("nl2sql.workspace.tableCount", { count: selection.tableNames.length }),
            },
            { label: t("nl2sql.profile.label"), value: selectedProfileName },
            { label: t("nl2sql.engine.label"), value: engineLabel(engine) },
          ]}
          metricColumnsClass="sm:grid-cols-2 lg:grid-cols-4"
          actions={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loadingCatalog}
              disabled={active}
              onClick={() => void loadCatalog(true)}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("nl2sql.action.refresh")}</span>
            </Button>
          }
        />

        <section
          className="grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm"
          aria-label={t("nl2sql.workspace.label")}
          data-testid="nl2sql-workspace-shell"
        >
          <section className="grid min-w-0 content-start gap-4" aria-labelledby="nl2sql-query-heading">
                <DbObjectPanelHeader
                  headingId="nl2sql-query-heading"
                  icon={Sparkles}
                  title={t("nl2sql.workbench.title")}
                  description={t("nl2sql.workbench.description")}
                />
                <div className="space-y-5">
                  <EngineSelector value={engine} onChange={setEngine} disabled={active} />

              {engine === "select_ai" && (
                <section className="overflow-hidden rounded-md border border-border bg-background">
                  <Button
                    type="button"
                    variant="ghost"
                    size="md"
                    className="min-h-11 w-full justify-between rounded-none px-4 text-left"
                    aria-expanded={selectAiAdvancedOpen}
                    aria-controls="select-ai-request-overrides"
                    onClick={() => setSelectAiAdvancedOpen((current) => !current)}
                    disabled={active}
                  >
                    <span>{t("nl2sql.selectAiOverrides.title")}</span>
                    <ChevronDown
                      size={16}
                      className={selectAiAdvancedOpen ? "rotate-180 transition-transform" : "transition-transform"}
                      aria-hidden="true"
                    />
                  </Button>
                  {selectAiAdvancedOpen && (
                    <div id="select-ai-request-overrides" className="grid gap-4 border-t border-border p-4">
                      <p className="text-sm leading-6 text-muted">
                        {t("nl2sql.selectAiOverrides.hint")}
                      </p>
                      <label className="grid gap-1 text-sm font-medium text-foreground">
                        <span>{t("nl2sql.selectAiOverrides.role")}</span>
                        <textarea
                          value={selectAiRoleOverride}
                          onChange={(event) => setSelectAiRoleOverride(event.currentTarget.value)}
                          disabled={active}
                          rows={3}
                          className="rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                          placeholder={t("nl2sql.selectAiOverrides.rolePlaceholder")}
                        />
                      </label>
                      <label className="grid gap-1 text-sm font-medium text-foreground">
                        <span>{t("nl2sql.selectAiOverrides.additionalInstructions")}</span>
                        <textarea
                          value={selectAiInstructionsOverride}
                          onChange={(event) => setSelectAiInstructionsOverride(event.currentTarget.value)}
                          disabled={active}
                          rows={5}
                          className="rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                          placeholder={t("nl2sql.selectAiOverrides.additionalInstructionsPlaceholder")}
                        />
                      </label>
                    </div>
                  )}
                </section>
              )}

              <div className="grid gap-4">
                <div className="grid gap-1">
                  <label
                    htmlFor="nl2sql-profile-select"
                    className="text-sm font-medium text-foreground"
                  >
                    {t("nl2sql.profile.label")}
                  </label>
                  <div className="flex flex-wrap items-stretch gap-2">
                    <select
                      id="nl2sql-profile-select"
                      value={profileId}
                      onChange={(event) => setProfileId(event.currentTarget.value)}
                      disabled={active}
                      className="min-h-11 min-w-0 flex-1 rounded-md border border-border bg-card px-3 py-2 text-sm focus:border-primary focus:ring-2 focus:ring-ring/40"
                    >
                      {profiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.name}
                        </option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      variant="secondary"
                      size="md"
                      className="shrink-0"
                      loading={detecting}
                      disabled={!question.trim() || active}
                      onClick={() => void detectProfile()}
                    >
                      <Wand2 size={16} aria-hidden="true" />
                      <span>{t("nl2sql.recommend.autoDetect")}</span>
                    </Button>
                  </div>
                </div>
                {recommendation &&
                  recommendation.confidence >= 0.3 &&
                  recommendation.recommended_profile_id !== profileId && (
                    <div
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm"
                      data-testid="nl2sql-recommend-hint"
                    >
                      <span className="flex min-w-0 items-center gap-2 text-foreground">
                        <Sparkles size={15} className="shrink-0 text-primary" aria-hidden="true" />
                        <span className="min-w-0 [overflow-wrap:anywhere]">
                          {t("nl2sql.recommend.switchHint", {
                            name: recommendation.recommended_profile_name,
                            confidence: Math.round(recommendation.confidence * 100),
                          })}
                        </span>
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={active}
                        onClick={applyRecommendation}
                      >
                        {t("nl2sql.recommend.switchApply")}
                      </Button>
                    </div>
                  )}
              </div>

                  {/* 検索クエリ（左）× スキーマ参照（右・常時表示）: 書きながら参照して即クリック挿入。 */}
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start">
                    <div className="grid gap-2">
                      {/* SQL 一括実行(StatementRunnerCard)とスタイル・挙動を統一したテンプレート行(全置換) */}
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-medium text-muted">{t("dbAdmin.runner.templates")}</span>
                        {QUESTION_TEMPLATES.map((template) => (
                          <Button
                            key={template.labelKey}
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={active}
                            onClick={() => {
                              setQuestion(template.body);
                              setActiveEditor("natural");
                              const el = questionTextareaRef.current;
                              if (el) {
                                requestAnimationFrame(() => {
                                  el.focus({ preventScroll: true });
                                  // 1 行目「対象テーブル：」の直後にカーソルを置き、すぐ記入できるようにする
                                  const firstLineEnd = template.body.indexOf("\n");
                                  const caret = firstLineEnd === -1 ? template.body.length : firstLineEnd;
                                  el.setSelectionRange(caret, caret);
                                });
                              }
                            }}
                          >
                            {t(template.labelKey)}
                          </Button>
                        ))}
                      </div>
                      <label className="grid gap-2 text-sm font-medium text-foreground">
                        <span>{t("nl2sql.question.label")}</span>
                        <textarea
                          ref={questionTextareaRef}
                          value={question}
                          onChange={(event) => setQuestion(event.currentTarget.value)}
                          onFocus={() => setActiveEditor("natural")}
                          disabled={active}
                          rows={5}
                          className="field-sizing-content min-h-36 max-h-[16.5rem] rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                          placeholder={t("nl2sql.question.placeholder")}
                        />
                      </label>
                    </div>
                    <div className="rounded-md border border-border bg-background p-3">
                      <SchemaReferencePanel
                        catalog={catalog}
                        loading={loadingCatalog}
                        disabled={active}
                        insertMode={activeEditor === "sql" ? "physical" : "logical"}
                        allowedTableNames={profileAllowedTableNames}
                        listMaxHeightClass="max-h-[30rem]"
                        onRefreshSchema={() => void loadCatalog(true)}
                        refreshing={loadingCatalog}
                        onInsert={insertSchemaText}
                      />
                    </div>
                  </div>

                  <section className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="font-semibold text-foreground">{t("nl2sql.rewrite.title")}</p>
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
                      <label className="flex min-h-11 items-start gap-3 rounded-md border border-border bg-card p-3 text-foreground">
                        <input
                          type="checkbox"
                          checked={rewriteUseGlossary}
                          onChange={(event) => setRewriteUseGlossary(event.currentTarget.checked)}
                          className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
                        />
                        <span>{t("nl2sql.rewrite.useGlossary")}</span>
                      </label>
                      <label className="flex min-h-11 items-start gap-3 rounded-md border border-border bg-card p-3 text-foreground">
                        <input
                          type="checkbox"
                          checked={rewriteUseSchema}
                          onChange={(event) => setRewriteUseSchema(event.currentTarget.checked)}
                          className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
                        />
                        <span>{t("nl2sql.rewrite.useSchema")}</span>
                      </label>
                    </div>
                    <label className="grid gap-1 font-medium text-foreground">
                      <span>{t("nl2sql.rewrite.extraPrompt")}</span>
                      <input
                        value={rewriteExtraPrompt}
                        onChange={(event) => setRewriteExtraPrompt(event.currentTarget.value)}
                        disabled={active}
                        className="min-h-11 rounded-md border border-border bg-card px-3 py-2 text-sm focus:border-primary focus:ring-2 focus:ring-ring/40"
                      />
                    </label>
                    {rewriteData && (
                      <div className="grid gap-3 rounded-md border border-primary/30 bg-card p-3">
                        <dl className="grid gap-2 text-sm">
                          <div>
                            <dt className="font-medium text-muted">{t("nl2sql.session.originalQuestion")}</dt>
                            <dd className="mt-1 text-foreground">{question}</dd>
                          </div>
                          <div>
                            <dt className="font-medium text-muted">{t("nl2sql.session.suggestedQuestion")}</dt>
                            <dd className="mt-1 text-foreground">{rewriteData.rewritten_question}</dd>
                          </div>
                        </dl>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-md bg-muted/30 px-2 py-1 text-xs font-medium text-foreground">
                            {rewriteData.source}
                          </span>
                          {rewriteData.model && (
                            <span className="rounded-md bg-muted/30 px-2 py-1 text-xs font-medium text-foreground">
                              {rewriteData.model}
                            </span>
                          )}
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            disabled={active}
                            onClick={() => void applyRewrittenQuestion()}
                          >
                            {t("nl2sql.session.applyPatch")}
                          </Button>
                        </div>
                      </div>
                    )}
                  </section>

                  {(similarHistory.length > 0 || similarHistoryLoading) && (
                    <div className="grid gap-3 rounded-md border border-border bg-card p-3 text-sm text-foreground">
                      <div className="flex items-center gap-2">
                        <BookOpenText size={16} className="text-foreground" aria-hidden="true" />
                        <p className="font-semibold text-foreground">
                          {similarHistoryLoading
                            ? t("nl2sql.similar.loading")
                            : t("nl2sql.similar.title")}
                        </p>
                      </div>
                      {similarHistory.slice(0, 2).map((entry) => (
                        <article key={entry.item.id} className="grid gap-2 rounded-md bg-background p-3">
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <div>
                              <p className="font-medium text-foreground">{entry.item.question}</p>
                              <p className="mt-1 text-xs text-muted">{entry.reason}</p>
                            </div>
                            <span className="rounded-md bg-card px-2 py-1 text-xs font-medium text-foreground">
                              {t("nl2sql.similar.score", {
                                score: Math.round(entry.score * 100),
                              })}
                            </span>
                          </div>
                          <pre className="max-h-28 overflow-auto rounded-md border border-border bg-card p-2 text-xs leading-5 text-foreground">
                            <code>{entry.item.executable_sql || entry.item.generated_sql}</code>
                          </pre>
                        </article>
                      ))}
                    </div>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm text-muted">
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
                          setRewriteUseGlossary(false);
                          setRewriteUseSchema(false);
                          setRewriteExtraPrompt("");
                          clearTrackedJob();
                          setActiveEditor("natural");
                          setSelectAiRoleOverride("");
                          setSelectAiInstructionsOverride("");
                          setSelectAiAdvancedOpen(false);
                          setOntologySession(null);
                          setOntologyPatch(null);
                          setOntologyVersionConflict(null);
                          setOntologyExecutionResults(null);
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
                        loading={ontologyLoading}
                        disabled={!question.trim() || active}
                        onClick={() => void startOntologySession()}
                      >
                        <Network size={16} aria-hidden="true" />
                        <span>{t("nl2sql.session.create")}</span>
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
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

                <details
                  className="group overflow-hidden rounded-md border border-border bg-card"
                  data-testid="nl2sql-direct-sql"
                  onToggle={(event) => setActiveEditor(event.currentTarget.open ? "sql" : "natural")}
                >
                  <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 bg-background px-4 py-3 text-sm font-semibold text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden">
                    <span className="flex min-w-0 items-center gap-2">
                      <Code2 size={16} className="shrink-0 text-foreground" aria-hidden="true" />
                      <span>{t("nl2sql.sqlRunner.disclosure")}</span>
                    </span>
                    <ChevronDown
                      size={16}
                      className="shrink-0 text-muted transition-transform duration-200 group-open:rotate-180 motion-reduce:transition-none"
                      aria-hidden="true"
                    />
                  </summary>
                <section className="grid gap-4 border-t border-border p-4">
                  <div className="flex items-start gap-2 rounded-md border border-border bg-background p-3 text-sm text-foreground">
                    <Code2 size={16} className="mt-0.5 text-foreground" aria-hidden="true" />
                    <p>{t("nl2sql.sqlRunner.description")}</p>
                  </div>
                  <label className="grid gap-2 text-sm font-medium text-foreground">
                    <span>{t("nl2sql.sqlRunner.label")}</span>
                    <textarea
                      aria-label={t("nl2sql.sqlRunner.label")}
                      ref={sqlTextareaRef}
                      value={sqlText}
                      onChange={(event) => setSqlText(event.currentTarget.value)}
                      onFocus={() => setActiveEditor("sql")}
                      disabled={active}
                      rows={9}
                      className="min-h-56 rounded-md border border-border bg-card px-3 py-2 font-mono text-xs leading-5 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
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
                  <Nl2SqlResultTable results={sqlExecutionResults} />
                </section>
                </details>
                </div>
              </section>
        </section>

        {ontologySession && (
          <QueryOntologyFlow
            session={ontologySession}
            pendingPatch={ontologyPatch}
            versionConflict={ontologyVersionConflict}
            onApplyIntentPatch={applyOntologyPatch}
            onConfirmIntent={confirmOntologyIntent}
            onConfirmSql={confirmOntologySql}
            onExecute={executeOntologySql}
            onCreateProposal={async (request) => {
              await createOntologyImprovementProposal(ontologySession.id, request);
              await reloadOntologySession();
            }}
            onReload={reloadOntologySession}
            labels={{
              businessTab: t("nl2sql.ontology.tab.business"),
              intentTab: t("nl2sql.ontology.tab.intent"),
              sqlTab: t("nl2sql.ontology.tab.sql"),
              validationTab: t("nl2sql.ontology.tab.diff"),
              applyPatch: t("nl2sql.session.applyPatch"),
              confirmIntent: t("nl2sql.session.generateSql"),
              confirmSql: t("nl2sql.session.confirmSql"),
              execute: t("nl2sql.session.execute"),
              proposal: t("nl2sql.session.propose"),
              rawSql: t("nl2sql.session.sqlDetails"),
            }}
          />
        )}

        <OperationStatusStrip
          job={job}
          elapsedSeconds={elapsedSeconds}
          catalogEmpty={catalog !== null && catalog.tables.length === 0}
          importingSample={importingSample}
          onImportSample={importSampleData}
        />
        <Nl2SqlResultTable results={ontologyExecutionResults} />

        {error && (
          <Banner
            severity="danger"
            action={
              (catalog?.tables.length ?? 0) === 0 ? (
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  loading={importingSample}
                  disabled={active}
                  onClick={() => void importSampleData()}
                >
                  <Database size={15} aria-hidden="true" />
                  <span>{t("nl2sql.sample.import")}</span>
                </Button>
              ) : (
                <Button type="button" variant="secondary" size="sm" onClick={() => void loadCatalog()}>
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>{t("nl2sql.action.refresh")}</span>
                </Button>
              )
            }
          >
            {error} {t("nl2sql.error.retryHint")}
          </Banner>
        )}

        <GeneratedSqlPanel
          result={result ?? previewResult}
          mode={result ? "result" : previewResult ? "preview" : "result"}
          executeLoading={previewExecuteLoading}
          onExecute={previewResult ? () => void executePreviewSql() : undefined}
        />
        <Nl2SqlResultTable results={result?.results ?? previewExecutionResults} />
        <FeedbackPanel result={result} history={latestHistory} onSaved={refreshHistory} />
        <SelectAiFeedbackAddPanel
          result={result ?? previewResult}
          history={latestHistory}
          selectedProfileId={profileId}
          questionText={question}
        />
      </div>
    </>
  );
}
