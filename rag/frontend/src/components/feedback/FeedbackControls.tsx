import { AnswerFileReview, AnswerFooter, type FileReviewState } from "@production-ready/ui";
import {
  ApiError,
  request,
  ANSWER_GENERATION_TIMEOUT_MS,
  type FeedbackContentSnapshot,
  type FeedbackSourceSurface,
  type RetrievedChunk,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { DataLoadFailure, DataLoadStaleNotice } from "@/components/DataLoadStates";
import { useCurrentFeedback, useSubmitFeedback } from "@/lib/queries";
import { AnswerCitations } from "@/components/search/CitationList";
import { AnswerText } from "@/components/search/AnswerText";
import { toast } from "@/lib/toast";
import { FEEDBACK_ANSWER_REASONS, FEEDBACK_REASON_LABEL_KEYS } from "./FeedbackClient.logic";

interface FeedbackControlsProps {
  traceId: string | null | undefined;
  searchAnswerProfileId: string | null | undefined;
  sourceSurface: FeedbackSourceSurface;
  messageId?: string | null;
  contentSnapshot?: FeedbackContentSnapshot | null;
  readOnly?: boolean;
  /** 回答し直した版があるか。false なら、資料を選び直す面を開くまで以前の版を読まない（#5938）。既定は読む。 */
  hasFileReview?: boolean;
  onHistory: (rootId: string) => void;
}

export function FeedbackControls({
  traceId,
  searchAnswerProfileId,
  sourceSurface,
  messageId,
  contentSnapshot,
  readOnly,
  hasFileReview = true,
  onHistory,
}: FeedbackControlsProps) {
  const current = useCurrentFeedback(!readOnly && traceId ? traceId : null);
  const mutation = useSubmitFeedback();
  if (!traceId) return null;
  if (!readOnly && current.isError && current.data === undefined) {
    return (
      <DataLoadFailure
        error={current.error}
        fallback={t("feedback.controls.loadError")}
        onRetry={() => void current.refetch()}
        retrying={current.isFetching}
        testId="feedback-current-load-failed"
      />
    );
  }
  const path = "/api/search/answers/" + encodeURIComponent(traceId) + "/review";
  const load = () => request<FileReviewState>(path);
  return (
    <>
      {!readOnly && current.isError ? (
        <DataLoadStaleNotice
          error={current.error}
          dataUpdatedAt={current.dataUpdatedAt}
          onRetry={() => void current.refetch()}
          retrying={current.isFetching}
        />
      ) : null}
      <AnswerFileReview
        key={traceId}
        rootId={traceId}
        onHistory={onHistory}
        t={t}
        readOnly={readOnly}
        disabled={current.isError}
        loadingRating={!readOnly && current.isLoading}
        load={load}
        loadOnMount={hasFileReview}
        initialRating={current.data?.find((item) => item.target_type === "answer")?.rating}
        change={(action) =>
          request<FileReviewState>(
            path,
            { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(action) },
            { timeoutMs: ANSWER_GENERATION_TIMEOUT_MS },
          )
        }
        vote={async (id, rating) => {
          await mutation.mutateAsync({
            trace_id: id,
            search_answer_profile_id: searchAnswerProfileId ?? null,
            source_surface: sourceSurface,
            target_type: "answer",
            rating,
            message_id: id === traceId ? messageId : undefined,
            content_snapshot: id === traceId && sourceSurface === "search" ? contentSnapshot : undefined,
          });
        }}
        details={{
          reasons: FEEDBACK_ANSWER_REASONS.map((reason) => ({
            value: reason,
            label: t(FEEDBACK_REASON_LABEL_KEYS[reason]),
          })),
          correctedAnswer: true,
          labels: {
            reasonLegend: t("feedback.controls.reasonLegend"),
            commentLabel: t("feedback.controls.commentLabel"),
            commentPlaceholder: t("feedback.controls.commentPlaceholder"),
            commentCount: (count) => t("feedback.controls.commentCount", { count }),
            correctedAnswerLabel: t("feedback.controls.correctedAnswerLabel"),
            correctedAnswerPlaceholder: t("feedback.controls.correctedAnswerPlaceholder"),
            correctedAnswerHelp: t("feedback.controls.correctedAnswerHelp"),
            save: t("feedback.controls.save"),
            cancel: t("common.cancel"),
          },
          value: (id) => {
            const saved = current.data?.find((item) => item.target_type === "answer" && item.trace_id === id);
            return saved
              ? {
                  rating: saved.rating,
                  reason: saved.reason ?? null,
                  comment: saved.comment ?? null,
                  correctedAnswer: saved.corrected_answer ?? null,
                }
              : null;
          },
          save: async (id, submission) => {
            try {
              await mutation.mutateAsync({
                trace_id: id,
                search_answer_profile_id: searchAnswerProfileId ?? null,
                source_surface: sourceSurface,
                target_type: "answer",
                rating: "not_helpful",
                reason: submission.reason as (typeof FEEDBACK_ANSWER_REASONS)[number] | null,
                comment: submission.comment,
                corrected_answer: submission.correctedAnswer,
                message_id: id === traceId ? messageId : undefined,
                content_snapshot: id === traceId && sourceSurface === "search" ? contentSnapshot : undefined,
              });
            } catch (error) {
              throw new Error(error instanceof ApiError ? error.message : t("feedback.controls.saveError"), {
                cause: error,
              });
            }
            toast.success(t("feedback.controls.savedToast"));
          },
        }}
        renderVersion={(v) => {
          const hits = (v.sources.question?.citations ?? []) as RetrievedChunk[];
          return (
            <>
              <AnswerText text={v.answer} citations={hits} />
              <AnswerFooter
                notice={v.notice}
                noticeLabel={t("answer.notice.label")}
                evidence={<AnswerCitations citations={hits} idPrefix={"review-" + v.id} />}
              />
            </>
          );
        }}
      />
    </>
  );
}

export function buildFeedbackContentSnapshot(
  question: string,
  answer: string,
  citations: RetrievedChunk[],
): FeedbackContentSnapshot | null {
  const normalizedQuestion = question.trim();
  if (!normalizedQuestion || !answer.trim()) return null;
  return {
    question: normalizedQuestion,
    answer: answer.trim() || null,
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

function metadataString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, 1000) : null;
}

function metadataInteger(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : null;
}
