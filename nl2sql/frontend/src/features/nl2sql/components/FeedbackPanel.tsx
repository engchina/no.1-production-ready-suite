import { useEffect, useState } from "react";
import { ThumbsDown, ThumbsUp, TriangleAlert, type LucideIcon } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle } from "@engchina/production-ready-ui";

import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import type { HistoryItem, Nl2SqlResult } from "../types";

type Rating = "good" | "bad" | "needs_review";

const FEEDBACK_OPTIONS: Array<{ rating: Rating; label: string; icon: LucideIcon }> = [
  { rating: "good", label: t("nl2sql.feedback.good"), icon: ThumbsUp },
  { rating: "needs_review", label: t("nl2sql.feedback.review"), icon: TriangleAlert },
  { rating: "bad", label: t("nl2sql.feedback.bad"), icon: ThumbsDown },
];

export function FeedbackPanel({
  result,
  history,
  onSaved,
}: {
  result: Nl2SqlResult | null;
  history: HistoryItem | null;
  onSaved: () => void | Promise<void>;
}) {
  const [comment, setComment] = useState("");
  const [savingRating, setSavingRating] = useState<Rating | null>(null);

  useEffect(() => {
    setComment(history?.feedback_comment ?? "");
  }, [history?.feedback_comment, history?.id]);

  if (!result || !history) return null;

  const save = async (rating: Rating) => {
    setSavingRating(rating);
    try {
      await apiPost("/api/nl2sql/feedback", { history_id: history.id, rating, comment: comment.trim() });
      await onSaved();
    } finally {
      setSavingRating(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("nl2sql.feedback.title")}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        <label className="grid gap-1 text-sm font-medium text-foreground">
          <span>{t("nl2sql.feedback.comment")}</span>
          <textarea
            value={comment}
            onChange={(event) => setComment(event.currentTarget.value)}
            rows={3}
            className="min-h-24 rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
            placeholder={t("nl2sql.feedback.commentPlaceholder")}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          {FEEDBACK_OPTIONS.map((option) => {
            const Icon = option.icon;
            return (
              <Button
                key={option.rating}
                type="button"
                variant={history.feedback_rating === option.rating ? "primary" : "secondary"}
                size="sm"
                loading={savingRating === option.rating}
                disabled={savingRating !== null}
                onClick={() => void save(option.rating)}
              >
                <Icon size={15} aria-hidden="true" />
                <span>{option.label}</span>
              </Button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
