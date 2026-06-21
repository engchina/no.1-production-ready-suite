import { useEffect, useState } from "react";
import { Columns3, Database, MessageSquareText, RefreshCw, RotateCcw, Rows3, ShieldCheck, type LucideIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet } from "@/lib/api";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { engineLabel } from "../labels";
import { historyRerunUrl } from "../queryPrefillState";
import { formatElapsed } from "../useOperationTimer";
import type { HistoryData, HistoryItem } from "../types";

function feedbackLabel(item: HistoryItem) {
  if (item.feedback_rating === "good") return t("nl2sql.feedback.good");
  if (item.feedback_rating === "bad") return t("nl2sql.feedback.bad");
  if (item.feedback_rating === "needs_review") return t("nl2sql.feedback.review");
  return t("history.feedback.none");
}

function columnsLabel(item: HistoryItem) {
  if (item.result_columns.length === 0) return "-";
  return item.result_columns.join(", ");
}

export function HistoryPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const load = async () => {
    setLoading(true);
    setMessage("");
    try {
      const data = await apiGet<HistoryData>("/api/nl2sql/history");
      setItems(data.items);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("history.error.load"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    <>
      <PageHeader
        title={t("nav.history")}
        subtitle={t("history.subtitle")}
        actions={
          <Button type="button" variant="secondary" size="sm" loading={loading} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("history.action.refresh")}</span>
          </Button>
        }
      />
      <main className="grid gap-4 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}
        {items.length === 0 ? (
          <EmptyState title={t("history.empty.title")} hint={t("history.empty.hint")} />
        ) : (
          items.map((item) => (
            <Card key={item.id}>
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <CardTitle>{item.question}</CardTitle>
                    <p className="mt-1 text-xs text-slate-500">{new Date(item.created_at).toLocaleString("ja-JP")}</p>
                    {item.rewritten_question && item.rewritten_question !== item.question && (
                      <p className="mt-2 text-sm text-slate-600">
                        <span className="font-medium">{t("history.rewritten")}:</span>{" "}
                        {item.rewritten_question}
                      </p>
                    )}
                  </div>
                  <div className="grid justify-items-end gap-2">
                    <div className="flex flex-wrap justify-end gap-2">
                      <StatusBadge variant="info" label={engineLabel(item.engine)} />
                      <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
                      <StatusBadge variant={item.feedback_rating ? "success" : "neutral"} label={feedbackLabel(item)} />
                      <StatusBadge
                        variant={item.safety_is_safe ? "success" : "danger"}
                        label={item.safety_is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => navigate(historyRerunUrl(item, APP_ROUTES.query))}
                    >
                      <RotateCcw size={15} aria-hidden="true" />
                      <span>{t("history.action.rerun")}</span>
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="grid gap-3 md:grid-cols-3">
                  <HistoryFact
                    icon={Database}
                    label={t("history.profile")}
                    value={item.profile_name || item.profile_id || "-"}
                  />
                  <HistoryFact
                    icon={Rows3}
                    label={t("history.rows")}
                    value={String(item.result_row_count)}
                  />
                  <HistoryFact
                    icon={Columns3}
                    label={t("history.columns")}
                    value={String(item.result_columns.length)}
                  />
                </div>
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <ShieldCheck size={15} aria-hidden="true" />
                    <span>{t("history.sql")}</span>
                  </div>
                  <pre className="overflow-auto rounded-md border border-slate-200 bg-slate-950 p-4 text-sm text-slate-50">
                    <code>{item.executable_sql || item.generated_sql}</code>
                  </pre>
                </div>
                <div className="rounded-md border border-slate-200 bg-white p-3">
                  <p className="text-xs font-medium text-slate-500">{t("history.resultColumns")}</p>
                  <p className="mt-1 break-words font-mono text-xs text-slate-700">{columnsLabel(item)}</p>
                </div>
                {item.feedback_comment && (
                  <div className="flex gap-2 rounded-md border border-sky-100 bg-sky-50 p-3 text-sm text-slate-800">
                    <MessageSquareText size={16} className="mt-0.5 text-sky-700" aria-hidden="true" />
                    <div>
                      <p className="font-medium text-slate-900">{t("history.feedbackComment")}</p>
                      <p className="mt-1">{item.feedback_comment}</p>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          ))
        )}
      </main>
    </>
  );
}

function HistoryFact({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
}) {
  return (
    <div className="flex min-w-0 items-start gap-2 rounded-md border border-slate-200 bg-white p-3">
      <Icon size={16} className="mt-0.5 shrink-0 text-slate-500" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-slate-500">{label}</p>
        <p className="mt-1 truncate text-sm font-semibold text-slate-900" title={value}>
          {value}
        </p>
      </div>
    </div>
  );
}
