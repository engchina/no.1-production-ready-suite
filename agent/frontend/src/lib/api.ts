import {
  DEFAULT_API_ERROR_DETAIL_LABELS,
  apiErrorDetail,
  httpApiErrorPresentation,
  isAbortError,
  toApiTransportError,
  type ApiErrorDetailLabels,
  type ApiErrorPresentable,
  type ApiErrorPresentation,
} from "@engchina/production-ready-ui";

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
  /** Run を作った利用者（共通認証の user_uuid）。 */
  created_by_user_uuid?: string | null;
  /** 会話（スレッド。#768）。チャットの 1 往復が 1 Run。 */
  thread_id?: string | null;
  /** 回答への評価（#774）。評価していない Run は null / 無し。 */
  feedback?: RunFeedback | null;
  /** 管理者の評価（本人の評価とは別。#774）。 */
  admin_review?: RunFeedback | null;
  /** モデルの利用量（#772）。モデルを呼ぶ前の Run・記録を始める前の Run は null / 無し。 */
  usage?: RunUsage | null;
  created_at: string;
  updated_at: string;
}

/** 業種テンプレート（#780）。業務 Agent の新規作成のフォームに入れる出発点。 */
export interface AgentTemplate {
  id: string;
  category: string;
  name: string;
  description: string;
  instructions: string;
  skill_ids: string[];
  sample_questions: string[];
  evaluation_cases: { question: string; expected: string }[];
}

/** 品質評価のケース（#776）。`id` を省くと `case-<番号>`。 */
export interface EvaluationCase {
  id?: string;
  question: string;
  expected: string;
  /** 呼ぶべきツール（任意。`rag_search` のように MCP 接続の名前を省いてもよい）。 */
  expected_tools?: string[];
  /** ケースの出どころ（フィードバック・Run の詳細から追加したときの Run の ID。#810）。 */
  source_run_id?: string | null;
}

/** Run から作る評価ケースの下書き（保存しない。#810）。 */
export interface EvaluationCaseDraft {
  agent_id: string;
  agent_name: string;
  source_run_id: string;
  question: string;
  /** 管理者の評価のコメント（無ければ空）。 */
  expected: string;
  /** Run が呼んだ（呼ぼうとした）ツール。 */
  expected_tools: string[];
  /** 同じ質問のケースを既に持つ評価セット。 */
  existing_set_ids: string[];
}

/** 評価する版（#810）。published = 公開中の版、draft = 下書き。 */
export type EvaluationTarget = "published" | "draft";
/** 評価した版（版の番号か "draft"。#810 より前の評価は null）。 */
export type EvaluatedAgentVersion = number | "draft" | null;

/** 評価セット（業務 Agent ごとに保存する評価ケースの集まり。#776）。 */
export interface EvaluationSetInput {
  agent_id: string;
  name: string;
  description: string;
  cases: EvaluationCase[];
}

export interface EvaluationSet extends EvaluationSetInput {
  id: string;
  cases: Required<EvaluationCase>[];
  created_by_user_uuid: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvaluationSetItem {
  id: string;
  agent_id: string;
  name: string;
  description: string;
  case_count: number;
  updated_at: string;
  last_job_id: string | null;
  last_job_status: EvaluationJobStatus | null;
  last_pass_rate: number | null;
}

export type JudgeVerdict = "correct" | "incorrect" | "uncertain";
export type EvaluationCaseStatus =
  | "pending"
  | "running"
  | "judged"
  | "run_failed"
  | "needs_approval"
  | "timed_out"
  | "judge_failed"
  | "cancelled";
export type EvaluationJobStatus = "queued" | "running" | "completed" | "cancelled" | "failed";

export interface EvaluationJudgement {
  verdict: JudgeVerdict;
  score: number;
  summary: string;
  missing_points: string[];
}

export interface EvaluationCaseResult {
  case: Required<EvaluationCase>;
  status: EvaluationCaseStatus;
  run_id: string | null;
  answer: string;
  judgement: EvaluationJudgement | null;
  /** 業務 Agent が呼んだ（呼ぼうとした）ツール。評価中は承認が要るツールを実行しない。 */
  tool_calls: string[];
  /** 期待するツールをすべて呼んだか（期待するツールが無いケースは null）。 */
  tool_selection_correct: boolean | null;
  error: string | null;
  duration_ms: number | null;
}

export interface EvaluationSummary {
  total: number;
  completed: number;
  correct: number;
  incorrect: number;
  uncertain: number;
  errors: number;
  /** 正しいと判定したケース / 終わったケース（評価できなかったケースは不合格に数える）。 */
  pass_rate: number | null;
  average_score: number | null;
  /** ツールの選択の正しさ（期待するツールを指定したケースのうち、すべて呼んだ割合）。 */
  tool_cases: number;
  tool_correct: number;
  tool_accuracy: number | null;
}

export interface EvaluationJob {
  id: string;
  agent_id: string;
  agent_name: string;
  set_id: string;
  set_name: string;
  /** 評価した版（#810）。 */
  agent_version: EvaluatedAgentVersion;
  /** 同じ評価セット・同じ評価ケースの前回（完了した評価）。比べた版と日時（#810）。 */
  previous_job_id: string | null;
  previous_summary: EvaluationSummary | null;
  previous_agent_version: EvaluatedAgentVersion;
  previous_created_at: string | null;
  status: EvaluationJobStatus;
  created_by_user_uuid: string | null;
  results: EvaluationCaseResult[];
  error: string | null;
  summary: EvaluationSummary;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type EvaluationJobItem = Pick<
  EvaluationJob,
  | "id"
  | "agent_id"
  | "agent_name"
  | "set_id"
  | "set_name"
  | "agent_version"
  | "status"
  | "summary"
  | "created_at"
  | "finished_at"
>;

/** 評価の履歴の 1 ページ（共通の `Page`。`total` は対象の評価の件数。#1266）。 */
export type EvaluationJobsPage = Page<EvaluationJobItem>;

/** 外部のクライアント向けの API キー（#778。秘密は作成時の応答にだけ入る）。 */
export interface ApiKey {
  id: string;
  name: string;
  /** 実行する利用者（キーはこの利用者として動く）。 */
  owner_user_uuid: string;
  owner_display_name: string;
  created_by_user_uuid: string;
  created_by_display_name: string;
  /** null は「実行する利用者が使える業務 Agent すべて」。 */
  agent_ids: string[] | null;
  token_prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  expired: boolean;
}

export interface ApiKeysData {
  keys: ApiKey[];
  /** キーの業務 Agent の ID → 名前（閲覧者が利用できる業務 Agent だけ。範囲外・削除済みは無い）。 */
  agent_names: Record<string, string>;
  /** false はキーの保存先（Oracle）が無い（再起動で消える）。 */
  persistent: boolean;
}

export type ApiKeyExpiryDays = 30 | 90 | 365;

export interface CreateApiKeyPayload {
  name: string;
  agent_ids: string[] | null;
  expires_in_days: ApiKeyExpiryDays | null;
  /** 実行する利用者。null は作った利用者（ほかの利用者はシステム管理者だけ）。 */
  run_as_user_uuid: string | null;
}

export interface ApiKeyCreated {
  key: ApiKey;
  /** `prak_…`。この応答でだけ返る。 */
  token: string;
}

/** 業務 Agent の自動実行（スケジュール・Webhook。#784）。 */
export type ScheduleFrequency = "daily" | "weekdays" | "weekly" | "hourly";
export type AutomationTrigger = "schedule" | "webhook";

export interface AutomationSchedule {
  frequency: ScheduleFrequency;
  /** HH:MM（毎日・平日・毎週）。 */
  time: string;
  /** 0 = 月曜 … 6 = 日曜（毎週）。 */
  weekdays: number[];
  /** 毎時の分。 */
  minute: number;
  timezone: string;
}

export interface AutomationInput {
  agent_id: string;
  name: string;
  goal: string;
  enabled: boolean;
  trigger: AutomationTrigger;
  schedule: AutomationSchedule | null;
}

export interface Automation extends AutomationInput {
  id: string;
  run_as_user_uuid: string;
  created_by_user_uuid: string;
  webhook_token_prefix: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_id: string | null;
  last_trigger: string | null;
  /** Run の作成（created）・前回の Run が終わっていないため飛ばした（skipped）・作れなかった（failed_to_start）。 */
  last_result: "created" | "skipped" | "failed_to_start" | null;
  last_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutomationRun {
  run_id: string;
  status: RunState["status"];
  trigger: string;
  created_at: string;
  updated_at: string;
}

export interface AutomationFired {
  run_id: string | null;
  result: "created" | "skipped" | "failed_to_start";
  message: string;
}

/** Run が使ったモデルの量（承認待ちからの再開を含めた累計。#772）。 */
export interface RunUsage {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

/** 利用状況の集計の 1 行（#772）。 */
export interface UsageTotals {
  runs: number;
  /** 利用量を記録した Run（記録を始める前の Run・モデルを呼ぶ前に止まった Run は含まない）。 */
  runs_with_usage: number;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

/** 集計の期間（日。90 日を超える期間は保存した Run の履歴で集計する。#794）。 */
export type ReportPeriodDays = 7 | 30 | 90 | 180 | 365;
export const REPORT_PERIOD_DAYS: readonly ReportPeriodDays[] = [7, 30, 90, 180, 365];
/** 集計元。history = 保存した Run の履歴（Oracle）、memory = バックエンドのメモリの Run（#794）。 */
export type ReportSource = "history" | "memory";

export type UsagePeriodDays = ReportPeriodDays;

export interface UsageReport {
  days: UsagePeriodDays;
  source: ReportSource;
  timezone: string;
  since: string;
  until: string;
  totals: UsageTotals;
  /** 直前の同じ長さの期間。 */
  previous: UsageTotals;
  by_agent: (UsageTotals & { agent_id: string; agent_name: string })[];
  by_user: (UsageTotals & { user_uuid: string | null; display_name: string })[];
  by_model: (UsageTotals & { model: string })[];
  /** 期間のすべての日（古い順。Run の無い日も 0 で入る）。 */
  by_day: (UsageTotals & { day: string })[];
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

/** ツール監査の 1 ページ（共通の `Page` に絞り込みの条件とツール名を足した形。#1266）。 */
export interface ToolCallAuditData extends Page<ToolCallAuditRecord> {
  filters: Record<string, unknown>;
  /** 見られる範囲の監査に記録されたツール名（絞り込みに依らない。MCP 接続のツールを含む。#983）。 */
  tool_names?: string[];
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
  /** 公開した版（#770）。利用者の Run は公開中の版（published_version）で実行する。 */
  versions: AgentVersion[];
  published_version: number | null;
  /** 下書きに公開していない変更があるか（公開した版が無いときも true）。 */
  unpublished_changes: boolean;
  /** 作成に使った業種テンプレート（#810。空は使っていない）。 */
  template_id?: string;
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
  /** 作成に使った業種テンプレート（#810）。 */
  template_id?: string;
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
  /** URL の userinfo・資格情報らしい query の値は `***` に伏せて返る（#1056）。 */
  base_url?: string | null;
  /** 保存済みの URL に資格情報があり、base_url を伏せて返したか。 */
  base_url_masked?: boolean;
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
  import_metadata?: Record<string, unknown>;
  import_warnings?: string[];
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
  refresh_status?: "not_fetched" | "ready" | "failed";
  revision?: string | null;
}

export interface MarketplaceEntry {
  catalog_entry: true;
  id: string;
  name: string;
  version?: string;
  description?: string;
  unavailable_reason?: string | null;
}

export interface PluginImportPreview {
  manifest: PluginManifest;
  digest: string;
  warnings: string[];
}

export interface MarketplaceSourcesData {
  marketplaces: MarketplaceSource[];
}

export interface MarketplaceListing {
  name: string;
  plugins: (PluginManifest | MarketplaceEntry)[];
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

/** 公開した業務 Agent の版（#770）。 */
export interface AgentVersion {
  version: number;
  name: string;
  description: string;
  instructions: string;
  skill_ids: string[];
  model_id: string;
  note: string;
  published_at: string;
  published_by?: string | null;
}

export interface CreateRunPayload {
  goal: string;
  agent_id?: string;
  metadata?: Record<string, unknown>;
  /** 公開前の下書きで実行する（Agent 管理の権限が要る。#770）。 */
  draft?: boolean;
  /** 続ける会話（#768）。省略すると新しい会話を始める。 */
  thread_id?: string;
}

/** チャットの会話の一覧の 1 件（#768）。 */
/** 回答の評価（RAG と同じ値。#774）。 */
export type FeedbackRating = "helpful" | "not_helpful";

/** 役に立たなかった理由（表示の順。#774）。 */
export const FEEDBACK_REASONS = [
  "incorrect",
  "incomplete",
  "not_relevant",
  "answer_untrusted",
  "ambiguous_question",
  "wrong_action",
] as const;
export type FeedbackReason = (typeof FEEDBACK_REASONS)[number];

export interface RunFeedback {
  rating: FeedbackRating;
  reason: FeedbackReason | null;
  comment: string;
  user_uuid: string | null;
  updated_at: string;
}

export interface RunFeedbackPayload {
  rating: FeedbackRating;
  reason?: FeedbackReason;
  comment?: string;
}

export type FeedbackPeriodDays = ReportPeriodDays;

export interface FeedbackSummary {
  total: number;
  helpful: number;
  not_helpful: number;
  /** 評価が 0 件のときは null。 */
  helpful_rate: number | null;
  reason_counts: { reason: FeedbackReason; count: number }[];
  /** 管理者の評価の件数と、そのうち役に立たなかった件数。 */
  admin_reviewed: number;
  admin_not_helpful: number;
}

export interface FeedbackItem {
  run_id: string;
  thread_id: string | null;
  agent_id: string;
  agent_name: string;
  /** 会話の本人（Run の作成者）。 */
  user_uuid: string | null;
  display_name: string;
  question: string;
  answer: string;
  /** 本人の評価・管理者の評価（どちらかは必ずある）。 */
  feedback: RunFeedback | null;
  admin_review: RunFeedback | null;
  reviewer_display_name: string;
  /** 新しい方の評価の日時。 */
  updated_at: string;
}

/**
 * フィードバックの集計と一覧（共通の `Page` に集計を足した形。#1266）。
 * `items` は絞り込みに合う評価のうち `offset` から `limit` 件（新しい順）、`total` は絞り込みに合う評価の件数
 * （ページングの総数。`summary.total` は期間内の評価の件数で別）。
 */
export interface FeedbackReport extends Page<FeedbackItem> {
  days: FeedbackPeriodDays;
  source: ReportSource;
  since: string;
  until: string;
  summary: FeedbackSummary;
  /** 直前の同じ長さの期間。 */
  previous: FeedbackSummary;
}

export interface FeedbackFilters {
  days: FeedbackPeriodDays;
  agentId?: string;
  rating?: FeedbackRating;
  reason?: FeedbackReason;
  offset?: number;
  limit?: number;
}

export interface ThreadSummary {
  thread_id: string;
  agent_id: string;
  /** 最初の質問の 1 行目。 */
  title: string;
  run_count: number;
  last_status: RunState["status"];
  created_at: string;
  updated_at: string;
}

/** 会話の一覧の 1 ページ（新しい順。共通の Page。#1265 / #1266）。 */
export type ThreadsData = Page<ThreadSummary>;

export interface ThreadData {
  thread_id: string;
  agent_id: string;
  /** 会話の Run（古い順）。 */
  runs: RunState[];
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
 * 業務 Agent・スキル・MCP 接続・実行などの保存先（#839）。`persistent` が false なら再起動で消える。
 * `reason`: `memory_backend` は DB は設定済みで保存先にメモリを明示、`restart_required` は既定（auto）で DB も
 * 設定済みだが起動時は未設定だった（再起動で DB になる）、`database_not_configured` は DB が未設定、
 * `checkpoint_invalid` は既定（auto）で保存済みの checkpoint 全体を読めず、上書きしないようメモリにした（#853）。
 */
export interface RuntimeStorageStatus {
  backend: "memory" | "file" | "oracle_checkpoint" | "oracle_normalized";
  /** 設定の値（`AGENT_RUNTIME_REPOSITORY_BACKEND`。既定は `auto`。#839）。 */
  configured_backend: string;
  persistent: boolean;
  database_configured: boolean;
  reason: "memory_backend" | "restart_required" | "database_not_configured" | "checkpoint_invalid" | null;
  /** 起動時の読み込みで整合しない状態を直した実行の数（#853）。 */
  repaired_runs?: number;
  /** 直せずに読み込まず、保存先に元の JSON のまま残している実行・業務 Agent の数（#853）。 */
  skipped_runs?: number;
  skipped_agents?: number;
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


/** サーバー側でページングする一覧の共通の形（backend の `pr_backend_core.Page[T]`。#1266）。 */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_next: boolean;
}

/** 権限管理の対象の候補の 1 ページ（検索とページング。#608）。 */
export type AccessTargetPage<T> = Page<T>;

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
  /** 入力の検証エラー（422）の Pydantic の種別と技術的な原文（「詳細」に出す。#1065）。 */
  code?: string;
  raw_location?: string;
  raw_message?: string;
}

export interface ApiErrorDetails {
  /** 機械判定用のエラーコード（共通認証・ユーザー / ロール操作の `error_code`）。 */
  errorCode?: string;
  fieldErrors?: ApiFieldError[];
  requestId?: string;
  /** backend の内部の文（英語・技術的な原文）。本文には出さず「詳細」の「元のメッセージ」に出す。 */
  rawMessage?: string;
}

export class ApiError extends Error implements ApiErrorPresentable {
  readonly status: number;
  readonly messages: string[];
  readonly errorCode?: string;
  readonly fieldErrors: ApiFieldError[];
  readonly requestId?: string;
  readonly rawMessage?: string;

  constructor(status: number, messages: string[], details: ApiErrorDetails = {}) {
    super(messages[0] ?? `APIエラー (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.messages = messages.length > 0 ? messages : [`APIエラー (${status})`];
    this.errorCode = details.errorCode;
    this.fieldErrors = details.fieldErrors ?? [];
    this.requestId = details.requestId;
    this.rawMessage = details.rawMessage;
  }

  /** 失敗の面の要約と「詳細」（共通の `ApiErrorBanner` / `presentApiError`。#906）。 */
  toApiErrorPresentation(labels: ApiErrorDetailLabels = DEFAULT_API_ERROR_DETAIL_LABELS): ApiErrorPresentation {
    const presentation = httpApiErrorPresentation(this, labels);
    return {
      ...presentation,
      details: [...presentation.details, ...apiErrorDetail(labels.rawMessage, this.rawMessage)],
    };
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
  /** Control Plane の error code の付いたエラーの補足（`reason` は内部の原文）。 */
  error_details?: { reason?: unknown } | null;
  problem?: { field_errors?: unknown; request_id?: unknown } | null;
}

function fieldErrorsOf(value: unknown): ApiFieldError[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    const { pointer, message } = record;
    if (typeof message !== "string") return [];
    const fieldError: ApiFieldError = { pointer: typeof pointer === "string" ? pointer : "", message };
    for (const key of ["code", "raw_location", "raw_message"] as const) {
      const value = record[key];
      if (typeof value === "string" && value) fieldError[key] = value;
    }
    return [fieldError];
  });
}

/** エラー応答（ApiResponse envelope / FastAPI の detail）から ApiError を作る。 */
async function apiErrorFrom(response: Response): Promise<ApiError> {
  // 本文が無い・JSON でない応答（前段の proxy の 502 / 504 など）は、英語の statusText（`Bad Gateway` 等）を
  // 出さず、ApiError の既定の文にする（#906）。
  let detail = "";
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
    rawMessage: typeof body.error_details?.reason === "string" ? body.error_details.reason : undefined,
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
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: withCsrfHeaders(init.method, headers),
    });
  } catch (cause) {
    throw transportFailure(cause, path, init.method);
  }
  // 403 は error_code が経路の権限拒否のときだけ権限なしの画面へ移す。権限の付与の制限などは
  // 呼び出した画面がその場で理由を表示する（#224）。本文は消費しない。
  if (!response.ok) void notifyAuthResponse(response);
  return response;
}

/**
 * fetch・本文の読み取りが投げた例外のうち、timeout・通信断（`TypeError: Failed to fetch` など）を利用者向けの
 * `ApiTransportError`（日本語の文 + 次の操作。英語の元の文は「詳細」に出す）にする。中止などはそのまま返す（#906）。
 */
function transportFailure(cause: unknown, path: string, method: string | undefined): unknown {
  if (isAbortError(cause)) return cause;
  return toApiTransportError(cause, { method: (method ?? "GET").toUpperCase(), path }) ?? cause;
}

/** ApiResponse を展開し data のみ返す。エラー時は ApiError を投げる。 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchWithSession(path, init);
  if (!response.ok) {
    throw await apiErrorFrom(response);
  }
  let json: ApiResponse<T>;
  try {
    json = (await response.json()) as ApiResponse<T>;
  } catch (cause) {
    throw transportFailure(cause, path, init?.method);
  }
  return json.data;
}

/** ファイルの応答（CSV など）を Blob で受け取る。エラー時は ApiError を投げる。 */
async function requestBlob(path: string): Promise<Blob> {
  const response = await fetchWithSession(path);
  if (!response.ok) {
    throw await apiErrorFrom(response);
  }
  try {
    return await response.blob();
  } catch (cause) {
    throw transportFailure(cause, path, "GET");
  }
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
  getRuntimeStorage: () => request<RuntimeStorageStatus>("/api/runtime/storage"),
  listRuns: () => request<{ runs: RunState[] }>("/api/runs"),
  publishAgent: (agentId: string, note = "") =>
    request<AgentProfile>(`/api/agents/${encodeURIComponent(agentId)}/publish`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  restoreAgentVersion: (agentId: string, version: number) =>
    request<AgentProfile>(
      `/api/agents/${encodeURIComponent(agentId)}/versions/${version}/restore`,
      { method: "POST" },
    ),
  listThreads: (agentId: string | undefined, page: { limit: number; offset: number }) => {
    const params = new URLSearchParams({ limit: String(page.limit), offset: String(page.offset) });
    if (agentId) params.set("agent_id", agentId);
    return request<ThreadsData>(`/api/threads?${params.toString()}`);
  },
  /** チャットの回答への評価（#774）。会話をした利用者だけ。付け直すと上書きする。 */
  putRunFeedback: (runId: string, payload: RunFeedbackPayload) =>
    request<RunState>(`/api/runs/${encodeURIComponent(runId)}/feedback`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  /** 管理者の評価（#774。Agent 管理の権限）。 */
  putRunAdminReview: (runId: string, payload: RunFeedbackPayload) =>
    request<RunState>(`/api/runs/${encodeURIComponent(runId)}/admin-review`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  /** フィードバックの集計と一覧（#774）。 */
  getFeedbackReport: (filters: FeedbackFilters) => {
    const query = new URLSearchParams({ days: String(filters.days) });
    if (filters.agentId) query.set("agent_id", filters.agentId);
    if (filters.rating) query.set("rating", filters.rating);
    if (filters.reason) query.set("reason", filters.reason);
    if (filters.offset) query.set("offset", String(filters.offset));
    if (filters.limit) query.set("limit", String(filters.limit));
    return request<FeedbackReport>(`/api/feedback?${query.toString()}`);
  },
  /** 会話（Run の一覧）。チャットは取り直しの `signal`（中止・待ち時間の上限）を渡す（#1160）。 */
  getThread: (threadId: string, options: { signal?: AbortSignal } = {}) =>
    request<ThreadData>(`/api/threads/${encodeURIComponent(threadId)}`, { signal: options.signal }),
  createRun: (payload: CreateRunPayload) =>
    request<RunState>("/api/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getRun: (runId: string) => request<RunState>(`/api/runs/${runId}`),
  getRunAudit: (runId: string) =>
    request<RunAuditData>(`/api/runs/${runId}/audit`),
  listAgentTemplates: () => request<{ templates: AgentTemplate[] }>("/api/agent-templates"),
  /** 自動実行（#784）。 */
  listAutomations: () => request<{ automations: Automation[]; persistent: boolean }>("/api/automations"),
  getAutomation: (automationId: string) =>
    request<{ automation: Automation; runs: AutomationRun[] }>(`/api/automations/${encodeURIComponent(automationId)}`),
  createAutomation: (payload: AutomationInput) =>
    request<Automation>("/api/automations", { method: "POST", body: JSON.stringify(payload) }),
  updateAutomation: (automationId: string, payload: AutomationInput) =>
    request<Automation>(`/api/automations/${encodeURIComponent(automationId)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteAutomation: (automationId: string) =>
    request<null>(`/api/automations/${encodeURIComponent(automationId)}`, { method: "DELETE" }),
  runAutomation: (automationId: string) =>
    request<AutomationFired>(`/api/automations/${encodeURIComponent(automationId)}/run`, { method: "POST" }),
  issueAutomationWebhookToken: (automationId: string) =>
    request<{ automation: Automation; token: string }>(
      `/api/automations/${encodeURIComponent(automationId)}/webhook-token`,
      { method: "POST" }
    ),
  /** 品質評価（#776）。 */
  listEvaluationSets: (agentId?: string) =>
    request<{ sets: EvaluationSetItem[] }>(
      `/api/evaluation-sets${agentId ? `?${new URLSearchParams({ agent_id: agentId }).toString()}` : ""}`
    ),
  getEvaluationSet: (setId: string) => request<EvaluationSet>(`/api/evaluation-sets/${encodeURIComponent(setId)}`),
  createEvaluationSet: (payload: EvaluationSetInput) =>
    request<EvaluationSet>("/api/evaluation-sets", { method: "POST", body: JSON.stringify(payload) }),
  updateEvaluationSet: (setId: string, payload: EvaluationSetInput) =>
    request<EvaluationSet>(`/api/evaluation-sets/${encodeURIComponent(setId)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteEvaluationSet: (setId: string) =>
    request<null>(`/api/evaluation-sets/${encodeURIComponent(setId)}`, { method: "DELETE" }),
  /** Excel の評価ケースを読む（保存しない）。 */
  parseEvaluationCasesXlsx: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<{ cases: Required<EvaluationCase>[] }>("/api/evaluation-sets/parse-xlsx", { method: "POST", body });
  },
  downloadEvaluationTemplate: () => requestBlob("/api/evaluation-sets/template.xlsx"),
  downloadEvaluationSetXlsx: (setId: string) =>
    requestBlob(`/api/evaluation-sets/${encodeURIComponent(setId)}/cases.xlsx`),
  /** Run（フィードバック・Run の詳細）から評価ケースの下書きを作る（#810）。 */
  getRunEvaluationCase: (runId: string) =>
    request<EvaluationCaseDraft>(`/api/runs/${encodeURIComponent(runId)}/evaluation-case`),
  /** 評価セットにケースを 1 件足す（同じ質問・上限は 409。#810）。 */
  appendEvaluationCase: (setId: string, payload: EvaluationCase) =>
    request<EvaluationSet>(`/api/evaluation-sets/${encodeURIComponent(setId)}/cases`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** 業務 Agent の作成に使った業種テンプレートの評価ケースで評価セットを作る（#810）。 */
  createEvaluationSetFromTemplate: (agentId: string) =>
    request<EvaluationSet>("/api/evaluation-sets/from-template", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId }),
    }),
  createEvaluation: (payload: { set_id: string; agent_version?: EvaluationTarget }) =>
    request<EvaluationJob>("/api/evaluations", { method: "POST", body: JSON.stringify(payload) }),
  /** 評価の履歴（新しい順のページ。終わった評価は 365 日残る。#794）。 */
  listEvaluations: (page: { offset: number; limit: number }) =>
    request<EvaluationJobsPage>(
      `/api/evaluations?${new URLSearchParams({ offset: String(page.offset), limit: String(page.limit) }).toString()}`
    ),
  getEvaluation: (jobId: string) => request<EvaluationJob>(`/api/evaluations/${encodeURIComponent(jobId)}`),
  cancelEvaluation: (jobId: string) =>
    request<EvaluationJob>(`/api/evaluations/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),
  deleteEvaluation: (jobId: string) =>
    request<null>(`/api/evaluations/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
  /** 利用状況（#772）。日は画面のブラウザのタイムゾーンで区切る。 */
  getUsageReport: (days: UsagePeriodDays, timezone: string) =>
    request<UsageReport>(`/api/usage?${new URLSearchParams({ days: String(days), timezone }).toString()}`),
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
  /** API キー（#778）。 */
  listApiKeys: () => request<ApiKeysData>("/api/settings/api-keys"),
  createApiKey: (payload: CreateApiKeyPayload) =>
    request<ApiKeyCreated>("/api/settings/api-keys", { method: "POST", body: JSON.stringify(payload) }),
  deleteApiKey: (keyId: string) =>
    request<null>(`/api/settings/api-keys/${encodeURIComponent(keyId)}`, { method: "DELETE" }),
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
    preview_digest?: string;
    accept_limitations?: boolean;
  }) =>
    request<PluginRecord>("/api/plugins", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  previewMarketplacePlugin: (marketplaceId: string, pluginId: string) =>
    request<PluginImportPreview>(
      `/api/plugins/marketplaces/${encodeURIComponent(marketplaceId)}/plugins/${encodeURIComponent(pluginId)}/preview`,
      { method: "POST" }
    ),
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

/**
 * 共通のシステム設定の取得の options。共有の画面は TanStack Query の `signal` を渡し、
 * 画面を離れたら取得を止める（#1117）。
 */
export interface SettingsRequestOptions {
  signal?: AbortSignal;
}

export const api = {
  // DB の状態（画面の DB ゲートが使う。3製品共通の判定と契約。ログイン不要。#325）。
  getDatabaseStatus: (options?: { signal?: AbortSignal }) =>
    request<DatabaseStatusData>("/api/ready/database", { signal: options?.signal }),

  // システムテーブル（運用設定。3製品共通のカードと契約。#751）。
  getSystemTablesStatus: (options?: { signal?: AbortSignal }) =>
    request<SystemTablesStatusData>("/api/settings/database/system-tables", { signal: options?.signal }),
  initializeSystemTables: (body: SystemTablesInitializeRequest) =>
    request<SystemTablesOperationData>("/api/settings/database/system-tables/initialize", jsonBody(body)),

  getModelSettings: (options: SettingsRequestOptions = {}) =>
    request<ModelSettingsData>("/api/settings/model", { signal: options.signal }),
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

  getDatabaseSettings: (options: SettingsRequestOptions = {}) =>
    request<DatabaseSettingsData>("/api/settings/database", { signal: options.signal }),
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

  getAdbInfo: (options: SettingsRequestOptions = {}) =>
    request<AdbInfoData>("/api/settings/database/adb", { signal: options.signal }),
  updateAdbSettings: (body: AdbSettingsUpdate) =>
    request<AdbInfoData>("/api/settings/database/adb/settings", jsonBody(body)),
  startAdb: () =>
    request<AdbInfoData>("/api/settings/database/adb/start", {
      method: "POST",
    }),
  stopAdb: () =>
    request<AdbInfoData>("/api/settings/database/adb/stop", { method: "POST" }),

  getUploadStorageSettings: (options: SettingsRequestOptions = {}) =>
    request<UploadStorageSettingsData>("/api/settings/upload-storage", {
      signal: options.signal,
    }),
  updateUploadStorageSettings: (body: UploadStorageSettingsUpdate) =>
    request<UploadStorageSettingsData>("/api/settings/upload-storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  getOciSettings: (options: SettingsRequestOptions = {}) =>
    request<OciSettingsData>("/api/settings/oci", { signal: options.signal }),
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
