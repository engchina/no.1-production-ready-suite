"use client";

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

import { BusinessViewPickerGrid } from "@/components/business-views/BusinessViewPickerGrid";
import { CitationCard, scoreMaximaForCitations } from "./CitationCard";
import { FeedbackControls } from "@/components/feedback/FeedbackControls";
import { PageHeader } from "@/components/PageHeader";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { EmptyState, ErrorState, LoadingState } from "@/components/StateViews";
import {
  ApiError,
  type BusinessViewSummary,
  type RetrievedChunk,
  type SearchDiagnostics,
  type SearchMode,
} from "@/lib/api";
import { streamSearch, type SearchStageEvent } from "@/lib/search-stream";
import { t, type I18nKey } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useBusinessViews } from "@/lib/queries";
import { formatDateTime } from "@/lib/format";

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
const STAGE_LABEL: Record<string, I18nKey> = {
  agentic_multi_hop: "search.stage.agentic",
  agentic_planning: "search.stage.agentic",
  answer_guardrail: "search.stage.answerGuardrail",
  business_fit_weighting: "search.stage.businessFit",
  context_adaptive_expansion: "search.stage.context",
  context_compression: "search.stage.context",
  context_dependency_promotion: "search.stage.context",
  context_diversity: "search.stage.context",
  context_expansion: "search.stage.context",
  context_group_expansion: "search.stage.context",
  corrective_retrieval: "search.stage.corrective",
  crag_corrective: "search.stage.corrective",
  embedding: "search.stage.embedding",
  generation: "search.stage.generation",
  rerank: "search.stage.rerank",
  retrieval: "search.stage.retrieval",
};

/** RAG 検索画面。回答を SSE でストリーミング表示する。 */
export function SearchClient() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [phase, setPhase] = useState<Phase>("idle");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<RetrievedChunk[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [errorText, setErrorText] = useState("");
  const [contentKind, setContentKind] = useState<ContentKindFilter>("");
  const [sectionTitle, setSectionTitle] = useState("");
  const [sectionPath, setSectionPath] = useState("");
  const [topK, setTopK] = useState<TopKOption>(DEFAULT_TOP_K);
  const [rerankTopN, setRerankTopN] = useState<RerankTopNOption>(DEFAULT_RERANK_TOP_N);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [sectionFiltersOpen, setSectionFiltersOpen] = useState(false);
  const [appliedFilters, setAppliedFilters] = useState<Record<string, string>>({});
  const [businessViewIds, setBusinessViewIds] = useState<string[]>([]);
  const [scopeError, setScopeError] = useState("");
  const [run, setRun] = useState<SearchRun | null>(null);
  const [elapsedNowMs, setElapsedNowMs] = useState(Date.now());
  const abortRef = useRef<AbortController | null>(null);
  const navigate = useNavigate();
  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 50, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  const hasSectionFilters = Boolean(sectionTitle.trim()) || Boolean(sectionPath.trim());
  const hasFilters =
    Boolean(contentKind) || hasSectionFilters;
  const hasSearchTuning = topK !== DEFAULT_TOP_K || rerankTopN !== DEFAULT_RERANK_TOP_N;
  const hasAdvancedSettings = hasFilters || hasSearchTuning;
  const sectionFiltersVisible = sectionFiltersOpen || hasSectionFilters;
  const rerankTopNOptions = RERANK_TOP_N_SELECT_OPTIONS.filter(
    (option) => Number(option.value) <= Number(topK)
  );
  const runStartedAtMs = run?.startedAtMs;

  useEffect(() => {
    if (phase !== "streaming" || runStartedAtMs == null) return;
    setElapsedNowMs(Date.now());
    const timer = window.setInterval(() => setElapsedNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [phase, runStartedAtMs]);

  const submit = async () => {
    const trimmed = query.trim();
    if (!trimmed || phase === "streaming") return;
    if (businessViewIds.length === 0) {
      setScopeError(t("businessViews.scope.required"));
      return;
    }
    setScopeError("");

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAtMs = Date.now();

    setPhase("streaming");
    setAnswer("");
    setCitations([]);
    setMeta(null);
    setErrorText("");
    setElapsedNowMs(startedAtMs);
    setRun({
      startedAtMs,
      startedAtIso: new Date(startedAtMs).toISOString(),
      endedAtMs: null,
      traceId: null,
      stages: [],
    });

    try {
      const filters = buildSearchFilters({ contentKind, sectionTitle, sectionPath });
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
  const citationScoreMaxima = scoreMaximaForCitations(citations);

  return (
    <div>
      <PageHeader title={t("nav.search")} subtitle={t("search.initial")} />
      <div className="p-4 sm:p-6 lg:p-8">
        <section className="space-y-6">
          {businessViewsQuery.isLoading ? (
            <Card>
              <CardContent className="pt-5">
                <LoadingState rows={4} label={t("search.businessViewRequired.title")} />
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
                    <Button onClick={() => navigate(APP_ROUTES.businessViews)}>
                      <Plus size={16} aria-hidden />
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
                error={scopeError}
              />

              <div className="flex flex-col gap-2 sm:flex-row">
                <label htmlFor="search-query" className="sr-only">
                  {t("nav.search")}
                </label>
                <div className="relative min-w-0 flex-1">
                  <SearchIcon
                    size={16}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                    aria-hidden
                  />
                  <input
                    id="search-query"
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void submit();
                    }}
                    placeholder={t("search.placeholder")}
                    aria-label={t("nav.search")}
                    className="h-11 w-full rounded-md border border-border bg-background py-2.5 pl-9 pr-3 text-sm outline-none focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                  />
                </div>
                <Button
                  type="button"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    void submit();
                  }}
                  onClick={() => void submit()}
                  loading={isStreaming}
                  size="lg"
                  className="sm:w-28"
                >
                  {isStreaming ? t("search.searching") : t("search.button")}
                </Button>
                {isStreaming ? (
                  <Button type="button" variant="secondary" size="lg" onClick={cancel}>
                    <X size={16} aria-hidden />
                    {t("search.cancel")}
                  </Button>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-1" role="group" aria-label={t("search.pipeline")}>
                  {MODES.map((m) => (
                    <ToggleChip key={m} selected={mode === m} onClick={() => setMode(m)}>
                      {t(MODE_LABEL[m])}
                    </ToggleChip>
                  ))}
                </div>
                <p className="text-xs text-muted">{t("search.pipeline")}</p>
              </div>

              <div className="rounded-md border border-border bg-background">
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
                  className="flex w-full cursor-pointer items-center gap-1.5 px-3 py-2 text-left text-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <SlidersHorizontal size={14} className="text-primary" aria-hidden />
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
                      buttonClassName="bg-card"
                    />

                    <SelectField
                      id="search-rerank-top-n"
                      label={t("search.tuning.rerankTopN")}
                      value={rerankTopN}
                      options={rerankTopNOptions}
                      helper={t("search.tuning.rerankTopNHelp")}
                      onValueChange={setRerankTopN}
                      className="[&_label]:text-xs"
                      buttonClassName="bg-card"
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
                      buttonClassName="bg-card"
                    />

                    <div className="rounded-md border border-border bg-card">
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
                        className="flex w-full cursor-pointer items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                      >
                        <span>{t("search.filters.sectionGroup")}</span>
                        <span className="text-muted" aria-hidden>{sectionFiltersVisible ? "−" : "+"}</span>
                      </button>
                      {sectionFiltersVisible ? (
                        <div id="search-section-filters" className="space-y-3 border-t border-border p-3">
                          <p className="text-xs leading-relaxed text-muted">
                            {t("search.filters.sectionHelper")}
                          </p>
                          <div className="grid gap-3 md:grid-cols-2">
                            <div className="space-y-1.5">
                              <label htmlFor="search-section-title" className="text-xs font-medium text-foreground">
                                {t("search.filters.sectionTitle")}
                              </label>
                              <input
                                id="search-section-title"
                                type="text"
                                value={sectionTitle}
                                onChange={(event) => setSectionTitle(event.target.value)}
                                placeholder={t("search.filters.sectionTitlePlaceholder")}
                                className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                              />
                            </div>

                            <div className="space-y-1.5">
                              <label htmlFor="search-section-path" className="text-xs font-medium text-foreground">
                                {t("search.filters.sectionPath")}
                              </label>
                              <input
                                id="search-section-path"
                                type="text"
                                value={sectionPath}
                                onChange={(event) => setSectionPath(event.target.value)}
                                placeholder={t("search.filters.sectionPathPlaceholder")}
                                className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                              />
                            </div>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>

                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="secondary"
                      size="md"
                      onClick={clearFilters}
                      disabled={!hasAdvancedSettings || isStreaming}
                      className="w-full sm:w-auto"
                    >
                      <X size={15} aria-hidden />
                      {t("search.filters.clear")}
                    </Button>
                  </div>
                </fieldset>
                ) : null}
              </div>
            </CardContent>
          </Card>

          {/* 状態別表示 */}
          {phase === "idle" ? (
            <Card>
              <CardContent className="pt-5">
                <EmptyState title={t("search.initial")} hint={t("search.initialHint")} />
              </CardContent>
            </Card>
          ) : phase === "error" ? (
            <ErrorState message={errorText} onRetry={() => void submit()} />
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
                <div className="rounded-md border border-border bg-card px-3 py-2 text-sm text-muted" role="status">
                  {t("search.cancelled")}
                </div>
              ) : null}

              {/* 回答 */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Sparkles size={16} className="text-primary" aria-hidden />
                    {t("search.answer")}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {run ? (
                    <SearchRunPanel
                      run={run}
                      phase={phase}
                      nowMs={elapsedNowMs}
                      traceId={run.traceId ?? meta?.trace_id ?? null}
                    />
                  ) : null}
                  <ActiveFilterChips filters={appliedFilters} />
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                    {answer || (phase === "cancelled" ? t("search.cancelledHint") : "")}
                    {isStreaming ? (
                      <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" />
                    ) : null}
                  </p>
                  {meta && phase === "done" ? (
                    <FeedbackControls
                      traceId={meta.trace_id}
                      businessViewId={
                        meta.diagnostics?.business_view_applied ?? businessViewIds[0] ?? null
                      }
                      targetType="answer"
                      sourceSurface="search"
                    />
                  ) : null}
                  {meta && phase === "done" ? (
                    <SearchExecutionMeta meta={meta} />
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
                  <h2 className="mb-3 text-sm font-semibold text-foreground">
                    {t("search.citations")}（{citations.length}）
                  </h2>
                  <ul className="bounded-scroll-area-lg space-y-3 rounded-lg border border-border bg-background p-3">
                    {citations.map((chunk, i) => (
                      <CitationCard
                        key={chunk.chunk_id}
                        chunk={chunk}
                        index={i}
                        traceId={meta?.trace_id}
                        businessViewId={
                          meta?.diagnostics?.business_view_applied ?? businessViewIds[0] ?? null
                        }
                        sourceSurface="search"
                        scoreMaxima={citationScoreMaxima}
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
      </div>
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
  nowMs,
  traceId,
}: {
  run: SearchRun;
  phase: Phase;
  nowMs: number;
  traceId: string | null;
}) {
  const completedStages = run.stages.filter((stage) => stage.outcome !== "started");
  return (
    <section
      aria-label={t("search.run.title")}
      className="mb-4 space-y-3 border-b border-border pb-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Clock3 size={15} className="text-primary" aria-hidden />
          {t("search.run.title")}
        </h3>
        <span className={runStatusClass(phase)}>{runStatusLabel(phase)}</span>
      </div>
      <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
        <SearchRunMetric label={t("search.run.startedAt")} value={formatDateTime(run.startedAtIso)} />
        <SearchRunMetric
          label={t("search.run.elapsed")}
          value={formatSearchElapsed(run, nowMs)}
          testId="search-run-elapsed"
        />
        <SearchRunMetric label={t("search.run.currentStage")} value={currentStageLabel(run, phase)} />
        <SearchRunMetric label={t("search.run.trace")} value={shortTraceId(traceId)} />
      </dl>
      {completedStages.length ? (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted">{t("search.run.stages")}</p>
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
      <dt className="text-muted">{label}</dt>
      <dd data-testid={testId} className="tnum mt-0.5 truncate font-medium text-foreground">
        {value}
      </dd>
    </div>
  );
}

function currentStageLabel(run: SearchRun, phase: Phase): string {
  if (phase === "done") return t("search.run.stage.done");
  if (phase === "cancelled") return t("search.run.stage.cancelled");
  if (phase === "error") return t("search.run.stage.error");
  const latest = run.stages[run.stages.length - 1];
  return latest ? stageLabel(latest.stage) : t("search.stage.waiting");
}

function stageLabel(stage: string): string {
  return t(STAGE_LABEL[stage] ?? "search.stage.processing");
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
      return `${base} bg-success-bg text-success`;
    case "error":
      return `${base} bg-danger-bg text-danger`;
    case "cancelled":
      return `${base} bg-warning-bg text-warning`;
    default:
      return `${base} bg-info-bg text-info`;
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
      return `${base} bg-success-bg text-success`;
    case "error":
    case "cancelled":
      return `${base} bg-danger-bg text-danger`;
    default:
      return `${base} bg-muted/10 text-muted`;
  }
}

function formatSearchElapsed(run: SearchRun, nowMs: number): string {
  const end = run.endedAtMs ?? nowMs;
  if (!Number.isFinite(end) || end < run.startedAtMs) return "—";
  const seconds = Math.max(0, Math.round((end - run.startedAtMs) / 1000));
  if (seconds < 60) return t("search.run.elapsedSeconds", { seconds });
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest
    ? t("search.run.elapsedMinutesSeconds", { minutes, seconds: rest })
    : t("search.run.elapsedMinutes", { minutes });
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
      <p className="text-xs font-medium text-muted">{t("search.filters.applied")}</p>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((chip) => (
          <span
            key={chip.key}
            className="max-w-full break-all rounded-full border border-border bg-background px-2 py-0.5 text-xs font-medium leading-snug text-foreground"
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
      <p className="tnum flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span>
          {t("search.meta.elapsed")}: {Math.round(meta.elapsed_ms)} ms
        </span>
        <span>
          {t("search.meta.trace")}: {meta.trace_id.slice(0, 12)}
        </span>
      </p>
      {keywordTerms.length ? (
        <div aria-label={t("search.meta.keywords")} className="space-y-1.5">
          <p className="text-xs font-medium text-muted">{t("search.meta.keywords")}</p>
          <div className="flex flex-wrap gap-1.5">
            {keywordTerms.map((term, index) => (
              <span
                key={`${term}-${index}`}
                className="max-w-full break-all rounded-full border border-border bg-background px-2 py-0.5 text-xs font-medium leading-snug text-foreground"
              >
                {term}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      <RetrievalFlow breakdown={breakdown} />
      {items.length || candidates.length ? (
        <div className="rounded-md border border-border bg-background">
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
            className="w-full cursor-pointer px-3 py-2 text-left text-xs font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            {t("search.meta.diagnostics")}
          </button>
          {diagnosticsOpen ? (
          <div id="search-diagnostics-panel" className="space-y-3 border-t border-border p-3">
            {items.length ? (
              <section className="space-y-2">
                <h4 className="text-xs font-medium text-muted">{t("search.meta.detailMetrics")}</h4>
                <dl
                  aria-label={t("search.meta.execution")}
                  className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4"
                >
                  {items.map((item) => (
                    <div
                      key={item.key}
                      className="min-w-0 rounded-md border border-border bg-card px-3 py-2"
                    >
                      <dt className="truncate text-[11px] font-medium text-muted">{item.label}</dt>
                      <dd className="tnum mt-0.5 text-sm font-semibold text-foreground">{item.value}</dd>
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
      <p className="text-xs font-medium text-muted">{t("search.meta.flow")}</p>
      <ol
        aria-label={t("search.meta.flow")}
        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-background px-3 py-2 text-xs"
      >
        {steps.map((step, index) => (
          <li key={step.key} className="inline-flex items-center gap-2">
            {index > 0 ? <span className="text-muted" aria-hidden>→</span> : null}
            <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
              <span className="text-muted">{step.label}</span>
              <strong className="tnum text-sm text-foreground">{step.value}</strong>
            </span>
          </li>
        ))}
      </ol>
      {breakdown.dropped_count > 0 ? (
        <p className="tnum text-xs text-muted">
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
      <h4 className="text-xs font-medium text-muted">{t("search.meta.candidateDetails")}</h4>
      {candidates.length ? (
        <div role="table" aria-label={t("search.meta.candidateDetails")} className="space-y-1.5">
          <div
            role="row"
            className="hidden grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_90px_90px_80px_90px_minmax(0,1fr)] gap-2 px-2 text-[11px] font-medium text-muted md:grid"
          >
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
        <p className="text-xs text-muted">{t("search.meta.noCandidates")}</p>
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
    <div
      role="row"
      className="grid gap-2 rounded-md border border-border bg-card p-2 text-xs md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_90px_90px_80px_90px_minmax(0,1fr)]"
    >
      <span role="cell" className="min-w-0">
        <span className="block truncate font-medium text-foreground" title={candidate.file_name ?? candidate.document_id}>
          {candidate.file_name ?? candidate.document_id}
        </span>
        <span className="block truncate text-[11px] text-muted" title={candidate.chunk_id}>
          {candidate.chunk_id}
        </span>
      </span>
      <span role="cell" className="flex flex-wrap gap-1">
        {candidate.sources.map((source) => (
          <span key={source} className="rounded-full bg-muted/10 px-2 py-0.5 text-[11px] font-medium text-muted">
            {sourceLabel(source)}
          </span>
        ))}
      </span>
      <span role="cell" className="tnum text-foreground">{formatRankScore(candidate.vector_rank, candidate.vector_score)}</span>
      <span role="cell" className="tnum text-foreground">{formatRankScore(candidate.keyword_rank, candidate.keyword_score)}</span>
      <span role="cell" className="tnum text-foreground">{formatScore(candidate.rrf_score)}</span>
      <span role="cell" className="tnum text-foreground">{formatRankScore(candidate.rerank_rank, candidate.rerank_score)}</span>
      <span role="cell" className="min-w-0 text-foreground">
        <span>{candidateStatusLabel(candidate.status)}</span>
        {candidate.drop_reason ? (
          <span className="ml-1 text-muted">({dropReasonLabel(candidate.drop_reason)})</span>
        ) : null}
      </span>
    </div>
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
}: {
  contentKind: ContentKindFilter;
  sectionTitle: string;
  sectionPath: string;
}): Record<string, string> {
  const filters: Record<string, string> = {};
  if (contentKind) filters.content_kind = contentKind;
  if (sectionTitle.trim()) filters.section_title = sectionTitle.trim();
  if (sectionPath.trim()) filters.section_path = sectionPath.trim();
  return filters;
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
      <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
        {t("businessViews.scope.label")}
        <span className="rounded-full bg-warning-bg px-2 py-0.5 text-[10px] font-medium text-warning">
          {t("common.required")}
        </span>
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
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : (
        <p className="text-xs text-muted">
          {selectedIds.length > 0
            ? t("businessViews.scope.applied", { count: selectedIds.length })
            : t("businessViews.scope.helper")}
        </p>
      )}
    </div>
  );
}
