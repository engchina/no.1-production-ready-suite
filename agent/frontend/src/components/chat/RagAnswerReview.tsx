import type { ReactNode } from "react";
import { AnswerFileReview, AnswerFooter, apiErrorMessage, toast, type FileReviewState } from "@production-ready/ui";
import { agentApi, FEEDBACK_REASONS, request, type FeedbackReason, type RunState } from "@/lib/api";
import { answerEvidenceData } from "@/lib/answer-evidence";
import { t, type I18nKey } from "@/lib/i18n";
import { AnswerEvidenceBlock } from "./AnswerEvidenceView";
import { FEEDBACK_COMMENT_MAX_CHARS } from "./AnswerFeedback";
import { AnswerBody } from "./ResultTables";

export function hasRagAnswer(run: RunState) {
  return run.steps.some(
    (step) =>
      step.status === "completed" &&
      step.tool_result?.success &&
      !!step.tool_result.output?.trace_id &&
      ["rag__rag_chat", "rag__rag_search"].includes(step.tool_call?.name ?? ""),
  );
}

export function RagAnswerReview({
  run,
  readOnly,
  onSaved,
  onHistory,
  originalBody,
}: {
  run: RunState;
  readOnly: boolean;
  originalBody: ReactNode;
  onHistory: (id: string) => void;
  onSaved: (run: RunState) => void;
}) {
  const path = "/api/runs/" + encodeURIComponent(run.id) + "/review";
  return (
    <AnswerFileReview
      rootId={run.id}
      onHistory={onHistory}
      t={t}
      readOnly={readOnly}
      initialRating={run.feedback?.rating}
      load={() => request<FileReviewState>(path)}
      taskLabel={(task, question, number) => {
        const call = run.steps.find((step) => step.id === task)?.tool_call;
        const tool = t(call?.name === "rag__rag_chat" ? "chat.fileReview.chatTool" : "chat.fileReview.searchTool");
        return t("chat.fileReview.task", { number, tool, question });
      }}
      original={(task, documentId) =>
        request<Record<string, string>>(path + "/original?" + new URLSearchParams({ task, document_id: documentId }))
      }
      change={(action) => request<FileReviewState>(path, { method: "POST", body: JSON.stringify(action) })}
      vote={async (id, rating) => onSaved(await agentApi.putRunFeedback(run.id, { rating }, id))}
      details={{
        reasons: FEEDBACK_REASONS.map((reason) => ({
          value: reason,
          label: t(`feedback.reason.${reason}` as I18nKey),
        })),
        commentMaxLength: FEEDBACK_COMMENT_MAX_CHARS,
        labels: {
          reasonLegend: t("chat.fileReview.reasonLegend"),
          commentLabel: t("chat.feedback.commentLabel"),
          commentPlaceholder: t("chat.feedback.commentPlaceholder"),
          commentCount: (count, max) => t("chat.feedback.commentCount", { count, max }),
          save: t("chat.feedback.save"),
          cancel: t("common.cancel"),
        },
        value: (id) => (id === run.id ? run.feedback : null),
        save: async (id, submission) => {
          try {
            onSaved(
              await agentApi.putRunFeedback(
                run.id,
                {
                  rating: "not_helpful",
                  reason: (submission.reason as FeedbackReason | null) ?? undefined,
                  comment: submission.comment ?? "",
                },
                id,
              ),
            );
          } catch (error) {
            throw new Error(apiErrorMessage(error, t("chat.feedback.saveFailed")));
          }
          toast.success(t("chat.feedback.saved"));
        },
      }}
      renderVersion={(v) => {
        const items = Object.values(v.sources).flatMap((s) =>
          s.citations.map((hit) => ({
            ...hit,
            cited: hit.cited ?? hit.used_in_answer ?? false,
          })),
        );
        return (
          <>
            {v.id === run.id ? originalBody : <AnswerBody text={v.answer} />}
            <AnswerFooter
              notice={v.notice}
              noticeLabel={t("answer.notice.label")}
              evidence={
                <AnswerEvidenceBlock
                  runId={run.id}
                  evidence={answerEvidenceData({ items })}
                  idPrefix={"review-" + v.id}
                />
              }
            />
          </>
        );
      }}
    />
  );
}
