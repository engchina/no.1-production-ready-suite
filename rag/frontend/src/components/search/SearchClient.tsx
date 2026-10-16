"use client";

import {
  DisclosureChevron,
  PageBody,
  PageHeader,
  Banner,
  Button,
  FieldActionRow,
  FormGrid,
  TextareaField,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  SelectField,
  type SelectFieldOption,
  Switch,
  InfoTip,
  ToggleChip,
  TextField,
  TimedLoadingState,
  Skeleton,
  StatusBadge,
  RunStopButton,
} from "@production-ready/ui";
import {
  Plus,
  Search as SearchIcon,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { SearchAnswerProfileSelect, SearchAnswerProfileSelectSkeleton } from "@/components/search-answer-profiles/SearchAnswerProfileSelect";
import { AnswerCitations, CitationList } from "./CitationList";
import {
  buildFeedbackContentSnapshot,
  FeedbackControls,
} from "@/components/feedback/FeedbackControls";
import { ApiErrorState, EmptyState, ErrorState } from "@/components/StateViews";
import {
  api,
  type ApprovedFaqSuggestionData,
  type RetrievedChunk,
  type SearchDiagnostics,
} from "@/lib/api";
import { streamSearch } from "@/lib/search-stream";
import { answerProcessStepsFromStages, type AnswerStageRecord } from "@/lib/answer-process";
import { isSubmitEnter } from "@/lib/keyboard";
import { evidenceGroups } from "@/lib/evidence-groups";
import { t } from "@/lib/i18n";
import { answerModelHelpKey, answerModelLabel } from "@/lib/answer-models";
import { APP_ROUTES } from "@/lib/routes";
import {
  useSearchAnswerProfiles,
  useSearchAnswerModels,
  useSearchExtractionFields,
} from "@/lib/queries";
import { isNullableString, isOneOf, useWorkspaceState } from "@/lib/workspace-state";
import { AnswerNotices } from "./AnswerDetailsPanel";
import { AnswerProcess } from "./AnswerProcess";
import { parseAnswerDiagnostics, unreadSectionLinks } from "@/lib/answer-diagnostics";
import { canOpenDocumentDetail } from "@/lib/route-permissions";
import { useAuth } from "@/components/security/AuthProvider";
import { AnswerText } from "./AnswerText";
import { QuerySuggestions } from "./QuerySuggestions";
import { ApprovedFaqAnswer, ApprovedFaqSuggestions } from "./ApprovedFaqSuggestions";
import { ExtractionFieldFilters } from "./ExtractionFieldFilters";
import {
  EXTRACTION_FIELD_FILTER_KEY,
  type ExtractionFieldFilterRow,
  extractionFieldConditionLabel,
  extractionFieldConditions,
  extractionFieldFilterValue,
  isActiveExtractionFieldFilterRow,
  isExtractionFieldFilterRows,
  parseExtractionFieldFilterValue,
} from "./extraction-field-filters";

type Phase = "idle" | "streaming" | "done" | "cancelled" | "error";

interface Meta {
  trace_id: string;
  elapsed_ms: number;
  guardrail_warnings: string[];
  diagnostics: Partial<SearchDiagnostics> | null;
}

interface SearchRun {
  /** この実行で回答を生成したか（実行後にスイッチを変えても表示は実行時のまま）。 */
  generateAnswer: boolean;
  startedAtMs: number;
  startedAtIso: string;
  endedAtMs: number | null;
  traceId: string | null;
  /** 工程のイベント（受け取った時刻つき。処理の経過の行を作る）。 */
  stages: AnswerStageRecord[];
}

/**
 * 検索 1 回の条件。「検索」は入力欄・選択から作り、表示中の結果への操作（再試行・類似問を使用しない・
 * 「自動」の条件を外す）はこの条件で送り直す（入力欄を書き換えていても前の質問を送る。#914）。
 */
interface SearchRequestSnapshot {
  query: string;
  searchAnswerProfileId: string;
  generateAnswer: boolean;
  /** 回答のモデル（空は既定のテキストモデル）。 */
  answerModelId: string;
  topK: TopKOption;
  filters: Record<string, string>;
  /** 質問から読み取った条件のうち、利用者が外した項目（#652）。 */
  excludedAutoFields: string[];
}

const TOP_K_OPTIONS = ["5", "10", "20", "50"] as const;
const DEFAULT_TOP_K = "20";
type TopKOption = (typeof TOP_K_OPTIONS)[number];
const TOP_K_SELECT_OPTIONS = TOP_K_OPTIONS.map((option) => ({
  value: option,
  label: option,
})) satisfies SelectFieldOption<TopKOption>[];

/**
 * RAG 検索画面。チャットと同じ工程（質問の理解・拡張・検索・rerank）で検索し、既定は検索結果までを表示する。
 * 「LLM で回答を生成する」をオンにしたときだけ、チャットと同じく回答（CRAG を含む）まで行う（#649）。
 */
export function SearchClient() {
  // 「回答に未反映の関連章節」の行から章節の原文を開くリンクは、文書の詳細を開ける利用者だけ（#2040・#303）。
  const canOpenSections = canOpenDocumentDetail(useAuth().hasPermission);
  // 入力中の質問・検索・回答プロファイル・詳細条件は、ページを行き来しても再読込しても残す（workspace-state.md）。
  // 回答・引用などの結果は保存せず、戻っただけで検索を送り直さない。
  const [query, setQuery] = useWorkspaceState("search.query", "");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<RetrievedChunk[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  // 検索の失敗（要約・次の操作・「詳細」の trace_id などを出す。#2023）。
  const [searchError, setSearchError] = useState<unknown>(null);
  // 文書の有効期間の基準日（YYYY-MM-DD。空は今日）。backend の検索 filters の as_of。
  const [asOf, setAsOf] = useWorkspaceState<string>("search.asOf", "", isString);
  const [periodOpen, setPeriodOpen] = useState(false);
  // 抽出項目の値の条件（#549）。行は作業状態に残し、項目の型は選んだ検索・回答プロファイルの KB の定義から引く。
  const [extractionRows, setExtractionRows] = useWorkspaceState<ExtractionFieldFilterRow[]>(
    "search.extractionFields",
    [],
    isExtractionFieldFilterRows
  );
  const [extractionOpen, setExtractionOpen] = useState(false);
  const [topK, setTopK] = useWorkspaceState<TopKOption>("search.topK", DEFAULT_TOP_K, isOneOf(TOP_K_OPTIONS));
  const [advancedOpen, setAdvancedOpen] = useWorkspaceState("search.advancedOpen", false);
  // 回答の生成は既定でオフ（検索結果までを表示する。#649）。選んだ値は作業状態に残す。
  const [generateAnswer, setGenerateAnswer] = useWorkspaceState("search.generateAnswer", false);
  // 回答のモデル（既定のテキストモデルか既定の Vision モデル。比較はしない。#675）。
  // 候補に無い保存値（モデルの設定が変わった後など）は既定（テキスト）として扱う。
  const [storedAnswerModelId, setAnswerModelId] = useWorkspaceState("search.answerModelId", "");
  const answerModels = useSearchAnswerModels().data ?? [];
  const answerModelId = answerModels.some((model) => model.model_id === storedAnswerModelId)
    ? storedAnswerModelId
    : "";
  const [appliedFilters, setAppliedFilters] = useState<Record<string, string>>({});
  // 対象の検索・回答プロファイルは 1 つ（#635）。チャットと同じく ID を 1 つだけ作業状態に残す。
  const [searchAnswerProfileId, setSearchAnswerProfileId] = useWorkspaceState<string | null>(
    "search.searchAnswerProfileId",
    null,
    isNullableString
  );
  const [scopeError, setScopeError] = useState("");
  const [run, setRun] = useState<SearchRun | null>(null);
  // 表示中の回答を生成したときの検索・回答プロファイル。回答・引用の評価はこの検索・回答プロファイルへ送る。
  // 検索後に選択を変えても、表示中の回答の評価先は変えない（#285）。
  const [answerSearchAnswerProfileId, setAnswerSearchAnswerProfileId] = useState<string | null>(null);
  // 直前の送信が類似 FAQ の提示を飛ばしたか。エラーの再試行を同じ操作にする（#285）。
  const [lastSkipFaq, setLastSkipFaq] = useState(false);
  // 直前に送った検索の条件。再試行・類似問を使用しない・「自動」の条件を外すはこれで送り直す（#914）。
  // 外した「自動」の条件（#652）もここに持ち、入力欄から新しく検索するたびに空に戻す。
  const [lastRequest, setLastRequest] = useState<SearchRequestSnapshot | null>(null);
  const [faqSuggestions, setFaqSuggestions] = useState<ApprovedFaqSuggestionData[] | null>(null);
  const [faqAnswer, setFaqAnswer] = useState<ApprovedFaqSuggestionData | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const navigate = useNavigate();
  const searchAnswerProfilesQuery = useSearchAnswerProfiles({ status: "ACTIVE", limit: 50, offset: 0 });
  const searchAnswerProfiles = searchAnswerProfilesQuery.data?.items ?? [];
  // 復元した検索・回答プロファイルがアーカイブ・削除されていたら選択を外す（別の対象へ置き換えない）。
  const searchAnswerProfileMissing =
    Boolean(searchAnswerProfileId) &&
    Boolean(searchAnswerProfilesQuery.data) &&
    !searchAnswerProfilesQuery.data?.has_next &&
    !searchAnswerProfiles.some((view) => view.id === searchAnswerProfileId);
  // 選んだ検索・回答プロファイルが参照 KB を持たないなら検索しない（利用者の全 KB を検索しない。#304）。
  // backend も 409 で理由を返すが、送信する前にこの場で理由を示す。
  const selectedSearchAnswerProfile = searchAnswerProfiles.find((view) => view.id === searchAnswerProfileId);
  const selectedWithoutKnowledgeBases = selectedSearchAnswerProfile?.knowledge_base_count === 0;
  // 画面を離れたら生成中の検索を止める（backend の pipeline と LLM を無駄に動かし続けない。#285）。
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (searchAnswerProfileMissing) setSearchAnswerProfileId(null);
  }, [searchAnswerProfileMissing, setSearchAnswerProfileId]);
  const hasPeriodFilter = Boolean(asOf.trim());
  const hasExtractionFieldFilters = extractionRows.some(isActiveExtractionFieldFilterRow);
  const extractionFieldsQuery = useSearchExtractionFields(
    searchAnswerProfileId,
    extractionOpen || hasExtractionFieldFilters
  );
  const fieldFilter = extractionFieldConditions(
    extractionRows,
    extractionFieldsQuery.data?.fields
  );
  const hasFilters = hasPeriodFilter || hasExtractionFieldFilters;
  // 開閉は利用者の操作だけで決める。閉じていても条件は効くので、見出しに「設定中」を出す（#461）。
  const periodVisible = periodOpen;
  const hasSearchTuning = topK !== DEFAULT_TOP_K;
  const hasAdvancedSettings = hasFilters || hasSearchTuning;

  /** 入力欄・選択から検索の条件を作って送る（「検索」ボタンと Enter）。 */
  const runSubmit = async () => {
    const trimmed = query.trim();
    if (!trimmed || phase === "streaming") return;
    if (!searchAnswerProfileId) {
      setScopeError(t("searchAnswerProfiles.scope.required"));
      return;
    }
    if (selectedWithoutKnowledgeBases) return;
    // 誤りのある項目の条件を黙って外して検索しない。条件を開いて最初の誤りへフォーカスする。
    const firstFieldError = extractionRows.find((row) => fieldFilter.errors[row.key]);
    if (firstFieldError) {
      setAdvancedOpen(true);
      setExtractionOpen(true);
      requestAnimationFrame(() => document.getElementById(`${firstFieldError.key}-name`)?.focus());
      return;
    }
    setScopeError("");
    await runRequest(
      {
        query: trimmed,
        searchAnswerProfileId,
        generateAnswer,
        answerModelId: generateAnswer ? answerModelId : "",
        topK,
        filters: buildSearchFilters({
          asOf,
          extractionFields: extractionFieldFilterValue(fieldFilter.conditions),
        }),
        excludedAutoFields: [],
      },
      false
    );
  };

  /**
   * 検索の条件で、類似 FAQ の照会と検索の stream を行う。押した時点で実行中にし、照会の間も「停止」と
   * 進行を出す（照会は embedding を呼ぶことがあり時間がかかる。#915）。
   */
  const runRequest = async (request: SearchRequestSnapshot, skipFaq: boolean) => {
    if (phase === "streaming") return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAtMs = runStartedAtMs();

    setLastRequest(request);
    setSubmittedQuery(request.query);
    setLastSkipFaq(skipFaq);
    setFaqSuggestions(null);
    setFaqAnswer(null);
    setPhase("streaming");
    setAnswer("");
    setCitations([]);
    setMeta(null);
    setSearchError(null);
    setAnswerSearchAnswerProfileId(request.searchAnswerProfileId);
    setAppliedFilters(request.filters);
    setRun({
      generateAnswer: request.generateAnswer,
      startedAtMs,
      startedAtIso: new Date(startedAtMs).toISOString(),
      endedAtMs: null,
      traceId: null,
      stages: [],
    });

    try {
      if (!skipFaq) {
        // 検索・回答プロファイルの承認済み FAQ に類似問があれば、回答生成の前に提示する(rag_poc の類似問)。
        let suggestions: ApprovedFaqSuggestionData[] = [];
        try {
          suggestions = (
            await api.suggestApprovedFaq(
              request.searchAnswerProfileId,
              request.query,
              "search",
              controller.signal
            )
          ).suggestions ?? [];
        } catch {
          // 類似問の照会に失敗しても通常の回答生成は続ける（停止したときは下で抜ける）。
        }
        if (controller.signal.aborted) return;
        if (suggestions.length > 0) {
          // 選ぶまで検索しない。実行の表示を消して FAQ の提示を出す。
          setFaqSuggestions(suggestions);
          setRun(null);
          setPhase("idle");
          return;
        }
      }

      await streamSearch(
        {
          query: request.query,
          top_k: Number(request.topK),
          search_answer_profile_id: request.searchAnswerProfileId,
          generate_answer: request.generateAnswer,
          ...(request.generateAnswer && request.answerModelId
            ? { model_id: request.answerModelId }
            : {}),
          ...(request.excludedAutoFields.length
            ? { auto_field_filter_excluded: request.excludedAutoFields }
            : {}),
          ...(Object.keys(request.filters).length ? { filters: request.filters } : {}),
        },
        {
          onStage: (stage) =>
            setRun((current) =>
              current
                ? {
                    ...current,
                    traceId: stage.trace_id || current.traceId,
                    stages: [...current.stages, { ...stage, receivedAtMs: Date.now() }],
                  }
                : current
            ),
          onMetadata: (m) => {
            setMeta({
              trace_id: m.trace_id,
              elapsed_ms: m.elapsed_ms,
              guardrail_warnings: m.guardrail_warnings,
              diagnostics: m.diagnostics ?? null,
            });
            setRun((current) =>
              current
                ? {
                    ...current,
                    traceId: current.traceId ?? m.trace_id,
                  }
                : current
            );
          },
          onDelta: (text) => setAnswer((prev) => prev + text),
          onReplace: (text) => setAnswer(text),
          onCitations: (list) => setCitations(list),
          onDone: () => {
            finishRun();
            setPhase("done");
            abortRef.current = null;
          },
        },
        controller.signal
      );
      setPhase((current) => (current === "streaming" ? "done" : current));
    } catch (error) {
      if (controller.signal.aborted) return;
      setSearchError(error);
      finishRun();
      setPhase("error");
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  };

  // pointerdown と click の両方から呼ばれる。類似問の照会を await する間も二重送信しないよう ref で守る。
  const submittingRef = useRef(false);
  const guardSubmit = async (run: () => Promise<void>) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    try {
      await run();
    } finally {
      submittingRef.current = false;
    }
  };
  const submit = () => guardSubmit(runSubmit);
  /**
   * 表示中の結果（エラー・類似 FAQ の提示）を出した検索を、同じ条件で送り直す。入力欄・選択を書き換えて
   * いても使わない（#914）。`excludedAutoFields` を渡すと、外した「自動」の条件を差し替える。
   */
  const resubmit = (skipFaq: boolean, excludedAutoFields?: string[]) =>
    guardSubmit(async () => {
      if (!lastRequest) return;
      await runRequest(
        excludedAutoFields ? { ...lastRequest, excludedAutoFields } : lastRequest,
        skipFaq
      );
    });

  const cancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    finishRun();
    setPhase("cancelled");
    setSearchError(null);
  };
  const finishRun = () => {
    setRun((current) =>
      current && current.endedAtMs == null ? { ...current, endedAtMs: Date.now() } : current
    );
  };
  const clearFilters = () => {
    setAsOf("");
    setPeriodOpen(false);
    setExtractionRows([]);
    setExtractionOpen(false);
    setTopK(DEFAULT_TOP_K);
    setAdvancedOpen(false);
  };

  const noResults = phase === "done" && citations.length === 0;
  const answerMode = run?.generateAnswer ?? false;
  const isStreaming = phase === "streaming";
  const feedbackSnapshot = buildFeedbackContentSnapshot(submittedQuery, answer, citations);
  // 処理の経過の行（工程のイベント → 1 工程 = 1 行。完了の後は回答の記録の LLM の回数・結果の説明を足す。#2096）。
  const processSteps = run
    ? answerProcessStepsFromStages(run.stages, {
        terminal:
          phase === "done" ? "done" : phase === "cancelled" ? "cancelled" : phase === "error" ? "failed" : null,
        executionSteps: parseAnswerDiagnostics(meta?.diagnostics?.answer)?.steps ?? [],
      })
    : [];
  // 回答を作らない検索の結果のまとまり（#2097）。回答の根拠は AnswerCitations が作る。
  const evidence = phase === "done" && !answerMode ? evidenceGroups(citations, meta?.diagnostics?.answer) : null;
  // 処理の経過（#2096。チャットと同じ AnswerProcess）。実行中も終わった後も回答・検索結果の上の同じ場所に 1 つ出す
  // （作成中の「N ステップ完了」が終わると「処理の経過」になり、完了したら一覧を閉じる。末尾に出し直さない。
  // #2115 / #2118）。「詳細」は回答の記録の詳細（回答を作ったときは標準回答による評価も）でチャットと同じ。
  const running = phase === "streaming";
  const processView = run ? (
    <AnswerProcess
      // 最初の工程が届くまでは「検索の準備」を今の工程にする（止まって見えない。#1176）。
      steps={
        !running || processSteps.length > 0
          ? processSteps
          : [{ id: "prepare", label: t("answer.progress.preparing"), status: "running", startedAt: run.startedAtIso }]
      }
      progressKey={!running || processSteps.length > 0 ? "steps" : "prepare"}
      active={running}
      startedAt={running ? run.startedAtMs : undefined}
      elapsedMs={
        running ? undefined : (meta?.elapsed_ms ?? (run.endedAtMs != null ? run.endedAtMs - run.startedAtMs : null))
      }
      diagnostics={running ? undefined : meta?.diagnostics?.answer}
      traceId={answerMode && phase === "done" ? meta?.trace_id : null}
      showNotices={!answerMode}
      showAutoFieldFilter={false}
      testId="search-run-progress"
    />
  ) : null;
  const conditionChips = (
    <SearchConditionChips
      appliedFilters={appliedFilters}
      meta={meta}
      phase={phase}
      disabled={isStreaming}
      onRemoveAuto={(name) => void resubmit(true, [...(lastRequest?.excludedAutoFields ?? []), name])}
    />
  );

  return (
    <div>
      <PageHeader wide title={t("nav.search")} subtitle={t("search.initial")} />
      <PageBody wide>
        <section className="space-y-6">
          {searchAnswerProfilesQuery.isLoading ? (
            <Card>
              <CardContent className="pt-5">
                <TimedLoadingState
                  label={t("search.searchAnswerProfileLoading")}
                  operationKey="search-search-answer-profiles-load"
                  framed={false}
                  testId="search-search-answer-profiles-loading"
                >
                  <SearchAnswerProfileSelectSkeleton />
                  <Skeleton className="h-[var(--button-height-md)] w-full" />
                </TimedLoadingState>
              </CardContent>
            </Card>
          ) : searchAnswerProfilesQuery.isError ? (
            <ErrorState
              message={t("search.searchAnswerProfileError")}
              onRetry={() => void searchAnswerProfilesQuery.refetch()}
            />
          ) : searchAnswerProfiles.length === 0 ? (
            <Card>
              <CardContent className="pt-5">
                <EmptyState
                  title={t("search.searchAnswerProfileRequired.title")}
                  hint={t("search.searchAnswerProfileRequired.hint")}
                  action={
                    <Button onClick={() => navigate(`${APP_ROUTES.searchAnswerProfiles}?id=new`)} icon={Plus}>
                      {t("search.searchAnswerProfileRequired.cta")}
                    </Button>
                  }
                />
              </CardContent>
            </Card>
          ) : (
            <>
          {/* 検索条件。カードの余白は CardContent の既定（見出しの無いカードは上も同じ pt-5）で、
              チャットの検索・回答プロファイルのカードと 375px でも内側の幅をそろえる（#1778）。 */}
          <Card>
            <CardContent className="space-y-4 pt-5">
              <SearchAnswerProfileSelect
                id={SEARCH_ANSWER_PROFILE_SCOPE_INPUT_ID}
                items={searchAnswerProfiles}
                value={searchAnswerProfileId}
                onChange={(next) => {
                  setSearchAnswerProfileId(next);
                  setScopeError("");
                }}
                disabled={isStreaming}
                error={
                  scopeError ||
                  (selectedWithoutKnowledgeBases ? t("searchAnswerProfiles.scope.noKnowledgeBases") : "")
                }
              />

              <div className="rounded-md border border-border bg-surface-sunken">
                <button
                  type="button"
                  aria-expanded={advancedOpen}
                  aria-controls="search-advanced-conditions"
                  onClick={() => setAdvancedOpen((open) => !open)}
                  className="flex w-full cursor-pointer items-center gap-1.5 px-3 py-2 text-left text-sm font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  <SlidersHorizontal size={14} className="text-accent-fg" aria-hidden />
                  {t("search.filters.advanced")}
                  {!advancedOpen && hasAdvancedSettings ? (
                    <StatusBadge variant="info" label={t("search.filters.active")} />
                  ) : null}
                  <DisclosureChevron
                    expanded={advancedOpen}
                    size={14}
                    className="ml-auto text-fg-muted"
                  />
                </button>
                {advancedOpen ? (
                <fieldset id="search-advanced-conditions" className="space-y-4 border-t border-border p-3">
                  <legend className="sr-only">{t("search.filters.title")}</legend>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <SelectField
                      id="search-top-k"
                      width="xs"
                      label={t("search.tuning.topK")}
                      value={topK}
                      options={TOP_K_SELECT_OPTIONS}
                      helper={t("search.tuning.topKHelp")}
                      onValueChange={setTopK}
                      className="[&_label]:text-xs"
                      buttonClassName="bg-surface"
                    />
                  </div>

                  <div className="rounded-md border border-border bg-surface">
                    <button
                      type="button"
                      aria-expanded={periodVisible}
                      aria-controls="search-period-filters"
                      onClick={() => setPeriodOpen((open) => !open)}
                      className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    >
                      <span>{t("search.filters.periodGroup")}</span>
                      <span className="flex items-center gap-2">
                        {!periodVisible && hasPeriodFilter ? (
                          <StatusBadge variant="info" label={t("search.filters.active")} />
                        ) : null}
                        <DisclosureChevron expanded={periodVisible} size={14} className="text-fg-muted" />
                      </span>
                    </button>
                    {periodVisible ? (
                      <div id="search-period-filters" className="space-y-3 border-t border-border p-3">
                        <p className="text-xs leading-relaxed text-fg-muted">
                          {t("search.filters.periodHelper")}
                        </p>
                        {/* 基準日は日付の段（sm。#1891）。 */}
                        <FormGrid>
                          <TextField
                            id="search-as_of"
                            width="sm"
                            type="date"
                            label={t("search.filters.as_of")}
                            value={asOf}
                            onValueChange={setAsOf}
                          />
                        </FormGrid>
                      </div>
                    ) : null}
                  </div>

                  <div className="rounded-md border border-border bg-surface">
                    <button
                      type="button"
                      aria-expanded={extractionOpen}
                      aria-controls="search-extraction-field-filters"
                      onClick={() => setExtractionOpen((open) => !open)}
                      className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    >
                      <span>{t("search.filters.fields.group")}</span>
                      <span className="flex items-center gap-2">
                        {!extractionOpen && hasExtractionFieldFilters ? (
                          <StatusBadge variant="info" label={t("search.filters.active")} />
                        ) : null}
                        <DisclosureChevron expanded={extractionOpen} size={14} className="text-fg-muted" />
                      </span>
                    </button>
                    {extractionOpen ? (
                      <div id="search-extraction-field-filters" className="space-y-3 border-t border-border p-3">
                        <p className="text-xs leading-relaxed text-fg-muted">
                          {t("search.filters.fields.helper")}
                        </p>
                        {!searchAnswerProfileId ? (
                          <p className="text-sm text-fg-muted">{t("search.filters.fields.chooseScope")}</p>
                        ) : (
                          <ExtractionFieldFilters
                            fieldsState={
                              extractionFieldsQuery.isPending
                                ? { status: "pending" }
                                : extractionFieldsQuery.isError
                                  ? {
                                      status: "error",
                                      error: extractionFieldsQuery.error,
                                      retry: () => void extractionFieldsQuery.refetch(),
                                    }
                                  : { status: "ready", fields: extractionFieldsQuery.data.fields }
                            }
                            rows={extractionRows}
                            onRowsChange={setExtractionRows}
                            errors={fieldFilter.errors}
                            disabled={isStreaming}
                          />
                        )}
                      </div>
                    ) : null}
                  </div>

                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="secondary"
                      size="md"
                      onClick={clearFilters}
                      disabled={!hasAdvancedSettings || isStreaming}
                      className="w-full sm:w-auto" icon={X}>
                      {t("search.filters.clear")}
                    </Button>
                  </div>
                </fieldset>
                ) : null}
              </div>

              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p id="search-generate-answer-label" className="text-sm font-medium text-fg">
                    {t("search.generateAnswer.label")}
                  </p>
                  <p id="search-generate-answer-help" className="mt-0.5 text-xs leading-relaxed text-fg-muted">
                    {t("search.generateAnswer.help")}
                  </p>
                </div>
                <Switch
                  checked={generateAnswer}
                  onCheckedChange={setGenerateAnswer}
                  disabled={isStreaming}
                  aria-labelledby="search-generate-answer-label"
                  aria-describedby="search-generate-answer-help"
                  data-testid="search-generate-answer"
                />
              </div>


              {/* 質問と検索の行はフォームの最後（詳細条件・スイッチの下。#413）。チャットの入力欄と同じく
                  複数行の入力欄（2 行）と lg のボタンを FieldActionRow に置く（ボタンは入力欄の下端にそろい、
                  375px では下に全幅。#613）。検索と停止は同じボタンで、実行中は同じ位置で「停止」になる。
                  Enter で検索、Shift+Enter で改行（IME の変換を確定する Enter では検索しない。#459）。
                  実行中の Enter は submit が無視し、停止しない。 */}
              <div className="space-y-2 border-t border-border pt-4">
                {/* 回答するモデル。チャットと同じく入力欄のすぐ上のチップの行（#686）。比較はしないので
                    1 つだけ選べ、もう一度押すと未選択（既定のテキストモデル）に戻る。 */}
                {generateAnswer && answerModels.length > 0 ? (
                  <div className="flex flex-wrap items-center gap-2" data-testid="search-answer-model">
                    {/* 説明は常設せず、ラベルの横の info アイコンから出す（#901）。 */}
                    <span className="inline-flex items-center gap-0.5">
                      <span className="text-xs font-medium text-fg-muted">
                        {t("search.answerModel.label")}
                      </span>
                      <InfoTip
                        label={t("search.answerModel.infoLabel")}
                        content={t(answerModelHelpKey(answerModels, "search.answerModel.help"))}
                        contentTestId="search-answer-model-help"
                        data-testid="search-answer-model-info"
                      />
                    </span>
                    {answerModels.map((model) => (
                      <ToggleChip
                        key={model.model_id}
                        selected={answerModelId === model.model_id}
                        disabled={isStreaming}
                        onClick={() =>
                          setAnswerModelId(answerModelId === model.model_id ? "" : model.model_id)
                        }
                      >
                        {answerModelLabel(model)}
                      </ToggleChip>
                    ))}
                  </div>
                ) : null}
                <FieldActionRow
                  actions={
                    <RunStopButton
                      running={isStreaming}
                      onRun={() => void submit()}
                      onStop={cancel}
                      runLabel={t("search.button")}
                      stopLabel={t("search.cancel")}
                      runIcon={SearchIcon}
                      size="lg"
                      testId="search-run-stop"
                    />
                  }
                >
                  <TextareaField
                    id="search-query"
                    width="full"
                    label={t("nav.search")}
                    labelHidden
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (isSubmitEnter(event) && !event.shiftKey) {
                        event.preventDefault();
                        void submit();
                      }
                    }}
                    // 質問の入力欄は 3 製品で 3.5 行（チャット・NL2SQL の質問・Agent の実行の目標と同じ。#2008）。
                    rows={3.5}
                    placeholder={t("search.placeholder")}
                    // ラベルは読み上げだけ（sr-only）なので、欄の上に余白を空けない。
                    className="space-y-0"
                  />
                </FieldActionRow>
                <QuerySuggestions
                  searchAnswerProfileId={searchAnswerProfileId}
                  query={query}
                  disabled={isStreaming}
                  onSelect={setQuery}
                />
              </div>
            </CardContent>
          </Card>

          {/* 状態別表示 */}
          {faqSuggestions ? (
            <ApprovedFaqSuggestions
              suggestions={faqSuggestions}
              onUse={(suggestion) => {
                setFaqSuggestions(null);
                setFaqAnswer(suggestion);
              }}
              onSkip={() => void resubmit(true)}
            />
          ) : faqAnswer ? (
            <ApprovedFaqAnswer suggestion={faqAnswer} />
          ) : phase === "idle" ? (
            <Card>
              <CardContent className="pt-5">
                <EmptyState title={t("search.initial")} hint={t("search.initialHint")} />
              </CardContent>
            </Card>
          ) : phase === "error" ? (
            <ApiErrorState
              error={searchError}
              fallback={t("search.error.failed")}
              onRetry={() => void resubmit(lastSkipFaq)}
            />
          ) : (
            <>
              {/* 安全チェック警告 */}
              {meta?.guardrail_warnings.length ? (
                <Banner severity="warning">
                  <span className="font-medium">{t("search.guardrail")}: </span>
                  {meta.guardrail_warnings.join(" / ")}
                </Banner>
              ) : null}

              {phase === "cancelled" ? (
                <div className="rounded-md border border-border bg-surface px-3 py-2 text-sm text-fg-muted" role="status">
                  {t("search.cancelled")}
                </div>
              ) : null}

              {/* 回答（スイッチがオンのときだけ。#649）。処理の経過 → 回答の本文 → 根拠の一覧（AI に渡した箇所）→ 評価 の
                  順で、チャットと同じ部品・並び（#2096 / #2097 / #2115）。処理の経過は実行中（工程の一覧が増え、今の工程に
                  Spinner と経過時間）も終わった後も同じ場所に出す。 */}
              {answerMode ? (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Sparkles size={16} className="text-accent-fg" aria-hidden />
                      {t("search.answer")}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {processView}
                    {conditionChips}
                    <AnswerText
                      text={answer || (phase === "cancelled" ? t("search.cancelledHint") : "")}
                      streaming={isStreaming}
                      citations={citations}
                      unreadSections={canOpenSections ? unreadSectionLinks(meta?.diagnostics?.answer) : []}
                      cursor={
                        isStreaming ? (
                          <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent-emphasis align-middle" />
                        ) : null
                      }
                    />
                    {/* 回答を読むときに要る案内（資料では決まらない値など）は回答の本文の直下（「詳細」に畳まない。#2096）。 */}
                    {meta && phase === "done" ? <AnswerNotices diagnostics={meta.diagnostics?.answer} /> : null}
                    <AnswerCitations
                      citations={citations}
                      diagnostics={phase === "done" ? meta?.diagnostics?.answer : null}
                      idPrefix="search"
                      cardProps={{
                        traceId: meta?.trace_id,
                        searchAnswerProfileId: answerSearchAnswerProfileId,
                        sourceSurface: "search",
                        contentSnapshot: feedbackSnapshot,
                      }}
                      className="border-t border-border pt-1"
                    />
                    {/* 評価は回答の枠の最後。根拠を確かめてから評価できるよう、その後に置く（チャットと同じ。
                        #1202 / #1420。page-archetypes.md §6.1）。 */}
                    {meta && phase === "done" ? (
                      <FeedbackControls
                        traceId={meta.trace_id}
                        searchAnswerProfileId={answerSearchAnswerProfileId}
                        targetType="answer"
                        sourceSurface="search"
                        contentSnapshot={feedbackSnapshot}
                      />
                    ) : null}
                  </CardContent>
                </Card>
              ) : run ? (
                // 回答を作らない検索: 処理の経過（実行中は今の工程と経過時間、終わった後は工程の一覧と「詳細」。
                // 同じ場所のまま切り替える。#2115）・検索の条件・検索できなかった理由（検索の準備が無いなど）。
                <Card>
                  <CardContent className="space-y-3 pt-5">
                    {processView}
                    {conditionChips}
                    {answer ? <Banner severity="warning">{answer}</Banner> : null}
                    {phase === "cancelled" ? <p className="text-sm text-fg-muted">{t("search.cancelledHint")}</p> : null}
                  </CardContent>
                </Card>
              ) : (
                // 実行の前（類似問の提示の間を含む）: 検索の条件だけ。
                <div className="space-y-3 empty:hidden">
                  {conditionChips}
                  {answer ? <Banner severity="warning">{answer}</Banner> : null}
                  {phase === "cancelled" ? <p className="text-sm text-fg-muted">{t("search.cancelledHint")}</p> : null}
                </div>
              )}

              {/* 引用 / no-results */}
              {noResults ? (
                <Card>
                  <CardContent className="pt-5">
                    <EmptyState title={t("search.noResults")} hint={t("search.noResultsHint")} />
                  </CardContent>
                </Card>
              ) : citations.length > 0 && !answerMode ? (
                // 回答を作らない検索は、検索結果が主な結果なので回答の枠の外に一覧で出す。
                <section>
                  <h2 className="mb-1 text-sm font-semibold text-fg">
                    {`${t("search.results")}（${citations.length}）`}
                  </h2>
                  <p className="mb-3 text-xs leading-relaxed text-fg-muted">{t("search.results.hint")}</p>
                  <CitationList
                    citations={citations}
                    groups={evidence}
                    idPrefix="search-results"
                    cardProps={{
                      traceId: meta?.trace_id,
                      searchAnswerProfileId: answerSearchAnswerProfileId,
                      sourceSurface: "search",
                      contentSnapshot: feedbackSnapshot,
                    }}
                  />
                </section>
              ) : null}

            </>
          )}
            </>
          )}
        </section>
      </PageBody>
    </div>
  );
}

/**
 * 実行を始めた時刻。`runRequest` はイベントの処理（検索・再試行など）からだけ呼ばれるが、await の前で
 * 時刻を取るため、React Compiler の lint が描画中の呼び出しと見なさないよう関数に分ける。
 */
function runStartedAtMs(): number {
  return Date.now();
}

/**
 * 質問から読み取って検索に足した条件（#652）。「自動」のチップで出し、外して検索し直せる。
 * 読み取った条件で見つからず外して検索したときは、その旨を出す。
 */
function AutoFieldFilterChips({
  diagnostics,
  disabled,
  onRemove,
}: {
  diagnostics: unknown;
  disabled: boolean;
  onRemove: (name: string) => void;
}) {
  const auto = parseAnswerDiagnostics(diagnostics)?.autoFieldFilter;
  if (!auto) return null;
  const names = [...new Set(auto.conditions.map((condition) => condition.name))];
  return (
    <div aria-label={t("search.answerDetails.autoFieldFilter")} className="mb-3 space-y-1.5" data-testid="auto-field-filter-chips">
      <p className="text-xs font-medium text-fg-muted">{t("search.answerDetails.autoFieldFilter")}</p>
      <div className="flex flex-wrap gap-1.5">
        {names.map((name) => {
          const label = auto.conditions
            .filter((condition) => condition.name === name)
            .map(extractionFieldConditionLabel)
            .join(" / ");
          return (
            <span
              key={name}
              data-surface-tint="info"
              className="inline-flex max-w-full items-center gap-0.5 rounded-full border border-border bg-info-subtle py-0.5 pl-2 text-xs font-medium leading-snug text-fg"
            >
              <span className="break-all">{t("search.autoFieldFilter.chip", { label })}</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                iconOnly
                icon={X}
                disabled={disabled}
                aria-label={t("search.autoFieldFilter.remove", { label })}
                onClick={() => onRemove(name)}
              />
            </span>
          );
        })}
      </div>
      {auto.relaxed ? (
        <Banner severity="info">{t("search.answerDetails.autoFieldFilterRelaxed")}</Banner>
      ) : null}
    </div>
  );
}

/** 検索に使った条件（指定した条件と、質問から読み取った「自動」の条件）。 */
function SearchConditionChips({
  appliedFilters,
  meta,
  phase,
  disabled,
  onRemoveAuto,
}: {
  appliedFilters: Record<string, string>;
  meta: Meta | null;
  phase: Phase;
  disabled: boolean;
  onRemoveAuto: (name: string) => void;
}) {
  return (
    <>
      <ActiveFilterChips filters={appliedFilters} />
      {meta && phase === "done" ? (
        <AutoFieldFilterChips diagnostics={meta.diagnostics?.answer} disabled={disabled} onRemove={onRemoveAuto} />
      ) : null}
    </>
  );
}

function ActiveFilterChips({ filters }: { filters: Record<string, string> }) {
  const chips = activeFilterChips(filters);
  if (!chips.length) return null;
  return (
    <div aria-label={t("search.filters.applied")} className="mb-3 space-y-1.5">
      <p className="text-xs font-medium text-fg-muted">{t("search.filters.applied")}</p>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((chip) => (
          <span
            key={chip.key}
            className="max-w-full break-all rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs font-medium leading-snug text-fg"
          >
            {chip.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function activeFilterChips(filters: Record<string, string>) {
  return [
    filters.as_of
      ? {
          key: "as_of",
          label: t("search.filters.appliedFilter", {
            label: t("search.filters.as_of"),
            value: filters.as_of,
          }),
        }
      : null,
    ...parseExtractionFieldFilterValue(filters[EXTRACTION_FIELD_FILTER_KEY]).map(
      (condition, index) => ({
        key: `${EXTRACTION_FIELD_FILTER_KEY}-${index}`,
        label: extractionFieldConditionLabel(condition),
      })
    ),
  ].flatMap((chip) => (chip ? [chip] : []));
}

function buildSearchFilters({
  asOf,
  extractionFields,
}: {
  asOf: string;
  extractionFields: string;
}): Record<string, string> {
  const filters: Record<string, string> = {};
  if (asOf.trim()) filters.as_of = asOf.trim();
  if (extractionFields) filters[EXTRACTION_FIELD_FILTER_KEY] = extractionFields;
  return filters;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

const SEARCH_ANSWER_PROFILE_SCOPE_INPUT_ID = "search-search-answer-profile-scope";
