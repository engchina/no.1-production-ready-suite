import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormActionBar,
  FormStatus,
  SelectField,
  type SelectFieldOption,
  Skeleton,
  Switch,
} from "@engchina/production-ready-ui";
import { MessageSquareText, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import {
  ApiError,
  type AnsweringSettingsData,
  type AnswerFlowName,
  type QueryStrategyName,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useAnsweringSettings, useUpdateAnsweringSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";

// 選択肢の名前は検索・回答プロファイルの上書きの欄と同じ文言を使う（backend の QueryStrategy と同じ順）。
const QUERY_STRATEGY_OPTIONS: SelectFieldOption<QueryStrategyName>[] = (
  [
    "auto_routing",
    "simple_retrieval",
    "rag_fusion",
    "query_decomposition",
    "step_back_prompting",
    "hyde",
  ] as const
).map((value) => ({ value, label: t(`searchAnswerProfiles.queryStrategy.${value}`) }));
const ANSWER_FLOW_OPTIONS: SelectFieldOption<AnswerFlowName>[] = [
  { value: "crag", label: t("searchAnswerProfiles.answerFlow.crag") },
  { value: "standard_rag", label: t("searchAnswerProfiles.answerFlow.standard_rag") },
];
// 0〜20（backend の rag_neighbor_child_count と同じ範囲）。SelectField は文字列値を扱う。
const NEIGHBOR_OPTIONS: SelectFieldOption<string>[] = Array.from({ length: 21 }, (_, n) => ({
  value: String(n),
  label: String(n),
}));

type AnsweringDraft = Omit<AnsweringSettingsData, "config_source">;

function formFromSettings(settings: AnsweringSettingsData): AnsweringDraft {
  return {
    query_strategy: settings.query_strategy,
    answer_flow: settings.answer_flow,
    neighbor_child_count: settings.neighbor_child_count,
    rerank_enabled: settings.rerank_enabled,
    screen_linking_enabled: settings.screen_linking_enabled,
    request_coverage_retrieval_enabled: settings.request_coverage_retrieval_enabled,
    auto_field_filter_enabled: settings.auto_field_filter_enabled,
  };
}

/**
 * 回答の検索と生成の全体既定（質問の拡張・回答の生成方式・根拠の前後の数・rerank・画面目録。#593）。
 * 検索・回答プロファイルの「検索・回答設定」で上書きできる。検索・回答プロファイルを指定しない呼び出し(MCP など)もこの値を使う。
 */
export function AnsweringSettingsCard() {
  const query = useAnsweringSettings();
  return (
    <Card data-testid="answering-settings-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquareText size={16} className="text-accent-fg" aria-hidden />
          {t("settings.answering.title")}
        </CardTitle>
        <CardDescription>{t("settings.answering.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? <Skeleton className="h-48 w-full" /> : null}
        {query.isError ? (
          <ApiErrorState
            error={query.error}
            fallback={t("settings.answering.loadError")}
            onRetry={() => void query.refetch()}
          />
        ) : null}
        {/* 保存値が変わったら編集欄を作り直す（保存後に保存値へ揃える）。 */}
        {query.data ? <AnsweringForm key={JSON.stringify(query.data)} saved={query.data} /> : null}
      </CardContent>
    </Card>
  );
}

function AnsweringForm({ saved }: { saved: AnsweringSettingsData }) {
  const save = useUpdateAnsweringSettings();
  const [form, setForm] = useState<AnsweringDraft>(() => formFromSettings(saved));
  const dirty = JSON.stringify(form) !== JSON.stringify(formFromSettings(saved));
  // 保存中は離脱を止め、欄も変えられなくする（保存に成功すると保存値で作り直すため、保存中の変更が消える。#1002）。
  useLeaveGuard(dirty, save.isPending);
  const disabled = save.isPending;

  function update(patch: Partial<AnsweringDraft>) {
    save.reset();
    setForm((current) => ({ ...current, ...patch }));
  }

  function resetForm() {
    save.reset();
    setForm(formFromSettings(saved));
  }

  function submit() {
    if (save.isPending) return;
    // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
    save.mutate(form, { onSuccess: () => toast.success(t("settings.answering.saved")) });
  }

  return (
    <div className="space-y-4">
      {/* 長い選択肢の名前で 375px の横幅を押し広げないよう、各欄に min-w-0 を付ける。 */}
      <div className="grid gap-3 md:grid-cols-3">
        <SelectField
          id="answering-query-strategy"
          className="min-w-0"
          disabled={disabled}
          label={t("settings.answering.queryStrategy")}
          helper={t("settings.answering.queryStrategyHint")}
          value={form.query_strategy}
          options={QUERY_STRATEGY_OPTIONS}
          onValueChange={(value) => update({ query_strategy: value })}
        />
        <SelectField
          id="answering-answer-flow"
          className="min-w-0"
          disabled={disabled}
          label={t("settings.answering.answerFlow")}
          helper={t("settings.answering.answerFlowHint")}
          value={form.answer_flow}
          options={ANSWER_FLOW_OPTIONS}
          onValueChange={(value) => update({ answer_flow: value })}
        />
        <SelectField
          id="answering-neighbor-child-count"
          className="min-w-0"
          disabled={disabled}
          label={t("settings.answering.neighborChildCount")}
          helper={t("settings.answering.neighborChildCountHint")}
          value={String(form.neighbor_child_count)}
          options={NEIGHBOR_OPTIONS}
          onValueChange={(value) => update({ neighbor_child_count: Number(value) })}
        />
      </div>
      <div
        role="group"
        aria-label={t("settings.answering.options")}
        className="divide-y divide-border rounded-md border border-border"
      >
        <SwitchRow
          id="answering-rerank"
          label={t("settings.answering.rerank")}
          description={t("settings.answering.rerankHint")}
          checked={form.rerank_enabled}
          disabled={disabled}
          onChange={(checked) => update({ rerank_enabled: checked })}
        />
        <SwitchRow
          id="answering-screen-linking"
          label={t("settings.answering.screenLinking")}
          description={t("settings.answering.screenLinkingHint")}
          checked={form.screen_linking_enabled}
          disabled={disabled}
          onChange={(checked) => update({ screen_linking_enabled: checked })}
        />
        <SwitchRow
          id="answering-request-coverage"
          label={t("settings.answering.requestCoverage")}
          description={t("settings.answering.requestCoverageHint")}
          checked={form.request_coverage_retrieval_enabled}
          disabled={disabled}
          onChange={(checked) => update({ request_coverage_retrieval_enabled: checked })}
        />
        <SwitchRow
          id="answering-auto-field-filter"
          label={t("settings.answering.autoFieldFilter")}
          description={t("settings.answering.autoFieldFilterHint")}
          checked={form.auto_field_filter_enabled}
          disabled={disabled}
          onChange={(checked) => update({ auto_field_filter_enabled: checked })}
        />
      </div>
      <FormActionBar
        ariaLabel={t("settings.answering.actions.label")}
        primaryActions={[
          {
            id: "save",
            label: t("settings.answering.save"),
            icon: Save,
            loading: save.isPending,
            disabled: !dirty,
            onClick: submit,
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
                save.error instanceof ApiError ? save.error.message : t("settings.answering.saveError")
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

function SwitchRow({
  id,
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const descriptionId = `${id}-description`;
  return (
    <div className="flex items-start justify-between gap-4 px-3 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium text-fg">{label}</div>
        <p id={descriptionId} className="mt-0.5 text-xs leading-relaxed text-fg-muted">
          {description}
        </p>
      </div>
      <Switch
        checked={checked}
        disabled={disabled}
        aria-label={label}
        aria-describedby={descriptionId}
        onCheckedChange={onChange}
        className="mt-0.5 shrink-0"
      />
    </div>
  );
}
