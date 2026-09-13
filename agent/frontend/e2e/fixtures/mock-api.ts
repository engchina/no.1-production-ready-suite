/**
 * e2e 用の API mock（hermetic）。
 *
 * e2e は実 backend を起動しない。実 backend は設定保存で利用者の実環境のファイル
 * （~/.oci/config、backend/.env、backend/model-settings.json 等）へ書き込むため、
 * すべての /api をこの fixture の page.route で応答する。
 *
 * - 既定値 `api-defaults.json` は backend の GET 応答（初期状態）と同じ形。
 * - 保存（POST / PATCH / DELETE）は in-memory の state を更新し、`mockApi.requests` に記録する。
 * - spec 固有の応答は spec 側で後から page.route を登録して上書きする（Playwright は後に登録した
 *   handler を先に評価する）。spec 側で扱わないリクエストは `route.fallback()` でこの fixture に渡し、
 *   `route.continue()`（ネットワークへ流す）は使わない。
 * - どの handler も扱わなかった /api は 404 で終端し、テスト終了時に失敗させる。
 */
import { expect, test as base, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";

type Json = Record<string, unknown>;

export const MOCK_NOW = "2026-06-28T00:00:00Z";

const defaults = JSON.parse(
  readFileSync(new URL("./api-defaults.json", import.meta.url), "utf-8")
) as Record<string, unknown>;

export interface RecordedRequest {
  method: string;
  path: string;
  searchParams: URLSearchParams;
  body: unknown;
}

/** 外部 MCP gateway の tools/list 相当（旧 external-tools-server.mjs の応答と同じ内容）。 */
const MOCK_MCP_TOOLS = [
  {
    name: "lookup_customer",
    description: "顧客情報を検索する",
    input_schema: { type: "object", properties: { customer_id: { type: "string" } } },
    output_schema: { type: "object" },
    metadata: { fixture: true },
  },
  {
    name: "search_orders",
    description: "受注を検索する",
    input_schema: {
      type: "object",
      properties: { account_id: { type: "string" }, limit: { type: "number" } },
    },
    output_schema: { type: "object" },
    metadata: { fixture: true },
  },
];

/** リモート marketplace の listing（旧 external-tools-server.mjs の /marketplace と同じ内容）。 */
const MOCK_MARKETPLACE_LISTING = {
  name: "Fixture Marketplace",
  plugins: [
    {
      id: "fixture_plugin",
      name: "Fixture Plugin",
      version: "1.0.0",
      description: "検証用 plugin(skill + MCP を束ねる)",
      skills: [
        {
          id: "fixture_plugin_skill",
          name: "Fixture Skill",
          tool_calls: [{ name: "agent_skill_list" }],
        },
      ],
      mcp_servers: [{ server_id: "fixture_plugin_mcp", base_url: "http://mcp.example.test/jsonrpc" }],
      resources: [],
    },
  ],
};

function clone<T>(value: T): T {
  return structuredClone(value);
}

function createState() {
  const d = clone(defaults) as Record<string, Json & Json[]>;
  return {
    health: d.health as Json,
    runtimes: d.runtimes as unknown as Json[],
    bindings: [] as Json[],
    runs: [] as Json[],
    agents: d.agents as unknown as Json[],
    skills: d.skills as unknown as Json[],
    tools: d.tools as unknown as Json[],
    memory: [] as Json[],
    plugins: [] as Json[],
    marketplaces: [] as Json[],
    observabilityStatus: d.observabilityStatus as Json,
    tracePolicy: d.tracePolicy as Json,
    runtimeSafety: d.runtimeSafety as Json,
    toolPolicy: d.toolPolicy as Json,
    commandPolicy: d.commandPolicy as Json,
    externalRag: d.externalRag as Json,
    externalNl2Sql: d.externalNl2Sql as Json,
    externalMcp: d.externalMcp as Json,
    externalMcpServers: d.externalMcpServers as Json as { servers: Json[]; default_server_id: string },
    modelSettings: d.modelSettings as Json,
    databaseSettings: d.databaseSettings as Json,
    adbInfo: d.adbInfo as Json,
    uploadStorage: d.uploadStorage as Json,
    ociSettings: d.ociSettings as Json,
  };
}

export type MockApiState = ReturnType<typeof createState>;

export interface MockApi {
  state: MockApiState;
  requests: RecordedRequest[];
  unmocked: string[];
  /** 条件に合う最後のリクエスト。保存 payload の検証に使う。 */
  lastRequest(method: string, path: string): RecordedRequest | undefined;
}

class HttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function pluginSummary(plugin: Json): Json {
  const summary = { ...plugin };
  delete summary.manifest;
  return summary;
}

function pluginRecord(manifest: Json, marketplaceId: string | null): Json {
  const skills = (manifest.skills as Json[] | undefined) ?? [];
  const mcpServers = (manifest.mcp_servers as Json[] | undefined) ?? [];
  const resources = (manifest.resources as Json[] | undefined) ?? [];
  return {
    id: manifest.id,
    name: manifest.name,
    version: manifest.version ?? "0.0.0",
    description: manifest.description ?? "",
    author: manifest.author ?? "",
    enabled: true,
    marketplace_id: marketplaceId,
    skill_count: skills.length,
    mcp_count: mcpServers.length,
    resource_count: resources.length,
    warnings: [],
    agent_count: 0,
    manifest,
  };
}

function skillFromPayload(payload: Json, source: string, current?: Json): Json {
  return {
    description: "",
    instructions: "",
    mcp_requirements: [],
    resource_ids: [],
    tool_calls: [],
    enabled: true,
    tags: [],
    created_at: MOCK_NOW,
    ...current,
    ...payload,
    source: current?.source ?? source,
    updated_at: MOCK_NOW,
  };
}

function mcpServer(payload: Json, current?: Json): Json {
  const merged: Json = { timeout_seconds: 10, label: null, ...current, ...payload };
  const baseUrl = (merged.base_url as string | null | undefined) ?? null;
  return {
    server_id: merged.server_id,
    label: merged.label ?? merged.server_id,
    base_url: baseUrl,
    api_key_configured: false,
    oauth_configured: false,
    auth_mode: "none",
    session_configured: Boolean(merged.session_id),
    timeout_seconds: merged.timeout_seconds,
    default_limit: null,
    configured: Boolean(baseUrl),
    is_default: false,
  };
}

function validateSnapshot(snapshot: Json) {
  const errors: string[] = [];
  const warnings: string[] = [];
  const agents = (snapshot.agents as Json[] | undefined) ?? [];
  const runs = (snapshot.runs as Json[] | undefined) ?? [];
  const memory = (snapshot.memory as Json[] | undefined) ?? [];
  if (
    !["agent-runtime.snapshot.v1", "agent-control-plane.snapshot.v2"].includes(
      String(snapshot.version)
    )
  ) {
    errors.push(`unsupported snapshot version: ${String(snapshot.version)}`);
  }
  const duplicates = (label: string, ids: unknown[]) => {
    const seen = new Set<unknown>();
    const dup = new Set<string>();
    for (const id of ids) {
      if (seen.has(id)) dup.add(String(id));
      seen.add(id);
    }
    if (dup.size) errors.push(`duplicate ${label} id: ${[...dup].sort().join(", ")}`);
  };
  duplicates("run", runs.map((run) => run.id));
  duplicates("agent", agents.map((agent) => agent.id));
  duplicates("memory", memory.map((entry) => entry.id));
  if (!agents.some((agent) => agent.id === "default")) {
    warnings.push("default agent is missing and will be recreated");
  }
  return {
    valid: errors.length === 0,
    errors,
    warnings,
    summary: {
      runs: runs.length,
      agents: agents.length,
      memory: memory.length,
      events: 0,
      steps: 0,
      approvals: 0,
      artifacts: 0,
      pending_tool_calls: 0,
    },
  };
}

function findOr404<T extends Json>(items: T[], key: string, id: string, label: string): T {
  const item = items.find((candidate) => candidate[key] === id);
  if (!item) throw new HttpError(404, `${label} not found: ${id}`);
  return item;
}

/** 1 リクエストを state に対して処理する。未対応は undefined を返す。 */
function handle(state: MockApiState, method: string, path: string, query: URLSearchParams, body: Json) {
  const segments = path.replace(/^\/api\/?/, "").split("/").map(decodeURIComponent);
  const [head, second, third] = segments;
  const at = (...parts: string[]) =>
    parts.length === segments.length && parts.every((part, index) => part === "*" || part === segments[index]);

  // --- health / observability ---
  if (method === "GET" && at("health")) return state.health;
  if (method === "GET" && at("observability", "status")) return state.observabilityStatus;

  // --- Runtime / Binding ---
  if (head === "runtimes") {
    if (method === "GET" && at("runtimes")) return { runtimes: state.runtimes };
    if (method === "GET" && at("runtimes", "*", "status")) {
      return findOr404(state.runtimes, "id", second, "runtime");
    }
  }
  if (head === "runtime-bindings" && method === "GET" && at("runtime-bindings")) {
    const agentId = query.get("agent_id");
    return {
      bindings: agentId ? state.bindings.filter((binding) => binding.agent_id === agentId) : state.bindings,
    };
  }

  // --- Run / 監査 ---
  if (method === "GET" && at("runs")) return { runs: state.runs };
  if (method === "GET" && at("audit", "tool-calls")) {
    return { total: 0, offset: 0, limit: 100, filters: {}, records: [] };
  }
  if (method === "GET" && at("tools")) return { tools: state.tools };
  if (method === "POST" && at("memory", "search")) return { entries: state.memory };

  // --- 業務 Agent ---
  if (head === "agents") {
    if (method === "GET" && at("agents")) return { agents: state.agents };
    if (method === "POST" && at("agents")) {
      const id = String(body.id ?? `agent-${state.agents.length + 1}`);
      if (state.agents.some((agent) => agent.id === id)) {
        throw new HttpError(400, `agent already exists: ${id}`);
      }
      const agent = {
        id,
        description: "",
        instructions: "",
        migration_required: false,
        tool_names: [],
        command_allowed_prefixes: [],
        source: "runtime",
        created_at: MOCK_NOW,
        updated_at: MOCK_NOW,
        ...body,
      };
      state.agents.push(agent);
      return agent;
    }
  }

  // --- Skill ---
  if (head === "skills") {
    if (method === "GET" && at("skills")) {
      return { skills: state.skills, metadata: { count: state.skills.length } };
    }
    if (method === "POST" && at("skills", "reload")) {
      return { skills: state.skills, metadata: { count: state.skills.length } };
    }
    if (method === "POST" && at("skills")) {
      const id = String(body.id ?? "");
      if (!id || state.skills.some((skill) => skill.id === id)) {
        throw new HttpError(409, `skill already exists: ${id}`);
      }
      const skill = skillFromPayload(body, "runtime");
      state.skills.push(skill);
      return skill;
    }
    if (at("skills", "*")) {
      const skill = findOr404(state.skills, "id", second, "skill");
      if (skill.source === "builtin" && method !== "GET") {
        throw new HttpError(409, `builtin skill cannot be modified: ${second}`);
      }
      if (method === "GET") return skill;
      if (method === "PATCH") {
        Object.assign(skill, skillFromPayload(body, "runtime", skill));
        return skill;
      }
      if (method === "DELETE") {
        state.skills = state.skills.filter((candidate) => candidate.id !== second);
        return { skills: state.skills, metadata: { count: state.skills.length } };
      }
    }
  }

  // --- Plugin / Marketplace ---
  if (head === "plugins") {
    const pluginList = () => ({
      plugins: state.plugins.map(pluginSummary),
      metadata: { count: state.plugins.length },
    });
    if (second === "marketplaces") {
      if (method === "GET" && at("plugins", "marketplaces")) return { marketplaces: state.marketplaces };
      if (method === "POST" && at("plugins", "marketplaces")) {
        const source = {
          id: body.id,
          name: body.name ?? body.id,
          url: body.url ?? null,
          plugin_count: 0,
          last_error: null,
        };
        state.marketplaces.push(source);
        return source;
      }
      if (third && at("plugins", "marketplaces", "*", "refresh") && method === "POST") {
        const source = findOr404(state.marketplaces, "id", third, "marketplace");
        source.plugin_count = MOCK_MARKETPLACE_LISTING.plugins.length;
        return source;
      }
      if (third && at("plugins", "marketplaces", "*", "plugins") && method === "GET") {
        const source = findOr404(state.marketplaces, "id", third, "marketplace");
        return source.plugin_count ? MOCK_MARKETPLACE_LISTING : { name: source.name, plugins: [] };
      }
      if (third && at("plugins", "marketplaces", "*") && method === "DELETE") {
        findOr404(state.marketplaces, "id", third, "marketplace");
        state.marketplaces = state.marketplaces.filter((source) => source.id !== third);
        return { marketplaces: state.marketplaces };
      }
    }
    if (method === "GET" && at("plugins")) return pluginList();
    if (method === "POST" && at("plugins", "reload")) return pluginList();
    if (method === "POST" && at("plugins")) {
      let manifest = body.manifest as Json | undefined;
      const marketplaceId = (body.marketplace_id as string | undefined) ?? null;
      if (marketplaceId) {
        findOr404(state.marketplaces, "id", marketplaceId, "marketplace");
        manifest = MOCK_MARKETPLACE_LISTING.plugins.find((plugin) => plugin.id === body.plugin_id);
      }
      if (!manifest?.id) throw new HttpError(400, "plugin manifest is required");
      if (state.plugins.some((plugin) => plugin.id === manifest.id)) {
        throw new HttpError(409, `plugin already installed: ${String(manifest.id)}`);
      }
      const record = pluginRecord(manifest, marketplaceId);
      state.plugins.push(record);
      for (const skill of (manifest.skills as Json[] | undefined) ?? []) {
        state.skills.push(skillFromPayload(skill, `plugin:${String(manifest.id)}`));
      }
      return record;
    }
    if (at("plugins", "*")) {
      const plugin = findOr404(state.plugins, "id", second, "plugin");
      if (method === "GET") return plugin;
      if (method === "PATCH") {
        plugin.enabled = Boolean(body.enabled);
        return plugin;
      }
      if (method === "DELETE") {
        state.plugins = state.plugins.filter((candidate) => candidate.id !== second);
        state.skills = state.skills.filter((skill) => skill.source !== `plugin:${second}`);
        return pluginList();
      }
    }
  }

  // --- Control Plane バックアップ ---
  if (method === "GET" && at("runtime", "snapshot")) {
    return {
      version: "agent-control-plane.snapshot.v2",
      exported_at: MOCK_NOW,
      runs: state.runs,
      agents: state.agents,
      memory: state.memory,
      control_plane_state: { runtimes: state.runtimes, bindings: state.bindings },
    };
  }
  if (method === "POST" && at("runtime", "snapshot", "import")) {
    const validation = validateSnapshot((body.snapshot as Json | undefined) ?? {});
    if (body.dry_run) {
      return { imported: false, dry_run: true, validation, reason: body.reason ?? null };
    }
    throw new HttpError(400, "e2e mock はスナップショットの置換を実装していません");
  }

  // --- 設定 ---
  if (head === "settings") {
    const patchable: Record<string, keyof MockApiState> = {
      "trace-policy": "tracePolicy",
      "runtime-safety": "runtimeSafety",
      "tool-policy": "toolPolicy",
      "command-policy": "commandPolicy",
    };
    if (at("settings", "*") && second in patchable) {
      const key = patchable[second];
      if (method === "GET") return state[key];
      if (method === "PATCH") {
        Object.assign(state[key] as Json, body);
        return state[key];
      }
    }
    const external: Record<string, "externalRag" | "externalNl2Sql" | "externalMcp"> = {
      "external-rag": "externalRag",
      "external-nl2sql": "externalNl2Sql",
      "external-mcp": "externalMcp",
    };
    if (method === "GET" && at("settings", "*") && second in external) return state[external[second]];

    if (second === "external-mcp-servers") {
      const servers = state.externalMcpServers;
      const serversData = () => ({
        servers: servers.servers.map((server) => ({
          ...server,
          is_default: server.server_id === servers.default_server_id,
        })),
        default_server_id: servers.default_server_id,
      });
      if (method === "GET" && at("settings", "external-mcp-servers")) return serversData();
      if (method === "POST" && at("settings", "external-mcp-servers")) {
        const id = String(body.server_id ?? "");
        if (!id || servers.servers.some((server) => server.server_id === id)) {
          throw new HttpError(409, `MCP server already exists: ${id}`);
        }
        const server = mcpServer(body);
        servers.servers.push(server);
        return server;
      }
      if (third && at("settings", "external-mcp-servers", "*", "default") && method === "POST") {
        findOr404(servers.servers, "server_id", third, "MCP server");
        servers.default_server_id = third;
        return serversData();
      }
      if (third && at("settings", "external-mcp-servers", "*")) {
        const server = findOr404(servers.servers, "server_id", third, "MCP server");
        if (method === "PATCH") {
          Object.assign(server, mcpServer({ ...body, server_id: third }, server));
          return server;
        }
        if (method === "DELETE") {
          servers.servers = servers.servers.filter((candidate) => candidate.server_id !== third);
          if (servers.default_server_id === third) servers.default_server_id = "default";
          return serversData();
        }
      }
    }

    if (at("settings", "model")) {
      if (method === "GET") return state.modelSettings;
      if (method === "PATCH") {
        const enterpriseAi = { ...(body.enterprise_ai as Json) };
        const hasApiKey = Boolean(enterpriseAi.api_key) && !enterpriseAi.clear_api_key;
        // backend と同じく API key の値は応答へ返さない。
        state.modelSettings = {
          ...state.modelSettings,
          settings: {
            enterprise_ai: { ...enterpriseAi, api_key: "", has_api_key: hasApiKey, clear_api_key: false },
            generative_ai: body.generative_ai,
          },
        };
        return state.modelSettings;
      }
    }
    if (at("settings", "database")) {
      if (method === "GET") return state.databaseSettings;
      if (method === "PATCH") {
        const { password, wallet_password: walletPassword, ...rest } = body;
        Object.assign(state.databaseSettings, rest, {
          has_password: Boolean(password) || state.databaseSettings.has_password,
          has_wallet_password: Boolean(walletPassword) || state.databaseSettings.has_wallet_password,
        });
        return state.databaseSettings;
      }
    }
    if (method === "GET" && at("settings", "database", "adb")) return state.adbInfo;
    if (method === "POST" && at("settings", "database", "adb", "settings")) {
      const adbOcid = String(body.adb_ocid ?? "").trim();
      state.databaseSettings.adb_ocid = adbOcid;
      state.databaseSettings.region = String(body.region ?? "").trim();
      state.adbInfo = {
        ...state.adbInfo,
        status: adbOcid ? "success" : "not_configured",
        message: adbOcid ? "ADB OCID を保存しました。" : "ADB OCID が未設定です。",
        id: adbOcid || null,
        lifecycle_state: adbOcid ? "AVAILABLE" : null,
        region: state.databaseSettings.region || null,
      };
      return state.adbInfo;
    }
    if (at("settings", "upload-storage")) {
      if (method === "GET") return state.uploadStorage;
      if (method === "PATCH") {
        Object.assign(state.uploadStorage, body);
        return state.uploadStorage;
      }
    }
    if (at("settings", "oci")) {
      if (method === "GET") return state.ociSettings;
      if (method === "PATCH") {
        Object.assign(state.ociSettings, {
          user: body.user ?? "",
          fingerprint: body.fingerprint ?? "",
          tenancy: body.tenancy ?? "",
          region: body.region ?? "",
          config_file_exists: true,
        });
        return state.ociSettings;
      }
    }
    if (method === "PATCH" && at("settings", "oci", "object-storage")) {
      Object.assign(state.uploadStorage, body);
      return state.uploadStorage;
    }
    if (method === "POST" && at("settings", "oci", "object-storage", "namespace")) {
      return { namespace: "mocktenancynamespace" };
    }
  }

  // --- 外部 MCP tools/list ---
  if (method === "GET" && at("tools", "external-mcp")) {
    const serverId = query.get("server_id") || state.externalMcpServers.default_server_id;
    const server = findOr404(state.externalMcpServers.servers, "server_id", serverId, "MCP server");
    if (!server.base_url) throw new HttpError(409, "external MCP is not configured");
    return {
      tools: MOCK_MCP_TOOLS.map((tool) => ({ ...tool, server_id: serverId })),
      metadata: { server_id: serverId, trace_id: query.get("trace_id") },
    };
  }

  return undefined;
}

async function fulfillJson(route: Route, status: number, payload: unknown) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });
}

export async function installMockApi(page: Page): Promise<MockApi> {
  const mockApi: MockApi = {
    state: createState(),
    requests: [],
    unmocked: [],
    lastRequest(method, path) {
      return [...this.requests]
        .reverse()
        .find((request) => request.method === method && request.path === path);
    },
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    let body: unknown;
    try {
      body = request.postDataJSON();
    } catch {
      body = request.postData();
    }
    mockApi.requests.push({ method, path: url.pathname, searchParams: url.searchParams, body });
    try {
      const data = handle(
        mockApi.state,
        method,
        url.pathname,
        url.searchParams,
        (body && typeof body === "object" ? body : {}) as Json
      );
      if (data === undefined) {
        mockApi.unmocked.push(`${method} ${url.pathname}${url.search}`);
        await fulfillJson(route, 404, {
          data: null,
          error_messages: [`e2e unmocked API: ${method} ${url.pathname}`],
          warning_messages: [],
        });
        return;
      }
      await fulfillJson(route, 200, { data, error_messages: [], warning_messages: [] });
    } catch (error) {
      if (error instanceof HttpError) {
        await fulfillJson(route, error.status, { detail: error.message });
        return;
      }
      throw error;
    }
  });

  // Run のイベント購読（WebSocket）も実 backend へ接続しない。接続は開いたまま何も送らない。
  await page.routeWebSocket(/\/api\//, () => {});

  return mockApi;
}

export const test = base.extend<{ mockApi: MockApi }>({
  mockApi: [
    async ({ page }, use) => {
      const mockApi = await installMockApi(page);
      await use(mockApi);
      expect(mockApi.unmocked, "e2e/fixtures/mock-api.ts が扱っていない API が呼ばれました").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
