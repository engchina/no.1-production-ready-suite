"use client";

import {
  DisclosureChevron,
  PageBody,
  PageHeader,
  Banner,
  Button,
  FieldActionRow,
  TextareaField,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  SelectField,
  type SelectFieldOption,
  Switch,
  TextField,
  TimedLoadingState,
  Skeleton,
  StatusBadge,
} from "@engchina/production-ready-ui";
import {
  Clock3,
  Plus,
  Search as SearchIcon,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { BusinessViewSelect, BusinessViewSelectSkeleton } from "@/components/business-views/BusinessViewSelect";
import { CitationCard } from "./CitationCard";
import {
  buildFeedbackContentSnapshot,
  FeedbackControls,
} from "@/components/feedback/FeedbackControls";
import { EmptyState, ErrorState } from "@/components/StateViews";
import {
  api,
  ApiError,
  type ApprovedFaqSuggestionData,
  type RetrievedChunk,
  type SearchDiagnostics,
} from "@/lib/api";
import { streamSearch, type SearchStageEvent } from "@/lib/search-stream";
import { answerStageLabel } from "@/lib/answer-progress";
import { isSubmitEnter } from "@/lib/keyboard";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useBusinessViews, useSearchExtractionFields } from "@/lib/queries";
import { formatDateTime } from "@/lib/format";
import { isNullableString, isOneOf, useWorkspaceState } from "@/lib/workspace-state";
import { RunStopButton } from "@/components/RunStopButton";
import { AnswerProgress } from "./AnswerProgress";
import { AnswerDetailsPanel } from "./AnswerDetailsPanel";
import { parseAnswerDiagnostics } from "@/lib/answer-diagnostics";
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
  stages: SearchStageEvent[];
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
  // 入力中の質問・業務ビュー・詳細条件は、ページを行き来しても再読込しても残す（workspace-state.md）。
  // 回答・引用などの結果は保存せず、戻っただけで検索を送り直さない。
  const [query, setQuery] = useWorkspaceState("search.query", "");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<RetrievedChunk[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [errorText, setErrorText] = useState("");
  const [classification, setClassification] = useWorkspaceState<ClassificationFilterValues>(
    "search.classification",
    EMPTY_CLASSIFICATION_FILTERS,
    isClassificationFilterValues
  );
  const [classificationOpen, setClassificationOpen] = useState(false);
  // 抽出項目の値の条件（#549）。行は作業状態に残し、項目の型は選んだ業務ビューの KB の定義から引く。
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
  const [appliedFilters, setAppliedFilters] = useState<Record<string, string>>({});
  // 対象の業務ビューは 1 つ（#635）。チャットと同じく ID を 1 つだけ作業状態に残す。
  const [businessViewId, setBusinessViewId] = useWorkspaceState<string | null>(
    "search.businessViewId",
    null,
    isNullableString
  );
  const [scopeError, setScopeError] = useState("");
  const [run, setRun] = useState<SearchRun | null>(null);
  // 表示中の回答を生成したときの業務ビュー。回答・引用の評価はこの業務ビューへ送る。
  // 検索後に選択を変えても、表示中の回答の評価先は変えない（#285）。
  const [answerBusinessViewId, setAnswerBusinessViewId] = useState<string | null>(null);
  // 直前の送信が類似 FAQ の提示を飛ばしたか。エラーの再試行を同じ操作にする（#285）。
  const [lastSkipFaq, setLastSkipFaq] = useState(false);
  // 質問から読み取った条件のうち、利用者が外した項目（#652）。新しく検索するたびに空に戻す。
  const [autoFieldExcluded, setAutoFieldExcluded] = useState<string[]>([]);
  const [faqSuggestions, setFaqSuggestions] = useState<ApprovedFaqSuggestionData[] | null>(null);
  const [faqAnswer, setFaqAnswer] = useState<ApprovedFaqSuggestionData | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const navigate = useNavigate();
  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 50, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  // 復元した業務ビューがアーカイブ・削除されていたら選択を外す（別の対象へ置き換えない）。
  const businessViewMissing =
    Boolean(businessViewId) &&
    Boolean(businessViewsQuery.data) &&
    !businessViewsQuery.data?.has_next &&
    !businessViews.some((view) => view.id === businessViewId);
  // 選んだ業務ビューが参照 KB を持たないなら検索しない（利用者の全 KB を検索しない。#304）。
  // backend も 409 で理由を返すが、送信する前にこの場で理由を示す。
  const selectedBusinessView = businessViews.find((view) => view.id === businessViewId);
  const selectedWithoutKnowledgeBases = selectedBusinessView?.knowledge_base_count === 0;
  // 画面を離れたら生成中の検索を止める（backend の pipeline と LLM を無駄に動かし続けない。#285）。
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (businessViewMissing) setBusinessViewId(null);
  }, [businessViewMissing, setBusinessViewId]);
  const hasClassificationFilters = Object.values(classification).some((value) => value.trim());
  const hasExtractionFieldFilters = extractionRows.some(isActiveExtractionFieldFilterRow);
  const extractionFieldsQuery = useSearchExtractionFields(
    businessViewId,
    extractionOpen || hasExtractionFieldFilters
  );
  const fieldFilter = extractionFieldConditions(
    extractionRows,
    extractionFieldsQuery.data?.fields
  );
  const hasFilters = hasClassificationFilters || hasExtractionFieldFilters;
  // 開閉は利用者の操作だけで決める。閉じていても条件は効くので、見出しに「設定中」を出す（#461）。
  const classificationVisible = classificationOpen;
  const hasSearchTuning = topK !== DEFAULT_TOP_K;
  const hasAdvancedSettings = hasFilters || hasSearchTuning;

  const runSubmit = async (skipFaq: boolean, excludedAutoFields: string[]) => {
    const trimmed = query.trim();
    if (!trimmed || phase === "streaming") return;
    if (!businessViewId) {
      setScopeError(t("businessViews.scope.required"));
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
    setSubmittedQuery(trimmed);
    setLastSkipFaq(skipFaq);
    setAutoFieldExcluded(excludedAutoFields);
    setFaqSuggestions(null);
    setFaqAnswer(null);
    if (!skipFaq) {
      // 業務ビューの承認済み FAQ に類似問があれば、回答生成の前に提示する(rag_poc の類似問)。
      try {
        const faq = await api.suggestApprovedFaq(businessViewId, trimmed);
        if (faq.suggestions.length > 0) {
          setFaqSuggestions(faq.suggestions);
          return;
        }
      } catch {
        // 類似問の照会に失敗しても通常の回答生成は続ける。
      }
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAtMs = Date.now();

    setPhase("streaming");
    setAnswer("");
    setCitations([]);
    setMeta(null);
    setErrorText("");
    setAnswerBusinessViewId(businessViewId);
    setRun({
      generateAnswer,
      startedAtMs,
      startedAtIso: new Date(startedAtMs).toISOString(),
      endedAtMs: null,
      traceId: null,
      stages: [],
    });

    try {
      const filters = buildSearchFilters({
        classification,
        extractionFields: extractionFieldFilterValue(fieldFilter.conditions),
      });
      setAppliedFilters(filters);
      await streamSearch(
        {
          query: trimmed,
          top_k: Number(topK),
          business_view_id: businessViewId,
          generate_answer: generateAnswer,
          ...(excludedAutoFields.length ? { auto_field_filter_excluded: excludedAutoFields } : {}),
          ...(Object.keys(filters).length ? { filters } : {}),
        },
        {
          onStage: (stage) =>
            setRun((current) =>
              current
                ? {
                    ...current,
                    traceId: stage.trace_id || current.traceId,
                    stages: [...current.stages, stage],
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
      setErrorText(
        error instanceof ApiError ? error.message : t("search.error.failed")
      );
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
  const submit = async (skipFaq = false, excludedAutoFields: string[] = []) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    try {
      await runSubmit(skipFaq, excludedAutoFields);
    } finally {
      submittingRef.current = false;
    }
  };

  const cancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    finishRun();
    setPhase("cancelled");
    setErrorText("");
  };
  const finishRun = () => {
    setRun((current) =>
      current && current.endedAtMs == null ? { ...current, endedAtMs: Date.now() } : current
    );
  };
  const clearFilters = () => {
    setClassification(EMPTY_CLASSIFICATION_FILTERS);
    setClassificationOpen(false);
    setExtractionRows([]);
    setExtractionOpen(false);
    setTopK(DEFAULT_TOP_K);
    setAdvancedOpen(false);
  };

  const noResults = phase === "done" && citations.length === 0;
  const answerMode = run?.generateAnswer ?? false;
  const isStreaming = phase === "streaming";
  const feedbackSnapshot = buildFeedbackContentSnapshot(submittedQuery, answer, citations);

  return (
    <div>
      <PageHeader wide title={t("nav.search")} subtitle={t("search.initial")} />
      <PageBody wide>
        <section className="space-y-6">
          {businessViewsQuery.isLoading ? (
            <Card>
              <CardContent className="pt-5">
                <TimedLoadingState
                  label={t("search.businessViewLoading")}
                  operationKey="search-business-views-load"
                  framed={false}
                  testId="search-business-views-loading"
                >
                  <BusinessViewSelectSkeleton />
                  <Skeleton className="h-[var(--button-height-md)] w-full" />
                </TimedLoadingState>
              </CardContent>
            </Card>
          ) : businessViewsQuery.isError ? (
            <ErrorState
              message={t("search.businessViewError")}
              onRetry={() => void businessViewsQuery.refetch()}
            />
          ) : businessViews.length === 0 ? (
            <Card>
              <CardContent className="pt-5">
                <EmptyState
                  title={t("search.businessViewRequired.title")}
                  hint={t("search.businessViewRequired.hint")}
                  action={
                    <Button onClick={() => navigate(`${APP_ROUTES.businessViews}?id=new`)} icon={Plus}>
                      {t("search.businessViewRequired.cta")}
                    </Button>
                  }
                />
              </CardContent>
            </Card>
          ) : (
            <>
          {/* 検索条件 */}
          <Card>
            <CardContent className="space-y-4 pt-4">
              <BusinessViewSelect
                id={BUSINESS_VIEW_SCOPE_INPUT_ID}
                items={businessViews}
                value={businessViewId}
                onChange={(next) => {
                  setBusinessViewId(next);
                  setScopeError("");
                }}
                disabled={isStreaming}
                error={
                  scopeError ||
                  (selectedWithoutKnowledgeBases ? t("businessViews.scope.noKnowledgeBases") : "")
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
                      aria-expanded={classificationVisible}
                      aria-controls="search-classification-filters"
                      onClick={() => setClassificationOpen((open) => !open)}
                      className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    >
                      <span>{t("search.filters.classificationGroup")}</span>
                      <span className="flex items-center gap-2">
                        {!classificationVisible && hasClassificationFilters ? (
                          <StatusBadge variant="info" label={t("search.filters.active")} />
                        ) : null}
                        <DisclosureChevron expanded={classificationVisible} size={14} className="text-fg-muted" />
                      </span>
                    </button>
                    {classificationVisible ? (
                      <div id="search-classification-filters" className="space-y-3 border-t border-border p-3">
                        <p className="text-xs leading-relaxed text-fg-muted">
                          {t("search.filters.classificationHelper")}
                        </p>
                        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                          {CLASSIFICATION_FILTER_KEYS.map((key) => (
                            <TextField
                              key={key}
                              id={`search-${key}`}
                              type={key === "as_of" ? "date" : "text"}
                              label={t(`search.filters.${key}`)}
                              value={classification[key]}
                              onValueChange={(value) =>
                                setClassification((current) => ({ ...current, [key]: value }))
                              }
                            />
                          ))}
                        </div>
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
                        {!businessViewId ? (
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
                    rows={2}
                    placeholder={t("search.placeholder")}
                    // ラベルは読み上げだけ（sr-only）なので、欄の上に余白を空けない。
                    className="space-y-0"
                  />
                </FieldActionRow>
                <QuerySuggestions
                  businessViewId={businessViewId}
                  query={query}
                  filters={classificationSuggestionFilters(classification)}
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
              onSkip={() => void submit(true)}
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
            <ErrorState
              message={errorText}
              onRetry={() => void submit(lastSkipFaq, autoFieldExcluded)}
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

              {/* 回答、または検索の実行（工程・使ったモデル・根拠の構成）。回答はスイッチがオンのときだけ（#649）。 */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    {answerMode ? (
                      <Sparkles size={16} className="text-accent-fg" aria-hidden />
                    ) : (
                      <SearchIcon size={16} className="text-accent-fg" aria-hidden />
                    )}
                    {answerMode ? t("search.answer") : t("search.run.card")}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {run ? (
                    <SearchRunPanel
                      run={run}
                      phase={phase}
                      traceId={run.traceId ?? meta?.trace_id ?? null}
                    />
                  ) : null}
                  <ActiveFilterChips filters={appliedFilters} />
                  {meta && phase === "done" ? (
                    <AutoFieldFilterChips
                      diagnostics={meta.diagnostics?.answer}
                      disabled={isStreaming}
                      // 外した項目を読み取らずに検索し直す（類似 FAQ は出し直さない）。
                      onRemove={(name) => void submit(true, [...autoFieldExcluded, name])}
                    />
                  ) : null}
                  {answerMode ? (
                    <AnswerText
                      text={answer || (phase === "cancelled" ? t("search.cancelledHint") : "")}
                      streaming={isStreaming}
                      citations={citations}
                      cursor={
                        isStreaming ? (
                          <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent-emphasis align-middle" />
                        ) : null
                      }
                    />
                  ) : (
                    <>
                      {/* 回答を作らないとき、本文は検索できなかった理由（検索の準備が無いなど）だけが届く。 */}
                      {answer ? <Banner severity="warning">{answer}</Banner> : null}
                      {phase === "cancelled" ? (
                        <p className="text-sm text-fg-muted">{t("search.cancelledHint")}</p>
                      ) : null}
                    </>
                  )}
                  {answerMode && meta && phase === "done" ? (
                    <FeedbackControls
                      traceId={meta.trace_id}
                      businessViewId={answerBusinessViewId}
                      targetType="answer"
                      sourceSurface="search"
                      contentSnapshot={feedbackSnapshot}
                    />
                  ) : null}
                  {meta && phase === "done" ? (
                    <SearchExecutionMeta meta={meta} />
                  ) : null}
                  {meta && phase === "done" && meta.diagnostics?.answer ? (
                    answerMode ? (
                      <AnswerDetailsPanel
                        diagnostics={meta.diagnostics.answer}
                        traceId={meta.trace_id}
                        showAutoFieldFilter={false}
                      />
                    ) : (
                      <AnswerDetailsPanel
                        diagnostics={meta.diagnostics.answer}
                        title={t("search.answerDetails.searchTitle")}
                        showAutoFieldFilter={false}
                      />
                    )
                  ) : null}
                </CardContent>
              </Card>

              {/* 引用 / no-results */}
              {noResults ? (
                <Card>
                  <CardContent className="pt-5">
                    <EmptyState title={t("search.noResults")} hint={t("search.noResultsHint")} />
                  </CardContent>
                </Card>
              ) : citations.length > 0 ? (
                <section>
                  <h2 className={answerMode ? "mb-3 text-sm font-semibold text-fg" : "mb-1 text-sm font-semibold text-fg"}>
                    {answerMode ? t("search.citations") : t("search.results")}（{citations.length}）
                  </h2>
                  {answerMode ? null : (
                    <p className="mb-3 text-xs leading-relaxed text-fg-muted">{t("search.results.hint")}</p>
                  )}
                  <ul className="bounded-scroll-area-lg space-y-2 pr-1">
                    {citations.map((chunk, i) => (
                      <CitationCard
                        key={chunk.chunk_id}
                        chunk={chunk}
                        index={i}
                        traceId={meta?.trace_id}
                        businessViewId={answerBusinessViewId}
                        sourceSurface="search"
                        contentSnapshot={feedbackSnapshot}
                      />
                    ))}
                  </ul>
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

function SearchRunPanel({
  run,
  phase,
  traceId,
}: {
  run: SearchRun;
  phase: Phase;
  traceId: string | null;
}) {
  const completedStages = run.stages.filter((stage) => stage.outcome !== "started");
  return (
    <section
      aria-label={t("search.run.title")}
      className="mb-4 space-y-3 border-b border-border pb-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
          <Clock3 size={16} className="text-accent-fg" aria-hidden />
          {t("search.run.title")}
        </h3>
        <span className={runStatusClass(phase)}>{runStatusLabel(phase)}</span>
      </div>
      {/* 今の工程と経過時間（#375）。検索のボタンは実行中に「停止」になりスピナーを出さないため、
          動くスピナーはここの 1 つだけ（#413）。 */}
      <AnswerProgress
        active={phase === "streaming"}
        stages={run.stages}
        startedAtMs={run.startedAtMs}
        finishedAtMs={run.endedAtMs}
        finalLabel={runFinishedLabel(phase, run.generateAnswer)}
        generateAnswer={run.generateAnswer}
        testId="search-run-progress"
      />
      <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
        <SearchRunMetric label={t("search.run.startedAt")} value={formatDateTime(run.startedAtIso)} />
        <SearchRunMetric label={t("search.run.trace")} value={shortTraceId(traceId)} />
      </dl>
      {completedStages.length ? (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-fg-muted">{t("search.run.stages")}</p>
          <div className="flex flex-wrap gap-1.5">
            {completedStages.map((stage, index) => (
              <span
                key={`${stage.stage}-${stage.outcome}-${index}`}
                className={stageChipClass(stage.outcome)}
                title={stageOutcomeLabel(stage.outcome)}
              >
                <span>{stageLabel(stage.stage)}</span>
                <span className="tnum">{Math.round(stage.elapsed_ms)} ms</span>
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function SearchRunMetric({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-fg-muted">{label}</dt>
      <dd data-testid={testId} className="tnum mt-0.5 truncate font-medium text-fg">
        {value}
      </dd>
    </div>
  );
}

function runFinishedLabel(phase: Phase, generateAnswer: boolean): string {
  if (phase === "cancelled") {
    return t(generateAnswer ? "search.run.finished.cancelled" : "search.run.finished.searchCancelled");
  }
  if (phase === "error") {
    return t(generateAnswer ? "search.run.finished.error" : "search.run.finished.searchError");
  }
  return t(generateAnswer ? "search.run.finished.done" : "search.run.finished.searchDone");
}

function stageLabel(stage: string): string {
  return answerStageLabel(stage);
}

function runStatusLabel(phase: Phase): string {
  switch (phase) {
    case "done":
      return t("search.run.status.done");
    case "cancelled":
      return t("search.run.status.cancelled");
    case "error":
      return t("search.run.status.error");
    default:
      return t("search.run.status.streaming");
  }
}

function runStatusClass(phase: Phase): string {
  const base = "rounded-full px-2 py-0.5 text-xs font-medium";
  switch (phase) {
    case "done":
      return `${base} bg-success-subtle text-success-fg`;
    case "error":
      return `${base} bg-danger-subtle text-danger-fg`;
    case "cancelled":
      return `${base} bg-warning-subtle text-warning-fg`;
    default:
      return `${base} bg-info-subtle text-info-fg`;
  }
}

function stageOutcomeLabel(outcome: SearchStageEvent["outcome"]): string {
  switch (outcome) {
    case "success":
      return t("search.run.outcome.success");
    case "error":
      return t("search.run.outcome.error");
    case "cancelled":
      return t("search.run.outcome.cancelled");
    default:
      return t("search.run.outcome.started");
  }
}

function stageChipClass(outcome: SearchStageEvent["outcome"]): string {
  const base = "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs";
  switch (outcome) {
    case "success":
      return `${base} bg-success-subtle text-success-fg`;
    case "error":
    case "cancelled":
      return `${base} bg-danger-subtle text-danger-fg`;
    default:
      return `${base} bg-surface-hover text-fg-muted`;
  }
}

function shortTraceId(traceId: string | null): string {
  if (!traceId) return "—";
  return traceId.length > 12 ? traceId.slice(0, 12) : traceId;
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
    ...CLASSIFICATION_FILTER_KEYS.map((key) =>
      filters[key]
        ? {
            key,
            label: t("search.filters.appliedClassification", {
              label: t(`search.filters.${key}`),
              value: filters[key],
            }),
          }
        : null
    ),
    ...parseExtractionFieldFilterValue(filters[EXTRACTION_FIELD_FILTER_KEY]).map(
      (condition, index) => ({
        key: `${EXTRACTION_FIELD_FILTER_KEY}-${index}`,
        label: extractionFieldConditionLabel(condition),
      })
    ),
  ].flatMap((chip) => (chip ? [chip] : []));
}

/** 実行の記録（経過時間と trace）。検索の内訳は回答エンジンの記録（AnswerDetailsPanel）が出す。 */
function SearchExecutionMeta({ meta }: { meta: Meta }) {
  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="tnum flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
        <span>
          {t("search.meta.elapsed")}: {Math.round(meta.elapsed_ms)} ms
        </span>
        <span>
          {t("search.meta.trace")}: {meta.trace_id.slice(0, 12)}
        </span>
      </p>
    </div>
  );
}

function buildSearchFilters({
  classification,
  extractionFields,
}: {
  classification: ClassificationFilterValues;
  extractionFields: string;
}): Record<string, string> {
  const filters: Record<string, string> = {};
  for (const key of CLASSIFICATION_FILTER_KEYS) {
    if (classification[key].trim()) filters[key] = classification[key].trim();
  }
  if (extractionFields) filters[EXTRACTION_FIELD_FILTER_KEY] = extractionFields;
  return filters;
}

/** 文書の分類（完全一致）と有効期間の基準日。backend の検索 filters のキーと同じ名前。 */
const CLASSIFICATION_FILTER_KEYS = ["large_category", "middle_category", "small_category", "as_of"] as const;
type ClassificationFilterValues = Record<(typeof CLASSIFICATION_FILTER_KEYS)[number], string>;
const EMPTY_CLASSIFICATION_FILTERS: ClassificationFilterValues = {
  large_category: "",
  middle_category: "",
  small_category: "",
  as_of: "",
};

/** 質問の候補は分類だけで絞る（基準日は候補に関係しない）。 */
function classificationSuggestionFilters(values: ClassificationFilterValues): Record<string, string> {
  const filters: Record<string, string> = {};
  for (const key of ["large_category", "middle_category", "small_category"] as const) {
    if (values[key].trim()) filters[key] = values[key].trim();
  }
  return filters;
}

function isClassificationFilterValues(value: unknown): value is ClassificationFilterValues {
  return (
    typeof value === "object" &&
    value !== null &&
    CLASSIFICATION_FILTER_KEYS.every(
      (key) => typeof (value as Record<string, unknown>)[key] === "string"
    )
  );
}

const BUSINESS_VIEW_SCOPE_INPUT_ID = "search-business-view-scope";
