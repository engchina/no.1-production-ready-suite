import { useEffect, useMemo, useState } from "react";
import { MessageSquareText, ThumbsDown, ThumbsUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { FormStatus } from "@/components/ui/form-status";
import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import type {
  GeneratedSqlPanelData,
  HistoryItem,
  Nl2SqlResult,
  FeedbackData,
} from "../types";

type Rating = "good" | "bad";
type SelectAiFeedbackSource = (GeneratedSqlPanelData | Nl2SqlResult) & {
  original_question?: string;
  history_id?: string;
};

export function SelectAiFeedbackAddPanel({
  result,
  history,
  questionText,
  onSaved,
}: {
  result: SelectAiFeedbackSource | null;
  history: HistoryItem | null;
  questionText?: string;
  onSaved: () => void | Promise<void>;
}) {
  const [feedbackContent, setFeedbackContent] = useState("");
  const [savingRating, setSavingRating] = useState<Rating | null>(null);
  const [message, setMessage] = useState("");

  const generatedSql = useMemo(
    () => (result?.executable_sql || result?.generated_sql || "").trim(),
    [result?.executable_sql, result?.generated_sql]
  );
  const question = history?.question || result?.original_question || questionText || "";
  const historyId = history?.id || result?.history_id || "";

  useEffect(() => {
    setFeedbackContent(history?.feedback_comment ?? "");
    setMessage("");
  }, [generatedSql, result?.original_question, history?.feedback_comment, history?.id]);

  if (!result) return null;

  const submit = async (rating: Rating) => {
    const trimmedContent = feedbackContent.trim();

    if (!question.trim()) return;
    if (!historyId) {
      setMessage(t("nl2sql.selectAiFeedbackAdd.requiresHistory"));
      return;
    }
    if (rating === "bad" && !trimmedContent) {
      setMessage(t("nl2sql.selectAiFeedbackAdd.requiresContent"));
      return;
    }

    setSavingRating(rating);
    setMessage("");
    try {
      await apiPost<FeedbackData>("/api/nl2sql/feedback", {
        history_id: historyId,
        rating,
        feedback_content: trimmedContent,
        comment: trimmedContent,
      });
      await onSaved();
      toast.success(t("nl2sql.selectAiFeedbackAdd.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("nl2sql.selectAiFeedbackAdd.failed"));
    } finally {
      setSavingRating(null);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-2">
          <CardTitle className="flex items-center gap-2">
            <MessageSquareText size={18} aria-hidden="true" />
            {t("nl2sql.selectAiFeedbackAdd.title")}
          </CardTitle>
          <p className="text-sm text-muted">{t("nl2sql.selectAiFeedbackAdd.description")}</p>
        </div>
        {history?.feedback_rating && (
          <div className="flex flex-wrap justify-end gap-2">
            <StatusBadge
              variant="neutral"
              label={history.feedback_rating === "good" ? t("nl2sql.feedback.good") : t("nl2sql.feedback.bad")}
            />
          </div>
        )}
      </CardHeader>
      <CardContent className="grid gap-4">
        <label className="grid gap-1 text-sm font-medium text-foreground">
          <span>{t("nl2sql.selectAiFeedbackAdd.response")}</span>
          <textarea
            value={generatedSql}
            readOnly
            rows={5}
            className="min-h-32 rounded-md border border-border bg-code px-3 py-2 font-mono text-sm leading-6 text-code-fg outline-none"
            placeholder={t("nl2sql.selectAiFeedbackAdd.responsePlaceholder")}
          />
        </label>
        <label className="grid gap-1 text-sm font-medium text-foreground">
          <span>{t("nl2sql.selectAiFeedbackAdd.content")}</span>
          <textarea
            value={feedbackContent}
            onChange={(event) => setFeedbackContent(event.currentTarget.value)}
            rows={3}
            className="min-h-24 rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
            placeholder={t("nl2sql.selectAiFeedbackAdd.contentPlaceholder")}
          />
        </label>
        <div className="flex flex-wrap items-center justify-end gap-3">
          <FormStatus
            tone="danger"
            message={message}
            className="mr-auto"
          />
          <Button
            type="button"
            variant={history?.feedback_rating === "good" ? "primary" : "secondary"}
            size="sm"
            loading={savingRating === "good"}
            disabled={savingRating !== null}
            onClick={() => void submit("good")}
          >
            <ThumbsUp size={15} aria-hidden="true" />
            <span>{t("nl2sql.feedback.good")}</span>
          </Button>
          <Button
            type="button"
            variant={history?.feedback_rating === "bad" ? "primary" : "secondary"}
            size="sm"
            loading={savingRating === "bad"}
            disabled={savingRating !== null}
            onClick={() => void submit("bad")}
          >
            <ThumbsDown size={15} aria-hidden="true" />
            <span>{t("nl2sql.feedback.bad")}</span>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
