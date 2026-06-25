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
import { CitationCard, type CitationScoreMaxima } from "./CitationCard";
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
] as const;
type ContentKindFilter = (typeof CONTENT_KIND_OPTIONS)[number];
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
};
const CONTENT_KIND_SELECT_OPTIONS = CONTENT_KIND_OPTIONS.map((option) => ({
  value: option,
  label: t(CONTENT_KIND_LABEL[option]),
})) satisfies SelectFieldOption<ContentKindFilter>[];
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
  const [businessViewIds, setBusinessViewIds] = useState<string[]>([]);
  const [scopeError, setScopeError] = useState("");
  const [run, setRun] = useState<SearchRun | null>(null);
  const [elapsedNowMs, setElapsedNowMs] = useState(Date.now());
  const abortRef = useRef<AbortController | null>(null);
  const navigate = useNavigate();
  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 50, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  const hasFilters =
    Boolean(contentKind) || Boolean(sectionTitle.trim()) || Boolean(sectionPath.trim());
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
      await streamSearch(
        {
          query: trimmed,
          mode,
          top_k: 20,
          rerank_top_n: 5,
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
          {/* 検索バー */}
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <div className="relative min-w-0 flex-1">
                <SearchIcon
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                  aria-hidden
                />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submit();
                  }}
                  placeholder={t("search.placeholder")}
                  aria-label={t("nav.search")}
                  className="w-full rounded-md border border-border bg-card py-2.5 pl-9 pr-3 text-sm outline-none focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                />
              </div>
              <Button onClick={() => void submit()} loading={isStreaming} size="lg">
                {isStreaming ? t("search.searching") : t("search.button")}
              </Button>
              {isStreaming ? (
                <Button type="button" variant="secondary" size="lg" onClick={cancel}>
                  <X size={16} aria-hidden />
                  {t("search.cancel")}
                </Button>
              ) : null}
            </div>

            {/* モード切替 */}
            <div className="flex items-center gap-1" role="group" aria-label={t("search.pipeline")}>
              {MODES.map((m) => (
                <ToggleChip key={m} selected={mode === m} onClick={() => setMode(m)}>
                  {t(MODE_LABEL[m])}
                </ToggleChip>
              ))}
            </div>
            <p className="text-xs text-muted">{t("search.pipeline")}</p>

            <fieldset className="grid gap-3 rounded-md border border-border bg-card p-3 sm:grid-cols-[160px_minmax(0,1fr)_minmax(0,1fr)_auto]">
              <legend className="px-1 text-xs font-medium text-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <SlidersHorizontal size={14} className="text-primary" aria-hidden />
                  {t("search.filters.title")}
                </span>
              </legend>

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

              <SelectField
                id="search-content-kind"
                label={t("search.filters.contentKind")}
                value={contentKind}
                options={CONTENT_KIND_SELECT_OPTIONS}
                onValueChange={setContentKind}
                className="[&_label]:text-xs"
                buttonClassName="bg-background"
              />

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

              <div className="flex items-end">
                <Button
                  type="button"
                  variant="secondary"
                  size="md"
                  onClick={clearFilters}
                  disabled={!hasFilters || isStreaming}
                  className="w-full sm:w-auto"
                >
                  <X size={15} aria-hidden />
                  {t("search.filters.clear")}
                </Button>
              </div>
            </fieldset>
          </div>

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
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                    {answer || (phase === "cancelled" ? t("search.cancelledHint") : "")}
                    {isStreaming ? (
                      <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle" />
                    ) : null}
                  </p>
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

function scoreMaximaForCitations(citations: RetrievedChunk[]): CitationScoreMaxima {
  return {
    score: maxScore(citations.map((chunk) => chunk.score)),
    rerankScore: maxScore(
      citations.flatMap((chunk) => (chunk.rerank_score == null ? [] : [chunk.rerank_score]))
    ),
  };
}

function maxScore(values: number[]): number {
  return values.reduce(
    (max, value) => (Number.isFinite(value) && value > max ? value : max),
    0
  );
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

function SearchExecutionMeta({ meta }: { meta: Meta }) {
  const diagnostics = meta.diagnostics ?? {};
  const items = searchExecutionItems(diagnostics);
  const keywordTerms = (diagnostics.keyword_terms ?? []).filter(Boolean);
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
      {items.length ? (
        <dl
          aria-label={t("search.meta.execution")}
          className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4"
        >
          {items.map((item) => (
            <div
              key={item.key}
              className="min-w-0 rounded-md border border-border bg-background px-3 py-2"
            >
              <dt className="truncate text-[11px] font-medium text-muted">{item.label}</dt>
              <dd className="tnum mt-0.5 text-sm font-semibold text-foreground">{item.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

function searchExecutionItems(diagnostics: Partial<SearchDiagnostics>) {
  return [
    { key: "retrieved", label: t("search.meta.retrieved"), value: diagnostics.retrieved_count },
    { key: "reranked", label: t("search.meta.reranked"), value: diagnostics.reranked_count },
    { key: "citations", label: t("search.meta.citations"), value: diagnostics.citation_count },
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
