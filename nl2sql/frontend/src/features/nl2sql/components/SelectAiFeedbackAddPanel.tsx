import { useEffect, useMemo, useState } from "react";
import { DatabaseZap, ThumbsDown, ThumbsUp } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, StatusBadge } from "@engchina/production-ready-ui";

import { FormStatus } from "@/components/ui/form-status";
import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import type {
  GeneratedSqlPanelData,
  HistoryItem,
  Nl2SqlResult,
  SelectAiFeedbackAddData,
} from "../types";

type MessageTone = "success" | "error";
type Rating = "good" | "bad";
type SelectAiFeedbackSource = (GeneratedSqlPanelData | Nl2SqlResult) & { original_question?: string };

export function SelectAiFeedbackAddPanel({
  result,
  history,
  selectedProfileId,
  questionText,
  onSaved,
}: {
  result: SelectAiFeedbackSource | null;
  history: HistoryItem | null;
  selectedProfileId: string;
  questionText?: string;
  onSaved: () => void | Promise<void>;
}) {
  const [response, setResponse] = useState("");
  const [feedbackContent, setFeedbackContent] = useState("");
  const [savingRating, setSavingRating] = useState<Rating | null>(null);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<MessageTone>("success");
  const [addData, setAddData] = useState<SelectAiFeedbackAddData | null>(null);

  const generatedSql = useMemo(
    () => (result?.executable_sql || result?.generated_sql || "").trim(),
    [result?.executable_sql, result?.generated_sql]
  );
  const question = history?.question || result?.original_question || questionText || "";
  const profileId = history?.profile_id || selectedProfileId || "default";

  useEffect(() => {
    setResponse(generatedSql);
    setFeedbackContent(history?.feedback_comment ?? "");
    setMessage("");
    setAddData(null);
  }, [generatedSql, result?.original_question, history?.feedback_comment, history?.id]);

  if (!result) return null;

  const submit = async (rating: Rating) => {
    const feedbackType = rating === "good" ? "positive" : "negative";
    const trimmedContent = feedbackContent.trim();
    // 良い=生成SQLを正しいものとして送る。違う=利用者が編集した修正SQLを送る。
    const effectiveResponse = rating === "good" ? generatedSql : response.trim();

    if (!question.trim()) return;
    if (rating === "bad") {
      if (!effectiveResponse) {
        setMessageTone("error");
        setMessage(t("nl2sql.selectAiFeedbackAdd.requiresResponse"));
        return;
      }
      if (!trimmedContent) {
        setMessageTone("error");
        setMessage(t("nl2sql.selectAiFeedbackAdd.requiresContent"));
        return;
      }
    }

    setSavingRating(rating);
    setMessage("");
    const warnings: string[] = [];
    let allOk = true;
    try {
      // 1) DBMS_CLOUD_AI feedback（バックエンドは常に NEGATIVE 登録）
      try {
        const data = await apiPost<SelectAiFeedbackAddData>("/api/nl2sql/select-ai/feedback/add", {
          profile_id: profileId,
          question,
          feedback_type: feedbackType,
          response: effectiveResponse,
          feedback_content: trimmedContent,
          generated_sql: generatedSql,
        });
        setAddData(data);
        if (!data.executed) allOk = false;
        warnings.push(...data.warnings);
      } catch (err) {
        allOk = false;
        warnings.push(err instanceof Error ? err.message : t("nl2sql.selectAiFeedbackAdd.failed"));
      }
      // 2) 結果フィードバック保存（履歴がある場合のみ。良い→good / 違う→bad）
      if (history) {
        try {
          await apiPost("/api/nl2sql/feedback", {
            history_id: history.id,
            rating,
            comment: trimmedContent,
          });
        } catch (err) {
          allOk = false;
          warnings.push(err instanceof Error ? err.message : t("nl2sql.selectAiFeedbackAdd.failed"));
        }
      }
      await onSaved();
      setMessageTone(allOk ? "success" : "error");
      setMessage(warnings.join(" ") || t("nl2sql.selectAiFeedbackAdd.saved"));
    } finally {
      setSavingRating(null);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-2">
          <CardTitle className="flex items-center gap-2">
            <DatabaseZap size={18} aria-hidden="true" />
            {t("nl2sql.selectAiFeedbackAdd.title")}
          </CardTitle>
          <p className="text-sm text-muted">{t("nl2sql.selectAiFeedbackAdd.description")}</p>
        </div>
        {addData && (
          <div className="flex flex-wrap justify-end gap-2">
            <StatusBadge variant={addData.executed ? "success" : "warning"} label={addData.status} />
            <StatusBadge variant="neutral" label={addData.runtime} />
          </div>
        )}
      </CardHeader>
      <CardContent className="grid gap-4">
        <label className="grid gap-1 text-sm font-medium text-foreground">
          <span>{t("nl2sql.selectAiFeedbackAdd.response")}</span>
          <textarea
            value={response}
            onChange={(event) => setResponse(event.currentTarget.value)}
            rows={5}
            className="min-h-32 rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
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
            tone={messageTone === "error" ? "danger" : "success"}
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
        {addData?.plsql_preview && (
          <label className="grid gap-1 text-sm font-medium text-foreground">
            <span>{t("nl2sql.selectAiFeedbackAdd.usedSql")}</span>
            <textarea
              value={addData.plsql_preview}
              readOnly
              rows={7}
              className="min-h-44 rounded-md border border-border bg-code px-3 py-2 font-mono text-sm leading-6 text-code-fg outline-none"
            />
          </label>
        )}
      </CardContent>
    </Card>
  );
}
