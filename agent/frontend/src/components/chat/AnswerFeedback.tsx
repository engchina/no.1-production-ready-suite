import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Save, ThumbsDown, ThumbsUp } from "lucide-react";
import { Button, FieldLegend, TextareaField, ToggleChip, toast } from "@engchina/production-ready-ui";

import {
  agentApi,
  FEEDBACK_REASONS,
  type FeedbackRating,
  type FeedbackReason,
  type RunFeedback,
  type RunFeedbackPayload,
  type RunState,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

export const FEEDBACK_COMMENT_MAX_CHARS = 1000;

/**
 * 回答への評価（#774）。RAG の回答の評価（`FeedbackControls`）と同じ形:
 * 👍 はすぐ保存、👎 は理由（必須）とコメント（任意）をその場で開いて保存する。付け直すと上書きする。
 *
 * - `owner`: 会話をした本人の評価（チャットの回答の下）。
 * - `admin`: 管理者の評価（Agent 管理の権限。フィードバック・Run の詳細。本人の評価とは別に残す）。
 */
export function AnswerFeedback({
  runId,
  current,
  mode = "owner",
  onSaved,
}: {
  runId: string;
  current: RunFeedback | null;
  mode?: "owner" | "admin";
  onSaved: (run: RunState) => void;
}) {
  const admin = mode === "admin";
  const [showReasons, setShowReasons] = useState(false);
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  const [comment, setComment] = useState("");
  const [retryPayload, setRetryPayload] = useState<RunFeedbackPayload | null>(null);
  const save = useMutation({
    mutationFn: (payload: RunFeedbackPayload) =>
      admin ? agentApi.putRunAdminReview(runId, payload) : agentApi.putRunFeedback(runId, payload),
    onSuccess: (updated) => {
      setShowReasons(false);
      setRetryPayload(null);
      onSaved(updated);
      toast.success(t("chat.feedback.saved"));
    },
  });
  const label = admin ? t("chat.feedback.adminQuestion") : t("chat.feedback.question");

  function submit(payload: RunFeedbackPayload) {
    // 同じ評価を送り直さない（理由・コメントも同じなら閉じるだけ）。
    if (
      current?.rating === payload.rating &&
      (current.reason ?? null) === (payload.reason ?? null) &&
      current.comment === (payload.comment ?? "")
    ) {
      setShowReasons(false);
      return;
    }
    setRetryPayload(payload);
    save.mutate(payload);
  }

  function toggleReasons() {
    setReason(current?.reason ?? null);
    setComment(current?.comment ?? "");
    setShowReasons((open) => !open);
  }

  const rated = (rating: FeedbackRating) => current?.rating === rating;
  return (
    <div className="min-w-0 border-t border-border pt-3" data-testid={admin ? `admin-review-${runId}` : `chat-feedback-${runId}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-sm font-medium text-fg">{label}</span>
        <div className="flex gap-1" role="group" aria-label={label}>
          <Button
            type="button"
            variant={rated("helpful") ? "secondary" : "ghost"}
            size="sm"
            iconOnly
            icon={ThumbsUp}
            className={rated("helpful") ? "text-success-fg" : undefined}
            aria-label={t("chat.feedback.helpful")}
            aria-pressed={rated("helpful")}
            disabled={save.isPending}
            loading={save.isPending && retryPayload?.rating === "helpful"}
            onClick={() => submit({ rating: "helpful" })}
          />
          <Button
            type="button"
            variant={rated("not_helpful") ? "secondary" : "ghost"}
            size="sm"
            iconOnly
            icon={ThumbsDown}
            className={rated("not_helpful") ? "text-danger-fg" : undefined}
            aria-label={t("chat.feedback.notHelpful")}
            aria-pressed={rated("not_helpful")}
            aria-expanded={showReasons}
            disabled={save.isPending}
            onClick={toggleReasons}
          />
        </div>
        {current ? (
          <span className="inline-flex items-center gap-1 text-xs text-fg-muted" role="status">
            <CheckCircle2 size={14} className="text-success-fg" aria-hidden />
            {t("chat.feedback.savedInline")}
          </span>
        ) : null}
      </div>

      {showReasons ? (
        <fieldset className="mt-3 rounded-md border border-border bg-surface-sunken p-3">
          {/* 役に立たなかった理由は必須（backend も必須）。コメントは任意なので何も付けない（#531）。 */}
          <FieldLegend required className="px-1 text-xs font-medium">
            {t("chat.feedback.reasonLegend")}
          </FieldLegend>
          <div className="flex flex-wrap gap-1" role="group" aria-label={t("chat.feedback.reasonLegend")}>
            {FEEDBACK_REASONS.map((item) => (
              <ToggleChip
                key={item}
                selected={reason === item}
                disabled={save.isPending}
                onClick={() => setReason(item)}
              >
                {t(`feedback.reason.${item}` as I18nKey)}
              </ToggleChip>
            ))}
          </div>
          <TextareaField
            id={`${admin ? "admin-review" : "chat-feedback"}-comment-${runId}`}
            label={t("chat.feedback.commentLabel")}
            className="mt-3"
            value={comment}
            maxLength={FEEDBACK_COMMENT_MAX_CHARS}
            rows={3}
            disabled={save.isPending}
            placeholder={t("chat.feedback.commentPlaceholder")}
            showCount={(count) => t("chat.feedback.commentCount", { count, max: FEEDBACK_COMMENT_MAX_CHARS })}
            onChange={(event) => setComment(event.target.value)}
          />
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <Button
              type="button"
              icon={Save}
              loading={save.isPending && retryPayload?.rating === "not_helpful"}
              disabled={!reason}
              onClick={() =>
                reason ? submit({ rating: "not_helpful", reason, comment: comment.trim() }) : undefined
              }
            >
              {t("chat.feedback.save")}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setShowReasons(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </fieldset>
      ) : null}

      {save.error ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-danger-fg" role="alert">
          <span>{save.error.message || t("chat.feedback.saveFailed")}</span>
          {retryPayload ? (
            <Button type="button" size="sm" variant="secondary" onClick={() => save.mutate(retryPayload)}>
              {t("common.retry")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
