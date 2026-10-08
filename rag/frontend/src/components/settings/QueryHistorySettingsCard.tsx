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
  Switch,
  TextareaField,
  TextField,
  useConfirm,
} from "@production-ready/ui";
import { History, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError, type QueryHistorySettingsData } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useQueryHistorySettings, useUpdateQueryHistorySettings } from "@/lib/queries";
import { focusFirstInvalidField } from "@/lib/required-fields";
import {
  QUERY_HISTORY_FIELD_IDS,
  blocklistFromText,
  queryHistoryErrors,
  retentionOptions,
  shortensRetention,
} from "@/components/settings/retrieval-settings.logic";
import { toast } from "@/lib/toast";

/**
 * 質問履歴（rag_poc の QUERY_HISTORY_*）。全体設定で、既定は無効（質問の本文を保存するため）。
 * 有効にすると、回答に成功した質問を検索・回答プロファイル単位で保存し、RAG 検索に「よく聞かれている質問」を出す。
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
          <ApiErrorState
            error={query.error}
            fallback={t("settings.queryHistory.loadError")}
            onRetry={() => void query.refetch()}
          />
        ) : null}
        {/* 保存値が変わったら編集欄を作り直す（保存後に保存値へ揃える）。 */}
        {query.data ? <QueryHistoryForm key={JSON.stringify(query.data)} saved={query.data} /> : null}
      </CardContent>
    </Card>
  );
}

/** 編集中の値。数値の欄は空を 0 にしないよう文字列で持つ（#1002）。 */
type QueryHistoryDraft = {
  enabled: boolean;
  retentionDays: string;
  minCount: string;
  suggestionLimit: string;
  blocklistText: string;
};

function draftFromSettings(saved: QueryHistorySettingsData): QueryHistoryDraft {
  return {
    enabled: saved.enabled,
    retentionDays: String(saved.retention_days),
    minCount: String(saved.min_count),
    suggestionLimit: String(saved.suggestion_limit),
    blocklistText: saved.blocklist.join("\n"),
  };
}

function QueryHistoryForm({ saved }: { saved: QueryHistorySettingsData }) {
  const save = useUpdateQueryHistorySettings();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<QueryHistoryDraft>(() => draftFromSettings(saved));
  // 保存を押した後だけ欄のエラーを出す（直すと消える）。
  const [showErrors, setShowErrors] = useState(false);
  const errors = queryHistoryErrors(draft);
  const visibleErrors = showErrors ? errors : {};
  // 除外する語は保存する形（空白・空行・重複を除く）で比べる。
  const dirty =
    JSON.stringify({ ...draft, blocklistText: blocklistFromText(draft.blocklistText) }) !==
    JSON.stringify({ ...draftFromSettings(saved), blocklistText: saved.blocklist });
  useLeaveGuard(dirty, save.isPending);
  const disabled = save.isPending;

  function update(patch: Partial<QueryHistoryDraft>) {
    save.reset();
    setDraft((current) => ({ ...current, ...patch }));
  }

  function resetForm() {
    save.reset();
    setShowErrors(false);
    setDraft(draftFromSettings(saved));
  }

  async function submit() {
    if (save.isPending) return;
    setShowErrors(true);
    if (
      focusFirstInvalidField([
        [QUERY_HISTORY_FIELD_IDS.minCount, errors.minCount],
        [QUERY_HISTORY_FIELD_IDS.suggestionLimit, errors.suggestionLimit],
        [QUERY_HISTORY_FIELD_IDS.blocklist, errors.blocklist],
      ])
    ) {
      return;
    }
    const retentionDays = Number(draft.retentionDays);
    // 保存期間を短くすると、backend は保存の時に期限を過ぎた履歴を削除する（元に戻せない）。
    if (shortensRetention(saved.retention_days, retentionDays)) {
      const confirmed = await confirm({
        title: t("settings.queryHistory.shortenTitle"),
        description: t("settings.queryHistory.shortenDescription", { days: retentionDays }),
        confirmLabel: t("settings.queryHistory.shortenConfirm"),
        tone: "danger",
      });
      if (!confirmed) return;
    }
    save.mutate(
      {
        enabled: draft.enabled,
        retention_days: retentionDays,
        min_count: Number(draft.minCount),
        suggestion_limit: Number(draft.suggestionLimit),
        blocklist: blocklistFromText(draft.blocklistText),
      },
      { onSuccess: () => toast.success(t("settings.queryHistory.saved")) }
    );
  }

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
          checked={draft.enabled}
          disabled={disabled}
          aria-label={t("settings.queryHistory.enabled")}
          onCheckedChange={(checked) => update({ enabled: checked })}
        />
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <SelectField
          id="query-history-retention"
          className="min-w-0"
          label={t("settings.queryHistory.retention")}
          value={draft.retentionDays}
          options={retentionOptions(saved.retention_days)}
          disabled={disabled}
          onValueChange={(value) => update({ retentionDays: value })}
        />
        <TextField
          id={QUERY_HISTORY_FIELD_IDS.minCount}
          className="min-w-0"
          type="number"
          inputMode="numeric"
          min={1}
          max={1000}
          label={t("settings.queryHistory.minCount")}
          helper={t("settings.queryHistory.minCountHint")}
          error={visibleErrors.minCount ?? undefined}
          required
          disabled={disabled}
          value={draft.minCount}
          onValueChange={(value) => update({ minCount: value })}
        />
        <TextField
          id={QUERY_HISTORY_FIELD_IDS.suggestionLimit}
          className="min-w-0"
          type="number"
          inputMode="numeric"
          min={1}
          max={20}
          label={t("settings.queryHistory.limit")}
          error={visibleErrors.suggestionLimit ?? undefined}
          required
          disabled={disabled}
          value={draft.suggestionLimit}
          onValueChange={(value) => update({ suggestionLimit: value })}
        />
      </div>
      <TextareaField
        id={QUERY_HISTORY_FIELD_IDS.blocklist}
        label={t("settings.queryHistory.blocklist")}
        helper={t("settings.queryHistory.blocklistHint")}
        error={visibleErrors.blocklist ?? undefined}
        value={draft.blocklistText}
        rows={3}
        disabled={disabled}
        onChange={(event) => update({ blocklistText: event.target.value })}
      />
      <FormActionBar
        ariaLabel={t("settings.queryHistory.actions.label")}
        primaryActions={[
          {
            id: "save",
            label: t("settings.queryHistory.save"),
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
                  : t("settings.queryHistory.saveError")
              }
            />
          ) : dirty ? (
            <FormStatus tone="warning" message={t("settings.retrieval.actions.unsaved")} />
          ) : null
        }
      />
    </div>
  );
}
