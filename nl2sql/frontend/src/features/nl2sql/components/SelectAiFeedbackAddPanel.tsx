import { useEffect, useMemo, useState } from "react";
import { DatabaseZap, Send } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, StatusBadge } from "@engchina/production-ready-ui";

import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import type {
  GeneratedSqlPanelData,
  HistoryItem,
  Nl2SqlResult,
  SelectAiFeedbackAddData,
  SelectAiFeedbackAddType,
} from "../types";

type MessageTone = "success" | "error";
type SelectAiFeedbackSource = (GeneratedSqlPanelData | Nl2SqlResult) & { original_question?: string };

export function SelectAiFeedbackAddPanel({
  result,
  history,
  selectedProfileId,
  questionText,
}: {
  result: SelectAiFeedbackSource | null;
  history: HistoryItem | null;
  selectedProfileId: string;
  questionText?: string;
}) {
  const [feedbackType, setFeedbackType] = useState<SelectAiFeedbackAddType>("positive");
  const [response, setResponse] = useState("");
  const [feedbackContent, setFeedbackContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
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
    setFeedbackType("positive");
    setResponse(generatedSql);
    setFeedbackContent("");
    setMessage("");
    setAddData(null);
  }, [generatedSql, result?.original_question]);

  if (!result) return null;

  const responseReadOnly = feedbackType === "positive";
  const effectiveResponse = responseReadOnly ? generatedSql : response;
  const canSubmit = Boolean(question.trim() && effectiveResponse.trim() && !submitting);

  const updateFeedbackType = (nextType: SelectAiFeedbackAddType) => {
    setFeedbackType(nextType);
    if (nextType === "positive") setResponse(generatedSql);
    if (nextType === "negative" && !response.trim()) setResponse(generatedSql);
    setMessage("");
  };

  const submit = async () => {
    if (!question.trim()) return;
    if (!effectiveResponse.trim()) {
      setMessageTone("error");
      setMessage(t("nl2sql.selectAiFeedbackAdd.requiresResponse"));
      return;
    }
    setSubmitting(true);
    setMessage("");
    try {
      const data = await apiPost<SelectAiFeedbackAddData>("/api/nl2sql/select-ai/feedback/add", {
        profile_id: profileId,
        question,
        feedback_type: feedbackType,
        response: effectiveResponse,
        feedback_content: feedbackContent.trim(),
        generated_sql: generatedSql,
      });
      setAddData(data);
      setMessageTone(data.executed ? "success" : "error");
      setMessage(data.warnings.join(" ") || t("nl2sql.selectAiFeedbackAdd.saved"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("nl2sql.selectAiFeedbackAdd.failed"));
    } finally {
      setSubmitting(false);
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
          <p className="text-sm text-slate-600">{t("nl2sql.selectAiFeedbackAdd.description")}</p>
        </div>
        {addData && (
          <div className="flex flex-wrap justify-end gap-2">
            <StatusBadge variant={addData.executed ? "success" : "warning"} label={addData.status} />
            <StatusBadge variant="neutral" label={addData.runtime} />
          </div>
        )}
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-4 md:grid-cols-[12rem_1fr]">
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("nl2sql.selectAiFeedbackAdd.type")}</span>
            <select
              value={feedbackType}
              onChange={(event) => updateFeedbackType(event.currentTarget.value as SelectAiFeedbackAddType)}
              disabled={submitting}
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
            >
              <option value="positive">{t("nl2sql.selectAiFeedbackAdd.positive")}</option>
              <option value="negative">{t("nl2sql.selectAiFeedbackAdd.negative")}</option>
            </select>
          </label>
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("nl2sql.selectAiFeedbackAdd.response")}</span>
            <textarea
              value={effectiveResponse}
              onChange={(event) => setResponse(event.currentTarget.value)}
              readOnly={responseReadOnly}
              rows={5}
              className={`min-h-32 rounded-md border border-slate-300 px-3 py-2 font-mono text-xs leading-5 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200 ${
                responseReadOnly ? "bg-slate-50 text-slate-700" : "bg-white text-slate-950"
              }`}
              placeholder={t("nl2sql.selectAiFeedbackAdd.responsePlaceholder")}
            />
          </label>
        </div>
        <label className="grid gap-1 text-sm font-medium text-slate-800">
          <span>{t("nl2sql.selectAiFeedbackAdd.content")}</span>
          <textarea
            value={feedbackContent}
            onChange={(event) => setFeedbackContent(event.currentTarget.value)}
            rows={3}
            className="min-h-24 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
            placeholder={t("nl2sql.selectAiFeedbackAdd.contentPlaceholder")}
          />
        </label>
        <div className="flex flex-wrap items-center justify-between gap-3">
          {message && (
            <p
              role={messageTone === "error" ? "alert" : "status"}
              className={`text-sm ${messageTone === "error" ? "text-red-700" : "text-emerald-700"}`}
            >
              {message}
            </p>
          )}
          <Button
            type="button"
            variant="primary"
            size="sm"
            loading={submitting}
            disabled={!canSubmit}
            onClick={() => void submit()}
          >
            <Send size={15} aria-hidden="true" />
            <span>{t("nl2sql.selectAiFeedbackAdd.send")}</span>
          </Button>
        </div>
        {addData?.plsql_preview && (
          <label className="grid gap-1 text-sm font-medium text-slate-800">
            <span>{t("nl2sql.selectAiFeedbackAdd.usedSql")}</span>
            <textarea
              value={addData.plsql_preview}
              readOnly
              rows={7}
              className="min-h-44 rounded-md border border-slate-300 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-50 outline-none"
            />
          </label>
        )}
      </CardContent>
    </Card>
  );
}
