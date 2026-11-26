import type { ReactNode } from "react";
import { AnswerFileReview, AnswerFooter, type FileReviewState } from "@production-ready/ui";
import { agentApi, request, type RunState } from "@/lib/api";
import { answerEvidenceData } from "@/lib/answer-evidence";
import { t } from "@/lib/i18n";
import { AnswerEvidenceBlock } from "./AnswerEvidenceView";
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
      original={(task, documentId) =>
        request<Record<string, string>>(path + "/original?" + new URLSearchParams({ task, document_id: documentId }))
      }
      change={(action) => request<FileReviewState>(path, { method: "POST", body: JSON.stringify(action) })}
      vote={async (id, rating) => onSaved(await agentApi.putRunFeedback(run.id, { rating }, id))}
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
