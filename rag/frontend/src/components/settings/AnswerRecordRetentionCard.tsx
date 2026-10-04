import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormActionBar,
  FormStatus,
  SelectField,
  Skeleton,
  useConfirm,
} from "@engchina/production-ready-ui";
import { Archive, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/StateViews";
import { retentionOptions, shortensRetention } from "@/components/settings/retrieval-settings.logic";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useAnswerRecordSettings, useUpdateAnswerRecordSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";

/**
 * 回答の記録の保持日数（全体設定）。回答スタイルの画面から「検索方法」の画面へ移した（#593）。
 */
export function AnswerRecordRetentionCard() {
  const query = useAnswerRecordSettings();
  const save = useUpdateAnswerRecordSettings();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<string | null>(null);
  const current = query.data ? String(query.data.retention_days) : null;
  const value = draft ?? current;
  const dirty = draft !== null && current !== null && draft !== current;
  useLeaveGuard(dirty, save.isPending);

  function resetForm() {
    save.reset();
    setDraft(null);
  }

  async function submit() {
    if (!dirty || value === null || query.data === undefined || save.isPending) return;
    const days = Number(value);
    // 保存期間を短くすると、backend は保存の時に期限を過ぎた回答の記録を削除する（元に戻せない。#1002）。
    if (shortensRetention(query.data.retention_days, days)) {
      const confirmed = await confirm({
        title: t("settings.answerRecords.shortenTitle"),
        description: t("settings.answerRecords.shortenDescription", { days }),
        confirmLabel: t("settings.answerRecords.shortenConfirm"),
        tone: "danger",
      });
      if (!confirmed) return;
    }
    save.mutate(days, {
      onSuccess: () => {
        setDraft(null);
        // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
        toast.success(t("settings.answerRecords.saved"));
      },
    });
  }

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
          <ErrorState
            message={t("settings.answerRecords.loadError")}
            onRetry={() => void query.refetch()}
          />
        ) : null}
        {value !== null && query.data ? (
          <>
            {/* 単独の選択欄なので幅は保持期間の値の長さ（#613）。 */}
            <SelectField
              id="answer-record-retention"
              label={t("settings.answerRecords.field")}
              value={value}
              options={retentionOptions(query.data.retention_days)}
              disabled={save.isPending}
              onValueChange={(next) => {
                if (!next) return;
                save.reset();
                setDraft(next);
              }}
              width="md"
            />
            <FormActionBar
              ariaLabel={t("settings.answerRecords.actions.label")}
              primaryActions={[
                {
                  id: "save",
                  label: t("settings.answerRecords.save"),
                  icon: Save,
                  loading: save.isPending,
                  disabled: !dirty,
                  onClick: () => void submit(),
                },
              ]}
              secondaryActions={[
                {
                  id: "reset",
                  label: t("settings.retrieval.actions.reset"),
                  icon: RotateCcw,
                  disabled: !dirty || save.isPending,
                  onClick: resetForm,
                },
              ]}
              status={
                save.isError ? (
                  <FormStatus
                    tone="danger"
                    message={
                      save.error instanceof ApiError
                        ? save.error.message
                        : t("settings.answerRecords.saveError")
                    }
                  />
                ) : dirty ? (
                  <FormStatus tone="warning" message={t("settings.retrieval.actions.unsaved")} />
                ) : null
              }
            />
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
