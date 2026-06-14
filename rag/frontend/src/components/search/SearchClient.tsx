"use client";

import { DatabaseZap, Search as SearchIcon, SlidersHorizontal, Sparkles, X } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";

import { CitationCard } from "./CitationCard";
import { PageHeader } from "@/components/PageHeader";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { ApiError, type RetrievedChunk, type SearchMode, type SelectAiAction } from "@/lib/api";
import { streamSearch } from "@/lib/search-stream";
import { t } from "@/lib/i18n";
import { useSelectAi } from "@/lib/queries";
import { cn } from "@/lib/utils";

type Phase = "idle" | "streaming" | "done" | "cancelled" | "error";

interface Meta {
  trace_id: string;
  elapsed_ms: number;
  guardrail_warnings: string[];
}

const MODES: SearchMode[] = ["hybrid", "vector", "keyword"];
const SELECT_AI_ACTIONS: SelectAiAction[] = ["showsql", "runsql"];
const CONTENT_KIND_OPTIONS = ["", "text", "list", "table", "figure"] as const;
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
};
const SELECT_AI_ACTION_LABEL: Record<SelectAiAction, Parameters<typeof t>[0]> = {
  showsql: "search.selectAi.action.showsql",
  runsql: "search.selectAi.action.runsql",
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
  const abortRef = useRef<AbortController | null>(null);
  const hasFilters =
    Boolean(contentKind) || Boolean(sectionTitle.trim()) || Boolean(sectionPath.trim());

  const submit = async () => {
    const trimmed = query.trim();
    if (!trimmed || phase === "streaming") return;

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
          ...(Object.keys(filters).length ? { filters } : {}),
        },
        {
          onMetadata: (m) =>
            setMeta({
              trace_id: m.trace_id,
              elapsed_ms: m.elapsed_ms,
              guardrail_warnings: m.guardrail_warnings,
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
      <div className="grid gap-6 p-8 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.9fr)]">
        <section className="space-y-6">
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
              {/* ガードレール警告 */}
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
                    <p className="tnum mt-4 flex flex-wrap gap-x-4 border-t border-border pt-3 text-xs text-muted">
                      <span>
                        {t("search.meta.elapsed")}: {Math.round(meta.elapsed_ms)} ms
                      </span>
                      <span>
                        {t("search.meta.trace")}: {meta.trace_id.slice(0, 12)}
                      </span>
                    </p>
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
                  <ul className="space-y-3">
                    {citations.map((chunk, i) => (
                      <CitationCard key={chunk.chunk_id} chunk={chunk} index={i} />
                    ))}
                  </ul>
                </section>
              ) : null}
            </>
          )}
        </section>

        <SelectAiPanel />
      </div>
    </div>
  );
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

function SelectAiPanel() {
  const mutation = useSelectAi();
  const [query, setQuery] = useState("");
  const [action, setAction] = useState<SelectAiAction>("showsql");
  const [profileName, setProfileName] = useState("");
  const [maxChars, setMaxChars] = useState(20000);
  const [errorText, setErrorText] = useState("");

  const result = mutation.data;
  const canSubmit = query.trim().length > 0 && maxChars >= 1000 && maxChars <= 200000;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;
    setErrorText("");
    mutation.reset();
    try {
      await mutation.mutateAsync({
        query: query.trim(),
        action,
        profile_name: profileName.trim() || null,
        max_result_chars: maxChars,
      });
    } catch (error) {
      setErrorText(
        error instanceof ApiError ? error.message : t("search.selectAi.error")
      );
    }
  };

  return (
    <aside className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <DatabaseZap size={16} className="text-primary" aria-hidden />
            {t("search.selectAi.title")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={(event) => void submit(event)}>
            <div className="space-y-1.5">
              <label htmlFor="select-ai-query" className="text-xs font-medium text-foreground">
                {t("search.selectAi.query")}
              </label>
              <textarea
                id="select-ai-query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("search.selectAi.placeholder")}
                rows={4}
                className="min-h-28 w-full resize-y rounded-md border border-border bg-card px-3 py-2 text-sm leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
              />
            </div>

            <div className="space-y-1.5">
              <span className="text-xs font-medium text-foreground">
                {t("search.selectAi.action")}
              </span>
              <div className="grid grid-cols-2 gap-1 rounded-md border border-border bg-background p-1">
                {SELECT_AI_ACTIONS.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setAction(item)}
                    aria-pressed={action === item}
                    className={cn(
                      "min-h-10 cursor-pointer rounded px-3 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
                      action === item
                        ? "bg-primary text-primary-foreground"
                        : "text-muted hover:bg-card hover:text-foreground"
                    )}
                  >
                    {t(SELECT_AI_ACTION_LABEL[item])}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_128px] xl:grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_128px]">
              <div className="space-y-1.5">
                <label htmlFor="select-ai-profile" className="text-xs font-medium text-foreground">
                  {t("search.selectAi.profile")}
                </label>
                <input
                  id="select-ai-profile"
                  type="text"
                  value={profileName}
                  onChange={(event) => setProfileName(event.target.value)}
                  placeholder={t("search.selectAi.profilePlaceholder")}
                  className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="select-ai-max-chars" className="text-xs font-medium text-foreground">
                  {t("search.selectAi.maxChars")}
                </label>
                <input
                  id="select-ai-max-chars"
                  type="number"
                  min={1000}
                  max={200000}
                  step={1000}
                  value={maxChars}
                  onChange={(event) => setMaxChars(Number(event.target.value))}
                  className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
                />
              </div>
            </div>

            <Button
              type="submit"
              className="w-full"
              loading={mutation.isPending}
              disabled={!canSubmit}
            >
              {mutation.isPending ? t("search.selectAi.running") : t("search.selectAi.run")}
            </Button>
          </form>
        </CardContent>
      </Card>

      {errorText ? <Banner severity="danger">{errorText}</Banner> : null}

      {result ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{t("search.selectAi.result")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {result.guardrail_warnings.length ? (
              <Banner severity="warning" className="text-xs">
                <span className="font-medium">{t("search.guardrail")}: </span>
                {result.guardrail_warnings.join(" / ")}
              </Banner>
            ) : null}
            <pre className="max-h-[420px] overflow-auto rounded-md border border-border bg-background p-3 text-xs leading-relaxed text-foreground">
              <code>{result.result_text}</code>
            </pre>
            <p className="tnum flex flex-wrap gap-x-3 gap-y-1 border-t border-border pt-3 text-xs text-muted">
              <span>
                {t("search.selectAi.resultAction")}: {t(SELECT_AI_ACTION_LABEL[result.action])}
              </span>
              <span>
                {t("search.selectAi.queryChars")}: {result.query_chars}
              </span>
              {result.profile_name ? (
                <span>
                  {t("search.selectAi.profile")}: {result.profile_name}
                </span>
              ) : null}
            </p>
          </CardContent>
        </Card>
      ) : null}
    </aside>
  );
}
