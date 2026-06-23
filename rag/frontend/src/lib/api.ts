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
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type FileStatus =
  | "UPLOADED"
  | "INGESTING"
  | "REVIEW"
  | "CHUNKING"
  | "CHUNKED"
  | "INDEXING"
  | "INDEXED"
  | "ERROR";
export type SearchMode = "hybrid" | "vector" | "keyword";
export type SearchStrategy = "hybrid" | "graph_local" | "graph_global";
export type KnowledgeBaseStatus = "ACTIVE" | "ARCHIVED";
export type CitationFeedbackRating = "helpful" | "not_helpful";
export type CitationFeedbackReason =
  | "missing_evidence"
  | "not_relevant"
  | "answer_untrusted";
export type UploadIngestionMode = "manual";
export type SourceModality =
  | "pdf"
  | "image"
  | "text"
  | "html"
  | "email"
  | "office"
  | "audio"
  | "unknown";
export type SourcePreviewKind =
  | "pdf"
  | "image"
  | "text"
  | "html"
  | "email"
  | "office"
  | "unsupported";
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
  | "content_kind_miss"
  | "section_miss"
  | "case_error";
export type EvaluationMetricName =
  | "precision_at_k"
  | "recall_at_k"
  | "mrr"
  | "answer_keyword_hit_rate"
  | "groundedness_pass_rate"
  | "citation_traceability_coverage"
  | "bbox_citation_coverage"
  | "element_lineage_coverage"
  | "content_kind_hit_rate"
  | "section_coverage"
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
export type ParserAdapterBackend =
  | "local"
  | "docling"
  | "marker"
  | "unstructured"
  | "mineru"
  | "dots_ocr"
  | "glm_ocr"
  | "oci_genai_vision"
  // enterprise_ai_vlm は oci_genai_vision の後方互換エイリアス(legacy 保存値の表示用)。
  | "enterprise_ai_vlm"
  | "oci_document_understanding";
export type ParserServiceBackendName = "oci_genai_vision" | "oci_document_understanding";
export type ParserAdapterBackendName = "docling" | "marker" | "unstructured";
export type ParserAdapterStatus = "active" | "available" | "disabled" | "ignored" | "missing";
export type ParserAdapterScoreBackend = "local" | "docling" | "marker" | "unstructured";
export type ParserAdapterScoreStatus =
  | "recommended"
  | "eligible"
  | "available"
  | "disabled"
  | "ignored"
  | "missing";
export type ParserAdapterContractStatus =
  | "passed"
  | "failed"
  | "fallback"
  | "available"
  | "ignored"
  | "disabled"
  | "missing"
  | "unsupported"
  | "fixture_missing";
export type ParserAdapterSourceKind =
  | "pdf"
  | "image"
  | "office"
  | "html"
  | "email"
  | "audio"
  | "text"
  | "unknown";

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
  figure_count: number;
  formula_count: number;
  list_count: number;
  page_count: number;
  low_confidence_count: number;
  fallback_document_count: number;
  failed_segment_document_count: number;
  segment_artifact_cache_miss_document_count: number;
  long_document_count: number;
  average_page_coverage: number;
  risk_counts: Record<string, number>;
  parser_profile_counts: Record<string, number>;
  parser_backend_counts: Record<string, number>;
  warning_counts: Record<string, number>;
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
  parser_backend: string;
  parser_version: string;
  preview_kind: SourcePreviewKind;
  text_charset: string | null;
  duplicate_of_document_id: string | null;
  unsupported_reason: string | null;
  quality_status: "ready" | "warning" | string;
  quality_warnings: string[];
}

export type IngestionJobPhase = "EXTRACT" | "CHUNK" | "INDEX";

export interface DocumentElementTextEdit {
  element_id: string;
  text: string;
}

export interface DocumentTableCellTextEdit {
  table_id: string;
  row: number;
  col: number;
  text: string;
}

export interface DocumentApproveRequest {
  raw_text?: string | null;
  element_edits?: DocumentElementTextEdit[];
  table_cell_edits?: DocumentTableCellTextEdit[];
}

export interface IngestionJob {
  id: string;
  document_id: string;
  status: IngestionJobStatus;
  phase: IngestionJobPhase;
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

export interface DuplicateDocumentRef {
  id: string;
  file_name: string;
  status: FileStatus;
  uploaded_at: string;
  indexed_at: string | null;
}

export interface DocumentElement {
  kind: string;
  text: string;
  order: number;
  element_id?: string | null;
  parent_id?: string | null;
  content_kind?: string | null;
  source_parser?: string | null;
  page_number?: number | null;
  bbox?: number[] | null;
  section_path?: string[];
  confidence?: number | null;
  metadata?: Record<string, string | number | boolean | null>;
}

export interface ExtractionPage {
  page_number: number;
  label?: string | null;
  width?: number | null;
  height?: number | null;
  rotation?: number | null;
  element_ids: string[];
  metadata?: Record<string, string | number | boolean | null>;
}

export interface ExtractionTableCell {
  row: number;
  col: number;
  text: string;
  row_span: number;
  col_span: number;
  page_number?: number | null;
  bbox?: number[] | null;
  confidence?: number | null;
  metadata?: Record<string, string | number | boolean | null>;
}

export interface ExtractionTable {
  table_id: string;
  element_id?: string | null;
  page_number?: number | null;
  caption?: string | null;
  cells: ExtractionTableCell[];
  metadata?: Record<string, string | number | boolean | null>;
}

export interface ExtractionAsset {
  asset_id: string;
  kind: string;
  object_path?: string | null;
  page_number?: number | null;
  bbox?: number[] | null;
  alt_text?: string | null;
  metadata?: Record<string, string | number | boolean | null>;
}

export interface StructuredExtraction {
  raw_text: string;
  document_type: string;
  confidence: number;
  warnings: string[];
  elements: DocumentElement[];
  pages: ExtractionPage[];
  tables: ExtractionTable[];
  assets: ExtractionAsset[];
  parser_artifacts: Record<string, string | number | boolean | null>;
}

export interface DocumentDetail extends DocumentSummary {
  object_storage_path: string | null;
  extraction: Record<string, unknown>;
  error_message: string | null;
  duplicate_source: DuplicateDocumentRef | null;
}

export interface DocumentDeleteResult {
  id: string;
  file_name: string;
  object_storage_path: string | null;
  object_deleted: boolean;
  artifact_deleted_count: number;
  artifact_delete_failed_count: number;
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

export interface BatchUploadFailedItem {
  file_name: string;
  status_code: number;
  message: string;
  source_profile: SourceProfile | null;
}

export interface DocumentChunkView {
  document_id: string;
  chunk_id: string;
  chunk_index: number;
  text: string;
  page_start: number | null;
  page_end: number | null;
  bbox: number[] | null;
  section_path: string | null;
  content_kind: string | null;
  chunk_group_id: string | null;
  source_parser: string | null;
  element_ids: string[];
  metadata: Record<string, JsonValue>;
}

/** 文書の chunk_set(variant = 1 レシピのチャンク集合)1 件分。 */
export type DocumentLayerStatusName =
  | "not_requested"
  | "planned_only"
  | "materialized"
  | "needs_reingest"
  | "error";

export interface DocumentMaterializationLayerStatus {
  layer_id: string | null;
  requested: boolean;
  status: DocumentLayerStatusName;
  reason: string | null;
}

export interface DocumentChunkSetLayerStatuses {
  metadata: DocumentMaterializationLayerStatus;
  graph: DocumentMaterializationLayerStatus;
  navigation: DocumentMaterializationLayerStatus;
}

export interface DocumentChunkSet {
  chunk_set_id: string;
  extraction_recipe_id: string | null;
  extraction_status: DocumentLayerStatusName;
  extraction_reason: string | null;
  status: string;
  chunk_count: number;
  vector_count: number;
  /** 親抽出(extraction)の ID。parser×preprocess ごとに分かれる 2 階層の上位キー。 */
  extraction_id: string | null;
  /** 親抽出の parser backend(2 階層表示のラベル)。 */
  parser: string | null;
  /** 親抽出の前処理プロファイル(2 階層表示のラベル)。 */
  preprocess: string | null;
  knowledge_base_ids: string[];
  serving_knowledge_base_ids: string[];
  layer_statuses: DocumentChunkSetLayerStatuses;
}

export type DocumentExtractionExportFormat = "json" | "markdown" | "html" | "chunks";

export interface DocumentExtractionExport {
  document_id: string;
  file_name: string;
  format: DocumentExtractionExportFormat;
  content_type: string;
  content: string;
  payload: Record<string, unknown>;
  chunks: DocumentChunkView[];
  parser_backend: string | null;
  parser_profile: string | null;
  page_count: number;
  element_count: number;
  table_count: number;
  asset_count: number;
}

export interface IngestionSegment {
  segment_id: string;
  document_id: string;
  status: string;
  parser_backend: string;
  parser_profile: string;
  page_start: number | null;
  page_end: number | null;
  progress_unit: "page" | "slide" | "sheet" | "source" | string;
  progress_start: number | null;
  progress_end: number | null;
  attempt_count: number;
  artifact_path: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface DocumentStats {
  total: number;
  by_status: Partial<Record<FileStatus, number>>;
}

export interface BatchUploadResult {
  items: UploadResult[];
  failed_items: BatchUploadFailedItem[];
  total_count: number;
  uploaded_count: number;
  failed_count: number;
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

/** KB 単位の取込上書き(Parser/Chunking)。null はグローバル継承。 */
export interface KnowledgeBaseIngestionConfig {
  preprocess_profile: PreprocessProfileName | null;
  parser_adapter_backend: ParserAdapterBackend | null;
  parser_docling_enabled: boolean | null;
  parser_marker_enabled: boolean | null;
  parser_unstructured_enabled: boolean | null;
  chunking_strategy: ChunkingStrategyName | null;
  chunk_size: number | null;
  chunk_overlap: number | null;
  chunk_child_size: number | null;
  chunk_sentence_window_size: number | null;
  chunk_min_chars: number | null;
  graph_profile: GraphProfileName | null;
  field_extraction_enabled: boolean | null;
  asset_summary_enabled: boolean | null;
  navigation_summary_enabled: boolean | null;
  auto_chunk_after_extract_enabled: boolean | null;
  auto_index_after_chunk_enabled: boolean | null;
}

/** 検索・回答設定。Business View の query 設定として使う。 */
export interface KnowledgeBaseQueryConfig {
  retrieval_strategy: RetrievalStrategyName | null;
  post_retrieval_pipeline: PostRetrievalPipelineName | null;
  generation_profile: GenerationProfileName | null;
  guardrail_policy: GuardrailPolicyName | null;
  vector_index_profile: VectorIndexProfileName | null;
  evaluation_suite: EvaluationSuiteName | null;
}

/** KB 単位の構築設定。query は legacy 互換として読めるが KB runtime では使わない。 */
export interface KnowledgeBaseAdapterConfig {
  version: number;
  ingestion: KnowledgeBaseIngestionConfig;
  query: KnowledgeBaseQueryConfig;
}

export interface KnowledgeBaseDetail extends KnowledgeBaseSummary {
  retrieval_config: Record<string, unknown>;
  adapter_config: KnowledgeBaseAdapterConfig;
  /** KB 構築設定をグローバル既定で埋めた解決済み設定(表示専用)。 */
  effective_adapter_config?: KnowledgeBaseAdapterConfig | null;
  /** 既存 retrieval_config に legacy query 設定が残っており、現在は無視されている。 */
  legacy_query_config_ignored?: boolean;
}

export interface KnowledgeBaseCreateRequest {
  name: string;
  description?: string | null;
  default_search_mode?: SearchMode;
  retrieval_config?: Record<string, unknown>;
  adapter_config?: KnowledgeBaseAdapterConfig | null;
}

export interface KnowledgeBaseUpdateRequest {
  name?: string | null;
  description?: string | null;
  default_search_mode?: SearchMode | null;
  retrieval_config?: Record<string, unknown> | null;
  adapter_config?: KnowledgeBaseAdapterConfig | null;
}

export type BusinessViewStatus = "ACTIVE" | "ARCHIVED";

export interface BusinessViewRef {
  id: string;
  name: string;
}

/** 配信モード。1 文書が複数 chunk_set を持つときの検索時配信方法。 */
export type ServingMode = "single" | "fused" | "routed";

/** Business View の設定一式。query は検索・回答設定。 */
export interface BusinessViewConfig {
  version: number;
  knowledge_base_ids: string[];
  query: KnowledgeBaseQueryConfig;
  system_prompt: string | null;
  default_language: string | null;
  serving_mode: ServingMode;
}

export interface BusinessViewSummary extends BusinessViewRef {
  description: string | null;
  status: BusinessViewStatus;
  knowledge_base_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface BusinessViewDetail extends BusinessViewSummary {
  config: BusinessViewConfig;
  knowledge_bases: KnowledgeBaseRef[];
}

export interface BusinessViewCreateRequest {
  name: string;
  description?: string | null;
  config?: BusinessViewConfig;
}

export interface BusinessViewUpdateRequest {
  name?: string | null;
  description?: string | null;
  config?: BusinessViewConfig;
}

/** 文書の取込設定スナップショット / owning KB とのドリフト状況。 */
export interface DocumentIngestionConfigData {
  document_id: string;
  is_indexed: boolean;
  owning_knowledge_base: KnowledgeBaseRef | null;
  effective_chunking_strategy: string;
  effective_parser_adapter_backend: string;
  observed_chunking_strategy: string | null;
  observed_parser_backend: string | null;
  chunking_drift: boolean;
  parser_drift: boolean;
  config_drift: boolean;
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
  business_view_id?: string | null;
}

export interface RetrievedChunk {
  document_id: string;
  chunk_id: string;
  text: string;
  score: number;
  rerank_score: number | null;
  file_name: string | null;
  category_name: string | null;
  metadata: Record<string, JsonValue>;
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
  context_adaptive_expanded_count: number;
  context_dependency_promoted_count: number;
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
  business_view_applied?: string | null;
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
  expected_content_kind?: string | null;
  expected_section_paths?: string[];
}

export interface EvaluationThresholds {
  precision_at_k?: number | null;
  recall_at_k?: number | null;
  mrr?: number | null;
  answer_keyword_hit_rate?: number | null;
  groundedness_pass_rate?: number | null;
  citation_traceability_coverage?: number | null;
  bbox_citation_coverage?: number | null;
  element_lineage_coverage?: number | null;
  content_kind_hit_rate?: number | null;
  section_coverage?: number | null;
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
  suite?: EvaluationSuiteName | null;
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
  citation_traceability_coverage: number;
  bbox_citation_coverage: number;
  element_lineage_coverage: number;
  content_kind_hit_rate: number;
  section_coverage: number;
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
  evaluation_suite: EvaluationSuiteName;
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
  citation_traceability_coverage: number;
  bbox_citation_coverage: number;
  element_lineage_coverage: number;
  content_kind_hit_rate: number;
  section_coverage: number;
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
  formula_document_count: number;
  low_confidence_document_count: number;
  fallback_document_count: number;
  failed_segment_document_count: number;
  segment_artifact_cache_miss_document_count: number;
  long_document_count: number;
  average_page_coverage: number;
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
  context_adaptive_expansion_enabled?: boolean | null;
  context_adaptive_neighbor_window?: number | null;
  context_adaptive_min_overlap?: number | null;
  context_group_expansion_enabled?: boolean | null;
  context_group_max_chunks?: number | null;
  context_dependency_promotion_enabled?: boolean | null;
  context_dependency_max_chunks?: number | null;
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
  suite?: EvaluationSuiteName | null;
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

export type EnterpriseAiVlmInputMode = "files_api" | "inline_image";

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

// --- 設定: Parser adapter ---
export interface ParserAdapterStatusData {
  backend: ParserAdapterBackendName;
  package_name: string;
  import_name: string;
  distribution_name: string | null;
  install_package: string;
  enabled: boolean;
  selected: boolean;
  installed: boolean;
  status: ParserAdapterStatus;
  version: string | null;
  warning_code: string | null;
}

export interface ParserAdapterScorecardEntryData {
  backend: ParserAdapterScoreBackend;
  rank: number;
  score: number;
  status: ParserAdapterScoreStatus;
  recommended: boolean;
  executable: boolean;
  selected: boolean;
  enabled: boolean;
  installed: boolean;
  metric_source: string;
  metric_count: number;
  signals: Record<string, number>;
  reason_codes: string[];
  warning_codes: string[];
}

export interface ParserAdapterScorecardData {
  selected_backend: ParserAdapterBackend;
  recommended_backend: ParserAdapterScoreBackend;
  metrics_source: string;
  metrics_applied_to: ParserAdapterScoreBackend | null;
  entries: ParserAdapterScorecardEntryData[];
}

export interface ParserAdapterSourceRouteData {
  source_kind: ParserAdapterSourceKind | string;
  candidate_order: ParserAdapterScoreBackend[];
  attempted_order: ParserAdapterScoreBackend[];
  active_order: ParserAdapterScoreBackend[];
  selected_backend: ParserAdapterScoreBackend;
  reason_codes: string[];
  warning_codes: string[];
}

export interface ParserAdapterBackendSourceMatrixData {
  evidence_source: "runtime_routes";
  required_source_kinds: string[];
  covered_source_kinds: string[];
  missing_source_kinds: string[];
  backend_source_kinds: Partial<Record<ParserAdapterScoreBackend, string[]>>;
  route_evidence: ParserAdapterSourceRouteData[];
}

export interface ParserAdapterContractCaseData {
  backend: ParserAdapterBackendName;
  source_kind: string;
  fixture_name: string;
  content_type: string;
  status: ParserAdapterContractStatus;
  blocking: boolean;
  parser_backend: string | null;
  parser_version: string | null;
  adapter_import_name: string | null;
  adapter_distribution_name: string | null;
  adapter_package_version: string | null;
  template: string | null;
  element_count: number;
  page_count: number;
  table_count: number;
  table_cell_count: number;
  asset_count: number;
  bbox_count: number;
  warning_codes: string[];
  reason_codes: string[];
}

export interface ParserAdapterContractSummaryData {
  passed: boolean;
  case_count: number;
  blocking_failure_count: number;
  source_kinds: string[];
  backends: ParserAdapterBackendName[];
  passed_source_kinds: string[];
  missing_source_kinds: string[];
  blocking_failure_source_kinds: string[];
  blocking_failure_backends: ParserAdapterBackendName[];
  backend_status_counts: Partial<Record<ParserAdapterBackendName, Partial<Record<string, number>>>>;
  backend_source_status: Partial<Record<ParserAdapterBackendName, Record<string, string>>>;
  backend_source_status_counts: Partial<
    Record<ParserAdapterBackendName, Record<string, Partial<Record<string, number>>>>
  >;
  source_kind_status_counts: Record<string, Partial<Record<string, number>>>;
  backend_passed_source_kinds: Partial<Record<ParserAdapterBackendName, string[]>>;
  scenarios: string[];
  passed_scenarios: string[];
  missing_scenarios: string[];
  blocking_failure_scenarios: string[];
  backend_passed_scenarios: Partial<Record<ParserAdapterBackendName, string[]>>;
  reason_code_counts: Record<string, number>;
  warning_code_counts: Record<string, number>;
  blocking_failure_reason_counts: Record<string, number>;
  blocking_failures: Array<{
    backend?: string;
    source_kind?: string;
    status?: string;
    warning_codes?: string[];
    reason_codes?: string[];
  }>;
}

export interface ParserAdapterContractData {
  passed: boolean;
  fixture_root: string;
  source_kinds: string[];
  backends: ParserAdapterBackendName[];
  case_count: number;
  blocking_failure_count: number;
  cases: ParserAdapterContractCaseData[];
  summary: ParserAdapterContractSummaryData;
  config_source: "runtime";
}

export interface ParserServiceBackendData {
  backend: ParserServiceBackendName;
  selected: boolean;
  configured: boolean;
  warning_code: string | null;
}

export interface ParserAdapterSettingsData {
  adapter_backend: ParserAdapterBackend;
  effective_order: ParserAdapterBackendName[];
  adapters: ParserAdapterStatusData[];
  service_backends: ParserServiceBackendData[];
  scorecard: ParserAdapterScorecardData;
  source_routes: ParserAdapterSourceRouteData[];
  backend_source_kind_matrix: ParserAdapterBackendSourceMatrixData;
  config_source: "runtime";
}

export interface ParserAdapterSettingsUpdate {
  adapter_backend: ParserAdapterBackend;
  docling_enabled: boolean;
  marker_enabled: boolean;
  unstructured_enabled: boolean;
}

// --- 設定: Chunking アダプター ---
export type ChunkingStrategyName =
  | "structure_aware"
  | "recursive_character"
  | "sentence_window"
  | "hierarchical_parent_child"
  | "markdown_heading"
  | "page_level"
  | "fixed_size";

// --- 設定: 前処理(Preprocess)アダプター ---
export type PreprocessProfileName =
  | "passthrough"
  | "text_normalize"
  | "office_to_pdf"
  | "pdf_to_page_images"
  | "csv_to_json"
  | "excel_to_json";

export interface PreprocessProfileStatusData {
  name: PreprocessProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  in_process: boolean;
  requires_service: boolean;
  available: boolean;
}

export interface PreprocessSettingsData {
  profile: PreprocessProfileName;
  service_enabled: boolean;
  service_url: string;
  canonical_artifact_prefix: string;
  profiles: PreprocessProfileStatusData[];
  config_source: "runtime";
}

export interface PreprocessSettingsUpdate {
  profile: PreprocessProfileName;
}

// --- サービス管理（前処理 / Parser マイクロサービスの稼働可視化・起動/停止）---
export type ServiceCategory =
  | "preprocess"
  | "parser"
  | "chunking"
  | "vector_index"
  | "retrieval"
  | "grounding"
  | "generation"
  | "guardrail"
  | "evaluation"
  | "graphrag"
  | "agentic";
export type ServiceProfile = "cpu" | "gpu" | "oci";
export type ServiceRuntimeStatus =
  | "running"
  | "degraded"
  | "stopped"
  | "dependency_stopped"
  | "unconfigured";
export type ServiceAction = "start" | "stop" | "restart";

export interface ServiceCatalogItemData {
  service_id: string;
  category: ServiceCategory;
  profile: ServiceProfile;
  label_key: string;
  depends_on: string[];
  configured: boolean;
}

export interface ServiceStatusData extends ServiceCatalogItemData {
  status: ServiceRuntimeStatus;
  blocked_by: string[];
}

export type DeploymentMode = "dev" | "prod";

export interface ServiceCatalogData {
  control_enabled: boolean;
  deployment_mode: DeploymentMode;
  services: ServiceCatalogItemData[];
}

export interface ServiceListData {
  control_enabled: boolean;
  deployment_mode: DeploymentMode;
  services: ServiceStatusData[];
}

export interface ServiceControlResultData {
  service_id: string;
  action: ServiceAction;
  status: ServiceRuntimeStatus;
}

export type ServiceLogsSource = "docker" | "uv";

export interface ServiceLogsData {
  service_id: string;
  source: ServiceLogsSource;
  lines: number;
  content: string;
}

export interface ChunkingStrategyStatusData {
  name: ChunkingStrategyName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  uses_child_size: boolean;
  uses_sentence_window: boolean;
}

export interface ChunkingSettingsData {
  strategy: ChunkingStrategyName;
  chunk_size: number;
  overlap: number;
  child_size: number;
  sentence_window_size: number;
  min_chars: number;
  strategies: ChunkingStrategyStatusData[];
  config_source: "runtime";
}

export interface ChunkingSettingsUpdate {
  strategy: ChunkingStrategyName;
  chunk_size: number;
  overlap: number;
  child_size: number;
  sentence_window_size: number;
  min_chars: number;
}

// --- 設定: Retrieval アダプター ---
export type RetrievalStrategyName =
  | "hybrid_rrf"
  | "vector"
  | "keyword"
  | "graph_augmented"
  | "business_context_strict"
  | "corrective_multi_query";

export interface RetrievalStrategyStatusData {
  name: RetrievalStrategyName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  gap_stop: boolean;
  corrective_retrieval: boolean;
  business_fit_weighting: boolean;
}

export interface RetrievalSettingsData {
  strategy: RetrievalStrategyName;
  query_expansion: boolean;
  gap_stop: boolean;
  corrective_retrieval: boolean;
  business_fit_weighting: boolean;
  strategies: RetrievalStrategyStatusData[];
  config_source: "runtime";
}

export interface RetrievalSettingsUpdate {
  strategy: RetrievalStrategyName;
}

// --- 設定: Grounding アダプター ---
export type PostRetrievalPipelineName =
  | "custom"
  | "lean"
  | "verified_context"
  | "context_enrich"
  | "compact"
  | "full_governed";

export type GroundingExpansionMode = "none" | "neighbor" | "group" | "adaptive";

export interface GroundingPipelineStatusData {
  name: PostRetrievalPipelineName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  dependency_promotion: boolean;
  diversity: boolean;
  expansion_mode: GroundingExpansionMode;
  compression: boolean;
}

export interface GroundingSettingsData {
  pipeline: PostRetrievalPipelineName;
  dependency_promotion_enabled: boolean;
  diversity_enabled: boolean;
  expansion_mode: GroundingExpansionMode;
  compression_enabled: boolean;
  pipelines: GroundingPipelineStatusData[];
  config_source: "runtime";
}

export interface GroundingSettingsUpdate {
  pipeline: PostRetrievalPipelineName;
}

// --- 設定: Generation アダプター ---
export type GenerationProfileName =
  | "grounded_concise"
  | "detailed_cited"
  | "strict_extractive"
  | "structured_json"
  | "bilingual_ja_en";

export interface GenerationProfileStatusData {
  name: GenerationProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  structured_output: boolean;
}

export interface GenerationSettingsData {
  profile: GenerationProfileName;
  structured_output: boolean;
  profiles: GenerationProfileStatusData[];
  config_source: "runtime";
}

export interface GenerationSettingsUpdate {
  profile: GenerationProfileName;
}

// --- 設定: Guardrail アダプター ---
export type GuardrailPolicyName = "standard" | "strict" | "lenient" | "regulated";

export interface GuardrailPolicyStatusData {
  name: GuardrailPolicyName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  grounding_min_overlap: number;
  grounding_min_ratio: number;
  audit_emphasis: boolean;
}

export interface GuardrailSettingsData {
  policy: GuardrailPolicyName;
  block_prompt_injection: boolean;
  mask_sensitive_identifiers: boolean;
  max_query_chars: number;
  grounding_min_overlap: number;
  grounding_min_ratio: number;
  audit_emphasis: boolean;
  policies: GuardrailPolicyStatusData[];
  config_source: "runtime";
}

export interface GuardrailSettingsUpdate {
  policy: GuardrailPolicyName;
}

// --- 設定: Vector Index アダプター ---
export type VectorIndexProfileName = "balanced" | "accurate" | "fast";

export interface VectorIndexProfileStatusData {
  name: VectorIndexProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  target_accuracy: number;
  neighbors: number;
  efconstruction: number;
  distance: string;
}

export interface VectorIndexSettingsData {
  profile: VectorIndexProfileName;
  target_accuracy: number;
  neighbors: number;
  efconstruction: number;
  distance: string;
  requires_reprovision: boolean;
  profiles: VectorIndexProfileStatusData[];
  config_source: "runtime";
}

export interface VectorIndexSettingsUpdate {
  profile: VectorIndexProfileName;
}

// --- 設定: Evaluation アダプター ---
export type EvaluationSuiteName =
  | "request_only"
  | "retrieval_focused"
  | "balanced"
  | "strict_ci"
  | "ragas_like";

export interface EvaluationSuiteStatusData {
  name: EvaluationSuiteName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  thresholds: Record<string, number>;
  focus_metrics: string[];
}

export interface EvaluationSettingsData {
  suite: EvaluationSuiteName;
  thresholds: Record<string, number>;
  focus_metrics: string[];
  suites: EvaluationSuiteStatusData[];
  config_source: "runtime";
}

export interface EvaluationSettingsUpdate {
  suite: EvaluationSuiteName;
}

// --- 設定: GraphRAG アダプター ---
export type GraphProfileName = "off" | "entities" | "full";

export interface GraphProfileStatusData {
  name: GraphProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  enabled: boolean;
  build_claims: boolean;
  build_community_summaries: boolean;
}

export interface GraphSettingsData {
  profile: GraphProfileName;
  enabled: boolean;
  build_claims: boolean;
  build_community_summaries: boolean;
  profiles: GraphProfileStatusData[];
  config_source: "runtime";
}

export interface GraphSettingsUpdate {
  profile: GraphProfileName;
}

// --- 設定: Agentic アダプター ---
export type AgenticProfileName = "off" | "query_rewrite" | "decompose" | "multi_hop";

export interface AgenticProfileStatusData {
  name: AgenticProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  enabled: boolean;
  rewrite: boolean;
  decompose: boolean;
  multi_hop: boolean;
}

export interface AgenticSettingsData {
  profile: AgenticProfileName;
  enabled: boolean;
  rewrite: boolean;
  decompose: boolean;
  multi_hop: boolean;
  max_subqueries: number;
  profiles: AgenticProfileStatusData[];
  config_source: "runtime";
}

export interface AgenticSettingsUpdate {
  profile: AgenticProfileName;
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

function ingestionJobSearch(force: boolean, phase: IngestionJobPhase): string {
  const search = new URLSearchParams();
  if (force) search.set("force", "true");
  search.set("phase", phase);
  return search.toString();
}

export const api = {
  // 認証
  getAuthStatus: () => request<AuthStatus>("/api/auth/me"),
  login: (body: LoginRequestBody) => request<AuthStatus>("/api/auth/login", jsonBody(body)),
  logout: () => request<AuthStatus>("/api/auth/logout", { method: "POST" }),

  // ヘルスチェック
  getReadiness: () => request<HealthData>("/api/ready", undefined, { allowStatus: [503] }),

  // データベース利用可否(設定の有無 + 実接続プローブ)。DB ゲートが参照する。
  getDatabaseStatus: () => request<DatabaseStatusData>("/api/ready/database"),

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
  listDocumentChunks: (id: string) =>
    request<DocumentChunkView[]>(`/api/documents/${encodeURIComponent(id)}/chunks`),
  listDocumentChunkSets: (id: string) =>
    request<DocumentChunkSet[]>(`/api/documents/${encodeURIComponent(id)}/chunk-sets`),
  getDocumentIngestionConfig: (id: string) =>
    request<DocumentIngestionConfigData>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-config`
    ),
  exportDocumentExtraction: (id: string, format: DocumentExtractionExportFormat = "markdown") => {
    const search = new URLSearchParams({ format });
    return request<DocumentExtractionExport>(
      `/api/documents/${encodeURIComponent(id)}/extraction-export?${search.toString()}`
    );
  },
  listDocumentIngestionJobs: (id: string) =>
    request<IngestionJob[]>(`/api/documents/${encodeURIComponent(id)}/ingestion-jobs`),
  listDocumentIngestionSegments: (id: string) =>
    request<IngestionSegment[]>(`/api/documents/${encodeURIComponent(id)}/ingestion-segments`),
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
  ingestDocument: (id: string, force = false, phase: IngestionJobPhase = "EXTRACT") =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-jobs?${ingestionJobSearch(force, phase)}`,
      { method: "POST" }
    ),
  enqueueDocumentIngestionJob: (
    id: string,
    force = false,
    phase: IngestionJobPhase = "EXTRACT"
  ) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-jobs?${ingestionJobSearch(force, phase)}`,
      { method: "POST" }
    ),
  retryFailedDocumentIngestionSegments: (id: string) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-segments/retry`,
      { method: "POST" }
    ),
  /** 現在の確認段階を承認し、次の取込 stage を投入する。任意で抽出テキスト修正を伴う。 */
  approveDocument: (id: string, payload?: DocumentApproveRequest) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/approve`,
      payload ? jsonBody(payload) : { method: "POST" }
    ),
  /** REVIEW(確認待ち)文書を却下し、UPLOADED へ戻す。 */
  rejectDocument: (id: string) =>
    request<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}/reject`, {
      method: "POST",
    }),
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
  getKnowledgeBase: (id: string) =>
    request<KnowledgeBaseDetail>(`/api/knowledge-bases/${encodeURIComponent(id)}`),
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

  // 業務ビュー(Business View)
  listBusinessViews: (params: {
    status?: BusinessViewStatus;
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
    return requestDegradable<Page<BusinessViewSummary>>(
      `/api/business-views${qs ? `?${qs}` : ""}`
    );
  },
  getBusinessView: (id: string) =>
    request<BusinessViewDetail>(`/api/business-views/${encodeURIComponent(id)}`),
  createBusinessView: (body: BusinessViewCreateRequest) =>
    request<BusinessViewDetail>("/api/business-views", jsonBody(body)),
  updateBusinessView: (id: string, body: BusinessViewUpdateRequest) =>
    request<BusinessViewDetail>(`/api/business-views/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  archiveBusinessView: (id: string) =>
    request<BusinessViewDetail>(`/api/business-views/${encodeURIComponent(id)}/archive`, {
      method: "POST",
    }),

  // 検索
  search: (body: SearchRequestBody) => request<SearchResponse>("/api/search", jsonBody(body)),
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
  getParserAdapterSettings: () =>
    request<ParserAdapterSettingsData>("/api/settings/parser-adapters"),
  getParserAdapterContract: () =>
    request<ParserAdapterContractData>("/api/settings/parser-adapters/contract"),
  updateParserAdapterSettings: (body: ParserAdapterSettingsUpdate) =>
    request<ParserAdapterSettingsData>("/api/settings/parser-adapters", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // サービス管理: 前処理 / Parser マイクロサービスの稼働可視化・起動/停止
  getServiceCatalog: () => request<ServiceCatalogData>("/api/services/catalog"),
  getServiceStatus: (serviceId: string) =>
    request<ServiceStatusData>(`/api/services/${encodeURIComponent(serviceId)}/status`),
  getServiceLogs: (serviceId: string, lines = 200) =>
    request<ServiceLogsData>(
      `/api/services/${encodeURIComponent(serviceId)}/logs?lines=${encodeURIComponent(String(lines))}`
    ),
  getServices: () => request<ServiceListData>("/api/services"),
  controlService: (serviceId: string, action: ServiceAction) =>
    request<ServiceControlResultData>(
      `/api/services/${encodeURIComponent(serviceId)}/${action}`,
      { method: "POST" }
    ),

  // 設定: Chunking アダプター
  getPreprocessSettings: () => request<PreprocessSettingsData>("/api/settings/preprocess"),
  updatePreprocessSettings: (body: PreprocessSettingsUpdate) =>
    request<PreprocessSettingsData>("/api/settings/preprocess", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  getChunkingSettings: () => request<ChunkingSettingsData>("/api/settings/chunking"),
  updateChunkingSettings: (body: ChunkingSettingsUpdate) =>
    request<ChunkingSettingsData>("/api/settings/chunking", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Retrieval アダプター
  getRetrievalSettings: () => request<RetrievalSettingsData>("/api/settings/retrieval"),
  updateRetrievalSettings: (body: RetrievalSettingsUpdate) =>
    request<RetrievalSettingsData>("/api/settings/retrieval", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Grounding アダプター
  getGroundingSettings: () => request<GroundingSettingsData>("/api/settings/grounding"),
  updateGroundingSettings: (body: GroundingSettingsUpdate) =>
    request<GroundingSettingsData>("/api/settings/grounding", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Generation アダプター
  getGenerationSettings: () => request<GenerationSettingsData>("/api/settings/generation"),
  updateGenerationSettings: (body: GenerationSettingsUpdate) =>
    request<GenerationSettingsData>("/api/settings/generation", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Guardrail アダプター
  getGuardrailSettings: () => request<GuardrailSettingsData>("/api/settings/guardrail"),
  updateGuardrailSettings: (body: GuardrailSettingsUpdate) =>
    request<GuardrailSettingsData>("/api/settings/guardrail", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Vector Index アダプター
  getVectorIndexSettings: () => request<VectorIndexSettingsData>("/api/settings/vector-index"),
  updateVectorIndexSettings: (body: VectorIndexSettingsUpdate) =>
    request<VectorIndexSettingsData>("/api/settings/vector-index", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Evaluation アダプター
  getEvaluationSettings: () => request<EvaluationSettingsData>("/api/settings/evaluation-suite"),
  updateEvaluationSettings: (body: EvaluationSettingsUpdate) =>
    request<EvaluationSettingsData>("/api/settings/evaluation-suite", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: GraphRAG アダプター
  getGraphSettings: () => request<GraphSettingsData>("/api/settings/graph"),
  updateGraphSettings: (body: GraphSettingsUpdate) =>
    request<GraphSettingsData>("/api/settings/graph", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Agentic アダプター
  getAgenticSettings: () => request<AgenticSettingsData>("/api/settings/agentic"),
  updateAgenticSettings: (body: AgenticSettingsUpdate) =>
    request<AgenticSettingsData>("/api/settings/agentic", {
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
