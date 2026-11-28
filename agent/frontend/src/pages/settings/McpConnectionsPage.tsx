import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
  FormActionBar,
  FormGrid,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ListToolbar,
  ObjectActionBar,
  Pagination,
  PAGE_SIZE_OPTIONS,
  TableSkeleton,
  TimedLoadingState,
  usePagination,
  PageHeader,
  RowActionMenu,
  SaveErrorBanner,
  Section,
  StatusBadge,
  Switch,
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
  dataLoadView,
} from "@production-ready/ui";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "@production-ready/system-settings";
import {
  agentApi,
  ApiError,
  type ExternalMcpToolInfo,
  type McpAuthMode,
  type McpConnectionPreset,
  type McpConnectionProtocol,
  type McpConnectionSettings,
} from "@/lib/api";
import { MissingEditorTarget } from "@/components/EntityLayout";
import { agentPaginationLabels, listScrollLabel, PagedDataTable, QueryState } from "@/components/ListViews";
import {
  ListSearchField,
  listCount,
  listTotal,
  matchesSearch,
  NoMatchState,
  useListSearch,
} from "@/components/ListFilters";
import { useEditorRoute } from "@/lib/editor-route";
import { focusFirstInvalidField, numberFieldError } from "@/lib/field-validation";
import { formatNumber } from "@/lib/format";
import {
  a2aAgentCardOf,
  mcpAuthModesFor,
  mcpBaseUrlChanged,
  mcpConnectionPayload,
  mcpFormOf,
  withMcpPreset,
  withMcpProtocol,
  type A2aAgentCardInfo,
  type McpConnectionFormState,
  type McpConnectionPayloadContext,
} from "@/lib/mcp-connection-form";
import { mcpUrlCredentialProblem } from "@/lib/mcp-url";
import { t, type I18nKey } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { sameDraft, useEditorLeaveGuard } from "@/lib/leave-guard";
import { formatDate } from "@/pages/shared/page-helpers";
import { NonPersistentStorageNotice } from "@/components/system/StorageNotice";

/** 外部 MCP のタイムアウト秒の上限（backend の `_MCP_TIMEOUT_MAX_SECONDS` と同じ）。 */
const MCP_TIMEOUT_MAX_SECONDS = 1500;

/** 接続を使えない理由と直し方（使えるときは出さない。常設の success の面にしない）。 */
function McpConnectionNotice({ connection }: { connection: McpConnectionSettings }) {
  if (connection.configured) {
    return null;
  }
  const missing = [
    connection.base_url
      ? null
      : t(connection.protocol === "a2a" ? "settings.mcpConnections.missingUrlA2a" : "settings.mcpConnections.missingUrl"),
    connection.auth_mode === "service_token" && !connection.service_token_configured
      ? t("settings.mcpConnections.missingSecret")
      : null,
    connection.auth_mode === "api_key" && !connection.api_key_configured
      ? t("settings.mcpConnections.missingApiKey")
      : null,
    connection.auth_mode === "oauth_client_credentials" && !connection.oauth_configured
      ? t("settings.mcpConnections.missingOauth")
      : null,
    connection.auth_mode === "oci_signature" && !connection.oci_signature_configured
      ? t("settings.mcpConnections.missingOciSignature")
      : null,
  ].filter((item): item is string => item !== null);
  return (
    <Banner severity="warning" title={t("settings.mcpConnections.notReady")}>
      {/* 環境変数名は長く区切りがないため、狭い幅では任意の位置で折り返す。 */}
      <ul className="list-disc space-y-1 pl-5 pr-break-anywhere">
        {missing.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Banner>
  );
}

/**
 * 接続ごとの資格情報を持たない認証の準備の状態（値は表示しない）。サービストークンは共通 .env の署名鍵、
 * OCI 署名は「システム設定 › OCI 認証」の API キー（#5932）。
 */
function McpSharedCredentialStatus({ connection }: { connection: McpConnectionSettings }) {
  const ociSignature = connection.auth_mode === "oci_signature";
  const rows = ociSignature
    ? [
        {
          id: "oci-signature",
          label: t("settings.mcpConnections.ociSignatureKey"),
          hint: t("settings.mcpConnections.ociSignatureKeyHint"),
          configured: connection.oci_signature_configured === true,
        },
      ]
    : [
        {
          id: "secret",
          label: t("settings.productMcp.secret"),
          hint: t("settings.productMcp.secretHint"),
          configured: connection.service_token_configured,
        },
      ];
  return (
    <ul
      aria-label={t(ociSignature ? "settings.mcpConnections.ociSignatureStatus" : "settings.productMcp.authStatus")}
      className="divide-y divide-border rounded-md border border-border"
    >
      {rows.map((row) => (
        <li key={row.id} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-0.5 pr-break-anywhere">
            <p className="text-sm font-medium text-fg">{row.label}</p>
            <p className="text-xs leading-5 text-fg-muted">{row.hint}</p>
          </div>
          <StatusBadge
            className="shrink-0 self-start"
            variant={row.configured ? "success" : "warning"}
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
  const settingsKey = `${connection.protocol ?? "mcp"}\u0000${connection.base_url ?? ""}\u0000${connection.agent_card_url ?? ""}\u0000${connection.auth_mode ?? ""}\u0000${connection.configured}`;
  useEffect(() => {
    reset();
  }, [reset, settingsKey]);

  const tools = fetchTools.data?.tools ?? [];
  // A2A の接続（#5932）は Agent card を読み、その Agent にメッセージを送る 1 つのツールを返す。
  const a2a = connection.protocol === "a2a";
  const agentCard = a2aAgentCardOf(fetchTools.data?.metadata);
  const name = connection.label || connection.server_id;

  return (
    <Section
      title={t("settings.mcpConnections.tools")}
      description={t(a2a ? "settings.mcpConnections.toolsDescriptionA2a" : "settings.mcpConnections.toolsDescription")}
    >
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
              label={t(a2a ? "loading.a2aAgentCard" : "loading.mcpTools")}
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
                  !tools.length
                    ? t("settings.mcpConnections.fetchEmpty", { name })
                    : agentCard
                      ? t("settings.mcpConnections.fetchSucceededA2a", { name })
                      : t("settings.mcpConnections.fetchSucceeded", { name, count: formatNumber(tools.length) })
                }
                elapsedMs={elapsedMs}
                checkedAt={checkedAt}
                troubleshooting={tools.length ? [] : [t("settings.mcpConnections.fetchEmptyHint")]}
                details={toSettingsTestResultDetails({
                  server_id: connection.server_id,
                  protocol: mcpProtocolLabel(connection.protocol),
                  base_url: connection.base_url,
                  auth_mode: mcpAuthLabel(connection.auth_mode),
                  agent_name: agentCard?.name,
                  protocol_version: agentCard?.protocolVersion,
                  agent_version: agentCard?.version,
                  tools: tools.length,
                })}
                testId="mcp-tools-result"
              />
              {agentCard ? <A2aAgentCardSummary card={agentCard} /> : null}
              {tools.length ? (
                <McpToolsList
                  tools={tools}
                  trusted={connection.trust_tool_annotations === true}
                  protocol={connection.protocol ?? "mcp"}
                />
              ) : null}
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
  const a2a = connection.protocol === "a2a";
  return (
    <SettingsTestResultPanel
      tone="danger"
      message={t("settings.mcpConnections.fetchFailedMessage", { name: connection.label || connection.server_id })}
      elapsedMs={elapsedMs}
      checkedAt={checkedAt}
      // 1 つ目は backend の文（OCI 署名の 401 / 403 / 404 は、要る権限の案内を含む。#5932）。
      troubleshooting={[
        error.message,
        t(a2a ? "settings.mcpConnections.fetchFailedHintUrlA2a" : "settings.mcpConnections.fetchFailedHintUrl"),
        t(a2a ? "settings.mcpConnections.fetchFailedHintAuthA2a" : "settings.mcpConnections.fetchFailedHintAuth"),
      ]}
      details={toSettingsTestResultDetails({
        server_id: connection.server_id,
        protocol: mcpProtocolLabel(connection.protocol),
        base_url: connection.base_url,
        status_code: apiError?.status,
        error_code: apiError?.errorCode,
        request_id: apiError?.requestId,
      })}
      testId="mcp-tools-result"
    />
  );
}

function McpToolApprovalBadge({
  tool,
  trusted,
  protocol,
}: {
  tool: ExternalMcpToolInfo;
  trusted: boolean;
  protocol: McpConnectionProtocol;
}) {
  // readOnlyHint の無いツールは、ツール権限で許可しない限り実行の前に承認を求める。注釈を信頼しない接続では
  // readOnlyHint のツールも承認を求める（#1832）。標準の接続の nl2sql_query のように backend の既定で
  // 承認なしのツールは、backend の判定（default_decision）に従う。信頼した A2A の接続の Agent も既定で
  // 承認なし（#5932）。
  if (!tool.read_only && tool.default_decision === "allow") {
    return (
      <StatusBadge
        variant="neutral"
        label={t(
          protocol === "a2a" ? "settings.mcpConnections.autoTrustedConnection" : "settings.mcpConnections.autoByDefault"
        )}
        icon={false}
      />
    );
  }
  if (tool.read_only && !trusted) {
    return <StatusBadge variant="warning" label={t("settings.mcpConnections.readOnlyUntrusted")} icon={false} />;
  }
  return tool.read_only ? (
    <StatusBadge variant="neutral" label={t("settings.mcpConnections.readOnly")} icon={false} />
  ) : (
    <StatusBadge variant="warning" label={t("settings.mcpConnections.needsApproval")} icon={false} />
  );
}

/** 「ツールを取得」で読んだ Agent card（A2A の接続。名前・説明・版を読める形で出す。#5932）。 */
function A2aAgentCardSummary({ card }: { card: A2aAgentCardInfo }) {
  return (
    <div className="min-w-0 rounded-md border border-border p-3" data-testid="a2a-agent-card">
      <p className="text-sm font-medium text-fg">{t("settings.mcpConnections.agentCard")}</p>
      <dl className="mt-2 grid grid-cols-1 gap-2 text-sm">
        <McpToolMeta label={t("settings.mcpConnections.agentCardName")} value={card.name} />
        <McpToolMeta label={t("settings.mcpConnections.agentCardDescription")} value={card.description || "-"} />
        <McpToolMeta
          label={t("settings.mcpConnections.agentCardProtocolVersion")}
          value={card.protocolVersion ?? "-"}
          mono={Boolean(card.protocolVersion)}
        />
        {card.version ? (
          <McpToolMeta label={t("settings.mcpConnections.agentCardVersion")} value={card.version} mono />
        ) : null}
      </dl>
    </div>
  );
}

function McpToolsList({
  tools,
  trusted,
  protocol,
}: {
  tools: ExternalMcpToolInfo[];
  trusted: boolean;
  protocol: McpConnectionProtocol;
}) {
  // 表示件数（10 / 25 / 50 / 100。#1891）。接続ごとの一覧なので、開いている間だけ保つ。
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  // 取り直した一覧は別の結果なので、そのときと表示件数を変えたときだけ 1 ページ目へ戻す。
  const resetKey = useMemo(() => ({ tools, pageSize }), [tools, pageSize]);
  const { page, setPage, totalPages, pageItems, range } = usePagination(tools, pageSize, { resetKey });
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
      render: (tool) => <McpToolApprovalBadge tool={tool} trusted={trusted} protocol={protocol} />,
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
      <ListToolbar count={listTotal(tools.length)} testId="mcp-tools-toolbar" />
      {/* md 以上は表、md 未満はカード。どちらも同じページを出し、Pagination は 1 つにする。 */}
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
              <McpToolApprovalBadge tool={tool} trusted={trusted} protocol={protocol} />
            </div>
            <p className="mt-1 text-sm leading-6 text-fg-muted">{tool.description || "-"}</p>
            <dl className="mt-3 grid grid-cols-1 gap-2 text-xs">
              <McpToolMeta label={t("settings.mcpConnections.functionName")} value={tool.function_name ?? "-"} mono={Boolean(tool.function_name)} />
              <McpToolMeta label={t("settings.mcpDiscovery.inputSchema")} value={schemaSummary(tool.input_schema)} />
            </dl>
          </div>
        ))}
      </div>
      <Pagination
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        range={range}
        pageSize={pageSize}
        pageSizeOptions={PAGE_SIZE_OPTIONS}
        onPageSizeChange={setPageSize}
        labels={labels}
        testId="mcp-tools-pagination"
      />
    </div>
  );
}

function McpToolMeta({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  // 他の <dl> と同じく、見出し（dt）は副次の色・値（dd）は本文の色（#5522）。
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2">
      <dt className="text-fg-muted">{label}</dt>
      <dd className={`min-w-0 break-words text-fg${mono ? " font-mono" : ""}`}>{value}</dd>
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
  if (mode === "oci_signature") {
    return t("settings.mcpConnections.authOciSignature");
  }
  return t("settings.mcpServers.authNone");
}

/** 一覧・結果の詳細に出すプロトコルの短い名前（値の無い接続は MCP）。 */
function mcpProtocolLabel(protocol?: McpConnectionProtocol | null): string {
  return protocol === "a2a" ? t("settings.mcpConnections.protocolA2aShort") : t("settings.mcpConnections.protocolMcp");
}

/** 追加の画面の既定値の表示名（id → i18n。知らない id はそのまま出す）。 */
const MCP_PRESET_LABEL_KEYS: Record<string, I18nKey> = {
  aidp_agent: "settings.mcpConnections.preset.aidp_agent",
};

function mcpPresetLabel(preset: McpConnectionPreset): string {
  const key = MCP_PRESET_LABEL_KEYS[preset.id];
  return key ? t(key) : preset.id;
}

/** 標準の接続（RAG / NL2SQL）の URL を決める配備の環境変数（backend の `_PRODUCT_MCP_URL_ENV` と同じ。#1325）。 */
function productMcpUrlEnv(serverId: string): string {
  return serverId === "nl2sql" ? "AGENT_EXTERNAL_NL2SQL_MCP_URL" : "AGENT_EXTERNAL_RAG_MCP_URL";
}

function mcpSourceLabel(source: string): string {
  if (source === "builtin") return t("settings.mcpConnections.sourceBuiltin");
  if (source === "env") return t("settings.mcpConnections.sourceEnv");
  return t("settings.mcpConnections.sourceRuntime");
}

/**
 * MCP の URL の検証（backend の `_validate_mcp_url` と同じ規則・文言）。空は未設定として保存できる。
 * 資格情報（userinfo・資格情報らしい query）は「認証」の欄へ案内する（#1056）。
 */
function mcpUrlError(raw: string): string | null {
  const value = raw.trim();
  if (!value) return null;
  if (!/^https?:\/\/[^\s/]+/.test(value)) return t("validation.mcpUrl.scheme");
  const problem = mcpUrlCredentialProblem(value);
  if (!problem) return null;
  return problem.kind === "userinfo"
    ? t("validation.mcpUrl.userinfo")
    : t("validation.mcpUrl.secretQuery", { names: problem.names.join("・") });
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
                    // 一覧を読み取れていない間は作らない（messaging.md §3.6.1。#4222）。
                    disabled: dataLoadView(connections, { isEmpty: list.length === 0 }).blockCreate,
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
                />
              }
              count={connections.data ? listCount(list.length, visibleConnections.length, Boolean(mcpQuery.trim())) : undefined}
              testId="mcp-connection-list-toolbar"
            />
            <QueryState query={connections} loadingLabel={t("loading.mcpServers")} skeleton={<TableSkeleton columns={7} />}>
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
      presets={connections.data?.presets ?? []}
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

/** 外部接続（MCP / A2A）の全画面エディタ（A 型。`?id=new` / `?id=<接続 ID>`）。 */
function McpConnectionEditor({
  connection,
  presets,
  actions,
  readOnly,
  onBack,
  onSaved,
}: {
  connection?: McpConnectionSettings;
  /** 追加の画面で選べる既定値（AIDP を配備した構成だけ。#5932）。 */
  presets: McpConnectionPreset[];
  actions: EntityAction[];
  /** 変更の権限がない利用者は閲覧だけ（保存を出さず、入力を無効にする）。 */
  readOnly: boolean;
  onBack: () => void;
  onSaved: (serverId: string) => Promise<void>;
}) {
  const confirm = useConfirm();
  const [form, setForm] = useState<McpConnectionFormState>(() => mcpFormOf(connection));
  const [formBaseline, setFormBaseline] = useState<McpConnectionFormState>(() => mcpFormOf(connection));
  const [serverIdError, setServerIdError] = useState<string | null>(null);
  const [baseUrlError, setBaseUrlError] = useState<string | null>(null);
  const [agentCardUrlError, setAgentCardUrlError] = useState<string | null>(null);
  const [timeoutError, setTimeoutError] = useState<string | null>(null);
  // 保存した回数。保存し直したら、前の設定（秘密を含む）でのツールの取得の結果を消す（messaging.md §10.4。#1014）。
  const [savedCount, setSavedCount] = useState(0);
  const editingId = connection?.server_id ?? null;
  // RAG / NL2SQL は Run の利用者のサービストークンで呼ぶ標準の接続（名前・認証方式は変えられない）。
  const builtin = connection?.source === "builtin";
  // 配備（環境変数）が URL を決めた標準の接続は、URL も変えられない（#1325）。
  const urlLocked = Boolean(connection?.base_url_locked);
  // A2A の接続（外部の Agent。#5932）。標準の接続では Oracle AI Data Platform の Agent（`aidp`）。
  const a2a = form.protocol === "a2a";
  const builtinA2a = builtin && a2a;
  const preset = presets.find((candidate) => candidate.id === form.presetId) ?? null;
  // 標準の接続（AIDP の Agent）の URL の形は、配備の既定値（リージョンの入った例）があればそれを出す。
  const aidpUrlExample =
    presets.find((candidate) => candidate.id === "aidp_agent")?.base_url_example ??
    t("settings.mcpConnections.urlExampleAidp");
  const payloadContext: McpConnectionPayloadContext = { editingId, builtin, urlLocked, baseline: formBaseline };

  // 送る内容は mutate の引数で渡す（クリック直前の入力を closure の古い state で送らない）。
  const saveMutation = useMutation({
    mutationFn: (current: McpConnectionFormState) => {
      const payload = mcpConnectionPayload(current, payloadContext);
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
      setSavedCount((count) => count + 1);
      await onSaved(saved.server_id ?? current.serverId.trim());
    },
  });

  // 開いた時点の内容から変わっていれば未保存（秘密の欄も含む。値は保存しない）。#87
  const formDirty = !sameDraft(form, formBaseline);
  const { confirmClose } = useEditorLeaveGuard(formDirty, saveMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  // 注釈の信頼をオンにする前に、MCP の仕様の注意を確認のダイアログで示す（#1875）。A2A の接続では、
  // 相手の Agent を承認なしで呼ぶことの注意を示す（#5932）。オフにするのは安全側なので確かめない。
  async function changeTrustToolAnnotations(checked: boolean) {
    if (checked) {
      const ok = await confirm({
        title: t(
          a2a ? "settings.mcpConnections.trustA2aConfirmTitle" : "settings.mcpConnections.trustAnnotationsConfirmTitle"
        ),
        description: t(
          a2a
            ? "settings.mcpConnections.trustA2aConfirmDescription"
            : "settings.mcpConnections.trustAnnotationsConfirmDescription",
          {
            name:
              form.label.trim() || form.serverId.trim() || t("settings.mcpConnections.trustAnnotationsThisConnection"),
          }
        ),
        confirmLabel: t(
          a2a ? "settings.mcpConnections.trustA2aConfirm" : "settings.mcpConnections.trustAnnotationsConfirm"
        ),
        cancelLabel: t("common.cancel"),
        tone: "warning",
      });
      if (!ok) return;
    }
    setForm((current) => ({ ...current, trustToolAnnotations: checked }));
  }

  function baseUrlChanged(current: McpConnectionFormState): boolean {
    return mcpBaseUrlChanged(current, payloadContext);
  }

  // プロトコルを切り替える。今の認証方式が使えなくなったら「なし」に戻す（MCP は OCI 署名、A2A は
  // サービストークンを使えない。#5932）。
  function changeProtocol(protocol: McpConnectionProtocol) {
    setForm((current) => withMcpProtocol(current, protocol));
    setBaseUrlError(null);
    setAgentCardUrlError(null);
  }

  // 追加の画面の既定値（プロトコル・認証・タイムアウトと URL の雛形を入れる。#5932）。
  function changePreset(presetId: string) {
    const next = presets.find((candidate) => candidate.id === presetId) ?? null;
    setForm((current) => withMcpPreset(current, next, preset));
    setBaseUrlError(null);
    setTimeoutError(null);
  }

  function save() {
    const nextServerIdError =
      !editingId && !form.serverId.trim() ? t("settings.mcpServers.idRequired") : null;
    // 保存済みの（伏せて表示している）URL のままなら検証しない（変えた URL だけを送る）。
    const nextBaseUrlError = !urlLocked && baseUrlChanged(form) ? mcpUrlError(form.baseUrl) : null;
    // Agent card の URL は A2A の外部の接続だけ（保存済みの値のままなら検証しない）。
    const nextAgentCardUrlError =
      a2a && !builtin && (!editingId || form.agentCardUrl.trim() !== formBaseline.agentCardUrl.trim())
        ? mcpUrlError(form.agentCardUrl)
        : null;
    // 空のタイムアウトを 0 として保存しない。規則は backend（McpConnectionCreate / Patch）と同じ（#540）。
    const nextTimeoutError = numberFieldError(form.timeoutSeconds, {
      label: t("settings.timeout"),
      min: 0,
      exclusiveMin: true,
      max: MCP_TIMEOUT_MAX_SECONDS,
    });
    setServerIdError(nextServerIdError);
    setBaseUrlError(nextBaseUrlError);
    setAgentCardUrlError(nextAgentCardUrlError);
    setTimeoutError(nextTimeoutError);
    if (
      focusFirstInvalidField([
        ["mcp-server-id", nextServerIdError],
        ["mcp-server-base-url", nextBaseUrlError],
        ["mcp-server-agent-card-url", nextAgentCardUrlError],
        ["mcp-server-timeout", nextTimeoutError],
      ])
    ) {
      return;
    }
    saveMutation.mutate(form);
  }

  // 認証の選択肢はプロトコルで変わる（MCP は OCI 署名を出さず、A2A はサービストークンを出さない。#5932）。
  const authOptions: SelectFieldOption<McpAuthMode>[] = mcpAuthModesFor(form.protocol).map((mode) => ({
    value: mode,
    label: mcpAuthLabel(mode),
  }));
  const protocolOptions: SelectFieldOption<McpConnectionProtocol>[] = [
    { value: "mcp", label: t("settings.mcpConnections.protocolMcp") },
    { value: "a2a", label: t("settings.mcpConnections.protocolA2a") },
  ];
  const presetOptions: SelectFieldOption<string>[] = [
    { value: "", label: t("settings.mcpConnections.presetNone") },
    ...presets.map((candidate) => ({ value: candidate.id, label: mcpPresetLabel(candidate) })),
  ];
  // URL の欄の補足: 配備が決めた URL → 標準の接続（AIDP の Agent / RAG・NL2SQL）→ 外部の接続（A2A / MCP）。
  const urlHelper = urlLocked
    ? t("settings.mcpConnections.urlHintLocked", { env: productMcpUrlEnv(connection?.server_id ?? "") })
    : builtinA2a
      ? `${t("settings.mcpConnections.urlHintBuiltinA2a")}${t("settings.mcpConnections.urlHintExample", { example: aidpUrlExample })}`
      : builtin
        ? t("settings.mcpConnections.urlHintBuiltin", { env: productMcpUrlEnv(connection?.server_id ?? "") })
        : a2a
          ? `${t("settings.mcpConnections.urlHintA2a")}${preset ? t("settings.mcpConnections.urlHintExample", { example: preset.base_url_example }) : ""}`
          : t("settings.mcpConnections.urlHint");
  const listTitle = t("nav.settingsMcpConnections");
  const title = connection ? connection.label || connection.server_id : t("settings.mcpConnections.addTitle");

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={connection ? connection.server_id : t("page.settings.mcp.subtitle")}
        // 一覧へ戻るは左上（#618）。確定（作成・保存と変更を破棄）はフォームの末尾の FormActionBar（#1786）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: listTitle }), onClick: () => void back(), testId: "editor-back" }}
      />
      <PageBody wide className="space-y-6">
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
                <StatusBadge variant="neutral" label={mcpProtocolLabel(connection.protocol)} icon={false} />
                <span className="text-xs text-fg-muted">
                  {`${t("settings.mcpServers.auth")}: ${mcpAuthLabel(connection.auth_mode)}`}
                </span>
              </div>
              {builtin ? (
                <Banner severity="info" title={t("settings.mcpConnections.builtinTitle")}>
                  {builtinA2a
                    ? t("settings.mcpConnections.builtinA2a")
                    : urlLocked
                      ? t("settings.mcpConnections.builtinLocked")
                      : t("settings.mcpConnections.builtinUnlocked")}
                </Banner>
              ) : null}
              <McpConnectionNotice connection={connection} />
            </div>
          </Section>
        ) : null}
        {/* 保存の対象（接続・認証）を包み、その末尾に確定の行を置く（#1786）。 */}
        <div className="min-w-0 space-y-6">
          <fieldset disabled={readOnly} className="min-w-0 space-y-6">
            <Section title={t("mcpServers.connection")}>
              <Card className="min-w-0">
                <CardContent className="pt-5">
                  <FormGrid>
                    {/* 追加の画面の既定値（AIDP を配備した構成だけ。選ぶとプロトコル・認証・URL の雛形が入る。#5932）。 */}
                    {!editingId && presets.length ? (
                      <div className="col-span-full min-w-0">
                        <SelectField<string>
                          id="mcp-server-preset"
                          width="md"
                          label={t("settings.mcpConnections.preset")}
                          helper={t("settings.mcpConnections.presetHint")}
                          value={form.presetId}
                          options={presetOptions}
                          onValueChange={changePreset}
                        />
                      </div>
                    ) : null}
                    {/* 接続 ID は作成時だけ入力でき、必須。モデルに渡すツール名（<接続>__<ツール>）の先頭になる。 */}
                    <TextField
                      id="mcp-server-id"
                      width="md"
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
                      width="md"
                      label={t("settings.mcpServers.label")}
                      className="min-w-0"
                      helper={builtin ? t("settings.mcpConnections.labelHintBuiltin") : undefined}
                      disabled={builtin}
                      value={form.label}
                      onValueChange={(value) => setForm({ ...form, label: value })}
                    />
                    {/* プロトコル（#5932）。標準の接続は変えられない。A2A は外部の Agent を 1 つのツールにする。 */}
                    <div className="col-span-full min-w-0">
                      <SelectField<McpConnectionProtocol>
                        id="mcp-server-protocol"
                        width="md"
                        label={t("settings.mcpConnections.protocol")}
                        helper={t(
                          builtin ? "settings.mcpConnections.protocolHintBuiltin" : "settings.mcpConnections.protocolHint"
                        )}
                        value={form.protocol}
                        options={protocolOptions}
                        disabled={builtin}
                        onValueChange={changeProtocol}
                      />
                    </div>
                    <div className="col-span-full min-w-0">
                      <TextField
                        id="mcp-server-base-url"
                        width="full"
                        label={t(a2a ? "settings.mcpConnections.urlA2a" : "settings.mcpConnections.url")}
                        helper={[urlHelper, connection?.base_url_masked ? t("settings.mcpConnections.urlMaskedHint") : null]
                          .filter(Boolean)
                          .join("")}
                        error={baseUrlError ?? undefined}
                        disabled={urlLocked}
                        value={form.baseUrl}
                        onValueChange={(value) => {
                          setForm({ ...form, baseUrl: value });
                          setBaseUrlError(null);
                        }}
                      />
                    </div>
                    {/* Agent card の URL は任意（空なら endpoint から決まる）。標準の接続では endpoint から決まる。 */}
                    {a2a && !builtin ? (
                      <div className="col-span-full min-w-0">
                        <TextField
                          id="mcp-server-agent-card-url"
                          width="full"
                          label={t("settings.mcpConnections.agentCardUrl")}
                          helper={t("settings.mcpConnections.agentCardUrlHint")}
                          error={agentCardUrlError ?? undefined}
                          value={form.agentCardUrl}
                          onValueChange={(value) => {
                            setForm({ ...form, agentCardUrl: value });
                            setAgentCardUrlError(null);
                          }}
                        />
                      </div>
                    ) : null}
                    <TextField
                      id="mcp-server-timeout"
                      width="xs"
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
                    {/* セッション ID は MCP（streamable HTTP の gateway）だけ。 */}
                    {a2a ? null : (
                      <TextField
                        id="mcp-server-session"
                        width="md"
                        label={t("settings.mcpSessionId")}
                        className="min-w-0"
                        helper={t("settings.mcpSessionHint")}
                        value={form.sessionId}
                        autoComplete="off"
                        onValueChange={(value) => setForm({ ...form, sessionId: value })}
                      />
                    )}
                    {/* MCP: ツールの注釈（readOnlyHint）の信頼（#1832）。A2A: この接続の Agent を承認なしで呼ぶ（#5932）。
                        標準の接続は常にオン（変えられない）。 */}
                    <div className="col-span-full space-y-1" data-testid="mcp-trust-annotations">
                      <label className="inline-flex items-center gap-2 text-sm text-fg">
                        <Switch
                          checked={builtin || form.trustToolAnnotations}
                          disabled={builtin}
                          aria-label={t(a2a ? "settings.mcpConnections.trustA2a" : "settings.mcpConnections.trustAnnotations")}
                          aria-describedby="mcp-trust-annotations-helper"
                          onCheckedChange={(checked) => void changeTrustToolAnnotations(checked)}
                        />
                        {t(a2a ? "settings.mcpConnections.trustA2a" : "settings.mcpConnections.trustAnnotations")}
                      </label>
                      <p id="mcp-trust-annotations-helper" className="text-xs leading-5 text-fg-muted">
                        {t(
                          a2a
                            ? builtin
                              ? "settings.mcpConnections.trustA2aBuiltin"
                              : "settings.mcpConnections.trustA2aHelper"
                            : builtin
                              ? "settings.mcpConnections.trustAnnotationsBuiltin"
                              : "settings.mcpConnections.trustAnnotationsHelper"
                        )}
                      </p>
                    </div>
                  </FormGrid>
                </CardContent>
              </Card>
            </Section>
            <Section title={t("settings.mcpConnections.authSection")}>
              <Card className="min-w-0">
                <CardContent className="pt-5">
                  <FormGrid>
                    <SelectField<McpAuthMode>
                      id="mcp-server-auth-mode"
                      width="md"
                      label={t("settings.mcpServers.auth")}
                      value={form.authMode}
                      options={authOptions}
                      disabled={builtin}
                      helper={
                        builtinA2a
                          ? t("settings.mcpConnections.authBuiltinHintA2a")
                          : builtin
                            ? t("settings.mcpConnections.authBuiltinHint")
                            : undefined
                      }
                      onValueChange={(value) => setForm({ ...form, authMode: value })}
                    />
                    {form.authMode === "service_token" ? (
                      <TextField
                        id="mcp-server-audience"
                        width="md"
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
                        width="lg"
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
                          width="full"
                          label={t("settings.mcpServers.oauthTokenUrl")}
                          className="col-span-full min-w-0"
                          value={form.oauthTokenUrl}
                          onValueChange={(value) => setForm({ ...form, oauthTokenUrl: value })}
                        />
                        <TextField
                          id="mcp-server-oauth-scope"
                          width="md"
                          label={t("settings.mcpServers.oauthScope")}
                          className="min-w-0"
                          value={form.oauthScope}
                          onValueChange={(value) => setForm({ ...form, oauthScope: value })}
                        />
                        <TextField
                          id="mcp-server-oauth-client"
                          width="md"
                          label={t("settings.mcpServers.oauthClientId")}
                          className="min-w-0"
                          value={form.oauthClientId}
                          autoComplete="off"
                          onValueChange={(value) => setForm({ ...form, oauthClientId: value })}
                        />
                        <SecretField
                          id="mcp-server-oauth-secret"
                          width="lg"
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
                    {/* OCI 署名は接続ごとの資格情報を持たない（「システム設定 › OCI 認証」の API キーで署名する。#5932）。 */}
                    {form.authMode === "oci_signature" ? (
                      <p className="col-span-full text-sm leading-6 text-fg-muted" data-testid="mcp-oci-signature-hint">
                        {t("settings.mcpConnections.ociSignatureHint")}
                      </p>
                    ) : null}
                    {/* 保存済みの認証方式の準備の状態（入力中の認証方式が保存済みと同じときだけ）。 */}
                    {connection &&
                    form.authMode === connection.auth_mode &&
                    (form.authMode === "service_token" || form.authMode === "oci_signature") ? (
                      <div className="col-span-full min-w-0">
                        <McpSharedCredentialStatus connection={connection} />
                      </div>
                    ) : null}
                  </FormGrid>
                </CardContent>
              </Card>
            </Section>
          </fieldset>
          {/* 保存の失敗は確定の行の直上の 1 か所だけ（messaging.md §3.3.1。#585 / #1786）。 */}
          <SaveErrorBanner
            message={saveMutation.error ? (saveMutation.error as Error).message : null}
            attemptKey={saveMutation.submittedAt}
            testId="mcp-server-save-error"
          />
          {!readOnly ? (
            // 確定はフォームの末尾の左（作成・保存 → 変更を破棄）。最後のカードの下に置き、画面の下端に貼り付けない（#1786 / #2715）。
            <FormActionBar
              ariaLabel={t("editor.actions.formLabel")}
              testId="mcp-server-form-actions"
              primaryActions={[
                {
                  id: "save",
                  label: editingId ? t("common.save") : t("common.create"),
                  icon: Save,
                  loading: saveMutation.isPending,
                  onClick: save,
                },
              ]}
              secondaryActions={[
                {
                  id: "discard",
                  label: t("editor.actions.discard"),
                  icon: RotateCcw,
                  disabled: !formDirty || saveMutation.isPending,
                  onClick: () => {
                    setForm(formBaseline);
                    setServerIdError(null);
                    setBaseUrlError(null);
                    setAgentCardUrlError(null);
                    setTimeoutError(null);
                  },
                },
              ]}
            />
          ) : null}
        </div>
        {/* 保存した接続だけツールを取得できる（入力中の値ではなく保存済みの設定で呼ぶ）。 */}
        {connection ? (
          <McpConnectionToolsPanel key={`${connection.server_id}-${savedCount}`} connection={connection} />
        ) : null}
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
      render: (connection) => (connection.base_url ? <span className="font-mono">{connection.base_url}</span> : "-"),
    },
    {
      key: "protocol",
      header: t("settings.mcpConnections.protocol"),
      render: (connection) => (
        <StatusBadge variant="neutral" label={mcpProtocolLabel(connection.protocol)} icon={false} />
      ),
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
      tableClassName="w-full min-w-[54rem]"
      ariaLabel={t("settings.mcpConnections.title")}
      empty={empty}
    />
  );
}
