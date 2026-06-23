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
  status: "pending" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
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
  status: "queued" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
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
  tool_names: string[];
  command_allowed_prefixes: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentProfileWritePayload {
  id?: string;
  name: string;
  description?: string;
  instructions?: string;
  tool_names: string[];
  command_allowed_prefixes?: string[];
  enabled: boolean;
}

export interface AgentProfilePatchPayload {
  name?: string;
  description?: string;
  instructions?: string;
  tool_names?: string[];
  command_allowed_prefixes?: string[];
  enabled?: boolean;
}

export type MemoryKind = "run_summary" | "user_preference" | "tool_learning" | "note";

export interface MemoryEntry {
  id: string;
  kind: MemoryKind;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface MemoryCreatePayload {
  kind: MemoryKind;
  content: string;
  metadata?: Record<string, unknown>;
}

export interface ExternalServiceSettings {
  base_url?: string | null;
  api_key_configured: boolean;
  oauth_configured?: boolean;
  auth_mode?: "none" | "api_key" | "oauth_client_credentials" | string;
  session_configured?: boolean;
  timeout_seconds: number;
  default_limit?: number | null;
  configured: boolean;
}

export interface ExternalMcpToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  server_id?: string | null;
  metadata: Record<string, unknown>;
}

export interface ExternalMcpToolsData {
  tools: ExternalMcpToolInfo[];
  metadata: Record<string, unknown>;
}

export interface ExternalMcpToolsFilters {
  server_id?: string;
  trace_id?: string;
}

export interface ExternalMcpServerSettings extends ExternalServiceSettings {
  server_id: string;
  label?: string | null;
  is_default: boolean;
}

export interface ExternalMcpServersData {
  servers: ExternalMcpServerSettings[];
  default_server_id: string;
}

export interface ExternalMcpServerWritePayload {
  server_id?: string;
  label?: string | null;
  base_url?: string | null;
  timeout_seconds?: number;
  session_id?: string | null;
  oauth_token_url?: string | null;
  oauth_client_id?: string | null;
  oauth_client_secret?: string | null;
  oauth_scope?: string | null;
}

export interface AgentSkillToolCall {
  name: string;
  arguments?: Record<string, unknown>;
  trace_id?: string | null;
}

export interface AgentSkill {
  id: string;
  name: string;
  description: string;
  instructions: string;
  tool_calls: AgentSkillToolCall[];
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
  tool_calls?: AgentSkillToolCall[];
  enabled?: boolean;
  tags?: string[];
}

export interface ObservabilityStatus {
  metrics_enabled: boolean;
  prometheus_metrics_path: string;
  trace_events_enabled: boolean;
  trace_events_buffer_size: number;
  trace_events_retention_seconds: number;
  trace_sample_rate: number;
  trace_exporter_configured: boolean;
  trace_exporter_last_success_at?: string | null;
  trace_exporter_last_error?: string | null;
  retry_queue_size: number;
  retry_queue_max_size: number;
  retry_max_attempts: number;
  retry_worker_enabled: boolean;
  retry_worker_running: boolean;
  retry_worker_interval_seconds: number;
  langfuse_configured: boolean;
  opentelemetry_configured: boolean;
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

export interface RuntimeSafetySettings {
  max_tool_calls_per_run: number;
  max_pending_approvals_per_run: number;
}

export interface ToolPolicySettings {
  default_mode: "approval" | "deny";
  allow: string[];
  ask: string[];
  deny: string[];
}

export interface CommandPolicySettings {
  enabled: boolean;
  workspace_root: string;
  allowed_prefixes: string[];
  default_timeout_seconds: number;
  max_timeout_seconds: number;
  output_limit_bytes: number;
  artifact_storage_backend: "inline" | "filesystem";
  artifact_storage_path: string;
}

export interface RuntimeSnapshot {
  version: string;
  exported_at: string;
  runs: RunState[];
  agents: AgentProfile[];
  memory: MemoryEntry[];
}

export interface RuntimeSnapshotSummary {
  runs: number;
  agents: number;
  memory: number;
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
  tool_calls?: ToolCall[];
  metadata?: Record<string, unknown>;
}

export interface ApprovalDecisionPayload {
  approved: boolean;
  decided_by?: string;
  comment?: string;
}

export type ModelSettingsCheckStatus = "ok" | "missing" | "invalid";
export type ModelSettingsTestStatus = "success" | "failed";
export type ModelSettingsTestTargetType =
  | "enterprise_text"
  | "enterprise_vision"
  | "embedding"
  | "rerank";
export type UploadStorageBackend = "local" | "oci";
export type DatabaseConnectionTestStatus = "success" | "failed" | "skipped";
export type OciConfigTestStatus = "success" | "failed";

export interface EnterpriseAiConfiguredModel {
  model_id: string;
  display_name: string;
  vision_enabled: boolean;
}

export type EnterpriseAiVlmInputMode = "auto" | "files_api" | "inline_image";

export interface EnterpriseAiModelSettings {
  endpoint: string;
  project_ocid: string;
  api_key: string;
  has_api_key: boolean;
  clear_api_key: boolean;
  models: EnterpriseAiConfiguredModel[];
  default_model_id: string;
  api_path: string;
  vlm_input_mode: EnterpriseAiVlmInputMode;
  text_payload_template: string;
  vision_payload_template: string;
  text_response_path: string;
  vision_response_path: string;
  timeout_seconds: number;
  max_retries: number;
}

export interface GenerativeAiModelSettings {
  embedding_model: string;
  embedding_dim: number;
  rerank_model: string;
}

export interface ModelSettingsPayload {
  enterprise_ai: EnterpriseAiModelSettings;
  generative_ai: GenerativeAiModelSettings;
}

export interface ModelSettingsData {
  settings: ModelSettingsPayload;
  checks: Record<"enterprise_ai" | "generative_ai" | "embedding_dim", ModelSettingsCheckStatus>;
  model_settings_file: string;
  source: "runtime";
}

export interface ModelSettingsTestRequest {
  settings: ModelSettingsPayload;
  target_type: ModelSettingsTestTargetType;
  model_id: string;
  vision_enabled: boolean;
}

export interface ModelSettingsTestResult {
  status: ModelSettingsTestStatus;
  target_type: ModelSettingsTestTargetType;
  model_id: string;
  message: string;
  troubleshooting: string[];
  raw_error: string | null;
  error_type: string | null;
  elapsed_ms: number;
  checked_at: string;
  details: Record<string, string | number | boolean | null>;
}

export interface DatabaseSettingsData {
  user: string;
  dsn: string;
  wallet_dir: string;
  wallet_uploaded: boolean;
  available_services: string[];
  has_password: boolean;
  has_wallet_password: boolean;
  readiness: string;
  embedding_dimension: number;
  vector_column: string;
  adb_ocid: string;
  region: string;
  config_source: "runtime";
}

export type AdbOperationStatus =
  | "success"
  | "not_configured"
  | "error"
  | "accepted"
  | "already_available"
  | "already_stopped"
  | "cannot_start"
  | "cannot_stop";

export interface AdbInfoData {
  status: AdbOperationStatus;
  message: string;
  id: string | null;
  display_name: string | null;
  lifecycle_state: string | null;
  db_name: string | null;
  cpu_core_count: number | null;
  data_storage_size_in_tbs: number | null;
  region: string | null;
}

export interface AdbSettingsUpdate {
  adb_ocid: string;
  region: string;
}

export interface DatabaseSettingsUpdate {
  user: string;
  dsn: string;
  wallet_dir: string;
  password?: string;
  wallet_password?: string;
  clear_password?: boolean;
  clear_wallet_password?: boolean;
}

export interface DatabaseConnectionTestResult {
  status: DatabaseConnectionTestStatus;
  readiness: string;
  message: string;
  elapsed_ms: number;
  troubleshooting: string[];
  details: Record<string, string | number | boolean | null>;
  checked_at: string;
  error_type: string | null;
}

export interface UploadStorageSettingsData {
  backend: UploadStorageBackend;
  local_storage_dir: string;
  object_storage_region: string;
  object_storage_namespace: string;
  object_storage_bucket: string;
  readiness: string;
  max_upload_bytes: number;
  config_source: "runtime";
}

export interface UploadStorageSettingsUpdate {
  backend: UploadStorageBackend;
  local_storage_dir: string;
  object_storage_namespace?: string;
  object_storage_bucket: string;
}

export type OciConfigField = "user" | "fingerprint" | "tenancy" | "region" | "key_file";

export interface OciConfigReadRequest {
  config_file: string;
  profile: string;
}

export interface OciConfigReadData {
  profile: string;
  user: string;
  fingerprint: string;
  tenancy: string;
  region: string;
  key_file: string;
  applied_fields: OciConfigField[];
}

export interface OciSettingsUpdate {
  user: string;
  fingerprint: string;
  tenancy: string;
  region: string;
}

export interface OciSettingsData {
  config_file: string;
  profile: string;
  user: string;
  fingerprint: string;
  tenancy: string;
  region: string;
  key_file: string;
  key_file_exists: boolean;
  config_file_exists: boolean;
  config_source: "runtime";
}

export interface OciObjectStorageSettingsUpdate {
  object_storage_region: string;
  object_storage_namespace: string;
}

export interface OciConfigTestResult {
  status: OciConfigTestStatus;
  profile: string;
  config_file: string;
  key_file: string;
  config_file_exists: boolean;
  key_file_exists: boolean;
  missing_fields: OciConfigField[];
  permission_issues: string[];
  oci_directory_mode: string | null;
  config_file_mode: string | null;
  key_file_mode: string | null;
  message: string;
  checked_at: string;
  error_type: string | null;
}

export interface OciObjectStorageNamespaceRequest {
  config_file: string;
  profile: string;
  region: string;
}

export interface OciObjectStorageNamespaceData {
  namespace: string;
}

export interface OciPrivateKeyUploadData {
  key_file: string;
  saved: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly messages: string[];

  constructor(status: number, messages: string[]) {
    super(messages[0] ?? `APIエラー (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.messages = messages.length > 0 ? messages : [`APIエラー (${status})`];
  }
}

function jsonBody(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown; error_messages?: unknown };
      if (Array.isArray(body.error_messages) && typeof body.error_messages[0] === "string") {
        detail = body.error_messages[0];
      } else if (typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // Ignore non-JSON error bodies.
    }
    throw new ApiError(response.status, [detail]);
  }
  const json = (await response.json()) as ApiResponse<T>;
  return json.data;
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

function externalMcpToolsQuery(filters: ExternalMcpToolsFilters): string {
  const params = new URLSearchParams();
  if (filters.server_id) {
    params.set("server_id", filters.server_id);
  }
  if (filters.trace_id) {
    params.set("trace_id", filters.trace_id);
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const agentApi = {
  listRuns: () => request<{ runs: RunState[] }>("/api/runs"),
  createRun: (payload: CreateRunPayload) =>
    request<RunState>("/api/runs", { method: "POST", body: JSON.stringify(payload) }),
  getRun: (runId: string) => request<RunState>(`/api/runs/${runId}`),
  getRunAudit: (runId: string) => request<RunAuditData>(`/api/runs/${runId}/audit`),
  listToolCallAudit: (filters: ToolCallAuditFilters) =>
    request<ToolCallAuditData>(`/api/audit/tool-calls${auditQuery(filters)}`),
  toolCallAuditCsvUrl: (filters: ToolCallAuditFilters) =>
    `/api/audit/tool-calls.csv${auditQuery(filters)}`,
  listRunArtifacts: (runId: string) =>
    request<{ artifacts: Artifact[] }>(`/api/runs/${runId}/artifacts`),
  getRunArtifact: (runId: string, artifactId: string) =>
    request<Artifact>(`/api/runs/${runId}/artifacts/${artifactId}`),
  cancelRun: (runId: string) => request<RunState>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  resumeRun: (runId: string) => request<RunState>(`/api/runs/${runId}/resume`, { method: "POST" }),
  replayRun: (runId: string) => request<RunState>(`/api/runs/${runId}/replay`, { method: "POST" }),
  decideApproval: (approvalId: string, payload: ApprovalDecisionPayload) =>
    request<RunState>(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listTools: () => request<{ tools: ToolDefinition[] }>("/api/tools"),
  listAgents: () => request<{ agents: AgentProfile[] }>("/api/agents"),
  createAgent: (payload: AgentProfileWritePayload) =>
    request<AgentProfile>("/api/agents", { method: "POST", body: JSON.stringify(payload) }),
  patchAgent: (agentId: string, payload: AgentProfilePatchPayload) =>
    request<AgentProfile>(`/api/agents/${agentId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  getObservabilityStatus: () => request<ObservabilityStatus>("/api/observability/status"),
  getTracePolicySettings: () => request<TracePolicySettings>("/api/settings/trace-policy"),
  patchTracePolicySettings: (payload: Partial<TracePolicySettings>) =>
    request<TracePolicySettings>("/api/settings/trace-policy", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  flushTraceExportRetryQueue: (limit = 100, force = false) =>
    request<TraceExportRetryData>(`/api/observability/export-retry/flush?limit=${limit}&force=${force}`, {
      method: "POST",
    }),
  getRuntimeSafetySettings: () => request<RuntimeSafetySettings>("/api/settings/runtime-safety"),
  patchRuntimeSafetySettings: (payload: Partial<RuntimeSafetySettings>) =>
    request<RuntimeSafetySettings>("/api/settings/runtime-safety", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  exportRuntimeSnapshot: () => request<RuntimeSnapshot>("/api/runtime/snapshot"),
  importRuntimeSnapshot: (payload: RuntimeSnapshotImportPayload) =>
    request<RuntimeSnapshotImportResult>("/api/runtime/snapshot/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getToolPolicySettings: () => request<ToolPolicySettings>("/api/settings/tool-policy"),
  patchToolPolicySettings: (payload: Partial<ToolPolicySettings>) =>
    request<ToolPolicySettings>("/api/settings/tool-policy", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  getCommandPolicySettings: () => request<CommandPolicySettings>("/api/settings/command-policy"),
  patchCommandPolicySettings: (payload: Partial<CommandPolicySettings>) =>
    request<CommandPolicySettings>("/api/settings/command-policy", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  searchMemory: (query: string) =>
    request<{ entries: MemoryEntry[] }>("/api/memory/search", {
      method: "POST",
      body: JSON.stringify({ query, limit: 20 }),
    }),
  addMemory: (payload: MemoryCreatePayload) =>
    request<MemoryEntry>("/api/memory", { method: "POST", body: JSON.stringify(payload) }),
  getExternalRagSettings: () => request<ExternalServiceSettings>("/api/settings/external-rag"),
  patchExternalRagSettings: (payload: { base_url?: string | null; timeout_seconds?: number }) =>
    request<ExternalServiceSettings>("/api/settings/external-rag", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  getExternalNl2SqlSettings: () => request<ExternalServiceSettings>("/api/settings/external-nl2sql"),
  patchExternalNl2SqlSettings: (payload: {
    base_url?: string | null;
    timeout_seconds?: number;
    default_limit?: number;
  }) =>
    request<ExternalServiceSettings>("/api/settings/external-nl2sql", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  getExternalMcpSettings: () => request<ExternalServiceSettings>("/api/settings/external-mcp"),
  patchExternalMcpSettings: (payload: {
    base_url?: string | null;
    timeout_seconds?: number;
    session_id?: string | null;
  }) =>
    request<ExternalServiceSettings>("/api/settings/external-mcp", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listExternalMcpTools: (filters: ExternalMcpToolsFilters) =>
    request<ExternalMcpToolsData>(`/api/tools/external-mcp${externalMcpToolsQuery(filters)}`),
  listExternalMcpServers: () =>
    request<ExternalMcpServersData>("/api/settings/external-mcp-servers"),
  createExternalMcpServer: (payload: ExternalMcpServerWritePayload) =>
    request<ExternalMcpServerSettings>("/api/settings/external-mcp-servers", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateExternalMcpServer: (serverId: string, payload: ExternalMcpServerWritePayload) =>
    request<ExternalMcpServerSettings>(
      `/api/settings/external-mcp-servers/${encodeURIComponent(serverId)}`,
      { method: "PATCH", body: JSON.stringify(payload) }
    ),
  deleteExternalMcpServer: (serverId: string) =>
    request<ExternalMcpServersData>(
      `/api/settings/external-mcp-servers/${encodeURIComponent(serverId)}`,
      { method: "DELETE" }
    ),
  setDefaultExternalMcpServer: (serverId: string) =>
    request<ExternalMcpServersData>(
      `/api/settings/external-mcp-servers/${encodeURIComponent(serverId)}/default`,
      { method: "POST" }
    ),
  listSkills: () => request<AgentSkillListData>("/api/skills"),
  getSkill: (skillId: string) =>
    request<AgentSkill>(`/api/skills/${encodeURIComponent(skillId)}`),
  createSkill: (payload: AgentSkillWritePayload) =>
    request<AgentSkill>("/api/skills", { method: "POST", body: JSON.stringify(payload) }),
  updateSkill: (skillId: string, payload: AgentSkillWritePayload) =>
    request<AgentSkill>(`/api/skills/${encodeURIComponent(skillId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteSkill: (skillId: string) =>
    request<AgentSkillListData>(`/api/skills/${encodeURIComponent(skillId)}`, {
      method: "DELETE",
    }),
  reloadSkills: () => request<AgentSkillListData>("/api/skills/reload", { method: "POST" }),
};

export const api = {
  getModelSettings: () => request<ModelSettingsData>("/api/settings/model"),
  updateModelSettings: (body: ModelSettingsPayload) =>
    request<ModelSettingsData>("/api/settings/model", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  checkModelSettings: (body: ModelSettingsPayload) =>
    request<ModelSettingsData>("/api/settings/model/check", jsonBody(body)),
  testModelSettings: (body: ModelSettingsTestRequest) =>
    request<ModelSettingsTestResult>("/api/settings/model/test", jsonBody(body)),

  getDatabaseSettings: () => request<DatabaseSettingsData>("/api/settings/database"),
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
  testDatabaseSettings: (body: DatabaseSettingsUpdate) =>
    request<DatabaseConnectionTestResult>("/api/settings/database/test", jsonBody(body)),

  getAdbInfo: () => request<AdbInfoData>("/api/settings/database/adb"),
  updateAdbSettings: (body: AdbSettingsUpdate) =>
    request<AdbInfoData>("/api/settings/database/adb/settings", jsonBody(body)),
  startAdb: () => request<AdbInfoData>("/api/settings/database/adb/start", { method: "POST" }),
  stopAdb: () => request<AdbInfoData>("/api/settings/database/adb/stop", { method: "POST" }),

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
    request<OciConfigTestResult>("/api/settings/oci/config/test", { method: "POST" }),
  readOciObjectStorageNamespace: (body: OciObjectStorageNamespaceRequest) =>
    request<OciObjectStorageNamespaceData>(
      "/api/settings/oci/object-storage/namespace",
      jsonBody(body)
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
