import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RotateCcw, RefreshCw, Save, Trash2 } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  DataTable,
  DEFAULT_PAGE_SIZE,
  EmptyState,
  FormSkeleton,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ListToolbar,
  ObjectActionBar,
  Pagination,
  TableSkeleton,
  TimedLoadingState,
  usePagination,
  PageHeader,
  RowActionMenu,
  SaveErrorBanner,
  Section,
  StatusBadge,
  toast,
  useConfirm,
  type DataTableColumn,
  visibleEntityActions,
  type EntityAction,
  PageBody,
  RowTitleButton,
  SecretField,
  SelectField,
  TextField,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "@engchina/production-ready-system-settings";
import {
  agentApi,
  ApiError,
  type ExternalMcpToolInfo,
  type McpAuthMode,
  type McpConnectionSettings,
  type McpConnectionWritePayload,
} from "@/lib/api";
import { MissingEditorTarget } from "@/components/EntityLayout";
import { agentPaginationLabels, listScrollLabel, PagedDataTable, QueryState } from "@/components/ListViews";
import {
  ListSearchField,
  listCountLabel,
  matchesSearch,
  NoMatchState,
  useListSearch,
} from "@/components/ListFilters";
import { useEditorRoute } from "@/lib/editor-route";
import { focusFirstInvalidField, numberFieldError } from "@/lib/field-validation";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { sameDraft, useEditorLeaveGuard } from "@/lib/leave-guard";
import { formatDate } from "@/pages/shared/page-helpers";
import { NonPersistentStorageNotice } from "@/components/system/StorageNotice";

/** 外部 MCP のタイムアウト秒の上限（backend の `_MCP_TIMEOUT_MAX_SECONDS` と同じ）。 */
const MCP_TIMEOUT_MAX_SECONDS = 600;

/** 接続を使えない理由と直し方（使えるときは出さない。常設の success の面にしない）。 */
function McpConnectionNotice({ connection }: { connection: McpConnectionSettings }) {
  if (connection.configured) {
    return null;
  }
  const missing = [
    connection.base_url ? null : t("settings.mcpConnections.missingUrl"),
    connection.auth_mode === "service_token" && !connection.service_token_configured
      ? t("settings.mcpConnections.missingSecret")
      : null,
    connection.auth_mode === "api_key" && !connection.api_key_configured
      ? t("settings.mcpConnections.missingApiKey")
      : null,
    connection.auth_mode === "oauth_client_credentials" && !connection.oauth_configured
      ? t("settings.mcpConnections.missingOauth")
      : null,
  ].filter((item): item is string => item !== null);
  return (
    <Banner severity="warning" title={t("settings.mcpConnections.notReady")}>
      {/* 環境変数名は長く区切りがないため、狭い幅では任意の位置で折り返す。 */}
      <ul className="list-disc space-y-1 pl-5 break-words [overflow-wrap:anywhere]">
        {missing.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Banner>
  );
}

/** サービストークンの準備の状態（.env で管理。値は表示しない）。 */
function McpServiceTokenStatus({ connection }: { connection: McpConnectionSettings }) {
  const rows = [
    {
      id: "secret",
      label: t("settings.productMcp.secret"),
      hint: t("settings.productMcp.secretHint"),
      configured: connection.service_token_configured,
      missingVariant: "warning" as const,
    },
    {
      id: "service-user",
      label: t("settings.productMcp.serviceUser"),
      hint: t("settings.productMcp.serviceUserHint"),
      configured: connection.service_user_configured,
      missingVariant: "neutral" as const,
    },
  ];
  return (
    <ul aria-label={t("settings.productMcp.authStatus")} className="divide-y divide-border rounded-md border border-border">
      {rows.map((row) => (
        <li key={row.id} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-0.5 break-words [overflow-wrap:anywhere]">
            <p className="text-sm font-medium text-fg">{row.label}</p>
            <p className="text-xs leading-5 text-fg-muted">{row.hint}</p>
          </div>
          <StatusBadge
            className="shrink-0 self-start"
            variant={row.configured ? "success" : row.missingVariant}
            label={row.configured ? t("common.configured") : t("common.notConfigured")}
          />
        </li>
      ))}
    </ul>
  );
}

/**
 * 接続のツール（MCP の tools/list）。接続の確認を兼ね、ログイン中の利用者として取得する。
 * 結果は「ツールを取得」の直下に、カードの全幅の結果パネル（`SettingsTestResultPanel`）で出し、
 * その下にツールの一覧を出す（messaging.md §10。#814）。
 */
function McpConnectionToolsPanel({ connection }: { connection: McpConnectionSettings }) {
  const startedRef = useRef(0);
  const [elapsedMs, setElapsedMs] = useState<number | undefined>(undefined);
  const [checkedAt, setCheckedAt] = useState<string | undefined>(undefined);
  const fetchTools = useMutation({
    mutationFn: () => agentApi.listMcpConnectionTools(connection.server_id),
    onMutate: () => {
      startedRef.current = performance.now();
    },
    onSettled: () => {
      setElapsedMs(Math.round(performance.now() - startedRef.current));
      setCheckedAt(formatDate(new Date().toISOString()));
    },
  });
  // 接続の設定（URL・認証）を保存し直したら、前の設定での結果は消す（messaging.md §10.4）。
  const { reset } = fetchTools;
  const settingsKey = `${connection.base_url ?? ""}\u0000${connection.auth_mode ?? ""}\u0000${connection.configured}`;
  useEffect(() => {
    reset();
  }, [reset, settingsKey]);

  const tools = fetchTools.data?.tools ?? [];

  return (
    <Section title={t("settings.mcpConnections.tools")} description={t("settings.mcpConnections.toolsDescription")}>
      <Card className="min-w-0">
        <CardContent className="space-y-4 pt-5">
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              onClick={() => fetchTools.mutate()}
              disabled={!connection.configured}
              aria-describedby={!connection.configured ? "mcp-tools-configure-hint" : undefined}
              loading={fetchTools.isPending}
              icon={RefreshCw}
            >
              {t("settings.mcpConnections.fetchTools")}
            </Button>
            {!connection.configured ? (
              // 未設定は通常の状態。警告にせず「ツールを取得」が使えない理由として補助テキストで伝える。
              <p id="mcp-tools-configure-hint" className="text-sm leading-6 text-fg-muted">
                {t("settings.mcpConnections.configureFirst")}
              </p>
            ) : null}
          </div>
          {fetchTools.isPending ? (
            // 取得中は結果の位置に経過時間を出す。スピナーは押した「ツールを取得」が担う（messaging.md §3.7 / §10.2）。
            <TimedLoadingState
              label={t("loading.mcpTools")}
              activityIcon="none"
              testId="mcp-tools-loading"
            >
              <TableSkeleton columns={4} />
            </TimedLoadingState>
          ) : fetchTools.error ? (
            <McpToolsFailure connection={connection} error={fetchTools.error} elapsedMs={elapsedMs} checkedAt={checkedAt} />
          ) : fetchTools.data ? (
            <>
              <SettingsTestResultPanel
                tone={tools.length ? "success" : "warning"}
                message={
                  tools.length
                    ? t("settings.mcpConnections.fetchSucceeded", {
                        name: connection.label || connection.server_id,
                        count: formatNumber(tools.length),
                      })
                    : t("settings.mcpConnections.fetchEmpty", { name: connection.label || connection.server_id })
                }
                elapsedMs={elapsedMs}
                checkedAt={checkedAt}
                troubleshooting={tools.length ? [] : [t("settings.mcpConnections.fetchEmptyHint")]}
                details={toSettingsTestResultDetails({
                  server_id: connection.server_id,
                  base_url: connection.base_url,
                  auth_mode: mcpAuthLabel(connection.auth_mode),
                  tools: tools.length,
                })}
                testId="mcp-tools-result"
              />
              {tools.length ? <McpToolsList tools={tools} /> : null}
            </>
          ) : null}
        </CardContent>
      </Card>
    </Section>
  );
}

/** ツールの取得の失敗（1 文目に何が起きたか、確認ポイント、技術的な詳細は開いた「詳細」）。 */
function McpToolsFailure({
  connection,
  error,
  elapsedMs,
  checkedAt,
}: {
  connection: McpConnectionSettings;
  error: Error;
  elapsedMs?: number;
  checkedAt?: string;
}) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <SettingsTestResultPanel
      tone="danger"
      message={t("settings.mcpConnections.fetchFailedMessage", { name: connection.label || connection.server_id })}
      elapsedMs={elapsedMs}
      checkedAt={checkedAt}
      troubleshooting={[
        error.message,
        t("settings.mcpConnections.fetchFailedHintUrl"),
        t("settings.mcpConnections.fetchFailedHintAuth"),
      ]}
      details={toSettingsTestResultDetails({
        server_id: connection.server_id,
        base_url: connection.base_url,
        status_code: apiError?.status,
        error_code: apiError?.errorCode,
        request_id: apiError?.requestId,
      })}
      testId="mcp-tools-result"
    />
  );
}

function McpToolApprovalBadge({ tool }: { tool: ExternalMcpToolInfo }) {
  // readOnlyHint の無いツールは、ツール権限で許可しない限り実行の前に承認を求める。
  return tool.read_only ? (
    <StatusBadge variant="neutral" label={t("settings.mcpConnections.readOnly")} icon={false} />
  ) : (
    <StatusBadge variant="warning" label={t("settings.mcpConnections.needsApproval")} icon={false} />
  );
}

function McpToolsList({ tools }: { tools: ExternalMcpToolInfo[] }) {
  // 取り直した一覧は別の結果なので、そのときだけ 1 ページ目へ戻す。
  const { page, setPage, totalPages, pageItems, range } = usePagination(tools, DEFAULT_PAGE_SIZE, { resetKey: tools });
  const labels = agentPaginationLabels();
  const mcpToolColumns: DataTableColumn<ExternalMcpToolInfo>[] = [
    {
      key: "name",
      header: t("settings.mcpDiscovery.tool"),
      rowHeader: true,
      render: (tool) => (
        <div className="min-w-0 space-y-0.5">
          <p className="break-words font-mono text-xs font-medium text-fg">{tool.name}</p>
          {tool.function_name ? (
            <p className="break-words font-mono text-xs text-fg-muted">{tool.function_name}</p>
          ) : null}
        </div>
      ),
    },
    {
      key: "description",
      header: t("settings.mcpDiscovery.descriptionColumn"),
      className: "max-w-sm text-fg-muted",
      render: (tool) => tool.description || "-",
    },
    {
      key: "approval",
      header: t("settings.mcpConnections.approval"),
      render: (tool) => <McpToolApprovalBadge tool={tool} />,
    },
    {
      key: "input_schema",
      header: t("settings.mcpDiscovery.inputSchema"),
      className: "text-fg-muted",
      render: (tool) => schemaSummary(tool.input_schema),
    },
  ];

  return (
    <div className="grid min-w-0 gap-2">
      {/* md 以上は表、md 未満はカード。どちらも同じページ（10 件）を出し、Pagination は 1 つにする。 */}
      <DataTable
        className="hidden md:block"
        rows={pageItems}
        columns={mcpToolColumns}
        getRowKey={(tool) => tool.name}
        rowProps={() => ({ className: `align-top ${INFORMATION_TABLE_ROW_CLASS}` })}
        tableClassName="w-full min-w-[46rem]"
        ariaLabel={t("settings.mcpConnections.tools")}
        scrollAriaLabel={listScrollLabel(t("settings.mcpConnections.tools"))}
        stickyHeader
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
      />
      <div
        className={`grid gap-3 md:hidden ${INFORMATION_LIST_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
        role="region"
        aria-label={listScrollLabel(t("settings.mcpConnections.tools"))}
        tabIndex={0}
      >
        {pageItems.map((tool) => (
          <div key={tool.name} className="rounded-md border border-border p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="min-w-0 break-words font-mono text-xs font-medium text-fg">{tool.name}</p>
              <McpToolApprovalBadge tool={tool} />
            </div>
            <p className="mt-1 text-sm leading-6 text-fg-muted">{tool.description || "-"}</p>
            <dl className="mt-3 grid grid-cols-1 gap-2 text-xs text-fg-muted">
              <McpToolMeta label={t("settings.mcpConnections.functionName")} value={tool.function_name ?? "-"} />
              <McpToolMeta label={t("settings.mcpDiscovery.inputSchema")} value={schemaSummary(tool.input_schema)} />
            </dl>
          </div>
        ))}
      </div>
      <Pagination
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        summary={labels.summary(range)}
        pageIndicator={labels.pageIndicator?.(page, totalPages)}
        prevLabel={labels.prev}
        nextLabel={labels.next}
        ariaLabel={labels.ariaLabel}
        testId="mcp-tools-pagination"
      />
    </div>
  );
}

function McpToolMeta({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2">
      <dt className="font-medium text-fg">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

function schemaSummary(schema?: Record<string, unknown> | null): string {
  if (!schema || Object.keys(schema).length === 0) {
    return "-";
  }
  const type = typeof schema.type === "string" ? schema.type : "schema";
  const properties = schema.properties;
  if (properties && typeof properties === "object" && !Array.isArray(properties)) {
    const count = Object.keys(properties).length;
    return t("settings.mcpDiscovery.schemaSummary", { type, count });
  }
  return type;
}

function mcpAuthLabel(mode?: McpAuthMode | null): string {
  if (mode === "service_token") {
    return t("settings.mcpConnections.authServiceToken");
  }
  if (mode === "oauth_client_credentials") {
    return t("settings.mcpServers.authOauth");
  }
  if (mode === "api_key") {
    return t("settings.mcpServers.authApiKey");
  }
  return t("settings.mcpServers.authNone");
}

function mcpSourceLabel(source: string): string {
  if (source === "builtin") return t("settings.mcpConnections.sourceBuiltin");
  if (source === "env") return t("settings.mcpConnections.sourceEnv");
  if (source.startsWith("plugin:")) return t("settings.mcpConnections.sourcePlugin");
  return t("settings.mcpConnections.sourceRuntime");
}

interface McpConnectionFormState {
  serverId: string;
  label: string;
  baseUrl: string;
  authMode: McpAuthMode;
  apiKey: string;
  serviceAudience: string;
  timeoutSeconds: string;
  sessionId: string;
  oauthTokenUrl: string;
  oauthClientId: string;
  oauthClientSecret: string;
  oauthScope: string;
}

const EMPTY_MCP_FORM: McpConnectionFormState = {
  serverId: "",
  label: "",
  baseUrl: "",
  authMode: "none",
  apiKey: "",
  serviceAudience: "",
  timeoutSeconds: "10",
  sessionId: "",
  oauthTokenUrl: "",
  oauthClientId: "",
  oauthClientSecret: "",
  oauthScope: "",
};

function mcpFormOf(connection: McpConnectionSettings | undefined): McpConnectionFormState {
  if (!connection) return EMPTY_MCP_FORM;
  return {
    ...EMPTY_MCP_FORM,
    serverId: connection.server_id,
    label: connection.label ?? "",
    baseUrl: connection.base_url ?? "",
    authMode: connection.auth_mode,
    serviceAudience: connection.service_audience ?? "",
    timeoutSeconds: String(connection.timeout_seconds),
  };
}

export function McpConnectionsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  // 接続の追加・変更・削除は Agent 管理の権限（admin）だけ（#215）。
  const capabilities = useCapabilities();
  const canManage = capabilities.admin;
  const connections = useQuery({
    queryKey: ["mcp-connections"],
    queryFn: agentApi.listMcpConnections,
  });

  function invalidate() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ["mcp-connections"] }),
      queryClient.invalidateQueries({ queryKey: ["mcp-connection-tools"] }),
    ]);
  }

  const deleteMutation = useMutation({
    mutationFn: (serverId: string) => agentApi.deleteMcpConnection(serverId),
    onSuccess: () => {
      toast.success(t("settings.mcpConnections.deleted"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  async function remove(connection: McpConnectionSettings) {
    const ok = await confirm({
      title: t("settings.mcpConnections.confirmDeleteTitle"),
      description: t("settings.mcpServers.confirmDeleteMessage", { id: connection.server_id }),
      confirmLabel: t("settings.mcpServers.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate(connection.server_id, {
      // エディタから削除したら、消えた対象へ戻れないよう履歴を置き換えて一覧へ戻る。
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  // 一覧の行と詳細（エディタの概要）で同じ定義を使う（UX 契約 buttons.md §5.1）。
  // RAG / NL2SQL・宣言・連携機能の接続は削除できない（項目を出さない）。
  const connectionActions = (connection: McpConnectionSettings): EntityAction[] =>
    canManage && connection.removable
      ? [
          {
            id: "delete",
            label: t("settings.mcpServers.delete"),
            icon: Trash2,
            tone: "danger",
            disabled: deleteMutation.isPending,
            onSelect: () => remove(connection),
          },
        ]
      : [];

  const list = connections.data?.connections ?? [];
  const [mcpQuery, setMcpQuery] = useListSearch("mcpServers");
  const visibleConnections = list.filter((connection) =>
    matchesSearch(mcpQuery, [connection.label, connection.server_id, connection.base_url])
  );
  // 追加できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;
  const listTitle = t("nav.settingsMcpConnections");

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={listTitle}
          subtitle={t("page.settings.mcp.subtitle")}
          actions={
            canManage
              ? [
                  {
                    id: "create",
                    kind: "primary",
                    label: t("settings.mcpConnections.add"),
                    icon: Plus,
                    onClick: editor.openNew,
                  },
                ]
              : []
          }
        />
        <PageBody wide className="space-y-6">
          <NonPersistentStorageNotice />
          <Section title={t("settings.mcpConnections.title")} description={t("settings.mcpConnections.description")}>
            <ListToolbar
              search={
                <ListSearchField
                  id="mcp-connection-search"
                  label={t("settings.mcpConnections.search")}
                  value={mcpQuery}
                  onSearch={setMcpQuery}
                  count={visibleConnections.length}
                />
              }
              summary={connections.data ? listCountLabel(visibleConnections.length, list.length) : undefined}
              testId="mcp-connection-list-toolbar"
            />
            <QueryState query={connections} loadingLabel={t("loading.mcpServers")} skeleton={<TableSkeleton columns={6} />}>
              <McpConnectionTable
                connections={visibleConnections}
                resetKey={mcpQuery}
                empty={
                  list.length ? (
                    <NoMatchState title={t("settings.mcpConnections.noMatch")} onClear={() => setMcpQuery("")} />
                  ) : (
                    <EmptyState title={t("settings.mcpConnections.empty")} />
                  )
                }
                onOpen={(connection) => editor.openItem(connection.server_id)}
                hrefFor={(connection) => editor.itemHref(connection.server_id)}
                actionsFor={connectionActions}
              />
            </QueryState>
          </Section>
        </PageBody>
      </>
    );
  }

  const connection =
    target.kind === "edit" ? list.find((candidate) => candidate.server_id === target.id) : undefined;
  if (target.kind === "edit" && !connection) {
    return (
      <>
        <PageHeader wide title={listTitle} />
        <PageBody wide>
          <QueryState query={connections} loadingLabel={t("loading.mcpServers")} skeleton={<FormSkeleton fields={4} />}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <McpConnectionEditor
      key={connection?.server_id ?? "new"}
      connection={connection}
      actions={connection ? connectionActions(connection) : []}
      readOnly={!canManage}
      onBack={() => editor.backToList()}
      onSaved={async (serverId) => {
        await invalidate();
        editor.openItem(serverId, { replace: true });
      }}
    />
  );
}

/** MCP 接続の全画面エディタ（A 型。`?id=new` / `?id=<接続 ID>`）。 */
function McpConnectionEditor({
  connection,
  actions,
  readOnly,
  onBack,
  onSaved,
}: {
  connection?: McpConnectionSettings;
  actions: EntityAction[];
  /** 変更の権限がない利用者は閲覧だけ（保存を出さず、入力を無効にする）。 */
  readOnly: boolean;
  onBack: () => void;
  onSaved: (serverId: string) => Promise<void>;
}) {
  const [form, setForm] = useState<McpConnectionFormState>(() => mcpFormOf(connection));
  const [formBaseline, setFormBaseline] = useState<McpConnectionFormState>(() => mcpFormOf(connection));
  const [serverIdError, setServerIdError] = useState<string | null>(null);
  const [timeoutError, setTimeoutError] = useState<string | null>(null);
  const editingId = connection?.server_id ?? null;
  // RAG / NL2SQL は Run の利用者のサービストークンで呼ぶ接続（認証方式は変えられない）。
  const builtin = connection?.source === "builtin";

  // 送る内容は mutate の引数で渡す（クリック直前の入力を closure の古い state で送らない）。
  const saveMutation = useMutation({
    mutationFn: (current: McpConnectionFormState) => {
      const payload: McpConnectionWritePayload = {
        label: current.label || null,
        base_url: current.baseUrl.trim(),
        timeout_seconds: Number(current.timeoutSeconds),
        session_id: current.sessionId || undefined,
      };
      if (!builtin) {
        payload.auth_mode = current.authMode;
      }
      if (current.authMode === "api_key" && current.apiKey) {
        payload.api_key = current.apiKey;
      }
      if (current.authMode === "service_token" && !builtin) {
        payload.service_audience = current.serviceAudience.trim();
      }
      if (current.authMode === "oauth_client_credentials") {
        payload.oauth_token_url = current.oauthTokenUrl || undefined;
        payload.oauth_client_id = current.oauthClientId || undefined;
        payload.oauth_client_secret = current.oauthClientSecret || undefined;
        payload.oauth_scope = current.oauthScope || undefined;
      }
      if (editingId) {
        return agentApi.updateMcpConnection(editingId, payload);
      }
      return agentApi.createMcpConnection({ server_id: current.serverId.trim(), ...payload });
    },
    onSuccess: async (saved, current) => {
      toast.success(editingId ? t("settings.mcpConnections.updated") : t("settings.mcpConnections.created"));
      // 秘密の欄は保存後に空へ戻す（値は保持も表示もしない）。
      const next = { ...current, apiKey: "", sessionId: "", oauthClientSecret: "" };
      setForm(next);
      setFormBaseline(next);
      await onSaved(saved.server_id ?? current.serverId.trim());
    },
  });

  // 開いた時点の内容から変わっていれば未保存（秘密の欄も含む。値は保存しない）。#87
  const formDirty = !sameDraft(form, formBaseline);
  const { confirmClose } = useEditorLeaveGuard(formDirty, saveMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function save() {
    const nextServerIdError =
      !editingId && !form.serverId.trim() ? t("settings.mcpServers.idRequired") : null;
    // 空のタイムアウトを 0 として保存しない。規則は backend（McpConnectionCreate / Patch）と同じ（#540）。
    const nextTimeoutError = numberFieldError(form.timeoutSeconds, {
      label: t("settings.timeout"),
      min: 0,
      exclusiveMin: true,
      max: MCP_TIMEOUT_MAX_SECONDS,
    });
    setServerIdError(nextServerIdError);
    setTimeoutError(nextTimeoutError);
    if (
      focusFirstInvalidField([
        ["mcp-server-id", nextServerIdError],
        ["mcp-server-timeout", nextTimeoutError],
      ])
    ) {
      return;
    }
    saveMutation.mutate(form);
  }

  const authOptions: SelectFieldOption<McpAuthMode>[] = [
    { value: "none", label: t("settings.mcpServers.authNone") },
    { value: "api_key", label: t("settings.mcpServers.authApiKey") },
    { value: "oauth_client_credentials", label: t("settings.mcpServers.authOauth") },
    { value: "service_token", label: t("settings.mcpConnections.authServiceToken") },
  ];
  const listTitle = t("nav.settingsMcpConnections");
  const title = connection ? connection.label || connection.server_id : t("settings.mcpConnections.addTitle");

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={connection ? connection.server_id : t("page.settings.mcp.subtitle")}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: listTitle }), onClick: () => void back(), testId: "editor-back" }}
        actions={[
          ...(readOnly
            ? []
            : [
                {
                  id: "discard",
                  kind: "secondary" as const,
                  label: t("editor.actions.discard"),
                  icon: RotateCcw,
                  disabled: !formDirty || saveMutation.isPending,
                  onClick: () => {
                    setForm(formBaseline);
                    setServerIdError(null);
                    setTimeoutError(null);
                  },
                },
                {
                  id: "save",
                  kind: "primary" as const,
                  label: editingId ? t("common.save") : t("common.create"),
                  icon: Save,
                  loading: saveMutation.isPending,
                  onClick: save,
                },
              ]),
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {/* 保存の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={saveMutation.error ? (saveMutation.error as Error).message : null}
          attemptKey={saveMutation.submittedAt}
          testId="mcp-server-save-error"
        />
        {connection ? (
          <Section
            title={t("editor.overview")}
            actions={
              <ObjectActionBar
                actions={actions}
                ariaLabel={t("common.entityActions", { name: connection.server_id })}
                moreLabel={t("common.moreActions")}
                testId="mcp-server-object-actions"
              />
            }
          >
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge
                  variant={connection.configured ? "success" : "warning"}
                  label={connection.configured ? t("common.configured") : t("common.notConfigured")}
                />
                <StatusBadge variant="neutral" label={mcpSourceLabel(connection.source)} icon={false} />
                <span className="text-xs text-fg-muted">
                  {`${t("settings.mcpServers.auth")}: ${mcpAuthLabel(connection.auth_mode)}`}
                </span>
              </div>
              <McpConnectionNotice connection={connection} />
            </div>
          </Section>
        ) : null}
        <fieldset disabled={readOnly} className="min-w-0 space-y-6">
          <Section title={t("mcpServers.connection")}>
            <Card className="min-w-0">
              <CardContent className="grid gap-x-6 gap-y-4 pt-5 lg:grid-cols-2">
                {/* 接続 ID は作成時だけ入力でき、必須。モデルに渡すツール名（<接続>__<ツール>）の先頭になる。 */}
                <TextField
                  id="mcp-server-id"
                  label={t("settings.mcpConnections.serverId")}
                  className="min-w-0"
                  required={!editingId}
                  error={serverIdError ?? undefined}
                  helper={t("settings.mcpConnections.serverIdHint")}
                  value={form.serverId}
                  disabled={Boolean(editingId)}
                  onValueChange={(value) => {
                    setForm({ ...form, serverId: value });
                    setServerIdError(null);
                  }}
                />
                <TextField
                  id="mcp-server-label"
                  label={t("settings.mcpServers.label")}
                  className="min-w-0"
                  value={form.label}
                  onValueChange={(value) => setForm({ ...form, label: value })}
                />
                <div className="col-span-full min-w-0">
                  <TextField
                    id="mcp-server-base-url"
                    label={t("settings.mcpConnections.url")}
                    helper={builtin ? t("settings.mcpConnections.urlHintBuiltin") : t("settings.mcpConnections.urlHint")}
                    value={form.baseUrl}
                    onValueChange={(value) => setForm({ ...form, baseUrl: value })}
                  />
                </div>
                <TextField
                  id="mcp-server-timeout"
                  label={t("settings.timeout")}
                  className="min-w-0"
                  required
                  error={timeoutError ?? undefined}
                  type="number"
                  min="1"
                  value={form.timeoutSeconds}
                  onValueChange={(value) => {
                    setForm({ ...form, timeoutSeconds: value });
                    setTimeoutError(null);
                  }}
                />
                <TextField
                  id="mcp-server-session"
                  label={t("settings.mcpSessionId")}
                  className="min-w-0"
                  helper={t("settings.mcpSessionHint")}
                  value={form.sessionId}
                  autoComplete="off"
                  onValueChange={(value) => setForm({ ...form, sessionId: value })}
                />
              </CardContent>
            </Card>
          </Section>
          <Section title={t("settings.mcpConnections.authSection")}>
            <Card className="min-w-0">
              <CardContent className="grid gap-x-6 gap-y-4 pt-5 lg:grid-cols-2">
                <SelectField<McpAuthMode>
                  id="mcp-server-auth-mode"
                  label={t("settings.mcpServers.auth")}
                  value={form.authMode}
                  options={authOptions}
                  disabled={builtin}
                  helper={builtin ? t("settings.mcpConnections.authBuiltinHint") : undefined}
                  onValueChange={(value) => setForm({ ...form, authMode: value })}
                />
                {form.authMode === "service_token" ? (
                  <TextField
                    id="mcp-server-audience"
                    label={t("settings.mcpConnections.audience")}
                    className="min-w-0"
                    helper={t("settings.mcpConnections.audienceHint")}
                    value={form.serviceAudience}
                    disabled={builtin}
                    onValueChange={(value) => setForm({ ...form, serviceAudience: value })}
                  />
                ) : null}
                {form.authMode === "api_key" ? (
                  // 秘密は共有の SecretField（保存済み / 未設定の表示・表示の切り替え。#631）。API は保存済みの有無だけを返す。
                  <SecretField
                    id="mcp-server-api-key"
                    label={t("settings.mcpServers.authApiKey")}
                    value={form.apiKey}
                    onValueChange={(value) => setForm({ ...form, apiKey: value })}
                    hasSavedSecret={Boolean(connection?.api_key_configured)}
                    savedLabel={t("settings.mcpConnections.secretSaved")}
                    notSetLabel={t("settings.mcpConnections.secretNotSet")}
                    showLabel={t("settings.mcpConnections.apiKeyShow")}
                    hideLabel={t("settings.mcpConnections.apiKeyHide")}
                    helper={
                      connection?.api_key_configured
                        ? `${t("settings.mcpConnections.apiKeyHint")}${t("settings.mcpConnections.secretKeepHint")}`
                        : t("settings.mcpConnections.apiKeyHint")
                    }
                  />
                ) : null}
                {form.authMode === "oauth_client_credentials" ? (
                  <>
                    <TextField
                      id="mcp-server-oauth-token"
                      label={t("settings.mcpServers.oauthTokenUrl")}
                      className="min-w-0"
                      value={form.oauthTokenUrl}
                      onValueChange={(value) => setForm({ ...form, oauthTokenUrl: value })}
                    />
                    <TextField
                      id="mcp-server-oauth-scope"
                      label={t("settings.mcpServers.oauthScope")}
                      className="min-w-0"
                      value={form.oauthScope}
                      onValueChange={(value) => setForm({ ...form, oauthScope: value })}
                    />
                    <TextField
                      id="mcp-server-oauth-client"
                      label={t("settings.mcpServers.oauthClientId")}
                      className="min-w-0"
                      value={form.oauthClientId}
                      autoComplete="off"
                      onValueChange={(value) => setForm({ ...form, oauthClientId: value })}
                    />
                    <SecretField
                      id="mcp-server-oauth-secret"
                      label={t("settings.mcpServers.oauthClientSecret")}
                      value={form.oauthClientSecret}
                      onValueChange={(value) => setForm({ ...form, oauthClientSecret: value })}
                      hasSavedSecret={Boolean(connection?.oauth_configured)}
                      savedLabel={t("settings.mcpConnections.secretSaved")}
                      notSetLabel={t("settings.mcpConnections.secretNotSet")}
                      showLabel={t("settings.mcpConnections.oauthSecretShow")}
                      hideLabel={t("settings.mcpConnections.oauthSecretHide")}
                      helper={connection?.oauth_configured ? t("settings.mcpConnections.secretKeepHint") : undefined}
                    />
                  </>
                ) : null}
                {form.authMode === "service_token" && connection ? (
                  <div className="col-span-full min-w-0">
                    <McpServiceTokenStatus connection={connection} />
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </Section>
        </fieldset>
        {/* 保存した接続だけツールを取得できる（入力中の値ではなく保存済みの設定で呼ぶ）。 */}
        {connection ? <McpConnectionToolsPanel key={connection.server_id} connection={connection} /> : null}
      </PageBody>
    </>
  );
}

function McpConnectionTable({
  connections,
  resetKey,
  empty,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  connections: McpConnectionSettings[];
  resetKey?: unknown;
  empty: ReactNode;
  onOpen: (connection: McpConnectionSettings) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (connection: McpConnectionSettings) => string;
  actionsFor: (connection: McpConnectionSettings) => EntityAction[];
}) {
  const columns: DataTableColumn<McpConnectionSettings>[] = [
    {
      key: "server_id",
      header: t("settings.mcpConnections.serverId"),
      rowHeader: true,
      render: (connection) => (
        <RowTitleButton
          title={connection.label || connection.server_id}
          subtitle={connection.server_id}
          href={hrefFor(connection)}
          onClick={() => onOpen(connection)}
        />
      ),
    },
    {
      key: "base_url",
      header: t("settings.mcpConnections.url"),
      className: "max-w-xs break-all text-fg-muted",
      render: (connection) => connection.base_url || "-",
    },
    {
      key: "auth_mode",
      header: t("settings.mcpServers.auth"),
      className: "text-fg-muted",
      render: (connection) => mcpAuthLabel(connection.auth_mode),
    },
    {
      key: "source",
      header: t("settings.mcpConnections.source"),
      render: (connection) => (
        <StatusBadge variant="neutral" label={mcpSourceLabel(connection.source)} icon={false} />
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      render: (connection) => (
        <StatusBadge
          variant={connection.configured ? "success" : "warning"}
          label={connection.configured ? t("common.configured") : t("common.notConfigured")}
        />
      ),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (connection) => (
        <RowActionMenu
          actions={actionsFor(connection)}
          ariaLabel={t("common.entityActions", { name: connection.server_id })}
          // RAG / NL2SQL などは削除できない。使える項目の無いメニューは開かせない。
          disabled={visibleEntityActions(actionsFor(connection)).every((action) => action.disabled)}
          testId={`mcp-server-row-actions-${connection.server_id}`}
        />
      ),
    },
  ];

  return (
    <PagedDataTable
      pageKey="mcpServers"
      resetKey={resetKey}
      rows={connections}
      columns={columns}
      getRowKey={(connection) => connection.server_id}
      onRowClick={onOpen}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[48rem]"
      ariaLabel={t("settings.mcpConnections.title")}
      empty={empty}
    />
  );
}
