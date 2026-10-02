// OCI 認証 API の型は platform の共有パッケージが正本（#100）。
// モデル設定の API 型は3製品共通（platform の共有パッケージ。#103）。
// データベース設定の API 型は3製品共通（platform の共有パッケージ。#108）。
export type {
  AdbInfoData,
  AdbOperationStatus,
  AdbSettingsUpdate,
  DatabaseConnectionSecurity,
  DatabaseConnectionTestResult,
  DatabaseConnectionTestStatus,
  DatabasePasswordRevealData,
  DatabaseSettingsData,
  DatabaseSettingsUpdate,
  DatabaseStatusData,
  DatabaseWalletDownloadData,
} from "@engchina/production-ready-system-settings";
import type {
  AdbInfoData,
  AdbSettingsUpdate,
  DatabaseConnectionTestResult,
  DatabaseSettingsData,
  DatabaseSettingsUpdate,
  DatabaseStatusData,
  DatabaseWalletDownloadData,
} from "@engchina/production-ready-system-settings";
// システムテーブルの API 型は3製品共通（platform の共有パッケージ。#325 / #751）。
import type {
  SystemTablesInitializeRequest,
  SystemTablesOperationData,
  SystemTablesStatusData,
} from "@engchina/production-ready-system-settings";
export type {
  EnterpriseAiConfiguredModel,
  EnterpriseAiModelSettings,
  EnterpriseAiVlmInputMode,
  GenerativeAiModelSettings,
  ModelSettingsData,
  ModelSettingsPayload,
  ModelSettingsSecretSource,
  ModelSettingsTestRequest,
  ModelSettingsTestResult,
  ModelSettingsTestStatus,
  ModelSettingsTestTargetType,
} from "@engchina/production-ready-system-settings";
// Cookie セッションの CSRF と 401 / 403 の通知は3製品共通（platform の共有パッケージ。#220 / #215）。
import { csrfHeader, notifyAuthResponse, type BaseCurrentUser } from "@engchina/production-ready-system-settings";
import type {
  ModelSettingsData,
  ModelSettingsPayload,
  ModelSettingsTestRequest,
  ModelSettingsTestResult,
} from "@engchina/production-ready-system-settings";
export type {
  OciConfigField,
  OciConfigReadData,
  OciConfigReadRequest,
  OciConfigTestResult,
  OciConfigTestStage,
  OciConfigTestStageKey,
  OciConfigTestStageStatus,
  OciConfigTestStatus,
  OciObjectStorageNamespaceData,
  OciObjectStorageNamespaceRequest,
  OciObjectStorageSettingsUpdate,
  OciPrivateKeyUploadData,
  OciSettingsData,
  OciSettingsUpdate,
} from "@engchina/production-ready-system-settings";
import type {
  OciConfigReadData,
  OciConfigReadRequest,
  OciConfigTestResult,
  OciObjectStorageNamespaceData,
  OciObjectStorageNamespaceRequest,
  OciObjectStorageSettingsUpdate,
  OciPrivateKeyUploadData,
  OciSettingsData,
  OciSettingsUpdate,
} from "@engchina/production-ready-system-settings";

// アップロード保存先 API の型は platform の共有パッケージが正本（#97）。
export type {
  UploadStorageBackend,
  UploadStorageSettingsData,
  UploadStorageSettingsUpdate,
} from "@engchina/production-ready-system-settings";
import type {
  UploadStorageSettingsData,
  UploadStorageSettingsUpdate,
} from "@engchina/production-ready-system-settings";
export interface ApiResponse<T> {
  data: T;
}

export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  permission_level: "read" | "write" | "sensitive";
  side_effects: boolean;
  timeout_seconds: number;
  max_retries: number;
  audit_tags: string[];
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  trace_id?: string;
}

export interface ToolResult {
  name: string;
  success: boolean;
  output?: Record<string, unknown> | null;
  error?: string | null;
  error_code?: string | null;
  error_details: Record<string, unknown>;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  policy_decision: "allow" | "ask" | "deny";
  approval_required: boolean;
  approval_id?: string | null;
  guardrail_warnings: string[];
  audit_metadata: Record<string, unknown>;
}

export interface RunStep {
  id: string;
  run_id: string;
  kind: string;
  status:
    | "pending"
    | "running"
    | "waiting_approval"
    | "completed"
    | "failed"
    | "cancelled";
  tool_call?: ToolCall | null;
  tool_result?: ToolResult | null;
  approval_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface RunEvent {
  id: string;
  run_id: string;
  type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ApprovalRequest {
  id: string;
  run_id: string;
  step_id: string;
  tool_call: ToolCall;
  status: "pending" | "approved" | "rejected" | "cancelled";
  reason: string;
  decided_by?: string | null;
  decided_at?: string | null;
  created_at: string;
}

export interface RunState {
  id: string;
  goal: string;
  agent_id: string;
  /** 実行した Runtime（新しい Run は組み込み Runtime の "builtin"。#754）。 */
  runtime_id: string;
  status:
    | "queued"
    | "running"
    | "waiting_approval"
    | "completed"
    | "failed"
    | "cancelled";
  steps: RunStep[];
  events: RunEvent[];
  approvals: ApprovalRequest[];
  artifacts: Artifact[];
  pending_tool_calls: ToolCall[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ToolAuditRecord {
  step_id: string;
  tool_name: string;
  status: string;
  approval_id?: string | null;
  approval_status?: string | null;
  policy_decision?: string | null;
  permission_level?: string | null;
  side_effects?: boolean | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  success?: boolean | null;
  error?: string | null;
  error_code?: string | null;
  guardrail_warnings: string[];
  trace_id?: string | null;
  artifact_ids: string[];
  audit_metadata: Record<string, unknown>;
}

export interface RunAuditData {
  run_id: string;
  goal: string;
  status: string;
  records: ToolAuditRecord[];
}

export interface ToolCallAuditRecord extends ToolAuditRecord {
  run_id: string;
  run_goal: string;
  run_status: string;
  agent_id: string;
  run_created_at: string;
  run_updated_at: string;
}

export interface ToolCallAuditData {
  total: number;
  offset: number;
  limit: number;
  filters: Record<string, unknown>;
  records: ToolCallAuditRecord[];
}

export interface ToolCallAuditFilters {
  run_id?: string;
  tool_name?: string;
  status?: string;
  approval_status?: string;
  error_code?: string;
  has_guardrail_warnings?: boolean;
  offset?: number;
  limit?: number;
}

export interface Artifact {
  id: string;
  name: string;
  kind: string;
  content: Record<string, unknown>;
  content_ref?: {
    backend: string;
    uri: string;
    content_type: string;
    size_bytes?: number | null;
    sha256?: string | null;
  } | null;
  created_at: string;
}

export interface AgentProfile {
  id: string;
  name: string;
  description: string;
  instructions: string;
  skill_ids: string[];
  /** 組み込み Runtime で使うモデル（空なら既定のテキストモデル。#754）。 */
  model_id?: string;
  migration_required: boolean;
  tool_names?: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentProfileWritePayload {
  id?: string;
  name: string;
  description?: string;
  instructions?: string;
  skill_ids: string[];
  model_id?: string;
  enabled: boolean;
}

export interface AgentProfilePatchPayload {
  name?: string;
  description?: string;
  instructions?: string;
  skill_ids?: string[];
  model_id?: string;
  enabled?: boolean;
}

export type McpAuthMode = "none" | "api_key" | "oauth_client_credentials" | "service_token";

/** MCP 接続（#757。RAG / NL2SQL / 外部 MCP）。資格情報の値は返らず、設定済みかだけを返す。 */
export interface McpConnectionSettings {
  server_id: string;
  label?: string | null;
  base_url?: string | null;
  auth_mode: McpAuthMode;
  /** サービストークンの aud（呼び先の製品名）。service_token のときだけ。 */
  service_audience?: string | null;
  timeout_seconds: number;
  /** builtin（RAG / NL2SQL）/ env / plugin:<id> / runtime。runtime だけ削除できる。 */
  source: string;
  removable: boolean;
  /** URL と認証方式に必要な資格情報がそろっているか。 */
  configured: boolean;
  api_key_configured: boolean;
  oauth_configured: boolean;
  session_configured: boolean;
  /** 共通 .env の PLATFORM_SERVICE_TOKEN_SECRET が 32 文字以上あるか（値は返らない）。 */
  service_token_configured: boolean;
  /** AGENT_MCP_SERVICE_USER_LOGIN_ID があるか（Run の利用者がいない呼び出しで使う）。 */
  service_user_configured: boolean;
}

export interface McpConnectionsData {
  connections: McpConnectionSettings[];
}

export interface McpConnectionWritePayload {
  server_id?: string;
  label?: string | null;
  base_url?: string | null;
  auth_mode?: McpAuthMode;
  api_key?: string | null;
  timeout_seconds?: number;
  session_id?: string | null;
  oauth_token_url?: string | null;
  oauth_client_id?: string | null;
  oauth_client_secret?: string | null;
  oauth_scope?: string | null;
  service_audience?: string | null;
}

export interface ExternalMcpToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  server_id?: string | null;
  /** MCP の readOnlyHint。false のツールは既定で承認が必要。 */
  read_only: boolean;
  /** Agent のモデルに渡すツール名（`<接続>__<ツール>`）。 */
  function_name?: string | null;
  metadata: Record<string, unknown>;
}

export interface ExternalMcpToolsData {
  tools: ExternalMcpToolInfo[];
  metadata: Record<string, unknown>;
}

export interface AgentSkill {
  id: string;
  name: string;
  description: string;
  instructions: string;
  mcp_requirements: { server_id: string; tool_names: string[] }[];
  resource_ids: string[];
  enabled: boolean;
  tags: string[];
  source: string;
  created_at?: string;
  updated_at?: string;
}

export interface AgentSkillListData {
  skills: AgentSkill[];
  metadata: Record<string, unknown>;
}

export interface AgentSkillWritePayload {
  id?: string;
  name?: string;
  description?: string;
  instructions?: string;
  mcp_requirements?: { server_id: string; tool_names: string[] }[];
  resource_ids?: string[];
  enabled?: boolean;
  tags?: string[];
}

export interface PluginManifest {
  id: string;
  name: string;
  version?: string;
  description?: string;
  author?: string;
  skills?: AgentSkill[];
  mcp_servers?: Record<string, unknown>[];
  resources?: PluginResource[];
  /** @deprecated v1 manifest compatibility only. */
  agents?: Record<string, unknown>[];
}

export interface PluginResource {
  id: string;
  kind: "prompt" | "workflow" | "template";
  name: string;
  version: string;
  media_type: string;
  content: string | Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface PluginSummary {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  enabled: boolean;
  marketplace_id?: string | null;
  skill_count: number;
  mcp_count: number;
  resource_count: number;
  warnings: string[];
  agent_count: number;
}

export interface PluginRecord extends PluginSummary {
  manifest: PluginManifest;
}

export interface PluginListData {
  plugins: PluginSummary[];
  metadata: Record<string, unknown>;
}

export interface MarketplaceSource {
  id: string;
  name: string;
  url?: string | null;
  plugin_count: number;
  last_error?: string | null;
}

export interface MarketplaceSourcesData {
  marketplaces: MarketplaceSource[];
}

export interface MarketplaceListing {
  name: string;
  plugins: PluginManifest[];
}

export interface TraceExportRetryData {
  attempted: number;
  succeeded: number;
  requeued: number;
  dropped: number;
  skipped: number;
  queue_size: number;
}

export interface TracePolicySettings {
  trace_events_enabled: boolean;
  trace_events_buffer_size: number;
  trace_events_retention_seconds: number;
  trace_sample_rate: number;
}

export interface ToolPolicySettings {
  default_mode: "approval" | "deny";
  allow: string[];
  ask: string[];
  deny: string[];
}

export interface RuntimeSnapshot {
  version: string;
  exported_at: string;
  runs: RunState[];
  agents: AgentProfile[];
  control_plane_state: Record<string, unknown>;
}

export interface RuntimeSnapshotSummary {
  runs: number;
  agents: number;
  events: number;
  steps: number;
  approvals: number;
  artifacts: number;
  pending_tool_calls: number;
}

export interface RuntimeSnapshotValidation {
  valid: boolean;
  errors: string[];
  warnings: string[];
  summary: RuntimeSnapshotSummary;
}

export interface RuntimeSnapshotImportResult {
  imported: boolean;
  dry_run: boolean;
  validation: RuntimeSnapshotValidation;
  reason?: string | null;
}

export interface RuntimeSnapshotImportPayload {
  snapshot: RuntimeSnapshot;
  dry_run: boolean;
  confirm_replace?: boolean;
  reason?: string | null;
}

export interface CreateRunPayload {
  goal: string;
  agent_id?: string;
  metadata?: Record<string, unknown>;
}

/** 組み込み Runtime で選べるモデル（システム設定 > モデル の登録モデル。#754）。 */
export interface BuiltinRuntimeModel {
  model_id: string;
  display_name: string;
}

/** 組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI）の状態。API key は含まない（#754）。 */
export interface BuiltinRuntimeStatus {
  id: string;
  name: string;
  sdk: string;
  sdk_version: string;
  model_provider: string;
  /** 既定のテキストモデル（空なら未設定）。 */
  model_id: string;
  ready: boolean;
  error_code?: string | null;
  message?: string | null;
  models: BuiltinRuntimeModel[];
}

/**
 * 承認の判断。決定者（`decided_by`）はログイン中の利用者から server が決めるため送らない（#215）。
 */
export interface ApprovalDecisionPayload {
  approved: boolean;
  comment?: string;
}

// --- 認証（platform の共通認証。#215） ---

/** CSRF の double submit に使う Cookie 名（backend の `app_auth_csrf_cookie_name` の既定値）。 */
export const CSRF_COOKIE_NAME = "agent_csrf";

/**
 * `GET /api/auth/me` などが返すログイン中の利用者。`permissions` は implies を展開済み。
 * `allowed_*_ids` が null なら制限なし（SYSTEM_ADMIN・`agent.admin`・ローカル DEBUG）。
 */
export interface CurrentUser extends BaseCurrentUser {
  allowed_agent_ids: string[] | null;
}

/** ロール（共通のロール項目に Agent の権限と対象範囲を足したもの）。 */
export interface SecurityRole {
  role_id: string;
  role_code: string;
  display_name: string;
  description: string;
  is_built_in: boolean;
  archived: boolean;
  version: number;
  permissions: string[];
  agent_ids: string[];
}

/** 権限管理で選べるエージェント（Runtime repository の業務 Agent）。status は enabled / disabled。 */
export interface AgentAccessTarget {
  id: string;
  name: string;
  description: string | null;
  status: string;
}


/** 権限管理の対象の候補の 1 ページ（検索とページング。#608）。 */
export interface AccessTargetPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_next: boolean;
}

/** 権限管理画面の保存（`PUT /api/security/roles/{role_id}/access`）。 */
export interface RoleAccessUpdate {
  role_id: string;
  version: number;
  permissions: string[];
  agent_ids: string[];
}

/** 入力項目に結び付く API の問題（JSON Pointer と表示文言）。 */
export interface ApiFieldError {
  pointer: string;
  message: string;
}

export interface ApiErrorDetails {
  /** 機械判定用のエラーコード（共通認証・ユーザー / ロール操作の `error_code`）。 */
  errorCode?: string;
  fieldErrors?: ApiFieldError[];
  requestId?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly messages: string[];
  readonly errorCode?: string;
  readonly fieldErrors: ApiFieldError[];
  readonly requestId?: string;

  constructor(status: number, messages: string[], details: ApiErrorDetails = {}) {
    super(messages[0] ?? `APIエラー (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.messages = messages.length > 0 ? messages : [`APIエラー (${status})`];
    this.errorCode = details.errorCode;
    this.fieldErrors = details.fieldErrors ?? [];
    this.requestId = details.requestId;
  }
}

function jsonBody(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

interface ErrorBody {
  detail?: unknown;
  error_messages?: unknown;
  error_code?: unknown;
  problem?: { field_errors?: unknown; request_id?: unknown } | null;
}

function fieldErrorsOf(value: unknown): ApiFieldError[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const { pointer, message } = item as { pointer?: unknown; message?: unknown };
    return typeof message === "string"
      ? [{ pointer: typeof pointer === "string" ? pointer : "", message }]
      : [];
  });
}

/** エラー応答（ApiResponse envelope / FastAPI の detail）から ApiError を作る。 */
async function apiErrorFrom(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  let body: ErrorBody = {};
  try {
    body = ((await response.json()) ?? {}) as ErrorBody;
    if (Array.isArray(body.error_messages) && typeof body.error_messages[0] === "string") {
      detail = body.error_messages[0];
    } else if (typeof body.detail === "string") {
      detail = body.detail;
    } else if (
      body.detail &&
      typeof body.detail === "object" &&
      "message" in body.detail &&
      typeof body.detail.message === "string"
    ) {
      detail = body.detail.message;
    }
  } catch {
    // Ignore non-JSON error bodies.
  }
  const problemRequestId =
    typeof body.problem?.request_id === "string" ? body.problem.request_id : undefined;
  return new ApiError(response.status, detail ? [detail] : [], {
    errorCode: typeof body.error_code === "string" ? body.error_code : undefined,
    fieldErrors: fieldErrorsOf(body.problem?.field_errors),
    requestId: response.headers.get("X-Request-ID") || problemRequestId,
  });
}

/** 状態を変える method のとき Cookie の CSRF token を `X-CSRF-Token` として付けた headers を作る。 */
function withCsrfHeaders(method: string | undefined, headers: Headers): Headers {
  for (const [name, value] of Object.entries(csrfHeader(CSRF_COOKIE_NAME, method ?? "GET"))) {
    headers.set(name, value);
  }
  return headers;
}


/**
 * Cookie セッションで API を呼ぶ（#215）。状態を変える method には CSRF header を付け、
 * 401 / 403 は共通の認証イベントで通知する。
 */
async function fetchWithSession(path: string, init: RequestInit = {}): Promise<Response> {
  const isFormData = init.body instanceof FormData;
  const headers = new Headers(isFormData ? undefined : { "Content-Type": "application/json" });
  new Headers(init.headers).forEach((value, name) => headers.set(name, value));
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: withCsrfHeaders(init.method, headers),
  });
  // 403 は error_code が経路の権限拒否のときだけ権限なしの画面へ移す。権限の付与の制限などは
  // 呼び出した画面がその場で理由を表示する（#224）。本文は消費しない。
  if (!response.ok) void notifyAuthResponse(response);
  return response;
}

/** ApiResponse を展開し data のみ返す。エラー時は ApiError を投げる。 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchWithSession(path, init);
  if (!response.ok) {
    throw await apiErrorFrom(response);
  }
  const json = (await response.json()) as ApiResponse<T>;
  return json.data;
}

/** ファイルの応答（CSV など）を Blob で受け取る。エラー時は ApiError を投げる。 */
async function requestBlob(path: string): Promise<Blob> {
  const response = await fetchWithSession(path);
  if (!response.ok) {
    throw await apiErrorFrom(response);
  }
  return response.blob();
}

function auditQuery(filters: ToolCallAuditFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === "") {
      continue;
    }
    params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const agentApi = {
  // 組み込み Runtime の状態（SDK の版・既定のモデル・選べるモデル。#754）。
  getRuntimeStatus: () => request<BuiltinRuntimeStatus>("/api/runtime/status"),
  listRuns: () => request<{ runs: RunState[] }>("/api/runs"),
  createRun: (payload: CreateRunPayload) =>
    request<RunState>("/api/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getRun: (runId: string) => request<RunState>(`/api/runs/${runId}`),
  getRunAudit: (runId: string) =>
    request<RunAuditData>(`/api/runs/${runId}/audit`),
  listToolCallAudit: (filters: ToolCallAuditFilters) =>
    request<ToolCallAuditData>(`/api/audit/tool-calls${auditQuery(filters)}`),
  /** 監査 CSV。Cookie セッションで取得し、401 / 403 は他の API と同じく通知する（#215）。 */
  downloadToolCallAuditCsv: (filters: ToolCallAuditFilters) =>
    requestBlob(`/api/audit/tool-calls.csv${auditQuery(filters)}`),
  listRunArtifacts: (runId: string) =>
    request<{ artifacts: Artifact[] }>(`/api/runs/${runId}/artifacts`),
  getRunArtifact: (runId: string, artifactId: string) =>
    request<Artifact>(`/api/runs/${runId}/artifacts/${artifactId}`),
  cancelRun: (runId: string) =>
    request<RunState>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  resumeRun: (runId: string) =>
    request<RunState>(`/api/runs/${runId}/resume`, { method: "POST" }),
  replayRun: (runId: string) =>
    request<RunState>(`/api/runs/${runId}/replay`, { method: "POST" }),
  decideApproval: (approvalId: string, payload: ApprovalDecisionPayload) =>
    request<RunState>(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listTools: () => request<{ tools: ToolDefinition[] }>("/api/tools"),
  listAgents: () => request<{ agents: AgentProfile[] }>("/api/agents"),
  createAgent: (payload: AgentProfileWritePayload) =>
    request<AgentProfile>("/api/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  patchAgent: (agentId: string, payload: AgentProfilePatchPayload) =>
    request<AgentProfile>(`/api/agents/${agentId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  getTracePolicySettings: () =>
    request<TracePolicySettings>("/api/settings/trace-policy"),
  patchTracePolicySettings: (payload: Partial<TracePolicySettings>) =>
    request<TracePolicySettings>("/api/settings/trace-policy", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  flushTraceExportRetryQueue: (limit = 100, force = false) =>
    request<TraceExportRetryData>(
      `/api/observability/export-retry/flush?limit=${limit}&force=${force}`,
      {
        method: "POST",
      },
    ),
  exportRuntimeSnapshot: () =>
    request<RuntimeSnapshot>("/api/runtime/snapshot"),
  importRuntimeSnapshot: (payload: RuntimeSnapshotImportPayload) =>
    request<RuntimeSnapshotImportResult>("/api/runtime/snapshot/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getToolPolicySettings: () =>
    request<ToolPolicySettings>("/api/settings/tool-policy"),
  patchToolPolicySettings: (payload: Partial<ToolPolicySettings>) =>
    request<ToolPolicySettings>("/api/settings/tool-policy", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listMcpConnections: () =>
    request<McpConnectionsData>("/api/settings/mcp-connections"),
  createMcpConnection: (payload: McpConnectionWritePayload) =>
    request<McpConnectionSettings>("/api/settings/mcp-connections", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateMcpConnection: (serverId: string, payload: McpConnectionWritePayload) =>
    request<McpConnectionSettings>(
      `/api/settings/mcp-connections/${encodeURIComponent(serverId)}`,
      { method: "PATCH", body: JSON.stringify(payload) },
    ),
  deleteMcpConnection: (serverId: string) =>
    request<McpConnectionsData>(
      `/api/settings/mcp-connections/${encodeURIComponent(serverId)}`,
      { method: "DELETE" },
    ),
  listMcpConnectionTools: (serverId: string) =>
    request<ExternalMcpToolsData>(
      `/api/settings/mcp-connections/${encodeURIComponent(serverId)}/tools`,
    ),
  listSkills: () => request<AgentSkillListData>("/api/skills"),
  getSkill: (skillId: string) =>
    request<AgentSkill>(`/api/skills/${encodeURIComponent(skillId)}`),
  createSkill: (payload: AgentSkillWritePayload) =>
    request<AgentSkill>("/api/skills", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateSkill: (skillId: string, payload: AgentSkillWritePayload) =>
    request<AgentSkill>(`/api/skills/${encodeURIComponent(skillId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteSkill: (skillId: string) =>
    request<AgentSkillListData>(`/api/skills/${encodeURIComponent(skillId)}`, {
      method: "DELETE",
    }),
  reloadSkills: () =>
    request<AgentSkillListData>("/api/skills/reload", { method: "POST" }),
  listPlugins: () => request<PluginListData>("/api/plugins"),
  getPlugin: (pluginId: string) =>
    request<PluginRecord>(`/api/plugins/${encodeURIComponent(pluginId)}`),
  installPlugin: (payload: {
    manifest?: PluginManifest;
    marketplace_id?: string;
    plugin_id?: string;
  }) =>
    request<PluginRecord>("/api/plugins", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setPluginEnabled: (pluginId: string, enabled: boolean) =>
    request<PluginRecord>(`/api/plugins/${encodeURIComponent(pluginId)}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  uninstallPlugin: (pluginId: string) =>
    request<PluginListData>(`/api/plugins/${encodeURIComponent(pluginId)}`, {
      method: "DELETE",
    }),
  reloadPlugins: () =>
    request<PluginListData>("/api/plugins/reload", { method: "POST" }),
  listPluginMarketplaces: () =>
    request<MarketplaceSourcesData>("/api/plugins/marketplaces"),
  addPluginMarketplace: (payload: {
    id: string;
    name?: string;
    url?: string | null;
    listing?: MarketplaceListing;
  }) =>
    request<MarketplaceSource>("/api/plugins/marketplaces", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  refreshPluginMarketplace: (marketplaceId: string) =>
    request<MarketplaceSource>(
      `/api/plugins/marketplaces/${encodeURIComponent(marketplaceId)}/refresh`,
      { method: "POST" },
    ),
  listMarketplacePlugins: (marketplaceId: string) =>
    request<MarketplaceListing>(
      `/api/plugins/marketplaces/${encodeURIComponent(marketplaceId)}/plugins`,
    ),
  deletePluginMarketplace: (marketplaceId: string) =>
    request<MarketplaceSourcesData>(
      `/api/plugins/marketplaces/${encodeURIComponent(marketplaceId)}`,
      { method: "DELETE" },
    ),
};

export const api = {
  // DB の状態（画面の DB ゲートが使う。3製品共通の判定と契約。ログイン不要。#325）。
  getDatabaseStatus: (options?: { signal?: AbortSignal }) =>
    request<DatabaseStatusData>("/api/ready/database", { signal: options?.signal }),

  // システムテーブル（運用設定。3製品共通のカードと契約。#751）。
  getSystemTablesStatus: (options?: { signal?: AbortSignal }) =>
    request<SystemTablesStatusData>("/api/settings/database/system-tables", { signal: options?.signal }),
  initializeSystemTables: (body: SystemTablesInitializeRequest) =>
    request<SystemTablesOperationData>("/api/settings/database/system-tables/initialize", jsonBody(body)),

  getModelSettings: () => request<ModelSettingsData>("/api/settings/model"),
  updateModelSettings: (body: ModelSettingsPayload) =>
    request<ModelSettingsData>("/api/settings/model", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  testModelSettings: (body: ModelSettingsTestRequest) =>
    request<ModelSettingsTestResult>(
      "/api/settings/model/test",
      jsonBody(body),
    ),

  getDatabaseSettings: () =>
    request<DatabaseSettingsData>("/api/settings/database"),
  updateDatabaseSettings: (body: DatabaseSettingsUpdate) =>
    request<DatabaseSettingsData>("/api/settings/database", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  uploadDatabaseWallet: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DatabaseSettingsData>("/api/settings/database/wallet", {
      method: "POST",
      body: form,
    });
  },
  downloadDatabaseWallet: () =>
    request<DatabaseWalletDownloadData>(
      "/api/settings/database/wallet/download",
      {
        method: "POST",
      },
    ),
  testDatabaseSettings: (body: DatabaseSettingsUpdate) =>
    request<DatabaseConnectionTestResult>(
      "/api/settings/database/test",
      jsonBody(body),
    ),

  getAdbInfo: () => request<AdbInfoData>("/api/settings/database/adb"),
  updateAdbSettings: (body: AdbSettingsUpdate) =>
    request<AdbInfoData>("/api/settings/database/adb/settings", jsonBody(body)),
  startAdb: () =>
    request<AdbInfoData>("/api/settings/database/adb/start", {
      method: "POST",
    }),
  stopAdb: () =>
    request<AdbInfoData>("/api/settings/database/adb/stop", { method: "POST" }),

  getUploadStorageSettings: () =>
    request<UploadStorageSettingsData>("/api/settings/upload-storage"),
  updateUploadStorageSettings: (body: UploadStorageSettingsUpdate) =>
    request<UploadStorageSettingsData>("/api/settings/upload-storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  getOciSettings: () => request<OciSettingsData>("/api/settings/oci"),
  updateOciSettings: (body: OciSettingsUpdate) =>
    request<OciSettingsData>("/api/settings/oci", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  updateOciObjectStorageSettings: (body: OciObjectStorageSettingsUpdate) =>
    request<UploadStorageSettingsData>("/api/settings/oci/object-storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  readOciConfig: (body: OciConfigReadRequest) =>
    request<OciConfigReadData>("/api/settings/oci/config/read", jsonBody(body)),
  testOciConfig: () =>
    request<OciConfigTestResult>("/api/settings/oci/config/test", {
      method: "POST",
    }),
  readOciObjectStorageNamespace: (body: OciObjectStorageNamespaceRequest) =>
    request<OciObjectStorageNamespaceData>(
      "/api/settings/oci/object-storage/namespace",
      jsonBody(body),
    ),
  uploadOciPrivateKey: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<OciPrivateKeyUploadData>("/api/settings/oci/key-file", {
      method: "POST",
      body: form,
    });
  },
};
