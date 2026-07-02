import { CheckCircle2, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ToggleChip } from "@/components/ui/toggle-chip";
import {
  ApiError,
  type CitationFeedbackReason,
  type CitationFeedbackRating,
  type FeedbackContentSnapshot,
  type FeedbackRequestBody,
  type FeedbackSourceSurface,
  type FeedbackTargetType,
  type RetrievedChunk,
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

interface FeedbackControlsProps {
  traceId: string | null | undefined;
  businessViewId: string | null | undefined;
  targetType: FeedbackTargetType;
  sourceSurface: FeedbackSourceSurface;
  documentId?: string | null;
  chunkId?: string | null;
  messageId?: string | null;
  contentSnapshot?: FeedbackContentSnapshot | null;
  compact?: boolean;
}

export function FeedbackControls({
  traceId,
  businessViewId,
  targetType,
  sourceSurface,
  documentId = null,
  chunkId = null,
  messageId = null,
  contentSnapshot = null,
  compact = false,
}: FeedbackControlsProps) {
  const currentQuery = useCurrentFeedback(traceId ?? null);
  const mutation = useSubmitFeedback();
  const [showReasons, setShowReasons] = useState(false);
  const [selectedReason, setSelectedReason] = useState<CitationFeedbackReason | null>(null);
  const [comment, setComment] = useState("");
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

  function handleOpenReasons() {
    setError("");
    setSelectedReason(current?.reason ?? null);
    setComment(current?.comment ?? "");
    setShowReasons((open) => !open);
  }

  async function submit(
    rating: CitationFeedbackRating,
    reason: CitationFeedbackReason | null,
    submittedComment: string | null
  ) {
    if (!traceId || !businessViewId) return;
    const normalizedComment = submittedComment?.trim() || null;
    if (
      current?.rating === rating &&
      (current.reason ?? null) === reason &&
      (current.comment ?? null) === normalizedComment
    ) {
      setShowReasons(false);
      return;
    }
    const payload = buildFeedbackPayload({
      trace_id: traceId,
      business_view_id: businessViewId,
      target_type: targetType,
      source_surface: sourceSurface,
      document_id: targetType === "citation" ? documentId : null,
      chunk_id: targetType === "citation" ? chunkId : null,
      message_id: messageId,
      content_snapshot: messageId ? null : contentSnapshot,
      rating,
      reason,
      comment: rating === "not_helpful" ? normalizedComment : null,
    });
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
      <div className={cn("flex gap-2", compact ? "items-center justify-end" : "flex-wrap items-center")}>
        <span className={compact ? "sr-only" : "mr-1 text-sm font-medium text-foreground"}>
          {label}
        </span>
        <div className="flex gap-1" role="group" aria-label={label}>
          <Button
            type="button"
            variant={current?.rating === "helpful" ? "secondary" : "ghost"}
            size="sm"
            className={cn("min-w-8 px-2", current?.rating === "helpful" && "text-success")}
            aria-label={helpfulLabel}
            aria-pressed={current?.rating === "helpful"}
            title={helpfulLabel}
            disabled={disabled}
            loading={mutation.isPending && retryPayload?.rating === "helpful"}
            onClick={() => void submit("helpful", null, null)}
          >
            <ThumbsUp size={14} aria-hidden />
          </Button>
          <Button
            type="button"
            variant={current?.rating === "not_helpful" ? "secondary" : "ghost"}
            size="sm"
            className={cn("min-w-8 px-2", current?.rating === "not_helpful" && "text-danger")}
            aria-label={notHelpfulLabel}
            aria-pressed={current?.rating === "not_helpful"}
            aria-expanded={showReasons}
            title={notHelpfulLabel}
            disabled={disabled}
            onClick={handleOpenReasons}
          >
            <ThumbsDown size={14} aria-hidden />
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
          <div className="flex flex-wrap gap-1" role="group" aria-label={t("feedback.controls.reasonLegend")}>
            {reasons.map((reason) => (
              <ToggleChip
                key={reason}
                selected={selectedReason === reason}
                disabled={mutation.isPending}
                onClick={() => setSelectedReason(reason)}
              >
                {t(REASON_LABEL_KEYS[reason])}
              </ToggleChip>
            ))}
          </div>
          <label className="mt-3 block text-xs font-medium text-foreground" htmlFor={`feedback-comment-${targetType}-${chunkId ?? "answer"}`}>
            {t("feedback.controls.commentLabel")}
          </label>
          <textarea
            id={`feedback-comment-${targetType}-${chunkId ?? "answer"}`}
            value={comment}
            maxLength={1000}
            rows={3}
            disabled={mutation.isPending}
            placeholder={t("feedback.controls.commentPlaceholder")}
            className="mt-1 w-full resize-y rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            onChange={(event) => setComment(event.target.value)}
          />
          <p className="mt-1 text-right text-xs tabular-nums text-muted">
            {t("feedback.controls.commentCount", { count: comment.length })}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <Button
              type="button"
              size="md"
              loading={mutation.isPending && retryPayload?.rating === "not_helpful"}
              disabled={!selectedReason}
              onClick={() => void submit("not_helpful", selectedReason, comment)}
            >
              {t("feedback.controls.save")}
            </Button>
            <Button type="button" variant="ghost" size="md" onClick={() => setShowReasons(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </fieldset>
      ) : null}

      {error ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-destructive" role="alert">
          <span>{error}</span>
          {retryPayload ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() =>
                void submit(
                  retryPayload.rating,
                  retryPayload.reason ?? null,
                  retryPayload.comment ?? null
                )
              }
            >
              {t("common.retry")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function buildFeedbackContentSnapshot(
  question: string,
  answer: string,
  citations: RetrievedChunk[]
): FeedbackContentSnapshot | null {
  const normalizedQuestion = question.trim();
  const normalizedAnswer = answer.trim();
  if (!normalizedQuestion || !normalizedAnswer) return null;
  return {
    question: normalizedQuestion,
    answer: normalizedAnswer,
    citations: citations.slice(0, 50).map((chunk) => ({
      document_id: chunk.document_id,
      chunk_id: chunk.chunk_id,
      file_name: chunk.file_name,
      section_title: metadataString(chunk.metadata.section_title),
      page_number: metadataInteger(chunk.metadata.page_number ?? chunk.metadata.page),
      content_preview: chunk.text.slice(0, 2000),
      rerank_score: chunk.rerank_score,
    })),
  };
}

export function buildFeedbackPayload(payload: FeedbackRequestBody): FeedbackRequestBody {
  if (payload.rating === "helpful") {
    return { ...payload, reason: null, comment: null };
  }
  return {
    ...payload,
    comment: payload.comment?.trim() || null,
  };
}

function metadataString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, 1000) : null;
}

function metadataInteger(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : null;
}
