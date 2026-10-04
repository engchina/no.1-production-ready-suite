import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Power, PowerOff, RotateCcw, RefreshCw, Trash2 } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  EmptyState,
  FormSkeleton,
  ListToolbar,
  ObjectActionBar,
  Skeleton,
  TableSkeleton,
  PageHeader,
  ProcessingIndicator,
  RowActionMenu,
  SaveErrorBanner,
  Section,
  StatusBadge,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  PageBody,
  RowTitleButton,
  TextareaField,
  apiErrorMessage,
} from "@engchina/production-ready-ui";
import { agentApi, type PluginManifest, type PluginSummary } from "@/lib/api";
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
import { parseJsonField } from "@/lib/field-validation";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { useEditorLeaveGuard } from "@/lib/leave-guard";
import { JsonPanel, focusField } from "@/pages/shared/page-helpers";
import { NonPersistentStorageNotice } from "@/components/system/StorageNotice";

export function PluginsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  // プラグインの install・有効化・削除・再読込は Agent 管理の権限（admin）だけ（#215）。
  const { admin: canManage } = useCapabilities();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: agentApi.listPlugins });

  function invalidate() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ["plugins"] }),
      queryClient.invalidateQueries({ queryKey: ["plugin"] }),
      queryClient.invalidateQueries({ queryKey: ["skills"] }),
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] }),
      queryClient.invalidateQueries({ queryKey: ["agents"] }),
    ]);
  }

  const enabledMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => agentApi.setPluginEnabled(id, enabled),
    onSuccess: () => {
      toast.success(t("plugins.enabledUpdated"));
      void invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error, t("common.error.operation"))),
  });
  const uninstallMutation = useMutation({
    mutationFn: (id: string) => agentApi.uninstallPlugin(id),
    onSuccess: () => {
      toast.success(t("plugins.uninstalled"));
      void invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error, t("common.error.operation"))),
  });
  const reloadMutation = useMutation({
    mutationFn: () => agentApi.reloadPlugins(),
    onSuccess: () => {
      toast.success(t("plugins.reloaded"));
      void invalidate();
    },
    onError: (error) => toast.error(t("plugins.reloadFailed"), { description: apiErrorMessage(error, t("common.error.retryLater")) }),
  });

  async function uninstall(plugin: PluginSummary) {
    const ok = await confirm({
      title: t("plugins.confirmUninstallTitle"),
      description: t("plugins.confirmUninstallMessage", { id: plugin.id }),
      confirmLabel: t("plugins.uninstall"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    uninstallMutation.mutate(plugin.id, {
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  const busy = uninstallMutation.isPending || enabledMutation.isPending;
  // 一覧の行と詳細で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const pluginActions = (plugin: PluginSummary): EntityAction[] => canManage ? [
    {
      id: "toggle-enabled",
      label: plugin.enabled ? t("plugins.disable") : t("plugins.enable"),
      icon: plugin.enabled ? PowerOff : Power,
      disabled: busy,
      onSelect: () => enabledMutation.mutate({ id: plugin.id, enabled: !plugin.enabled }),
    },
    {
      id: "uninstall",
      label: t("plugins.uninstall"),
      icon: Trash2,
      tone: "danger",
      disabled: busy,
      onSelect: () => uninstall(plugin),
    },
  ] : [];

  const list = plugins.data?.plugins ?? [];
  const [pluginQuery, setPluginQuery] = useListSearch("plugins");
  const visiblePlugins = list.filter((plugin) =>
    matchesSearch(pluginQuery, [plugin.name, plugin.id, plugin.description, plugin.marketplace_id])
  );
  // install できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("plugins.title")}
          subtitle={t("page.plugins.subtitle")}
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
                  { id: "install", kind: "primary", label: t("plugins.install"), icon: Plus, onClick: editor.openNew },
                ]
              : []
          }
          moreActionsLabel={t("common.moreActions")}
        />
        <PageBody wide>
          <NonPersistentStorageNotice />
          <Section title={t("plugins.list")} description={t("plugins.description")}>
            <ListToolbar
              search={
                <ListSearchField
                  id="plugin-search"
                  label={t("plugins.search")}
                  value={pluginQuery}
                  onSearch={setPluginQuery}
                  count={visiblePlugins.length}
                />
              }
              summary={plugins.data ? listCountLabel(visiblePlugins.length, list.length) : undefined}
              testId="plugin-list-toolbar"
            />
            <QueryState query={plugins} loadingLabel={t("loading.plugins")} skeleton={<TableSkeleton columns={5} />}>
              <PluginTable
                plugins={visiblePlugins}
                resetKey={pluginQuery}
                empty={
                  list.length ? (
                    <NoMatchState title={t("plugins.noMatch")} onClear={() => setPluginQuery("")} />
                  ) : (
                    <EmptyState
                      title={t("plugins.empty")}
                      hint={canManage ? t("plugins.emptyHint") : undefined}
                      action={
                        canManage ? (
                          <Button variant="secondary" icon={Plus} onClick={editor.openNew}>
                            {t("plugins.installFirst")}
                          </Button>
                        ) : undefined
                      }
                    />
                  )
                }
                onOpen={(plugin) => editor.openItem(plugin.id)}
                hrefFor={(plugin) => editor.itemHref(plugin.id)}
                actionsFor={pluginActions}
              />
            </QueryState>
          </Section>
        </PageBody>
      </>
    );
  }

  if (target.kind === "new") {
    return (
      <PluginInstallEditor
        onBack={() => editor.backToList()}
        onInstalled={async (pluginId) => {
          await invalidate();
          editor.openItem(pluginId, { replace: true });
        }}
      />
    );
  }

  const plugin = list.find((candidate) => candidate.id === target.id);
  if (!plugin) {
    return (
      <>
        <PageHeader
          wide
          title={t("plugins.title")}
        />
        <PageBody wide>
          <QueryState query={plugins} loadingLabel={t("loading.plugins")} skeleton={<FormSkeleton fields={3} />}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return <PluginDetail plugin={plugin} actions={pluginActions(plugin)} onBack={() => editor.backToList()} />;
}

/** manifest から install する全画面エディタ（`?id=new`）。入力中の manifest を未保存の変更として守る（#87）。 */
function PluginInstallEditor({
  onBack,
  onInstalled,
}: {
  onBack: () => void;
  onInstalled: (pluginId: string) => Promise<void>;
}) {
  const [manifestJson, setManifestJson] = useState("");
  const [manifestError, setManifestError] = useState<string | null>(null);
  const installMutation = useMutation({
    mutationFn: (manifest: PluginManifest) => agentApi.installPlugin({ manifest }),
    onSuccess: async (record) => {
      toast.success(t("plugins.installed"));
      setManifestJson("");
      await onInstalled(record.id);
    },
  });
  const { confirmClose } = useEditorLeaveGuard(manifestJson.trim() !== "", installMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function install() {
    // 未入力・JSON の形式のエラーは、どちらも manifest の欄の直下に出す（#541）。
    const manifest = parseJsonField<PluginManifest>(manifestJson, t("plugins.manifest"), {
      required: true,
      expect: "object",
    });
    if (!manifest.ok || !manifest.value) {
      setManifestError(manifest.ok ? t("plugins.manifestRequired") : manifest.error);
      focusField("plugin-manifest");
      return;
    }
    setManifestError(null);
    installMutation.mutate(manifest.value);
  }

  const title = t("plugins.installTitle");
  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={t("page.plugins.subtitle")}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("plugins.title") }), onClick: () => void back(), testId: "editor-back" }}
        actions={[
          {
            id: "discard",
            kind: "secondary",
            label: t("editor.actions.discard"),
            icon: RotateCcw,
            disabled: manifestJson.trim() === "" || installMutation.isPending,
            onClick: () => {
              setManifestJson("");
              setManifestError(null);
            },
          },
          {
            id: "install",
            kind: "primary",
            label: t("plugins.installSubmit"),
            icon: Download,
            loading: installMutation.isPending,
            onClick: install,
          },
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {/* 導入の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={installMutation.error ? apiErrorMessage(installMutation.error, t("common.error.operation")) : null}
          attemptKey={installMutation.submittedAt}
          testId="plugin-install-error"
        />
        {installMutation.isPending ? (
          // manifest の検証と Skill / MCP の登録を行うため数秒かかる。スピナーはヘッダーの install ボタンが担う。
          <ProcessingIndicator
            active
            label={t("plugins.progress.installing")}
            operationKey="plugin-manifest-install"
            placement="action"
            activityIcon="none"
            className="rounded-md border border-border bg-surface-sunken px-3 py-2"
            testId="plugin-install-processing"
          />
        ) : null}
        <Section title={t("plugins.manifest")} description={t("plugins.manifestHint")}>
          <Card className="min-w-0">
            <CardContent className="pt-5">
              <TextareaField
                id="plugin-manifest"
                label={t("plugins.manifest")}
                required
                error={manifestError ?? undefined}
                value={manifestJson}
                rows={16}
                monospace
                spellCheck={false}
                onValueChange={(value) => {
                  setManifestJson(value);
                  setManifestError(null);
                }}
              />
            </CardContent>
          </Card>
        </Section>
      </PageBody>
    </>
  );
}

/** インストール済み連携の詳細（`?id=<plugin id>`）。manifest は変更できないため閲覧と対象の操作だけを出す。 */
function PluginDetail({
  plugin,
  actions,
  onBack,
}: {
  plugin: PluginSummary;
  actions: EntityAction[];
  onBack: () => void;
}) {
  const record = useQuery({
    queryKey: ["plugin", plugin.id],
    queryFn: () => agentApi.getPlugin(plugin.id),
  });
  const manifest = record.data?.manifest;

  return (
    <>
      <PageHeader
        wide
        title={plugin.name}
        subtitle={`${plugin.id} · v${plugin.version}`}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("plugins.title") }), onClick: onBack, testId: "editor-back" }}
        actions={[
        ]}
      />
      <PageBody wide className="space-y-6">
        <Section
          title={t("editor.overview")}
          description={plugin.description || undefined}
          actions={
            <ObjectActionBar
              actions={actions}
              ariaLabel={t("common.entityActions", { name: plugin.name })}
              moreLabel={t("common.moreActions")}
              testId="plugin-object-actions"
            />
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              variant={plugin.enabled ? "success" : "neutral"}
              label={plugin.enabled ? t("agent.enabled") : t("agent.disabled")}
            />
            <span className="text-xs text-fg-muted">
              {`${t("plugins.source")}: ${plugin.marketplace_id ? plugin.marketplace_id : t("plugins.sourceManual")}`}
            </span>
          </div>
          <PluginBundle plugin={plugin} />
          {plugin.warnings.map((warning) => (
            <Banner key={warning} severity="warning">
              {warning}
            </Banner>
          ))}
        </Section>
        <Section title={t("plugins.contents")}>
          <QueryState
            query={record}
            loadingLabel={t("loading.pluginContents")}
            skeleton={
              <div className="grid min-w-0 gap-4 xl:grid-cols-3" aria-hidden="true">
                <Skeleton className="h-40" />
                <Skeleton className="h-40" />
                <Skeleton className="h-40" />
              </div>
            }
          >
            {manifest ? (
              <Card className="min-w-0">
                <CardContent className="grid min-w-0 gap-4 pt-5 xl:grid-cols-3">
                  <JsonPanel
                    title={t("plugins.skills")}
                    value={(manifest.skills ?? []).map((skill) => ({ id: skill.id, name: skill.name }))}
                  />
                  <JsonPanel title={t("plugins.mcp")} value={manifest.mcp_servers ?? []} />
                  <JsonPanel
                    title={t("plugins.resources")}
                    value={(manifest.resources ?? []).map((resource) => ({
                      id: resource.id,
                      kind: resource.kind,
                      name: resource.name,
                    }))}
                  />
                </CardContent>
              </Card>
            ) : null}
          </QueryState>
        </Section>
      </PageBody>
    </>
  );
}

function PluginBundle({ plugin }: { plugin: PluginSummary }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <StatusBadge variant="info" label={`${t("plugins.skills")} ${plugin.skill_count}`} icon={false} />
      <StatusBadge variant="info" label={`${t("plugins.mcp")} ${plugin.mcp_count}`} icon={false} />
      <StatusBadge variant="info" label={`${t("plugins.resources")} ${plugin.resource_count}`} icon={false} />
    </div>
  );
}

function PluginTable({
  plugins,
  resetKey,
  empty,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  plugins: PluginSummary[];
  resetKey?: unknown;
  empty: ReactNode;
  onOpen: (plugin: PluginSummary) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (plugin: PluginSummary) => string;
  actionsFor: (plugin: PluginSummary) => EntityAction[];
}) {
  const columns: DataTableColumn<PluginSummary>[] = [
    {
      key: "name",
      header: t("plugins.title"),
      rowHeader: true,
      render: (plugin) => (
        <RowTitleButton
          title={plugin.name}
          subtitle={`${plugin.id} · v${plugin.version}`}
          href={hrefFor(plugin)}
          onClick={() => onOpen(plugin)}
        />
      ),
    },
    {
      key: "source",
      header: t("plugins.source"),
      className: "text-fg-muted",
      render: (plugin) => (plugin.marketplace_id ? plugin.marketplace_id : t("plugins.sourceManual")),
    },
    {
      key: "bundle",
      header: t("plugins.bundle"),
      render: (plugin) => <PluginBundle plugin={plugin} />,
    },
    {
      key: "enabled",
      header: t("plugins.enabledLabel"),
      render: (plugin) => (
        <StatusBadge
          variant={plugin.enabled ? "success" : "neutral"}
          label={plugin.enabled ? t("agent.enabled") : t("agent.disabled")}
        />
      ),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (plugin) => (
        <RowActionMenu
          actions={actionsFor(plugin)}
          ariaLabel={t("common.entityActions", { name: plugin.id })}
          testId={`plugin-row-actions-${plugin.id}`}
        />
      ),
    },
  ];

  return (
    <PagedDataTable
      pageKey="plugins"
      resetKey={resetKey}
      rows={plugins}
      columns={columns}
      getRowKey={(plugin) => plugin.id}
      onRowClick={onOpen}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[46rem]"
      ariaLabel={t("plugins.title")}
      empty={empty}
    />
  );
}
