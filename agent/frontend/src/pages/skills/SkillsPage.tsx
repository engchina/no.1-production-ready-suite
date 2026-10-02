import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RotateCcw, RefreshCw, Save, Trash2 } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  EmptyState,
  FormSkeleton,
  ListToolbar,
  ObjectActionBar,
  TableSkeleton,
  PageHeader,
  RowActionMenu,
  SaveErrorBanner,
  Section,
  StatusBadge,
  Switch,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  type PageHeaderAction,
  PageBody,
  RowTitleButton,
  TextareaField,
  TextField,
} from "@engchina/production-ready-ui";
import { agentApi, type AgentSkill } from "@/lib/api";
import { MissingEditorTarget } from "@/components/EntityLayout";
import { PagedDataTable, QueryState } from "@/components/ListViews";
import {
  ListSearchField,
  listCountLabel,
  matchesSearch,
  NoMatchState,
  useListSearch,
} from "@/components/ListFilters";
import { useEditorRoute } from "@/lib/editor-route";
import { focusFirstInvalidField, parseJsonField } from "@/lib/field-validation";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { sameDraft, useEditorLeaveGuard } from "@/lib/leave-guard";
import { JsonPanel } from "@/pages/shared/page-helpers";
import { skillSourceLabel, skillSourceVariant } from "@/pages/skills/skill-labels";
import { NonPersistentStorageNotice } from "@/components/system/StorageNotice";

interface SkillFormState {
  id: string;
  name: string;
  description: string;
  instructions: string;
  tags: string;
  enabled: boolean;
  mcpRequirementsJson: string;
  resourceIdsJson: string;
}

const EMPTY_SKILL_FORM: SkillFormState = {
  id: "",
  name: "",
  description: "",
  instructions: "",
  tags: "",
  enabled: true,
  mcpRequirementsJson: "[]",
  resourceIdsJson: "[]",
};

function skillFormOf(skill: AgentSkill | undefined): SkillFormState {
  if (!skill) return EMPTY_SKILL_FORM;
  return {
    id: skill.id,
    name: skill.name,
    description: skill.description,
    instructions: skill.instructions,
    tags: skill.tags.join(", "),
    enabled: skill.enabled,
    mcpRequirementsJson: JSON.stringify(skill.mcp_requirements, null, 2),
    resourceIdsJson: JSON.stringify(skill.resource_ids, null, 2),
  };
}

export function SkillsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  // スキルの追加・変更・削除・再読込は Agent 管理の権限（admin）だけ（#215）。
  const { admin: canManage } = useCapabilities();
  const skills = useQuery({ queryKey: ["skills"], queryFn: agentApi.listSkills });

  function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ["skills"] });
  }

  const deleteMutation = useMutation({
    mutationFn: (skillId: string) => agentApi.deleteSkill(skillId),
    onSuccess: () => {
      toast.success(t("skills.deleted"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  const reloadMutation = useMutation({
    mutationFn: () => agentApi.reloadSkills(),
    onSuccess: () => {
      toast.success(t("skills.reloaded"));
      void invalidate();
    },
    // ヘッダーの操作で固定の面が無いため、失敗は danger の Toast（messaging.md §1「失敗を黙って捨てない」）。
    onError: (error) => toast.error(t("skills.reloadFailed"), { description: error.message }),
  });

  async function remove(skill: AgentSkill) {
    const ok = await confirm({
      title: t("skills.confirmDeleteTitle"),
      description: t("skills.confirmDeleteMessage", { id: skill.id }),
      confirmLabel: t("skills.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate(skill.id, {
      // エディタから削除したら、消えた対象へ戻れないよう履歴を置き換えて一覧へ戻る。
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  // 一覧の行と詳細（エディタの概要）で同じ定義を使う。ビルトイン / ファイル / env は読み取り専用。
  const skillActions = (skill: AgentSkill): EntityAction[] => [
    {
      id: "delete",
      label: t("skills.delete"),
      icon: Trash2,
      tone: "danger",
      visible: canManage && skill.source === "runtime",
      disabled: deleteMutation.isPending,
      onSelect: () => remove(skill),
    },
  ];

  const list = skills.data?.skills ?? [];
  const [skillQuery, setSkillQuery] = useListSearch("skills");
  const visibleSkills = list.filter((skill) =>
    matchesSearch(skillQuery, [skill.name, skill.id, skill.description, skill.tags.join(" ")])
  );
  // 追加できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("skills.title")}
          subtitle={t("page.skills.subtitle")}
          actions={
            canManage
              ? [
                  {
                    id: "reload",
                    kind: "utility",
                    label: t("skills.reload"),
                    icon: RefreshCw,
                    loading: reloadMutation.isPending,
                    onClick: () => reloadMutation.mutate(),
                  },
                  { id: "create", kind: "primary", label: t("skills.add"), icon: Plus, onClick: editor.openNew },
                ]
              : []
          }
          moreActionsLabel={t("common.moreActions")}
        />
        <PageBody wide>
          <NonPersistentStorageNotice />
          <Section title={t("skills.list")} description={t("skills.description")}>
            <ListToolbar
              search={
                <ListSearchField
                  id="skill-search"
                  label={t("skills.search")}
                  value={skillQuery}
                  onSearch={setSkillQuery}
                  count={visibleSkills.length}
                />
              }
              summary={skills.data ? listCountLabel(visibleSkills.length, list.length) : undefined}
              testId="skill-list-toolbar"
            />
            <QueryState query={skills} loadingLabel={t("loading.skills")} skeleton={<TableSkeleton columns={5} />}>
              <SkillTable
                skills={visibleSkills}
                resetKey={skillQuery}
                empty={
                  list.length ? (
                    <NoMatchState title={t("skills.noMatch")} onClear={() => setSkillQuery("")} />
                  ) : (
                    <EmptyState
                      title={t("skills.empty")}
                      hint={canManage ? t("skills.emptyHint") : undefined}
                      action={
                        canManage ? (
                          <Button variant="secondary" icon={Plus} onClick={editor.openNew}>
                            {t("skills.addFirst")}
                          </Button>
                        ) : undefined
                      }
                    />
                  )
                }
                onOpen={(skill) => editor.openItem(skill.id)}
                hrefFor={(skill) => editor.itemHref(skill.id)}
                actionsFor={skillActions}
              />
            </QueryState>
          </Section>
        </PageBody>
      </>
    );
  }

  const skill = target.kind === "edit" ? list.find((candidate) => candidate.id === target.id) : undefined;
  if (target.kind === "edit" && !skill) {
    return (
      <>
        <PageHeader
          wide
          title={t("skills.title")}
        />
        <PageBody wide>
          <QueryState query={skills} loadingLabel={t("loading.skills")} skeleton={<FormSkeleton fields={4} />}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <SkillEditor
      key={skill?.id ?? "new"}
      skill={skill}
      actions={skill ? skillActions(skill) : []}
      readOnly={!canManage}
      onBack={() => editor.backToList()}
      onSaved={async (skillId) => {
        await invalidate();
        editor.openItem(skillId, { replace: true });
      }}
    />
  );
}

/**
 * Skill の全画面エディタ（A 型。`?id=new` / `?id=<skill id>`）。
 * 実行時に追加した Skill だけを編集でき、ビルトイン / ファイル / env の Skill は読み取り専用の詳細を出す。
 */
type SkillFieldErrors = {
  id?: string;
  name?: string;
  mcpRequirements?: string;
  resourceIds?: string;
};

function SkillEditor({
  skill,
  actions,
  readOnly,
  onBack,
  onSaved,
}: {
  skill?: AgentSkill;
  actions: EntityAction[];
  /** 変更の権限がない利用者は、実行時に追加したスキルも読み取り専用の詳細で出す。 */
  readOnly: boolean;
  onBack: () => void;
  onSaved: (skillId: string) => Promise<void>;
}) {
  const [form, setForm] = useState<SkillFormState>(() => skillFormOf(skill));
  const [formBaseline, setFormBaseline] = useState<SkillFormState>(() => skillFormOf(skill));
  const [fieldErrors, setFieldErrors] = useState<SkillFieldErrors>({});
  const editingId = skill?.id ?? null;
  const editable = !readOnly && (!skill || skill.source === "runtime");

  // 送る内容は mutate の引数で渡す（クリック直前の入力を closure の古い state で送らない）。
  const saveMutation = useMutation({
    mutationFn: (current: SkillFormState) => {
      const payload = {
        name: current.name,
        description: current.description,
        instructions: current.instructions,
        tags: current.tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        enabled: current.enabled,
        mcp_requirements: JSON.parse(current.mcpRequirementsJson) as { server_id: string; tool_names: string[] }[],
        resource_ids: JSON.parse(current.resourceIdsJson) as string[],
      };
      if (editingId) {
        return agentApi.updateSkill(editingId, payload);
      }
      return agentApi.createSkill({ id: current.id.trim(), ...payload });
    },
    onSuccess: async (saved, current) => {
      toast.success(editingId ? t("skills.updated") : t("skills.created"));
      setFormBaseline(current);
      await onSaved(saved.id);
    },
  });

  const formDirty = editable && !sameDraft(form, formBaseline);
  const { confirmClose } = useEditorLeaveGuard(formDirty, saveMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function save() {
    // 未入力・JSON の形式のエラーは欄の下に出し、画面の並び順で最初のエラーの欄へフォーカスする（#531 / #541）。
    const mcpRequirements = parseJsonField(form.mcpRequirementsJson, t("skills.mcpRequirements"), {
      required: true,
      expect: "array",
    });
    const resourceIds = parseJsonField(form.resourceIdsJson, t("skills.resourceIds"), {
      required: true,
      expect: "array",
    });
    const errors: SkillFieldErrors = {
      id: !editingId && !form.id.trim() ? t("skills.idRequired") : undefined,
      name: form.name.trim() ? undefined : t("skills.nameRequired"),
      mcpRequirements: mcpRequirements.ok ? undefined : mcpRequirements.error,
      resourceIds: resourceIds.ok ? undefined : resourceIds.error,
    };
    setFieldErrors(errors);
    if (
      focusFirstInvalidField([
        ["skill-id", errors.id],
        ["skill-name", errors.name],
        ["skill-mcp-requirements", errors.mcpRequirements],
        ["skill-resource-ids", errors.resourceIds],
      ])
    ) {
      return;
    }
    saveMutation.mutate(form);
  }

  const title = skill ? skill.name : t("skills.addTitle");
  const headerActions: PageHeaderAction[] = [];
  if (editable) {
    // 変更を破棄は保存の左の secondary。変更が無いときは disabled（#618）。
    headerActions.push({
      id: "discard",
      kind: "secondary",
      label: t("editor.actions.discard"),
      icon: RotateCcw,
      disabled: !formDirty || saveMutation.isPending,
      onClick: () => {
        setForm(formBaseline);
        setFieldErrors({});
      },
    });
    headerActions.push({
      id: "save",
      kind: "primary",
      label: editingId ? t("common.save") : t("common.create"),
      icon: Save,
      loading: saveMutation.isPending,
      onClick: save,
    });
  }

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={skill ? skill.id : t("page.skills.subtitle")}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("skills.title") }), onClick: () => void back(), testId: "editor-back" }}
        actions={headerActions}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {/* 保存の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={saveMutation.error ? (saveMutation.error as Error).message : null}
          attemptKey={saveMutation.submittedAt}
          testId="skill-save-error"
        />
        {skill ? (
          <Section
            title={t("editor.overview")}
            description={skill.description || undefined}
            actions={
              <ObjectActionBar
                actions={actions}
                ariaLabel={t("common.entityActions", { name: skill.name })}
                moreLabel={t("common.moreActions")}
                testId="skill-object-actions"
              />
            }
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge variant={skillSourceVariant(skill.source)} label={skillSourceLabel(skill.source)} icon={false} />
              <StatusBadge
                variant={skill.enabled ? "success" : "neutral"}
                label={skill.enabled ? t("agent.enabled") : t("agent.disabled")}
              />
            </div>
            {!editable && !readOnly ? <Banner severity="info">{t("skills.readOnly")}</Banner> : null}
          </Section>
        ) : null}
        {skill && !editable ? (
          <SkillReadOnlyDetail skill={skill} />
        ) : (
          <>
            <Section title={t("skills.basic")}>
              <Card className="min-w-0">
                {/* ID / 名前、説明 / タグを同じ行に、指示は全幅（README §4「wide 画面の 100% 充填」）。 */}
                <CardContent className="grid gap-x-6 gap-y-4 pt-5 lg:grid-cols-2">
                  {/* ID は作成時だけ入力でき、必須（backend の create_agent_skill と送信ガード）。 */}
                  <TextField
                    id="skill-id"
                    label={t("skills.id")}
                    required={!editingId}
                    error={fieldErrors.id}
                    value={form.id}
                    disabled={Boolean(editingId)}
                    onValueChange={(value) => {
                      setForm({ ...form, id: value });
                      setFieldErrors((current) => ({ ...current, id: undefined }));
                    }}
                  />
                  <TextField
                    id="skill-name"
                    label={t("skills.name")}
                    required
                    error={fieldErrors.name}
                    value={form.name}
                    onValueChange={(value) => {
                      setForm({ ...form, name: value });
                      setFieldErrors((current) => ({ ...current, name: undefined }));
                    }}
                  />
                  <TextField
                    id="skill-description"
                    label={t("agent.description")}
                    value={form.description}
                    onValueChange={(value) => setForm({ ...form, description: value })}
                  />
                  <TextField
                    id="skill-tags"
                    label={t("skills.tags")}
                    helper={t("skills.tagsHint")}
                    value={form.tags}
                    onValueChange={(value) => setForm({ ...form, tags: value })}
                  />
                  <TextareaField
                    id="skill-instructions"
                    label={t("skills.instructions")}
                    className="col-span-full"
                    rows={8}
                    value={form.instructions}
                    onValueChange={(value) => setForm({ ...form, instructions: value })}
                  />
                  <label className="col-span-full flex items-center gap-2 text-sm text-fg">
                    <Switch
                      checked={form.enabled}
                      aria-label={t("skills.enabledLabel")}
                      onCheckedChange={(checked) => setForm({ ...form, enabled: checked })}
                    />
                    {t("skills.enabledLabel")}
                  </label>
                </CardContent>
              </Card>
            </Section>
            <Section title={t("skills.dependencies")}>
              <Card className="min-w-0">
                <CardContent className="grid gap-x-6 gap-y-4 pt-5 lg:grid-cols-2">
                  <TextareaField
                    id="skill-mcp-requirements"
                    label={t("skills.mcpRequirements")}
                    required
                    error={fieldErrors.mcpRequirements}
                    helper={t("skills.mcpRequirementsHint")}
                    value={form.mcpRequirementsJson}
                    rows={8}
                    monospace
                    spellCheck={false}
                    onValueChange={(value) => {
                      setForm({ ...form, mcpRequirementsJson: value });
                      setFieldErrors((current) => ({ ...current, mcpRequirements: undefined }));
                    }}
                  />
                  <TextareaField
                    id="skill-resource-ids"
                    label={t("skills.resourceIds")}
                    required
                    error={fieldErrors.resourceIds}
                    value={form.resourceIdsJson}
                    rows={8}
                    monospace
                    spellCheck={false}
                    onValueChange={(value) => {
                      setForm({ ...form, resourceIdsJson: value });
                      setFieldErrors((current) => ({ ...current, resourceIds: undefined }));
                    }}
                  />
                </CardContent>
              </Card>
            </Section>
          </>
        )}
      </PageBody>
    </>
  );
}

function SkillTable({
  skills,
  resetKey,
  empty,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  skills: AgentSkill[];
  resetKey?: unknown;
  empty: ReactNode;
  onOpen: (skill: AgentSkill) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (skill: AgentSkill) => string;
  actionsFor: (skill: AgentSkill) => EntityAction[];
}) {
  const columns: DataTableColumn<AgentSkill>[] = [
    {
      key: "name",
      header: t("skills.skill"),
      rowHeader: true,
      render: (skill) => (
        <RowTitleButton title={skill.name} subtitle={skill.id} href={hrefFor(skill)} onClick={() => onOpen(skill)} />
      ),
    },
    {
      key: "source",
      header: t("skills.source"),
      render: (skill) => (
        <StatusBadge variant={skillSourceVariant(skill.source)} label={skillSourceLabel(skill.source)} icon={false} />
      ),
    },
    {
      key: "enabled",
      header: t("common.status"),
      render: (skill) => (
        <StatusBadge
          variant={skill.enabled ? "success" : "neutral"}
          label={skill.enabled ? t("agent.enabled") : t("agent.disabled")}
        />
      ),
    },
    {
      key: "tags",
      header: t("skills.tags"),
      className: "text-xs text-fg-muted",
      render: (skill) => (skill.tags.length ? skill.tags.join(", ") : "-"),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (skill) => (
        <RowActionMenu
          actions={actionsFor(skill)}
          ariaLabel={t("common.entityActions", { name: skill.name })}
          testId={`skill-row-actions-${skill.id}`}
        />
      ),
    },
  ];

  return (
    <PagedDataTable
      pageKey="skills"
      resetKey={resetKey}
      rows={skills}
      columns={columns}
      getRowKey={(skill) => skill.id}
      onRowClick={onOpen}
      rowProps={(skill) => ({ className: "align-top", "data-testid": `skill-row-${skill.id}` })}
      tableClassName="w-full min-w-[44rem]"
      ariaLabel={t("skills.list")}
      empty={empty}
    />
  );
}

function SkillReadOnlyDetail({ skill }: { skill: AgentSkill }) {
  return (
    <>
      <Section title={t("skills.instructions")}>
        <Card className="min-w-0">
          <CardContent className="space-y-3 pt-5">
            <p className="whitespace-pre-wrap text-sm leading-6 text-fg">{skill.instructions || "-"}</p>
            <p className="text-xs text-fg-muted">{`${t("skills.tags")}: ${skill.tags.length ? skill.tags.join(", ") : "-"}`}</p>
          </CardContent>
        </Card>
      </Section>
      <Section title={t("skills.dependencies")}>
        <Card className="min-w-0">
          <CardContent className="grid min-w-0 gap-4 pt-5 md:grid-cols-2">
            <JsonPanel title={t("skills.mcpRequirements")} value={skill.mcp_requirements} />
            <JsonPanel title={t("skills.resourceIds")} value={skill.resource_ids} />
          </CardContent>
        </Card>
      </Section>
    </>
  );
}
