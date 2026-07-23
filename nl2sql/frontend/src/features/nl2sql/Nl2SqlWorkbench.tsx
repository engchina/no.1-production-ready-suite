import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { BookOpenText, ChevronDown, Database, Eye, History, Network, Play, RefreshCw, RotateCcw, Sparkles, Wand2 } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  Banner,
  Button,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { PageNotice } from "@/components/page-notice";
import { useAuth } from "@/features/security/AuthProvider";
import { apiGet, apiPost, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/format";
import { DbObjectPanelHeader } from "./components/DbObjectManagementShared";
import { EngineSelector } from "./components/EngineSelector";
import { Nl2SqlResultTable } from "./components/Nl2SqlResultTable";
import { OperationStatusStrip } from "./components/OperationStatusStrip";
import { SchemaReferencePanel } from "./components/SchemaReferencePanel";
import {
  getSchemaObjectDetail,
  useProfileDetail,
  useProfileSummaries,
  useSchemaCatalogHead,
  useSchemaObjects,
  useSchemaRefreshJob,
  useStartSchemaRefresh,
} from "./incrementalQueries";
import { SelectAiFeedbackAddPanel } from "./components/SelectAiFeedbackAddPanel";
import { isJobInFlight } from "./jobPersistence";
import { previewExecutePayload, previewToJob } from "./previewState";
import { prefillFromSearchParams } from "./queryPrefillState";
import { QUESTION_TEMPLATES } from "./questionTemplates";
import type {
  HistoryData,
  HistoryItem,
  JobCreateData,
  JobData,
  Nl2SqlEngine,
  Nl2SqlResult,
  PreviewData,
  ProfileRecommendationData,
  QueryResults,
  RewriteData,
  SchemaCatalog,
  SchemaObjectDetail,
  SchemaTable,
  SimilarHistoryData,
  SimilarHistoryItem,
} from "./types";
import { useNl2SqlJobPolling } from "./useNl2SqlJobPolling";
import { useOperationTimer } from "./useOperationTimer";
import {
  confirmQuerySessionSql,
  confirmOntologyProfileRecommendation,
  createOntologyImprovementProposal,
  createQuerySession,
  executeQuerySession,
  generateQuerySessionSql,
  getQuerySession,
  patchQuerySessionIntent,
  recommendOntologyProfiles,
  QuerySessionVersionConflictError,
} from "./ontology/api";
import { QueryOntologyFlow } from "./ontology/QueryOntologyFlow";
import {
  currentIntentVersionForSession,
  type GraphPatch,
  type OntologyProfileRecommendation,
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
  const { hasPermission } = useAuth();
  const canExecute = hasPermission("search.execute");
  if (!canExecute) {
    return (
      <>
        <PageHeader title={t("nav.query")} subtitle={t("page.query.subtitle")} />
        <main className="p-4 lg:p-8">
          <Banner severity="info">{t("nl2sql.permission.executeRequired")}</Banner>
        </main>
      </>
    );
  }
  return <ExecutableNl2SqlWorkbench />;
}

function ExecutableNl2SqlWorkbench() {
  const [searchParams] = useSearchParams();
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [engine, setEngine] = useState<Nl2SqlEngine>("select_ai");
  const [profileId, setProfileId] = useState("default");
  const [question, setQuestion] = useState("");
  const [selection, setSelection] = useState<SchemaSelection>(() => emptySelection());
  const [result, setResult] = useState<Nl2SqlResult | null>(null);
  const [previewJob, setPreviewJob] = useState<JobData | null>(null);
  const [previewExecutionResults, setPreviewExecutionResults] = useState<QueryResults | null>(null);
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [similarHistory, setSimilarHistory] = useState<SimilarHistoryItem[]>([]);
  const [similarHistoryLoading, setSimilarHistoryLoading] = useState(false);
  const [rewriteData, setRewriteData] = useState<RewriteData | null>(null);
  const [ontologySession, setOntologySession] = useState<QuerySession | null>(null);
  const [ontologyProfileRecommendation, setOntologyProfileRecommendation] =
    useState<OntologyProfileRecommendation | null>(null);
  const [ontologyProfileConfirmation, setOntologyProfileConfirmation] = useState<{
    question: string;
    profileId: string;
    revisionId: string;
    token: string;
  } | null>(null);
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
  const [similarHistoryOpen, setSimilarHistoryOpen] = useState(false);
  const [selectAiRoleOverride, setSelectAiRoleOverride] = useState("");
  const [selectAiInstructionsOverride, setSelectAiInstructionsOverride] = useState("");
  const [schemaSearch, setSchemaSearch] = useState("");
  const [schemaDetails, setSchemaDetails] = useState<Record<string, SchemaObjectDetail>>({});
  const [schemaRefreshJobId, setSchemaRefreshJobId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewExecuteLoading, setPreviewExecuteLoading] = useState(false);
  const [error, setError] = useState("");
  const [importingSample, setImportingSample] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const questionTextareaRef = useRef<HTMLTextAreaElement>(null);
  const schemaDetailRequests = useRef(new Set<string>());
  const reportedSchemaRefreshJob = useRef("");

  useLayoutEffect(() => {
    const textarea = questionTextareaRef.current;
    if (!textarea) return;
    const minHeight = 144;
    const maxHeight = 266;
    textarea.style.height = "0px";
    const contentHeight = textarea.scrollHeight;
    textarea.style.height = `${Math.min(maxHeight, Math.max(minHeight, contentHeight))}px`;
    textarea.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
  }, [question]);

  const profilesQuery = useProfileSummaries("");
  const profiles = useMemo(
    () => profilesQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [profilesQuery.data]
  );
  const selectedProfileQuery = useProfileDetail(profileId);
  const schemaObjectsQuery = useSchemaObjects(schemaSearch, "", profileId);
  const schemaHeadQuery = useSchemaCatalogHead();
  const startSchemaRefresh = useStartSchemaRefresh();
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshStatus = schemaRefreshJobQuery.isError
    ? "error"
    : (schemaRefreshJobQuery.data?.status ?? "");
  const catalog = useMemo<SchemaCatalog>(() => {
    const objects = schemaObjectsQuery.data?.pages.flatMap((page) => page.items) ?? [];
    return {
      refreshed_at: schemaHeadQuery.data?.refreshed_at ?? "",
      schema_fingerprint: schemaHeadQuery.data?.schema_fingerprint ?? "",
      tables: objects.map((object) => {
        const key = `${object.owner}.${object.object_name}`.toUpperCase();
        return (
          schemaDetails[key]?.table ?? {
            table_name: object.object_name,
            qualified_name: `${object.owner}.${object.object_name}`,
            logical_name: object.logical_name,
            owner: object.owner,
            table_type: object.object_type,
            comment: object.comment,
            row_count: object.row_count,
            columns: [],
            constraints: [],
          }
        );
      }),
    };
  }, [schemaDetails, schemaHeadQuery.data, schemaObjectsQuery.data]);
  const loadingCatalog = schemaObjectsQuery.isPending || schemaObjectsQuery.isFetching;

  // 画面 entry は summary/object page だけを独立取得する。refresh は persistent job を投入し、
  // 前 catalog を表示したまま job 完了時の query invalidation を待つ。
  const loadCatalog = useCallback(
    async (refresh = false, announce = false) => {
      try {
        if (refresh) {
          const job = await startSchemaRefresh.mutateAsync();
          reportedSchemaRefreshJob.current = "";
          setSchemaRefreshJobId(job.job_id);
          return;
        }
        const results = await Promise.allSettled([
          schemaObjectsQuery.refetch(),
          schemaHeadQuery.refetch(),
          profilesQuery.refetch(),
        ]);
        if (
          announce &&
          results.every(
            (result) => result.status === "fulfilled" && !result.value.isError
          )
        ) {
          toast.success(t("common.action.refreshed"));
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : t("nl2sql.error.loadFailed"));
      }
    },
    [profilesQuery, schemaHeadQuery, schemaObjectsQuery, startSchemaRefresh]
  );

  useEffect(() => {
    const job = schemaRefreshJobQuery.data;
    if (!job || !["done", "error"].includes(job.status)) return;
    const reportKey = `${job.job_id}:${job.status}`;
    if (reportedSchemaRefreshJob.current === reportKey) return;
    reportedSchemaRefreshJob.current = reportKey;
    if (job.status === "done") {
      toast.success(t("common.action.schemaRefreshed"));
    } else {
      toast.error(t("profiles.schemaRefresh.error"));
    }
  }, [schemaRefreshJobQuery.data]);

  const refreshHistory = useCallback(async () => {
    const historyData = await apiGet<HistoryData>("/api/nl2sql/history");
    setHistory(historyData.items);
  }, []);

  const handleJobResult = useCallback((data: Nl2SqlResult) => {
    setPreviewJob(null);
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
  const active = jobActive || previewLoading || previewExecuteLoading || ontologyLoading;
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
  const selectedProfile = selectedProfileQuery.data?.profile ?? null;
  const profileAllowedTableNames = useMemo(() => {
    if (!selectedProfile) return null;
    const names = [...selectedProfile.allowed_tables, ...selectedProfile.allowed_views];
    return names.length > 0 ? names : null;
  }, [selectedProfile]);

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
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void apiPost<ProfileRecommendationData>("/api/nl2sql/recommend-profile", {
        question: trimmed,
        current_profile_id: profileId || null,
      }, { signal: controller.signal })
        .then((data) => {
          if (!controller.signal.aborted) setRecommendation(data);
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted && !isAbortError(cause)) {
            setRecommendation(null);
          }
        });
    }, 500);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [active, profileId, profiles.length, question]);

  useEffect(() => {
    const trimmed = question.trim();
    if (trimmed.length < 4 || active) {
      setSimilarHistory([]);
      return undefined;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSimilarHistoryLoading(true);
      void apiPost<SimilarHistoryData>("/api/nl2sql/similar-history", {
        question: trimmed,
        profile_id: profileId || null,
        limit: 3,
      }, { signal: controller.signal })
        .then((data) => {
          if (!controller.signal.aborted) setSimilarHistory(data.items);
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted && !isAbortError(cause)) {
            setSimilarHistory([]);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setSimilarHistoryLoading(false);
        });
    }, 650);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [active, profileId, question]);

  const insertSchemaText = (text: string) => {
    const el = questionTextareaRef.current;
    if (!el) {
      // ref 未取得（フォーカス外）のときは末尾へ追記。各項目を改行区切りにする。
      setQuestion((current) => `${current}${leadingNewlinePrefix(current, current.length)}${text}`);
      return;
    }
    const source = question;
    const start = el.selectionStart ?? source.length;
    const end = el.selectionEnd ?? source.length;
    // 各項目を改行区切りにする（先頭・直前が改行のときは付けない）。
    const prefixed = `${leadingNewlinePrefix(source, start)}${text}`;
    const nextValue = insertTextAtRange(source, prefixed, start, end);
    setQuestion(nextValue);
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

  const loadSchemaDetail = useCallback(async (table: SchemaTable, signal?: AbortSignal) => {
    const key = `${table.owner}.${table.table_name}`.toUpperCase();
    if (schemaDetails[key] || schemaDetailRequests.current.has(key)) return;
    schemaDetailRequests.current.add(key);
    try {
      const detail = await getSchemaObjectDetail(table.owner, table.table_name, signal);
      if (signal?.aborted) return;
      setSchemaDetails((current) => ({ ...current, [key]: detail }));
    } catch (err) {
      if (!signal?.aborted && !isAbortError(err)) {
        setError(err instanceof Error ? err.message : t("nl2sql.error.loadFailed"));
      }
    } finally {
      schemaDetailRequests.current.delete(key);
    }
  }, [schemaDetails]);

  useEffect(() => {
    if (!schemaSearch.trim()) return;
    const controller = new AbortController();
    for (const table of catalog.tables) {
      if (table.columns.length === 0) void loadSchemaDetail(table, controller.signal);
    }
    return () => controller.abort();
  }, [catalog.tables, loadSchemaDetail, schemaSearch]);

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
    try {
      const data = await apiPost<PreviewData>("/api/nl2sql/preview", {
        question: trimmed,
        engine,
        profile_id: profileId || null,
        allowed_objects: toAllowedObjects(selection),
        select_ai_overrides: selectAiOverrides,
      });
      setPreviewJob(previewToJob(data, trimmed));
      setPreviewExecutionResults(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.previewFailed"));
      setPreviewJob(null);
    } finally {
      setPreviewLoading(false);
    }
  };

  const executePreviewSql = async () => {
    const previewResult = previewJob?.result;
    const sql = previewResult?.executable_sql || previewResult?.generated_sql || "";
    if (!sql.trim() || active) return;
    setPreviewExecuteLoading(true);
    setError("");
    try {
      const data = await apiPost<QueryResults>("/api/nl2sql/execute", {
        ...previewExecutePayload(sql, profileId || null, toAllowedObjects(selection)),
      });
      setPreviewExecutionResults(data);
      // タイムライン上でも実行済みを示す: execute/format を done に、結果を result へ反映。
      setPreviewJob((prev) =>
        prev
          ? {
              ...prev,
              result: prev.result ? { ...prev.result, results: data } : prev.result,
              steps: prev.steps.map((step) =>
                step.stage === "execute_sql" || step.stage === "format_results"
                  ? { ...step, status: "done" }
                  : step
              ),
            }
          : prev
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t("nl2sql.error.executePreviewFailed"));
      setPreviewExecutionResults(null);
    } finally {
      setPreviewExecuteLoading(false);
    }
  };

  const updateOntologySession = (session: QuerySession) => {
    setOntologySession(session);
    setOntologyExecutionResults(queryResultsFromOntologySession(session));
    setOntologyVersionConflict(null);
  };

  const confirmOntologyProfile = async (candidateProfileId: string, revisionId: string) => {
    const trimmed = question.trim();
    if (!trimmed || !ontologyProfileRecommendation || active) return;
    setOntologyLoading(true);
    setError("");
    try {
      const confirmation = await confirmOntologyProfileRecommendation(
        ontologyProfileRecommendation.id,
        candidateProfileId,
        revisionId
      );
      setProfileId(candidateProfileId);
      setOntologyProfileConfirmation({
        question: trimmed,
        profileId: candidateProfileId,
        revisionId,
        token: confirmation.confirmation_token,
      });
      toast.success(t("nl2sql.ontologyProfile.confirmed"));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("nl2sql.ontologyProfile.confirmFailed")
      );
    } finally {
      setOntologyLoading(false);
    }
  };

  const startOntologySession = async () => {
    const trimmed = question.trim();
    if (!trimmed || active) return;
    setOntologyLoading(true);
    setError("");
    setOntologyPatch(null);
    setOntologyExecutionResults(null);
    try {
      const confirmationIsCurrent =
        ontologyProfileConfirmation?.question === trimmed &&
        ontologyProfileConfirmation.profileId === profileId;
      if (!confirmationIsCurrent) {
        setOntologyProfileConfirmation(null);
        setOntologyProfileRecommendation(await recommendOntologyProfiles(trimmed));
        return;
      }
      updateOntologySession(
        await createQuerySession({
          question: trimmed,
          profile_id: profileId,
          allowed_objects: toAllowedObjects(selection),
          profile_confirmation_token: ontologyProfileConfirmation.token,
        })
      );
      setOntologyProfileRecommendation(null);
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
    setPreviewJob(null);
    setPreviewExecutionResults(null);
    const startedAt = Date.now();
    try {
      // チェックが ON のときだけ質問を書き換えてから検索する（入力欄は変えず job にだけ反映）。
      let effectiveQuestion = trimmed;
      if (rewriteUseGlossary || rewriteUseSchema) {
        const rewrite = await apiPost<RewriteData>("/api/nl2sql/rewrite", {
          question: trimmed,
          profile_id: profileId || null,
          use_glossary: rewriteUseGlossary,
          use_schema: rewriteUseSchema,
          extra_prompt: rewriteExtraPrompt,
        });
        setRewriteData(rewrite);
        effectiveQuestion = rewrite.rewritten_question.trim() || trimmed;
      }
      const data = await apiPost<JobCreateData>("/api/nl2sql/jobs", {
        question: effectiveQuestion,
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
      <PageHeader
        title={t("nav.query")}
        subtitle={t("page.query.subtitle")}
        status={
          schemaRefreshStatus ? (
            <span aria-live="polite" aria-atomic="true">
              <StatusBadge
                variant={
                  schemaRefreshStatus === "done"
                    ? "success"
                    : schemaRefreshStatus === "error"
                      ? "danger"
                      : "info"
                }
                label={t(`profiles.schemaRefresh.status.${schemaRefreshStatus}`)}
              />
            </span>
          ) : undefined
        }
        meta={
          catalog.refreshed_at
            ? t("common.schemaRefreshedAt", {
                date: formatDateTime(catalog.refreshed_at),
              })
            : undefined
        }
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => loadCatalog(false, true),
            loading: loadingCatalog,
            disabled: active,
          },
          {
            id: "schema-refresh",
            kind: "utility",
            label: t("common.action.schemaRefresh"),
            icon: RefreshCw,
            onClick: () => loadCatalog(true),
            loading:
              schemaRefreshStatus === "pending" ||
              schemaRefreshStatus === "running",
            disabled: active,
          },
        ]}
      />

      <div className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={error ? { tone: "danger", message: `${error} ${t("nl2sql.error.retryHint")}` } : null}
          action={
            error ? (
              catalog.tables.length === 0 ? (
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
            ) : undefined
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
                      disabled={active || profilesQuery.isPending}
                      className="min-h-11 min-w-0 flex-1 rounded-md border border-border bg-card px-3 py-2 text-sm focus:border-primary focus:ring-2 focus:ring-ring/40"
                    >
                      {profilesQuery.isPending && (
                        <option value={profileId}>{t("profiles.summary.loading")}</option>
                      )}
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
                      className="min-h-11 shrink-0"
                      loading={detecting}
                      disabled={!question.trim() || active}
                      onClick={() => void detectProfile()}
                    >
                      <Wand2 size={16} aria-hidden="true" />
                      <span>{t("nl2sql.recommend.autoDetect")}</span>
                    </Button>
                    {profilesQuery.hasNextPage && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        loading={profilesQuery.isFetchingNextPage}
                        disabled={active}
                        onClick={() => void profilesQuery.fetchNextPage()}
                      >
                        {t("profiles.action.loadMore")}
                      </Button>
                    )}
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
                {ontologyProfileRecommendation ? (
                  <section
                    className="grid gap-3 rounded-md border border-primary/30 bg-primary/5 p-3"
                    aria-labelledby="nl2sql-ontology-profile-recommendation-heading"
                    data-testid="nl2sql-ontology-profile-recommendation"
                  >
                    <div>
                      <h3
                        id="nl2sql-ontology-profile-recommendation-heading"
                        className="text-sm font-semibold text-foreground"
                      >
                        {t("nl2sql.ontologyProfile.title")}
                      </h3>
                      <p className="mt-1 text-sm leading-6 text-muted">
                        {t("nl2sql.ontologyProfile.description")}
                      </p>
                    </div>
                    <div className="grid gap-2">
                      {ontologyProfileRecommendation.candidates.map((candidate) => (
                        <div
                          key={candidate.profile_id}
                          className="grid gap-2 rounded-md border border-border bg-card p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                        >
                          <div className="min-w-0 text-sm">
                            <p className="font-medium text-foreground">
                              {candidate.profile_name}（{Math.round(candidate.score * 100)}%）
                            </p>
                            <p className="mt-1 text-muted">
                              {candidate.reasons_ja.join(" ")}
                            </p>
                          </div>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            disabled={active}
                            onClick={() =>
                              void confirmOntologyProfile(
                                candidate.profile_id,
                                candidate.ontology_revision_id
                              )
                            }
                          >
                            {t("nl2sql.ontologyProfile.confirm")}
                          </Button>
                        </div>
                      ))}
                      {ontologyProfileRecommendation.candidates.length === 0 ? (
                        <div className="grid gap-2 rounded-md border border-border bg-card p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                          <p className="text-sm leading-6 text-muted">
                            {t("nl2sql.ontologyProfile.noMatch")}
                          </p>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            disabled={active || !profileId}
                            onClick={() =>
                              void confirmOntologyProfile(
                                profileId,
                                ontologyProfileRecommendation.ontology_revision_id
                              )
                            }
                          >
                            {t("nl2sql.ontologyProfile.confirmCurrent")}
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  </section>
                ) : null}
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
                            disabled={active}
                          rows={5}
                          className="min-h-36 max-h-[16.625rem] resize-none rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                          placeholder={t("nl2sql.question.placeholder")}
                        />
                      </label>
                    </div>
                    <div className="rounded-md border border-border bg-background p-3">
                      <SchemaReferencePanel
                        catalog={catalog}
                        loading={loadingCatalog}
                        disabled={active}
                        availableTableCount={
                          schemaHeadQuery.data?.object_count ?? catalog.tables.length
                        }
                        selectedTableCount={selection.tableNames.length}
                        insertMode="logical"
                        allowedTableNames={profileAllowedTableNames}
                        listMaxHeightClass="max-h-[30rem]"
                        onRefreshSchema={() => void loadCatalog(true)}
                        refreshing={
                          schemaRefreshStatus === "pending" || schemaRefreshStatus === "running"
                        }
                        searchQuery={schemaSearch}
                        onSearchQueryChange={setSchemaSearch}
                        hasMore={Boolean(schemaObjectsQuery.hasNextPage)}
                        loadingMore={schemaObjectsQuery.isFetchingNextPage}
                        onLoadMore={() => void schemaObjectsQuery.fetchNextPage()}
                        onExpandTable={(table) => void loadSchemaDetail(table)}
                        onInsert={insertSchemaText}
                      />
                    </div>
                  </div>

                  <section className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm">
                    <div className="grid gap-1">
                      <p className="font-semibold text-foreground">{t("nl2sql.rewrite.title")}</p>
                      <p className="text-xs leading-5 text-muted">{t("nl2sql.rewrite.hint")}</p>
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
                    <section className="overflow-hidden rounded-md border border-border bg-card">
                      <Button
                        type="button"
                        variant="ghost"
                        size="md"
                        className="min-h-11 w-full justify-between rounded-none px-4 text-left"
                        aria-expanded={similarHistoryOpen}
                        aria-controls="nl2sql-similar-history"
                        onClick={() => setSimilarHistoryOpen((current) => !current)}
                      >
                        <span className="flex items-center gap-2">
                          <BookOpenText size={16} className="text-foreground" aria-hidden="true" />
                          {similarHistoryLoading
                            ? t("nl2sql.similar.loading")
                            : t("nl2sql.similar.title")}
                        </span>
                        <ChevronDown
                          size={16}
                          className={similarHistoryOpen ? "rotate-180 transition-transform" : "transition-transform"}
                          aria-hidden="true"
                        />
                      </Button>
                      {similarHistoryOpen && (
                        <div id="nl2sql-similar-history" className="grid gap-3 border-t border-border p-3 text-sm text-foreground">
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
                              <pre className="max-h-28 overflow-auto rounded-md border border-border bg-card p-2 text-sm leading-6 text-foreground">
                                <code>{entry.item.executable_sql || entry.item.generated_sql}</code>
                              </pre>
                            </article>
                          ))}
                        </div>
                      )}
                    </section>
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
                          setResult(null);
                          setPreviewJob(null);
                          setPreviewExecutionResults(null);
                          setRewriteData(null);
                          setRewriteUseGlossary(false);
                          setRewriteUseSchema(false);
                          setRewriteExtraPrompt("");
                          clearTrackedJob();
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
          job={job ?? previewJob}
          elapsedSeconds={elapsedSeconds}
          catalogEmpty={catalog !== null && catalog.tables.length === 0}
          importingSample={importingSample}
          onImportSample={importSampleData}
          onPreviewExecute={
            !job && previewJob && !previewExecutionResults
              ? () => void executePreviewSql()
              : undefined
          }
          previewExecuteLoading={previewExecuteLoading}
        />
        <Nl2SqlResultTable results={ontologyExecutionResults} />

        <Nl2SqlResultTable results={result?.results ?? previewExecutionResults} />
        <SelectAiFeedbackAddPanel
          result={result ?? previewJob?.result ?? null}
          history={latestHistory}
          selectedProfileId={profileId}
          questionText={question}
          onSaved={refreshHistory}
        />
      </div>
    </>
  );
}
