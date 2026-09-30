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
} from "@engchina/production-ready-ui";
import { Archive, Save } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useAnswerRecordSettings, useUpdateAnswerRecordSettings } from "@/lib/queries";

const RETENTION_OPTIONS: SelectFieldOption<string>[] = [
  ...[30, 90, 180, 365].map((days) => ({
    value: String(days),
    label: t("settings.answerRecords.days", { days }),
  })),
  { value: "0", label: t("settings.answerRecords.unlimited") },
];

/**
 * 回答の記録の保持日数（全体設定）。回答スタイルの画面から「検索方法」の画面へ移した（#593）。
 */
export function AnswerRecordRetentionCard() {
  const query = useAnswerRecordSettings();
  const save = useUpdateAnswerRecordSettings();
  const [draft, setDraft] = useState<string | null>(null);
  const current = query.data ? String(query.data.retention_days) : null;
  const value = draft ?? current;
  useLeaveGuard(draft !== null && current !== null && draft !== current);
  const options =
    current && !RETENTION_OPTIONS.some((item) => item.value === current)
      ? [...RETENTION_OPTIONS, { value: current, label: t("settings.answerRecords.days", { days: current }) }]
      : RETENTION_OPTIONS;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Archive size={16} className="text-accent-fg" aria-hidden />
          {t("settings.answerRecords.title")}
        </CardTitle>
        <CardDescription>{t("settings.answerRecords.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isPending ? <Skeleton className="h-10 w-full max-w-md" /> : null}
        {query.isError ? (
          <FormStatus tone="danger" message={t("settings.answerRecords.loadError")} />
        ) : null}
        {value !== null ? (
          <div className="flex flex-col gap-3 md:flex-row md:items-end">
            {/* 保存のボタン（lg）と同じ行なので、選択欄も lg。幅は保持期間の値の長さ（#613）。 */}
            <SelectField
              id="answer-record-retention"
              label={t("settings.answerRecords.field")}
              value={value}
              options={options}
              onValueChange={(next) => {
                if (!next) return;
                save.reset();
                setDraft(next);
              }}
              size="lg"
              width="md"
            />
            <Button
              type="button"
              size="lg"
              icon={Save}
              loading={save.isPending}
              disabled={value === current}
              onClick={() =>
                save.mutate(Number(value), { onSuccess: () => setDraft(null) })
              }
            >
              {t("settings.answerRecords.save")}
            </Button>
          </div>
        ) : null}
        {save.isSuccess ? (
          <FormStatus tone="success" message={t("settings.answerRecords.saved")} />
        ) : null}
        {save.isError ? (
          <FormStatus
            tone="danger"
            message={
              save.error instanceof ApiError
                ? save.error.message
                : t("settings.answerRecords.saveError")
            }
          />
        ) : null}
      </CardContent>
    </Card>
  );
}
