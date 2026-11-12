import { Banner, Button, ProcessingIndicator, Skeleton, StatusBadge, TimedLoadingState } from "@production-ready/ui";
import { RefreshCw, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useDocumentContentSummary, useRegenerateDocumentContentSummary } from "@/lib/queries";

import { SUMMARY_AWAIT_AFTER_INGESTION_MS, documentSummaryView } from "./DocumentSummaryPanel.logic";

/**
 * 文書の詳細の「文書の要約」（#4793）。取込の後に 1 文書 1 回の LLM で作った文書全体の説明を出す。
 *
 * - 要約は AI が作った説明で、原文ではないことを見出しの説明で示す。
 * - 作成中は `ProcessingIndicator`、失敗は理由と「作り直す」、まだ無い文書は「要約を作る」。
 * - `ingesting` は取込の job が動いているか。動いている間と終わった直後は、後段が作る要約の状態を読み直す。
 * - `canRegenerate` は検索に出している索引があるか（無い文書は作れない）。
 */
export function DocumentSummaryPanel({
  documentId,
  ingesting,
  canRegenerate,
}: {
  documentId: string;
  ingesting: boolean;
  canRegenerate: boolean;
}) {
  const awaiting = useAwaitAfter(ingesting, SUMMARY_AWAIT_AFTER_INGESTION_MS);
  const query = useDocumentContentSummary(documentId, { awaiting });
  const regenerate = useRegenerateDocumentContentSummary();
  const state = query.data;
  const view = state ? documentSummaryView(state) : null;
  const summary = state?.summary ?? null;
  const busy = regenerate.isPending || view?.kind === "generating" || (view?.kind === "ready" && view.regenerating);
  const action =
    state?.enabled && canRegenerate && view ? (
      <Button
        type="button"
        variant="secondary"
        size="sm"
        icon={view.kind === "empty" ? Sparkles : RefreshCw}
        loading={regenerate.isPending}
        disabled={busy}
        onClick={() => regenerate.mutate(documentId)}
      >
        {view.kind === "empty" ? t("documents.summary.create") : t("documents.summary.regenerate")}
      </Button>
    ) : null;

  return (
    <section className="space-y-3 border-t border-border pt-4" data-testid="document-summary" aria-labelledby="document-summary-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id="document-summary-title" className="text-sm font-semibold text-fg">{t("documents.summary.title")}</h3>
          <p className="mt-1 text-xs text-fg-muted">{t("documents.summary.description")}</p>
        </div>
        {action}
      </div>
      {query.isPending ? (
        <TimedLoadingState label={t("documents.summary.loading")} operationKey={`document-summary-${documentId}`}>
          <Skeleton className="h-16 w-full" />
        </TimedLoadingState>
      ) : query.isError || !state || !view ? (
        <ApiErrorState error={query.error} fallback={t("documents.summary.loadError")} onRetry={() => void query.refetch()} />
      ) : (
        <>
          {regenerate.isPending || view.kind === "generating" || (view.kind === "ready" && view.regenerating) ? (
            <ProcessingIndicator
              active
              label={view.kind === "ready" || regenerate.isPending ? t("documents.summary.regenerating") : t("documents.summary.generating")}
              operationKey={`document-summary-generate-${documentId}`}
              // 「作り直す」のボタンが回っている間は、動く印を 1 つにする。
              activityIcon={regenerate.isPending ? "none" : "spinner"}
              testId="document-summary-generating"
            />
          ) : null}
          {regenerate.isError ? (
            <Banner severity="danger" title={t("documents.summary.regenerateError")}>
              {regenerate.error instanceof ApiError ? regenerate.error.message : t("documents.summary.failure.unknown")}
            </Banner>
          ) : null}
          {view.kind === "ready" && summary ? (
            <div className="space-y-3">
              {view.failure && !busy ? (
                <Banner severity="warning" title={t("documents.summary.regenerateFailedTitle")}>
                  {t(view.failure)}
                </Banner>
              ) : null}
              <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-fg" data-testid="document-summary-text">
                {summary.text}
              </p>
              {summary.topics.length > 0 ? (
                <ul className="flex flex-wrap gap-2" aria-label={t("documents.summary.topics")}>
                  {summary.topics.map((topic) => (
                    <li key={topic} className="max-w-full">
                      <StatusBadge variant="neutral" label={topic} icon={false} />
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className="space-y-1 text-xs text-fg-muted">
                {summary.generated_at ? (
                  <p className="tnum">{t("documents.summary.generatedAt", { time: formatDateTime(summary.generated_at) })}</p>
                ) : null}
                {summary.input_truncated ? <p>{t("documents.summary.truncated")}</p> : null}
              </div>
            </div>
          ) : view.kind === "failed" && !busy ? (
            <Banner severity="warning" title={t("documents.summary.failedTitle")}>
              <div className="space-y-1">
                <p>{t(view.failure)}</p>
                {state.enabled && canRegenerate ? <p>{t("documents.summary.failedHint")}</p> : null}
              </div>
            </Banner>
          ) : view.kind === "empty" && !busy ? (
            <p className="text-sm text-fg-muted" data-testid="document-summary-empty">
              {!state.enabled
                ? t("documents.summary.disabled")
                : canRegenerate
                  ? t("documents.summary.empty")
                  : t("documents.summary.emptyBeforeIndex")}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

/** `active` の間と、`active` が終わってから `holdMs` の間 true。 */
function useAwaitAfter(active: boolean, holdMs: number): boolean {
  const [holding, setHolding] = useState(false);
  const wasActive = useRef(active);
  useEffect(() => {
    if (active) {
      wasActive.current = true;
      return;
    }
    if (!wasActive.current) return;
    wasActive.current = false;
    setHolding(true);
    const timer = window.setTimeout(() => setHolding(false), holdMs);
    return () => window.clearTimeout(timer);
  }, [active, holdMs]);
  return active || holding;
}
