"use client";

import { FlaskConical, Search as SearchIcon, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { RunStopButton } from "@/components/RunStopButton";
import { AnswerProgress } from "@/components/search/AnswerProgress";
import { CitationCard } from "@/components/search/CitationCard";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  TextField,
  ToggleChip,
} from "@engchina/production-ready-ui";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { ApiError, type RetrievedChunk, type SearchMode } from "@/lib/api";
import { streamSearch, type SearchStageEvent } from "@/lib/search-stream";
import { isSubmitEnter } from "@/lib/keyboard";
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
  // 処理中の表示（今の工程と経過時間）。ボタンは実行中に「停止」になりスピナーを出さないため、ここで示す（#413）。
  const [stages, setStages] = useState<SearchStageEvent[]>([]);
  const [startedAtMs, setStartedAtMs] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  // パネルを離れたら生成中の検索を止める（#285）。
  useEffect(() => () => abortRef.current?.abort(), []);

  const ready = !disabled && indexedDocumentCount > 0;
  const isStreaming = phase === "streaming";

  const submit = async () => {
    const trimmed = query.trim();
    if (!trimmed || isStreaming || !ready) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase("streaming");
    setStages([]);
    setStartedAtMs(Date.now());
    setAnswer("");
    setCitations([]);
    setMeta(null);
    setErrorText("");

    try {
      await streamSearch(
        { query: trimmed, mode, top_k: TEST_TOP_K, knowledge_base_ids: [knowledgeBaseId] },
        {
          onStage: (stage) => setStages((current) => [...current, stage]),
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
          <FlaskConical className="size-4 text-fg-muted" aria-hidden />
          {t("knowledgeBases.searchTest.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-fg-muted">{t("knowledgeBases.searchTest.description")}</p>

        {!ready ? (
          <EmptyState
            title={t("knowledgeBases.searchTest.needsIndexed")}
            hint={t("knowledgeBases.searchTest.needsIndexedHint")}
          />
        ) : (
          <>
            <div className="flex flex-col gap-2 sm:flex-row">
              {/* 隣の lg の Button と同じ高さ（size="lg"）。入力欄の Enter は実行だけ（実行中の Enter は submit が無視する）。 */}
              <TextField
                id={inputId}
                label={t("knowledgeBases.searchTest.title")}
                labelHidden
                size="lg"
                value={query}
                onValueChange={setQuery}
                onKeyDown={(e) => {
                  if (isSubmitEnter(e)) void submit();
                }}
                placeholder={t("knowledgeBases.searchTest.placeholder")}
                leadingIcon={SearchIcon}
                className="min-w-0 flex-1"
              />
              {/* 検索テストと停止は同じボタン。実行中は同じ位置で「停止」になる（buttons.md §3.1、#413）。 */}
              <RunStopButton
                running={isStreaming}
                onRun={() => void submit()}
                onStop={cancel}
                runLabel={t("knowledgeBases.searchTest.button")}
                stopLabel={t("knowledgeBases.searchTest.cancel")}
                runIcon={SearchIcon}
                className="sm:min-w-28"
                testId="kb-search-test-run-stop"
              />
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
                  <div className="rounded-lg border border-border bg-surface p-4">
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-fg">
                      <Sparkles size={16} className="text-accent-fg" aria-hidden />
                      {t("search.answer")}
                    </h3>
                    {isStreaming ? (
                      <AnswerProgress
                        active
                        stages={stages}
                        startedAtMs={startedAtMs}
                        testId="kb-search-test-progress"
                        className="mb-3"
                      />
                    ) : null}
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
                      {answer || (phase === "cancelled" ? t("search.cancelledHint") : "")}
                      {isStreaming ? (
                        <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent-emphasis align-middle motion-reduce:animate-none" />
                      ) : null}
                    </p>
                    {meta && phase === "done" ? (
                      <p className="tnum mt-3 border-t border-border pt-2 text-xs text-fg-muted">
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
                    <h3 className="mb-3 text-sm font-semibold text-fg">
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
