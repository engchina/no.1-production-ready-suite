"use client";

import {
  PageBody,
  PageHeader,
  Banner,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  RequiredBadge,
  SelectField,
  type SelectFieldOption,
  TextField,
  ToggleChip,
  TimedLoadingState,
  Skeleton,
  ListSkeleton,
} from "@engchina/production-ready-ui";
import {
  ChevronRight,
  Clock3,
  Plus,
  Search as SearchIcon,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { BusinessViewPickerGrid } from "@/components/business-views/BusinessViewPickerGrid";
import { ExtractedText } from "@/components/documents/extraction-bits";
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
  type BusinessViewSummary,
  type RetrievedChunk,
  type SearchDiagnostics,
  type SearchMode,
} from "@/lib/api";
import { streamSearch, type SearchStageEvent } from "@/lib/search-stream";
import { answerStageLabel } from "@/lib/answer-progress";
import { isSubmitEnter } from "@/lib/keyboard";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useBusinessViews } from "@/lib/queries";
import { formatDateTime } from "@/lib/format";
import { isOneOf, removeWorkspace, useWorkspaceState } from "@/lib/workspace-state";
import { AnswerProgress } from "./AnswerProgress";
import { DocragAnswerHistory } from "./DocragAnswerHistory";
import { DocragAnswerPanel } from "./DocragAnswerPanel";
import { QuerySuggestions } from "./QuerySuggestions";
import { ApprovedFaqAnswer, ApprovedFaqSuggestions } from "./ApprovedFaqSuggestions";

type Phase = "idle" | "streaming" | "done" | "cancelled" | "error";

interface Meta {
  trace_id: string;
  elapsed_ms: number;
  guardrail_warnings: string[];
  diagnostics: Partial<SearchDiagnostics> | null;
}

interface SearchRun {
  startedAtMs: number;
  startedAtIso: string;
  endedAtMs: number | null;
  traceId: string | null;
  stages: SearchStageEvent[];
}

const MODES: SearchMode[] = ["hybrid", "vector", "keyword"];
const CONTENT_KIND_OPTIONS = [
  "",
  "text",
  "list",
  "table",
  "figure",
  "equation",
  "code",
  "email",
  "slide",
  "sheet",
  "field",
  "section_summary",
] as const;
type ContentKindFilter = (typeof CONTENT_KIND_OPTIONS)[number];
const TOP_K_OPTIONS = ["5", "10", "20", "50"] as const;
const RERANK_TOP_N_OPTIONS = ["1", "3", "5", "8", "10"] as const;
const DEFAULT_TOP_K = "20";
const DEFAULT_RERANK_TOP_N = "5";
type TopKOption = (typeof TOP_K_OPTIONS)[number];
type RerankTopNOption = (typeof RERANK_TOP_N_OPTIONS)[number];
const MODE_LABEL: Record<SearchMode, Parameters<typeof t>[0]> = {
  hybrid: "search.mode.hybrid",
  vector: "search.mode.vector",
  keyword: "search.mode.keyword",
};
const CONTENT_KIND_LABEL: Record<ContentKindFilter, Parameters<typeof t>[0]> = {
  "": "search.filters.contentKind.all",
  text: "search.filters.contentKind.text",
  list: "search.filters.contentKind.list",
  table: "search.filters.contentKind.table",
  figure: "search.filters.contentKind.figure",
  equation: "search.filters.contentKind.equation",
  code: "search.filters.contentKind.code",
  email: "search.filters.contentKind.email",
  slide: "search.filters.contentKind.slide",
  sheet: "search.filters.contentKind.sheet",
  field: "search.filters.contentKind.field",
  section_summary: "search.filters.contentKind.section_summary",
};
const CONTENT_KIND_SELECT_OPTIONS = CONTENT_KIND_OPTIONS.map((option) => ({
  value: option,
  label: t(CONTENT_KIND_LABEL[option]),
})) satisfies SelectFieldOption<ContentKindFilter>[];
const TOP_K_SELECT_OPTIONS = TOP_K_OPTIONS.map((option) => ({
  value: option,
  label: option,
})) satisfies SelectFieldOption<TopKOption>[];
const RERANK_TOP_N_SELECT_OPTIONS = RERANK_TOP_N_OPTIONS.map((option) => ({
  value: option,
  label: option,
})) satisfies SelectFieldOption<RerankTopNOption>[];

/** RAG 検索画面。回答を SSE でストリーミング表示する。 */
export function SearchClient() {
  // 入力中の質問・業務ビュー・詳細条件は、ページを行き来しても再読込しても残す（workspace-state.md）。
  // 回答・引用などの結果は保存せず、戻っただけで検索を送り直さない。
  const [query, setQuery] = useWorkspaceState("search.query", "");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [mode, setMode] = useWorkspaceState<SearchMode>("search.mode", "hybrid", isOneOf(MODES));
  const [phase, setPhase] = useState<Phase>("idle");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<RetrievedChunk[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [errorText, setErrorText] = useState("");
  const [contentKind, setContentKind] = useWorkspaceState<ContentKindFilter>(
    "search.contentKind",
    "",
    isOneOf(CONTENT_KIND_OPTIONS)
  );
  const [sectionTitle, setSectionTitle] = useWorkspaceState("search.sectionTitle", "");
  const [sectionPath, setSectionPath] = useWorkspaceState("search.sectionPath", "");
  const [classification, setClassification] = useWorkspaceState<ClassificationFilterValues>(
    "search.classification",
    EMPTY_CLASSIFICATION_FILTERS,
    isClassificationFilterValues
  );
  const [classificationOpen, setClassificationOpen] = useState(false);
  const [topK, setTopK] = useWorkspaceState<TopKOption>("search.topK", DEFAULT_TOP_K, isOneOf(TOP_K_OPTIONS));
  const [rerankTopN, setRerankTopN] = useWorkspaceState<RerankTopNOption>(
    "search.rerankTopN",
    DEFAULT_RERANK_TOP_N,
    isOneOf(RERANK_TOP_N_OPTIONS)
  );
  const [advancedOpen, setAdvancedOpen] = useWorkspaceState("search.advancedOpen", false);
  const [sectionFiltersOpen, setSectionFiltersOpen] = useState(false);
  const [appliedFilters, setAppliedFilters] = useState<Record<string, string>>({});
  const [businessViewIds, setBusinessViewIds] = useWorkspaceState<string[]>("search.businessViewIds", []);
  const [scopeError, setScopeError] = useState("");
  const [run, setRun] = useState<SearchRun | null>(null);
  // 表示中の回答を生成したときの代表の業務ビュー（選択の先頭）。回答・引用の評価はこの業務ビューへ送る。
  // 検索後に選択を変えても、表示中の回答の評価先は変えない。backend の business_view_applied は
  // 複数選択で "a,b" になるため評価先には使わない（#285）。
  const [answerBusinessViewId, setAnswerBusinessViewId] = useState<string | null>(null);
  // 直前の送信が類似 FAQ の提示を飛ばしたか。エラーの再試行を同じ操作にする（#285）。
  const [lastSkipFaq, setLastSkipFaq] = useState(false);
  const [faqSuggestions, setFaqSuggestions] = useState<ApprovedFaqSuggestionData[] | null>(null);
  const [faqAnswer, setFaqAnswer] = useState<ApprovedFaqSuggestionData | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const navigate = useNavigate();
  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 50, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  // 復元した業務ビューのうち、アーカイブ・削除されたものだけ選択から外す（別の対象へ置き換えない）。
  const staleBusinessViewIds =
    businessViewsQuery.data && !businessViewsQuery.data.has_next
      ? businessViewIds.filter((id) => !businessViews.some((view) => view.id === id))
      : [];
  const staleBusinessViewKey = staleBusinessViewIds.join(",");
  // 選んだ業務ビューがどれも参照 KB を持たないなら検索しない（利用者の全 KB を検索しない。#304）。
  // backend も 409 で理由を返すが、送信する前にこの場で理由を示す。
  const selectedBusinessViews = businessViews.filter((view) => businessViewIds.includes(view.id));
  const selectedWithoutKnowledgeBases =
    selectedBusinessViews.length > 0 &&
    selectedBusinessViews.length === businessViewIds.length &&
    selectedBusinessViews.every((view) => view.knowledge_base_count === 0);
  // 画面を離れたら生成中の検索を止める（backend の pipeline と LLM を無駄に動かし続けない。#285）。
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (!staleBusinessViewKey) return;
    const stale = new Set(staleBusinessViewKey.split(","));
    setBusinessViewIds((current) => current.filter((id) => !stale.has(id)));
  }, [staleBusinessViewKey, setBusinessViewIds]);
  const hasSectionFilters = Boolean(sectionTitle.trim()) || Boolean(sectionPath.trim());
  const hasClassificationFilters = Object.values(classification).some((value) => value.trim());
  const hasFilters =
    Boolean(contentKind) || hasSectionFilters || hasClassificationFilters;
  const classificationVisible = classificationOpen || hasClassificationFilters;
  const hasSearchTuning = topK !== DEFAULT_TOP_K || rerankTopN !== DEFAULT_RERANK_TOP_N;
  const hasAdvancedSettings = hasFilters || hasSearchTuning;
  const sectionFiltersVisible = sectionFiltersOpen || hasSectionFilters;
  const rerankTopNOptions = RERANK_TOP_N_SELECT_OPTIONS.filter(
    (option) => Number(option.value) <= Number(topK)
  );

  const runSubmit = async (skipFaq: boolean) => {
    const trimmed = query.trim();
    if (!trimmed || phase === "streaming") return;
    if (businessViewIds.length === 0) {
      setScopeError(t("businessViews.scope.required"));
      return;
    }
    if (selectedWithoutKnowledgeBases) return;
    setScopeError("");
    setSubmittedQuery(trimmed);
    setLastSkipFaq(skipFaq);
    setFaqSuggestions(null);
    setFaqAnswer(null);
    if (!skipFaq) {
      // 業務ビューの承認済み FAQ に類似問があれば、回答生成の前に提示する(rag_poc の類似問)。
      try {
        const faq = await api.suggestApprovedFaq(businessViewIds[0], trimmed);
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
    setAnswerBusinessViewId(businessViewIds[0] ?? null);
    setRun({
      startedAtMs,
      startedAtIso: new Date(startedAtMs).toISOString(),
      endedAtMs: null,
      traceId: null,
      stages: [],
    });

    try {
      const filters = buildSearchFilters({ contentKind, sectionTitle, sectionPath, classification });
      setAppliedFilters(filters);
      await streamSearch(
        {
          query: trimmed,
          mode,
          top_k: Number(topK),
          rerank_top_n: Number(rerankTopN),
          business_view_ids: businessViewIds,
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
            // 新しい回答は回答履歴の先頭に入るため、履歴を 1 ページ目に戻す（#304）。
            removeWorkspace("search.historyPage");
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
        error instanceof ApiError ? error.message : "検索に失敗しました。再度お試しください。"
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
  const submit = async (skipFaq = false) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    try {
      await runSubmit(skipFaq);
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
    setContentKind("");
    setSectionTitle("");
    setSectionPath("");
    setClassification(EMPTY_CLASSIFICATION_FILTERS);
    setClassificationOpen(false);
    setTopK(DEFAULT_TOP_K);
    setRerankTopN(DEFAULT_RERANK_TOP_N);
    setAdvancedOpen(false);
    setSectionFiltersOpen(false);
  };
  const changeTopK = (next: TopKOption) => {
    setTopK(next);
    setRerankTopN((current) => clampRerankTopN(current, next));
  };

  const noResults = phase === "done" && citations.length === 0;
  const isStreaming = phase === "streaming";
  const feedbackSnapshot = buildFeedbackContentSnapshot(submittedQuery, answer, citations);
  const structuredJsonAnswer = meta?.diagnostics?.generation_profile === "structured_json";

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
                  <Skeleton className="h-[var(--button-height-md)] w-full max-w-md" />
                  <ListSkeleton rows={3} rowClassName="h-10" />
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
              <BusinessViewScopePicker
                views={businessViews}
                selectedIds={businessViewIds}
                onChange={(next) => {
                  setBusinessViewIds(next);
                  if (next.length > 0) setScopeError("");
                }}
                disabled={isStreaming}
                error={
                  scopeError ||
                  (selectedWithoutKnowledgeBases ? t("businessViews.scope.noKnowledgeBases") : "")
                }
              />

              <div className="flex flex-col gap-2 sm:flex-row">
                {/* 隣の lg の Button と同じ高さ（size="lg"）。 */}
                <TextField
                  id="search-query"
                  label={t("nav.search")}
                  labelHidden
                  size="lg"
                  value={query}
                  onValueChange={setQuery}
                  onKeyDown={(e) => {
                    if (isSubmitEnter(e)) void submit();
                  }}
                  placeholder={t("search.placeholder")}
                  leadingIcon={SearchIcon}
                  className="min-w-0 flex-1"
                />
                <Button
                  type="button"
                  icon={SearchIcon}
                  onPointerDown={(event) => {
                    event.preventDefault();
                    void submit();
                  }}
                  onClick={() => void submit()}
                  loading={isStreaming}
                  size="lg"
                  className="sm:min-w-28"
                >
                  {t("search.button")}
                </Button>
                {isStreaming ? (
                  <Button type="button" variant="secondary" size="lg" onClick={cancel} icon={X}>
                    {t("search.cancel")}
                  </Button>
                ) : null}
              </div>

              <QuerySuggestions
                businessViewId={businessViewIds[0] ?? null}
                query={query}
                filters={classificationSuggestionFilters(classification)}
                disabled={isStreaming}
                onSelect={setQuery}
              />

              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-1" role="group" aria-label={t("search.pipeline")}>
                  {MODES.map((m) => (
                    <ToggleChip key={m} selected={mode === m} onClick={() => setMode(m)}>
                      {t(MODE_LABEL[m])}
                    </ToggleChip>
                  ))}
                </div>
                <p className="text-xs text-fg-muted">{t("search.pipeline")}</p>
              </div>

              <div className="rounded-md border border-border bg-surface-sunken">
                <button
                  type="button"
                  aria-expanded={advancedOpen || hasAdvancedSettings}
                  aria-controls="search-advanced-conditions"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    setAdvancedOpen((open) => !open);
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    setAdvancedOpen((open) => !open);
                  }}
                  className="flex w-full cursor-pointer items-center gap-1.5 px-3 py-2 text-left text-sm font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  <SlidersHorizontal size={14} className="text-accent-fg" aria-hidden />
                  {t("search.filters.advanced")}
                </button>
                {advancedOpen || hasAdvancedSettings ? (
                <fieldset id="search-advanced-conditions" className="space-y-4 border-t border-border p-3">
                  <legend className="sr-only">{t("search.filters.title")}</legend>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <SelectField
                      id="search-top-k"
                      label={t("search.tuning.topK")}
                      value={topK}
                      options={TOP_K_SELECT_OPTIONS}
                      helper={t("search.tuning.topKHelp")}
                      onValueChange={changeTopK}
                      className="[&_label]:text-xs"
                      buttonClassName="bg-surface"
                    />

                    <SelectField
                      id="search-rerank-top-n"
                      label={t("search.tuning.rerankTopN")}
                      value={rerankTopN}
                      options={rerankTopNOptions}
                      helper={t("search.tuning.rerankTopNHelp")}
                      onValueChange={setRerankTopN}
                      className="[&_label]:text-xs"
                      buttonClassName="bg-surface"
                    />
                  </div>

                  <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
                    <SelectField
                      id="search-content-kind"
                      label={t("search.filters.contentKind")}
                      value={contentKind}
                      options={CONTENT_KIND_SELECT_OPTIONS}
                      onValueChange={setContentKind}
                      className="[&_label]:text-xs"
                      buttonClassName="bg-surface"
                    />

                    <div className="rounded-md border border-border bg-surface">
                      <button
                        type="button"
                        aria-expanded={sectionFiltersVisible}
                        aria-controls="search-section-filters"
                        onPointerDown={(event) => {
                          event.preventDefault();
                          setSectionFiltersOpen((open) => !open);
                        }}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" && event.key !== " ") return;
                          event.preventDefault();
                          setSectionFiltersOpen((open) => !open);
                        }}
                        className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                      >
                        <span>{t("search.filters.sectionGroup")}</span>
                        <span className="text-fg-muted" aria-hidden>{sectionFiltersVisible ? "−" : "+"}</span>
                      </button>
                      {sectionFiltersVisible ? (
                        <div id="search-section-filters" className="space-y-3 border-t border-border p-3">
                          <p className="text-xs leading-relaxed text-fg-muted">
                            {t("search.filters.sectionHelper")}
                          </p>
                          <div className="grid gap-3 md:grid-cols-2">
                            <div className="space-y-1.5">
                              <label htmlFor="search-section-title" className="text-xs font-medium text-fg">
                                {t("search.filters.sectionTitle")}
                              </label>
                              <input
                                id="search-section-title"
                                type="text"
                                value={sectionTitle}
                                onChange={(event) => setSectionTitle(event.target.value)}
                                placeholder={t("search.filters.sectionTitlePlaceholder")}
                                className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm text-fg outline-none transition-colors placeholder:text-fg-muted focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring"
                              />
                            </div>

                            <div className="space-y-1.5">
                              <label htmlFor="search-section-path" className="text-xs font-medium text-fg">
                                {t("search.filters.sectionPath")}
                              </label>
                              <input
                                id="search-section-path"
                                type="text"
                                value={sectionPath}
                                onChange={(event) => setSectionPath(event.target.value)}
                                placeholder={t("search.filters.sectionPathPlaceholder")}
                                className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm text-fg outline-none transition-colors placeholder:text-fg-muted focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring"
                              />
                            </div>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>

                  <div className="rounded-md border border-border bg-surface">
                    <button
                      type="button"
                      aria-expanded={classificationVisible}
                      aria-controls="search-classification-filters"
                      onPointerDown={(event) => {
                        event.preventDefault();
                        setClassificationOpen((open) => !open);
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== "Enter" && event.key !== " ") return;
                        event.preventDefault();
                        setClassificationOpen((open) => !open);
                      }}
                      className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    >
                      <span>{t("search.filters.classificationGroup")}</span>
                      <span className="text-fg-muted" aria-hidden>{classificationVisible ? "−" : "+"}</span>
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
            <ErrorState message={errorText} onRetry={() => void submit(lastSkipFaq)} />
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

              {/* 回答 */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Sparkles size={16} className="text-accent-fg" aria-hidden />
                    {t("search.answer")}
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
                  {structuredJsonAnswer ? (
                    <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface-hover p-3 font-mono text-sm leading-relaxed text-fg">
                      {answer}
                    </pre>
                  ) : (
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
                      {answer || (phase === "cancelled" ? t("search.cancelledHint") : "")}
                      {isStreaming ? (
                        <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent-emphasis align-middle" />
                      ) : null}
                    </p>
                  )}
                  {meta && phase === "done" ? (
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
                  {meta && phase === "done" && meta.diagnostics?.docrag ? (
                    <DocragAnswerPanel docrag={meta.diagnostics.docrag} traceId={meta.trace_id} />
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
                  <h2 className="mb-3 text-sm font-semibold text-fg">
                    {t("search.citations")}（{citations.length}）
                  </h2>
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
        {businessViewIds.length > 0 ? (
          <DocragAnswerHistory
            key={`${businessViewIds[0]}-${meta?.trace_id ?? ""}`}
            businessViewId={businessViewIds[0]}
          />
        ) : null}
      </PageBody>
    </div>
  );
}

function clampRerankTopN(
  current: RerankTopNOption,
  topK: TopKOption
): RerankTopNOption {
  if (Number(current) <= Number(topK)) return current;
  const allowed = RERANK_TOP_N_OPTIONS.filter((option) => Number(option) <= Number(topK));
  return allowed[allowed.length - 1] ?? DEFAULT_RERANK_TOP_N;
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
      {/* 今の工程と経過時間（#375）。検索ボタンが loading のスピナーを出すため、ここは静的な表示にする。 */}
      <AnswerProgress
        active={phase === "streaming"}
        stages={run.stages}
        startedAtMs={run.startedAtMs}
        finishedAtMs={run.endedAtMs}
        finalLabel={runFinishedLabel(phase)}
        activityIcon="none"
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

function runFinishedLabel(phase: Phase): string {
  if (phase === "cancelled") return t("search.run.finished.cancelled");
  if (phase === "error") return t("search.run.finished.error");
  return t("search.run.finished.done");
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
    filters.content_kind
      ? {
          key: "content_kind",
          label: t("search.filters.appliedContentKind", {
            value: contentKindFilterLabel(filters.content_kind),
          }),
        }
      : null,
    filters.section_title
      ? {
          key: "section_title",
          label: t("search.filters.appliedSectionTitle", { value: filters.section_title }),
        }
      : null,
    filters.section_path
      ? {
          key: "section_path",
          label: t("search.filters.appliedSectionPath", { value: filters.section_path }),
        }
      : null,
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
  ].flatMap((chip) => (chip ? [chip] : []));
}

function contentKindFilterLabel(value: string): string {
  return CONTENT_KIND_OPTIONS.includes(value as ContentKindFilter)
    ? t(CONTENT_KIND_LABEL[value as ContentKindFilter])
    : value;
}

function SearchExecutionMeta({ meta }: { meta: Meta }) {
  const diagnostics = meta.diagnostics ?? {};
  const items = searchExecutionItems(diagnostics);
  const keywordTerms = (diagnostics.keyword_terms ?? []).filter(Boolean);
  const breakdown = retrievalBreakdownFromDiagnostics(diagnostics);
  const candidates = diagnostics.retrieval_candidates ?? [];
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  return (
    <div className="mt-4 space-y-3 border-t border-border pt-3">
      <p className="tnum flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
        <span>
          {t("search.meta.elapsed")}: {Math.round(meta.elapsed_ms)} ms
        </span>
        <span>
          {t("search.meta.trace")}: {meta.trace_id.slice(0, 12)}
        </span>
      </p>
      {keywordTerms.length ? (
        <div aria-label={t("search.meta.keywords")} className="space-y-1.5">
          <p className="text-xs font-medium text-fg-muted">{t("search.meta.keywords")}</p>
          <div className="flex flex-wrap gap-1.5">
            {keywordTerms.map((term, index) => (
              <span
                key={`${term}-${index}`}
                className="max-w-full break-all rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs font-medium leading-snug text-fg"
              >
                {term}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      <RetrievalFlow breakdown={breakdown} />
      {items.length || candidates.length ? (
        <div className="rounded-md border border-border bg-surface-sunken">
          <button
            type="button"
            aria-expanded={diagnosticsOpen}
            aria-controls="search-diagnostics-panel"
            onPointerDown={(event) => {
              event.preventDefault();
              setDiagnosticsOpen((open) => !open);
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              setDiagnosticsOpen((open) => !open);
            }}
            className="w-full cursor-pointer px-3 py-2 text-left text-xs font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
          >
            {t("search.meta.diagnostics")}
          </button>
          {diagnosticsOpen ? (
          <div id="search-diagnostics-panel" className="space-y-3 border-t border-border p-3">
            {items.length ? (
              <section className="space-y-2">
                <h4 className="text-xs font-medium text-fg-muted">{t("search.meta.detailMetrics")}</h4>
                <dl
                  aria-label={t("search.meta.execution")}
                  className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4"
                >
                  {items.map((item) => (
                    <div
                      key={item.key}
                      className="min-w-0 rounded-md border border-border bg-surface px-3 py-2"
                    >
                      <dt className="truncate text-xs font-medium text-fg-muted">{item.label}</dt>
                      <dd className="tnum mt-0.5 text-sm font-semibold text-fg">{item.value}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ) : null}
            <RetrievalCandidateDetails candidates={candidates} />
          </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function RetrievalFlow({ breakdown }: { breakdown: NormalizedRetrievalBreakdown }) {
  const steps = [
    { key: "vector", label: t("search.meta.flow.vector"), value: breakdown.vector_count },
    { key: "keyword", label: t("search.meta.flow.keyword"), value: breakdown.keyword_count },
    { key: "overlap", label: t("search.meta.flow.overlap"), value: breakdown.overlap_count },
    { key: "fused", label: t("search.meta.flow.fused"), value: breakdown.fused_count },
    {
      key: "rerankKept",
      label: t("search.meta.flow.rerankKept"),
      value: breakdown.rerank_kept_count,
    },
    { key: "citation", label: t("search.meta.flow.citation"), value: breakdown.citation_count },
  ];
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-fg-muted">{t("search.meta.flow")}</p>
      <ol
        aria-label={t("search.meta.flow")}
        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-surface-sunken px-3 py-2 text-xs"
      >
        {steps.map((step, index) => (
          <li key={step.key} className="inline-flex items-center gap-2">
            {index > 0 ? <span className="text-fg-muted" aria-hidden>→</span> : null}
            <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
              <span className="text-fg-muted">{step.label}</span>
              <strong className="tnum text-sm text-fg">{step.value}</strong>
            </span>
          </li>
        ))}
      </ol>
      {breakdown.dropped_count > 0 ? (
        <p className="tnum text-xs text-fg-muted">
          {t("search.meta.flow.dropped")}: {breakdown.dropped_count}
        </p>
      ) : null}
    </div>
  );
}

function RetrievalCandidateDetails({
  candidates,
}: {
  candidates: NonNullable<SearchDiagnostics["retrieval_candidates"]>;
}) {
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-medium text-fg-muted">{t("search.meta.candidateDetails")}</h4>
      {candidates.length ? (
        <div role="table" aria-label={t("search.meta.candidateDetails")} className="space-y-1.5">
          <div
            role="row"
            className="hidden grid-cols-[minmax(0,0.75fr)_minmax(0,1.35fr)_minmax(0,0.9fr)_80px_80px_60px_80px_minmax(0,1fr)] gap-2 px-2 text-xs font-medium text-fg-muted md:grid"
          >
            <span role="columnheader">{t("fileList.col.fileName")}</span>
            <span role="columnheader">{t("search.meta.candidate")}</span>
            <span role="columnheader">{t("search.meta.source")}</span>
            <span role="columnheader">{t("search.meta.vector")}</span>
            <span role="columnheader">{t("search.meta.keyword")}</span>
            <span role="columnheader">{t("search.meta.rrf")}</span>
            <span role="columnheader">{t("search.meta.rerankScore")}</span>
            <span role="columnheader">{t("search.meta.status")}</span>
          </div>
          {candidates.map((candidate) => (
            <CandidateRow key={candidate.chunk_id} candidate={candidate} />
          ))}
        </div>
      ) : (
        <p className="text-xs text-fg-muted">{t("search.meta.noCandidates")}</p>
      )}
    </section>
  );
}

function CandidateRow({
  candidate,
}: {
  candidate: NonNullable<SearchDiagnostics["retrieval_candidates"]>[number];
}) {
  return (
    <details
      role="row"
      className="group rounded-md border border-border bg-surface text-xs"
    >
      <summary className="grid min-h-11 cursor-pointer list-none gap-2 rounded-md p-2 transition-colors hover:bg-surface-hover motion-reduce:transition-none md:grid-cols-[minmax(0,0.75fr)_minmax(0,1.35fr)_minmax(0,0.9fr)_80px_80px_60px_80px_minmax(0,1fr)] [&::-webkit-details-marker]:hidden">
        <span
          role="cell"
          data-testid="candidate-file-name"
          className="flex min-w-0 items-center gap-2"
        >
          <ChevronRight
            size={14}
            className="shrink-0 text-fg-muted transition-transform duration-200 group-open:rotate-90 motion-reduce:transition-none"
            aria-hidden
          />
          <span
            className="min-w-0 truncate font-medium text-fg"
            title={candidate.file_name ?? candidate.document_id}
          >
            {candidate.file_name ?? candidate.document_id}
          </span>
        </span>
        <span
          role="cell"
          data-testid="candidate-preview"
          className="min-w-0 truncate text-xs text-fg-muted"
          title={candidate.text || undefined}
        >
          {candidate.text || "—"}
        </span>
        <span role="cell" className="flex flex-wrap gap-1">
          {candidate.sources.map((source) => (
            <span
              key={source}
              className="rounded-full bg-surface-hover px-2 py-0.5 text-xs font-medium text-fg-muted"
            >
              {sourceLabel(source)}
            </span>
          ))}
        </span>
        <span role="cell" className="tnum text-fg">
          {formatRankScore(candidate.vector_rank, candidate.vector_score)}
        </span>
        <span role="cell" className="tnum text-fg">
          {formatRankScore(candidate.keyword_rank, candidate.keyword_score)}
        </span>
        <span role="cell" className="tnum text-fg">
          {formatScore(candidate.rrf_score)}
        </span>
        <span role="cell" className="tnum text-fg">
          {formatRankScore(candidate.rerank_rank, candidate.rerank_score)}
        </span>
        <span role="cell" className="min-w-0 text-fg">
          <span>{candidateStatusLabel(candidate.status)}</span>
          {candidate.drop_reason ? (
            <span className="ml-1 text-fg-muted">({dropReasonLabel(candidate.drop_reason)})</span>
          ) : null}
        </span>
      </summary>
      <div data-testid="candidate-original" className="border-t border-border p-3">
        <p className="mb-2 text-xs font-medium text-fg-muted">
          {t("search.meta.chunkOriginal")}
        </p>
        <ExtractedText text={candidate.text ?? ""} />
      </div>
    </details>
  );
}

interface NormalizedRetrievalBreakdown {
  vector_count: number;
  keyword_count: number;
  overlap_count: number;
  fused_count: number;
  fusion_dropped_count: number;
  rerank_input_count: number;
  rerank_kept_count: number;
  rerank_dropped_count: number;
  evidence_count: number;
  citation_count: number;
  dropped_count: number;
}

function retrievalBreakdownFromDiagnostics(
  diagnostics: Partial<SearchDiagnostics>
): NormalizedRetrievalBreakdown {
  const fallbackRetrieved = diagnostics.retrieved_count ?? 0;
  const fallbackReranked = diagnostics.reranked_count ?? 0;
  const fallbackCitations = diagnostics.citation_count ?? 0;
  return {
    vector_count: diagnostics.retrieval_breakdown?.vector_count ?? 0,
    keyword_count: diagnostics.retrieval_breakdown?.keyword_count ?? 0,
    overlap_count: diagnostics.retrieval_breakdown?.overlap_count ?? 0,
    fused_count: diagnostics.retrieval_breakdown?.fused_count ?? fallbackRetrieved,
    fusion_dropped_count: diagnostics.retrieval_breakdown?.fusion_dropped_count ?? 0,
    rerank_input_count: diagnostics.retrieval_breakdown?.rerank_input_count ?? fallbackRetrieved,
    rerank_kept_count: diagnostics.retrieval_breakdown?.rerank_kept_count ?? fallbackReranked,
    rerank_dropped_count: diagnostics.retrieval_breakdown?.rerank_dropped_count ?? 0,
    evidence_count: diagnostics.retrieval_breakdown?.evidence_count ?? 0,
    citation_count: diagnostics.retrieval_breakdown?.citation_count ?? fallbackCitations,
    dropped_count:
      diagnostics.retrieval_breakdown?.dropped_count ??
      Math.max(0, fallbackRetrieved - fallbackCitations),
  };
}

function searchExecutionItems(diagnostics: Partial<SearchDiagnostics>) {
  return [
    { key: "retrieved", label: t("search.meta.retrieved"), value: diagnostics.retrieved_count },
    { key: "reranked", label: t("search.meta.reranked"), value: diagnostics.reranked_count },
    { key: "citations", label: t("search.meta.citations"), value: diagnostics.citation_count },
    {
      key: "fusionDropped",
      label: t("search.meta.flow.fusionDropped"),
      value: diagnostics.retrieval_breakdown?.fusion_dropped_count,
    },
    {
      key: "rerankDropped",
      label: t("search.meta.flow.rerankDropped"),
      value: diagnostics.retrieval_breakdown?.rerank_dropped_count,
    },
    {
      key: "adaptive",
      label: t("search.meta.adaptive"),
      value: diagnostics.context_adaptive_expanded_count,
    },
    {
      key: "dependency",
      label: t("search.meta.dependency"),
      value: diagnostics.context_dependency_promoted_count,
    },
    { key: "group", label: t("search.meta.group"), value: diagnostics.context_group_expanded_count },
    { key: "neighbor", label: t("search.meta.neighbor"), value: diagnostics.context_expanded_count },
    { key: "compressed", label: t("search.meta.compressed"), value: diagnostics.context_compressed_count },
  ].flatMap((item) => (typeof item.value === "number" ? [{ ...item, value: item.value }] : []));
}

function sourceLabel(source: string): string {
  switch (source) {
    case "vector":
      return t("search.meta.vector");
    case "keyword":
      return t("search.meta.keyword");
    case "graph":
      return "Graph";
    case "agent_memory":
      return "Memory";
    default:
      return source || "—";
  }
}

function candidateStatusLabel(status: string): string {
  switch (status) {
    case "citation":
      return t("search.meta.status.citation");
    case "reranked":
      return t("search.meta.status.reranked");
    case "dropped":
      return t("search.meta.status.dropped");
    default:
      return t("search.meta.status.retrieved");
  }
}

function dropReasonLabel(reason: string): string {
  switch (reason) {
    case "rerank_out":
      return t("search.meta.drop.rerank_out");
    case "not_cited":
      return t("search.meta.drop.not_cited");
    default:
      return reason;
  }
}

function formatRankScore(rank: number | null, score: number | null): string {
  const scoreText = formatScore(score);
  return rank == null ? scoreText : `#${rank} / ${scoreText}`;
}

function formatScore(score: number | null): string {
  return typeof score === "number" && Number.isFinite(score) ? score.toFixed(3) : "—";
}

function buildSearchFilters({
  contentKind,
  sectionTitle,
  sectionPath,
  classification,
}: {
  contentKind: ContentKindFilter;
  sectionTitle: string;
  sectionPath: string;
  classification: ClassificationFilterValues;
}): Record<string, string> {
  const filters: Record<string, string> = {};
  if (contentKind) filters.content_kind = contentKind;
  if (sectionTitle.trim()) filters.section_title = sectionTitle.trim();
  if (sectionPath.trim()) filters.section_path = sectionPath.trim();
  for (const key of CLASSIFICATION_FILTER_KEYS) {
    if (classification[key].trim()) filters[key] = classification[key].trim();
  }
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


/**
 * RAG 検索の対象業務ビュー(Business View)選択。複数選ぶと参照 KB 群を union し、
 * query 方針・persona は選択順の先頭を代表として適用する。
 */
function BusinessViewScopePicker({
  views,
  selectedIds,
  onChange,
  disabled = false,
  error,
}: {
  views: BusinessViewSummary[];
  selectedIds: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  error?: string;
}) {
  return (
    <div className="space-y-1.5 sm:col-span-4">
      <p className="flex items-center gap-1.5 text-xs font-medium text-fg">
        {t("businessViews.scope.label")}
        {/* グリッド選択は aria-required を持てないので、バッジは読み上げ対象のままにする */}
        <RequiredBadge label={t("common.required")} />
      </p>
      <BusinessViewPickerGrid
        items={views}
        selectedIds={selectedIds}
        onChange={(next) => {
          if (!disabled) onChange(next);
        }}
        disabled={disabled}
        ariaLabel={t("businessViews.scope.label")}
      />
      {error ? (
        <p className="text-xs text-danger-fg" role="alert">
          {error}
        </p>
      ) : (
        <p className="text-xs text-fg-muted">
          {selectedIds.length > 0
            ? t("businessViews.scope.applied", { count: selectedIds.length })
            : t("businessViews.scope.helper")}
        </p>
      )}
    </div>
  );
}
