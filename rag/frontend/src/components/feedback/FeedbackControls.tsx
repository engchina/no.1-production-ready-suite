import { CheckCircle2, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  ApiError,
  type CitationFeedbackReason,
  type CitationFeedbackRating,
  type FeedbackRequestBody,
  type FeedbackSourceSurface,
  type FeedbackTargetType,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useCurrentFeedback, useSubmitFeedback } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const ANSWER_REASONS: CitationFeedbackReason[] = [
  "incorrect",
  "incomplete",
  "not_relevant",
  "answer_untrusted",
];
const CITATION_REASONS: CitationFeedbackReason[] = [
  "missing_evidence",
  "not_relevant",
  "answer_untrusted",
];
const REASON_LABEL_KEYS: Record<CitationFeedbackReason, I18nKey> = {
  incorrect: "feedback.reason.incorrect",
  incomplete: "feedback.reason.incomplete",
  missing_evidence: "feedback.reason.missing_evidence",
  not_relevant: "feedback.reason.not_relevant",
  answer_untrusted: "feedback.reason.answer_untrusted",
};

export function FeedbackControls({
  traceId,
  businessViewId,
  targetType,
  sourceSurface,
  documentId = null,
  chunkId = null,
  compact = false,
}: {
  traceId: string | null | undefined;
  businessViewId: string | null | undefined;
  targetType: FeedbackTargetType;
  sourceSurface: FeedbackSourceSurface;
  documentId?: string | null;
  chunkId?: string | null;
  compact?: boolean;
}) {
  const currentQuery = useCurrentFeedback(traceId ?? null);
  const mutation = useSubmitFeedback();
  const [showReasons, setShowReasons] = useState(false);
  const [error, setError] = useState("");
  const [retryPayload, setRetryPayload] = useState<FeedbackRequestBody | null>(null);
  const current = currentQuery.data?.find(
    (item) =>
      item.target_type === targetType &&
      (item.document_id ?? null) === documentId &&
      (item.chunk_id ?? null) === chunkId
  );
  const disabled = !traceId || !businessViewId || currentQuery.isLoading || mutation.isPending;
  const reasons = targetType === "answer" ? ANSWER_REASONS : CITATION_REASONS;
  const label =
    targetType === "answer"
      ? t("feedback.controls.answerQuestion")
      : t("feedback.controls.citationQuestion");
  const helpfulLabel =
    targetType === "answer"
      ? t("feedback.controls.answerHelpful")
      : t("search.citation.feedback.helpful");
  const notHelpfulLabel =
    targetType === "answer"
      ? t("feedback.controls.answerNotHelpful")
      : t("search.citation.feedback.notHelpful");

  if (!traceId || !businessViewId) return null;

  async function submit(rating: CitationFeedbackRating, reason: CitationFeedbackReason | null) {
    if (!traceId || !businessViewId) return;
    if (current?.rating === rating && (current.reason ?? null) === reason) {
      setShowReasons(false);
      return;
    }
    const payload: FeedbackRequestBody = {
      trace_id: traceId,
      business_view_id: businessViewId,
      target_type: targetType,
      source_surface: sourceSurface,
      document_id: targetType === "citation" ? documentId : null,
      chunk_id: targetType === "citation" ? chunkId : null,
      rating,
      reason,
    };
    setError("");
    setRetryPayload(payload);
    try {
      await mutation.mutateAsync(payload);
      setShowReasons(false);
      toast.success(t("feedback.controls.savedToast"));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("feedback.controls.saveError"));
    }
  }

  return (
    <div className={cn("min-w-0", !compact && "mt-4 border-t border-border pt-3")}>
      <div
        className={cn(
          "flex gap-2",
          compact ? "items-center justify-end" : "flex-wrap items-center"
        )}
      >
        <span className={compact ? "sr-only" : "mr-1 text-sm font-medium text-foreground"}>
          {label}
        </span>
        <div className="flex gap-2" role="group" aria-label={label}>
          <Button
            type="button"
            variant={current?.rating === "helpful" ? "secondary" : "ghost"}
            size="sm"
            className={cn(
              "size-11 min-h-11 min-w-11 touch-manipulation px-0",
              current?.rating === "helpful" && "text-success"
            )}
            aria-label={helpfulLabel}
            aria-pressed={current?.rating === "helpful"}
            title={helpfulLabel}
            disabled={disabled}
            loading={mutation.isPending && retryPayload?.rating === "helpful"}
            onClick={() => void submit("helpful", null)}
          >
            {mutation.isPending && retryPayload?.rating === "helpful" ? null : (
              <ThumbsUp size={17} aria-hidden />
            )}
          </Button>
          <Button
            type="button"
            variant={current?.rating === "not_helpful" ? "secondary" : "ghost"}
            size="sm"
            className={cn(
              "size-11 min-h-11 min-w-11 touch-manipulation px-0",
              current?.rating === "not_helpful" && "text-danger"
            )}
            aria-label={notHelpfulLabel}
            aria-pressed={current?.rating === "not_helpful"}
            aria-expanded={showReasons}
            title={notHelpfulLabel}
            disabled={disabled}
            onClick={() => setShowReasons((open) => !open)}
          >
            <ThumbsDown size={17} aria-hidden />
          </Button>
        </div>
        {current && !compact ? (
          <span className="inline-flex items-center gap-1 text-xs text-muted" role="status">
            <CheckCircle2 size={14} className="text-success" aria-hidden />
            {t("feedback.controls.savedInline")}
          </span>
        ) : null}
      </div>

      {showReasons ? (
        <fieldset className="mt-3 rounded-md border border-border bg-background p-3">
          <legend className="px-1 text-xs font-medium text-foreground">
            {t("feedback.controls.reasonLegend")}
          </legend>
          <div className="flex flex-wrap gap-2">
            {reasons.map((reason) => (
              <Button
                key={reason}
                type="button"
                size="sm"
                variant="secondary"
                className={cn(
                  "min-h-11 touch-manipulation whitespace-normal text-left",
                  current?.reason === reason && "border-primary bg-primary/10"
                )}
                aria-pressed={current?.reason === reason}
                loading={mutation.isPending && retryPayload?.reason === reason}
                disabled={mutation.isPending}
                onClick={() => void submit("not_helpful", reason)}
              >
                {t(REASON_LABEL_KEYS[reason])}
              </Button>
            ))}
          </div>
        </fieldset>
      ) : null}

      {error ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-destructive" role="alert">
          <span>{error}</span>
          {retryPayload ? (
            <button
              type="button"
              className="min-h-11 rounded-md px-2 font-medium underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              onClick={() => void submit(retryPayload.rating, retryPayload.reason ?? null)}
            >
              {t("common.retry")}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
