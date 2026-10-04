import { type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Power, PowerOff, Rocket, RotateCcw } from "lucide-react";
import {
  Banner,
  Button,
  DataTable,
  EmptyState,
  FormSkeleton,
  ListToolbar,
  TableSkeleton,
  PageHeader,
  RowActionMenu,
  Section,
  StatusBadge,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  PageBody,
  RowTitleButton,
} from "@engchina/production-ready-ui";
import { agentApi, type AgentProfile, type AgentVersion } from "@/lib/api";
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
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { AgentEditorView } from "@/pages/agents/AgentEditorView";
import { formatDate } from "@/pages/shared/page-helpers";
import { NonPersistentStorageNotice } from "@/components/system/StorageNotice";

export function AgentsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  // 業務 Agent の変更は Agent 管理の権限（admin）だけ。それ以外は閲覧だけにする（#215）。
  const { admin: canManage } = useCapabilities();
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const skills = useQuery({ queryKey: ["skills"], queryFn: agentApi.listSkills });
  // 組み込み Runtime で選べるモデル（システム設定 > モデル の登録モデル。#754）。
  const runtimeStatus = useQuery({ queryKey: ["runtime-status"], queryFn: agentApi.getRuntimeStatus });
  const toggleAgent = useMutation({
    mutationFn: (agent: AgentProfile) => agentApi.patchAgent(agent.id, { enabled: !agent.enabled }),
    onSuccess: () => {
      toast.success(t("agent.enabledUpdated"));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const publishAgent = useMutation({
    mutationFn: (agent: AgentProfile) => agentApi.publishAgent(agent.id),
    onSuccess: (published) => {
      toast.success(t("agent.version.published", { version: published.published_version ?? 0 }));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error) => toast.error(error.message),
  });

  async function confirmPublish(agent: AgentProfile) {
    const next = Math.max(0, ...(agent.versions ?? []).map((item) => item.version)) + 1;
    const ok = await confirm({
      title: t("agent.version.publishTitle", { version: next }),
      description: t("agent.version.publishMessage"),
      confirmLabel: t("agent.version.publish"),
      cancelLabel: t("common.cancel"),
    });
    if (ok) publishAgent.mutate(agent);
  }

  // 一覧の行と詳細（エディタの概要）で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const agentActions = (agent: AgentProfile): EntityAction[] =>
    canManage
      ? [
          {
            // 下書きを版として公開する（#770）。公開していない変更があるときだけ出す。
            id: "publish",
            label: t("agent.version.publish"),
            icon: Rocket,
            visible: agent.unpublished_changes,
            disabled: publishAgent.isPending,
            onSelect: () => void confirmPublish(agent),
          },
          {
            id: "toggle-enabled",
            label: agent.enabled ? t("agent.disable") : t("agent.enable"),
            icon: agent.enabled ? PowerOff : Power,
            disabled: toggleAgent.isPending,
            onSelect: () => toggleAgent.mutate(agent),
          },
        ]
      : [];

  const agentList = agents.data?.agents ?? [];
  // 一覧の絞り込み（名前・ID・説明。#808）。検索語は作業状態に残し、変わったら 1 ページ目へ戻す。
  const [agentQuery, setAgentQuery] = useListSearch("agents");
  const visibleAgents = agentList.filter((agent) => matchesSearch(agentQuery, [agent.name, agent.id, agent.description]));
  // 作成できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("nav.agents")}
          subtitle={t("page.agents.subtitle")}
          actions={
            canManage
              ? [{ id: "create", kind: "primary", label: t("agent.create"), icon: Plus, onClick: editor.openNew }]
              : []
          }
        />
        <PageBody wide>
          <NonPersistentStorageNotice />
          {skills.error ? <Banner severity="danger">{skills.error.message}</Banner> : null}
          <Section title={t("agent.list")}>
            <ListToolbar
              search={
                <ListSearchField
                  id="agent-search"
                  label={t("agent.search")}
                  value={agentQuery}
                  onSearch={setAgentQuery}
                  count={visibleAgents.length}
                />
              }
              summary={agents.data ? listCountLabel(visibleAgents.length, agentList.length) : undefined}
              testId="agent-list-toolbar"
            />
            <QueryState query={agents} loadingLabel={t("loading.agents")} skeleton={<TableSkeleton columns={7} />}>
              <AgentTable
                agents={visibleAgents}
                resetKey={agentQuery}
                onOpen={(agent) => editor.openItem(agent.id)}
                hrefFor={(agent) => editor.itemHref(agent.id)}
                actionsFor={agentActions}
                empty={
                  agentList.length ? (
                    <NoMatchState title={t("agent.noMatch")} onClear={() => setAgentQuery("")} />
                  ) : (
                    <EmptyState
                      title={t("agent.empty.title")}
                      hint={canManage ? t("agent.empty.hint") : t("agent.empty.restrictedHint")}
                      action={
                        canManage ? (
                          <Button variant="secondary" icon={Plus} onClick={editor.openNew}>
                            {t("agent.createFirst")}
                          </Button>
                        ) : undefined
                      }
                    />
                  )
                }
              />
            </QueryState>
          </Section>
        </PageBody>
      </>
    );
  }

  const agent = target.kind === "edit" ? agentList.find((candidate) => candidate.id === target.id) : undefined;
  if (target.kind === "edit" && !agent) {
    return (
      <>
        <PageHeader
          wide
          title={t("nav.agents")}
        />
        <PageBody wide>
          <QueryState query={agents} loadingLabel={t("loading.agents")} skeleton={<FormSkeleton fields={3} />}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <AgentEditorView
      key={agent?.id ?? "new"}
      agent={agent}
      availableSkills={skills.data?.skills ?? []}
      skillsLoading={skills.isLoading}
      skillsError={skills.error}
      models={runtimeStatus.data?.models ?? []}
      defaultModelId={runtimeStatus.data?.model_id ?? ""}
      modelsLoading={runtimeStatus.isLoading}
      actions={agent ? agentActions(agent) : []}
      readOnly={!canManage}
      onBack={() => editor.backToList()}
      onCreated={(created) => editor.openItem(created.id, { replace: true })}
    />
  );
}

/** 公開の状態（公開中 vN / 未公開 / 公開していない変更あり。#770）。 */
export function AgentVersionBadges({ agent }: { agent: AgentProfile }) {
  if (agent.published_version === null) {
    return <StatusBadge variant="warning" label={t("agent.version.unpublished")} />;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusBadge
        variant="success"
        label={t("agent.version.publishedBadge", { version: agent.published_version })}
      />
      {agent.unpublished_changes ? (
        <StatusBadge variant="info" label={t("agent.version.changed")} icon={false} />
      ) : null}
    </span>
  );
}

/** 公開した版の一覧と「この版に戻す」（#770）。 */
export function AgentVersionsSection({ agent, readOnly }: { agent: AgentProfile; readOnly: boolean }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const restore = useMutation({
    mutationFn: (version: number) => agentApi.restoreAgentVersion(agent.id, version),
    onSuccess: (restored) => {
      toast.success(t("agent.version.restored", { version: restored.published_version ?? 0 }));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error) => toast.error(error.message),
  });

  async function confirmRestore(version: AgentVersion) {
    const ok = await confirm({
      title: t("agent.version.restoreTitle", { version: version.version }),
      description: t("agent.version.restoreMessage"),
      confirmLabel: t("agent.version.restore"),
      cancelLabel: t("common.cancel"),
    });
    if (ok) restore.mutate(version.version);
  }

  const rows = [...(agent.versions ?? [])].reverse();
  const columns: DataTableColumn<AgentVersion>[] = [
    {
      key: "version",
      header: t("agent.version.number"),
      rowHeader: true,
      render: (version) => (
        <span className="inline-flex flex-wrap items-center gap-2">
          <span className="font-medium text-fg">{`v${version.version}`}</span>
          {version.version === agent.published_version ? (
            <StatusBadge variant="success" label={t("agent.version.current")} />
          ) : null}
        </span>
      ),
    },
    {
      key: "published_at",
      header: t("agent.version.publishedAt"),
      className: "text-fg-muted",
      render: (version) => formatDate(version.published_at),
    },
    {
      key: "published_by",
      header: t("agent.version.publishedBy"),
      className: "text-fg-muted",
      render: (version) => version.published_by || "-",
    },
    {
      key: "note",
      header: t("agent.version.note"),
      className: "max-w-sm break-words text-fg-muted",
      render: (version) => version.note || "-",
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (version) => (
        <RowActionMenu
          actions={
            readOnly
              ? []
              : [
                  {
                    id: "restore",
                    label: t("agent.version.restore"),
                    icon: RotateCcw,
                    visible: version.version !== agent.published_version || agent.unpublished_changes,
                    disabled: restore.isPending,
                    onSelect: () => void confirmRestore(version),
                  },
                ]
          }
          ariaLabel={t("common.entityActions", { name: `v${version.version}` })}
          testId={`agent-version-actions-${version.version}`}
        />
      ),
    },
  ];

  return (
    <Section title={t("agent.version.title")} description={t("agent.version.description")}>
      {rows.length === 0 ? (
        <EmptyState title={t("agent.version.empty")} />
      ) : (
        <DataTable
          rows={rows}
          columns={columns}
          getRowKey={(version) => String(version.version)}
          rowProps={() => ({ className: "align-top" })}
          tableClassName="w-full min-w-[40rem]"
          ariaLabel={t("agent.version.title")}
        />
      )}
    </Section>
  );
}

function AgentTable({
  agents,
  resetKey,
  empty,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  agents: AgentProfile[];
  /** 検索語など、変わったら 1 ページ目へ戻す契機。 */
  resetKey?: unknown;
  /** 0 件のときの表示（データが無い / 検索に一致しない）。 */
  empty: ReactNode;
  onOpen: (agent: AgentProfile) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (agent: AgentProfile) => string;
  actionsFor: (agent: AgentProfile) => EntityAction[];
}) {
  const columns: DataTableColumn<AgentProfile>[] = [
    {
      key: "name",
      header: t("agent.name"),
      rowHeader: true,
      render: (agent) => (
        <RowTitleButton title={agent.name} subtitle={agent.id} href={hrefFor(agent)} onClick={() => onOpen(agent)} />
      ),
    },
    {
      key: "description",
      header: t("agent.description"),
      className: "max-w-xs text-fg-muted",
      render: (agent) => agent.description || "-",
    },
    {
      key: "skills",
      header: t("agent.skillCount"),
      align: "right",
      className: "tabular-nums",
      render: (agent) => agent.skill_ids.length,
    },
    {
      key: "model",
      header: t("agent.model"),
      // 空は「既定のテキストモデル」（システム設定 > モデル）。
      render: (agent) => (
        <span className="break-all text-xs text-fg">{agent.model_id || t("agent.modelDefault")}</span>
      ),
    },
    {
      key: "enabled",
      header: t("common.status"),
      render: (agent) => (
        <StatusBadge
          variant={agent.enabled ? "success" : "neutral"}
          label={agent.enabled ? t("agent.enabled") : t("agent.disabled")}
        />
      ),
    },
    {
      key: "version",
      header: t("agent.version.column"),
      render: (agent) => <AgentVersionBadges agent={agent} />,
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (agent) => (
        <RowActionMenu
          actions={actionsFor(agent)}
          ariaLabel={t("common.entityActions", { name: agent.name })}
          testId={`agent-row-actions-${agent.id}`}
        />
      ),
    },
  ];

  return (
    <PagedDataTable
      pageKey="agents"
      resetKey={resetKey}
      rows={agents}
      columns={columns}
      getRowKey={(agent) => agent.id}
      onRowClick={onOpen}
      rowProps={(agent) => ({ className: "align-top", "data-testid": `agent-row-${agent.id}` })}
      tableClassName="w-full min-w-[46rem]"
      ariaLabel={t("agent.list")}
      paginationTestId="agent-list-pagination"
      empty={empty}
    />
  );
}
