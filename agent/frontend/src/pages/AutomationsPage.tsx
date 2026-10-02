import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Pencil, Play, Plus, Save, Trash2, Undo2 } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  DataTable,
  EmptyState,
  Fieldset,
  ListSkeleton,
  ObjectActionBar,
  PageBody,
  PageHeader,
  RowActionMenu,
  RowTitleButton,
  SaveErrorBanner,
  Section,
  SelectField,
  StatusBadge,
  Switch,
  TableSkeleton,
  TextareaField,
  TextField,
  TimedLoadingState,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import { MissingEditorTarget } from "@/components/EntityLayout";
import { OneTimeSecret } from "@/components/OneTimeSecret";
import { PagedDataTable, listScrollLabel } from "@/components/ListViews";
import {
  agentApi,
  type AgentProfile,
  type Automation,
  type AutomationInput,
  type AutomationRun,
  type AutomationSchedule,
  type AutomationTrigger,
  type ScheduleFrequency,
} from "@/lib/api";
import { useEditorRoute } from "@/lib/editor-route";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { sameDraft, useEditorLeaveGuard } from "@/lib/leave-guard";
import { useCapabilities } from "@/lib/permissions";

// 業務 Agent の自動実行（スケジュール・Webhook。#784）。一覧 → 全画面エディタ（A 型。`?id=`）。
// 自動実行は作った利用者として Run を作り、前回の Run が終わっていなければその回は飛ばす。

const FREQUENCIES: readonly ScheduleFrequency[] = ["daily", "weekdays", "weekly", "hourly"];
const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6];

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tokyo";
  } catch {
    return "Asia/Tokyo";
  }
}

function defaultSchedule(): AutomationSchedule {
  return { frequency: "daily", time: "09:00", weekdays: [0], minute: 0, timezone: browserTimezone() };
}

export function describeTrigger(item: Pick<Automation, "trigger" | "schedule">): string {
  if (item.trigger === "webhook" || !item.schedule) return t("automation.trigger.webhook");
  const schedule = item.schedule;
  if (schedule.frequency === "hourly") return t("automation.describe.hourly", { minute: schedule.minute });
  if (schedule.frequency === "weekly") {
    const days = schedule.weekdays.map((day) => t(`automation.weekday.${day}` as I18nKey)).join("・");
    return t("automation.describe.weekly", { days, time: schedule.time });
  }
  return t(`automation.describe.${schedule.frequency}` as I18nKey, { time: schedule.time });
}

function resultVariant(result: Automation["last_result"]): StatusVariant {
  if (result === "created") return "success";
  if (result === "skipped") return "warning";
  return "danger";
}

function runStatusVariant(status: AutomationRun["status"]): StatusVariant {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "cancelled") return "neutral";
  if (status === "waiting_approval") return "warning";
  return "pending";
}

// Webhook の URL のコピー（秘密ではない）。失敗しても値は Toast に入れない（#790。秘密のコピーは OneTimeSecret）。
async function copyWebhookUrl(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(t("automation.copied"));
  } catch {
    toast.error(t("automation.webhook.copyUrlFailed"));
  }
}

export function AutomationsPage() {
  const editor = useEditorRoute();
  const capabilities = useCapabilities();
  const canManage = capabilities.admin;
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const usableAgents = useMemo(
    () => (agents.data?.agents ?? []).filter((agent) => agent.enabled && !agent.migration_required),
    [agents.data]
  );
  const target = editor.target;
  const editingId = target.kind === "edit" ? target.id : null;
  const detail = useQuery({
    queryKey: ["automation", editingId],
    queryFn: () => agentApi.getAutomation(editingId ?? ""),
    enabled: Boolean(editingId),
    retry: false,
  });

  if (target.kind === "new") {
    return (
      <AutomationEditor
        agents={usableAgents}
        readOnly={!canManage}
        onBack={() => editor.backToList()}
        onCreated={(item) => editor.openItem(item.id, { replace: true })}
        onDeleted={() => editor.backToList({ replace: true })}
      />
    );
  }
  if (target.kind === "edit") {
    if (detail.isLoading) {
      return (
        <PageBody wide>
          <TimedLoadingState label={t("loading.automation")} testId="automation-loading">
            <ListSkeleton rows={4} />
          </TimedLoadingState>
        </PageBody>
      );
    }
    if (!detail.data) {
      return <MissingEditorTarget id={target.id} onBack={() => editor.backToList({ replace: true })} />;
    }
    return (
      <AutomationEditor
        key={detail.data.automation.id}
        automation={detail.data.automation}
        runs={detail.data.runs}
        agents={usableAgents}
        readOnly={!canManage}
        onBack={() => editor.backToList()}
        onCreated={() => undefined}
        onDeleted={() => editor.backToList({ replace: true })}
      />
    );
  }
  return (
    <AutomationList
      canManage={canManage}
      agents={agents.data?.agents ?? []}
      onCreate={() => editor.openNew()}
      onOpen={(item) => editor.openItem(item.id)}
      itemHref={editor.itemHref}
    />
  );
}

function useRunNow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentApi.runAutomation(id),
    onSuccess: (fired) => {
      void queryClient.invalidateQueries({ queryKey: ["automations"] });
      void queryClient.invalidateQueries({ queryKey: ["automation"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      if (fired.result === "created") toast.success(t("automation.ranNow"));
      else if (fired.result === "skipped") toast.warning(t("automation.skippedNow"));
      else toast.error(fired.message);
    },
    onError: (error) => toast.error(error.message),
  });
}

function useDeleteAutomation(onDeleted?: () => void) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const remove = useMutation({
    mutationFn: (id: string) => agentApi.deleteAutomation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["automations"] });
      toast.success(t("automation.deleted"));
      onDeleted?.();
    },
    onError: (error) => toast.error(error.message),
  });
  return async (item: Automation) => {
    const ok = await confirm({
      title: t("automation.deleteTitle", { name: item.name }),
      description: t("automation.deleteDescription"),
      confirmLabel: t("automation.delete"),
      tone: "danger",
    });
    if (ok) remove.mutate(item.id);
  };
}

function AutomationList({
  canManage,
  agents,
  onCreate,
  onOpen,
  itemHref,
}: {
  canManage: boolean;
  agents: AgentProfile[];
  onCreate: () => void;
  onOpen: (item: Automation) => void;
  itemHref: (id: string) => string;
}) {
  const list = useQuery({ queryKey: ["automations"], queryFn: agentApi.listAutomations, refetchInterval: 30_000 });
  const runNow = useRunNow();
  const confirmDelete = useDeleteAutomation();
  const agentNames = new Map(agents.map((agent) => [agent.id, agent.name]));

  const columns: DataTableColumn<Automation>[] = [
    {
      key: "name",
      header: t("automation.column.name"),
      rowHeader: true,
      className: "min-w-56",
      render: (item) => (
        <RowTitleButton
          title={item.name}
          subtitle={agentNames.get(item.agent_id) ?? item.agent_id}
          href={itemHref(item.id)}
          onClick={() => onOpen(item)}
        />
      ),
    },
    { key: "trigger", header: t("automation.column.trigger"), className: "text-xs text-fg", render: (item) => describeTrigger(item) },
    {
      key: "status",
      header: t("automation.column.status"),
      render: (item) => (
        <StatusBadge
          variant={item.enabled ? "success" : "neutral"}
          label={item.enabled ? t("automation.enabled") : t("automation.disabled")}
        />
      ),
    },
    {
      key: "next",
      header: t("automation.column.next"),
      className: "whitespace-nowrap text-xs tabular-nums text-fg",
      render: (item) => (item.next_run_at ? formatDateTime(item.next_run_at) : "—"),
    },
    {
      key: "last",
      header: t("automation.column.last"),
      render: (item) =>
        item.last_result ? (
          <span className="inline-flex flex-wrap items-center gap-2">
            <StatusBadge variant={resultVariant(item.last_result)} label={t(`automation.result.${item.last_result}` as I18nKey)} />
            <span className="text-xs tabular-nums text-fg-muted">{formatDateTime(item.last_run_at)}</span>
          </span>
        ) : (
          <span className="text-xs text-fg-muted">—</span>
        ),
    },
  ];
  if (canManage) {
    columns.push({
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (item) => {
        const actions: EntityAction[] = [
          { id: "run", label: t("automation.runNow"), icon: Play, onSelect: () => runNow.mutate(item.id) },
          { id: "edit", label: t("automation.edit"), icon: Pencil, onSelect: () => onOpen(item) },
          { id: "delete", label: t("automation.delete"), icon: Trash2, tone: "danger", onSelect: () => void confirmDelete(item) },
        ];
        return (
          <RowActionMenu
            actions={actions}
            ariaLabel={t("common.entityActions", { name: item.name })}
            testId={`automation-row-actions-${item.id}`}
          />
        );
      },
    });
  }

  return (
    <>
      <PageHeader
        wide
        title={t("nav.automations")}
        subtitle={t("page.automations.subtitle")}
        actions={
          canManage
            ? [{ id: "create", kind: "primary", label: t("automation.create"), icon: Plus, onClick: onCreate }]
            : []
        }
      />
      <PageBody wide>
        {list.data && !list.data.persistent ? <Banner severity="warning">{t("automation.notPersistent")}</Banner> : null}
        <Card className="min-w-0">
          <CardContent className="pt-5">
            {list.isLoading ? (
              <TimedLoadingState label={t("loading.automations")} testId="automations-loading">
                <TableSkeleton columns={5} />
              </TimedLoadingState>
            ) : list.error ? (
              <Banner severity="danger">{list.error.message}</Banner>
            ) : (list.data?.automations ?? []).length === 0 ? (
              <EmptyState
                title={t("automation.list.empty")}
                hint={t("automation.list.emptyHint")}
                action={
                  canManage ? (
                    <Button variant="secondary" icon={Plus} onClick={onCreate}>
                      {t("automation.create")}
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <PagedDataTable<Automation>
                pageKey="automations"
                rows={list.data?.automations ?? []}
                columns={columns}
                getRowKey={(item) => item.id}
                ariaLabel={t("automation.list.label")}
                tableClassName="w-full min-w-[760px]"
              />
            )}
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}

interface AutomationDraft {
  agentId: string;
  name: string;
  goal: string;
  enabled: boolean;
  trigger: AutomationTrigger;
  schedule: AutomationSchedule;
}

function draftOf(item: Automation | undefined, agents: AgentProfile[]): AutomationDraft {
  return {
    agentId: item?.agent_id ?? agents[0]?.id ?? "",
    name: item?.name ?? "",
    goal: item?.goal ?? "",
    enabled: item?.enabled ?? true,
    trigger: item?.trigger ?? "schedule",
    schedule: item?.schedule ?? defaultSchedule(),
  };
}

function toPayload(draft: AutomationDraft): AutomationInput {
  return {
    agent_id: draft.agentId,
    name: draft.name.trim(),
    goal: draft.goal.trim(),
    enabled: draft.enabled,
    trigger: draft.trigger,
    schedule: draft.trigger === "schedule" ? draft.schedule : null,
  };
}

function AutomationEditor({
  automation,
  runs = [],
  agents,
  readOnly,
  onBack,
  onCreated,
  onDeleted,
}: {
  automation?: Automation;
  runs?: AutomationRun[];
  agents: AgentProfile[];
  readOnly: boolean;
  onBack: () => void;
  onCreated: (item: Automation) => void;
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [baseline, setBaseline] = useState<AutomationDraft>(() => draftOf(automation, agents));
  const [draft, setDraft] = useState<AutomationDraft>(baseline);
  const [submitted, setSubmitted] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const dirty = !sameDraft(draft, baseline);
  const runNow = useRunNow();
  const confirmDelete = useDeleteAutomation(onDeleted);

  const save = useMutation({
    mutationFn: (payload: AutomationInput) =>
      automation ? agentApi.updateAutomation(automation.id, payload) : agentApi.createAutomation(payload),
    onSuccess: async (saved) => {
      const next = draftOf(saved, agents);
      setBaseline(next);
      setDraft(next);
      setSubmitted(false);
      toast.success(automation ? t("automation.saved") : t("automation.created"));
      await queryClient.invalidateQueries({ queryKey: ["automations"] });
      await queryClient.invalidateQueries({ queryKey: ["automation", saved.id] });
      if (!automation) onCreated(saved);
    },
  });
  const issue = useMutation({
    mutationFn: (id: string) => agentApi.issueAutomationWebhookToken(id),
    onSuccess: (result) => {
      setToken(result.token);
      void queryClient.invalidateQueries({ queryKey: ["automation", result.automation.id] });
    },
    onError: (error) => toast.error(error.message),
  });
  const { confirmClose } = useEditorLeaveGuard(dirty, save.isPending);

  const nameError = submitted && !draft.name.trim() ? t("automation.nameRequired") : undefined;
  const goalError = submitted && !draft.goal.trim() ? t("automation.goalRequired") : undefined;
  const weekdaysError =
    submitted && draft.trigger === "schedule" && draft.schedule.frequency === "weekly" && draft.schedule.weekdays.length === 0
      ? t("automation.weekdaysRequired")
      : undefined;

  function setField<K extends keyof AutomationDraft>(key: K, value: AutomationDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }
  function setSchedule(patch: Partial<AutomationSchedule>) {
    setDraft((current) => ({ ...current, schedule: { ...current.schedule, ...patch } }));
  }
  function toggleWeekday(day: number) {
    const days = draft.schedule.weekdays.includes(day)
      ? draft.schedule.weekdays.filter((item) => item !== day)
      : [...draft.schedule.weekdays, day].sort();
    setSchedule({ weekdays: days });
  }

  async function back() {
    if (await confirmClose()) onBack();
  }

  function submit() {
    setSubmitted(true);
    if (!draft.name.trim() || !draft.goal.trim() || weekdaysErrorNow(draft)) return;
    save.mutate(toPayload(draft));
  }

  async function issueToken(item: Automation) {
    if (item.webhook_token_prefix) {
      const ok = await confirm({
        title: t("automation.webhook.reissueTitle"),
        description: t("automation.webhook.reissueDescription"),
        confirmLabel: t("automation.webhook.reissue"),
        tone: "warning",
      });
      if (!ok) return;
    }
    issue.mutate(item.id);
  }

  const objectActions: EntityAction[] =
    automation && !readOnly
      ? [
          {
            id: "run",
            label: t("automation.runNow"),
            icon: Play,
            loading: runNow.isPending,
            disabled: dirty,
            onSelect: () => runNow.mutate(automation.id),
            testId: "automation-run-now",
          },
          { id: "delete", label: t("automation.delete"), icon: Trash2, tone: "danger", onSelect: () => void confirmDelete(automation) },
        ]
      : [];
  const webhookUrl = automation ? `${window.location.origin}/api/hooks/${automation.id}` : null;

  return (
    <>
      <PageHeader
        wide
        title={automation ? automation.name : t("automation.create")}
        subtitle={t("page.automations.subtitle")}
        back={{
          label: t("common.backToList"),
          ariaLabel: t("editor.backToListOf", { list: t("nav.automations") }),
          onClick: () => void back(),
          testId: "editor-back",
        }}
        actions={
          readOnly
            ? []
            : [
                ...(automation && dirty
                  ? [{ id: "discard", kind: "secondary" as const, label: t("common.discardChanges"), icon: Undo2, onClick: () => setDraft(baseline) }]
                  : []),
                {
                  id: "save",
                  kind: "primary" as const,
                  label: automation ? t("common.save") : t("common.create"),
                  icon: Save,
                  loading: save.isPending,
                  onClick: submit,
                },
              ]
        }
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        <SaveErrorBanner message={save.error?.message ?? null} attemptKey={save.submittedAt} testId="automation-save-error" />
        <fieldset disabled={readOnly} className="min-w-0 space-y-6">
          <Section
            title={t("automation.basic")}
            actions={
              automation && objectActions.length ? (
                <ObjectActionBar
                  actions={objectActions}
                  ariaLabel={t("common.entityActions", { name: automation.name })}
                  moreLabel={t("common.moreActions")}
                  testId="automation-actions"
                />
              ) : undefined
            }
          >
            <Card>
              <CardContent className="space-y-4 pt-5">
                {automation?.last_result ? (
                  <p className="flex flex-wrap items-center gap-2 text-xs text-fg-muted" data-testid="automation-last">
                    <StatusBadge
                      variant={resultVariant(automation.last_result)}
                      label={t(`automation.result.${automation.last_result}` as I18nKey)}
                    />
                    {t("automation.lastResult", { message: automation.last_message ?? "" })}
                  </p>
                ) : null}
                <div className="grid gap-4 md:grid-cols-2">
                  <TextField
                    id="automation-name"
                    label={t("automation.name")}
                    required
                    maxLength={100}
                    value={draft.name}
                    error={nameError}
                    onValueChange={(value) => setField("name", value)}
                  />
                  <SelectField<string>
                    id="automation-agent"
                    label={t("automation.agent")}
                    value={draft.agentId}
                    options={agents.map((agent) => ({ value: agent.id, label: agent.name }))}
                    onValueChange={(value) => setField("agentId", value)}
                  />
                </div>
                <TextareaField
                  id="automation-goal"
                  label={t("automation.goal")}
                  helper={t("automation.goalHelper")}
                  required
                  rows={4}
                  maxLength={4000}
                  value={draft.goal}
                  error={goalError}
                  onValueChange={(value) => setField("goal", value)}
                />
                <label className="inline-flex items-center gap-2 text-sm text-fg">
                  <Switch
                    checked={draft.enabled}
                    aria-label={t("automation.enabledLabel")}
                    onCheckedChange={(checked) => setField("enabled", checked)}
                  />
                  {t("automation.enabledLabel")}
                </label>
              </CardContent>
            </Card>
          </Section>

          <Section title={t("automation.trigger")}>
            <Card>
              <CardContent className="space-y-4 pt-5">
                <Fieldset legend={t("automation.trigger")} legendClassName="sr-only" role="radiogroup" className="space-y-2">
                  <div className="flex flex-wrap gap-2">
                    {(["schedule", "webhook"] as const).map((option) => (
                      <label
                        key={option}
                        className="flex cursor-pointer items-center gap-2 rounded-md border border-border-control bg-surface px-3 py-2 text-sm text-fg has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle"
                      >
                        <input
                          type="radio"
                          name="automation-trigger"
                          value={option}
                          checked={draft.trigger === option}
                          onChange={() => setField("trigger", option)}
                          className="accent-accent-emphasis"
                        />
                        {t(`automation.trigger.${option}` as I18nKey)}
                      </label>
                    ))}
                  </div>
                </Fieldset>
                {draft.trigger === "schedule" ? (
                  <div className="space-y-4" data-testid="automation-schedule">
                    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                      <SelectField<ScheduleFrequency>
                        id="automation-frequency"
                        label={t("automation.frequency")}
                        value={draft.schedule.frequency}
                        options={FREQUENCIES.map((item) => ({ value: item, label: t(`automation.frequency.${item}` as I18nKey) }))}
                        onValueChange={(value) => setSchedule({ frequency: value })}
                      />
                      {draft.schedule.frequency === "hourly" ? (
                        <TextField
                          id="automation-minute"
                          label={t("automation.minute")}
                          type="number"
                          min="0"
                          max="59"
                          value={String(draft.schedule.minute)}
                          onValueChange={(value) => setSchedule({ minute: Math.min(59, Math.max(0, Number(value) || 0)) })}
                        />
                      ) : (
                        <TextField
                          id="automation-time"
                          label={t("automation.time")}
                          type="time"
                          value={draft.schedule.time}
                          onValueChange={(value) => setSchedule({ time: value })}
                        />
                      )}
                      <TextField
                        id="automation-timezone"
                        label={t("automation.timezone")}
                        helper={t("automation.timezoneHelper")}
                        value={draft.schedule.timezone}
                        onValueChange={(value) => setSchedule({ timezone: value })}
                      />
                    </div>
                    {draft.schedule.frequency === "weekly" ? (
                      <Fieldset legend={t("automation.weekdays")} error={weekdaysError} className="space-y-2">
                        <div className="flex flex-wrap gap-2">
                          {WEEKDAYS.map((day) => (
                            <label
                              key={day}
                              className="flex cursor-pointer items-center gap-2 rounded-md border border-border-control bg-surface px-3 py-2 text-sm text-fg has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle"
                            >
                              <input
                                type="checkbox"
                                checked={draft.schedule.weekdays.includes(day)}
                                onChange={() => toggleWeekday(day)}
                                className="accent-accent-emphasis"
                              />
                              {t(`automation.weekday.${day}` as I18nKey)}
                            </label>
                          ))}
                        </div>
                      </Fieldset>
                    ) : null}
                    {automation?.next_run_at && !dirty ? (
                      <p className="text-sm text-fg" data-testid="automation-next">
                        {t("automation.nextRun", { time: formatDateTime(automation.next_run_at) })}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <div className="space-y-3" data-testid="automation-webhook">
                    <p className="text-xs leading-5 text-fg-muted">{t("automation.webhook.description")}</p>
                    {webhookUrl && automation?.trigger === "webhook" ? (
                      <>
                        <div className="flex flex-wrap items-center gap-2">
                          <code className="min-w-0 break-all rounded-md bg-surface-sunken px-3 py-2 font-mono text-sm text-fg" data-testid="automation-webhook-url">
                            {webhookUrl}
                          </code>
                          <Button variant="secondary" icon={Copy} onClick={() => void copyWebhookUrl(webhookUrl)}>
                            {t("automation.webhook.copyUrl")}
                          </Button>
                        </div>
                        <p className="text-sm text-fg">
                          {automation.webhook_token_prefix
                            ? t("automation.webhook.current", { prefix: automation.webhook_token_prefix })
                            : t("automation.webhook.none")}
                        </p>
                        {!readOnly ? (
                          <Button
                            variant="secondary"
                            icon={KeyRound}
                            loading={issue.isPending}
                            disabled={dirty}
                            onClick={() => void issueToken(automation)}
                            data-testid="automation-issue-token"
                          >
                            {automation.webhook_token_prefix ? t("automation.webhook.reissue") : t("automation.webhook.issue")}
                          </Button>
                        ) : null}
                        {/* 発行した秘密は起点の「秘密を発行」の直下に 1 回だけ出す（messaging.md §10.1。#790）。 */}
                        {token ? (
                          <OneTimeSecret
                            key={token}
                            id="automation-webhook-secret"
                            title={t("automation.webhook.issued")}
                            description={t("automation.webhook.issuedDescription")}
                            label={t("automation.webhook.secret")}
                            value={token}
                            copyLabel={t("automation.webhook.copy")}
                            copiedMessage={t("automation.copied")}
                            copyFailedMessage={t("automation.webhook.copyFailed")}
                            doneLabel={t("automation.webhook.done")}
                            onDone={() => setToken(null)}
                            testId="automation-webhook-token"
                            valueTestId="automation-webhook-secret"
                          />
                        ) : null}
                      </>
                    ) : (
                      <p className="text-sm text-fg-muted">{t("automation.webhook.urlAfterSave")}</p>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </Section>
        </fieldset>

        {automation ? (
          <Section title={t("automation.history")}>
            <Card className="min-w-0">
              <CardContent className="pt-5">
                {runs.length === 0 ? (
                  <p className="text-sm text-fg-muted">{t("automation.history.empty")}</p>
                ) : (
                  <DataTable
                    rows={runs}
                    columns={historyColumns()}
                    getRowKey={(run) => run.run_id}
                    ariaLabel={t("automation.history.label")}
                    scrollAriaLabel={listScrollLabel(t("automation.history.label"))}
                    tableClassName="w-full min-w-[560px]"
                    stickyHeader
                  />
                )}
              </CardContent>
            </Card>
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}

function weekdaysErrorNow(draft: AutomationDraft): boolean {
  return draft.trigger === "schedule" && draft.schedule.frequency === "weekly" && draft.schedule.weekdays.length === 0;
}

function historyColumns(): DataTableColumn<AutomationRun>[] {
  return [
    {
      key: "started",
      header: t("automation.history.started"),
      rowHeader: true,
      className: "whitespace-nowrap tabular-nums text-fg",
      render: (run) => formatDateTime(run.created_at),
    },
    {
      key: "trigger",
      header: t("automation.history.trigger"),
      className: "text-xs text-fg",
      render: (run) => t(`automation.history.trigger.${run.trigger || "schedule"}` as I18nKey),
    },
    {
      key: "status",
      header: t("automation.history.status"),
      render: (run) => <StatusBadge variant={runStatusVariant(run.status)} label={run.status} />,
    },
    {
      key: "run",
      header: "Run",
      className: "break-all font-mono text-xs text-fg-muted",
      render: (run) => run.run_id,
    },
  ];
}
