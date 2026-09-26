import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormStatus,
  SelectField,
  type SelectFieldOption,
  Skeleton,
  Switch,
  TextField,
} from "@engchina/production-ready-ui";
import { History, Save } from "lucide-react";
import { useState } from "react";

import { ApiError, type QueryHistorySettingsData } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useQueryHistorySettings, useUpdateQueryHistorySettings } from "@/lib/queries";
import { toast } from "@/lib/toast";

const RETENTION_OPTIONS: SelectFieldOption<string>[] = [
  ...[30, 90, 180, 365].map((days) => ({
    value: String(days),
    label: t("settings.answerRecords.days", { days }),
  })),
  { value: "0", label: t("settings.answerRecords.unlimited") },
];

/**
 * 質問履歴（rag_poc の QUERY_HISTORY_*）。全体設定で、既定は無効（質問の本文を保存するため）。
 * 有効にすると、回答に成功した質問を業務ビュー単位で保存し、RAG 検索に「よく聞かれている質問」を出す。
 */
export function QueryHistorySettingsCard() {
  const query = useQueryHistorySettings();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History size={16} className="text-accent-fg" aria-hidden />
          {t("settings.queryHistory.title")}
        </CardTitle>
        <CardDescription>{t("settings.queryHistory.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? <Skeleton className="h-24 w-full" /> : null}
        {query.isError ? (
          <FormStatus tone="danger" message={t("settings.queryHistory.loadError")} />
        ) : null}
        {/* 保存値が変わったら編集欄を作り直す（保存後に保存値へ揃える）。 */}
        {query.data ? <QueryHistoryForm key={JSON.stringify(query.data)} saved={query.data} /> : null}
      </CardContent>
    </Card>
  );
}

function QueryHistoryForm({ saved }: { saved: QueryHistorySettingsData }) {
  const save = useUpdateQueryHistorySettings();
  const [form, setForm] = useState(saved);
  const [blocklistText, setBlocklistText] = useState(saved.blocklist.join("\n"));
  const next: QueryHistorySettingsData = {
    ...form,
    blocklist: blocklistText.split("\n").map((item) => item.trim()).filter(Boolean),
  };
  const dirty = JSON.stringify(next) !== JSON.stringify(saved);
  useLeaveGuard(dirty);
  const validNumbers = next.min_count >= 1 && next.suggestion_limit >= 1 && next.suggestion_limit <= 20;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 rounded-md border border-border bg-surface p-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-fg">{t("settings.queryHistory.enabled")}</div>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">
            {t("settings.queryHistory.enabledHint")}
          </p>
        </div>
        <Switch
          checked={form.enabled}
          disabled={save.isPending}
          aria-label={t("settings.queryHistory.enabled")}
          onCheckedChange={(checked) => setForm((current) => ({ ...current, enabled: checked }))}
        />
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <SelectField
          id="query-history-retention"
          label={t("settings.queryHistory.retention")}
          value={String(form.retention_days)}
          options={RETENTION_OPTIONS}
          onValueChange={(value) =>
            setForm((current) => ({ ...current, retention_days: Number(value) }))
          }
        />
        <TextField
          id="query-history-min-count"
          type="number"
          min={1}
          max={1000}
          label={t("settings.queryHistory.minCount")}
          helper={t("settings.queryHistory.minCountHint")}
          value={String(form.min_count)}
          onValueChange={(value) => setForm((current) => ({ ...current, min_count: Number(value) }))}
        />
        <TextField
          id="query-history-limit"
          type="number"
          min={1}
          max={20}
          label={t("settings.queryHistory.limit")}
          value={String(form.suggestion_limit)}
          onValueChange={(value) =>
            setForm((current) => ({ ...current, suggestion_limit: Number(value) }))
          }
        />
      </div>
      <div>
        <label htmlFor="query-history-blocklist" className="text-sm font-medium text-fg">
          {t("settings.queryHistory.blocklist")}
        </label>
        <textarea
          id="query-history-blocklist"
          value={blocklistText}
          rows={3}
          disabled={save.isPending}
          aria-describedby="query-history-blocklist-hint"
          onChange={(event) => setBlocklistText(event.target.value)}
          className="mt-1 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm outline-none focus-visible:border-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
        />
        <p id="query-history-blocklist-hint" className="mt-1 text-xs text-fg-muted">
          {t("settings.queryHistory.blocklistHint")}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          size="lg"
          icon={Save}
          loading={save.isPending}
          disabled={!dirty || !validNumbers}
          onClick={() =>
            save.mutate(next, { onSuccess: () => toast.success(t("settings.queryHistory.saved")) })
          }
        >
          {t("settings.queryHistory.save")}
        </Button>
        {save.isError ? (
          <FormStatus
            tone="danger"
            message={
              save.error instanceof ApiError ? save.error.message : t("settings.queryHistory.saveError")
            }
          />
        ) : null}
      </div>
    </div>
  );
}
