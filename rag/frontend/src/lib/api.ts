/**
 * バックエンド API クライアント。
 *
 * - レスポンスは共通エンベロープ `ApiResponse<T>`（snake_case）。
 * - `/api/*` は Vite dev/preview proxy または Docker nginx proxy でバックエンドへ転送される。
 * - 型はバックエンドの Pydantic スキーマ（snake_case）にそのまま対応させる。
 */

export type FileStatus = "UPLOADED" | "INGESTING" | "INDEXED" | "ERROR";
export type SearchMode = "hybrid" | "vector" | "keyword";
export type SelectAiAction = "showsql" | "runsql";
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
  | "groundedness_pass_rate";
export type ModelSettingsCheckStatus = "ok" | "missing" | "invalid";
export type AiServiceAdapter = "local" | "oci";
export type UploadStorageBackend = "local" | "oci";
export type DatabaseConnectionTestStatus = "success" | "failed" | "skipped";

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
  adapter: string;
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

// --- ドキュメント ---
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

export interface UploadResult {
  id: string;
  file_name: string;
  status: FileStatus;
  file_size_bytes: number;
  content_sha256: string;
  duplicate_of_document_id: string | null;
}

export interface DocumentStats {
  total: number;
  by_status: Partial<Record<FileStatus, number>>;
}

// --- 検索 ---
export interface SearchRequestBody {
  query: string;
  top_k?: number;
  rerank_top_n?: number;
  mode?: SearchMode;
  filters?: Record<string, string>;
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
}

export interface EvaluationRunRequestBody {
  cases: EvaluationCase[];
  top_k?: number;
  rerank_top_n?: number;
  mode?: SearchMode;
  filters?: Record<string, string>;
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
  passed: boolean;
  threshold_failures: EvaluationThresholdFailure[];
  failure_reason_counts: Partial<Record<EvaluationFailureReason, number>>;
  case_results: EvaluationCaseResult[];
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
export interface EnterpriseAiModelSettings {
  endpoint: string;
  project_ocid: string;
  api_key: string;
  has_api_key: boolean;
  clear_api_key: boolean;
  llm_model: string;
  vlm_model: string;
  llm_path: string;
  vlm_path: string;
  llm_payload_template: string;
  vlm_payload_template: string;
  llm_response_path: string;
  vlm_response_path: string;
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
  source: "runtime";
}

// --- 設定: データベース ---
export interface DatabaseSettingsData {
  adapter: AiServiceAdapter;
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
  config_source: "runtime";
}

export interface DatabaseSettingsUpdate {
  user: string;
  dsn: string;
  wallet_dir: string;
  password?: string;
  wallet_password?: string;
}

export interface DatabaseConnectionTestResult {
  status: DatabaseConnectionTestStatus;
  readiness: string;
  message: string;
  checked_at: string;
  error_type: string | null;
}

// --- 設定: アップロード保存先 ---
export interface UploadStorageSettingsData {
  backend: UploadStorageBackend;
  ai_service_adapter: AiServiceAdapter;
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

async function parseEnvelope<T>(res: Response): Promise<ApiResponse<T>> {
  try {
    return (await res.json()) as ApiResponse<T>;
  } catch {
    return { data: null, error_messages: [], warning_messages: [] };
  }
}

/** ApiResponse を展開し、エラー時は ApiError を投げる。 */
async function request<T>(
  path: string,
  init?: RequestInit,
  options: { allowStatus?: number[] } = {}
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
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
  return envelope.data as T;
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

  // ダッシュボード
  getDashboardSummary: () => request<DashboardSummary>("/api/dashboard/summary"),

  // ドキュメント
  listDocuments: (params: {
    status?: FileStatus;
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
    return request<Page<DocumentSummary>>(`/api/documents${qs ? `?${qs}` : ""}`);
  },
  getDocument: (id: string) => request<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}`),
  getDocumentStats: () => request<DocumentStats>("/api/documents/stats"),
  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadResult>("/api/documents/upload", { method: "POST", body: form });
  },
  ingestDocument: (id: string, force = false) =>
    request<DocumentDetail>(
      `/api/documents/${encodeURIComponent(id)}/ingest${force ? "?force=true" : ""}`,
      { method: "POST" }
    ),
  /** 原本ファイルの配信 URL（プレビュー用）。 */
  documentContentUrl: (id: string) => `/api/documents/${encodeURIComponent(id)}/content`,

  // 検索
  search: (body: SearchRequestBody) => request<SearchResponse>("/api/search", jsonBody(body)),
  selectAi: (body: SelectAiRequestBody) =>
    request<SelectAiResponse>("/api/search/select-ai", jsonBody(body)),

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
  readOciConfig: (body: OciConfigReadRequest) =>
    request<OciConfigReadData>("/api/settings/oci/config/read", jsonBody(body)),
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
