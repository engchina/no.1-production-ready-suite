"use client";

import { Search as SearchIcon, Sparkles } from "lucide-react";
import { useRef, useState } from "react";

import { CitationCard } from "./CitationCard";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { ApiError, type RetrievedChunk, type SearchMode } from "@/lib/api";
import { streamSearch } from "@/lib/search-stream";
import { t } from "@/lib/i18n";

type Phase = "idle" | "streaming" | "done" | "error";

interface Meta {
  trace_id: string;
  elapsed_ms: number;
  guardrail_warnings: string[];
}

const MODES: SearchMode[] = ["hybrid", "vector", "keyword"];
const MODE_LABEL: Record<SearchMode, Parameters<typeof t>[0]> = {
  hybrid: "search.mode.hybrid",
  vector: "search.mode.vector",
  keyword: "search.mode.keyword",
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
  const abortRef = useRef<AbortController | null>(null);

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
      await streamSearch(
        { query: trimmed, mode, top_k: 20, rerank_top_n: 5 },
        {
          onMetadata: (m) =>
            setMeta({
              trace_id: m.trace_id,
              elapsed_ms: m.elapsed_ms,
              guardrail_warnings: m.guardrail_warnings,
            }),
          onDelta: (text) => setAnswer((prev) => prev + text),
          onCitations: (list) => setCitations(list),
          onDone: () => setPhase("done"),
        },
        controller.signal
      );
    } catch (error) {
      if (controller.signal.aborted) return;
      setErrorText(
        error instanceof ApiError ? error.message : "検索に失敗しました。再度お試しください。"
      );
      setPhase("error");
    }
  };

  const noResults = phase === "done" && citations.length === 0;

  return (
    <div>
      <PageHeader title={t("nav.search")} subtitle={t("search.initial")} />
      <div className="space-y-6 p-8">
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
            <Button onClick={() => void submit()} loading={phase === "streaming"} size="lg">
              {phase === "streaming" ? t("search.searching") : t("search.button")}
            </Button>
          </div>

          {/* モード切替 */}
          <div className="flex items-center gap-1">
            {MODES.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                aria-pressed={mode === m}
                className={
                  mode === m
                    ? "cursor-pointer rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground"
                    : "cursor-pointer rounded-full border border-border bg-card px-3 py-1 text-xs text-muted hover:bg-background"
                }
              >
                {t(MODE_LABEL[m])}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted">{t("search.pipeline")}</p>
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
              <div className="rounded-md bg-warning-bg/60 px-3 py-2 text-sm text-warning" role="alert">
                <span className="font-medium">{t("search.guardrail")}: </span>
                {meta.guardrail_warnings.join(" / ")}
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
                  {answer}
                  {phase === "streaming" ? (
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
      </div>
    </div>
  );
}
