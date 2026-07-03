"use client";

import { FlaskConical, Search as SearchIcon, Sparkles, X } from "lucide-react";
import { useRef, useState } from "react";

import { CitationCard } from "@/components/search/CitationCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { ApiError, type RetrievedChunk, type SearchMode } from "@/lib/api";
import { streamSearch } from "@/lib/search-stream";
import { t, type I18nKey } from "@/lib/i18n";

type Phase = "idle" | "streaming" | "done" | "cancelled" | "error";

const MODES: SearchMode[] = ["hybrid", "vector", "keyword"];
const MODE_LABEL: Record<SearchMode, I18nKey> = {
  hybrid: "search.mode.hybrid",
  vector: "search.mode.vector",
  keyword: "search.mode.keyword",
};
const TEST_TOP_K = 10;

/**
 * KB 詳細の「このナレッジで検索テスト」パネル。
 * 業務ビュー(Business View)を介さず、単一 KB scope で retrieval をその場確認する。
 * backend は business_view_ids が無ければ request 明示 KB scope + global defaults で検索する。
 */
export function KnowledgeBaseSearchTestPanel({
  knowledgeBaseId,
  indexedDocumentCount,
  disabled = false,
}: {
  knowledgeBaseId: string;
  indexedDocumentCount: number;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [phase, setPhase] = useState<Phase>("idle");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<RetrievedChunk[]>([]);
  const [meta, setMeta] = useState<{ trace_id: string; elapsed_ms: number } | null>(null);
  const [errorText, setErrorText] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const ready = !disabled && indexedDocumentCount > 0;
  const isStreaming = phase === "streaming";

  const submit = async () => {
    const trimmed = query.trim();
    if (!trimmed || isStreaming || !ready) return;

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
        { query: trimmed, mode, top_k: TEST_TOP_K, knowledge_base_ids: [knowledgeBaseId] },
        {
          onMetadata: (m) => setMeta({ trace_id: m.trace_id, elapsed_ms: m.elapsed_ms }),
          onDelta: (text) => setAnswer((prev) => prev + text),
          onReplace: (text) => setAnswer(text),
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
        error instanceof ApiError ? error.message : t("knowledgeBases.searchTest.error")
      );
      setPhase("error");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const cancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase("cancelled");
    setErrorText("");
  };

  const noResults = phase === "done" && citations.length === 0;
  const inputId = `kb-search-test-${knowledgeBaseId}`;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FlaskConical className="size-4 text-muted" aria-hidden />
          {t("knowledgeBases.searchTest.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted">{t("knowledgeBases.searchTest.description")}</p>

        {!ready ? (
          <EmptyState
            title={t("knowledgeBases.searchTest.needsIndexed")}
            hint={t("knowledgeBases.searchTest.needsIndexedHint")}
          />
        ) : (
          <>
            <div className="flex flex-col gap-2 sm:flex-row">
              <label htmlFor={inputId} className="sr-only">
                {t("knowledgeBases.searchTest.title")}
              </label>
              <div className="relative min-w-0 flex-1">
                <SearchIcon
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                  aria-hidden
                />
                <input
                  id={inputId}
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submit();
                  }}
                  placeholder={t("knowledgeBases.searchTest.placeholder")}
                  aria-label={t("knowledgeBases.searchTest.title")}
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
                {isStreaming
                  ? t("knowledgeBases.searchTest.searching")
                  : t("knowledgeBases.searchTest.button")}
              </Button>
              {isStreaming ? (
                <Button type="button" variant="secondary" size="lg" onClick={cancel}>
                  <X size={16} aria-hidden />
                  {t("knowledgeBases.searchTest.cancel")}
                </Button>
              ) : null}
            </div>

            <div
              className="flex flex-wrap items-center gap-1"
              role="group"
              aria-label={t("search.pipeline")}
            >
              {MODES.map((m) => (
                <ToggleChip key={m} selected={mode === m} onClick={() => setMode(m)}>
                  {t(MODE_LABEL[m])}
                </ToggleChip>
              ))}
            </div>

            {phase === "idle" ? (
              <EmptyState title={t("knowledgeBases.searchTest.initialHint")} />
            ) : phase === "error" ? (
              <ErrorState message={errorText} onRetry={() => void submit()} />
            ) : (
              <div className="space-y-4">
                {answer || isStreaming ? (
                  <div className="rounded-lg border border-border bg-card p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
                      <Sparkles size={15} className="text-primary" aria-hidden />
                      {t("search.answer")}
                    </h3>
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                      {answer || (phase === "cancelled" ? t("search.cancelledHint") : "")}
                      {isStreaming ? (
                        <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-middle motion-reduce:animate-none" />
                      ) : null}
                    </p>
                    {meta && phase === "done" ? (
                      <p className="tnum mt-3 border-t border-border pt-2 text-xs text-muted">
                        {t("knowledgeBases.searchTest.resultMeta", {
                          mode: t(MODE_LABEL[mode]),
                          count: citations.length,
                          ms: Math.round(meta.elapsed_ms),
                        })}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {noResults ? (
                  <EmptyState title={t("search.noResults")} hint={t("search.noResultsHint")} />
                ) : citations.length > 0 ? (
                  <section>
                    <h3 className="mb-3 text-sm font-semibold text-foreground">
                      {t("search.citations")}（{citations.length}）
                    </h3>
                    <ul className="bounded-scroll-area-lg space-y-2 pr-1">
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
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
