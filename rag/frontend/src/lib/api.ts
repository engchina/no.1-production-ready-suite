/**
 * バックエンド API クライアント。
 *
 * - レスポンスは共通エンベロープ `ApiResponse<T>`（snake_case）。
 * - `/api/*` は Vite dev/preview proxy または Docker nginx proxy でバックエンドへ転送される。
 * - 型はバックエンドの Pydantic スキーマ（snake_case）にそのまま対応させる。
 */

import { t } from "./i18n";

export const API_REQUEST_TIMEOUT_MS = resolveTimeoutMs(
  import.meta.env.VITE_API_TIMEOUT_MS,
  30_000
);
// バックエンドは DB 停止時 dashboard_query_timeout_seconds(既定 8 秒)で縮退応答する。
// フロント側は縮退応答が届くよう十分な余裕を取り、全画面エラーに落ちないようにする。
export const DASHBOARD_REQUEST_TIMEOUT_MS = resolveTimeoutMs(
  import.meta.env.VITE_DASHBOARD_API_TIMEOUT_MS,
  15_000
);

/** DB 停止時に warning_messages を併せて返す閲覧系レスポンス。 */
export type Degradable<T> = T & { warning_messages: string[] };

export type FileStatus = "UPLOADED" | "INGESTING" | "INDEXED" | "ERROR";
export type SearchMode = "hybrid" | "vector" | "keyword";
export type SearchStrategy = "auto" | "hybrid" | "graph_local" | "graph_global" | "select_ai";
export type KnowledgeBaseStatus = "ACTIVE" | "ARCHIVED";
export type SelectAiAction = "showsql" | "runsql";
export type CitationFeedbackRating = "helpful" | "not_helpful";
export type CitationFeedbackReason =
  | "missing_evidence"
  | "not_relevant"
  | "answer_untrusted";
export type UploadIngestionMode = "manual" | "auto";
export type SourceModality = "pdf" | "image" | "text" | "office" | "unknown";
export type IngestionJobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "SKIPPED"
  | "CANCELLED";
export type EvaluationFailureReason =
  | "retrieval_miss"
  | "partial_recall"
  | "unexpected_retrieval"
  | "answer_keyword_miss"
  | "low_groundedness"
  | "guardrail_warning"
  | "case_error";
export type EvaluationMetricName =
  | "precision_at_k"
  | "recall_at_k"
  | "mrr"
  | "answer_keyword_hit_rate"
  | "groundedness_pass_rate"
  | "faithfulness"
  | "context_precision"
  | "context_recall"
  | "response_relevancy"
  | "noise_sensitivity";
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

export interface ApiResponse<T> {
  data: T | null;
  error_messages: string[];
  warning_messages: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_next: boolean;
}

// --- 認証 ---
export interface AuthUser {
  id: string;
  name: string;
  role: string;
}

export interface AuthStatus {
  mode: "local" | "production" | string;
  auth_required: boolean;
  authenticated: boolean;
  user: AuthUser | null;
  expires_at: number | null;
}

export interface LoginRequestBody {
  username: string;
  password: string;
  remember_me: boolean;
}

// --- ダッシュボード ---
export interface DashboardStats {
  total_uploads: number;
  uploads_this_month: number;
  total_indexed: number;
  indexed_this_month: number;
  searchable_rows: number;
}

export interface DashboardIngestionQuality {
  document_count: number;
  structured_document_count: number;
  element_count: number;
  table_count: number;
  list_count: number;
  page_count: number;
  chunk_profile_counts: Record<string, number>;
  content_kind_counts: Record<string, number>;
}

export interface DashboardActivity {
  id: string;
  type: "UPLOAD" | "INDEXING";
  file_name: string;
  timestamp: string;
  status: FileStatus;
  category_name: string | null;
}

export interface DashboardSystemInfo {
  status: "online" | "degraded" | "offline";
  version: string;
  searchable_rows: number;
  checks: Record<string, string>;
}

export interface DashboardSummary {
  stats: DashboardStats;
  ingestion_quality: DashboardIngestionQuality;
  recent_activities: DashboardActivity[];
  system: DashboardSystemInfo;
}

// --- ヘルスチェック ---
export interface HealthData {
  status: "ok" | "degraded" | "error" | string;
  version: string;
  message: string | null;
  checks: Record<string, string>;
}

export type DatabaseAvailability = "ok" | "not_configured" | "unreachable";

export interface DatabaseStatusData {
  status: DatabaseAvailability;
  check: string;
  detail: string | null;
}

// --- ドキュメント ---
export interface KnowledgeBaseRef {
  id: string;
  name: string;
}

export interface SourceProfile {
  original_file_name: string;
  sanitized_file_name: string;
  extension: string | null;
  content_type: string;
  inferred_content_type: string | null;
  file_size_bytes: number;
  content_sha256: string;
  modality: SourceModality;
  parser_profile: string;
  text_charset: string | null;
  duplicate_of_document_id: string | null;
  quality_status: "ready" | "warning" | string;
  quality_warnings: string[];
}

export interface IngestionJob {
  id: string;
  document_id: string;
  status: IngestionJobStatus;
  parser_profile: string;
  quality_warnings: string[];
  skip_reason: string | null;
  error_message: string | null;
  attempt_count: number;
  max_attempts: number;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface DocumentSummary {
  id: string;
  file_name: string;
  status: FileStatus;
  category_name: string | null;
  content_type: string | null;
  file_size_bytes: number | null;
  content_sha256: string | null;
  duplicate_of_document_id: string | null;
  uploaded_at: string;
  indexed_at: string | null;
  knowledge_bases: KnowledgeBaseRef[];
  source_profile: SourceProfile | null;
}

export interface DocumentElement {
  kind: string;
  text: string;
  order: number;
  page_number?: number | null;
  bbox?: number[] | null;
  section_path?: string[];
  confidence?: number | null;
  metadata?: Record<string, string | number | boolean | null>;
}

export interface StructuredExtraction {
  raw_text: string;
  document_type: string;
  confidence: number;
  warnings: string[];
  elements: DocumentElement[];
}

export interface DocumentDetail extends DocumentSummary {
  object_storage_path: string | null;
  extraction: Record<string, unknown>;
  error_message: string | null;
}

export interface DocumentDeleteResult {
  id: string;
  file_name: string;
  object_storage_path: string | null;
  object_deleted: boolean;
}

export interface UploadResult {
  id: string;
  file_name: string;
  status: FileStatus;
  file_size_bytes: number;
  content_sha256: string;
  duplicate_of_document_id: string | null;
  knowledge_bases: KnowledgeBaseRef[];
  source_profile: SourceProfile;
  ingestion_started: boolean;
  ingestion_job: IngestionJob | null;
}

export interface DocumentStats {
  total: number;
  by_status: Partial<Record<FileStatus, number>>;
}

export interface BatchUploadResult {
  items: UploadResult[];
  total_count: number;
  uploaded_count: number;
  queued_count: number;
  skipped_count: number;
}

// --- ナレッジベース ---
export interface KnowledgeBaseSummary extends KnowledgeBaseRef {
  description: string | null;
  status: KnowledgeBaseStatus;
  default_search_mode: SearchMode;
  document_count: number;
  indexed_document_count: number;
  error_document_count: number;
  searchable_chunk_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface KnowledgeBaseDetail extends KnowledgeBaseSummary {
  retrieval_config: Record<string, unknown>;
}

export interface KnowledgeBaseCreateRequest {
  name: string;
  description?: string | null;
  default_search_mode?: SearchMode;
  retrieval_config?: Record<string, unknown>;
}

export interface KnowledgeBaseUpdateRequest {
  name?: string | null;
  description?: string | null;
  default_search_mode?: SearchMode | null;
  retrieval_config?: Record<string, unknown> | null;
}

export interface KnowledgeBaseDocumentAssignmentRequest {
  document_ids: string[];
}

export interface DocumentKnowledgeBaseReplaceRequest {
  knowledge_base_ids: string[];
}

// --- 検索 ---
export interface SearchRequestBody {
  query: string;
  top_k?: number;
  rerank_top_n?: number;
  mode?: SearchMode;
  strategy?: SearchStrategy;
  filters?: Record<string, string>;
  knowledge_base_ids?: string[];
}

export interface RetrievedChunk {
  document_id: string;
  chunk_id: string;
  text: string;
  score: number;
  rerank_score: number | null;
  file_name: string | null;
  category_name: string | null;
  metadata: Record<string, string | number | boolean | null>;
}

export interface SearchDiagnostics {
  adapter: string;
  mode: string;
  retrieval_strategy: string;
  route_reason: string;
  graph_hit_count: number;
  fallback_reason: string | null;
  stream_stage_timings: Record<string, number>;
  top_k: number;
  rerank_top_n: number;
  retrieved_count: number;
  reranked_count: number;
  deduplicated_count: number;
  context_diversified_count: number;
  context_group_expanded_count: number;
  context_expanded_count: number;
  context_compressed_count: number;
  context_compression_saved_chars: number;
  citation_count: number;
  context_chars: number;
  context_window_chars: number;
  rrf_k: number;
  query_variant_count: number;
  oracle_vector_target_accuracy: number;
  filter_keys: string[];
  knowledge_base_count: number;
  config_fingerprint: string;
}

export interface SearchResponse {
  answer: string;
  citations: RetrievedChunk[];
  trace_id: string;
  guardrail_warnings: string[];
  elapsed_ms: number;
  diagnostics: SearchDiagnostics;
}

export interface SelectAiRequestBody {
  query: string;
  action?: SelectAiAction;
  profile_name?: string | null;
  max_result_chars?: number | null;
}

export interface SelectAiResponse {
  action: SelectAiAction;
  result_text: string;
  generated_sql: string | null;
  profile_name: string | null;
  query_chars: number;
  guardrail_warnings: string[];
}

export interface CitationFeedbackRequestBody {
  trace_id: string;
  document_id: string;
  chunk_id: string;
  rating: CitationFeedbackRating;
  reason?: CitationFeedbackReason | null;
  comment?: string | null;
}

export interface CitationFeedbackResponse {
  feedback_id: string;
  trace_id: string;
  document_id: string;
  chunk_id: string;
  rating: CitationFeedbackRating;
}

// --- 評価 ---
export interface EvaluationCase {
  id: string;
  query: string;
  relevant_document_ids: string[];
  expected_answer_keywords: string[];
}

export interface EvaluationThresholds {
  precision_at_k?: number | null;
  recall_at_k?: number | null;
  mrr?: number | null;
  answer_keyword_hit_rate?: number | null;
  groundedness_pass_rate?: number | null;
  faithfulness?: number | null;
  context_precision?: number | null;
  context_recall?: number | null;
  response_relevancy?: number | null;
  noise_sensitivity?: number | null;
}

export interface EvaluationRunRequestBody {
  cases: EvaluationCase[];
  top_k?: number;
  rerank_top_n?: number;
  mode?: SearchMode;
  filters?: Record<string, string>;
  knowledge_base_ids?: string[];
  thresholds?: EvaluationThresholds | null;
  rag_overrides?: EvaluationRagOverrides | null;
}

export interface EvaluationCaseResult {
  case_id: string;
  trace_id: string;
  status: "success" | "error";
  retrieved_document_ids: string[];
  relevant_document_ids: string[];
  hit_document_ids: string[];
  precision_at_k: number;
  recall_at_k: number;
  reciprocal_rank: number;
  answer_keyword_hit: boolean;
  groundedness_passed: boolean;
  groundedness_score: number;
  grounding_overlap_count: number;
  grounding_answer_feature_count: number;
  faithfulness: number;
  context_precision: number;
  context_recall: number;
  response_relevancy: number;
  noise_sensitivity: number;
  guardrail_warnings: string[];
  failure_reasons: EvaluationFailureReason[];
  diagnostics: SearchDiagnostics;
  elapsed_ms: number;
  error_type: string | null;
  error_message: string | null;
}

export interface EvaluationThresholdFailure {
  metric: EvaluationMetricName;
  actual: number;
  threshold: number;
}

export interface EvaluationMetrics {
  case_count: number;
  error_count: number;
  evaluated_k: number;
  precision_at_k: number;
  recall_at_k: number;
  mrr: number;
  answer_keyword_hit_rate: number;
  groundedness_pass_rate: number;
  faithfulness: number;
  context_precision: number;
  context_recall: number;
  response_relevancy: number;
  noise_sensitivity: number;
  passed: boolean;
  threshold_failures: EvaluationThresholdFailure[];
  failure_reason_counts: Partial<Record<EvaluationFailureReason, number>>;
  case_results: EvaluationCaseResult[];
  ingestion_quality: EvaluationIngestionQualitySummary;
}

export interface EvaluationIngestionQualitySummary {
  document_count: number;
  table_document_count: number;
  figure_document_count: number;
  long_document_count: number;
  warning_counts: Record<string, number>;
  risk_counts: Record<string, number>;
  parser_profile_counts: Record<string, number>;
}

export interface EvaluationRagOverrides {
  rrf_k?: number | null;
  query_expansion_enabled?: boolean | null;
  query_expansion_max_variants?: number | null;
  context_window_chars?: number | null;
  context_neighbor_window?: number | null;
  context_diversity_lambda?: number | null;
  context_group_expansion_enabled?: boolean | null;
  context_group_max_chunks?: number | null;
  context_compression_enabled?: boolean | null;
  context_compression_max_sentences?: number | null;
  context_compression_max_chars_per_chunk?: number | null;
  oracle_vector_target_accuracy?: number | null;
}

export interface EvaluationExperiment {
  id: string;
  top_k: number;
  rerank_top_n: number;
  mode: SearchMode;
  filters: Record<string, string>;
  knowledge_base_ids?: string[];
  rag_overrides?: EvaluationRagOverrides | null;
}

export interface EvaluationCompareRequestBody {
  cases: EvaluationCase[];
  experiments: EvaluationExperiment[];
  ranking_metric?: EvaluationMetricName;
  thresholds?: EvaluationThresholds | null;
}

export interface EvaluationExperimentResult {
  rank: number;
  ranking_score: number;
  experiment: EvaluationExperiment;
  metrics: EvaluationMetrics;
}

export interface EvaluationCompareResponse {
  ranking_metric: EvaluationMetricName;
  best_experiment_id: string | null;
  results: EvaluationExperimentResult[];
}

// --- 設定: モデル ---
export interface EnterpriseAiConfiguredModel {
  model_id: string;
  display_name: string;
  vision_enabled: boolean;
}

export interface EnterpriseAiModelSettings {
  endpoint: string;
  project_ocid: string;
  api_key: string;
  has_api_key: boolean;
  clear_api_key: boolean;
  models: EnterpriseAiConfiguredModel[];
  default_model_id: string;
  api_path: string;
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

// --- 設定: データベース ---
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

// --- 設定: アップロード保存先 ---
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

// --- 設定: OCI config ---
export type OciConfigField =
  | "user"
  | "fingerprint"
  | "tenancy"
  | "region"
  | "key_file";

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

/** API 由来のエラー。`messages` は日本語のユーザー向け文言。 */
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

function resolveTimeoutMs(value: unknown, fallbackMs: number): number {
  const parsed = typeof value === "string" ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallbackMs;
}

function runtimeApiTimeoutOverrideMs(): number | null {
  if (typeof window === "undefined") return null;
  const value = Number(
    (window as unknown as { __RAG_API_TIMEOUT_MS__?: string | number })
      .__RAG_API_TIMEOUT_MS__
  );
  return Number.isFinite(value) && value > 0 ? value : null;
}

function timeoutMessage(timeoutMs: number): string {
  return t("common.api.timeout", { seconds: Math.ceil(timeoutMs / 1000) });
}

async function parseEnvelope<T>(res: Response): Promise<ApiResponse<T>> {
  try {
    return (await res.json()) as ApiResponse<T>;
  } catch {
    return { data: null, error_messages: [], warning_messages: [] };
  }
}

/** ApiResponse エンベロープを取得し、エラー時は ApiError を投げる。 */
async function requestEnvelope<T>(
  path: string,
  init?: RequestInit,
  options: { allowStatus?: number[]; timeoutMs?: number } = {}
): Promise<ApiResponse<T>> {
  const timeoutMs =
    runtimeApiTimeoutOverrideMs() ?? options.timeoutMs ?? API_REQUEST_TIMEOUT_MS;
  const controller = new AbortController();
  const externalSignal = init?.signal;
  let timedOut = false;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;

  const abortFromExternal = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) {
    abortFromExternal();
  } else {
    externalSignal?.addEventListener("abort", abortFromExternal, { once: true });
  }

  if (timeoutMs > 0) {
    timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }

  try {
    const res = await fetch(path, {
      ...init,
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
    });
    const envelope = await parseEnvelope<T>(res);
    if (!res.ok && !options.allowStatus?.includes(res.status)) {
      const messages = envelope.error_messages?.length
        ? envelope.error_messages
        : [`APIエラー (${res.status})`];
      throw new ApiError(res.status, messages);
    }
    return envelope;
  } catch (error) {
    if (timedOut) {
      throw new ApiError(408, [timeoutMessage(timeoutMs)]);
    }
    throw error;
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", abortFromExternal);
  }
}

/** ApiResponse を展開し data のみ返す。エラー時は ApiError を投げる。 */
async function request<T>(
  path: string,
  init?: RequestInit,
  options: { allowStatus?: number[]; timeoutMs?: number } = {}
): Promise<T> {
  const envelope = await requestEnvelope<T>(path, init, options);
  return envelope.data as T;
}

/**
 * DB 停止時に縮退応答(空 data + warning_messages)を返す閲覧系 API 用。
 * data オブジェクトへ `warning_messages` を併設して返すため、既存の
 * data アクセス(`page.items` 等)を壊さずに縮退状態を画面へ伝えられる。
 */
async function requestDegradable<T extends object>(
  path: string,
  init?: RequestInit,
  options: { allowStatus?: number[]; timeoutMs?: number } = {}
): Promise<Degradable<T>> {
  const envelope = await requestEnvelope<T>(path, init, options);
  return {
    ...(envelope.data as T),
    warning_messages: envelope.warning_messages ?? [],
  };
}

function jsonBody(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  // 認証
  getAuthStatus: () => request<AuthStatus>("/api/auth/me"),
  login: (body: LoginRequestBody) => request<AuthStatus>("/api/auth/login", jsonBody(body)),
  logout: () => request<AuthStatus>("/api/auth/logout", { method: "POST" }),

  // ヘルスチェック
  getReadiness: () => request<HealthData>("/api/ready", undefined, { allowStatus: [503] }),

  // データベース利用可否(設定の有無 + 実接続プローブ)。DB ゲートが参照する。
  getDatabaseStatus: () =>
    request<DatabaseStatusData>("/api/ready/database", undefined, {
      // 実接続プローブはバックエンドで bounded(db_read_timeout_seconds)。
      // フロントはそれより十分長く待つ。
      timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS,
    }),

  // ダッシュボード
  getDashboardSummary: () =>
    request<DashboardSummary>("/api/dashboard/summary", undefined, {
      timeoutMs: DASHBOARD_REQUEST_TIMEOUT_MS,
    }),

  // ドキュメント
  listDocuments: (params: {
    status?: FileStatus;
    q?: string;
    knowledge_base_id?: string;
    limit?: number;
    offset?: number;
  } = {}) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.q) search.set("q", params.q);
    if (params.knowledge_base_id) search.set("knowledge_base_id", params.knowledge_base_id);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<DocumentSummary>>(`/api/documents${qs ? `?${qs}` : ""}`);
  },
  getDocument: (id: string) => request<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}`),
  deleteDocument: (id: string) =>
    request<DocumentDeleteResult>(`/api/documents/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  getDocumentStats: () => requestDegradable<DocumentStats>("/api/documents/stats"),
  listDocumentKnowledgeBases: (id: string) =>
    request<KnowledgeBaseRef[]>(`/api/documents/${encodeURIComponent(id)}/knowledge-bases`),
  replaceDocumentKnowledgeBases: (id: string, body: DocumentKnowledgeBaseReplaceRequest) =>
    request<KnowledgeBaseRef[]>(`/api/documents/${encodeURIComponent(id)}/knowledge-bases`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  uploadDocument: (
    file: File,
    knowledgeBaseIds: string[] = [],
    ingestionMode: UploadIngestionMode = "manual"
  ) => {
    const form = new FormData();
    form.append("file", file);
    for (const id of knowledgeBaseIds) {
      form.append("knowledge_base_ids", id);
    }
    form.append("ingestion_mode", ingestionMode);
    return request<UploadResult>("/api/documents/upload", { method: "POST", body: form });
  },
  batchUploadDocuments: (
    files: File[],
    knowledgeBaseIds: string[] = [],
    ingestionMode: UploadIngestionMode = "manual"
  ) => {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    for (const id of knowledgeBaseIds) {
      form.append("knowledge_base_ids", id);
    }
    form.append("ingestion_mode", ingestionMode);
    return request<BatchUploadResult>("/api/documents/batch-upload", {
      method: "POST",
      body: form,
    });
  },
  ingestDocument: (id: string, force = false) =>
    request<DocumentDetail>(
      `/api/documents/${encodeURIComponent(id)}/ingest${force ? "?force=true" : ""}`,
      { method: "POST" }
    ),
  enqueueDocumentIngestionJob: (id: string, force = false) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-jobs${force ? "?force=true" : ""}`,
      { method: "POST" }
    ),
  listIngestionJobs: (params: {
    status?: IngestionJobStatus;
    limit?: number;
    offset?: number;
  } = {}) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<IngestionJob>>(
      `/api/documents/ingestion-jobs${qs ? `?${qs}` : ""}`
    );
  },
  getIngestionJob: (id: string) =>
    request<IngestionJob>(`/api/documents/ingestion-jobs/${encodeURIComponent(id)}`),
  drainIngestionJobs: (limit = 50) =>
    request<IngestionJob[]>(`/api/documents/ingestion-jobs/drain?limit=${limit}`, {
      method: "POST",
    }),
  retryIngestionJob: (id: string, force = false) =>
    request<IngestionJob>(
      `/api/documents/ingestion-jobs/${encodeURIComponent(id)}/retry${force ? "?force=true" : ""}`,
      { method: "POST" }
    ),
  cancelIngestionJob: (id: string) =>
    request<IngestionJob>(`/api/documents/ingestion-jobs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),
  /** 原本ファイルの配信 URL（プレビュー用）。 */
  documentContentUrl: (id: string) => `/api/documents/${encodeURIComponent(id)}/content`,

  // ナレッジベース
  listKnowledgeBases: (params: {
    status?: KnowledgeBaseStatus;
    q?: string;
    limit?: number;
    offset?: number;
  } = {}) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.q) search.set("q", params.q);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<KnowledgeBaseSummary>>(
      `/api/knowledge-bases${qs ? `?${qs}` : ""}`
    );
  },
  createKnowledgeBase: (body: KnowledgeBaseCreateRequest) =>
    request<KnowledgeBaseDetail>("/api/knowledge-bases", jsonBody(body)),
  updateKnowledgeBase: (id: string, body: KnowledgeBaseUpdateRequest) =>
    request<KnowledgeBaseDetail>(`/api/knowledge-bases/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  archiveKnowledgeBase: (id: string) =>
    request<KnowledgeBaseDetail>(`/api/knowledge-bases/${encodeURIComponent(id)}/archive`, {
      method: "POST",
    }),
  assignDocumentsToKnowledgeBase: (
    id: string,
    body: KnowledgeBaseDocumentAssignmentRequest
  ) =>
    request<KnowledgeBaseDetail>(
      `/api/knowledge-bases/${encodeURIComponent(id)}/documents`,
      jsonBody(body)
    ),
  removeDocumentFromKnowledgeBase: (knowledgeBaseId: string, documentId: string) =>
    request<KnowledgeBaseDetail>(
      `/api/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(
        documentId
      )}`,
      { method: "DELETE" }
    ),

  // 検索
  search: (body: SearchRequestBody) => request<SearchResponse>("/api/search", jsonBody(body)),
  selectAi: (body: SelectAiRequestBody) =>
    request<SelectAiResponse>("/api/search/select-ai", jsonBody(body)),
  submitCitationFeedback: (body: CitationFeedbackRequestBody) =>
    request<CitationFeedbackResponse>("/api/search/citation-feedback", jsonBody(body)),

  // 評価
  runEvaluation: (body: EvaluationRunRequestBody) =>
    request<EvaluationMetrics>("/api/evaluation/run", jsonBody(body)),
  compareEvaluation: (body: EvaluationCompareRequestBody) =>
    request<EvaluationCompareResponse>("/api/evaluation/compare", jsonBody(body)),

  // 設定: モデル
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

  // 設定: データベース
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

  // 設定: Autonomous Database 管理
  getAdbInfo: () => request<AdbInfoData>("/api/settings/database/adb"),
  updateAdbSettings: (body: AdbSettingsUpdate) =>
    request<AdbInfoData>("/api/settings/database/adb/settings", jsonBody(body)),
  startAdb: () => request<AdbInfoData>("/api/settings/database/adb/start", { method: "POST" }),
  stopAdb: () => request<AdbInfoData>("/api/settings/database/adb/stop", { method: "POST" }),

  // 設定: アップロード保存先
  getUploadStorageSettings: () =>
    request<UploadStorageSettingsData>("/api/settings/upload-storage"),
  updateUploadStorageSettings: (body: UploadStorageSettingsUpdate) =>
    request<UploadStorageSettingsData>("/api/settings/upload-storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: OCI config
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
