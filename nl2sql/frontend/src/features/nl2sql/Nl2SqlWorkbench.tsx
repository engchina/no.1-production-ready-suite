import { Button } from "@/components/ui/button";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  BookOpenText,
  Database,
  Play,
  RefreshCw,
  RotateCcw,
  Sparkles,
  UserCog,
  Wand2,
} from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Banner, toast } from "@engchina/production-ready-ui";

import { TimedLoadingState } from "@/components/ProcessingState";
import { ActionResultRegion } from "@/components/ActionResultRegion";
import { PageHeader } from "@/components/PageHeader";
import { PageNotice } from "@/components/page-notice";
import { EmptyState } from "@/components/StateViews";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { FieldLabel } from "@/components/ui/required-field";
import { StatusBadge } from "@/components/ui/status-badge";
import { useAuth } from "@/features/security/AuthProvider";
import {
  CAPABILITY_PERMISSIONS,
  MENU_PERMISSIONS,
} from "@/features/security/menu-permissions";
import { ApiError, apiGet, apiPost, isAbortError, isTimeoutError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { toastError } from "@/lib/toast";
import { formatDateTime } from "@/lib/format";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import { DbObjectPanelHeader } from "./components/DbObjectManagementShared";
import { EngineSelector } from "./components/EngineSelector";
import { Nl2SqlExecutionOptionsPanel } from "./components/Nl2SqlExecutionOptionsPanel";
import { Nl2SqlResultTable } from "./components/Nl2SqlResultTable";
import { OperationStatusStrip } from "./components/OperationStatusStrip";
import { QuestionText } from "./components/QuestionText";
import { SchemaReferencePanel } from "./components/SchemaReferencePanel";
import { SchemaRefreshHeaderStatus } from "./components/SchemaRefreshFeedback";
import { useSchemaRefreshCoordinator } from "./SchemaRefreshCoordinator";
import {
  getSchemaObjectDetail,
  useProfileUsageContext,
  useProfileSummaries,
  useSchemaCatalogHead,
  useSchemaObjects,
} from "./incrementalQueries";
import { SelectAiFeedbackAddPanel } from "./components/SelectAiFeedbackAddPanel";
import { isJobInFlight } from "./jobPersistence";
import { prefillFromSearchParams } from "./queryPrefillState";
import { QUESTION_TEMPLATES } from "./questionTemplates";
import { profileDisplayLabel } from "./profileDisplay";
import type {
  HistoryData,
  HistoryItem,
  JobCreateData,
  Nl2SqlEngine,
  Nl2SqlResult,
  ProfileRecommendationData,
  RewriteData,
  SchemaCatalog,
  SchemaObjectDetail,
  SchemaTable,
  SimilarHistoryData,
  SimilarHistoryItem,
} from "./types";
import { useNl2SqlJobPolling } from "./useNl2SqlJobPolling";
import {
  emptySelection,
  insertTextAtRange,
  isSchemaEmptyError,
  leadingNewlinePrefix,
  toAllowedObjects,
  toSchemaSelection,
  type SchemaSelection,
} from "./workbenchState";

// history_id が一致するものだけを結果の履歴とみなす。generated_sql の一致で代用すると、
// 履歴永続化に失敗した回のフィードバックが同じ SQL の別の履歴へ付いてしまう。
function lastMatchingHistory(history: HistoryItem[], result: Nl2SqlResult | null) {
  if (!result?.history_id) return null;
  return history.find((item) => item.id === result.history_id) ?? null;
}

function goodFeedbackSimilarHistory(items: SimilarHistoryItem[]): SimilarHistoryItem[] {
  return items.filter((entry) => entry.item.admin_feedback_rating === "good");
}

type PageErrorSource = "profile-load" | "schema-load" | "schema-refresh";
type PageError = { source: PageErrorSource; message: string; code?: string } | null;

const PROFILE_RECOMMENDATION_APPLY_THRESHOLD = 0.3;

function messageFromError(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

/** ApiError の機械判定用 code(problem.code)。PageError の分類に使う。 */
function codeFromError(error: unknown): string | undefined {
  return error instanceof ApiError ? error.errorCode : undefined;
}

function messageWithRetryHint(message: string) {
  const retryHint = t("nl2sql.error.retryHint");
  return message.includes(retryHint) ? message : `${message} ${retryHint}`;
}

function actionErrorMessage(error: unknown, fallback: string) {
  return messageWithRetryHint(messageFromError(error, fallback));
}

function listLoadMoreErrorMessage(error: unknown, fallbackKey: Parameters<typeof t>[0]) {
  if (isTimeoutError(error)) {
    return t("objectSelector.loadMoreTimeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
    });
  }
  return error instanceof Error ? error.message : t(fallbackKey);
}

export function Nl2SqlWorkbench() {
  const { hasPermission } = useAuth();
  const canExecute = hasPermission(MENU_PERMISSIONS.query);
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
  const { hasPermission } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [engine, setEngine] = useState<Nl2SqlEngine>("select_ai");
  const [profileId, setProfileId] = useState("");
  const [question, setQuestion] = useState("");
  const [selection, setSelection] = useState<SchemaSelection>(() => emptySelection());
  const [result, setResult] = useState<Nl2SqlResult | null>(null);
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [similarHistory, setSimilarHistory] = useState<SimilarHistoryItem[]>([]);
  const [similarHistoryLoading, setSimilarHistoryLoading] = useState(false);
  const [similarHistorySearchCompleted, setSimilarHistorySearchCompleted] = useState(false);
  const [rewriteData, setRewriteData] = useState<RewriteData | null>(null);
  const [rewriteUseGlossary, setRewriteUseGlossary] = useState(false);
  const [rewriteExtraPrompt, setRewriteExtraPrompt] = useState("");
  const [useOntologyContext, setUseOntologyContext] = useState(true);
  const [includeInterpretation, setIncludeInterpretation] = useState(true);
  // Show Prompt は Select AI への追加 round-trip を伴うため既定 OFF(必要な人だけ ON にする)。
  const [includeShowPrompt, setIncludeShowPrompt] = useState(false);
  const [executionOptionsOpen, setExecutionOptionsOpen] = useState(false);
  const [selectAiAdvancedOpen, setSelectAiAdvancedOpen] = useState(false);
  const [selectAiRoleAdvancedOpen, setSelectAiRoleAdvancedOpen] = useState(false);
  const [similarHistoryOpen, setSimilarHistoryOpen] = useState(false);
  const [selectAiRoleOverride, setSelectAiRoleOverride] = useState("");
  const [selectAiInstructionsOverride, setSelectAiInstructionsOverride] = useState("");
  const [schemaSearch, setSchemaSearch] = useState("");
  const [schemaDetails, setSchemaDetails] = useState<Record<string, SchemaObjectDetail>>({});
  const [submitting, setSubmitting] = useState(false);
  const [pageError, setPageError] = useState<PageError>(null);
  const [actionError, setActionError] = useState("");
  const [schemaDetailError, setSchemaDetailError] = useState("");
  const [autoDetectLowConfidence, setAutoDetectLowConfidence] = useState(false);
  const [actionOperationKey, setActionOperationKey] = useState(0);
  const [importingSample, setImportingSample] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const questionTextareaRef = useRef<HTMLTextAreaElement>(null);
  const schemaDetailRequests = useRef(new Set<string>());

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
  const profileLoadMoreError =
    profilesQuery.isFetchNextPageError && profilesQuery.error
      ? listLoadMoreErrorMessage(profilesQuery.error, "profiles.error.load")
      : "";
  const noProfiles = profilesQuery.isSuccess && profiles.length === 0;
  const selectedProfileQuery = useProfileUsageContext(profileId);
  const selectedProfile = selectedProfileQuery.data?.profile ?? null;
  const canManageProfiles = hasPermission(CAPABILITY_PERMISSIONS.profilesManage);
  const canRefreshSchema = hasPermission(CAPABILITY_PERMISSIONS.schemaRefresh);
  const canImportSampleData = hasPermission(CAPABILITY_PERMISSIONS.sampleDataManage);
  const profileSelectionReady =
    !noProfiles &&
    Boolean(profileId) &&
    (profiles.some((profile) => profile.id === profileId) || selectedProfileQuery.isSuccess);
  const schemaObjectsQuery = useSchemaObjects(
    schemaSearch,
    "",
    profileId,
    "",
    profileSelectionReady
  );
  const schemaLoadMoreError =
    schemaObjectsQuery.isFetchNextPageError && schemaObjectsQuery.error
      ? listLoadMoreErrorMessage(schemaObjectsQuery.error, "dataMgmt.objectList.error")
      : "";
  const schemaHeadQuery = useSchemaCatalogHead();
  const schemaRefresh = useSchemaRefreshCoordinator();
  const schemaObjects = useMemo(
    () => schemaObjectsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [schemaObjectsQuery.data]
  );
  const catalog = useMemo<SchemaCatalog>(() => {
    return {
      refreshed_at: schemaHeadQuery.data?.refreshed_at ?? "",
      schema_fingerprint: schemaHeadQuery.data?.schema_fingerprint ?? "",
      tables: schemaObjects.map((object) => {
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
  }, [schemaDetails, schemaHeadQuery.data, schemaObjects]);
  const loadingCatalog = schemaObjectsQuery.isPending && !schemaObjectsQuery.data;
  const schemaCatalogHasObjects =
    catalog.tables.length > 0 || (schemaHeadQuery.data?.object_count ?? 0) > 0;
  const currentScopedSchemaEmpty =
    schemaObjectsQuery.isSuccess &&
    catalog.tables.length === 0 &&
    (schemaHeadQuery.data?.object_count ?? 0) === 0;
  const visiblePageError =
    pageError && !(isSchemaEmptyError(pageError) && schemaCatalogHasObjects)
      ? pageError
      : null;

  // 画面 entry は summary/object page だけを独立取得する。refresh は persistent job を投入し、
  // 前 catalog を表示したまま job 完了時にこの画面で必要な read model だけを取り直す。
  const loadCatalog = useCallback(
    async (refresh = false, announce = false) => {
      try {
        if (refresh) {
          await schemaRefresh.start();
          return;
        }
        const results = await Promise.allSettled([
          schemaObjectsQuery.refetch(),
          schemaHeadQuery.refetch(),
          profilesQuery.refetch(),
        ]);
        const failedResult = results.find(
          (result) => result.status === "rejected" || result.value.isError
        );
        if (failedResult) {
          const failedIndex = results.indexOf(failedResult);
          const fallback =
            failedIndex === 2 ? t("profiles.error.load") : t("nl2sql.error.loadFailed");
          const cause =
            failedResult.status === "rejected" ? failedResult.reason : failedResult.value.error;
          setPageError({
            source: failedIndex === 2 ? "profile-load" : "schema-load",
            message: messageFromError(cause, fallback),
            code: codeFromError(cause),
          });
          return;
        }
        setPageError(null);
        if (
          announce &&
          results.every(
            (result) => result.status === "fulfilled" && !result.value.isError
          )
        ) {
          toast.success(t("common.action.refreshed"));
        }
      } catch (err) {
        setPageError({
          source: refresh ? "schema-refresh" : "schema-load",
          message: messageFromError(err, t("nl2sql.error.loadFailed")),
          code: codeFromError(err),
        });
      }
    },
    [profilesQuery, schemaHeadQuery, schemaObjectsQuery, schemaRefresh]
  );

  useEffect(() => {
    if (profilesQuery.isError && !profilesQuery.data) {
      setPageError({
        source: "profile-load",
        message: messageFromError(profilesQuery.error, t("profiles.error.load")),
        code: codeFromError(profilesQuery.error),
      });
      return;
    }
    if (selectedProfileQuery.isError && profileId) {
      setPageError({
        source: "profile-load",
        message: messageFromError(selectedProfileQuery.error, t("profiles.error.load")),
        code: codeFromError(selectedProfileQuery.error),
      });
      return;
    }
    if (schemaObjectsQuery.isError && !schemaObjectsQuery.data) {
      setPageError({
        source: "schema-load",
        message: messageFromError(schemaObjectsQuery.error, t("nl2sql.error.loadFailed")),
        code: codeFromError(schemaObjectsQuery.error),
      });
      return;
    }
    if (schemaHeadQuery.isError && !schemaHeadQuery.data) {
      setPageError({
        source: "schema-load",
        message: messageFromError(schemaHeadQuery.error, t("nl2sql.error.loadFailed")),
        code: codeFromError(schemaHeadQuery.error),
      });
    }
  }, [
    profileId,
    profilesQuery.data,
    profilesQuery.error,
    profilesQuery.isError,
    schemaHeadQuery.data,
    schemaHeadQuery.error,
    schemaHeadQuery.isError,
    schemaObjectsQuery.data,
    schemaObjectsQuery.error,
    schemaObjectsQuery.isError,
    selectedProfileQuery.error,
    selectedProfileQuery.isError,
  ]);

  useEffect(() => {
    if (!pageError) return;
    if (
      pageError.source === "profile-load" &&
      profilesQuery.isSuccess &&
      (noProfiles || !profileId || selectedProfileQuery.isSuccess)
    ) {
      setPageError(null);
      return;
    }
    if (pageError.source === "schema-load" && schemaCatalogHasObjects) {
      setPageError(null);
    }
  }, [
    pageError,
    noProfiles,
    profileId,
    profilesQuery.isSuccess,
    schemaCatalogHasObjects,
    selectedProfileQuery.isSuccess,
  ]);

  useEffect(() => {
    if (!schemaRefresh.error) return;
    setPageError({ source: "schema-refresh", message: schemaRefresh.error });
  }, [schemaRefresh.error]);

  const refreshHistory = useCallback(async () => {
    const historyData = await apiGet<HistoryData>("/api/nl2sql/history");
    setHistory(historyData.items);
  }, []);

  const handleJobResult = useCallback((data: Nl2SqlResult) => {
    setResult(data);
  }, []);

  // job 自体のエラーは OperationStatusStrip（job.error_message）へ一本化して表示する。
  // ページ共通 Banner（catalog/execute 用）には流さず、重複表示を避ける。
  const handleJobFailed = useCallback(() => {}, []);

  // ポーリング通信の断念/job 消失は error_message を持たないため、
  // 実行ボタン直下の ActionResultRegion を正本として表示する（Toast とは併用しない）。
  const handlePollingLost = useCallback((message: string) => {
    setActionError(messageWithRetryHint(message));
    setActionOperationKey((current) => current + 1);
  }, []);

  // 結果自体は成功しているため、履歴更新の失敗は warning に留める。
  const handleHistoryRefreshFailed = useCallback(() => {
    toast.warning(t("nl2sql.history.refreshFailed"));
  }, []);

  const { job, jobStartedAt, trackJob, clearTrackedJob } = useNl2SqlJobPolling({
    onResult: handleJobResult,
    onJobFailed: handleJobFailed,
    onPollingLost: handlePollingLost,
    onHistoryRefresh: refreshHistory,
    onHistoryRefreshFailed: handleHistoryRefreshFailed,
  });
  // 実行中 job の協調キャンセル要求。停止自体は polling が terminal 遷移で拾う。
  const [cancelRequesting, setCancelRequesting] = useState(false);
  const requestJobCancel = useCallback(async () => {
    if (!job || !isJobInFlight(job.status)) return;
    setCancelRequesting(true);
    try {
      await apiPost(`/api/nl2sql/jobs/${encodeURIComponent(job.job_id)}/cancel`, {});
    } catch (err) {
      setActionError(actionErrorMessage(err, t("nl2sql.job.cancelFailed")));
      setActionOperationKey((current) => current + 1);
    } finally {
      setCancelRequesting(false);
    }
  }, [job]);

  const clearGeneratedOutput = useCallback(() => {
    setResult(null);
    clearTrackedJob();
    setRewriteData(null);
    setActionError("");
  }, [clearTrackedJob]);
  const jobActive = isJobInFlight(job?.status) || submitting;
  const active = jobActive;
  const actionBusy = submitting;
  const showSimilarHistoryPanel =
    similarHistoryLoading || similarHistorySearchCompleted || similarHistory.length > 0;
  const beginActionFeedback = useCallback(() => {
    setActionError("");
    setActionOperationKey((current) => current + 1);
  }, []);
  const showActionError = useCallback((err: unknown, fallback: string) => {
    setActionError(actionErrorMessage(err, fallback));
    setActionOperationKey((current) => current + 1);
  }, []);
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
  const selectAiRoleHasOverride = Boolean(selectAiRoleOverride.trim());
  const hasSelectAiOverrideInputs = useMemo(
    () => Boolean(selectAiRoleHasOverride || selectAiInstructionsOverride.trim()),
    [selectAiInstructionsOverride, selectAiRoleHasOverride]
  );
  const selectAiRolePanelOpen = selectAiRoleAdvancedOpen || selectAiRoleHasOverride;
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
    if (profilesQuery.isPending) return;
    if (noProfiles) {
      if (profileId) setProfileId("");
      setSelection(emptySelection());
      setRecommendation(null);
      return;
    }
    if (!profileId) {
      const nextProfileId = profiles[0]?.id ?? "";
      if (nextProfileId) setProfileId(nextProfileId);
      return;
    }
    if (
      profiles.some((profile) => profile.id === profileId) ||
      selectedProfileQuery.isPending ||
      selectedProfileQuery.isSuccess
    ) {
      return;
    }
    setProfileId(profiles[0]?.id ?? "");
    setSelection(emptySelection());
    setRecommendation(null);
  }, [
    noProfiles,
    profileId,
    profiles,
    profilesQuery.isPending,
    selectedProfileQuery.isPending,
    selectedProfileQuery.isSuccess,
  ]);

  // 実行中かどうかは ref で参照し、effect の再実行トリガーにしない。
  // active を依存に入れると job 完了(active: true → false)のたびに同じ質問で推薦 API が再実行される。
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => {
    const trimmed = question.trim();
    setAutoDetectLowConfidence(false);
    if (trimmed.length < 4 || profiles.length === 0) {
      setRecommendation(null);
      return undefined;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (activeRef.current) return;
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
  }, [profileId, profiles.length, question]);

  useEffect(() => {
    const trimmed = question.trim();
    if (trimmed.length < 4 || active || profiles.length === 0) {
      setSimilarHistory([]);
      setSimilarHistorySearchCompleted(false);
      // 実行開始・質問クリア時は in-flight を abort するため .finally が走らない。
      // loading フラグを明示的に落とさないと「参考履歴を検索中」が固まって残る。
      setSimilarHistoryLoading(false);
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
          if (!controller.signal.aborted) {
            setSimilarHistory(goodFeedbackSimilarHistory(data.items));
            setSimilarHistorySearchCompleted(true);
          }
        })
        .catch((cause: unknown) => {
          if (!controller.signal.aborted && !isAbortError(cause)) {
            setSimilarHistory([]);
            setSimilarHistorySearchCompleted(false);
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
  }, [active, profileId, profiles.length, question]);

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
      setSchemaDetailError("");
    } catch (err) {
      if (!signal?.aborted && !isAbortError(err)) {
        setSchemaDetailError(messageFromError(err, t("nl2sql.error.loadFailed")));
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
    setAutoDetectLowConfidence(false);
    setProfileId(recommendation.recommended_profile_id);
    setSelection(toSchemaSelection(recommendation.recommended_allowed_objects));
    setPageError(null);
    setSchemaDetailError("");
  };

  // 質問から業務プロファイルを自動判定（学習済み分類器 → 決定論フォールバック）して選択する。
  const detectProfile = useCallback(async () => {
    const trimmed = question.trim();
    if (!trimmed || active || !profileSelectionReady) return;
    setDetecting(true);
    try {
      const data = await apiPost<ProfileRecommendationData>("/api/nl2sql/recommend-profile", {
        question: trimmed,
        current_profile_id: profileId || null,
      });
      setRecommendation(data);
      setPageError(null);
      setSchemaDetailError("");
      const recommendedProfileLabel = profileDisplayLabel({
        name: data.recommended_profile_name,
        category: data.recommended_profile_category,
      });
      if (data.confidence < PROFILE_RECOMMENDATION_APPLY_THRESHOLD) {
        // 継続的な状況提示なので固定面の warning Banner に一本化する
        // (同文の Toast と二重表示しない: messaging spec §0.6)。
        setAutoDetectLowConfidence(true);
        return;
      }
      setAutoDetectLowConfidence(false);
      setProfileId(data.recommended_profile_id);
      setSelection(toSchemaSelection(data.recommended_allowed_objects));
      const sourceLabel =
        data.recommendation_source === "classifier"
          ? t("nl2sql.recommend.sourceClassifier")
          : t("nl2sql.recommend.sourceDeterministic");
      toast.success(
        t("nl2sql.recommend.autoDetectApplied", {
          name: recommendedProfileLabel,
          source: sourceLabel,
          confidence: Math.round(data.confidence * 100),
        })
      );
    } catch (err) {
      toastError(err instanceof Error ? err.message : t("nl2sql.recommend.autoDetectFailed"));
    } finally {
      setDetecting(false);
    }
  }, [active, profileId, profileSelectionReady, question]);

  // schema catalog が空（サンプル未投入）のとき、エラーからワンクリックで投入して解消する。
  const importSampleData = useCallback(async () => {
    setImportingSample(true);
    try {
      await apiPost("/api/nl2sql/sample-data/import", { step: "all", confirmation: "SQL_ASSIST_SAMPLE" });
      toast.success(t("nl2sql.sample.importSuccess"));
      setPageError(null);
      setSchemaDetailError("");
      clearTrackedJob();
      await loadCatalog();
    } catch (err) {
      toastError(err instanceof Error ? err.message : t("nl2sql.sample.importFailed"));
    } finally {
      setImportingSample(false);
    }
  }, [clearTrackedJob, loadCatalog]);

  const applyRewrittenQuestion = async () => {
    if (!rewriteData) return;
    setQuestion(rewriteData.rewritten_question);
  };

  // 用語・同義語の置換が起きていない（= 無変換）ときはカードを出さない。
  // 変更前後が同一なのに「生成に使用される質問」を並べると、書き換えられた誤解を招く。
  // 「抑止した」等の内部処理 warning だけではカードを開かない（無変換の報告は余計な情報）。
  const rewriteChanged =
    !!rewriteData && rewriteData.rewritten_question.trim() !== question.trim();

  const submit = async () => {
    const trimmed = question.trim();
    if (!trimmed || active || !profileSelectionReady) return;
    beginActionFeedback();
    clearGeneratedOutput();
    setSubmitting(true);
    const startedAt = Date.now();
    try {
      // チェックが ON のときだけ質問を書き換えてから検索する（入力欄は変えず job にだけ反映）。
      let effectiveQuestion = trimmed;
      if (rewriteUseGlossary) {
        const rewrite = await apiPost<RewriteData>("/api/nl2sql/rewrite", {
          question: trimmed,
          profile_id: profileId || null,
          use_glossary: rewriteUseGlossary,
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
        use_ontology_context: useOntologyContext,
        include_interpretation: includeInterpretation,
        include_show_prompt: includeShowPrompt,
      });
      // 追跡開始後の取得・リトライ・断念は useNl2SqlJobPolling が担う。
      // ここで初回 poll を await すると、その失敗が「検索開始失敗」と誤表示され
      // 成功した job の追跡まで破棄されるため、try 節は job 作成までとする。
      trackJob(data, startedAt);
    } catch (err) {
      showActionError(err, t("nl2sql.error.submitFailed"));
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
        status={<SchemaRefreshHeaderStatus testId="query-schema-refresh-status" />}
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
          ...(canRefreshSchema
            ? [
                {
                  id: "schema-refresh",
                  kind: "utility" as const,
                  label: t("common.action.schemaRefresh"),
                  icon: RefreshCw,
                  onClick: () => loadCatalog(true),
                  loading: schemaRefresh.isRefreshing,
                  disabled: active || schemaRefresh.isRefreshing,
                },
              ]
            : []),
        ]}
      />

      <div className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={
            visiblePageError
              ? { tone: "danger", message: messageWithRetryHint(visiblePageError.message) }
              : null
          }
          action={
            visiblePageError ? (
              <Button type="button" variant="secondary" size="sm" onClick={() => void loadCatalog()}>
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("nl2sql.action.refresh")}</span>
              </Button>
            ) : undefined
          }
        />

        {noProfiles ? (
          <Banner
            severity="info"
            title={t("nl2sql.profile.empty.title")}
            action={
              canManageProfiles ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => navigate("/profiles?profile=new")}
                >
                  <UserCog size={15} aria-hidden="true" />
                  <span>{t("nl2sql.profile.empty.action")}</span>
                </Button>
              ) : undefined
            }
          >
            {canManageProfiles
              ? t("nl2sql.profile.empty.description")
              : t("nl2sql.profile.empty.readOnlyDescription")}
          </Banner>
        ) : null}

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
                  <EngineSelector
                    value={engine}
                    onChange={(nextEngine) => {
                      setEngine(nextEngine);
                      setActionError("");
                    }}
                    disabled={active}
                  />

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
                      onChange={(event) => {
                        setProfileId(event.currentTarget.value);
                        setAutoDetectLowConfidence(false);
                        setActionError("");
                        setPageError(null);
                        setSchemaDetailError("");
                      }}
                      disabled={active || profilesQuery.isPending || noProfiles}
                      className="min-h-11 min-w-0 flex-1 rounded-md border border-border bg-card px-3 py-2 text-sm focus:border-primary focus:ring-2 focus:ring-ring/40"
                    >
                      {profilesQuery.isPending && (
                        <option value={profileId}>{t("profiles.summary.loading")}</option>
                      )}
                      {profiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profileDisplayLabel(profile)}
                        </option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      variant="secondary"
                      size="md"
                      className="min-h-11 shrink-0"
                      loading={detecting}
                      disabled={!question.trim() || active || !profileSelectionReady}
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
                  {profileLoadMoreError && (
                    <Banner
                      severity="danger"
                      action={<Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="w-full sm:w-auto"
                        loading={profilesQuery.isFetchingNextPage}
                        disabled={active}
                        onClick={() => void profilesQuery.fetchNextPage()}
                      >
                        <RefreshCw size={15} aria-hidden="true" />
                        <span>{t("common.retry")}</span>
                      </Button>}
                    >
                      {profileLoadMoreError}
                    </Banner>
                  )}
                </div>
                {recommendation &&
                  recommendation.confidence >= PROFILE_RECOMMENDATION_APPLY_THRESHOLD &&
                  recommendation.recommended_profile_id !== profileId && (
                    <div
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm"
                      data-testid="nl2sql-recommend-hint"
                    >
                      <span className="flex min-w-0 items-center gap-2 text-foreground">
                        <Sparkles size={15} className="shrink-0 text-primary" aria-hidden="true" />
                        <span className="min-w-0 [overflow-wrap:anywhere]">
                          {t("nl2sql.recommend.switchHint", {
                            name: profileDisplayLabel({
                              name: recommendation.recommended_profile_name,
                              category: recommendation.recommended_profile_category,
                            }),
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
                {autoDetectLowConfidence &&
                  recommendation &&
                  recommendation.confidence < PROFILE_RECOMMENDATION_APPLY_THRESHOLD && (
                    <div data-testid="nl2sql-recommend-low-confidence">
                      <Banner severity="warning">
                        {t("nl2sql.recommend.autoDetectLowConfidence", {
                          name: profileDisplayLabel({
                            name: recommendation.recommended_profile_name,
                            category: recommendation.recommended_profile_category,
                          }),
                          confidence: Math.round(recommendation.confidence * 100),
                        })}
                      </Banner>
                    </div>
                  )}
              </div>

                  {/* 検索クエリ（左）× スキーマ参照（右・常時表示）: 書きながら参照して即クリック挿入。 */}
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start">
                    <div className="grid gap-2">
                      {/* 検索クエリの入力を補助するテンプレート行（選択時は全文置換）。 */}
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
                              setActionError("");
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
                      <div className="grid gap-2">
                        <FieldLabel
                          htmlFor="nl2sql-question-input"
                          label={t("nl2sql.question.label")}
                          required
                        />
                        <textarea
                          id="nl2sql-question-input"
                          ref={questionTextareaRef}
                          value={question}
                          onChange={(event) => {
                            setQuestion(event.currentTarget.value);
                            setActionError("");
                          }}
                          disabled={active}
                          rows={5}
                          required
                          aria-required="true"
                          className="min-h-36 max-h-[16.625rem] resize-none rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                          placeholder={t("nl2sql.question.placeholder")}
                        />
                        {engine === "select_ai" && (
                          <section className="overflow-hidden rounded-md border border-dashed border-border bg-background">
                            <Button
                              type="button"
                              variant="ghost"
                              size="md"
                              className="min-h-11 w-full justify-between rounded-none px-3 text-left"
                              aria-expanded={selectAiAdvancedOpen}
                              aria-controls="select-ai-request-overrides"
                              onClick={() => setSelectAiAdvancedOpen((current) => !current)}
                              disabled={active}
                            >
                              <span className="flex min-w-0 items-center gap-2">
                                <Sparkles size={15} className="shrink-0 text-foreground" aria-hidden="true" />
                                <span className="min-w-0 [overflow-wrap:anywhere]">
                                  {t("nl2sql.selectAiOverrides.title")}
                                </span>
                                {hasSelectAiOverrideInputs && (
                                  <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                                    {t("nl2sql.selectAiOverrides.activeBadge")}
                                  </span>
                                )}
                              </span>
                              <DisclosureChevron
                                expanded={selectAiAdvancedOpen}
                                size={16}
                              />
                            </Button>
                            {/* aria-controls の参照先を常時レンダし、閉時は hidden にする
                                (Nl2SqlExecutionOptionsPanel と同方式)。 */}
                            <div
                              id="select-ai-request-overrides"
                              hidden={!selectAiAdvancedOpen}
                              className="grid gap-3 border-t border-border p-3"
                            >
                                <p className="text-xs leading-5 text-muted">
                                  {t("nl2sql.selectAiOverrides.hint")}
                                </p>
                                <label className="grid gap-1 text-sm font-medium text-foreground">
                                  <span>{t("nl2sql.selectAiOverrides.additionalInstructions")}</span>
                                  <textarea
                                    value={selectAiInstructionsOverride}
                                    onChange={(event) => setSelectAiInstructionsOverride(event.currentTarget.value)}
                                    disabled={active}
                                    rows={3}
                                    className="min-h-24 rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                                    placeholder={t("nl2sql.selectAiOverrides.additionalInstructionsPlaceholder")}
                                  />
                                </label>
                                <div className="overflow-hidden rounded-md border border-border bg-card">
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="min-h-10 w-full justify-between rounded-none px-3 text-left"
                                    aria-expanded={selectAiRolePanelOpen}
                                    aria-controls="select-ai-role-override"
                                    onClick={() =>
                                      setSelectAiRoleAdvancedOpen((current) =>
                                        selectAiRoleHasOverride ? true : !current
                                      )
                                    }
                                    disabled={active}
                                  >
                                    <span className="flex min-w-0 items-center gap-2">
                                      <UserCog size={14} className="shrink-0 text-muted" aria-hidden="true" />
                                      <span>{t("nl2sql.selectAiOverrides.roleToggle")}</span>
                                      {selectAiRoleHasOverride && (
                                        <span className="rounded-full bg-muted/30 px-2 py-0.5 text-xs font-medium text-muted">
                                          {t("nl2sql.selectAiOverrides.activeBadge")}
                                        </span>
                                      )}
                                    </span>
                                    <DisclosureChevron
                                      expanded={selectAiRolePanelOpen}
                                      size={15}
                                    />
                                  </Button>
                                  <label
                                    id="select-ai-role-override"
                                    hidden={!selectAiRolePanelOpen}
                                    className="grid gap-1 border-t border-border p-3 text-sm font-medium text-foreground"
                                  >
                                    <span>{t("nl2sql.selectAiOverrides.role")}</span>
                                    <textarea
                                      value={selectAiRoleOverride}
                                      onChange={(event) => setSelectAiRoleOverride(event.currentTarget.value)}
                                      disabled={active}
                                      rows={2}
                                      className="min-h-20 rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                                      placeholder={t("nl2sql.selectAiOverrides.rolePlaceholder")}
                                    />
                                  </label>
                                </div>
                              </div>
                          </section>
                        )}
                      </div>
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
                        onRefreshSchema={
                          canRefreshSchema ? () => void loadCatalog(true) : undefined
                        }
                        refreshing={schemaRefresh.isRefreshing}
                        searchQuery={schemaSearch}
                        onSearchQueryChange={(value) => {
                          setSchemaSearch(value);
                          setSchemaDetailError("");
                        }}
                        hasMore={Boolean(schemaObjectsQuery.hasNextPage)}
                        loadingMore={schemaObjectsQuery.isFetchingNextPage}
                        loadMoreError={schemaLoadMoreError}
                        onLoadMore={() => void schemaObjectsQuery.fetchNextPage()}
                        onRetryLoadMore={() => void schemaObjectsQuery.fetchNextPage()}
                        onExpandTable={(table) => void loadSchemaDetail(table)}
                        detailLoadError={schemaDetailError}
                        onDismissDetailLoadError={() => setSchemaDetailError("")}
                        onInsert={insertSchemaText}
                      />
                    </div>
                  </div>

                  <Nl2SqlExecutionOptionsPanel
                    disabled={active}
                    engine={engine}
                    includeInterpretation={includeInterpretation}
                    includeShowPrompt={includeShowPrompt}
                    open={executionOptionsOpen}
                    useOntologyContext={useOntologyContext}
                    onIncludeInterpretationChange={setIncludeInterpretation}
                    onIncludeShowPromptChange={setIncludeShowPrompt}
                    onOpenChange={setExecutionOptionsOpen}
                    onRewriteUseGlossaryChange={setRewriteUseGlossary}
                    onUseOntologyContextChange={setUseOntologyContext}
                    rewriteUseGlossary={rewriteUseGlossary}
                  />
                  {rewriteData && rewriteChanged && (
                    <div className="grid gap-3 rounded-md border border-primary/30 bg-card p-3">
                      <dl className="grid gap-2 text-sm">
                        <div>
                          <dt className="font-medium text-muted">{t("nl2sql.session.originalQuestion")}</dt>
                          <dd className="mt-1">
                            <QuestionText value={question} variant="detail" maxLines={3} expandable />
                          </dd>
                        </div>
                        <div>
                          <dt className="font-medium text-muted">{t("nl2sql.session.suggestedQuestion")}</dt>
                          <dd className="mt-1">
                            <QuestionText value={rewriteData.rewritten_question} variant="detail" maxLines={3} expandable />
                          </dd>
                        </div>
                      </dl>
                      {/* API 応答に warnings が無くても画面全体を落とさない(防御)。 */}
                      {(rewriteData.warnings ?? []).length > 0 && (
                        <div className="grid gap-2">
                          {(rewriteData.warnings ?? []).map((warning) => (
                            <Banner key={warning} severity="warning">
                              {warning}
                            </Banner>
                          ))}
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-2">
                        {/* source（deterministic 等）は内部識別子なので画面には出さない。 */}
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

                  {showSimilarHistoryPanel && (
                    <section className="w-full min-w-0 max-w-full overflow-hidden rounded-md border border-border bg-card">
                      <Button
                        type="button"
                        variant="ghost"
                        size="md"
                        className="min-h-11 w-full min-w-0 max-w-full justify-between rounded-none px-4 text-left"
                        aria-expanded={similarHistoryOpen}
                        aria-controls="nl2sql-similar-history"
                        onClick={() => setSimilarHistoryOpen((current) => !current)}
                      >
                        <span className="flex min-w-0 flex-wrap items-center gap-2">
                          <span className="flex min-w-0 items-center gap-2">
                            <BookOpenText size={16} className="shrink-0 text-foreground" aria-hidden="true" />
                            <span className="truncate">
                              {similarHistoryLoading
                                ? t("nl2sql.similar.loading")
                                : t("nl2sql.similar.title")}
                            </span>
                          </span>
                          <StatusBadge variant="success" label={t("nl2sql.similar.goodOnly")} />
                        </span>
                        <DisclosureChevron
                          expanded={similarHistoryOpen}
                          size={16}
                        />
                      </Button>
                      {/* aria-controls の参照先を常時レンダし、閉時は hidden にする。 */}
                      <div
                        id="nl2sql-similar-history"
                        data-testid="nl2sql-similar-history"
                        hidden={!similarHistoryOpen}
                        className="grid gap-3 border-t border-border p-3 text-sm text-foreground"
                      >
                        {similarHistory.length === 0 && !similarHistoryLoading ? (
                          <EmptyState
                            title={t("nl2sql.similar.emptyTitle")}
                            hint={t("nl2sql.similar.emptyHint")}
                          />
                        ) : (
                          similarHistory.slice(0, 2).map((entry) => (
                            <article
                              key={entry.item.id}
                              data-testid="nl2sql-similar-history-item"
                              className="grid w-full min-w-0 max-w-full gap-2 overflow-hidden rounded-md bg-background p-3"
                            >
                              <div className="grid w-full min-w-0 max-w-full gap-2 sm:flex sm:flex-wrap sm:items-start sm:justify-between">
                                <div className="min-w-0 sm:flex-1 sm:basis-0">
                                  <QuestionText
                                    value={entry.item.question}
                                    variant="select"
                                    maxLines={1}
                                    testId="nl2sql-similar-history-question"
                                  />
                                  <p className="mt-1 text-xs text-muted">{entry.reason}</p>
                                </div>
                                <span className="w-fit rounded-md bg-card px-2 py-1 text-xs font-medium text-foreground sm:shrink-0">
                                  {t("nl2sql.similar.score", {
                                    score: Math.round(entry.score * 100),
                                  })}
                                </span>
                              </div>
                              {/* スクロール領域はキーボードでも操作できるよう focus 可能にする
                                  (WCAG 2.1.1)。 */}
                              <pre
                                tabIndex={0}
                                role="region"
                                aria-label={t("nl2sql.similar.title")}
                                className="max-h-28 overflow-auto rounded-md border border-border bg-card p-2 text-sm leading-6 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                              >
                                <code>{entry.item.executable_sql || entry.item.generated_sql}</code>
                              </pre>
                            </article>
                          ))
                        )}
                      </div>
                    </section>
                  )}

                  {/* 主アクションバー(button spec §2/§4): size lg、primary → secondary、
                      border-t で末尾に区切る。 */}
                  <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                    <Button
                      type="button"
                      variant="primary"
                      size="lg"
                      loading={jobActive}
                      disabled={!question.trim() || active || !profileSelectionReady}
                      onClick={() => void submit()}
                    >
                      <Play size={16} aria-hidden="true" />
                      <span>{t("nl2sql.action.run")}</span>
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      size="lg"
                      disabled={active}
                      onClick={() => {
                        setSelection(emptySelection());
                        setQuestion("");
                        setResult(null);
                        setRewriteData(null);
                        setRewriteUseGlossary(false);
                        setRewriteExtraPrompt("");
                        setUseOntologyContext(true);
                        setIncludeInterpretation(true);
                        setIncludeShowPrompt(false);
                        setExecutionOptionsOpen(false);
                        clearTrackedJob();
                        setSelectAiRoleOverride("");
                        setSelectAiInstructionsOverride("");
                        setSelectAiAdvancedOpen(false);
                        setSelectAiRoleAdvancedOpen(false);
                        setPageError(null);
                        setSchemaDetailError("");
                        setActionError("");
                      }}
                    >
                      <RotateCcw size={16} aria-hidden="true" />
                      <span>{t("nl2sql.action.reset")}</span>
                    </Button>
                  </div>

                  <ActionResultRegion
                    loading={actionBusy}
                    operationKey={actionOperationKey}
                    errorMessage={actionError}
                    errorAction={
                      actionError && currentScopedSchemaEmpty && canImportSampleData ? (
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
                      ) : undefined
                    }
                    testId="nl2sql-action-feedback"
                  />

                </div>
              </section>
        </section>

        {submitting || detecting ? (
          <TimedLoadingState
            label={submitting ? t("nl2sql.action.run") : t("nl2sql.recommend.autoDetect")}
            operationKey={submitting ? "submit" : "profile-detect"}
            placement="result"
            testId="nl2sql-foreground-processing"
            activityIcon="none"
          />
        ) : null}

        <OperationStatusStrip
          job={job}
          profileId={profileId}
          startedAtMs={jobStartedAt}
          catalogEmpty={catalog !== null && catalog.tables.length === 0}
          importingSample={importingSample}
          onImportSample={canImportSampleData ? importSampleData : undefined}
          sampleImportUnavailableHint={
            canImportSampleData ? "" : t("nl2sql.sample.importReadOnlyHint")
          }
          onCancelJob={requestJobCancel}
          cancelRequesting={cancelRequesting}
        />

        <Nl2SqlResultTable results={result?.results ?? null} />
        <SelectAiFeedbackAddPanel
          result={result}
          history={latestHistory}
          questionText={question}
          onSaved={refreshHistory}
        />
      </div>
    </>
  );
}
