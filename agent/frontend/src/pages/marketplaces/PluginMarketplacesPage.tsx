import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, RotateCcw, RefreshCw, Save, Trash2 } from "lucide-react";
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
  TextField,
} from "@engchina/production-ready-ui";
import { agentApi, type MarketplaceSource, type PluginManifest } from "@/lib/api";
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
import { sameDraft, useEditorLeaveGuard } from "@/lib/leave-guard";
import { focusField } from "@/pages/shared/page-helpers";

const EMPTY_MARKETPLACE_FORM = { id: "", name: "", url: "" };

export function PluginMarketplacesPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  // マーケットプレイスの追加・更新・削除とプラグインの install は Agent 管理の権限（admin）だけ（#215）。
  const { admin: canManage } = useCapabilities();
  const markets = useQuery({
    queryKey: ["plugin-marketplaces"],
    queryFn: agentApi.listPluginMarketplaces,
  });

  function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ["plugin-marketplaces"] });
  }

  const refreshMutation = useMutation({
    mutationFn: (id: string) => agentApi.refreshPluginMarketplace(id),
    onSuccess: (_data, id) => {
      toast.success(t("marketplaces.refreshed"));
      void invalidate();
      void queryClient.invalidateQueries({ queryKey: ["marketplace-plugins", id] });
    },
    onError: (error) => toast.error(error.message),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => agentApi.deletePluginMarketplace(id),
    onSuccess: () => {
      toast.success(t("marketplaces.deleted"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  async function remove(source: MarketplaceSource) {
    const ok = await confirm({
      title: t("marketplaces.confirmDeleteTitle"),
      description: t("marketplaces.confirmDeleteMessage", { id: source.id }),
      confirmLabel: t("marketplaces.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate(source.id, {
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  const busy = refreshMutation.isPending || deleteMutation.isPending;
  // 一覧の行と詳細で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const marketplaceActions = (source: MarketplaceSource): EntityAction[] => canManage ? [
    {
      id: "refresh",
      label: t("marketplaces.refresh"),
      icon: RefreshCw,
      disabled: busy,
      loading: refreshMutation.isPending && refreshMutation.variables === source.id,
      onSelect: () => refreshMutation.mutate(source.id),
    },
    {
      id: "delete",
      label: t("marketplaces.delete"),
      icon: Trash2,
      tone: "danger",
      disabled: busy,
      onSelect: () => remove(source),
    },
  ] : [];

  const list = markets.data?.marketplaces ?? [];
  const [marketQuery, setMarketQuery] = useListSearch("marketplaces");
  const visibleMarkets = list.filter((source) => matchesSearch(marketQuery, [source.name, source.id, source.url]));
  // 追加できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("marketplaces.title")}
          subtitle={t("page.pluginMarketplaces.subtitle")}
          actions={
            canManage
              ? [{ id: "create", kind: "primary", label: t("marketplaces.add"), icon: Plus, onClick: editor.openNew }]
              : []
          }
        />
        <PageBody wide>
          <Section title={t("marketplaces.list")} description={t("marketplaces.description")}>
            {refreshMutation.isPending ? (
              // 取得元（Git / HTTP）からプラグイン一覧を読み直すため数秒以上かかる。
              // スピナーは行メニューの loading が担う（messaging.md §3.7）。
              <MarketplaceRefreshProcessing id={refreshMutation.variables} />
            ) : null}
            <ListToolbar
              search={
                <ListSearchField
                  id="marketplace-search"
                  label={t("marketplaces.search")}
                  value={marketQuery}
                  onSearch={setMarketQuery}
                  count={visibleMarkets.length}
                />
              }
              summary={markets.data ? listCountLabel(visibleMarkets.length, list.length) : undefined}
              testId="marketplace-list-toolbar"
            />
            <QueryState query={markets} loadingLabel={t("loading.marketplaces")} skeleton={<TableSkeleton columns={5} />}>
              <MarketplaceTable
                sources={visibleMarkets}
                resetKey={marketQuery}
                empty={
                  list.length ? (
                    <NoMatchState title={t("marketplaces.noMatch")} onClear={() => setMarketQuery("")} />
                  ) : (
                    <EmptyState
                      title={t("marketplaces.empty")}
                      hint={canManage ? t("marketplaces.emptyHint") : undefined}
                      action={
                        canManage ? (
                          <Button variant="secondary" icon={Plus} onClick={editor.openNew}>
                            {t("marketplaces.addFirst")}
                          </Button>
                        ) : undefined
                      }
                    />
                  )
                }
                onOpen={(source) => editor.openItem(source.id)}
                hrefFor={(source) => editor.itemHref(source.id)}
                actionsFor={marketplaceActions}
              />
            </QueryState>
          </Section>
        </PageBody>
      </>
    );
  }

  if (target.kind === "new") {
    return (
      <MarketplaceAddEditor
        onBack={() => editor.backToList()}
        onAdded={async (id) => {
          await invalidate();
          editor.openItem(id, { replace: true });
        }}
      />
    );
  }

  const source = list.find((candidate) => candidate.id === target.id);
  if (!source) {
    return (
      <>
        <PageHeader
          wide
          title={t("marketplaces.title")}
        />
        <PageBody wide>
          <QueryState query={markets} loadingLabel={t("loading.marketplaces")} skeleton={<FormSkeleton fields={3} />}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <MarketplaceDetail
      source={source}
      actions={marketplaceActions(source)}
      refreshing={refreshMutation.isPending && refreshMutation.variables === source.id}
      canInstall={canManage}
      onBack={() => editor.backToList()}
      onInstalled={() => {
        void queryClient.invalidateQueries({ queryKey: ["plugins"] });
        void queryClient.invalidateQueries({ queryKey: ["skills"] });
        void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
        void queryClient.invalidateQueries({ queryKey: ["agents"] });
      }}
    />
  );
}

/** マーケットプレイスを追加する全画面エディタ（`?id=new`）。 */
function MarketplaceAddEditor({ onBack, onAdded }: { onBack: () => void; onAdded: (id: string) => Promise<void> }) {
  const [form, setForm] = useState(EMPTY_MARKETPLACE_FORM);
  const [idError, setIdError] = useState<string | null>(null);
  const addMutation = useMutation({
    mutationFn: (current: typeof EMPTY_MARKETPLACE_FORM) =>
      agentApi.addPluginMarketplace({
        id: current.id.trim(),
        name: current.name || undefined,
        url: current.url || undefined,
      }),
    onSuccess: async (source) => {
      toast.success(t("marketplaces.added"));
      setForm(EMPTY_MARKETPLACE_FORM);
      await onAdded(source.id);
    },
  });
  const { confirmClose } = useEditorLeaveGuard(!sameDraft(form, EMPTY_MARKETPLACE_FORM), addMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function add() {
    setIdError(null);
    if (!form.id.trim()) {
      setIdError(t("marketplaces.idRequired"));
      focusField("mkt-id");
      return;
    }
    addMutation.mutate(form);
  }

  const title = t("marketplaces.addTitle");
  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={t("page.pluginMarketplaces.subtitle")}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("marketplaces.title") }), onClick: () => void back(), testId: "editor-back" }}
        actions={[
          {
            id: "discard",
            kind: "secondary",
            label: t("editor.actions.discard"),
            icon: RotateCcw,
            disabled: sameDraft(form, EMPTY_MARKETPLACE_FORM) || addMutation.isPending,
            onClick: () => {
              setForm(EMPTY_MARKETPLACE_FORM);
              setIdError(null);
            },
          },
          {
            id: "create",
            kind: "primary",
            label: t("common.create"),
            icon: Save,
            loading: addMutation.isPending,
            onClick: add,
          },
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {/* 追加の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={addMutation.error ? (addMutation.error as Error).message : null}
          attemptKey={addMutation.submittedAt}
          testId="marketplace-add-error"
        />
        <Section title={t("marketplaces.overview")}>
          <Card className="min-w-0">
            {/* ID / 名前を同じ行に、URL は全幅（README §4「wide 画面の 100% 充填」）。 */}
            <CardContent className="grid gap-x-6 gap-y-4 pt-5 lg:grid-cols-2">
              <TextField
                id="mkt-id"
                label={t("marketplaces.id")}
                required
                error={idError ?? undefined}
                value={form.id}
                onValueChange={(value) => {
                  setForm({ ...form, id: value });
                  setIdError(null);
                }}
              />
              <TextField
                id="mkt-name"
                label={t("marketplaces.name")}
                value={form.name}
                onValueChange={(value) => setForm({ ...form, name: value })}
              />
              <TextField
                id="mkt-url"
                className="col-span-full"
                label={t("marketplaces.url")}
                helper={t("marketplaces.urlHint")}
                value={form.url}
                onValueChange={(value) => setForm({ ...form, url: value })}
              />
            </CardContent>
          </Card>
        </Section>
      </PageBody>
    </>
  );
}

function MarketplaceTable({
  sources,
  resetKey,
  empty,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  sources: MarketplaceSource[];
  resetKey?: unknown;
  empty: ReactNode;
  onOpen: (source: MarketplaceSource) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (source: MarketplaceSource) => string;
  actionsFor: (source: MarketplaceSource) => EntityAction[];
}) {
  const columns: DataTableColumn<MarketplaceSource>[] = [
    {
      key: "name",
      header: t("marketplaces.name"),
      rowHeader: true,
      render: (source) => (
        <RowTitleButton title={source.name || source.id} subtitle={source.id} href={hrefFor(source)} onClick={() => onOpen(source)} />
      ),
    },
    {
      key: "url",
      header: t("marketplaces.url"),
      className: "max-w-xs break-all text-xs text-fg-muted",
      render: (source) => source.url || "-",
    },
    {
      key: "plugin_count",
      header: t("marketplaces.pluginCount"),
      align: "right",
      className: "tabular-nums",
      render: (source) => source.plugin_count,
    },
    {
      key: "status",
      header: t("common.status"),
      render: (source) =>
        source.last_error ? (
          <StatusBadge variant="warning" label={t("common.error")} />
        ) : (
          <StatusBadge variant="success" label={t("common.valid")} />
        ),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (source) => (
        <RowActionMenu
          actions={actionsFor(source)}
          ariaLabel={t("common.entityActions", { name: source.id })}
          testId={`marketplace-row-actions-${source.id}`}
        />
      ),
    },
  ];

  return (
    <PagedDataTable
      pageKey="marketplaces"
      resetKey={resetKey}
      rows={sources}
      columns={columns}
      getRowKey={(source) => source.id}
      onRowClick={onOpen}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[44rem]"
      ariaLabel={t("marketplaces.list")}
      empty={empty}
    />
  );
}

/** マーケットプレイスの詳細（`?id=<marketplace id>`）。配布元の情報と、そこから install できる連携機能を出す。 */
function MarketplaceRefreshProcessing({ id }: { id: string | undefined }) {
  return (
    <ProcessingIndicator
      active
      label={t("marketplaces.progress.refreshing", { id: id ?? "" })}
      operationKey={`marketplace-refresh-${id ?? ""}`}
      placement="action"
      activityIcon="none"
      className="mb-3 rounded-md border border-border bg-surface-sunken px-3 py-2"
      testId="marketplace-refresh-processing"
    />
  );
}

function MarketplaceDetail({
  source,
  actions,
  refreshing,
  canInstall,
  onBack,
  onInstalled,
}: {
  source: MarketplaceSource;
  actions: EntityAction[];
  refreshing: boolean;
  /** プラグインの install（Agent 管理の権限）。無ければ行の操作を出さない。 */
  canInstall: boolean;
  onBack: () => void;
  onInstalled: () => void;
}) {
  const listing = useQuery({
    queryKey: ["marketplace-plugins", source.id],
    queryFn: () => agentApi.listMarketplacePlugins(source.id),
  });
  const installMutation = useMutation({
    mutationFn: (pluginId: string) => agentApi.installPlugin({ marketplace_id: source.id, plugin_id: pluginId }),
    onSuccess: () => {
      toast.success(t("plugins.installed"));
      onInstalled();
      void listing.refetch();
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const plugins = listing.data?.plugins ?? [];
  // 他の一覧と同じく名前・ID・説明で絞り込む（page-archetypes.md #535 / #600。検索語は作業状態に残す。#814）。
  const [pluginQuery, setPluginQuery] = useListSearch("marketplacePlugins");
  const visiblePlugins = plugins.filter((manifest) =>
    matchesSearch(pluginQuery, [manifest.name, manifest.id, manifest.description])
  );
  const title = source.name || source.id;

  const columns: DataTableColumn<PluginManifest>[] = [
    {
      key: "name",
      header: t("plugins.title"),
      rowHeader: true,
      render: (manifest) => (
        <div className="min-w-0">
          <p className="text-sm font-medium text-fg">{manifest.name}</p>
          <p className="font-mono text-xs text-fg-muted">
            {manifest.id}
            {manifest.version ? ` · v${manifest.version}` : ""}
          </p>
        </div>
      ),
    },
    {
      key: "description",
      header: t("agent.description"),
      className: "max-w-sm text-xs text-fg-muted",
      render: (manifest) => manifest.description || "-",
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (manifest) => (
        <RowActionMenu
          actions={[
            {
              id: "install",
              label: t("marketplaces.install"),
              icon: Download,
              visible: canInstall,
              disabled: installMutation.isPending,
              loading: installMutation.isPending && installMutation.variables === manifest.id,
              onSelect: () => installMutation.mutate(manifest.id),
            },
          ]}
          ariaLabel={t("common.entityActions", { name: manifest.id })}
          testId={`marketplace-plugin-row-actions-${manifest.id}`}
        />
      ),
    },
  ];

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={source.id}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("marketplaces.title") }), onClick: onBack, testId: "editor-back" }}
        actions={[
        ]}
      />
      <PageBody wide className="space-y-6">
        <Section
          title={t("editor.overview")}
          actions={
            <ObjectActionBar
              actions={actions}
              ariaLabel={t("common.entityActions", { name: source.id })}
              moreLabel={t("common.moreActions")}
              testId="marketplace-object-actions"
            />
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              variant="info"
              label={`${t("marketplaces.pluginCount")}: ${source.plugin_count}`}
              icon={false}
            />
            <span className="break-all text-xs text-fg-muted">{source.url || "-"}</span>
          </div>
          {source.last_error ? <Banner severity="warning">{source.last_error}</Banner> : null}
          {refreshing ? <MarketplaceRefreshProcessing id={source.id} /> : null}
        </Section>
        <Section title={t("marketplaces.available")}>
          {installMutation.isPending ? (
            // プラグインの取得と Skill / MCP の登録を行うため数秒以上かかる。
            <ProcessingIndicator
              active
              label={t("marketplaces.progress.installing", { id: installMutation.variables ?? "" })}
              operationKey={`marketplace-install-${installMutation.variables ?? ""}`}
              placement="action"
              activityIcon="none"
              className="mb-3 rounded-md border border-border bg-surface-sunken px-3 py-2"
              testId="marketplace-install-processing"
            />
          ) : null}
          <ListToolbar
            search={
              <ListSearchField
                id="marketplace-plugin-search"
                label={t("marketplaces.pluginSearch")}
                value={pluginQuery}
                onSearch={setPluginQuery}
                count={visiblePlugins.length}
              />
            }
            summary={listing.data ? listCountLabel(visiblePlugins.length, plugins.length) : undefined}
            testId="marketplace-plugin-toolbar"
          />
          <QueryState query={listing} loadingLabel={t("loading.marketplacePlugins")} skeleton={<TableSkeleton columns={3} />} testId="marketplace-plugins-loading">
            <PagedDataTable
              rows={visiblePlugins}
              columns={columns}
              getRowKey={(manifest) => manifest.id}
              rowProps={() => ({ className: "align-top" })}
              tableClassName="w-full min-w-[36rem]"
              ariaLabel={t("marketplaces.available")}
              resetKey={pluginQuery}
              empty={
                plugins.length ? (
                  <NoMatchState title={t("marketplaces.pluginNoMatch")} onClear={() => setPluginQuery("")} />
                ) : (
                  <EmptyState title={t("marketplaces.availableEmpty")} />
                )
              }
            />
          </QueryState>
        </Section>
      </PageBody>
    </>
  );
}
