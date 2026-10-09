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
import { expect, test as base, type Page, type Route } from "./test";
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
import { maskUrlCredentials, mcpUrlCredentialProblem } from "../../src/lib/mcp-url";
import { progressEventsForStatus } from "./chat-progress-events";

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
              "権限管理でロールに割り当てていた検索・回答プロファイル（AGENT_ROLE_BUSINESS_VIEWS）を削除します。#750 から使っていません。",
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

/** `GET /api/runtime/storage`（保存先。#839）。Oracle に保存している状態。 */
export const RUNTIME_STORAGE_PERSISTENT = {
  backend: "oracle_checkpoint",
  configured_backend: "auto",
  persistent: true,
  database_configured: true,
  reason: null,
};

/** 保存先にメモリを明示している（DB は設定済み）。作成・変更した内容は再起動で消える。 */
export const RUNTIME_STORAGE_MEMORY = {
  backend: "memory",
  configured_backend: "memory",
  persistent: false,
  database_configured: true,
  reason: "memory_backend",
};

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

/** 業種テンプレートの既定（backend の `AGENT_TEMPLATES` の一部と、使えない Skill を含む例）。 */
export const AGENT_TEMPLATES = [
  {
    id: "internal-policy-helpdesk",
    category: "共通（総務・人事）",
    name: "社内規程の問い合わせ",
    description: "就業規則・各種規程・社内手続きの質問に、規程の条項を示して答えます。",
    instructions: "あなたは総務・人事の問い合わせ窓口です。\n- 業務 RAG で規程を検索し、条項を引用して答える。",
    skill_ids: ["business_rag_research"],
    sample_questions: ["育児休業はいつから取得できますか？", "在宅勤務の申請はどのように行いますか？"],
    evaluation_cases: [{ question: "在宅勤務の申請は？", expected: "申請の方法と期限" }],
  },
  {
    id: "sales-analytics",
    category: "営業",
    name: "営業分析",
    description: "売上・受注・顧客のデータを自然言語で集計し、数字の根拠とともに答えます。",
    instructions: "あなたは営業企画の分析担当です。\n- 構造化データ照会で集計し、期間と単位を示す。",
    skill_ids: ["structured_data_query"],
    sample_questions: ["今月の地域別の売上を教えてください。", "売上上位 10 社の顧客は？"],
    evaluation_cases: [{ question: "今月の地域別の売上は？", expected: "地域ごとの金額" }],
  },
  {
    id: "manufacturing-quality",
    category: "製造",
    name: "品質管理",
    description: "品質基準・不具合報告の文書と、検査・不良率のデータをあわせて調べます。",
    instructions: "あなたは製造部門の品質管理の担当です。",
    skill_ids: ["business_rag_research", "quality_lab_only"],
    sample_questions: ["先月のライン別の不良率を教えてください。", "溶接の外観検査の判定基準は？"],
    evaluation_cases: [{ question: "溶接の判定基準は？", expected: "合否の条件" }],
  },
];

/** 応答に出す評価（mock の内部の数 `_polls` を除く）。 */
function publicJob(job: Json): Json {
  return clone(Object.fromEntries(Object.entries(job).filter(([key]) => key !== "_polls")));
}

/**
 * 評価セットの入力を保存する形にする（backend の `number_cases` と同じ。#965）。
 * id を省いたケースは使われていない `case-<番号>`（位置の番号から探す）、明示した id の重複は 422。
 */
function normalizedSet(body: Json): Json {
  const cases = (body.cases as Json[] | undefined) ?? [];
  const explicit = cases.map((item) => String(item.id ?? "").trim()).filter(Boolean);
  if (new Set(explicit).size !== explicit.length) {
    throw new HttpError(422, "ケースの id が重複しています。");
  }
  const used = new Set(explicit);
  return {
    agent_id: body.agent_id,
    name: body.name,
    description: body.description ?? "",
    cases: cases.map((item, index) => {
      let id = String(item.id ?? "").trim();
      if (!id) {
        let number = index + 1;
        while (used.has(`case-${number}`)) number += 1;
        id = `case-${number}`;
        used.add(id);
      }
      return {
        id,
        question: item.question,
        expected: item.expected,
        expected_tools: item.expected_tools ?? [],
        source_run_id: item.source_run_id ?? null,
      };
    }),
  };
}

/** 同じ質問かを比べる形（backend の `normalize_question` と同じ。#810）。 */
function normalizedQuestion(question: unknown): string {
  return String(question ?? "").trim().split(/\s+/).join(" ");
}

/** 評価の版（#810）。省略時は公開していない変更があれば下書き、なければ公開中の版。 */
function evaluationVersion(state: MockApiState, agentId: unknown, target: unknown): number | "draft" {
  const agent = state.agents.find((item) => item.id === agentId);
  const published = (agent?.published_version as number | null | undefined) ?? null;
  const chosen = target ?? (agent?.unpublished_changes || published === null ? "draft" : "published");
  if (chosen === "published") {
    if (published === null) {
      throw new HttpError(409, "公開した版がありません。下書きで評価するか、公開してから評価してください。");
    }
    return published;
  }
  return "draft";
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

/** 自動実行の入力を保存する形にする（次回は MOCK_NOW の翌日。Webhook・無効は無し）。 */
function automationFields(body: Json): Json {
  const schedule = body.trigger === "schedule" ? (body.schedule ?? null) : null;
  return {
    agent_id: body.agent_id,
    name: body.name,
    goal: body.goal,
    enabled: body.enabled ?? true,
    trigger: body.trigger ?? "schedule",
    schedule,
    next_run_at:
      schedule && (body.enabled ?? true) ? new Date(Date.parse(MOCK_NOW) + 86_400_000).toISOString() : null,
  };
}

function answerTextOf(run: Json): string {
  const answer = ((run.artifacts as Json[] | undefined) ?? []).find((artifact) => artifact.kind === "answer");
  const text = (answer?.content as Json | undefined)?.text;
  return typeof text === "string" ? text : "";
}

/** Run の評価から `GET /api/feedback` の応答を作る（前の期間は 0 件。#774）。 */
function feedbackReportFromRuns(
  runs: Json[],
  days: number,
  filters: { agentId: string | null; rating: string | null; reason: string | null },
  page: { offset: number; limit: number }
): Json {
  const rated = runs.filter(
    (run) => (run.feedback || run.admin_review) && (!filters.agentId || run.agent_id === filters.agentId)
  );
  const owned = rated.filter((run) => run.feedback).map((run) => run.feedback as Json);
  const reviews = rated.filter((run) => run.admin_review).map((run) => run.admin_review as Json);
  const helpful = owned.filter((item) => item.rating === "helpful").length;
  const reasons = new Map<string, number>();
  for (const item of owned) {
    const reason = item.reason as string | null;
    if (reason) reasons.set(reason, (reasons.get(reason) ?? 0) + 1);
  }
  const summary = {
    total: owned.length,
    helpful,
    not_helpful: owned.length - helpful,
    helpful_rate: owned.length ? helpful / owned.length : null,
    reason_counts: [...reasons.entries()].sort((a, b) => b[1] - a[1]).map(([reason, count]) => ({ reason, count })),
    admin_reviewed: reviews.length,
    admin_not_helpful: reviews.filter((item) => item.rating === "not_helpful").length,
  };
  const matches = (item: unknown) => {
    const rating = item as Json | null | undefined;
    return Boolean(
      rating &&
        (!filters.rating || rating.rating === filters.rating) &&
        (!filters.reason || rating.reason === filters.reason)
    );
  };
  const items = rated
    .filter((run) => matches(run.feedback) || matches(run.admin_review))
    .map((run) => ({
      run_id: run.id,
      thread_id: run.thread_id ?? null,
      agent_id: run.agent_id,
      agent_name: run.agent_id === "default" ? "汎用業務 Agent" : "",
      user_uuid: run.created_by_user_uuid ?? null,
      display_name: "ローカル利用者",
      question: run.goal,
      answer: answerTextOf(run),
      feedback: run.feedback ?? null,
      admin_review: run.admin_review ?? null,
      reviewer_display_name: run.admin_review ? "ローカル利用者" : "",
      updated_at: MOCK_NOW,
    }));
  return {
    days,
    source: "memory",
    since: MOCK_NOW,
    until: MOCK_NOW,
    summary,
    previous: {
      total: 0,
      helpful: 0,
      not_helpful: 0,
      helpful_rate: null,
      reason_counts: [],
      admin_reviewed: 0,
      admin_not_helpful: 0,
    },
    // 一覧はサーバー側のページング（#794）。
    ...pageOfItems(items, page),
  };
}

/** 集計で選べる期間（日。#794）。 */
export const REPORT_PERIOD_DAYS = [7, 30, 90, 180, 365];
const REPORT_PERIOD_DETAIL = "期間は 7・30・90・180・365 日のどれかにしてください。";

/** offset / limit の query（既定は 10 件/ページ）。 */
function pageOf(query: URLSearchParams): { offset: number; limit: number } {
  return { offset: Number(query.get("offset") ?? 0), limit: Number(query.get("limit") ?? 10) };
}

const EMPTY_USAGE_TOTALS = {
  runs: 0,
  runs_with_usage: 0,
  requests: 0,
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
};

/** Run の無い期間の `GET /api/usage`（#772）。日は MOCK_NOW までの `days` 日（古い順）。 */
export function emptyUsageReport(days: number, timezone = "Asia/Tokyo"): Json {
  const end = new Date(MOCK_NOW);
  const dayList = Array.from({ length: days }, (_, index) => {
    const day = new Date(end);
    day.setUTCDate(end.getUTCDate() - (days - 1 - index));
    return day.toISOString().slice(0, 10);
  });
  return {
    days,
    source: "memory",
    timezone,
    since: `${dayList[0]}T00:00:00+09:00`,
    until: MOCK_NOW,
    totals: { ...EMPTY_USAGE_TOTALS },
    previous: { ...EMPTY_USAGE_TOTALS },
    by_agent: [],
    by_user: [],
    by_model: [],
    by_day: dayList.map((day) => ({ ...EMPTY_USAGE_TOTALS, day })),
  };
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

/** データの範囲の候補の既定（#1378）。NL2SQL は 3 件、RAG は 2 件。 */
const DATA_SCOPE_CANDIDATES: Record<string, Json[]> = {
  nl2sql: [
    { id: "profile-sales", name: "売上", description: "売上の集計" },
    { id: "profile-cost", name: "原価", description: "原価と粗利" },
    { id: "profile-hr", name: "人事", description: "人員と勤怠" },
  ],
  rag: [
    { id: "bv-sales", name: "営業の検索・回答プロファイル", description: "営業部門の文書" },
    { id: "bv-legal", name: "法務の検索・回答プロファイル", description: "契約と規程" },
  ],
};

const DATA_SCOPE_LABELS: Record<string, string> = {
  nl2sql: "データ問い合わせ（NL2SQL）",
  rag: "ナレッジ検索（RAG）",
};

function createState() {
  const d = clone(defaults) as Record<string, Json & Json[]>;
  return {
    health: d.health as Json,
    // 組み込み Runtime の状態（#754）。既定は実行できる状態。
    runtimeStatus: clone(BUILTIN_RUNTIME_STATUS) as Json,
    // 保存先（`GET /api/runtime/storage`。#839）。既定は Oracle に保存している状態。
    runtimeStorage: clone(RUNTIME_STORAGE_PERSISTENT) as Json,
    runs: [] as Json[],
    agents: d.agents as unknown as Json[],
    skills: d.skills as unknown as Json[],
    tools: d.tools as unknown as Json[],
    // 監査の記録（`GET /api/audit/tool-calls`）。offset / limit で切り出して返す（#265）。
    auditRecords: [] as Json[],
    // 業種テンプレート（`GET /api/agent-templates`。#780）。
    agentTemplates: clone(AGENT_TEMPLATES) as Json[],
    // データの範囲に選べるプロファイル（`GET /api/agent-data-scopes/{接続}/candidates`。#1378）。
    // 編集者が使えるものだけ（backend は編集者として MCP に問い合わせる）。`dataScopeCandidateErrors` は接続ごとの失敗。
    dataScopeCandidates: clone(DATA_SCOPE_CANDIDATES) as Record<string, Json[]>,
    dataScopeCandidateErrors: {} as Record<string, { status: number; message: string }>,
    // フィードバック（`GET /api/feedback`。#774）。null なら Run の評価から作る。
    feedbackReport: null as Json | null,
    // 自動実行（#784）。`automationRuns` は自動実行ごとの実行履歴。
    automations: [] as Json[],
    automationsPersistent: true,
    automationRuns: {} as Record<string, Json[]>,
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
    // 利用状況（`GET /api/usage`。#772）。期間（日数）ごとの応答。無い期間は Run の無い集計を返す。
    usageReports: {} as Record<string, Json>,
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
    // システムテーブルの状態（#751）。既定は旧版の DB（検索・回答プロファイルの表の削除を承認する前）。
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

// ---- backend の権限の判定（#1113）----
// mock は backend と同じく、API の権限 manifest（`app/security/permissions.py` の `ROUTE_PERMISSIONS`）と、
// router の capability の判定（`Depends(require_*)`）で 403 を返す。定義は backend のソースから読み、
// 書き写さない（backend の manifest を変えると mock も追従する）。ローカルの全権限の利用者
// （システム管理者）は判定しない。
const BACKEND_APP_DIR = new URL("../../../backend/app/", import.meta.url);

interface BackendRouteRule {
  method: string;
  pattern: RegExp;
  /** 固定の segment の数（`/plugins/marketplaces` を `/plugins/{plugin_id}` より先に選ぶ）。 */
  staticSegments: number;
  permissions?: string[];
  /** router の `require_*` が受け付ける従来のロール（admin は常に通る）。 */
  roles?: string[];
}

const REQUIRE_ROLES: Record<string, string[]> = {
  viewer: ["viewer", "operator", "approver", "auditor"],
  operator: ["operator"],
  approver: ["approver"],
  auditor: ["auditor"],
  admin: ["admin"],
};

/** capability → 従来のロール（backend の `CAPABILITY_ROLES`）。 */
const CAPABILITY_ROLES: Record<string, string> = {
  "agent.runs.view": "viewer",
  "agent.runs.operate": "operator",
  "agent.approvals.decide": "approver",
  "agent.audit.view": "auditor",
  "agent.admin": "admin",
};

function routePattern(template: string): { pattern: RegExp; staticSegments: number } {
  const segments = template.split("/").filter(Boolean);
  const source = segments
    .map((segment) => (/^\{.+\}$/.test(segment) ? "[^/]+" : segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))
    .join("/");
  return {
    pattern: new RegExp(`^/${source}$`),
    staticSegments: segments.filter((segment) => !segment.startsWith("{")).length,
  };
}

function loadBackendRouteRules(): BackendRouteRule[] {
  const permissions = readFileSync(new URL("security/permissions.py", BACKEND_APP_DIR), "utf-8");
  const codes = new Map(
    [...permissions.matchAll(/^([A-Z][A-Z0-9_]*) = "((?:menu|agent)\.[a-z0-9_.]+)"$/gm)].map((match) => [match[1], match[2]])
  );
  const names = (list: string) =>
    list
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean);
  const groups = new Map(
    [...permissions.matchAll(/^(_[A-Z][A-Z0-9_]*) = _any\(([^)]*)\)/gm)].map((match) => [
      match[1],
      names(match[2]).map((name) => codes.get(name) ?? name),
    ])
  );
  const paths = new Map([...permissions.matchAll(/^(_[A-Z][A-Z0-9_]*) = "(\/[^"]*)"$/gm)].map((match) => [match[1], match[2]]));
  const manifest = permissions.slice(permissions.indexOf("ROUTE_PERMISSIONS:"), permissions.indexOf("\n}\n", permissions.indexOf("ROUTE_PERMISSIONS:")));
  const rules = new Map<string, BackendRouteRule>();
  const entries = /\(\s*"([A-Z]+)",\s*(?:(f?)"([^"]+)"|(_[A-Z][A-Z0-9_]*))\s*\):\s*(?:_any\(([^)]*)\)|(_[A-Z][A-Z0-9_]*))/g;
  for (const match of manifest.matchAll(entries)) {
    const [, method, isFormat, rawPath, pathName, inline, groupName] = match;
    const path =
      pathName !== undefined
        ? (paths.get(pathName) ?? "")
        : isFormat
          ? rawPath
              .replace(/\{(_[A-Z][A-Z0-9_]*)\}/g, (_all, name: string) => paths.get(name) ?? "")
              .replace(/\{\{|\}\}/g, (brace) => brace[0])
          : rawPath;
    const allowed = inline !== undefined ? names(inline).map((name) => codes.get(name) ?? name) : groups.get(groupName) ?? [];
    rules.set(`${method} ${path}`, { method, ...routePattern(path), permissions: allowed });
  }
  const router = readFileSync(new URL("features/agent/router.py", BACKEND_APP_DIR), "utf-8");
  for (const match of router.matchAll(/@router\.(get|post|put|patch|delete)\(\s*"([^"]+)"[^\n]*\n(?:[^\n]*\n)*?(?:async )?def \w+\(([\s\S]*?)\)\s*->/g)) {
    const [, rawMethod, path, params] = match;
    const role = /Depends\(require_(viewer|operator|approver|auditor|admin)\)/.exec(params)?.[1];
    if (!role) continue;
    const key = `${rawMethod.toUpperCase()} ${path}`;
    const rule = rules.get(key) ?? { method: rawMethod.toUpperCase(), ...routePattern(path) };
    rules.set(key, { ...rule, roles: REQUIRE_ROLES[role] });
  }
  return [...rules.values()];
}

const BACKEND_ROUTE_RULES = loadBackendRouteRules();

function isAllAccess(user: CurrentUserPayload): boolean {
  return user.is_system_admin;
}

/** 利用者が使える業務 Agent（null は制限なし。backend の `allowed_agent_ids`。Agent 管理は全対象）。 */
function agentScope(state: MockApiState): Set<string> | null {
  const user = state.auth.currentUser;
  if (!user || isAllAccess(user) || user.allowed_agent_ids === null || user.permissions.includes("agent.admin")) {
    return null;
  }
  return new Set(user.allowed_agent_ids);
}

function agentAllowed(state: MockApiState, agentId: unknown): boolean {
  const scope = agentScope(state);
  return scope === null || scope.has(String(agentId));
}

/** 業務 Agent の対象範囲の外（backend の `_require_agent_access`）。 */
function requireAgentAccess(state: MockApiState, agentId: unknown) {
  if (!agentAllowed(state, agentId)) throw new HttpError(403, "この業務 Agent を使う権限がありません。");
}

/** capability の不足の文（backend の `_capability_denied_message`。権限管理と同じ名前・並び）。 */
function capabilityDeniedMessage(roles: string[]): string {
  const allowed = new Set([...roles, "admin"]);
  const labels = PERMISSION_CATALOG.filter((item) => allowed.has(CAPABILITY_ROLES[String(item.code)])).map((item) =>
    String(item.label)
  );
  return `この操作を行う権限がありません。必要な権限（いずれか）: ${labels.join("、")}`;
}

/** API の権限 manifest と router の capability の判定（backend の `authorize_api_request` と `require_*`）。 */
function enforceBackendPermissions(state: MockApiState, method: string, apiPath: string) {
  const user = state.auth.currentUser;
  if (!user || isAllAccess(user)) return;
  const path = apiPath.replace(/^\/api/, "") || "/";
  const rule = BACKEND_ROUTE_RULES.filter((candidate) => candidate.method === method && candidate.pattern.test(path)).sort(
    (left, right) => right.staticSegments - left.staticSegments
  )[0];
  if (!rule) return;
  if (rule.permissions && !rule.permissions.some((code) => user.permissions.includes(code))) {
    throw new HttpError(403, "この機能を利用する権限がありません。", "SECURITY_ROUTE_FORBIDDEN");
  }
  if (rule.roles) {
    const roles = new Set(user.permissions.map((code) => CAPABILITY_ROLES[code]).filter(Boolean));
    if (!roles.has("admin") && !rule.roles.some((role) => roles.has(role))) {
      throw new HttpError(403, capabilityDeniedMessage(rule.roles));
    }
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

/**
 * URL の userinfo と資格情報らしい query の値を `***` に伏せる（backend の `mask_url_credentials`。#1081）。
 * #1078 の `src/lib/mcp-url.ts` の `maskUrlCredentials` と同じ規則。#1078 の merge 後はそちらを使う。
 */
function maskPluginMcpUrl(url: unknown): unknown {
  if (typeof url !== "string" || !url) return url ?? null;
  const match = /^([A-Za-z][A-Za-z0-9+.-]*:\/\/)([^/?#]*)([^?#]*)(?:\?([^#]*))?(#.*)?$/.exec(url);
  if (!match) return url;
  const [, head, authority, rest, query, fragment = ""] = match;
  const at = authority.lastIndexOf("@");
  const host = at >= 0 ? `***@${authority.slice(at + 1)}` : authority;
  const secretWord =
    /^(key|apikey|token|secret|password|passwd|pwd|auth|authorization|credential|credentials|signature|sig|jwt|bearer)$/;
  const maskedQuery = (query ?? "")
    .split("&")
    .map((part) => {
      const name = part.split("=", 1)[0];
      const words = name
        .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
        .toLowerCase()
        .split(/[^a-z0-9]+/);
      return part && words.some((word) => secretWord.test(word)) ? `${name}=***` : part;
    })
    .join("&");
  return `${head}${host}${rest}${query === undefined ? "" : `?${maskedQuery}`}${fragment}`;
}

/** プラグインの MCP サーバーの応答の形（backend の `PluginMcpServerView`。秘密は返さず設定済みかだけ。#1081）。 */
function publicPluginMcpServer(server: Json): Json {
  const oauthReady = Boolean(server.oauth_token_url && server.oauth_client_id && server.oauth_client_secret);
  return {
    server_id: server.server_id,
    label: server.label ?? null,
    base_url: maskPluginMcpUrl(server.base_url),
    auth_mode: server.auth_mode ?? (oauthReady ? "oauth_client_credentials" : server.api_key ? "api_key" : "none"),
    oauth_token_url: maskPluginMcpUrl(server.oauth_token_url),
    oauth_client_id: server.oauth_client_id ?? null,
    oauth_scope: server.oauth_scope ?? null,
    service_audience: server.service_audience ?? null,
    timeout_seconds: server.timeout_seconds ?? 10,
    source: server.source ?? "runtime",
    api_key_configured: Boolean(server.api_key),
    oauth_client_secret_configured: Boolean(server.oauth_client_secret),
    session_configured: Boolean(server.session_id),
  };
}

function publicPluginManifest(manifest: Json): Json {
  const mcpServers = (manifest.mcp_servers as Json[] | undefined) ?? [];
  return { ...manifest, mcp_servers: mcpServers.map(publicPluginMcpServer) };
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
    // 応答と同じく MCP サーバーの資格情報を持たない形で置く（mock は実際に接続しない）。
    manifest: publicPluginManifest(manifest),
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

/**
 * 保存の API の URL の検証（backend の `_validate_mcp_url` と同じ 422。#1056）。
 * userinfo・資格情報らしい query は拒否する（値は文に含めない）。
 */
function validateMcpUrl(body: Json) {
  if (body.base_url == null) return;
  const value = String(body.base_url).trim();
  if (!value) return;
  const prefix = "body.base_url: Value error, ";
  if (!/^https?:\/\/[^\s/]+/.test(value)) {
    throw new HttpError(422, `${prefix}MCP の URL は http:// または https:// で始めてください。`);
  }
  const problem = mcpUrlCredentialProblem(value);
  const guide = "資格情報は「認証」の欄（API キー・OAuth）で設定してください。";
  if (problem?.kind === "userinfo") {
    throw new HttpError(422, `${prefix}MCP の URL にユーザー名・パスワード（user:pass@）を含めないでください。${guide}`);
  }
  if (problem?.kind === "secretQuery") {
    throw new HttpError(
      422,
      `${prefix}MCP の URL に資格情報のパラメータ（${problem.names.join("・")}）を含めないでください。${guide}`
    );
  }
}

/** 一覧・保存の応答の MCP 接続（保存した URL の資格情報は `***` に伏せる。backend と同じ。#1056）。 */
function publicMcpConnection(connection: Json): Json {
  const raw = typeof connection.base_url === "string" ? connection.base_url : null;
  const masked = raw ? maskUrlCredentials(raw) : raw;
  return { ...connection, base_url: masked, base_url_masked: Boolean(raw) && masked !== raw };
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
    errors.push(`未対応のスナップショットの版です: ${String(snapshot.version)}`);
  }
  const duplicates = (label: string, ids: unknown[]) => {
    const seen = new Set<unknown>();
    const dup = new Set<string>();
    for (const id of ids) {
      if (seen.has(id)) dup.add(String(id));
      seen.add(id);
    }
    if (dup.size) errors.push(`ID が重複している${label}${/[ -~]$/.test(label) ? " " : ""}があります: ${[...dup].sort().join(", ")}`);
  };
  duplicates("実行", runs.map((run) => run.id));
  duplicates("業務 Agent", agents.map((agent) => agent.id));
  if (!agents.some((agent) => agent.id === "default")) {
    warnings.push("既定の業務 Agent（default）がありません。置換すると作り直します。");
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
  return pageOfItems(matched, { offset, limit });
}

/** 共通の `Page[T]` の形（backend の `paginate_slice` と同じ。`total` は省略すると items の全件数）。 */
function pageOfItems<T>(items: T[], page: { offset: number; limit: number }, total?: number) {
  const pageItems = items.slice(page.offset, page.offset + page.limit);
  const count = total ?? items.length;
  return {
    items: pageItems,
    total: count,
    limit: page.limit,
    offset: page.offset,
    has_next: page.offset + pageItems.length < count,
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
      // backend の SecurityService.update_role_access と同じ順の確認。
      if (role.is_built_in) {
        throw new HttpError(409, "組み込み SYSTEM_ADMIN ロールは変更できません。", "SECURITY_STATE_CONFLICT");
      }
      if (role.archived) throw new HttpError(409, "アーカイブ済みロールは変更できません。", "SECURITY_STATE_CONFLICT");
      const catalog = new Set(PERMISSION_CATALOG.map((item) => String(item.code)));
      const requested = ((body.permissions as string[] | undefined) ?? []).map((code) => code.trim()).filter(Boolean);
      const unknownCodes = [...new Set(requested.filter((code) => !catalog.has(code)))].sort();
      if (unknownCodes.length) {
        throw new HttpError(400, `未登録の権限コードです: ${unknownCodes.join(", ")}`, "SECURITY_REQUEST_INVALID");
      }
      const permissions = [...new Set(requested)].sort();
      // 業務 Agent の存在は Runtime の業務 Agent（mock は state.agents と権限管理の対象の候補）で確かめる。
      const knownAgents = new Set(
        [...state.agents, ...state.security.accessTargets.agents].map((agent) => String(agent.id))
      );
      const currentAgents = new Set(((role.agent_ids as string[] | undefined) ?? []).map(String));
      const requestedAgents = [
        ...new Set(((body.agent_ids as string[] | undefined) ?? []).map((id) => id.trim()).filter(Boolean)),
      ];
      const unknownAgents = requestedAgents.filter((id) => !knownAgents.has(id) && !currentAgents.has(id)).sort();
      if (unknownAgents.length) {
        throw new HttpError(400, `エージェントが見つかりません: ${unknownAgents.join(", ")}`, "SECURITY_REQUEST_INVALID");
      }
      const grantsAll = expandPermissions(permissions).includes("agent.admin");
      // agent.admin を含むロールは対象を空に正規化する（backend と同じ）。
      const agentIds = grantsAll ? [] : requestedAgents.filter((id) => knownAgents.has(id)).sort();
      const actor = state.auth.currentUser;
      if (actor && !actor.is_system_admin) {
        // 自分が持たない権限・自分の範囲外の業務 Agent を足すと 403（権限の昇格を防ぐ）。
        const before = new Set(expandPermissions((role.permissions as string[] | undefined) ?? []));
        const added = expandPermissions(permissions).filter((code) => !before.has(code));
        if (!added.every((code) => actor.permissions.includes(code))) {
          throw new HttpError(403, "自分が持たない権限をロールに追加することはできません。", "SECURITY_PERMISSION_DENIED");
        }
        const scope = actor.allowed_agent_ids;
        if (scope !== null && agentIds.some((id) => !currentAgents.has(id) && !scope.includes(id))) {
          throw new HttpError(
            403,
            "自分が利用できないエージェントをロールに追加することはできません。",
            "SECURITY_PERMISSION_DENIED"
          );
        }
      }
      if (body.version !== role.version) {
        throw new HttpError(409, "ロールが別の操作で更新されています。", "SECURITY_STATE_CONFLICT");
      }
      Object.assign(role, { version: (role.version as number) + 1, permissions, agent_ids: agentIds });
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
  if (method === "GET" && at("runtime", "storage")) return state.runtimeStorage;

  // --- Run / 承認 / 監査 ---
  // 一覧は利用者の対象範囲の業務 Agent の Run だけ（backend の `_filter_runs_for_actor`）。
  if (method === "GET" && at("runs")) return { runs: state.runs.filter((run) => agentAllowed(state, run.agent_id)) };
  // チャット（#768）。mock の Run はすぐ完了し、質問を引いた回答の成果物を持つ（実行中・承認待ちは spec が state を書き換える）。
  if (method === "POST" && at("runs")) {
    // backend の create_run と同じ確認（対象範囲・業務 Agent の有無・移行・無効・下書き・公開）。
    const agentId = String(body.agent_id ?? "default");
    requireAgentAccess(state, agentId);
    const agent = state.agents.find((candidate) => candidate.id === agentId);
    if (!agent) throw new HttpError(404, "業務 Agent が見つかりません。");
    if (agent.migration_required) {
      throw new HttpError(409, "Skill を選択して Agent の移行を完了してください。", "agent_migration_required");
    }
    if (!agent.enabled) throw new HttpError(409, "無効な業務 Agent は実行できません。", "agent_disabled");
    const currentUser = state.auth.currentUser;
    if (body.draft && currentUser && !currentUser.is_system_admin && !currentUser.permissions.includes("agent.admin")) {
      throw new HttpError(403, "下書きで実行できるのは Agent 管理の権限がある利用者だけです。");
    }
    if (!body.draft && (agent.published_version ?? null) === null) {
      throw new HttpError(
        409,
        "公開していない業務 Agent は実行できません。公開するか、下書きで実行してください。",
        "agent_unpublished"
      );
    }
    const threadId = typeof body.thread_id === "string" ? body.thread_id : null;
    if (threadId && !state.runs.some((run) => run.thread_id === threadId)) {
      throw new HttpError(404, "会話が見つかりません。");
    }
    const id = `run-chat-${state.runs.length + 1}`;
    const run: Json = {
      id,
      goal: String(body.goal ?? ""),
      agent_id: agentId,
      runtime_id: "builtin",
      status: "completed",
      steps: [],
      events: [],
      approvals: [],
      artifacts: [{ id: `${id}-answer`, name: "回答", kind: "answer", created_at: MOCK_NOW, content: { text: `「${String(body.goal ?? "")}」への回答です。` } }],
      pending_tool_calls: [],
      metadata: {},
      created_by_user_uuid: "local",
      thread_id: threadId ?? `thread_${String(state.runs.length + 1).padStart(32, "0")}`,
      created_at: MOCK_NOW,
      updated_at: MOCK_NOW,
    };
    state.runs.push(run);
    return run;
  }
  // フィードバックの集計と一覧（#774）。`state.feedbackReport` があればそれを返し、無ければ Run の評価から作る。
  if (method === "GET" && at("feedback")) {
    const days = Number(query.get("days") ?? 30);
    if (!REPORT_PERIOD_DAYS.includes(days)) throw new HttpError(422, REPORT_PERIOD_DETAIL);
    const page = pageOf(query);
    if (state.feedbackReport) {
      const report = state.feedbackReport;
      const items = (report.items as Json[] | undefined) ?? [];
      return {
        source: "memory",
        ...report,
        ...pageOfItems(items, page, (report.total as number | undefined) ?? items.length),
      };
    }
    return feedbackReportFromRuns(
      state.runs,
      days,
      { agentId: query.get("agent_id"), rating: query.get("rating"), reason: query.get("reason") },
      page
    );
  }
  if (method === "GET" && at("threads")) {
    const agentId = query.get("agent_id");
    const grouped = new Map<string, Json[]>();
    for (const run of state.runs) {
      if (typeof run.thread_id !== "string") continue;
      if (agentId && run.agent_id !== agentId) continue;
      if (!agentAllowed(state, run.agent_id)) continue;
      grouped.set(run.thread_id, [...(grouped.get(run.thread_id) ?? []), run]);
    }
    // 会話の履歴のページング（limit / offset と全件数。#1265）。
    const limit = Number(query.get("limit") ?? 10);
    const offset = Number(query.get("offset") ?? 0);
    const threads = [...grouped.entries()].map(([threadId, runs]) => ({
      thread_id: threadId,
      agent_id: runs[0].agent_id,
      title: String(runs[0].goal).split("\n")[0],
      run_count: runs.length,
      last_status: runs[runs.length - 1].status,
      created_at: runs[0].created_at,
      updated_at: runs[runs.length - 1].updated_at,
    })).reverse();
    return pageOfItems(threads, { offset, limit });
  }
  if (method === "GET" && at("threads", "*")) {
    const runs = state.runs.filter((run) => run.thread_id === second);
    if (runs.length === 0) throw new HttpError(404, "会話が見つかりません。");
    return { thread_id: second, agent_id: runs[0].agent_id, runs };
  }
  if (head === "runs" && second) {
    const run = findOr404(state.runs, "id", second, "run");
    // 実行の詳細・操作は利用者の対象範囲の業務 Agent だけ（backend の `_require_agent_access`。
    // 評価ケースの下書きは範囲外を 404 にする）。
    if (at("runs", "*", "evaluation-case") && !agentAllowed(state, run.agent_id)) {
      throw new HttpError(404, "実行が見つかりません。");
    }
    requireAgentAccess(state, run.agent_id);
    if (method === "GET" && at("runs", "*", "audit")) {
      return { run_id: run.id, goal: run.goal, status: run.status, records: [] };
    }
    if (method === "GET" && at("runs", "*", "artifacts")) return { artifacts: [] };
    // チャットの処理の段階のイベント（polling。`since` より後。#1359）。
    if (method === "GET" && at("runs", "*", "progress")) {
      return progressPage(run, Number(query.get("since") ?? 0));
    }
    // RAG の図の根拠を開く短命の URL（#1311）。spec は `http://rag.e2e.test/**` を page.route で応答する。
    if (method === "GET" && at("runs", "*", "figure-url")) {
      return {
        url: `http://rag.e2e.test/api/figures/f1.${encodeURIComponent(query.get("chunk_id") ?? "")}.sig`,
        expires_at: "2026-06-28T00:05:00+00:00",
      };
    }
    // Run から評価ケースの下書きを作る（#810）。
    if (method === "GET" && at("runs", "*", "evaluation-case")) {
      const agent = state.agents.find((item) => item.id === run.agent_id);
      const review = run.admin_review as Json | null | undefined;
      const tools = ((run.steps as Json[] | undefined) ?? [])
        .map((step) => (step.tool_call as Json | null | undefined)?.name)
        .filter((name): name is string => typeof name === "string");
      return {
        agent_id: run.agent_id,
        agent_name: agent?.name ?? run.agent_id,
        source_run_id: run.id,
        question: String(run.goal ?? "").trim(),
        expected: String(review?.comment ?? "").trim(),
        expected_tools: [...new Set(tools)],
        existing_set_ids: state.evaluationSets
          .filter(
            (item) =>
              item.agent_id === run.agent_id &&
              (item.cases as Json[]).some(
                (candidate) => normalizedQuestion(candidate.question) === normalizedQuestion(run.goal)
              )
          )
          .map((item) => item.id),
      };
    }
    if (method === "POST" && at("runs", "*", "cancel")) {
      run.status = "cancelled";
      // spec が積んだ処理の段階のイベントには、backend と同じく停止の終端を足す（#1359）。
      const events = run.progress_events;
      if (Array.isArray(events) && !events.some((event) => (event as Json).type === "terminal")) {
        events.push({
          schema_version: 1,
          type: "terminal",
          seq: events.length + 1,
          target_id: run.id,
          attempt: 0,
          emitted_at: new Date().toISOString(),
          status: "cancelled",
        });
      }
      return run;
    }
    if (method === "POST" && at("runs", "*", "resume")) {
      // 承認がすべて決まり、保存した SDK の状態から再開を待つ組み込み Runtime の Run だけ（`builtin_resume_pending`）。
      const resumable =
        run.runtime_id === "builtin" &&
        run.status === "queued" &&
        typeof (run.metadata as Json | undefined)?._builtin_sdk_state === "string";
      if (!resumable) {
        throw new HttpError(409, "承認待ちが残っているか、再開できる状態ではありません。", "run_not_resumable");
      }
      return run;
    }
    // チャットの回答への評価（#774）。役に立たなかったときは理由が必須。役に立った評価は理由・コメントを残さない。
    // 管理者の評価（#774）。本人の評価とは別に残す。
    if (method === "PUT" && at("runs", "*", "admin-review")) {
      if (run.status !== "completed") throw new HttpError(409, "回答が出た Run にだけ評価を付けられます。");
      const helpful = body.rating === "helpful";
      if (!helpful && !body.reason) throw new HttpError(422, "役に立たなかった理由を選んでください。");
      run.admin_review = {
        rating: body.rating,
        reason: helpful ? null : body.reason,
        comment: helpful ? "" : String(body.comment ?? "").trim(),
        user_uuid: "local",
        updated_at: MOCK_NOW,
      };
      return run;
    }
    if (method === "PUT" && at("runs", "*", "feedback")) {
      if (run.status !== "completed") throw new HttpError(409, "回答が出た Run にだけフィードバックを付けられます。");
      const helpful = body.rating === "helpful";
      if (!helpful && !body.reason) throw new HttpError(422, "役に立たなかった理由を選んでください。");
      run.feedback = {
        rating: body.rating,
        reason: helpful ? null : body.reason,
        comment: helpful ? "" : String(body.comment ?? "").trim(),
        user_uuid: "local",
        updated_at: MOCK_NOW,
      };
      return run;
    }
    if (method === "POST" && at("runs", "*", "replay")) {
      // backend の replay_run と同じく、同じ業務 Agent・ゴールの新しい Run（待ち）を作る。元の Run の成果物・
      // 経過・承認・評価・会話は引き継がない。
      const replayId = `${String(run.id)}-replay-${state.runs.length}`;
      const replay: Json = {
        id: replayId,
        goal: run.goal,
        agent_id: run.agent_id,
        runtime_id: "builtin",
        status: "queued",
        steps: [],
        events: [],
        approvals: [],
        artifacts: [],
        pending_tool_calls: [],
        metadata: { replayed_from_run_id: run.id },
        created_by_user_uuid: state.auth.currentUser?.user_uuid ?? null,
        thread_id: `thread_${String(state.runs.length + 1).padStart(32, "0")}`,
        created_at: MOCK_NOW,
        updated_at: MOCK_NOW,
      };
      state.runs.unshift(replay);
      return replay;
    }
  }
  if (method === "POST" && at("approvals", "*", "decision")) {
    for (const run of state.runs) {
      const approval = ((run.approvals as Json[] | undefined) ?? []).find((candidate) => candidate.id === second);
      if (approval) {
        const decidedBy = state.auth.currentUser?.login_user_id ?? "local";
        // backend の decide_approval と同じく、終了した Run の保留中の承認は取り消し、判断済みの承認
        // （ほかの操作者が先に判断した）は変えずに 200 で返す。
        if (["completed", "failed", "cancelled"].includes(String(run.status))) {
          if (approval.status === "pending") {
            Object.assign(approval, { status: "cancelled", decided_by: decidedBy, decided_at: MOCK_NOW });
          }
          return run;
        }
        if (approval.status !== "pending") return run;
        approval.status = body.approved ? "approved" : "rejected";
        approval.decided_by = decidedBy;
        approval.decided_at = MOCK_NOW;
        // 組み込み Runtime の Run は、承認待ちが残らなければ再開を待つ状態（queued）にする。
        const pending = ((run.approvals as Json[] | undefined) ?? []).some((item) => item.status === "pending");
        if (run.runtime_id === "builtin" && !pending) {
          run.status = "queued";
          run.updated_at = MOCK_NOW;
        }
        return run;
      }
    }
    throw new HttpError(404, `approval not found: ${second}`);
  }
  if (method === "GET" && at("agent-templates")) return { templates: state.agentTemplates };
  // データの範囲の候補（#1378）。backend と同じく範囲を持てる接続だけ。
  if (method === "GET" && at("agent-data-scopes", "*", "candidates")) {
    const connection = String(second);
    if (!(connection in DATA_SCOPE_LABELS)) throw new HttpError(404, "データの範囲を設定できない接続です。");
    const failure = state.dataScopeCandidateErrors[connection];
    if (failure) throw new HttpError(failure.status, failure.message);
    return {
      connection,
      label: DATA_SCOPE_LABELS[connection],
      profiles: state.dataScopeCandidates[connection] ?? [],
    };
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
  // 業種テンプレートの評価ケースで評価セットを作る（#810）。
  if (method === "POST" && at("evaluation-sets", "from-template")) {
    const agent = findOr404(state.agents, "id", String(body.agent_id), "agent");
    const template = state.agentTemplates.find((item) => item.id === agent.template_id);
    if (!template) {
      throw new HttpError(409, "この業務 Agent はテンプレートから作っていないため、評価ケースがありません。");
    }
    const item: Json = {
      ...normalizedSet({
        agent_id: agent.id,
        name: `${String(template.name)}（テンプレート）`,
        description: "業種テンプレートの評価ケースから作りました。",
        cases: template.evaluation_cases,
      }),
      id: `evset-${state.evaluationSets.length + 1}`,
      created_by_user_uuid: "local",
      created_at: MOCK_NOW,
      updated_at: MOCK_NOW,
    };
    state.evaluationSets.unshift(item);
    return item;
  }
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
    // ケースを 1 件足す（同じ質問・50 件の上限は 409。#810）。
    if (method === "POST" && at("evaluation-sets", "*", "cases")) {
      const cases = item.cases as Json[];
      if (cases.some((candidate) => normalizedQuestion(candidate.question) === normalizedQuestion(body.question))) {
        throw new HttpError(409, "同じ質問の評価ケースが、この評価セットに既にあります。");
      }
      if (cases.length >= 50) {
        throw new HttpError(409, "評価セットのケースは 50 件までです。別の評価セットに追加してください。");
      }
      const used = new Set(cases.map((candidate) => candidate.id));
      let number = cases.length + 1;
      while (used.has(`case-${number}`)) number += 1;
      cases.push({
        id: `case-${number}`,
        question: String(body.question).trim(),
        expected: String(body.expected).trim(),
        expected_tools: body.expected_tools ?? [],
        source_run_id: body.source_run_id ?? null,
      });
      item.updated_at = MOCK_NOW;
      return item;
    }
    if (method === "PUT" && at("evaluation-sets", "*")) {
      if (body.agent_id !== item.agent_id) throw new HttpError(422, "評価セットの業務 Agent は変えられません。");
      Object.assign(item, normalizedSet(body), { updated_at: MOCK_NOW });
      return item;
    }
    if (method === "DELETE" && at("evaluation-sets", "*")) {
      if (state.evaluations.some((job) => job.set_id === item.id && (job.status === "running" || job.status === "queued"))) {
        throw new HttpError(409, "この評価セットで評価を実行しています。終わってから削除してください。");
      }
      state.evaluationSets = state.evaluationSets.filter((candidate) => candidate !== item);
      return null;
    }
  }
  // --- 品質評価（#776） ---
  if (method === "POST" && at("evaluations")) {
    // backend と同じく、待っている（queued）評価も実行中に数える。
    if (state.evaluations.some((job) => job.status === "running" || job.status === "queued")) {
      throw new HttpError(409, "ほかの評価を実行しています。終わってから始めてください。");
    }
    const evaluationSet = findOr404(state.evaluationSets, "id", String(body.set_id), "evaluation set");
    // 無効・移行が要る業務 Agent は評価しない（backend の create_evaluation）。
    const evaluatedAgent = state.agents.find((agent) => agent.id === evaluationSet.agent_id);
    if (!evaluatedAgent) throw new HttpError(404, "業務 Agent が見つかりません。");
    if (!evaluatedAgent.enabled || evaluatedAgent.migration_required) {
      throw new HttpError(409, "この業務 Agent は実行できない状態です。");
    }
    const cases = evaluationSet.cases as Json[];
    const job: Json = {
      id: `eval-${state.evaluations.length + 1}`,
      agent_id: evaluationSet.agent_id,
      agent_name: evaluationSet.agent_id === "default" ? "汎用業務 Agent" : String(evaluationSet.agent_id),
      set_id: evaluationSet.id,
      set_name: evaluationSet.name,
      agent_version: evaluationVersion(state, evaluationSet.agent_id, body.agent_version),
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
      previous_agent_version: null,
      previous_created_at: null,
      _polls: 0,
    };
    job.summary = evaluationSummary(job.results as Json[]);
    const previous = state.evaluations.find((item) => item.set_id === evaluationSet.id && item.status === "completed");
    if (previous) {
      job.previous_job_id = previous.id;
      job.previous_summary = previous.summary;
      job.previous_agent_version = previous.agent_version ?? null;
      job.previous_created_at = previous.created_at;
    }
    state.evaluations.unshift(job);
    return publicJob(job);
  }
  if (method === "GET" && at("evaluations")) {
    // 評価の履歴はサーバー側のページング（#794）。
    const page = pageOf(query);
    return {
      ...pageOfItems(state.evaluations, page),
      items: state.evaluations.slice(page.offset, page.offset + page.limit).map((job) => ({
        id: job.id,
        agent_id: job.agent_id,
        agent_name: job.agent_name,
        set_id: job.set_id ?? "",
        set_name: job.set_name ?? "",
        agent_version: job.agent_version ?? null,
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
      // 終わった評価は変えない（backend の EvaluationStore.cancel は実行中・待ちの評価だけを取り消す）。
      if (job.status !== "running" && job.status !== "queued") return publicJob(job);
      job.status = "cancelled";
      for (const result of job.results as Json[]) {
        if (result.status === "pending" || result.status === "running") result.status = "cancelled";
      }
      job.summary = evaluationSummary(job.results as Json[]);
      return publicJob(job);
    }
    if (method === "DELETE" && at("evaluations", "*")) {
      if (job.status === "running" || job.status === "queued") {
        throw new HttpError(409, "実行中の評価は削除できません。取り消してから削除してください。");
      }
      state.evaluations = state.evaluations.filter((item) => item !== job);
      return null;
    }
  }
  // --- 自動実行（#784） ---
  // 実行できる業務 Agent か（backend の `_require_runnable_agent` / `agent_unavailable_reason`。#927）。
  const requireRunnableAgent = (agentId: unknown) => {
    const agent = state.agents.find((candidate) => candidate.id === agentId);
    if (!agent) throw new HttpError(422, "業務 Agent が見つかりません。");
    if (!agent.enabled || agent.migration_required) throw new HttpError(422, "この業務 Agent は実行できない状態です。");
    if (agent.published_version === null) {
      throw new HttpError(422, "公開していない業務 Agent は実行できません。公開してから使ってください。");
    }
  };
  if (method === "GET" && at("automations")) {
    return {
      automations: state.automations.filter((item) => agentAllowed(state, item.agent_id)),
      persistent: state.automationsPersistent,
    };
  }
  if (method === "POST" && at("automations")) {
    requireRunnableAgent(body.agent_id);
    const item: Json = {
      ...automationFields(body),
      id: `auto-${state.automations.length + 1}`,
      run_as_user_uuid: "local",
      created_by_user_uuid: "local",
      webhook_token_prefix: null,
      last_run_at: null,
      last_run_id: null,
      last_trigger: null,
      last_result: null,
      last_message: null,
      created_at: MOCK_NOW,
      updated_at: MOCK_NOW,
    };
    state.automations.unshift(item);
    return item;
  }
  if (head === "automations" && second) {
    const item = findOr404(state.automations, "id", second, "automation");
    if (method === "GET" && at("automations", "*")) {
      return { automation: item, runs: state.automationRuns[String(item.id)] ?? [] };
    }
    if (method === "PUT" && at("automations", "*")) {
      // 業務 Agent を変えるときと有効のまま保存するときだけ確かめる（無効にする保存は通す）。
      if (body.agent_id !== item.agent_id || body.enabled) requireRunnableAgent(body.agent_id);
      Object.assign(item, automationFields(body), { updated_at: MOCK_NOW });
      return item;
    }
    if (method === "DELETE" && at("automations", "*")) {
      state.automations = state.automations.filter((candidate) => candidate !== item);
      return null;
    }
    if (method === "POST" && at("automations", "*", "run")) {
      const runs = (state.automationRuns[String(item.id)] ??= []);
      const runId = `run-auto-${runs.length + 1}`;
      runs.unshift({ run_id: runId, status: "queued", trigger: "manual", created_at: MOCK_NOW, updated_at: MOCK_NOW });
      Object.assign(item, {
        last_run_at: MOCK_NOW,
        last_run_id: runId,
        last_trigger: "manual",
        last_result: "created",
        last_message: "実行を作りました。",
      });
      return { run_id: runId, result: "created", message: "実行を作りました。" };
    }
    if (method === "POST" && at("automations", "*", "webhook-token")) {
      if (item.trigger !== "webhook") throw new HttpError(409, "Webhook のトリガーではありません。");
      const token = `prwh_${"t".repeat(43)}`;
      item.webhook_token_prefix = token.slice(0, 9);
      return { automation: item, token };
    }
  }
  if (method === "GET" && at("usage")) {
    const days = Number(query.get("days") ?? 30);
    if (!REPORT_PERIOD_DAYS.includes(days)) throw new HttpError(422, REPORT_PERIOD_DETAIL);
    return state.usageReports[String(days)] ?? emptyUsageReport(days, query.get("timezone") ?? "Asia/Tokyo");
  }
  if (method === "GET" && at("audit", "tool-calls")) {
    const offset = Number(query.get("offset") ?? 0);
    const limit = Number(query.get("limit") ?? 100);
    // backend の `_audit_record_matches` と同じ絞り込み（完全一致）。
    const exact: [string, string][] = [
      ["run_id", "run_id"],
      ["tool_name", "tool_name"],
      ["status", "status"],
      ["approval_status", "approval_status"],
      ["error_code", "error_code"],
    ];
    const warnings = query.get("has_guardrail_warnings");
    const matched = state.auditRecords.filter(
      (record) =>
        exact.every(([param, field]) => !query.has(param) || record[field] === query.get(param)) &&
        (warnings === null ||
          ((record.guardrail_warnings as unknown[] | undefined) ?? []).length > 0 === (warnings === "true"))
    );
    return {
      ...pageOfItems(matched, { offset, limit }),
      filters: Object.fromEntries([...query.entries()].filter(([key]) => key !== "offset" && key !== "limit")),
      // 絞り込みに依らない、記録されたツール名（#983）。
      tool_names: [...new Set(state.auditRecords.map((record) => String(record.tool_name)))].sort(),
    };
  }
  if (method === "GET" && at("tools")) return { tools: state.tools };

  // --- 業務 Agent ---
  if (head === "agents") {
    // 版に残す項目（#770）と、登録されていないスキルの検証（backend の `_validate_agent_skills`。#925）。
    const VERSIONED = ["name", "description", "instructions", "skill_ids", "model_id", "data_scopes"] as const;
    // 範囲に新しく加えるプロファイルは、編集者が使える（候補にある）ものだけ（backend の `_verify_data_scopes`。#1378）。
    const requireUsableScopes = (requested: unknown, current: unknown) => {
      const scopes = (requested ?? {}) as Record<string, { profile_ids?: string[] }>;
      const before = (current ?? {}) as Record<string, { profile_ids?: string[] }>;
      for (const [connection, scope] of Object.entries(scopes)) {
        const known = new Set((state.dataScopeCandidates[connection] ?? []).map((item) => String(item.id)));
        const existing = new Set(before[connection]?.profile_ids ?? []);
        const missing = (scope.profile_ids ?? []).filter((id) => !existing.has(id) && !known.has(id));
        if (missing.length) {
          throw new HttpError(400, `プロファイル「${missing.join("、")}」は、あなたが使えないか見つかりません。候補から選んでください。`);
        }
      }
    };
    const requireKnownSkills = (skillIds: unknown) => {
      const known = new Set(state.skills.map((skill) => String(skill.id)));
      const unknown = [...new Set(((skillIds as string[] | undefined) ?? []).filter((id) => !known.has(id)))].sort();
      if (unknown.length) {
        throw new HttpError(400, `登録されていないスキルがあります: ${unknown.join(", ")}。スキルの選択から外してください。`);
      }
    };
    // 公開中の版と下書きの版の項目が違うか（backend の `has_unpublished_changes`）。
    const unpublishedChanges = (agent: Json) => {
      const published = ((agent.versions as Json[] | undefined) ?? []).find(
        (item) => item.version === agent.published_version
      );
      if (!published) return true;
      // データの範囲は、無い・空（{}）を同じとみなす（backend は空の範囲を {} で持つ）。
      const comparable = (field: string, value: unknown) =>
        field === "data_scopes" && (!value || Object.keys(value as Json).length === 0) ? null : (value ?? null);
      return VERSIONED.some(
        (field) => JSON.stringify(comparable(field, agent[field])) !== JSON.stringify(comparable(field, published[field]))
      );
    };
    // 一覧は利用者の対象範囲の業務 Agent だけ（backend の list_agents）。
    if (method === "GET" && at("agents")) return { agents: state.agents.filter((agent) => agentAllowed(state, agent.id)) };
    if (method === "POST" && at("agents")) {
      // backend の create_agent と同じく、送られた ID は URL に置ける形だけを受け付ける（#1033）。
      const id = String(body.id ?? `agent-${state.agents.length + 1}`).trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(id)) {
        throw new HttpError(422, "業務 Agent の ID は英数字で始め、英数字・_・-・. の 100 文字以内にしてください。");
      }
      if (state.agents.some((agent) => agent.id === id)) {
        throw new HttpError(400, "同じ ID の業務 Agent があります。");
      }
      // backend の `_require_agent_name` と、知らない業種テンプレートの 400（#810 / #925）。
      if (!String(body.name ?? "").trim()) throw new HttpError(422, "業務 Agent の名前を入力してください。");
      if (body.template_id && !state.agentTemplates.some((template) => template.id === body.template_id)) {
        throw new HttpError(400, "業種テンプレートが見つかりません。");
      }
      requireKnownSkills(body.skill_ids);
      requireUsableScopes(body.data_scopes, null);
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
        id,
        // 画面・API で作る Agent は下書きから始める（#770）。
        versioned: true,
        versions: [],
        published_version: null,
        unpublished_changes: true,
      };
      state.agents.push(agent);
      return agent;
    }
    if (method === "PATCH" && at("agents", "*")) {
      const agent = findOr404(state.agents, "id", second, "agent");
      if (body.name !== undefined && body.name !== null && !String(body.name).trim()) {
        throw new HttpError(422, "業務 Agent の名前を入力してください。");
      }
      if (body.skill_ids !== undefined) requireKnownSkills(body.skill_ids);
      if (body.data_scopes !== undefined) requireUsableScopes(body.data_scopes, agent.data_scopes);
      // 変えられるのは AgentProfilePatch の項目だけ（版・由来などは送られても変えない）。
      const patchable = ["name", "description", "instructions", "skill_ids", "model_id", "data_scopes", "tool_names", "enabled"];
      const patch = Object.fromEntries(
        Object.entries(body).filter(([key, value]) => patchable.includes(key) && value !== null && value !== undefined)
      );
      Object.assign(agent, patch, { updated_at: MOCK_NOW });
      // スキルを選び直すと移行が済む（backend の patch_agent）。
      if (Array.isArray(body.skill_ids)) agent.migration_required = false;
      // 有効の切り替えなど版に残さない項目だけの変更は「公開していない変更」にしない（backend と同じ）。
      agent.unpublished_changes = unpublishedChanges(agent);
      return agent;
    }
    // 下書きを版として公開する・前の版に戻す（#770）。
    if (method === "POST" && at("agents", "*", "publish")) {
      const agent = findOr404(state.agents, "id", second, "agent");
      requireKnownSkills(agent.skill_ids);
      const versions = (agent.versions as Json[] | undefined) ?? [];
      const version = Math.max(0, ...versions.map((item) => Number(item.version))) + 1;
      const snapshot: Json = { version, note: body.note ?? "", published_at: MOCK_NOW, published_by: "local" };
      for (const field of VERSIONED) {
        snapshot[field] = clone(agent[field] ?? (field === "skill_ids" ? [] : field === "data_scopes" ? {} : ""));
      }
      agent.versions = [...versions, snapshot];
      agent.published_version = version;
      agent.unpublished_changes = false;
      return agent;
    }
    if (method === "POST" && at("agents", "*", "versions", "*", "restore")) {
      const agent = findOr404(state.agents, "id", second, "agent");
      const target = ((agent.versions as Json[] | undefined) ?? []).find(
        (item) => String(item.version) === segments[3]
      );
      if (!target) throw new HttpError(404, "業務 Agent の版が見つかりません。");
      for (const field of VERSIONED) agent[field] = clone(target[field]);
      agent.published_version = target.version;
      agent.unpublished_changes = false;
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
      // backend の create_agent_skill と同じ検証（#926）。
      const id = String(body.id ?? "").trim();
      if (!id) throw new HttpError(400, "スキルの ID を入力してください。");
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(id)) {
        throw new HttpError(422, "スキルの ID は英数字で始め、英数字・_・-・. の 100 文字以内にしてください。");
      }
      if (state.skills.some((skill) => skill.id === id)) {
        throw new HttpError(409, "同じ ID のスキルがあります。");
      }
      const skill = skillFromPayload(body, "runtime");
      state.skills.push(skill);
      return skill;
    }
    if (at("skills", "*")) {
      const skill = findOr404(state.skills, "id", second, "skill");
      if (skill.source !== "runtime" && method === "PATCH") {
        throw new HttpError(
          400,
          "組み込み・ファイル・環境変数・プラグインのスキルは画面から変更できません。読み込み元の定義を変更してください。"
        );
      }
      if (skill.source !== "runtime" && method === "DELETE") {
        throw new HttpError(400, "組み込み・ファイル・環境変数・プラグインのスキルは画面から削除できません。");
      }
      if (method === "GET") return skill;
      if (method === "PATCH") {
        Object.assign(skill, skillFromPayload(body, "runtime", skill));
        return skill;
      }
      if (method === "DELETE") {
        // 業務 Agent の下書きか公開中の版が使っていれば断る（backend の delete_agent_skill。#926）。
        const users = state.agents
          .filter((agent) => {
            const published = ((agent.versions as Json[] | undefined) ?? []).find(
              (item) => item.version === agent.published_version
            );
            return [agent.skill_ids, published?.skill_ids].some((ids) => ((ids as string[] | undefined) ?? []).includes(second));
          })
          .map((agent) => String(agent.name || agent.id));
        if (users.length) {
          throw new HttpError(
            409,
            `このスキルは業務 Agent（${users.join("、")}）が使っています。業務 Agent のスキルから外してから削除してください。`
          );
        }
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
        // backend の add_plugin_marketplace と同じ検証（#928。同じ ID は上書きせず 409）。
        const id = String(body.id ?? "").trim();
        if (!id) throw new HttpError(400, "マーケットプレイスの ID を入力してください。");
        if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(id)) {
          throw new HttpError(
            422,
            "マーケットプレイスの ID は英数字で始め、英数字・_・-・. の 100 文字以内にしてください。"
          );
        }
        if (state.marketplaces.some((source) => source.id === id)) {
          throw new HttpError(409, "同じ ID のマーケットプレイスがあります。");
        }
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
        if (!source.plugin_count) return { name: source.name, plugins: [] };
        return {
          ...MOCK_MARKETPLACE_LISTING,
          plugins: MOCK_MARKETPLACE_LISTING.plugins.map(publicPluginManifest),
        };
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
        throw new HttpError(409, "このプラグインは導入済みです。");
      }
      const record = pluginRecord(manifest, marketplaceId);
      state.plugins.push(record);
      for (const skill of (manifest.skills as Json[] | undefined) ?? []) {
        state.skills.push(skillFromPayload(skill, `plugin:${String(manifest.id)}`));
      }
      return record;
    }
    if (at("plugins", "*")) {
      const plugin = state.plugins.find((candidate) => candidate.id === second);
      if (!plugin) throw new HttpError(404, "プラグインが見つかりません。");
      if (method === "GET") return plugin;
      // 業務 Agent が下書きか公開中の版で使うスキルを含むプラグインは、無効化・削除しない
      // （backend の PluginRegistry._ensure_not_referenced。#1032）。
      if ((method === "PATCH" && body.enabled === false) || method === "DELETE") {
        const manifest = (plugin.manifest as Json | undefined) ?? {};
        const pluginSkillIds = new Set(((manifest.skills as Json[] | undefined) ?? []).map((skill) => String(skill.id)));
        const users = state.agents
          .filter((agent) => {
            const published = ((agent.versions as Json[] | undefined) ?? []).find(
              (item) => item.version === agent.published_version
            );
            return [agent.skill_ids, published?.skill_ids].some((ids) =>
              ((ids as string[] | undefined) ?? []).some((id) => pluginSkillIds.has(id))
            );
          })
          .map((agent) => String(agent.name || agent.id));
        if (users.length) {
          throw new HttpError(
            409,
            `このプラグインのスキルは業務 Agent（${users.join("、")}）が使っています。業務 Agent のスキルから外して公開してから、無効化・削除してください。`
          );
        }
      }
      if (method === "PATCH") {
        if (body.enabled !== undefined) plugin.enabled = Boolean(body.enabled);
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
    // backend と同じく、無効なスナップショットと確認の無い置換は 400（#1027）。
    if (!validation.valid) {
      throw new HttpError(
        400,
        `スナップショットに ${validation.errors.length} 件のエラーがあるため置換できません。「検証」でエラーの内容を確認してください。`
      );
    }
    if (body.confirm_replace !== true) {
      throw new HttpError(400, "置換するには確認（confirm_replace=true）が必要です。");
    }
    const replaced = body.snapshot as Json;
    state.runs = clone((replaced.runs as Json[] | undefined) ?? []);
    state.agents = clone((replaced.agents as Json[] | undefined) ?? []);
    return { imported: true, dry_run: false, validation, reason: body.reason ?? null };
  }

  // --- 設定 ---
  if (head === "settings" && second === "api-keys") {
    if (method === "GET" && at("settings", "api-keys")) {
      // キーの業務 Agent の名前は、閲覧者が利用できる業務 Agent だけ（backend の list_api_keys と同じ）。
      const scope = state.auth.currentUser?.allowed_agent_ids ?? null;
      const keyAgentIds = new Set(state.apiKeys.flatMap((key) => (key.agent_ids as string[] | null) ?? []));
      const agentNames = Object.fromEntries(
        state.agents
          .filter((agent) => keyAgentIds.has(String(agent.id)) && (scope === null || scope.includes(String(agent.id))))
          .map((agent) => [agent.id, agent.name])
      );
      return { keys: state.apiKeys, persistent: state.apiKeysPersistent, agent_names: agentNames };
    }
    if (method === "POST" && at("settings", "api-keys")) {
      // backend と同じ確認: 空の業務 Agent の選択は 422、ほかの利用者として動くキーはシステム管理者だけ、
      // 知らない業務 Agent は 422。
      if (Array.isArray(body.agent_ids) && !(body.agent_ids as string[]).some((id) => id.trim())) {
        throw new HttpError(422, "body.agent_ids: Value error, 業務 Agent を 1 つ以上選ぶか、すべてにしてください。");
      }
      const creator = state.auth.currentUser;
      if (body.run_as_user_uuid && creator && body.run_as_user_uuid !== creator.user_uuid && !creator.is_system_admin) {
        throw new HttpError(403, "ほかの利用者として動くキーは、システム管理者だけが作れます。");
      }
      const unknownAgents = ((body.agent_ids as string[] | null | undefined) ?? [])
        .filter((agentId) => !state.agents.some((agent) => agent.id === agentId))
        .sort();
      if (unknownAgents.length) throw new HttpError(422, `業務 Agent が見つかりません: ${unknownAgents.join(", ")}`);
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
      const listData = () => ({ connections: store.connections.map(publicMcpConnection) });
      if (method === "GET" && at("settings", "mcp-connections")) return listData();
      if (method === "POST" && at("settings", "mcp-connections")) {
        const id = String(body.server_id ?? "").trim();
        if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/.test(id) || id.includes("__")) {
          throw new HttpError(422, "接続 ID は英数字で始まる 40 文字以内の英数字・「-」・「_」で入力してください。");
        }
        validateMcpUrl(body);
        if (store.connections.some((connection) => connection.server_id === id)) {
          throw new HttpError(409, "MCP 接続の ID はすでに使われています。");
        }
        const connection = mcpConnection({ ...body, server_id: id });
        store.connections.push(connection);
        store.connections.sort((left, right) => String(left.server_id).localeCompare(String(right.server_id)));
        return publicMcpConnection(connection);
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
          validateMcpUrl(body);
          // RAG / NL2SQL の認証方式・audience は変えられない（backend と同じ 400。#1014）。
          const builtinAuthChanged =
            connection.source === "builtin" &&
            ((body.auth_mode != null && body.auth_mode !== connection.auth_mode) ||
              (body.service_audience != null &&
                (String(body.service_audience).trim() || third) !== connection.service_audience));
          if (builtinAuthChanged) {
            throw new HttpError(400, "標準の接続（RAG / NL2SQL）の認証方式と audience は変えられません。");
          }
          // 標準の接続の名前と、配備が決めた URL は変えられない（backend と同じ 400。#1325）。
          if (
            connection.source === "builtin" &&
            body.label != null &&
            (String(body.label).trim() || null) !== connection.label
          ) {
            throw new HttpError(400, "標準の接続（RAG / NL2SQL）の名前は変えられません。");
          }
          if (
            connection.base_url_locked &&
            body.base_url != null &&
            (String(body.base_url) || null) !== connection.base_url
          ) {
            const envName = third === "rag" ? "AGENT_EXTERNAL_RAG_MCP_URL" : "AGENT_EXTERNAL_NL2SQL_MCP_URL";
            throw new HttpError(
              400,
              `この接続の URL は配備（${envName}）で設定しているため、画面・API では変えられません。変えるときは配備の設定を変えて再起動してください。`
            );
          }
          Object.assign(connection, mcpConnection({ ...body, server_id: third }, connection));
          return publicMcpConnection(connection);
        }
        if (method === "DELETE") {
          if (!connection.removable) {
            throw new HttpError(400, "標準の接続（RAG / NL2SQL）・宣言・プラグインの MCP 接続は削除できません。");
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

/** Run の処理の段階のイベント（spec が入れていなければ、状態から作る最小のイベント。#1359）。 */
function runProgressEvents(run: Json): Json[] {
  return Array.isArray(run.progress_events) ? (run.progress_events as Json[]) : progressEventsForStatus(run);
}

/** polling の応答（backend の `chat_progress_page`）。 */
function progressPage(run: Json, since: number): Json {
  const events = runProgressEvents(run);
  return {
    target_id: run.id,
    attempt: 0,
    events: events.filter((event) => Number(event.seq) > since),
    last_seq: events.length > 0 ? Number(events[events.length - 1].seq) : 0,
    terminal: events.some((event) => event.type === "terminal"),
  };
}

function isRunPayload(value: unknown): value is Json {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Json).goal === "string" &&
    typeof (value as Json).status === "string" &&
    Array.isArray((value as Json).steps)
  );
}

/** 応答の Run（単体・`runs` の一覧）に処理の段階のイベントを入れる（state は書き換えない）。 */
function withRunProgress(data: unknown): unknown {
  const decorate = (run: Json): Json => ({ ...run, progress_events: runProgressEvents(run) });
  if (isRunPayload(data)) return decorate(data);
  if (typeof data === "object" && data !== null && Array.isArray((data as Json).runs)) {
    return { ...(data as Json), runs: ((data as Json).runs as unknown[]).map((run) => (isRunPayload(run) ? decorate(run) : run)) };
  }
  return data;
}

/**
 * 処理の段階の SSE（backend の `chat_progress_sse_response`）。e2e は stream を保てないため、続き（`since` と
 * `Last-Event-ID` の大きい方）のイベントと heartbeat を送って閉じる。ブラウザが `retry` の後に `Last-Event-ID` で
 * 張り直すので、続けて届く。終わった Run を続きから求めたら 204。
 */
async function fulfillProgressStream(route: Route, state: MockApiState, runId: string, url: URL, headers: Record<string, string>) {
  const run = state.runs.find((candidate) => candidate.id === runId);
  if (!run) {
    await fulfillHttpError(route, new HttpError(404, "実行が見つかりません。"));
    return;
  }
  if (!agentAllowed(state, run.agent_id)) {
    await fulfillHttpError(route, new HttpError(403, "この業務 Agent を使う権限がありません。"));
    return;
  }
  const lastEventId = Number(headers["last-event-id"] ?? 0);
  const cursor = Math.max(Number(url.searchParams.get("since") ?? 0), Number.isFinite(lastEventId) ? lastEventId : 0);
  const page = progressPage(run, cursor);
  if (page.terminal && cursor >= Number(page.last_seq)) {
    await route.fulfill({ status: 204, headers: { "Cache-Control": "no-cache" }, body: "" });
    return;
  }
  const lines = ["retry: 500\n\n"];
  for (const event of page.events as Json[]) {
    lines.push(`id: ${String(event.seq)}\nevent: chat_progress\ndata: ${JSON.stringify(event)}\n\n`);
  }
  lines.push(`event: heartbeat\ndata: ${JSON.stringify({ last_seq: page.last_seq })}\n\n`);
  await route.fulfill({ status: 200, contentType: "text/event-stream", headers: { "Cache-Control": "no-cache" }, body: lines.join("") });
}

async function fulfillJson(route: Route, status: number, payload: unknown) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });
}

async function fulfillHttpError(route: Route, error: HttpError) {
  await fulfillJson(route, error.status, {
    data: null,
    error_messages: [error.message],
    warning_messages: [],
    error_code: error.errorCode ?? null,
    detail: error.message,
  });
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
    // backend と同じ権限の判定（manifest と capability。#1113）。
    try {
      enforceBackendPermissions(mockApi.state, method, url.pathname);
    } catch (error) {
      if (!(error instanceof HttpError)) throw error;
      await fulfillHttpError(route, error);
      return;
    }
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
    const progressStream = /^\/api\/runs\/([^/]+)\/progress\/stream$/.exec(url.pathname);
    if (method === "GET" && progressStream) {
      await fulfillProgressStream(route, mockApi.state, decodeURIComponent(progressStream[1]), url, await request.allHeaders());
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
      await fulfillJson(route, 200, { data: withRunProgress(data), error_messages: [], warning_messages: [] });
    } catch (error) {
      if (error instanceof HttpError) {
        await fulfillHttpError(route, error);
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
