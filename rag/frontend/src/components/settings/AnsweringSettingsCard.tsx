import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormActionBar,
  FormGrid,
  FormStatus,
  SelectField,
  type SelectFieldOption,
  Skeleton,
  Switch,
  TimedLoadingState,
  TextField,
} from "@production-ready/ui";
import { MessageSquareText, RotateCcw, Save } from "lucide-react";
import { type ReactNode, useState } from "react";

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

// 選択肢の名前は `searchAnswerProfiles.queryStrategy.*` の文言を使う（backend の QueryStrategy と同じ順）。
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
// 既定（標準 RAG）を先頭に置く。CRAG は AI の呼び出しが増えるので、明示して選んだときだけ使う（#3509）。
const ANSWER_FLOW_OPTIONS: SelectFieldOption<AnswerFlowName>[] = [
  { value: "standard_rag", label: t("searchAnswerProfiles.answerFlow.standard_rag") },
  { value: "crag", label: t("searchAnswerProfiles.answerFlow.crag") },
];
const ANSWER_AUDIT_OPTIONS: SelectFieldOption<AnsweringSettingsData["answer_audit_mode"]>[] = (
  ["none", "standard", "detailed"] as const
).map((value) => ({ value, label: t(`settings.answering.auditMode.${value}`) }));
const EVIDENCE_REFINEMENT_OPTIONS: SelectFieldOption<AnsweringSettingsData["answer_evidence_refinement_mode"]>[] = (
  ["legacy", "cited_only", "cited_required"] as const
).map((value) => ({ value, label: t(`settings.answering.evidenceRefinement.${value}`) }));
// 0〜20（backend の rag_neighbor_child_count と同じ範囲）。SelectField は文字列値を扱う。
const NEIGHBOR_OPTIONS: SelectFieldOption<string>[] = Array.from({ length: 21 }, (_, n) => ({
  value: String(n),
  label: String(n),
}));

type AnsweringDraft = Omit<
  AnsweringSettingsData,
  "config_source" | "runtime_terms_answer_enabled" | "runtime_rules_answer_enabled" | "step_model_choices"
>;

function formFromSettings(settings: AnsweringSettingsData): AnsweringDraft {
  return {
    retrieval_query_limit: settings.retrieval_query_limit ?? 60,
    rerank_top_n: settings.rerank_top_n ?? 60,
    min_similarity: settings.min_similarity ?? 0.2,
    rerank_min_relevance_score: settings.rerank_min_relevance_score ?? 0.3,
    hybrid_search_backend: settings.hybrid_search_backend ?? "oracle_native",
    rrf_method_ratio: settings.rrf_method_ratio ?? [7, 3],
    rrf_source_ratio: settings.rrf_source_ratio ?? [7, 3],
    rerank_slot_ratio: settings.rerank_slot_ratio ?? [7, 3],
    runtime_terms_search_enabled: settings.runtime_terms_search_enabled ?? false,
    runtime_rules_confirmation_enabled: settings.runtime_rules_confirmation_enabled ?? false,
    query_strategy: settings.query_strategy,
    answer_flow: settings.answer_flow,
    answer_audit_mode: settings.answer_audit_mode,
    answer_evidence_refinement_mode: settings.answer_evidence_refinement_mode,
    neighbor_child_count: settings.neighbor_child_count,
    rerank_enabled: settings.rerank_enabled,
    screen_linking_enabled: settings.screen_linking_enabled,
    request_coverage_retrieval_enabled: settings.request_coverage_retrieval_enabled,
    auto_field_filter_enabled: settings.auto_field_filter_enabled,
    graph_expansion_enabled: settings.graph_expansion_enabled,
    entity_expansion_enabled: settings.entity_expansion_enabled,
    entity_expansion_max_chunks: settings.entity_expansion_max_chunks,
    section_expansion_enabled: settings.section_expansion_enabled,
    section_expansion_max_levels: settings.section_expansion_max_levels,
    section_expansion_input_token_percent: settings.section_expansion_input_token_percent,
    question_subject_role_model: settings.question_subject_role_model ?? "",
    row_condition_model: settings.row_condition_model ?? "",
    application_audit_model: settings.application_audit_model ?? "",
    application_reason_model: settings.application_reason_model ?? "",
  };
}

/** 段ごとのモデル（#4351・#4349・#4564・#4565）の項目。並びは画面の表示の順（回答の工程の順）。 */
const STEP_MODEL_FIELDS = [
  { key: "question_subject_role_model", id: "answering-question-subject-role-model", label: "settings.answering.questionSubjectRoleModel" },
  { key: "row_condition_model", id: "answering-row-condition-model", label: "settings.answering.rowConditionModel" },
  { key: "application_audit_model", id: "answering-application-audit-model", label: "settings.answering.applicationAuditModel" },
  { key: "application_reason_model", id: "answering-application-reason-model", label: "settings.answering.applicationReasonModel" },
] as const;

/** 段のモデルの選択肢: 回答のモデルと同じ（空）・登録モデル・登録が外れた保存値（#4351・#4349・#4564・#4565）。 */
function stepModelOptions(settings: AnsweringSettingsData, current: string): SelectFieldOption<string>[] {
  const choices = settings.step_model_choices ?? [];
  return [
    { value: "", label: t("settings.answering.stepModel.sameAsAnswer") },
    ...choices.map((choice) => ({ value: choice.model_id, label: choice.display_name || choice.model_id })),
    ...(current && !choices.some((choice) => choice.model_id === current)
      ? [{ value: current, label: t("settings.answering.stepModel.unavailable", { model: current }) }]
      : []),
  ];
}

/**
 * 回答の検索と生成の全体既定（質問の拡張・回答の生成方式・根拠の前後の数・rerank・画面目録。#593）。
 * 検索・回答プロファイルでは上書きしない（`PROFILE_GLOBAL_QUERY_FIELDS`）。検索・回答プロファイルを指定しない呼び出し(MCP など)もこの値を使う。
 */
/** 見出しと枠のある設定の群。枠の中の切り替えの一覧と同じ線と余白にそろえる（#4238）。 */
function SettingsGroup({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="divide-y divide-border rounded-md border border-border">
      <h3 id={id} className="px-3 py-3 text-sm font-medium text-fg">
        {title}
      </h3>
      <div className="space-y-4 p-3">{children}</div>
    </section>
  );
}

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
        {/* ほかの読み込み中と同じく、先頭に文言と経過時間の行、その下に内容の形の Skeleton（#2510）。 */}
        {query.isPending ? (
          <TimedLoadingState
            label={t("settings.answering.loading")}
            operationKey="answering-settings-load"
            testId="answering-settings-loading"
          >
            <Skeleton className="h-48 w-full" />
          </TimedLoadingState>
        ) : null}
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
  const invalidThreshold = [form.min_similarity, form.rerank_min_relevance_score].some(
    (value) => value == null || !Number.isFinite(value) || value < 0 || value > 1
  );
  const invalidCount = !Number.isInteger(form.retrieval_query_limit) || (form.retrieval_query_limit ?? 0) < 0 || (form.retrieval_query_limit ?? 0) > 2000
    || !Number.isInteger(form.rerank_top_n) || (form.rerank_top_n ?? -1) < 0 || (form.rerank_top_n ?? 0) > 2000;
  const invalidRatio = [form.rrf_method_ratio, form.rrf_source_ratio].some((ratio) => !ratio || ratio.some((v) => !Number.isFinite(v) || v < 0) || ratio[0] + ratio[1] <= 0);
  const slotRatio = form.rerank_slot_ratio;
  const invalidSlotRatio = !slotRatio || slotRatio.some((v) => !Number.isInteger(v) || v < 0) || slotRatio[0] + slotRatio[1] <= 0;

  function update(patch: Partial<AnsweringDraft>) {
    save.reset();
    setForm((current) => ({ ...current, ...patch }));
  }

  function resetForm() {
    save.reset();
    setForm(formFromSettings(saved));
  }

  function submit() {
    if (save.isPending || invalidRatio || invalidSlotRatio || invalidThreshold || invalidCount) return;
    // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
    save.mutate(form, { onSuccess: () => toast.success(t("settings.answering.saved")) });
  }

  return (
    <div className="space-y-4">
      {/* 長い選択肢の名前で 375px の横幅を押し広げないよう、各欄に min-w-0 を付ける。
          選択肢の名前が長い（20 字ほど）質問の拡張・回答の生成方式は lg、数は xs（#1891）。 */}
      <SettingsGroup id="answer-confirmation-title" title={t("settings.answering.confirmationTitle")}>
        <SwitchRow
          id="answering-runtime-rules-confirmation"
          label={t("settings.answering.runtimeRulesConfirmation")}
          description={t("settings.answering.runtimeRulesConfirmationHint")}
          checked={form.runtime_rules_confirmation_enabled ?? false}
          disabled={disabled}
          onChange={(checked) => update({ runtime_rules_confirmation_enabled: checked })}
        />
      </SettingsGroup>
      <SettingsGroup id="search-expression-title" title={t("settings.answering.searchExpressionTitle")}>
      <FormGrid>
        <SelectField
          id="answering-query-strategy"
          className="min-w-0"
          disabled={disabled}
          label={t("settings.answering.queryStrategy")}
          helper={t("settings.answering.queryStrategyHint")}
          value={form.query_strategy}
          options={QUERY_STRATEGY_OPTIONS}
          width="lg"
          onValueChange={(value) => update({ query_strategy: value })}
        />
        <SwitchRow
          id="answering-runtime-terms-search"
          label={t("settings.answering.runtimeTermsSearch")}
          description={t("settings.answering.runtimeTermsSearchHint")}
          checked={form.runtime_terms_search_enabled ?? false}
          disabled={disabled}
          onChange={(checked) => update({ runtime_terms_search_enabled: checked })}
        />
      </FormGrid>
      </SettingsGroup>
      <SettingsGroup id="search-execution-title" title={t("settings.answering.searchExecutionTitle")}>
      <FormGrid className="@[57.5rem]:grid-cols-[var(--field-width-lg)_minmax(0,1fr)_minmax(0,1fr)]">
        <SelectField id="answering-hybrid-backend" width="lg" disabled={disabled}
          label={t("settings.answering.hybridBackend")}
          helper={t("settings.answering.hybridBackendHint")}
          value={form.hybrid_search_backend ?? "oracle_native"}
          options={[{value:"oracle_native", label:t("settings.answering.hybridBackend.native")},
            {value:"sudachi", label:t("settings.answering.hybridBackend.sudachi")}]}
          onValueChange={(value: "oracle_native" | "sudachi") => update({hybrid_search_backend:value})} />
        <TextField
          id="answering-min-similarity" type="number" min={0} max={1} step="any" width="sm"
          label={t("settings.answering.minSimilarity")}
          helper={t("settings.answering.minSimilarityHint")}
          value={Number.isFinite(form.min_similarity) ? String(form.min_similarity) : ""}
          error={form.min_similarity == null || !Number.isFinite(form.min_similarity) || form.min_similarity < 0 || form.min_similarity > 1 ? t("settings.answering.thresholdInvalid") : undefined}
          disabled={disabled}
          onChange={(event) => update({ min_similarity: event.target.value === "" ? Number.NaN : Number(event.target.value) })}
        />
        <TextField
          id="answering-retrieval-query-limit" type="number" min={0} max={2000} step={1} width="sm"
          label={t("settings.answering.retrievalQueryLimit")}
          helper={t("settings.answering.retrievalQueryLimitHint")}
          value={Number.isFinite(form.retrieval_query_limit) ? String(form.retrieval_query_limit) : ""}
          error={!Number.isInteger(form.retrieval_query_limit) || (form.retrieval_query_limit ?? 0) < 0 || (form.retrieval_query_limit ?? 0) > 2000 ? t("settings.answering.retrievalQueryLimitInvalid") : undefined}
          disabled={disabled}
          onChange={(event) => update({ retrieval_query_limit: event.target.value === "" ? Number.NaN : Number(event.target.value) })}
        />
      </FormGrid>
      </SettingsGroup>
      <SettingsGroup id="rrf-title" title={t("settings.answering.rrfTitle")}>
        <p className="text-sm text-fg-muted">{t("settings.answering.rrfHint")}</p>
        <FormStatus tone="info" message={t("settings.answering.rrfOfficialDescription")} />
        <div className="space-y-2">
          <p>
            <a href="https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/search.html" target="_blank" rel="noopener noreferrer" className="text-sm text-accent-fg underline underline-offset-2">
              {t("settings.answering.rrfOfficialDefaults")}
            </a>
          </p>
          <p>
            <a href="https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/understand-hybrid-search.html" target="_blank" rel="noopener noreferrer" className="text-sm text-accent-fg underline underline-offset-2">
              {t("settings.answering.rrfOfficialScoring")}
            </a>
          </p>
        </div>
        {(["rrf_method_ratio", "rrf_source_ratio", "rerank_slot_ratio"] as const).map((key) => (
          <fieldset key={key} className="space-y-2">
            <legend className="text-sm font-medium text-fg">{t(`settings.answering.${key}`)}</legend>
            {key === "rerank_slot_ratio" ? <p className="text-sm text-fg-muted">{t("settings.answering.rerank_slot_ratioHint")}</p> : null}
            <FormGrid>
              {([0, 1] as const).map((index) => <TextField
                key={index} id={`${key}-${index}`} width="xs" type="number" min={0} step={key === "rerank_slot_ratio" ? 1 : "any"}
                label={t(`settings.answering.${key}.${index}`)}
                value={String(form[key]?.[index] ?? [7,3][index])}
                disabled={disabled} onChange={(event) => {
                  const ratio: [number, number] = [...(form[key] ?? [0,0])];
                  ratio[index] = Number(event.target.value);
                  update({ [key]: ratio });
                }}
              />)}
            </FormGrid>
          </fieldset>
        ))}
        {invalidRatio ? <p role="alert" className="text-sm text-danger-fg">{t("settings.answering.rrfInvalid")}</p> : null}
        {invalidSlotRatio ? <p role="alert" className="text-sm text-danger-fg">{t("settings.answering.rerank_slot_ratioInvalid")}</p> : null}
      </SettingsGroup>
      <SettingsGroup id="answer-generation-title" title={t("settings.answering.answerGenerationTitle")}>
      <FormGrid>
        <SelectField
          id="answering-answer-flow"
          className="min-w-0"
          disabled={disabled}
          label={t("settings.answering.answerFlow")}
          helper={t("settings.answering.answerFlowHint")}
          value={form.answer_flow}
          options={ANSWER_FLOW_OPTIONS}
          width="lg"
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
          width="xs"
          onValueChange={(value) => update({ neighbor_child_count: Number(value) })}
        />
      </FormGrid>
      </SettingsGroup>
      <div
        role="group"
        aria-label={t("settings.answering.options")}
        className="divide-y divide-border rounded-md border border-border"
      >
        <SwitchRow
          id="answering-auto-field-filter"
          label={t("settings.answering.autoFieldFilter")}
          description={t("settings.answering.autoFieldFilterHint")}
          checked={form.auto_field_filter_enabled}
          disabled={disabled}
          onChange={(checked) => update({ auto_field_filter_enabled: checked })}
        />
        <div>
          <SwitchRow id="answering-entity-expansion" label={t("settings.answering.entityExpansion")}
            description={t("settings.answering.entityExpansionHint")} checked={form.entity_expansion_enabled}
            disabled={disabled} onChange={(checked) => update({ entity_expansion_enabled: checked })} />
          {form.entity_expansion_enabled ? <FormGrid className="px-3 pb-3">
            <SelectField id="answering-entity_expansion_max_chunks" label={t("settings.answering.entity_expansion_max_chunks")} width="xs"
              value={String(form.entity_expansion_max_chunks)} disabled={disabled} options={Array.from({ length: 20 }, (_, n) => { const value = n + 1; return { value: String(value), label: String(value) }; })}
              onValueChange={(value) => update({ entity_expansion_max_chunks: Number(value) })} />
          </FormGrid> : null}
        </div>
        <SwitchRow
          id="answering-graph-expansion"
          label={t("settings.answering.graphExpansion")}
          description={t("settings.answering.graphExpansionHint")}
          checked={form.graph_expansion_enabled}
          disabled={disabled}
          onChange={(checked) => update({ graph_expansion_enabled: checked })}
        />
        <SwitchRow
          id="answering-screen-linking"
          label={t("settings.answering.screenLinking")}
          description={t("settings.answering.screenLinkingHint")}
          checked={form.screen_linking_enabled}
          disabled={disabled}
          onChange={(checked) => update({ screen_linking_enabled: checked })}
        />
        <div data-testid="answering-rerank-block">
        <SwitchRow
          id="answering-rerank"
          label={t("settings.answering.rerank")}
          description={t("settings.answering.rerankHint")}
          checked={form.rerank_enabled}
          disabled={disabled}
          onChange={(checked) => update({ rerank_enabled: checked })}
        />
        <FormGrid className="px-3 pb-3">
        <TextField
          id="answering-rerank-min-score" type="number" min={0} max={1} step="any" width="sm"
          label={t("settings.answering.rerankMinScore")}
          helper={t("settings.answering.rerankMinScoreHint")}
          value={Number.isFinite(form.rerank_min_relevance_score) ? String(form.rerank_min_relevance_score) : ""}
          error={form.rerank_min_relevance_score == null || !Number.isFinite(form.rerank_min_relevance_score) || form.rerank_min_relevance_score < 0 || form.rerank_min_relevance_score > 1 ? t("settings.answering.thresholdInvalid") : undefined}
          disabled={disabled || !form.rerank_enabled}
          onChange={(event) => update({ rerank_min_relevance_score: event.target.value === "" ? Number.NaN : Number(event.target.value) })}
        />
        <TextField
          id="answering-rerank-top-n" type="number" min={0} max={2000} step={1} width="sm"
          label={t("settings.answering.rerankTopN")}
          helper={t("settings.answering.rerankTopNHint")}
          value={Number.isFinite(form.rerank_top_n) ? String(form.rerank_top_n) : ""}
          error={!Number.isInteger(form.rerank_top_n) || (form.rerank_top_n ?? -1) < 0 || (form.rerank_top_n ?? 0) > 2000 ? t("settings.answering.rerankTopNInvalid") : undefined}
          disabled={disabled || !form.rerank_enabled}
          onChange={(event) => update({ rerank_top_n: event.target.value === "" ? Number.NaN : Number(event.target.value) })}
        />
        </FormGrid>
        </div>
        <div>
          <SwitchRow id="answering-section-expansion" label={t("settings.answering.sectionExpansion")}
            description={t("settings.answering.sectionExpansionHint")} checked={form.section_expansion_enabled}
            disabled={disabled} onChange={(checked) => update({ section_expansion_enabled: checked })} />
          {form.section_expansion_enabled ? <FormGrid className="px-3 pb-3">
            <SelectField id="answering-section_expansion_max_levels" label={t("settings.answering.section_expansion_max_levels")} width="xs"
              value={String(form.section_expansion_max_levels)} disabled={disabled} options={Array.from({ length: 8 }, (_, n) => { const value = n + 1; return { value: String(value), label: String(value) }; })}
              onValueChange={(value) => update({ section_expansion_max_levels: Number(value) })} />
            <SelectField id="answering-section_expansion_input_token_percent" label={t("settings.answering.section_expansion_input_token_percent")} width="xs"
              value={String(form.section_expansion_input_token_percent)} disabled={disabled} options={Array.from({ length: 81 }, (_, n) => { const value = n + 10; return { value: String(value), label: String(value) }; })}
              onValueChange={(value) => update({ section_expansion_input_token_percent: Number(value) })} />
          </FormGrid> : null}
        </div>
        <div className="space-y-3 px-3 py-3" data-testid="answering-audit-mode">
          <h3 className="text-sm font-medium text-fg">{t("settings.answering.auditMode")}</h3>
          <p className="text-xs leading-relaxed text-fg-muted">{t("settings.answering.auditModeHint")}</p>
          <SelectField
            id="answering-answer-audit-mode"
            label={t("settings.answering.auditModeField")}
            helper={t(`settings.answering.auditModeHint.${form.answer_audit_mode}`)}
            value={form.answer_audit_mode}
            options={ANSWER_AUDIT_OPTIONS}
            width="lg"
            disabled={disabled}
            onValueChange={(value) => update({ answer_audit_mode: value })}
          />
          {STEP_MODEL_FIELDS.map((field) => (
            <SelectField
              key={field.key}
              id={field.id}
              label={t(field.label)}
              helper={t(`${field.label}Hint`)}
              value={form[field.key]}
              options={stepModelOptions(saved, saved[field.key] ?? "")}
              width="md"
              disabled={disabled}
              onValueChange={(value) => update({ [field.key]: value })}
            />
          ))}
          <p className="text-xs leading-relaxed text-fg-muted">{t("settings.answering.auditBasicHint")}</p>
          <p className="text-xs leading-relaxed text-fg-muted">{t("settings.answering.auditBudgetHint")}</p>
        </div>
        <div className="space-y-3 px-3 py-3" data-testid="answering-evidence-refinement">
          <SelectField
            id="answering-evidence-refinement-mode"
            label={t("settings.answering.evidenceRefinement")}
            helper={t(`settings.answering.evidenceRefinementHint.${form.answer_evidence_refinement_mode}`)}
            value={form.answer_evidence_refinement_mode}
            options={EVIDENCE_REFINEMENT_OPTIONS}
            width="lg"
            disabled={disabled}
            onValueChange={(value) => update({ answer_evidence_refinement_mode: value })}
          />
          <p className="text-xs leading-relaxed text-fg-muted">{t("settings.answering.evidenceRefinementBudgetHint")}</p>
        </div>
        <SwitchRow
          id="answering-request-coverage"
          label={t("settings.answering.requestCoverage")}
          description={t("settings.answering.requestCoverageHint")}
          checked={form.request_coverage_retrieval_enabled}
          disabled={disabled}
          onChange={(checked) => update({ request_coverage_retrieval_enabled: checked })}
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
            disabled: !dirty || invalidRatio || invalidSlotRatio || invalidThreshold || invalidCount,
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
