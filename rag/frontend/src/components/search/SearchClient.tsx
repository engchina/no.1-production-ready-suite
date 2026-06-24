"use client";

import {
  Plus,
  Search as SearchIcon,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { BusinessViewPickerGrid } from "@/components/business-views/BusinessViewPickerGrid";
import { CitationCard } from "./CitationCard";
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
import { streamSearch } from "@/lib/search-stream";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useBusinessViews } from "@/lib/queries";

type Phase = "idle" | "streaming" | "done" | "cancelled" | "error";

interface Meta {
  trace_id: string;
  elapsed_ms: number;
  guardrail_warnings: string[];
  diagnostics: Partial<SearchDiagnostics> | null;
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
  const abortRef = useRef<AbortController | null>(null);
  const navigate = useNavigate();
  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 50, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  const hasFilters =
    Boolean(contentKind) || Boolean(sectionTitle.trim()) || Boolean(sectionPath.trim());

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

    setPhase("streaming");
    setAnswer("");
    setCitations([]);
    setMeta(null);
    setErrorText("");

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
          onMetadata: (m) =>
            setMeta({
              trace_id: m.trace_id,
              elapsed_ms: m.elapsed_ms,
              guardrail_warnings: m.guardrail_warnings,
              diagnostics: m.diagnostics ?? null,
            }),
          onDelta: (text) => setAnswer((prev) => prev + text),
          onCitations: (list) => setCitations(list),
          onDone: () => {
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
    setPhase("cancelled");
    setErrorText("");
  };
  const clearFilters = () => {
    setContentKind("");
    setSectionTitle("");
    setSectionPath("");
  };

  const noResults = phase === "done" && citations.length === 0;
  const isStreaming = phase === "streaming";

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

function SearchExecutionMeta({ meta }: { meta: Meta }) {
  const diagnostics = meta.diagnostics ?? {};
  const items = searchExecutionItems(diagnostics);
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
