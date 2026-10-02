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

import {
  ACCESS_TARGETS,
  LOCAL_CURRENT_USER,
  OPERATOR_ROLE,
  PERMISSION_CATALOG,
  SECURITY_USERS,
  SYSTEM_ADMIN_ROLE,
  expandPermissions,
  type CurrentUserPayload,
} from "./auth";

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
  /** 小文字の header 名 → 値（CSRF header の検証に使う）。 */
  headers: Record<string, string>;
}

/** `POST /api/auth/login` で受け付ける利用者（spec が `mockApi.state.auth.accounts` に足す）。 */
export interface MockAccount {
  password: string;
  user: CurrentUserPayload;
}

/** MCP 接続の tools/list 相当（`GET /api/settings/mcp-connections/{id}/tools`。#757）。 */
const MOCK_MCP_TOOLS = [
  {
    name: "lookup_customer",
    description: "顧客情報を検索する",
    input_schema: { type: "object", properties: { customer_id: { type: "string" } } },
    output_schema: { type: "object" },
    read_only: true,
    metadata: { fixture: true },
  },
  {
    name: "update_order",
    description: "受注を更新する",
    input_schema: {
      type: "object",
      properties: { order_id: { type: "string" }, status: { type: "string" } },
    },
    output_schema: { type: "object" },
    read_only: false,
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
          instructions: "Skill の一覧を確かめる。",
        },
      ],
      mcp_servers: [{ server_id: "fixture_plugin_mcp", base_url: "http://mcp.example.test/jsonrpc" }],
      resources: [],
    },
  ],
};

type SystemTableRow = { name: string; object_type: string; exists: boolean };

const SYSTEM_TABLE_OBJECTS: Array<[string, string]> = [
  ["AGENT_SCHEMA_OPERATIONS", "TABLE"],
  ["AGENT_SCHEMA_MIGRATIONS", "TABLE"],
  ["AGENT_ROLE_PERMISSIONS", "TABLE"],
  ["AGENT_ROLE_AGENTS", "TABLE"],
  ["AGENT_ROLE_AGENTS_AGENT_IDX", "INDEX"],
];
const SYSTEM_TABLE_MIGRATIONS = [
  "20261002_001_role_access",
  "20261002_002_remove_retired_permission_codes",
  "20261002_003_retire_role_business_views",
];

/** `GET /api/settings/database/system-tables` の応答（backend の `app.system_schema` と同じ形。#751）。 */
function systemTablesStatus(ready: boolean): Record<string, unknown> {
  const exists = (name: string) => ready || !name.startsWith("AGENT_SCHEMA") && name !== "AGENT_ROLE_AGENTS_AGENT_IDX";
  const objects: Array<SystemTableRow & Record<string, unknown>> = SYSTEM_TABLE_OBJECTS.map(([name, object_type]) => ({
    name,
    object_type,
    exists: exists(name),
    estimated_rows: exists(name) && object_type === "TABLE" ? 0 : null,
    created_at: exists(name) ? MOCK_NOW : null,
    last_analyzed_at: null,
  }));
  return {
    status: ready ? "ready" : "partial",
    schema_head: SYSTEM_TABLE_MIGRATIONS.at(-1),
    applied_versions: ready ? SYSTEM_TABLE_MIGRATIONS : [],
    pending_versions: ready ? [] : SYSTEM_TABLE_MIGRATIONS,
    pending_destructive_migrations: ready
      ? []
      : [
          {
            name: "20261002_003_retire_role_business_views",
            description:
              "権限管理でロールに割り当てていた業務ビュー（AGENT_ROLE_BUSINESS_VIEWS）を削除します。#750 から使っていません。",
          },
        ],
    expected_object_count: objects.length,
    existing_object_count: objects.filter((item) => item.exists).length,
    expected_table_count: 4,
    existing_table_count: objects.filter((item) => item.exists && item.object_type === "TABLE").length,
    missing_objects: objects.filter((item) => !item.exists).map(({ name, object_type }) => ({ name, object_type })),
    retired_objects: ready ? [] : [{ name: "AGENT_ROLE_BUSINESS_VIEWS", object_type: "TABLE" }],
    missing_foreign_keys: [],
    orphaned_foreign_keys: [],
    mismatched_foreign_keys: [],
    disabled_foreign_keys: [],
    tables: objects.filter((item) => item.object_type === "TABLE").map(({ object_type: _type, ...table }) => table),
    objects,
    operation_state: {
      status: "idle",
      operation_kind: null,
      lease_expires_at: null,
      last_error_code: null,
      schema_epoch: ready ? 1 : 0,
      updated_at: ready ? MOCK_NOW : null,
    },
  };
}

const SYSTEM_TABLES_LEGACY = systemTablesStatus(false);
const SYSTEM_TABLES_READY = systemTablesStatus(true);

/** `GET /api/runtime/status`（組み込み Runtime。#754）の既定の応答。 */
export const BUILTIN_RUNTIME_STATUS = {
  id: "builtin",
  name: "組み込み Runtime",
  sdk: "openai-agents",
  sdk_version: "0.22.3",
  model_provider: "OCI Enterprise AI（Responses API）",
  model_id: "xai.grok-4",
  ready: true,
  error_code: null,
  message: null,
  models: [
    { model_id: "xai.grok-4", display_name: "Grok 4" },
    { model_id: "openai.gpt-oss-120b", display_name: "gpt-oss-120b" },
  ],
};

/** 応答に出す評価（mock の内部の数 `_polls` を除く）。 */
function publicJob(job: Json): Json {
  return clone(Object.fromEntries(Object.entries(job).filter(([key]) => key !== "_polls")));
}

/** 評価セットの入力を保存する形にする（id を省いたケースは `case-<番号>`）。 */
function normalizedSet(body: Json): Json {
  return {
    agent_id: body.agent_id,
    name: body.name,
    description: body.description ?? "",
    cases: ((body.cases as Json[] | undefined) ?? []).map((item, index) => ({
      id: (item.id as string | undefined) || `case-${index + 1}`,
      question: item.question,
      expected: item.expected,
      expected_tools: item.expected_tools ?? [],
    })),
  };
}

/** 評価の概要（backend の `summarize` と同じ数え方）。 */
export function evaluationSummary(results: Json[]): Json {
  const judged = results.filter((item) => item.status === "judged" && item.judgement);
  const verdict = (name: string) => judged.filter((item) => (item.judgement as Json).verdict === name).length;
  const completed = results.filter((item) => item.status !== "pending" && item.status !== "running").length;
  const cancelled = results.filter((item) => item.status === "cancelled").length;
  const errors = results.filter((item) =>
    ["run_failed", "needs_approval", "timed_out", "judge_failed"].includes(String(item.status))
  ).length;
  const scores = judged.map((item) => Number((item.judgement as Json).score));
  const finished = completed - cancelled;
  const toolResults = results.filter((item) => item.tool_selection_correct !== null && item.tool_selection_correct !== undefined);
  const toolCorrect = toolResults.filter((item) => item.tool_selection_correct === true).length;
  return {
    total: results.length,
    completed,
    correct: verdict("correct"),
    incorrect: verdict("incorrect"),
    uncertain: verdict("uncertain"),
    errors,
    pass_rate: finished > 0 ? verdict("correct") / finished : null,
    average_score: scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null,
    tool_cases: toolResults.length,
    tool_correct: toolCorrect,
    tool_accuracy: toolResults.length ? toolCorrect / toolResults.length : null,
  };
}

/** 実行中の評価を完了にする（質問に「売上」を含むケースは誤り、それ以外は正しい）。 */
function finishEvaluation(job: Json) {
  for (const [index, result] of (job.results as Json[]).entries()) {
    const question = String((result.case as Json).question);
    const wrong = question.includes("売上");
    result.status = "judged";
    result.run_id = `run-eval-${index + 1}`;
    result.answer = wrong ? "分かりません。" : "毎月 25 日です。過ぎた分は翌月の精算になります。";
    result.judgement = wrong
      ? { verdict: "incorrect", score: 0, summary: "金額を答えていません。", missing_points: ["今月の売上の合計金額"] }
      : { verdict: "correct", score: 1, summary: "要点を満たしています。", missing_points: [] };
    result.duration_ms = 4200;
    const expectedTools = ((result.case as Json).expected_tools as string[] | undefined) ?? [];
    result.tool_calls = wrong ? [] : ["rag__rag_search"];
    result.tool_selection_correct = expectedTools.length
      ? expectedTools.every((tool) => (result.tool_calls as string[]).some((called) => called === tool || called.endsWith(`__${tool}`)))
      : null;
  }
  job.status = "completed";
  job.finished_at = MOCK_NOW;
  job.summary = evaluationSummary(job.results as Json[]);
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function createState() {
  const d = clone(defaults) as Record<string, Json & Json[]>;
  return {
    health: d.health as Json,
    // 組み込み Runtime の状態（#754）。既定は実行できる状態。
    runtimeStatus: clone(BUILTIN_RUNTIME_STATUS) as Json,
    runs: [] as Json[],
    agents: d.agents as unknown as Json[],
    skills: d.skills as unknown as Json[],
    tools: d.tools as unknown as Json[],
    // 監査の記録（`GET /api/audit/tool-calls`）。offset / limit で切り出して返す（#265）。
    auditRecords: [] as Json[],
    // 品質評価（#776）。新しい順。作成直後は実行中で、`pollsUntilDone` 回の取得の後に完了する。
    evaluations: [] as Json[],
    evaluationPollsUntilDone: 1,
    // 評価セット（#776）と、Excel の取り込み（parse-xlsx）が返すケース。
    evaluationSets: [] as Json[],
    parsedCases: [
      { id: "from-excel-1", question: "Excel の質問 1", expected: "Excel の要点 1", expected_tools: ["rag_search"] },
      { id: "from-excel-2", question: "Excel の質問 2", expected: "Excel の要点 2", expected_tools: [] },
    ] as Json[],
    // API キー（`/api/settings/api-keys`。#778）。`apiKeysPersistent` が false なら保存先が無い。
    apiKeys: [] as Json[],
    apiKeysPersistent: true,
    plugins: [] as Json[],
    marketplaces: [] as Json[],
    tracePolicy: d.tracePolicy as Json,
    toolPolicy: d.toolPolicy as Json,
    // MCP 接続（RAG / NL2SQL / 外部 MCP。#757）。
    mcpConnections: d.mcpConnections as Json as { connections: Json[] },
    modelSettings: d.modelSettings as Json,
    databaseSettings: d.databaseSettings as Json,
    adbInfo: d.adbInfo as Json,
    uploadStorage: d.uploadStorage as Json,
    ociSettings: d.ociSettings as Json,
    // システムテーブルの状態（#751）。既定は旧版の DB（業務ビューの表の削除を承認する前）。
    systemTables: clone(SYSTEM_TABLES_LEGACY) as Json,
    // DB の状態（`GET /api/ready/database`。DB ゲートが使う。#325）。既定は使える状態。
    databaseStatus: {
      status: "ok",
      check: "ok",
      detail: null,
      context_id: "e2e-context",
      schema_status: null,
      adb_lifecycle_state: null,
    } as Json,
    // 認証（#215）。既定はローカルの全権限の利用者（ログインなし）。null は未ログイン（me が 401）。
    auth: {
      currentUser: clone(LOCAL_CURRENT_USER) as CurrentUserPayload | null,
      accounts: {} as Record<string, MockAccount>,
    },
    security: {
      users: clone(SECURITY_USERS),
      roles: [clone(SYSTEM_ADMIN_ROLE), clone(OPERATOR_ROLE)],
      accessTargets: clone(ACCESS_TARGETS) as { agents: Json[] },
    },
  };
}

export type MockApiState = ReturnType<typeof createState>;

export interface MockApi {
  state: MockApiState;
  requests: RecordedRequest[];
  unmocked: string[];
  /** 条件に合う最後のリクエスト。保存 payload の検証に使う。 */
  lastRequest(method: string, path: string): RecordedRequest | undefined;
  /** ログイン中の利用者を差し替える（null で未ログイン = 401）。 */
  setCurrentUser(user: CurrentUserPayload | null): void;
}

class HttpError extends Error {
  readonly status: number;
  readonly errorCode?: string;

  constructor(status: number, message: string, errorCode?: string) {
    super(message);
    this.status = status;
    this.errorCode = errorCode;
  }
}

function findRole(state: MockApiState, roleId: string): Json {
  return findOr404(state.security.roles, "role_id", roleId, "role");
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
    enabled: true,
    tags: [],
    created_at: MOCK_NOW,
    ...current,
    ...payload,
    source: current?.source ?? source,
    updated_at: MOCK_NOW,
  };
}

/** MCP 接続の公開設定（backend の `_mcp_connection_settings` と同じ形。資格情報の値は返さない）。 */
function mcpConnection(payload: Json, current?: Json): Json {
  const merged: Json = {
    timeout_seconds: 10,
    label: null,
    auth_mode: "none",
    source: "runtime",
    removable: true,
    api_key_configured: false,
    oauth_configured: false,
    session_configured: false,
    service_token_configured: false,
    service_user_configured: false,
    ...current,
    ...payload,
  };
  const baseUrl = typeof merged.base_url === "string" && merged.base_url ? merged.base_url : null;
  const apiKeyConfigured = Boolean(payload.api_key) || Boolean(current?.api_key_configured);
  const oauthConfigured =
    Boolean(payload.oauth_token_url && payload.oauth_client_id && payload.oauth_client_secret) ||
    Boolean(current?.oauth_configured);
  const mode = String(merged.auth_mode);
  const ready =
    mode === "none" ||
    (mode === "api_key" && apiKeyConfigured) ||
    (mode === "oauth_client_credentials" && oauthConfigured) ||
    (mode === "service_token" && Boolean(merged.service_token_configured));
  return {
    server_id: merged.server_id,
    label: merged.label ?? null,
    base_url: baseUrl,
    auth_mode: mode,
    service_audience: mode === "service_token" ? merged.service_audience || merged.server_id : null,
    timeout_seconds: merged.timeout_seconds,
    source: merged.source,
    removable: merged.source === "runtime",
    configured: Boolean(baseUrl) && ready,
    api_key_configured: apiKeyConfigured,
    oauth_configured: oauthConfigured,
    session_configured: Boolean(payload.session_id) || Boolean(current?.session_configured),
    service_token_configured: Boolean(merged.service_token_configured),
    service_user_configured: Boolean(merged.service_user_configured),
  };
}

function validateSnapshot(snapshot: Json) {
  const errors: string[] = [];
  const warnings: string[] = [];
  const agents = (snapshot.agents as Json[] | undefined) ?? [];
  const runs = (snapshot.runs as Json[] | undefined) ?? [];
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

function accessTargetPage(items: Json[], query: URLSearchParams) {
  const q = (query.get("q") ?? "").toLowerCase();
  const ids = query.getAll("ids");
  const limit = Number(query.get("limit") ?? "50");
  const offset = Number(query.get("offset") ?? "0");
  const matched = items.filter(
    (item) =>
      (ids.length === 0 || ids.includes(item.id as string)) &&
      (!q || [item.id, item.name, item.description ?? ""].join(" ").toLowerCase().includes(q)),
  );
  return {
    items: matched.slice(offset, offset + limit),
    total: matched.length,
    limit,
    offset,
    has_next: offset + limit < matched.length,
  };
}

/** 1 リクエストを state に対して処理する。未対応は undefined を返す。 */
function handle(state: MockApiState, method: string, path: string, query: URLSearchParams, body: Json) {
  const segments = path.replace(/^\/api\/?/, "").split("/").map(decodeURIComponent);
  const [head, second, third] = segments;
  const at = (...parts: string[]) =>
    parts.length === segments.length && parts.every((part, index) => part === "*" || part === segments[index]);

  // --- 認証（platform の共通認証。#215） ---
  if (head === "auth") {
    if (method === "GET" && at("auth", "me")) {
      if (!state.auth.currentUser) throw new HttpError(401, "ログインが必要です。", "auth.unauthenticated");
      return state.auth.currentUser;
    }
    if (method === "POST" && at("auth", "login")) {
      const account = state.auth.accounts[String(body.login_user_id ?? "")];
      if (!account || account.password !== body.password) {
        throw new HttpError(401, "ログインユーザーIDまたはパスワードを確認してください。", "auth.invalid_credentials");
      }
      state.auth.currentUser = clone(account.user);
      return state.auth.currentUser;
    }
    if (method === "POST" && at("auth", "logout")) {
      state.auth.currentUser = null;
      return { logged_out: true };
    }
    if (method === "POST" && at("auth", "password", "change")) {
      // 変更後はセッションを失効させ、新しいパスワードでのログインを求める（backend と同じ）。
      state.auth.currentUser = null;
      return { changed: true };
    }
  }
  if (head === "security") {
    if (method === "GET" && at("security", "users")) return state.security.users;
    if (method === "GET" && at("security", "roles")) {
      const includeArchived = query.get("include_archived") === "true";
      return state.security.roles.filter((role) => includeArchived || !role.archived);
    }
    if (method === "GET" && at("security", "permissions")) return PERMISSION_CATALOG;
    // 権限管理の対象の候補（#608）: backend と同じく q（名前・ID・説明）・ids・limit / offset で絞った Page を返す。
    if (method === "GET" && at("security", "access-targets", "agents")) {
      return accessTargetPage(state.security.accessTargets.agents, query);
    }
    if (method === "PUT" && at("security", "roles", "*", "access")) {
      const role = findRole(state, third);
      if (body.version !== role.version) throw new HttpError(409, "ロールが更新されています。再読み込みしてください。");
      const grantsAll = expandPermissions((body.permissions as string[]) ?? []).includes("agent.admin");
      Object.assign(role, {
        version: (role.version as number) + 1,
        permissions: [...((body.permissions as string[]) ?? [])].sort(),
        // agent.admin を含むロールは対象を空に正規化する（backend と同じ）。
        agent_ids: grantsAll ? [] : [...((body.agent_ids as string[]) ?? [])].sort(),
      });
      return role;
    }
  }

  // --- health ---
  if (method === "GET" && at("health")) return state.health;
  if (method === "GET" && at("ready", "database")) return state.databaseStatus;
  // システムテーブル（#751）。作成・更新は承認（allow_destructive）が無ければ 409（backend と同じ）。
  if (method === "GET" && at("settings", "database", "system-tables")) return state.systemTables;
  if (method === "POST" && at("settings", "database", "system-tables", "initialize")) {
    const current = state.systemTables as Json;
    const destructive = (current.pending_destructive_migrations as Json[] | undefined) ?? [];
    if (!body.recreate && destructive.length > 0 && body.allow_destructive !== true) {
      throw new HttpError(409, "データを削除する未適用の migration があります。内容を確認して承認してください。");
    }
    state.systemTables = clone(SYSTEM_TABLES_READY) as Json;
    return { ...(state.systemTables as Json), operation: body.recreate ? "recreated" : "migrated", dropped_object_count: 1, created_object_count: 3 };
  }

  // --- 組み込み Runtime（#754） ---
  if (method === "GET" && at("runtime", "status")) return state.runtimeStatus;

  // --- Run / 承認 / 監査 ---
  if (method === "GET" && at("runs")) return { runs: state.runs };
  if (head === "runs" && second) {
    const run = findOr404(state.runs, "id", second, "run");
    if (method === "GET" && at("runs", "*", "audit")) {
      return { run_id: run.id, goal: run.goal, status: run.status, records: [] };
    }
    if (method === "GET" && at("runs", "*", "artifacts")) return { artifacts: [] };
    if (method === "POST" && at("runs", "*", "cancel")) {
      run.status = "cancelled";
      return run;
    }
    if (method === "POST" && at("runs", "*", "resume")) return run;
    if (method === "POST" && at("runs", "*", "replay")) {
      const replay = { ...clone(run), id: `${String(run.id)}-replay-${state.runs.length}`, status: "completed" };
      state.runs.unshift(replay);
      return replay;
    }
  }
  if (method === "POST" && at("approvals", "*", "decision")) {
    for (const run of state.runs) {
      const approval = ((run.approvals as Json[] | undefined) ?? []).find((candidate) => candidate.id === second);
      if (approval) {
        approval.status = body.approved ? "approved" : "rejected";
        approval.decided_by = body.decided_by ?? null;
        return run;
      }
    }
    throw new HttpError(404, `approval not found: ${second}`);
  }
  // --- 品質評価（#776） ---
  // --- 評価セット（#776） ---
  if (method === "GET" && at("evaluation-sets")) {
    const agentId = query.get("agent_id");
    return {
      sets: state.evaluationSets
        .filter((item) => !agentId || item.agent_id === agentId)
        .map((item) => {
          const latest = state.evaluations.find((job) => job.set_id === item.id);
          return {
            id: item.id,
            agent_id: item.agent_id,
            name: item.name,
            description: item.description,
            case_count: (item.cases as Json[]).length,
            updated_at: item.updated_at,
            last_job_id: latest?.id ?? null,
            last_job_status: latest?.status ?? null,
            last_pass_rate: latest ? ((latest.summary as Json).pass_rate ?? null) : null,
          };
        }),
    };
  }
  if (method === "POST" && at("evaluation-sets", "parse-xlsx")) return { cases: state.parsedCases };
  if (method === "POST" && at("evaluation-sets")) {
    const item: Json = {
      ...normalizedSet(body),
      id: `evset-${state.evaluationSets.length + 1}`,
      created_by_user_uuid: "local",
      created_at: MOCK_NOW,
      updated_at: MOCK_NOW,
    };
    state.evaluationSets.unshift(item);
    return item;
  }
  if (head === "evaluation-sets" && second) {
    const item = findOr404(state.evaluationSets, "id", second, "evaluation set");
    if (method === "GET" && at("evaluation-sets", "*")) return item;
    if (method === "PUT" && at("evaluation-sets", "*")) {
      Object.assign(item, normalizedSet(body), { updated_at: MOCK_NOW });
      return item;
    }
    if (method === "DELETE" && at("evaluation-sets", "*")) {
      state.evaluationSets = state.evaluationSets.filter((candidate) => candidate !== item);
      return null;
    }
  }
  // --- 品質評価（#776） ---
  if (method === "POST" && at("evaluations")) {
    if (state.evaluations.some((job) => job.status === "running")) {
      throw new HttpError(409, "ほかの評価を実行しています。終わってから始めてください。");
    }
    const evaluationSet = findOr404(state.evaluationSets, "id", String(body.set_id), "evaluation set");
    const cases = evaluationSet.cases as Json[];
    const job: Json = {
      id: `eval-${state.evaluations.length + 1}`,
      agent_id: evaluationSet.agent_id,
      agent_name: evaluationSet.agent_id === "default" ? "汎用業務 Agent" : String(evaluationSet.agent_id),
      set_id: evaluationSet.id,
      set_name: evaluationSet.name,
      status: "running",
      created_by_user_uuid: "local",
      results: cases.map((item) => ({
        case: item,
        status: "pending",
        run_id: null,
        answer: "",
        judgement: null,
        tool_calls: [],
        tool_selection_correct: null,
        error: null,
        duration_ms: null,
      })),
      error: null,
      summary: evaluationSummary([]),
      created_at: MOCK_NOW,
      started_at: MOCK_NOW,
      finished_at: null,
      previous_job_id: null,
      previous_summary: null,
      _polls: 0,
    };
    job.summary = evaluationSummary(job.results as Json[]);
    const previous = state.evaluations.find((item) => item.set_id === evaluationSet.id && item.status === "completed");
    if (previous) {
      job.previous_job_id = previous.id;
      job.previous_summary = previous.summary;
    }
    state.evaluations.unshift(job);
    return publicJob(job);
  }
  if (method === "GET" && at("evaluations")) {
    return {
      jobs: state.evaluations.map((job) => ({
        id: job.id,
        agent_id: job.agent_id,
        agent_name: job.agent_name,
        set_id: job.set_id ?? "",
        set_name: job.set_name ?? "",
        status: job.status,
        summary: job.summary,
        created_at: job.created_at,
        finished_at: job.finished_at,
      })),
    };
  }
  if (head === "evaluations" && second) {
    const job = findOr404(state.evaluations, "id", second, "evaluation");
    if (method === "GET" && at("evaluations", "*")) {
      if (job.status === "running") {
        job._polls = Number(job._polls) + 1;
        if (Number(job._polls) > state.evaluationPollsUntilDone) finishEvaluation(job);
      }
      return publicJob(job);
    }
    if (method === "POST" && at("evaluations", "*", "cancel")) {
      job.status = "cancelled";
      for (const result of job.results as Json[]) {
        if (result.status === "pending" || result.status === "running") result.status = "cancelled";
      }
      job.summary = evaluationSummary(job.results as Json[]);
      return publicJob(job);
    }
    if (method === "DELETE" && at("evaluations", "*")) {
      if (job.status === "running") throw new HttpError(409, "実行中の評価は削除できません。");
      state.evaluations = state.evaluations.filter((item) => item !== job);
      return null;
    }
  }
  if (method === "GET" && at("audit", "tool-calls")) {
    const offset = Number(query.get("offset") ?? 0);
    const limit = Number(query.get("limit") ?? 100);
    return {
      total: state.auditRecords.length,
      offset,
      limit,
      filters: {},
      records: state.auditRecords.slice(offset, offset + limit),
    };
  }
  if (method === "GET" && at("tools")) return { tools: state.tools };

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
        source: "runtime",
        created_at: MOCK_NOW,
        updated_at: MOCK_NOW,
        ...body,
      };
      state.agents.push(agent);
      return agent;
    }
    if (method === "PATCH" && at("agents", "*")) {
      const agent = findOr404(state.agents, "id", second, "agent");
      Object.assign(agent, body, { updated_at: MOCK_NOW });
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
      control_plane_state: {},
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
  if (head === "settings" && second === "api-keys") {
    if (method === "GET" && at("settings", "api-keys")) {
      return { keys: state.apiKeys, persistent: state.apiKeysPersistent };
    }
    if (method === "POST" && at("settings", "api-keys")) {
      const id = `${(state.apiKeys.length + 1).toString(16).padStart(16, "0")}`;
      const token = `prak_${id}_${"x".repeat(43)}`;
      const days = body.expires_in_days as number | null;
      const key: Json = {
        id,
        name: body.name,
        owner_user_uuid: (body.run_as_user_uuid as string | null) ?? "local",
        owner_display_name: body.run_as_user_uuid
          ? String(
              (state.security.users as Json[]).find((user) => user.user_uuid === body.run_as_user_uuid)
                ?.display_name ?? body.run_as_user_uuid
            )
          : "ローカル利用者",
        created_by_user_uuid: "local",
        created_by_display_name: "ローカル利用者",
        agent_ids: body.agent_ids ?? null,
        token_prefix: `prak_${id}_xxxx`,
        created_at: MOCK_NOW,
        expires_at: days ? new Date(Date.parse(MOCK_NOW) + days * 86_400_000).toISOString() : null,
        last_used_at: null,
        expired: false,
      };
      state.apiKeys.unshift(key);
      return { key, token };
    }
    if (method === "DELETE" && at("settings", "api-keys", "*")) {
      findOr404(state.apiKeys, "id", third, "api key");
      state.apiKeys = state.apiKeys.filter((key) => key.id !== third);
      return null;
    }
  }
  if (head === "settings") {
    const patchable: Record<string, keyof MockApiState> = {
      "trace-policy": "tracePolicy",
      "tool-policy": "toolPolicy",
    };
    if (at("settings", "*") && second in patchable) {
      const key = patchable[second];
      if (method === "GET") return state[key];
      if (method === "PATCH") {
        Object.assign(state[key] as Json, body);
        return state[key];
      }
    }
    if (second === "mcp-connections") {
      const store = state.mcpConnections;
      const listData = () => ({ connections: store.connections });
      if (method === "GET" && at("settings", "mcp-connections")) return listData();
      if (method === "POST" && at("settings", "mcp-connections")) {
        const id = String(body.server_id ?? "").trim();
        if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/.test(id) || id.includes("__")) {
          throw new HttpError(422, "接続 ID は英数字で始まる 40 文字以内の英数字・「-」・「_」で入力してください。");
        }
        if (store.connections.some((connection) => connection.server_id === id)) {
          throw new HttpError(409, "MCP 接続の ID はすでに使われています。");
        }
        const connection = mcpConnection({ ...body, server_id: id });
        store.connections.push(connection);
        store.connections.sort((left, right) => String(left.server_id).localeCompare(String(right.server_id)));
        return connection;
      }
      if (third && at("settings", "mcp-connections", "*", "tools") && method === "GET") {
        const connection = findOr404(store.connections, "server_id", third, "MCP 接続");
        if (!connection.configured) throw new HttpError(400, "MCP 接続の URL が設定されていません。");
        return {
          tools: MOCK_MCP_TOOLS.map((tool) => ({
            ...tool,
            server_id: third,
            function_name: `${third}__${tool.name}`,
          })),
          metadata: { server_id: third, method: "tools/list" },
        };
      }
      if (third && at("settings", "mcp-connections", "*")) {
        const connection = findOr404(store.connections, "server_id", third, "MCP 接続");
        if (method === "PATCH") {
          Object.assign(connection, mcpConnection({ ...body, server_id: third }, connection));
          return connection;
        }
        if (method === "DELETE") {
          if (!connection.removable) {
            throw new HttpError(400, "RAG / NL2SQL・宣言・連携機能の MCP 接続は削除できません。");
          }
          store.connections = store.connections.filter((candidate) => candidate.server_id !== third);
          return listData();
        }
      }
    }

    if (at("settings", "model")) {
      if (method === "GET") return state.modelSettings;
      if (method === "PATCH") {
        const enterpriseAi = { ...(body.enterprise_ai as Json) };
        // backend と同じく API key の値は応答へ返さない（接続ごと。#533）。
        const connections = ((enterpriseAi.connections as Json[] | undefined) ?? []).map(
          (connection) => ({
            ...connection,
            api_key: "",
            has_api_key:
              !connection.clear_api_key &&
              (Boolean(connection.api_key) || Boolean(connection.has_api_key)),
            clear_api_key: false,
          })
        );
        state.modelSettings = {
          ...state.modelSettings,
          settings: {
            enterprise_ai: { ...enterpriseAi, connections },
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
    if (method === "POST" && at("settings", "database", "wallet", "download")) {
      // 共有画面（#108）は ADB の保存後に OCI から Wallet の取得を試みる。
      return { status: "already_configured", settings: state.databaseSettings };
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
    setCurrentUser(user) {
      this.state.auth.currentUser = user ? clone(user) : null;
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
    mockApi.requests.push({
      method,
      path: url.pathname,
      searchParams: url.searchParams,
      body,
      headers: await request.allHeaders(),
    });
    // Run のイベント購読（SSE）。e2e は stream を保てないため空の stream を返して閉じる
    // （画面は購読の停止を示す。#215）。
    // Excel の書き出し・テンプレート（#776）。中身は確かめないため、Excel の形の空のデータを返す。
    if (method === "GET" && url.pathname.endsWith(".xlsx")) {
      await route.fulfill({
        status: 200,
        contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body: Buffer.from("PK-mock-xlsx"),
      });
      return;
    }
    if (method === "GET" && /^\/api\/runs\/[^/]+\/events$/.test(url.pathname)) {
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
      return;
    }
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
        await fulfillJson(route, error.status, {
          data: null,
          error_messages: [error.message],
          warning_messages: [],
          error_code: error.errorCode ?? null,
          detail: error.message,
        });
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
