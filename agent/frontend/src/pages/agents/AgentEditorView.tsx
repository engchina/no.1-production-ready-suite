import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save } from "lucide-react";
import {
  Banner,
  Card,
  CardContent,
  ListPicker,
  ListSkeleton,
  ObjectActionBar,
  TimedLoadingState,
  PageHeader,
  SaveErrorBanner,
  Section,
  StatusBadge,
  Switch,
  toast,
  useConfirm,
  type EntityAction,
  PageBody,
  SelectField,
  TextareaField,
  TextField,
  type ListPickerItem,
  type SelectFieldOption,
  ApiErrorBanner,
  apiErrorMessage,
} from "@production-ready/ui";
import {
  agentApi,
  type AgentProfile,
  type AgentTemplate,
  type AgentProfilePatchPayload,
  type AgentSkill,
  type BuiltinRuntimeModel,
} from "@/lib/api";
import { matchesSearch } from "@/components/ListFilters";
import { AgentTemplatePicker } from "@/components/agents/AgentTemplatePicker";
import { useCanEditEvaluationSets } from "@/components/evaluation/AddToEvaluationCase";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useValuesChanged } from "@/lib/render-sync";
import { sameDraft, useDirtySources, useEditorLeaveGuard } from "@/lib/leave-guard";
import { AgentVersionBadges, AgentVersionsSection } from "@/pages/agents/AgentsPage";
import { focusField, formatDate } from "@/pages/shared/page-helpers";
import { skillSourceLabel, skillSourceVariant } from "@/pages/skills/skill-labels";

interface AgentDraft {
  name: string;
  description: string;
  instructions: string;
  skill_ids: string[];
  model_id: string;
}

function agentDraftOf(agent: AgentProfile | undefined): AgentDraft {
  return {
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    instructions: agent?.instructions ?? "",
    // Skill の選択は集合なので並べ替えて比べる。
    skill_ids: [...(agent?.skill_ids ?? [])].sort(),
    model_id: agent?.model_id ?? "",
  };
}

/** エディタの dirty を親へ知らせる。unmount 時は false を知らせる。 */
function useReportDirty(dirty: boolean, onDirtyChange: (dirty: boolean) => void) {
  const callbackRef = useRef(onDirtyChange);
  // 最新の callback を commit 時に入れる（render 中に ref を書かない）。下の effect より先に走る。
  useLayoutEffect(() => {
    callbackRef.current = onDirtyChange;
  });
  useEffect(() => {
    callbackRef.current(dirty);
  }, [dirty]);
  useEffect(() => () => callbackRef.current(false), []);
}

/**
 * 業務 Agent の全画面エディタ（A 型。`?id=new` / `?id=<agent id>`）。
 * 有効 / 無効は対象の操作（一覧の行メニュー・概要の ObjectActionBar）で切り替え、フォームの下書きには含めない
 * （切り替えで一覧を取り直しても、編集中の内容を上書きしない）。新規作成だけは初期状態をフォームで選ぶ。
 */
export function AgentEditorView({
  agent,
  availableSkills,
  skillsLoading,
  skillsError,
  models,
  defaultModelId,
  modelsLoading,
  actions,
  readOnly,
  onBack,
  onCreated,
}: {
  agent?: AgentProfile;
  availableSkills: AgentSkill[];
  /** Skill を取得中は「取得できません」と誤って出さず、読み込み中の表示にする。 */
  skillsLoading: boolean;
  skillsError: Error | null;
  /** 組み込み Runtime で選べるモデル（システム設定 > モデル の登録モデル。#754）。 */
  models: BuiltinRuntimeModel[];
  /** 空を選んだときに使う既定のテキストモデル（表示用）。 */
  defaultModelId: string;
  modelsLoading: boolean;
  actions: EntityAction[];
  /** 変更の権限がない利用者は閲覧だけ（保存を出さず、入力を無効にする）。 */
  readOnly: boolean;
  onBack: () => void;
  onCreated: (agent: AgentProfile) => void;
}) {
  const queryClient = useQueryClient();
  const saved = agentDraftOf(agent);
  const savedKey = JSON.stringify(saved);
  const [name, setName] = useState(saved.name);
  const [agentDescription, setAgentDescription] = useState(saved.description);
  const [instructions, setInstructions] = useState(saved.instructions);
  const [newEnabled, setNewEnabled] = useState(true);
  const [skillIds, setSkillIds] = useState<string[]>(saved.skill_ids);
  const [modelId, setModelId] = useState(saved.model_id);
  const [baseline, setBaseline] = useState<AgentDraft>(saved);
  const [nameError, setNameError] = useState<string | null>(null);
  // 新規作成で選んだ業種テンプレート（#780）。作成した業務 Agent に残す（#810）。
  const [templateId, setTemplateId] = useState<string | null>(null);
  // テンプレートの評価ケースで評価セットを作るか（既定はオン。品質評価の権限を持つ利用者だけ。#810）。
  const canCreateEvaluationSet = useCanEditEvaluationSets();
  const [templateEvaluationSet, setTemplateEvaluationSet] = useState(true);
  const confirmTemplate = useConfirm();

  async function applyTemplate(template: AgentTemplate) {
    const touched = Boolean(name.trim() || agentDescription.trim() || instructions.trim() || skillIds.length);
    if (touched && templateId !== template.id) {
      const ok = await confirmTemplate({
        title: t("agent.template.replaceTitle"),
        description: t("agent.template.replaceDescription", { name: template.name }),
        confirmLabel: t("agent.template.replace"),
        tone: "warning",
      });
      if (!ok) return;
    }
    // 使えない（登録されていない）Skill は外して知らせる。
    const known = new Set(availableSkills.map((skill) => skill.id));
    const usable = template.skill_ids.filter((skillId) => known.has(skillId));
    const missing = template.skill_ids.filter((skillId) => !known.has(skillId));
    setName(template.name);
    setNameError(null);
    setAgentDescription(template.description);
    setInstructions(template.instructions);
    setSkillIds([...usable].sort());
    setTemplateId(template.id);
    toast.success(t("agent.template.applied", { name: template.name }), {
      description: missing.length ? t("agent.template.skillsMissing", { skills: missing.join("、") }) : undefined,
    });
  }

  const createAgent = useMutation({
    mutationFn: agentApi.createAgent,
    onSuccess: async (created) => {
      toast.success(t("agent.created"));
      setBaseline(draft);
      if (created.template_id && canCreateEvaluationSet && templateEvaluationSet) {
        // 評価セットの作成に失敗しても業務 Agent は作れている。品質評価の画面から作り直せる（#810）。
        try {
          const evaluationSet = await agentApi.createEvaluationSetFromTemplate(created.id);
          void queryClient.invalidateQueries({ queryKey: ["evaluation-sets"] });
          toast.success(t("evaluation.sets.fromTemplateCreated", { name: evaluationSet.name }));
        } catch (error) {
          toast.error(t("agent.template.evaluationSetFailed"), {
            description: apiErrorMessage(error, t("common.error.retryLater")),
          });
        }
      }
      // 一覧を取り直してから作成した Agent のエディタへ移る（戻るで空の新規フォームへ戻さない）。
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      onCreated(created);
    },
  });
  const patchAgent = useMutation({
    mutationFn: (payload: AgentProfilePatchPayload) => agentApi.patchAgent(agent?.id ?? "", payload),
    onSuccess: (_data, payload) => {
      toast.success(t("agent.saved"));
      // 保存に成功した内容を基準にする（一覧の再取得を待たずに dirty を解く）。
      setBaseline({
        name: payload.name ?? "",
        description: payload.description ?? "",
        instructions: payload.instructions ?? "",
        skill_ids: [...(payload.skill_ids ?? [])].sort(),
        model_id: payload.model_id ?? "",
      });
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const pending = createAgent.isPending || patchAgent.isPending;

  // 保存済みの内容が変わったときだけフォームを取り直す。有効状態の切替や他の Agent の保存による
  // 一覧の再取得で、編集中の内容を上書きしない（#87）。
  const isExisting = Boolean(agent);
  const savedChanged = useValuesChanged([isExisting, savedKey]);
  if (savedChanged && isExisting) {
    const next = JSON.parse(savedKey) as AgentDraft;
    setName(next.name);
    setAgentDescription(next.description);
    setInstructions(next.instructions);
    setSkillIds(next.skill_ids);
    setModelId(next.model_id);
    setBaseline(next);
    setNameError(null);
  }

  const draft: AgentDraft = {
    name,
    description: agentDescription,
    instructions,
    skill_ids: [...skillIds].sort(),
    model_id: modelId,
  };
  // Agent のフォームの dirty を 1 つの離脱ガードで守る（#87）。
  const dirtySources = useDirtySources();
  const formDirty = !sameDraft(draft, baseline) || (!agent && !newEnabled);
  useReportDirty(formDirty, (dirty) => dirtySources.report("agent", dirty));
  const { confirmClose } = useEditorLeaveGuard(dirtySources.anyDirty, pending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function setSkillSelected(skillId: string, selected: boolean) {
    setSkillIds((current) => {
      const next = current.filter((id) => id !== skillId);
      return selected ? [...next, skillId].sort() : next;
    });
  }

  /** 「変更を破棄」: 保存済みの内容（新規は空のフォーム）に戻す（#618。確認は出さない。RAG と同じ）。 */
  function discardChanges() {
    setName(baseline.name);
    setAgentDescription(baseline.description);
    setInstructions(baseline.instructions);
    setSkillIds(baseline.skill_ids);
    setModelId(baseline.model_id);
    setNewEnabled(true);
    setNameError(null);
    setTemplateId(null);
  }

  // スキルの選択は ListPicker（検索・表示中をすべて選択・選択中だけ表示・キーボード。page-archetypes.md「大量の候補から選ぶ」）。
  const [skillSearch, setSkillSearch] = useState("");
  const skillItem = (skill: AgentSkill): ListPickerItem => ({
    key: skill.id,
    label: skill.name,
    textValue: skill.name,
    description: skill.description || skill.id,
    meta: (
      <span className="flex shrink-0 flex-wrap items-center gap-1.5">
        <StatusBadge variant={skillSourceVariant(skill.source)} label={skillSourceLabel(skill.source)} icon={false} />
        {skill.enabled ? null : <StatusBadge variant="neutral" label={t("agent.disabled")} />}
      </span>
    ),
  });
  // 割り当て済みで登録から消えたスキル（スキルの削除・プラグインの外し方による）。選択の一覧に出さないと
  // 外せず、保存・公開が「登録されていないスキル」で断られ続けるため、ID を名前にした行で出す（#925）。
  const knownSkillIds = new Set(availableSkills.map((skill) => skill.id));
  const missingSkillIds = skillsLoading || skillsError ? [] : skillIds.filter((skillId) => !knownSkillIds.has(skillId));
  const missingSkillItem = (skillId: string): ListPickerItem => ({
    key: skillId,
    label: skillId,
    textValue: skillId,
    description: t("agent.skillPicker.missingDescription", { id: skillId }),
    meta: <StatusBadge variant="danger" label={t("agent.skillPicker.missing")} />,
  });
  const missingSkillItems = missingSkillIds.map(missingSkillItem);
  const skillItems = [
    ...missingSkillItems.filter((item) => matchesSearch(skillSearch, [item.key])),
    ...availableSkills
      .filter((skill) => matchesSearch(skillSearch, [skill.name, skill.id, skill.description]))
      .map(skillItem),
  ];
  const selectedSkillItems = [
    ...missingSkillItems,
    ...availableSkills.filter((skill) => skillIds.includes(skill.id)).map(skillItem),
  ];
  // 公開は保存済みの下書きを版にする。画面の入力と違う内容を公開しないよう、未保存の変更があるあいだは
  // 押せなくする（自動実行の「今すぐ実行」と同じ。#925）。
  const publishBlocked = formDirty && actions.some((action) => action.id === "publish" && action.visible !== false);
  const editorActions = actions.map((action) =>
    action.id === "publish" ? { ...action, disabled: Boolean(action.disabled) || formDirty } : action
  );

  function saveAgent() {
    setNameError(null);
    if (!name.trim()) {
      setNameError(t("agent.nameRequired"));
      focusField(`${fieldId}-agent-name`);
      return;
    }
    const payload = {
      name: name.trim(),
      description: agentDescription.trim(),
      instructions: instructions.trim(),
      skill_ids: skillIds,
      model_id: modelId,
    };
    if (agent) {
      setName(payload.name);
      setAgentDescription(payload.description);
      setInstructions(payload.instructions);
      patchAgent.mutate(payload);
      return;
    }
    createAgent.mutate({ ...payload, enabled: newEnabled, ...(templateId ? { template_id: templateId } : {}) });
  }

  const fieldId = agent?.id ?? "new";
  const title = agent ? agent.name : t("agent.create");
  const error = createAgent.error ?? patchAgent.error;
  const saveAttemptKey = Math.max(createAgent.submittedAt, patchAgent.submittedAt);

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={agent ? agent.id : t("page.agents.subtitle")}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("nav.agents") }), onClick: () => void back(), testId: "editor-back" }}
        // 一覧へ戻るは左上、保存は右端の primary、変更を破棄はその左の secondary（#618）。
        actions={[
          ...(readOnly
            ? []
            : [
                {
                  id: "discard",
                  kind: "secondary" as const,
                  label: t("editor.actions.discard"),
                  icon: RotateCcw,
                  disabled: !formDirty || pending,
                  onClick: discardChanges,
                },
                {
                  id: "save",
                  kind: "primary" as const,
                  label: agent ? t("common.save") : t("common.create"),
                  icon: Save,
                  loading: pending,
                  onClick: saveAgent,
                },
              ]),
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {/* 保存の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={error ? apiErrorMessage(error, t("common.error.save")) : null}
          attemptKey={saveAttemptKey}
          testId="agent-save-error"
        />
        {!agent && !readOnly ? (
          <AgentTemplatePicker
            selectedId={templateId}
            onApply={(template) => void applyTemplate(template)}
            evaluationSet={
              canCreateEvaluationSet
                ? { checked: templateEvaluationSet, onChange: setTemplateEvaluationSet }
                : undefined
            }
          />
        ) : null}
        {agent ? (
          <Section
            title={t("editor.overview")}
            actions={
              <ObjectActionBar
                actions={editorActions}
                ariaLabel={t("common.entityActions", { name: agent.name })}
                moreLabel={t("common.moreActions")}
                testId="agent-object-actions"
              />
            }
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge
                variant={agent.enabled ? "success" : "neutral"}
                label={agent.enabled ? t("agent.enabled") : t("agent.disabled")}
              />
              <AgentVersionBadges agent={agent} />
              <span className="text-xs text-fg-muted">{`${t("common.updatedAt")}: ${formatDate(agent.updated_at)}`}</span>
            </div>
            {publishBlocked ? (
              <p className="text-xs text-fg-muted" data-testid="agent-save-before-publish">
                {t("agent.version.saveBeforePublish")}
              </p>
            ) : null}
            {agent.migration_required ? <Banner severity="warning">{t("agent.migrationRequired")}</Banner> : null}
            {agent.published_version === null ? (
              // 公開するまで利用者のチャット・Run には使えない（管理者は Run の画面の「下書きで実行」で試せる）。
              <Banner severity="info">{t("agent.version.unpublishedHint")}</Banner>
            ) : null}
          </Section>
        ) : null}
        <fieldset disabled={readOnly} className="min-w-0 space-y-6">
          <Section title={t("agent.basic")}>
            <Card className="min-w-0">
              {/* wide の画面では段組みで埋める（名前 / 説明、モデル / 有効を同じ行に、指示は全幅。README §4）。 */}
              <CardContent className="grid gap-x-6 gap-y-4 pt-5 lg:grid-cols-2">
                <TextField
                  id={`${fieldId}-agent-name`}
                  label={t("agent.name")}
                  required
                  error={nameError ?? undefined}
                  value={name}
                  onValueChange={(value) => {
                    setName(value);
                    setNameError(null);
                  }}
                />
                <TextField
                  id={`${fieldId}-agent-description`}
                  label={t("agent.description")}
                  value={agentDescription}
                  onValueChange={setAgentDescription}
                />
                <TextareaField
                  id={`${fieldId}-agent-instructions`}
                  label={t("agent.instructions")}
                  className="col-span-full"
                  value={instructions}
                  onValueChange={setInstructions}
                  rows={8}
                />
                {/* 実行は組み込み Runtime（#754）。空は「既定のテキストモデル」（システム設定 > モデル）。 */}
                <SelectField
                  id={`${fieldId}-agent-model`}
                  label={t("agent.model")}
                  helper={t("agent.modelHint")}
                  disabled={modelsLoading}
                  value={modelId}
                  emptyOptionLabel={
                    defaultModelId ? t("agent.modelDefaultWith", { model: defaultModelId }) : t("agent.modelDefault")
                  }
                  options={agentModelOptions(models, modelId)}
                  onValueChange={setModelId}
                />
                {!agent ? (
                  // 有効は Switch（スキルのエディタと同じ形）。作成した後は対象の操作（有効にする / 無効にする）で切り替える。
                  <label className="flex items-center gap-2 self-end text-sm text-fg">
                    <Switch
                      checked={newEnabled}
                      aria-label={t("agent.enabled")}
                      onCheckedChange={setNewEnabled}
                      data-testid="agent-new-enabled"
                    />
                    {t("agent.enabled")}
                  </label>
                ) : null}
              </CardContent>
            </Card>
          </Section>
          <Section title={t("agent.skills")}>
            {skillsError ? <ApiErrorBanner error={skillsError} fallback={t("common.error.load")} /> : null}
            {missingSkillIds.length ? (
              <Banner severity="warning">
                {t("agent.skillPicker.missingBanner", { skills: missingSkillIds.join("、") })}
              </Banner>
            ) : null}
            {skillsLoading ? (
              <TimedLoadingState label={t("loading.skills")} testId="agent-skills-loading">
                <ListSkeleton rows={4} />
              </TimedLoadingState>
            ) : availableSkills.length || missingSkillIds.length ? (
              <ListPicker
                id={`${fieldId}-agent-skills`}
                label={t("agent.skillPicker.label")}
                items={skillItems}
                selectedKeys={new Set(skillIds)}
                selectedItems={selectedSkillItems}
                onToggle={(item, selected) => setSkillSelected(item.key, selected)}
                onSelectMany={(items) =>
                  setSkillIds((current) => [...new Set([...current, ...items.map((item) => item.key)])].sort())
                }
                onClearSelection={() => setSkillIds([])}
                search={{
                  id: `${fieldId}-agent-skill-search`,
                  label: t("agent.skillPicker.search"),
                  placeholder: t("agent.skillPicker.search"),
                  value: skillSearch,
                  onSearch: setSkillSearch,
                }}
                disabled={readOnly || pending}
                labels={{
                  resultCount: ({ visible, total, selected }) =>
                    t("agent.skillPicker.count", {
                      visible: formatNumber(visible),
                      total: formatNumber(total),
                      selected: formatNumber(selected),
                    }),
                  emptyTitle: t("agent.skillPicker.empty"),
                  noResultsTitle: t("agent.skillPicker.noMatch"),
                  selectedEmpty: t("agent.skillPicker.selectedEmpty"),
                  clearSearch: t("common.clearSearch"),
                }}
                testId="agent-skill-picker"
              />
            ) : skillsError ? null : (
              <Banner severity="warning">{t("agent.skillsUnavailable")}</Banner>
            )}
          </Section>
        </fieldset>
        {agent ? <AgentVersionsSection agent={agent} readOnly={readOnly} /> : null}
      </PageBody>
    </>
  );
}

/** モデルの選択肢（空 =「既定のテキストモデル」は emptyOptionLabel）。登録から消えた保存済みの値も残す。 */
function agentModelOptions(models: BuiltinRuntimeModel[], current: string): SelectFieldOption[] {
  const options: SelectFieldOption[] = models.map((model) => ({
    value: model.model_id,
    label: model.display_name,
  }));
  if (current && !models.some((model) => model.model_id === current)) {
    options.push({ value: current, label: t("agent.modelMissing", { model: current }) });
  }
  return options;
}
