/**
 * バックエンド API クライアント。
 *
 * - レスポンスは共通エンベロープ `ApiResponse<T>`（snake_case）。
 * - `/api/*` は Vite dev/preview proxy または本番の Nginx(host)でバックエンドへ転送される。
 * - 型はバックエンドの Pydantic スキーマ（snake_case）にそのまま対応させる。
 */

import { t } from "./i18n";
// Cookie セッションの CSRF と 401 / 403 の通知は3製品共通（platform の共有パッケージ。#220 / #214）。
import { csrfHeader, notifyAuthResponse, notifyAuthStatus } from "@engchina/production-ready-system-settings";
import type { BaseCurrentUser } from "@engchina/production-ready-system-settings";

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
  DatabaseWalletDownloadData,
} from "@engchina/production-ready-system-settings";
import type {
  AdbInfoData,
  AdbSettingsUpdate,
  DatabaseConnectionTestResult,
  DatabaseSettingsData,
  DatabaseSettingsUpdate,
  DatabaseWalletDownloadData,
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

export const API_REQUEST_TIMEOUT_MS = resolveTimeoutMs(
  import.meta.env.VITE_API_TIMEOUT_MS,
  30_000,
);

/**
 * 保存済みの回答の評価（標準回答による評価）の timeout（#304）。評価は LLM を複数回呼ぶため、
 * 通常の API の 30 秒では足りない。backend は評価全体を LLM 1 回の timeout の設定の上限（600 秒）で
 * 打ち切って 504 と理由を返すので、画面はそれより 30 秒長く待ち、backend の理由を表示する。
 * Nginx（`init_script.sh` が生成する設定）はさらに長い 660 秒にしている。
 */
export const ANSWER_EVALUATION_TIMEOUT_MS = 630_000;

/**
 * 回答を LLM で生成する `POST /api/search`（非ストリーム）の timeout（#375）。backend は回答生成を
 * `RAG_ANSWER_TIMEOUT_SECONDS`（上限 600 秒）で打ち切って 504 と理由（時間切れになった工程）を返すので、
 * 画面はそれより 30 秒長く待つ。SSE（`/api/search/stream`・チャットの送信）は画面で打ち切らない。
 */
export const ANSWER_GENERATION_TIMEOUT_MS = 630_000;

/**
 * 品質評価の job の状態を取得する間隔（#390）。評価は job（投入 → 状態の取得 → 結果）で動き、
 * 投入と状態の取得は通常の API の timeout に収まる。
 */
export const EVALUATION_JOB_POLL_INTERVAL_MS = 2_000;

/** チャットが会話の回答の保存有無を一度に引き当てる trace_id の上限（backend と同じ）。 */
export const ANSWER_TRACE_ID_FILTER_MAX = 100;

/** DB 停止時に warning_messages を併せて返す閲覧系レスポンス。 */
export type Degradable<T> = T & { warning_messages: string[] };
export type JsonValue =
  string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type FileStatus =
  | "UPLOADED"
  | "PREPROCESSING"
  | "PREPROCESSED"
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
  | "incorrect"
  | "incomplete"
  | "missing_evidence"
  | "not_relevant"
  | "answer_untrusted"
  | "missing_knowledge"
  | "outdated_source"
  | "ambiguous_question";
export type FeedbackTargetType = "answer" | "citation";
export type FeedbackSourceSurface = "search" | "chat";
export type SourceModality =
  "pdf" | "image" | "text" | "html" | "email" | "office" | "audio" | "unknown";
export type SourcePreviewKind =
  "pdf" | "image" | "text" | "html" | "email" | "office" | "unsupported";
export type IngestionJobStatus =
  "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED" | "CANCELLED";
/** 評価の失敗理由（#591）。保存済みの結果には削除した理由が残るため、表示は string で受ける。 */
export type EvaluationFailureReason =
  | "retrieval_miss"
  | "partial_recall"
  | "unexpected_answer"
  | "unexpected_refusal"
  | "answer_keyword_miss"
  | "low_groundedness"
  | "unsupported_claim"
  | "missing_content"
  | "answer_failed"
  | "answer_evaluation_error"
  | "guardrail_warning"
  | "case_error";
/** 評価の指標（#591）。検索・根拠・回答の 3 つの観点に整理した 9 つ。 */
export type EvaluationMetricName =
  | "context_recall"
  | "mrr"
  | "faithfulness"
  | "citation_traceability_coverage"
  | "claim_support_rate"
  | "answer_keyword_hit_rate"
  | "refusal_accuracy"
  | "requirement_coverage"
  | "answer_pass_rate";

export type ParserAdapterBackend =
  | "local"
  | "docling"
  | "unstructured"
  | "mineru"
  | "dots_ocr"
  | "oci_genai_vision"
  // enterprise_ai_vlm は oci_genai_vision の後方互換エイリアス(legacy 保存値の表示用)。
  | "enterprise_ai_vlm"
  | "oci_document_understanding";
export type ParserServiceBackendName =
  "oci_genai_vision" | "oci_document_understanding";
export type ParserAdapterBackendName =
  | "docling"
  | "unstructured"
  | "mineru"
  | "dots_ocr";
export type ExternalParserBackendName = "mineru" | "dots_ocr";
export type ExternalParserProtocol =
  "mineru_file_parse" | "openai_chat_completions";
export type ExternalParserConnectionStatus =
  | "available"
  | "unconfigured"
  | "unreachable"
  | "model_missing"
  | "invalid_response";
export type ParserAdapterStatus =
  "active" | "available" | "disabled" | "ignored" | "missing";
export type ParserAdapterScoreBackend = "local" | ParserAdapterBackendName;
export type ParserAdapterScoreStatus =
  "recommended" | "eligible" | "available" | "disabled" | "ignored" | "missing";
export type ParserAdapterSourceKind =
  "pdf" | "image" | "office" | "html" | "email" | "audio" | "text" | "unknown";

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

// --- 認証（platform の共通認証。#214） ---
/** CSRF の double submit に使う Cookie 名（backend の `RAG_APP_AUTH_CSRF_COOKIE_NAME` の既定値）。 */
export const CSRF_COOKIE_NAME = "rag_csrf";

/**
 * `GET /api/auth/me` などが返すログイン中の利用者。`permissions` は implies を展開済み。
 * `allowed_*_ids` が null なら制限なし（SYSTEM_ADMIN・`rag.*.manage`・ローカル DEBUG）。
 */
export interface CurrentUser extends BaseCurrentUser {
  allowed_business_view_ids: string[] | null;
  allowed_knowledge_base_ids: string[] | null;
}

/** ロール（共通のロール項目に RAG の権限と対象範囲を足したもの）。 */
export interface SecurityRole {
  role_id: string;
  role_code: string;
  display_name: string;
  description: string;
  is_built_in: boolean;
  archived: boolean;
  version: number;
  permissions: string[];
  business_view_ids: string[];
  knowledge_base_ids: string[];
}

/** 権限管理で選べる対象（業務ビュー・ナレッジベース）。 */
export interface AccessTarget {
  id: string;
  name: string;
  status: string;
  description: string | null;
}

/** 権限管理の対象の種類（`GET /api/security/access-targets/{kind}`。#608）。 */
export type AccessTargetKind = "business-views" | "knowledge-bases";

/** 権限管理の対象の候補の 1 ページ（検索とページング。#608）。 */
export interface AccessTargetPage {
  items: AccessTarget[];
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
  business_view_ids: string[];
  knowledge_base_ids: string[];
}

export type DatabaseAvailability =
  "ok" | "not_configured" | "unreachable" | "setup_required";

export interface DatabaseStatusData {
  status: DatabaseAvailability;
  check: string;
  detail: string | null;
  schema_status: SystemTableSchemaStatus | null;
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

export type IngestionJobPhase = "PREPROCESS" | "EXTRACT" | "CHUNK" | "INDEX";

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

export interface DocumentReviewEditsRequest {
  element_edits?: DocumentElementTextEdit[];
  table_cell_edits?: DocumentTableCellTextEdit[];
}

export interface DocumentApproveRequest extends DocumentReviewEditsRequest {
  raw_text?: string | null;
}

export interface IngestionJob {
  id: string;
  document_id: string;
  /** 文書のファイル名。一覧・取得の応答だけが持つ（作成直後の応答などでは null。#306）。 */
  document_file_name?: string | null;
  recipe_id: string | null;
  recipe_revision: number | null;
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
  /** 検索対象のレシピの派生情報レイヤーに、作り直しが必要なものがあるか（一覧だけが返す。#550）。 */
  layers_rebuild_required?: boolean;
}

export interface DuplicateDocumentRef {
  id: string;
  file_name: string;
  status: FileStatus;
  uploaded_at: string;
  indexed_at: string | null;
}

export interface DocumentPreprocessArtifact {
  derivation_id: string;
  profile: string;
  converted: boolean;
  converter_name: string | null;
  converter_version: string | null;
  source_content_type: string | null;
  source_sha256: string | null;
  object_storage_path: string | null;
  content_type: string | null;
  sha256: string | null;
  file_name: string;
  page_map: Record<string, number>;
  warnings: string[];
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
  /** 図表 VLM 要約(有効時のみ)。 */
  summary?: string | null;
  metadata?: Record<string, string | number | boolean | null>;
}

/** 章節 navigation tree のノード(ナビゲーション要約 有効時は summary 付き)。 */
export interface DocumentNavigationNode {
  section_id: string;
  title: string;
  section_path: string[];
  depth: number;
  parent_section_id: string | null;
  page_start: number | null;
  page_end: number | null;
  summary: string | null;
}

/** schema 駆動で抽出した項目(メタデータ/項目抽出 有効時のみ)。 */
export interface ExtractionField {
  name: string;
  value: string;
  value_type: string;
  confidence: number | null;
  page_number: number | null;
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
  preprocess_artifact: DocumentPreprocessArtifact | null;
  extraction: Record<string, unknown>;
  error_message: string | null;
  duplicate_source: DuplicateDocumentRef | null;
  /** 文書の分類と有効期間(検索の分類フィルタと基準日に使う)。未設定は null。 */
  classification?: DocumentClassification | null;
}

/** 文書の分類と有効期間。日付は YYYY-MM-DD、終了日は排他的(当日は期間外)。 */
export interface DocumentClassification {
  large_category: string | null;
  middle_category: string | null;
  small_category: string | null;
  effective_from: string | null;
  effective_to: string | null;
}

/** 分類の入力の候補（保存済みの文書の分類の値。番号の接頭辞付きの表記を優先する。#547）。 */
export interface DocumentClassificationOptions {
  large_categories: string[];
  middle_categories: string[];
  small_categories: string[];
}

export interface DocumentDeleteResult {
  id: string;
  file_name: string;
  object_storage_path: string | null;
  object_deleted: boolean;
  artifact_deleted_count: number;
  artifact_delete_failed_count: number;
}

/** 削除の前に確認する影響（#303）。正本を参照する重複文書の件数と、その所属ナレッジベース。 */
export interface DocumentDeleteImpact {
  document_id: string;
  duplicate_count: number;
  knowledge_bases: KnowledgeBaseRef[];
}

/**
 * 選んだ文書解析エンジン（既定は Docling。PDF と画像だけ）で扱えない形式の案内（#286）。
 * 取込は始めず、処理レシピで Unstructured を選ぶよう案内する。
 */
export interface ParserSourceNotice {
  code: string;
  backend: string;
  file_format: string;
  suggested_backend: string | null;
  message: string;
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
  parser_notice?: ParserSourceNotice | null;
}

export interface BatchUploadFailedItem {
  file_name: string;
  status_code: number;
  message: string;
  source_profile: SourceProfile | null;
}

/** プレビューのページ画像 1 ページの表示寸法（pt。ページの /Rotate を反映した向き）。 */
export interface DocumentPreviewPage {
  page_number: number;
  width: number;
  height: number;
}

export interface DocumentPreviewPages {
  page_count: number;
  pages: DocumentPreviewPage[];
}

function documentPreviewPagesBase(id: string, recipeId?: string | null): string {
  const base = `/api/documents/${encodeURIComponent(id)}`;
  return recipeId
    ? `${base}/recipes/${encodeURIComponent(recipeId)}/preview-pages`
    : `${base}/preview-pages`;
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

/** レイヤーの作成後に変わると作り直しが必要になる入力（backend の layer_fingerprint。#550）。 */
export type DocumentLayerRebuildInput =
  | "field_schema_hash"
  | "docrag_chunk_contract"
  | "navigation_summary_max_nodes";

export interface DocumentMaterializationLayerStatus {
  layer_id: string | null;
  requested: boolean;
  status: DocumentLayerStatusName;
  reason: string | null;
  /** 作ったときの入力が今の設定と違う（status とは別の印。指紋の無い古い行は false）。 */
  rebuild_required?: boolean;
  rebuild_inputs?: string[];
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
  /** 配信中(serving)か。文書につき 1 つだけ true。candidate(実験)は false。 */
  is_serving: boolean;
  /** chunk_set の作成日時。診断行の「chunk_set 作成」表示に使う。 */
  created_at: string | null;
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

export interface DocumentChunkPreviewRequest {
  chunking_strategy?: ChunkingStrategyName;
  chunk_size?: number;
  chunk_overlap?: number;
  chunk_min_chars?: number;
  chunk_delimiter?: string;
  docrag_child_target_chars?: number;
  docrag_table_child_target_chars?: number;
  docrag_parent_target_chars?: number;
  docrag_parent_max_pages?: number;
  docrag_parent_max_children?: number;
  chunk_context_header_enabled?: boolean;
}

export interface DocumentChunkPreviewStats {
  chunk_count: number;
  min_chars: number;
  average_chars: number;
  max_chars: number;
  overflow_count: number;
  embedding_overflow_count: number;
}

export interface DocumentChunkPreviewResponse {
  chunks: DocumentChunkView[];
  stats: DocumentChunkPreviewStats;
  warnings: string[];
}

export type DocumentExtractionExportFormat =
  "json" | "markdown" | "html" | "chunks";

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
  recipe_id: string | null;
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

export interface BatchUploadResult {
  items: UploadResult[];
  failed_items: BatchUploadFailedItem[];
  total_count: number;
  uploaded_count: number;
  failed_count: number;
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
  parser_unstructured_enabled: boolean | null;
  parser_mineru_enabled: boolean | null;
  parser_dots_ocr_enabled: boolean | null;
  /** 図・画像を AI で読み取る(Vision)。解析エンジンに関係なく使える(#497)。 */
  vision_enabled: boolean | null;
  chunking_strategy: ChunkingStrategyName | null;
  chunk_size: number | null;
  chunk_overlap: number | null;
  chunk_min_chars: number | null;
  /** DocRAG 親子階層の分割パラメータ(分割方式が docrag_small_to_big のときだけ効く)。 */
  docrag_child_target_chars?: number | null;
  docrag_table_child_target_chars?: number | null;
  docrag_parent_target_chars?: number | null;
  docrag_parent_max_pages?: number | null;
  docrag_parent_max_children?: number | null;
  graph_profile: GraphProfileName | null;
  field_extraction_enabled: boolean | null;
  navigation_summary_enabled: boolean | null;
  auto_parse_after_preprocess_enabled: boolean | null;
  auto_chunk_after_extract_enabled: boolean | null;
  auto_index_after_chunk_enabled: boolean | null;
}

/** 検索・回答設定。Business View の query 設定として使う。 */
export interface KnowledgeBaseQueryConfig {
  retrieval_strategy: RetrievalStrategyName | null;
  /** 検索方法の合成トグル(null はグローバル継承)。 */
  retrieval_query_expansion: boolean | null;
  retrieval_query_expansion_llm: boolean | null;
  retrieval_gap_stop: boolean | null;
  retrieval_corrective: boolean | null;
  retrieval_business_fit_weighting: boolean | null;
  post_retrieval_pipeline: PostRetrievalPipelineName | null;
  generation_profile: GenerationProfileName | null;
  guardrail_policy: GuardrailPolicyName | null;
  // 回答エンジンの選択(answer_engine)は #594 で削除した(回答は DocRAG だけ)。
  /** DocRAG 回答フローの設定。null / 未指定はグローバル継承。 */
  docrag_query_strategy?: DocragQueryStrategyName | null;
  docrag_answer_flow?: DocragAnswerFlowName | null;
  docrag_neighbor_child_count?: number | null;
  docrag_rerank_enabled?: boolean | null;
  // 画面目録で操作画面を探す(LLM の呼び出しが 1 回増える。#554)。
  docrag_screen_linking_enabled?: boolean | null;
}

export type DocragQueryStrategyName =
  | "auto_routing"
  | "simple_retrieval"
  | "rag_fusion"
  | "query_decomposition"
  | "step_back_prompting"
  | "hyde";

export type DocragAnswerFlowName = "crag" | "standard_rag";


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

export const DEFAULT_KNOWLEDGE_BASE_NAME = "DEFAULT";

export interface KnowledgeBaseGraphNode {
  id: string;
  name: string;
  type: string | null;
  confidence: number;
}

export interface KnowledgeBaseGraphEdge {
  id: string;
  source: string;
  target: string;
  type: string | null;
  confidence: number;
}

export interface KnowledgeBaseGraphData {
  status: "ok" | "empty";
  nodes: KnowledgeBaseGraphNode[];
  edges: KnowledgeBaseGraphEdge[];
  truncated: boolean;
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

export type BusinessViewStatus = "ACTIVE" | "ARCHIVED";

export const DEFAULT_BUSINESS_VIEW_NAME = "DEFAULT";

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
  /** 参照 KB のうちアーカイブ済みの件数（検索対象にならない。#302）。 */
  archived_knowledge_base_count?: number;
  /** 参照 KB のうち存在しない件数（検索対象にならない。#302）。 */
  missing_knowledge_base_count?: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

/** 業務ビューが参照する KB（アーカイブ済みを含む。status で見分ける）。 */
export interface BusinessViewKnowledgeBaseRef extends KnowledgeBaseRef {
  status: KnowledgeBaseStatus;
}

export interface BusinessViewDetail extends BusinessViewSummary {
  config: BusinessViewConfig;
  knowledge_bases: BusinessViewKnowledgeBaseRef[];
  /** 参照 KB のうち存在しない ID（#302）。 */
  missing_knowledge_base_ids?: string[];
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

/** 文書の取込設定スナップショット(3 層モデル: 文書単位の単一レシピ)と global 既定とのドリフト状況。 */
/** 処理設定パネルに渡すレシピの設定（保存した上書きと、global 既定を重ねた有効値）。 */
export interface DocumentProcessingConfigData {
  processing_config: DocumentProcessingConfig;
  effective_processing_config: DocumentProcessingConfig;
}

export interface DocumentProcessingConfig extends KnowledgeBaseIngestionConfig {
  chunk_context_header_enabled: boolean | null;
}

export type DocumentRecipeStepStatus =
  | "PENDING"
  | "QUEUED"
  | "RUNNING"
  | "NEEDS_REVIEW"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export interface DocumentRecipeStep {
  phase: IngestionJobPhase;
  status: DocumentRecipeStepStatus;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
}

export interface DocumentRecipeView {
  recipe_id: string;
  document_id: string;
  slot_no: 1 | 2 | 3;
  status: FileStatus;
  failed_phase: IngestionJobPhase | null;
  processing_config: DocumentProcessingConfig;
  effective_processing_config: DocumentProcessingConfig;
  preprocess_artifact: DocumentPreprocessArtifact | null;
  active_extraction_recipe_id: string | null;
  active_chunk_set_id: string | null;
  chunk_count: number;
  vector_count: number;
  config_revision: number;
  materialized_revision: number | null;
  searchable: boolean;
  needs_reprocessing: boolean;
  error_message: string | null;
  steps: DocumentRecipeStep[];
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface DocumentRecipeDeleteResult {
  recipe_id: string;
  document_id: string;
  removed_chunk_set_count: number;
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
  business_view_ids?: string[];
  generation_profile?: GenerationProfileName | null;
  /** 回答を作らずに検索だけを行う(LLM を呼ばない。#593)。 */
  retrieval_only?: boolean;
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

// --- チャット（会話 / マルチモデル比較）---
export type ConversationStatus = "ACTIVE" | "ARCHIVED";
export type MessageRole = "USER" | "ASSISTANT" | "SYSTEM";
export type MessageStatus = "STREAMING" | "COMPLETE" | "ERROR";

export interface ChatMessage {
  message_id: string;
  conversation_id: string;
  role: MessageRole;
  content: string;
  model: string | null;
  citations: RetrievedChunk[];
  guardrail_warnings: string[];
  trace_id: string | null;
  status: MessageStatus;
  reply_to_message_id: string | null;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  business_view_id: string;
  title: string | null;
  status: ConversationStatus;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
}

export interface ConversationCreateBody {
  business_view_id: string;
  title?: string | null;
}

export interface ConversationUpdateBody {
  title: string;
}

export interface ChatMessageRequestBody {
  content: string;
  model_ids?: string[];
  mode?: SearchMode;
  top_k?: number;
}

export interface CompareModel {
  model_id: string;
  display_name: string;
}

export interface SearchDiagnostics {
  mode: string;
  retrieval_strategy_adapter?: string;
  retrieval_strategy: string;
  generation_profile?: GenerationProfileName | string;
  generation_config_source?: "request" | "business_view" | "global";
  generation_contract_mode?:
    "groundedness" | "format_validated" | "json_schema" | "custom";
  generation_attempt_count?: number;
  generation_repair_count?: number;
  generation_validation_codes?: string[];
  custom_prompt_version_id?: string | null;
  guardrail_backend?: GuardrailBackend;
  guardrail_degraded?: boolean;
  route_reason: string;
  keyword_terms: string[];
  retrieval_breakdown: SearchRetrievalBreakdown;
  retrieval_candidates: SearchRetrievalCandidate[];
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
  /** DocRAG 回答エンジンの記録(standard では null)。 */
  docrag?: Record<string, JsonValue> | null;
}

export interface SearchRetrievalBreakdown {
  vector_count: number;
  keyword_count: number;
  overlap_count: number;
  fused_count: number;
  fusion_dropped_count: number;
  rerank_input_count: number;
  rerank_kept_count: number;
  rerank_dropped_count: number;
  evidence_count: number;
  citation_count: number;
  dropped_count: number;
}

export interface SearchRetrievalCandidate {
  chunk_id: string;
  document_id: string;
  text?: string;
  file_name: string | null;
  sources: string[];
  vector_rank: number | null;
  vector_score: number | null;
  keyword_rank: number | null;
  keyword_score: number | null;
  rrf_score: number | null;
  rerank_rank: number | null;
  rerank_score: number | null;
  status: string;
  drop_reason: string | null;
}

export interface SearchResponse {
  answer: string;
  /** 安全チェックが回答を差し替えたか（backend の `answer_replaced`）。 */
  answer_replaced?: boolean;
  citations: RetrievedChunk[];
  trace_id: string;
  guardrail_warnings: string[];
  elapsed_ms: number;
  diagnostics: SearchDiagnostics;
}

export interface FeedbackRequestBody {
  trace_id: string;
  business_view_id: string;
  target_type: FeedbackTargetType;
  source_surface: FeedbackSourceSurface;
  document_id?: string | null;
  chunk_id?: string | null;
  message_id?: string | null;
  content_snapshot?: FeedbackContentSnapshot | null;
  rating: CitationFeedbackRating;
  reason?: CitationFeedbackReason | null;
  comment?: string | null;
  /** 修正した回答(回答を「役に立たなかった」と評価したときだけ)。 */
  corrected_answer?: string | null;
}

/** 応答は画面の snapshot を返さない（backend が除外する）。 */
export interface FeedbackSubmissionResponse extends Omit<FeedbackRequestBody, "content_snapshot"> {
  feedback_id: string;
}

export interface CurrentFeedbackItem extends Omit<
  FeedbackSubmissionResponse,
  "business_view_id" | "source_surface"
> {
  business_view_id: string | null;
  source_surface: FeedbackSourceSurface | null;
  created_at: string;
}

export interface FeedbackCitationSnapshot {
  document_id: string;
  chunk_id: string;
  file_name: string | null;
  section_title: string | null;
  page_number: number | null;
  content_preview: string | null;
  rerank_score: number | null;
}

export interface FeedbackContentSnapshot {
  question: string;
  answer: string;
  citations: FeedbackCitationSnapshot[];
}

export interface FeedbackReasonCount {
  reason: CitationFeedbackReason;
  count: number;
}

export interface FeedbackSummary {
  total: number;
  helpful_count: number;
  not_helpful_count: number;
  helpful_rate: number;
  answer_total: number;
  answer_helpful_rate: number;
  citation_total: number;
  citation_helpful_rate: number;
  reason_counts: FeedbackReasonCount[];
}

export interface FeedbackItem extends CurrentFeedbackItem {
  business_view_name: string | null;
  conversation_id: string | null;
  conversation_title: string | null;
  message_id: string | null;
  model: string | null;
  file_name: string | null;
  question_preview: string | null;
  comment_preview: string | null;
  has_comment: boolean;
}

export interface FeedbackExecutionInfo {
  outcome: string | null;
  search_mode: string | null;
  elapsed_ms: number | null;
  retrieved_count: number | null;
  reranked_count: number | null;
  citation_count: number | null;
  guardrail_codes: string[];
  config_fingerprint: string | null;
}

export interface FeedbackDetail extends FeedbackItem {
  content_source: "chat_message" | "search_snapshot" | null;
  question: string | null;
  answer: string | null;
  comment: string | null;
  citations: FeedbackCitationSnapshot[];
  execution: FeedbackExecutionInfo;
}

/** 質問履歴の設定(rag_poc の QUERY_HISTORY_*)。 */
export interface QueryHistorySettingsData {
  enabled: boolean;
  retention_days: number;
  min_count: number;
  suggestion_limit: number;
  blocklist: string[];
}

export interface QuerySuggestionsData {
  business_view_id: string;
  enabled: boolean;
  suggestions: { question: string; count: number }[];
}

export type DocragPromptKey = "vlm_answer" | "image_retrieval";

/** 編集できる DocRAG プロンプト(rag_poc の vlm_answer.txt / image_retrieval.txt)。 */
export interface DocragPromptView {
  key: DocragPromptKey;
  content: string;
  default_content: string;
  customized: boolean;
  required_placeholders: string[];
  updated_at: string | null;
}

export interface DocragPromptsData {
  prompts: DocragPromptView[];
  /** 回答フローの各段の読み取り専用プロンプト(コードで管理)。 */
  stages: { id: string; prompts: { id: string; content: string }[] }[];
}

export interface FeedbackApprovedFaqPromotion {
  business_view_id: string;
  question: string;
  inserted_count: number;
  deleted_count: number;
}

export interface FeedbackDashboard {
  summary: FeedbackSummary;
  previous_summary: FeedbackSummary | null;
  items: Page<FeedbackItem>;
}

export interface FeedbackListParams {
  business_view_id?: string;
  target_type?: FeedbackTargetType;
  rating?: CitationFeedbackRating;
  reason?: CitationFeedbackReason;
  period_days?: number | null;
  q?: string;
  sort_order?: "newest" | "oldest";
  limit?: number;
  offset?: number;
}

// --- 評価 ---
export interface EvaluationCase {
  id: string;
  query: string;
  relevant_document_ids?: string[];
  expected_answer_keywords?: string[];
  /** 標準回答。あるケースは回答を標準回答と LLM で比較する（#591）。 */
  standard_answer?: string | null;
  /** false は答えるべきでない質問。省略時は期待値（文書・語・標準回答）の有無で決まる。 */
  answerable?: boolean | null;
}

export type EvaluationThresholds = Partial<Record<EvaluationMetricName, number | null>>;

export interface EvaluationRunRequestBody {
  cases: EvaluationCase[];
  top_k?: number;
  filters?: Record<string, string>;
  knowledge_base_ids?: string[];
  thresholds?: EvaluationThresholds | null;
  suite?: EvaluationSuiteName | null;
  rag_overrides?: EvaluationRagOverrides | null;
}

/** 標準回答による LLM の評価の要約（詳細は回答の記録）。 */
export interface EvaluationAnswerJudgement {
  /** completed / error / input_too_large / timeout / unavailable */
  status: string;
  total_score: number | null;
  max_score: number;
  passed: boolean | null;
  claims_supported: boolean | null;
  requirement_coverage: number | null;
  missing_content: boolean | null;
  goal_alignment: string | null;
  message: string | null;
}

/** 1 ケースの結果。測れない指標は null（保存済みの古い結果では欄が無いことがある）。 */
export interface EvaluationCaseResult {
  case_id: string;
  trace_id: string;
  status: "success" | "error";
  retrieved_document_ids: string[];
  relevant_document_ids: string[];
  hit_document_ids: string[];
  context_recall?: number | null;
  reciprocal_rank?: number | null;
  faithfulness?: number | null;
  citation_traceability_coverage?: number | null;
  answer_keyword_hit?: boolean | null;
  abstained?: boolean | null;
  refusal_correct?: boolean | null;
  answer_evaluation?: EvaluationAnswerJudgement | null;
  guardrail_warnings: string[];
  failure_reasons: string[];
  diagnostics: SearchDiagnostics;
  elapsed_ms: number;
  error_type: string | null;
  /** 時間切れになった工程（進捗の stage と同じ名前）。時間切れ以外は null（#383）。 */
  error_stage?: string | null;
  error_message: string | null;
}

export interface EvaluationThresholdFailure {
  /** 保存済みの結果には削除した指標の名前が残ることがある。 */
  metric: string;
  actual: number;
  threshold: number;
}

/** 評価結果。各指標は測れたケースだけの平均で、測れなかった指標は null。 */
export type EvaluationMetrics = Partial<Record<EvaluationMetricName, number | null>> & {
  case_count: number;
  error_count: number;
  /** 保存済みの結果には削除した基準の名前が残ることがある。 */
  evaluation_suite: string;
  metric_case_counts?: Partial<Record<string, number>>;
  passed: boolean;
  threshold_failures: EvaluationThresholdFailure[];
  failure_reason_counts: Partial<Record<string, number>>;
  case_results: EvaluationCaseResult[];
};

/** experiment ごとに一時適用する回答設定（#591）。 */
export interface EvaluationRagOverrides {
  query_strategy?: DocragQueryStrategyName | null;
  answer_flow?: DocragAnswerFlowName | null;
  neighbor_child_count?: number | null;
  rerank_enabled?: boolean | null;
  rrf_k?: number | null;
  context_group_max_chunks?: number | null;
  oracle_vector_target_accuracy?: number | null;
}

export interface EvaluationExperiment {
  id: string;
  top_k?: number;
  filters?: Record<string, string>;
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
  /** 順位の指標を測れなかったときは null。 */
  ranking_score: number | null;
  experiment: EvaluationExperiment;
  metrics: EvaluationMetrics;
}

export interface EvaluationCompareResponse {
  /** 保存済みの結果には削除した指標の名前が残ることがある。 */
  ranking_metric: string;
  best_experiment_id: string | null;
  results: EvaluationExperimentResult[];
}

export type EvaluationJobKind = "run" | "compare";
export type EvaluationJobStatus = "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

/**
 * 品質評価の job（#390）。進捗は終わったケースの数（比較は experiment × ケースの通しの数）と
 * 今のケース。結果は成功のときだけ（評価は `run_result`、比較は `compare_result`）。
 */
export interface EvaluationJob {
  job_id: string;
  kind: EvaluationJobKind;
  status: EvaluationJobStatus;
  total_cases: number;
  completed_cases: number;
  current_case_id: string | null;
  current_experiment_id: string | null;
  current_case_started_at: string | null;
  time_limit_seconds: number;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  run_result: EvaluationMetrics | null;
  compare_result: EvaluationCompareResponse | null;
}

// --- 設定: モデル ---
// --- 設定: データベース ---

export type SystemTableSchemaStatus =
  "missing" | "partial" | "outdated" | "ready";
export type SystemTableOperationStatus = "idle" | "running" | "failed";
export type SystemTableOperationKind = "initialize" | "recreate";
export type SystemTableOperationResult =
  "no_op" | "initialized" | "migrated" | "recreated";

export interface SystemTableObjectData {
  name: string;
  object_type: string;
}

export interface SystemTableMetadata {
  name: string;
  exists: boolean;
  estimated_rows: number | null;
  created_at: string | null;
  last_analyzed_at: string | null;
}

export interface SystemTableOperationState {
  status: SystemTableOperationStatus;
  operation_kind: SystemTableOperationKind | null;
  lease_expires_at: string | null;
  last_error_code: string | null;
  schema_epoch: number;
  updated_at: string | null;
}

export interface SystemTablesStatusData {
  status: SystemTableSchemaStatus;
  schema_version: string;
  schema_head: string;
  applied_versions: string[];
  pending_versions: string[];
  expected_object_count: number;
  existing_object_count: number;
  expected_table_count: number;
  existing_table_count: number;
  missing_objects: SystemTableObjectData[];
  retired_objects: SystemTableObjectData[];
  // 外部キーの差分（不足・参照先のない行は #505、削除規則の違い・無効化は #511）。
  missing_foreign_keys?: SystemTableForeignKeyData[];
  orphaned_foreign_keys?: SystemTableForeignKeyData[];
  mismatched_foreign_keys?: SystemTableForeignKeyData[];
  disabled_foreign_keys?: SystemTableForeignKeyData[];
  tables: SystemTableMetadata[];
  operation_state: SystemTableOperationState;
}

export interface SystemTableForeignKeyData {
  name: string;
  table_name: string;
  columns: string[];
  referenced_table_name: string;
  referenced_columns: string[];
  delete_rule: string;
  orphan_rows: number | null;
  current_name?: string | null;
  current_delete_rule?: string | null;
}

export interface SystemTablesInitializeRequest {
  recreate: boolean;
  confirmation?: string;
}

export interface SystemTablesOperationData extends SystemTablesStatusData {
  operation: SystemTableOperationResult;
  dropped_object_count: number;
  created_object_count: number;
}

/** 参照先のない行の削除（#511）。`expected_orphan_rows` は利用者が確認した件数。 */
export interface SystemTablesDeleteOrphansRequest {
  constraint_name: string;
  expected_orphan_rows: number;
}

export interface SystemTablesOrphanDeletionData extends SystemTablesStatusData {
  operation: "no_op" | "orphans_deleted";
  deleted_row_count: number;
  foreign_key: SystemTableForeignKeyData;
}

// --- 設定: HuggingFace モデルダウンロード ---
export interface HuggingFaceSettingsData {
  endpoint: string;
  token_configured: boolean;
  config_source: "runtime";
}

export interface HuggingFaceSettingsUpdate {
  endpoint: string;
  token?: string;
  clear_token?: boolean;
}

// --- 設定: アップロード保存先 ---

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

export interface ParserServiceBackendData {
  backend: ParserServiceBackendName;
  selected: boolean;
  configured: boolean;
  warning_code: string | null;
}

export interface ParserBackendCapabilityData {
  backend: string;
  modalities: string[];
  extensions: string[];
}

export interface ExternalParserConnectionData {
  backend: ExternalParserBackendName;
  protocol: ExternalParserProtocol;
  endpoint: string;
  model: string | null;
  api_key_configured: boolean;
  configured: boolean;
}

export interface ExternalParserConnectionUpdate {
  backend: ExternalParserBackendName;
  endpoint?: string;
  model?: string;
  api_key?: string;
  clear_api_key?: boolean;
}

export interface ExternalParserConnectionStatusData {
  backend: ExternalParserBackendName;
  status: ExternalParserConnectionStatus;
  version: string | null;
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
  capabilities: ParserBackendCapabilityData[];
  connections: ExternalParserConnectionData[];
  /** 「解析後の処理」の全体の既定（#528）。保存先は backend/.env。 */
  vision_enabled: boolean;
  field_extraction_enabled: boolean;
  navigation_summary_enabled: boolean;
  config_source: "runtime";
}

/** `adapter_backend` を省略すると解析エンジンの設定は変えない（「解析後の処理」だけの保存。#528）。 */
export interface ParserAdapterSettingsUpdate {
  adapter_backend?: ParserAdapterBackend;
  docling_enabled?: boolean;
  unstructured_enabled?: boolean;
  mineru_enabled?: boolean;
  dots_ocr_enabled?: boolean;
  connections?: ExternalParserConnectionUpdate[];
  vision_enabled?: boolean;
  field_extraction_enabled?: boolean;
  navigation_summary_enabled?: boolean;
}

/** 設定の概要: 工程の自動進行と、レシピ 11 項目の全体の既定（#528）。 */
export interface PipelineSettingsData {
  auto_parse_after_preprocess_enabled: boolean;
  auto_chunk_after_extract_enabled: boolean;
  auto_index_after_chunk_enabled: boolean;
  /** レシピで何も上書きしないときの実効値（レシピの「グローバル設定に従う」と同じ）。 */
  recipe_defaults: DocumentProcessingConfig;
  config_source: "runtime";
}

export type PipelineAutoAdvanceField =
  | "auto_parse_after_preprocess_enabled"
  | "auto_chunk_after_extract_enabled"
  | "auto_index_after_chunk_enabled";

export type PipelineSettingsUpdate = Partial<Record<PipelineAutoAdvanceField, boolean>>;

// --- 設定: Chunking アダプター ---
export type ChunkingStrategyName =
  | "structure_aware"
  | "recursive_character"
  | "docrag_small_to_big"
  | "markdown_heading"
  | "page_level"
  | "fixed_size"
  | "fixed_delimiter";

// --- 設定: 前処理(Preprocess)アダプター ---
export type PreprocessProfileName =
  | "passthrough"
  | "office_to_pdf"
  | "pdf_to_page_images"
  | "csv_to_json"
  | "excel_to_json"
  | "url_to_markdown"
  | "image_enhance"
  | "pii_redact";

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
// systemd の unit の状態 + /health（#286）。starting は unit が動いているが /health にまだ届かない、
// failed は unit が失敗して止まった、not_installed は unit が登録されていない。
export type ServiceRuntimeStatus =
  | "running"
  | "degraded"
  | "starting"
  | "failed"
  | "stopped"
  | "not_installed"
  | "unconfigured"
  | "in_process";
export type ServiceExecutionPolicy =
  "required_no_fallback" | "in_process_when_disabled" | "selected_adapter";
// 起動 = systemctl enable --now、停止 = systemctl disable --now、再起動 = systemctl restart。
export type ServiceAction = "start" | "stop" | "restart";

export interface ServiceModelCacheData {
  /** サービスの実行ユーザーのキャッシュ親ディレクトリ（~/.cache）。 */
  path: string;
  editable: false;
}

export interface ServiceCatalogItemData {
  service_id: string;
  category: ServiceCategory;
  profile: ServiceProfile;
  label_key: string;
  execution_policy: ServiceExecutionPolicy;
  deployable: boolean;
  configured: boolean;
  /** 起動/停止・ログに使う systemd の unit 名。backend 内処理のステージは null。 */
  systemd_unit: string | null;
  model_cache: ServiceModelCacheData | null;
}

export interface ServiceStatusData extends ServiceCatalogItemData {
  status: ServiceRuntimeStatus;
}

export type DeploymentMode = "dev" | "prod";

export interface ServiceCatalogData {
  control_enabled: boolean;
  deployment_mode: DeploymentMode;
  services: ServiceCatalogItemData[];
}

export interface ServiceControlResultData {
  service_id: string;
  action: ServiceAction;
  status: ServiceRuntimeStatus;
}

export type ServiceLogsSource = "journald";

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
}

export interface ChunkingSettingsData {
  strategy: ChunkingStrategyName;
  chunk_size: number;
  overlap: number;
  min_chars: number;
  delimiter: string;
  context_header_enabled: boolean;
  /** DocRAG 親子階層の分割パラメータ(rag_poc と同じ 5 項目)。 */
  docrag_child_target_chars: number;
  docrag_table_child_target_chars: number;
  docrag_parent_target_chars: number;
  docrag_parent_max_pages: number;
  docrag_parent_max_children: number;
  strategies: ChunkingStrategyStatusData[];
  config_source: "runtime";
}

export interface ChunkingSettingsUpdate {
  strategy: ChunkingStrategyName;
  chunk_size: number;
  overlap: number;
  min_chars: number;
  delimiter: string;
  context_header_enabled: boolean;
  /** DocRAG 親子階層の分割パラメータ(rag_poc と同じ 5 項目)。 */
  docrag_child_target_chars: number;
  docrag_table_child_target_chars: number;
  docrag_parent_target_chars: number;
  docrag_parent_max_pages: number;
  docrag_parent_max_children: number;
}

// --- 設定: Retrieval アダプター ---
/** 検索モード(新形式・排他選択)。 */
export type RetrievalModeName =
  | "hybrid_rrf"
  | "vector"
  | "keyword"
  | "graph_augmented"
  | "reasoning_tree_search";

/** legacy 複合値込みの読み取り互換型。保存は RetrievalModeName のみ。 */
export type RetrievalStrategyName =
  RetrievalModeName | "business_context_strict" | "corrective_multi_query";

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
  mode: RetrievalModeName;
  legacy_strategy: RetrievalStrategyName | null;
  query_expansion: boolean;
  query_expansion_llm: boolean;
  gap_stop: boolean;
  corrective_retrieval: boolean;
  business_fit_weighting: boolean;
  modes: RetrievalStrategyStatusData[];
  config_source: "runtime";
}

/** 部分更新。null/undefined のフィールドは変更しない。 */
export interface RetrievalSettingsUpdate {
  mode?: RetrievalModeName;
  query_expansion?: boolean;
  query_expansion_llm?: boolean;
  gap_stop?: boolean;
  corrective_retrieval?: boolean;
  business_fit_weighting?: boolean;
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
  corrective: boolean;
}

export interface GroundingSettingsData {
  pipeline: PostRetrievalPipelineName;
  dependency_promotion_enabled: boolean;
  diversity_enabled: boolean;
  expansion_mode: GroundingExpansionMode;
  compression_enabled: boolean;
  /** CRAG(補正検索)の evidence grade 判定パラメータ。 */
  crag_low_confidence_threshold: number;
  crag_high_confidence_threshold: number;
  crag_max_hops: number;
  crag_low_evidence_abstain: boolean;
  pipelines: GroundingPipelineStatusData[];
  config_source: "runtime";
}

/** 部分更新。undefined のフィールドは変更しない。 */
export interface GroundingSettingsUpdate {
  pipeline?: PostRetrievalPipelineName;
  crag_low_confidence_threshold?: number;
  crag_high_confidence_threshold?: number;
  crag_max_hops?: number;
  crag_low_evidence_abstain?: boolean;
}

// --- 設定: Generation アダプター ---
export type GenerationProfileName =
  | "grounded_concise"
  | "detailed_cited"
  | "strict_extractive"
  | "structured_json"
  | "bilingual_ja_en"
  | "inline_cited"
  | "custom";

export interface GenerationProfileStatusData {
  name: GenerationProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  structured_output: boolean;
  contract_mode: "groundedness" | "format_validated" | "json_schema" | "custom";
  repair_enabled: boolean;
}

export interface GenerationSettingsData {
  profile: GenerationProfileName;
  structured_output: boolean;
  profiles: GenerationProfileStatusData[];
  config_source: "oracle";
  revision: number;
  updated_at: string;
  active_prompt_version_id: string | null;
  custom_prompt_configured: boolean;
}

/** 回答の検索と生成の全体既定(業務ビューで上書きできる。#593)。 */
export interface AnsweringSettingsData {
  query_strategy: DocragQueryStrategyName;
  answer_flow: DocragAnswerFlowName;
  neighbor_child_count: number;
  rerank_enabled: boolean;
  screen_linking_enabled: boolean;
  config_source: "runtime";
}

export type AnsweringSettingsUpdate = Partial<Omit<AnsweringSettingsData, "config_source">>;

/** 回答の記録の保持日数(0 は無期限)。 */
export interface AnswerRecordSettingsData {
  retention_days: number;
  config_source: "runtime";
}

export interface GenerationSettingsUpdate {
  profile: GenerationProfileName;
  expected_revision?: number;
}

// --- 設定: 回答プロンプト版(custom 回答スタイルが使用) ---
export interface PromptVersionData {
  version_id: string;
  name: string;
  system_prompt: string;
  note: string;
  created_at: string;
  created_by: string;
  active: boolean;
}

export interface PromptVersionsData {
  active_version_id: string | null;
  versions: PromptVersionData[];
  settings_revision: number;
}

export interface PromptVersionCreate {
  name: string;
  system_prompt: string;
  note?: string;
  activate?: boolean;
}

// --- 設定: Guardrail アダプター ---
export type GuardrailPolicyName =
  "standard" | "strict" | "lenient" | "regulated";

export interface GuardrailPolicyStatusData {
  name: GuardrailPolicyName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  grounding_min_overlap: number;
  grounding_min_ratio: number;
  audit_emphasis: boolean;
}

/** メタデータ/項目抽出のスキーマ定義(検索・回答設定)。 */
export type ExtractionFieldValueType = "string" | "number" | "date" | "bool";

export interface ExtractionFieldDefinition {
  name: string;
  description: string;
  value_type: ExtractionFieldValueType;
}

/** 全体の既定の項目の定義。uses_standard なら一度も保存しておらず、fields は標準の項目（#556）。 */
export interface ExtractionFieldsSettingsData {
  fields: ExtractionFieldDefinition[];
  uses_standard: boolean;
}

/** 検索の絞り込みに使える項目（#549）。 */
export interface SearchExtractionFieldsData {
  fields: ExtractionFieldDefinition[];
}

/** ナレッジベースの項目抽出の定義（#548）。inherits_default なら fields は全体の既定。 */
export interface KnowledgeBaseExtractionFieldsData {
  inherits_default: boolean;
  fields: ExtractionFieldDefinition[];
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
  backend: GuardrailBackend;
  oci_configured: boolean;
  oci_warning_code: string | null;
  config_source: "runtime";
}

export interface GuardrailSettingsUpdate {
  policy: GuardrailPolicyName;
  backend?: GuardrailBackend;
}

export type GuardrailBackend = "local" | "oci_guardrails";

// --- 設定: Vector Index アダプター ---
export type VectorIndexProfileName = "balanced" | "accurate" | "fast";
/** 実際の索引と推奨ビルドの比較結果(backend の判定。#562)。unknown = 実際の値を確認できない。 */
export type VectorIndexBuildStatus = "match" | "reprovision" | "unknown";

export interface VectorIndexProfileStatusData {
  name: VectorIndexProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  target_accuracy: number;
  neighbors: number;
  efconstruction: number;
  distance: string;
  index_status: VectorIndexBuildStatus;
}

export interface VectorIndexSettingsData {
  profile: VectorIndexProfileName;
  target_accuracy: number;
  neighbors: number;
  efconstruction: number;
  distance: string;
  requires_reprovision: boolean;
  index_status: VectorIndexBuildStatus;
  /** 実際の索引の値。確認できないときは null。 */
  actual_neighbors: number | null;
  actual_efconstruction: number | null;
  profiles: VectorIndexProfileStatusData[];
  reindex_sql: string;
  config_source: "runtime";
}

export interface VectorIndexSettingsUpdate {
  profile: VectorIndexProfileName;
}

// --- 設定: Evaluation アダプター ---
/** 評価の基準（閾値のプリセット。#591）。 */
export type EvaluationSuiteName = "standard" | "strict";

export interface EvaluationSuiteStatusData {
  name: EvaluationSuiteName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  thresholds: Record<string, number>;
}

export interface EvaluationSettingsData {
  suite: EvaluationSuiteName;
  thresholds: Record<string, number>;
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
export type AgenticProfileName =
  | "off"
  | "smart_routing"
  | "query_rewrite"
  | "hyde"
  | "decompose"
  | "multi_hop";

export interface AgenticProfileStatusData {
  name: AgenticProfileName;
  origin: string;
  recommended_for: string[];
  selected: boolean;
  enabled: boolean;
  rewrite: boolean;
  decompose: boolean;
  multi_hop: boolean;
  hyde: boolean;
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
  max_subqueries: number;
}

// --- 設定: OCI config ---

/** API 由来のエラー。`messages` は日本語のユーザー向け文言。 */
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
  /** 応答に理由がなく、既定の文言（「APIエラー (状態コード)」）にしたか。文言の一致で判定しない。 */
  readonly isFallbackMessage: boolean;

  constructor(status: number, messages: string[], details: ApiErrorDetails = {}) {
    const isFallbackMessage = messages.length === 0;
    const resolved = isFallbackMessage
      ? [t("common.apiError", { status })]
      : messages;
    super(resolved[0]);
    this.name = "ApiError";
    this.status = status;
    this.messages = resolved;
    this.isFallbackMessage = isFallbackMessage;
    this.errorCode = details.errorCode;
    this.fieldErrors = details.fieldErrors ?? [];
    this.requestId = details.requestId;
  }
}

interface ErrorEnvelope {
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

/** エラー応答（ApiResponse envelope）から ApiError を作る。stream の直接 fetch も使う。 */
export function apiErrorFromEnvelope(
  status: number,
  envelope: unknown,
  requestId?: string | null,
): ApiError {
  const body = (envelope && typeof envelope === "object" ? envelope : {}) as ErrorEnvelope;
  const messages = Array.isArray(body.error_messages)
    ? body.error_messages.filter((item): item is string => typeof item === "string")
    : [];
  const problemRequestId =
    typeof body.problem?.request_id === "string" ? body.problem.request_id : undefined;
  return new ApiError(status, messages, {
    errorCode: typeof body.error_code === "string" ? body.error_code : undefined,
    fieldErrors: fieldErrorsOf(body.problem?.field_errors),
    requestId: requestId || problemRequestId,
  });
}

/** 状態を変える method のとき Cookie の CSRF token を `X-CSRF-Token` として付けた headers を作る。 */
export function withCsrfHeaders(method: string | undefined, headers?: HeadersInit): Headers {
  const merged = new Headers(headers);
  for (const [name, value] of Object.entries(csrfHeader(CSRF_COOKIE_NAME, method ?? "GET"))) {
    merged.set(name, value);
  }
  return merged;
}

/**
 * 応答の 401 / 403 を共通の認証イベント（ログインへ / 権限なしの画面へ）として通知する。
 * 403 は error_code が経路の権限拒否のときだけ権限なしの画面へ移し、業務ビュー / KB の範囲外
 * （`RAG_SCOPE_FORBIDDEN`）や権限の付与の制限などは呼び出した画面がその場で表示する（#224）。
 * 本文を読む前に呼ぶ（本文は消費しない）。
 */
export function notifyResponseAuthStatus(res: Response): void {
  void notifyAuthResponse(res);
}

function resolveTimeoutMs(value: unknown, fallbackMs: number): number {
  const parsed = typeof value === "string" ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallbackMs;
}

function runtimeApiTimeoutOverrideMs(): number | null {
  if (typeof window === "undefined") return null;
  const value = Number(
    (window as unknown as { __RAG_API_TIMEOUT_MS__?: string | number })
      .__RAG_API_TIMEOUT_MS__,
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

interface RequestOptions {
  timeoutMs?: number;
}

/**
 * ApiResponse エンベロープを取得し、エラー時は ApiError を投げる。
 * Cookie セッションの CSRF header の付与と、401 / 403 の認証イベントの通知もここで行う（#214）。
 */
async function requestEnvelope<T>(
  path: string,
  init?: RequestInit,
  options: RequestOptions = {},
): Promise<ApiResponse<T>> {
  const timeoutMs =
    runtimeApiTimeoutOverrideMs() ??
    options.timeoutMs ??
    API_REQUEST_TIMEOUT_MS;
  const controller = new AbortController();
  const externalSignal = init?.signal;
  let timedOut = false;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;

  const abortFromExternal = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) {
    abortFromExternal();
  } else {
    externalSignal?.addEventListener("abort", abortFromExternal, {
      once: true,
    });
  }

  if (timeoutMs > 0) {
    timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
  }

  const headers = withCsrfHeaders(init?.method, init?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  try {
    const res = await fetch(path, {
      ...init,
      credentials: "same-origin",
      signal: controller.signal,
      headers,
    });
    const envelope = await parseEnvelope<T>(res);
    if (!res.ok) {
      const error = apiErrorFromEnvelope(res.status, envelope, res.headers.get("X-Request-ID"));
      // 本文は読み終えているので、読み取った error_code で通知する（#224）。
      notifyAuthStatus(res.status, error.requestId, error.errorCode);
      throw error;
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
export async function request<T>(
  path: string,
  init?: RequestInit,
  options: RequestOptions = {},
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
  options: RequestOptions = {},
): Promise<Degradable<T>> {
  const envelope = await requestEnvelope<T>(path, init, options);
  return {
    ...(envelope.data as T),
    warning_messages: envelope.warning_messages ?? [],
  };
}

/** アップロードの送信済みバイト数（multipart の本文全体に対する値）。 */
export interface UploadTransferProgress {
  loaded: number;
  total: number;
}

/**
 * 文書アップロード用の multipart 送信（#306）。
 *
 * `fetch` では送信（request body）の進み具合を取れないため、XHR の `upload.onprogress` で送信済みの
 * バイト数を通知する。CSRF header・401 / 403 の認証イベント・エラーの ApiError 化は `requestEnvelope` と
 * 同じにする。大きなファイルの送信・保存は既定の 30 秒を超えるため、時間では打ち切らない（#280。途中で
 * 打ち切ると、backend では保存済みなのに画面は失敗と表示し、再送で同じ文書が二重に登録される）。
 */
function requestUpload<T>(
  path: string,
  body: FormData,
  onUploadProgress?: (progress: UploadTransferProgress) => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    const headers = withCsrfHeaders("POST");
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    headers.forEach((value, name) => xhr.setRequestHeader(name, value));
    if (onUploadProgress) {
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable && event.total > 0) {
          onUploadProgress({ loaded: event.loaded, total: event.total });
        }
      });
    }
    xhr.addEventListener("load", () => {
      let envelope: ApiResponse<T>;
      try {
        envelope = JSON.parse(xhr.responseText) as ApiResponse<T>;
      } catch {
        envelope = { data: null, error_messages: [], warning_messages: [] } as ApiResponse<T>;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const error = apiErrorFromEnvelope(
          xhr.status,
          envelope,
          xhr.getResponseHeader("X-Request-ID"),
        );
        notifyAuthStatus(xhr.status, error.requestId, error.errorCode);
        reject(error);
        return;
      }
      resolve(envelope.data as T);
    });
    // 接続の失敗は fetch と同じく ApiError ではない例外にする（画面は既定の失敗文言を出す）。
    xhr.addEventListener("error", () => reject(new TypeError("upload request failed")));
    xhr.addEventListener("abort", () => reject(new DOMException("upload aborted", "AbortError")));
    xhr.send(body);
  });
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
  // 認証・ユーザー / ロール・権限管理は securityApi（lib/security-api.ts。#214）。

  // データベース利用可否(設定の有無 + 実接続プローブ)。DB ゲートが参照する。
  getDatabaseStatus: () => request<DatabaseStatusData>("/api/ready/database"),

  // ドキュメント
  listDocuments: (
    params: {
      status?: FileStatus;
      q?: string;
      knowledge_base_id?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.q) search.set("q", params.q);
    if (params.knowledge_base_id)
      search.set("knowledge_base_id", params.knowledge_base_id);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<DocumentSummary>>(
      `/api/documents${qs ? `?${qs}` : ""}`,
    );
  },
  getDocument: (id: string) =>
    request<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}`),
  listDocumentChunkSets: (id: string) =>
    request<DocumentChunkSet[]>(
      `/api/documents/${encodeURIComponent(id)}/chunk-sets`,
    ),
  listDocumentRecipes: (id: string) =>
    request<DocumentRecipeView[]>(
      `/api/documents/${encodeURIComponent(id)}/recipes`,
    ),
  createDocumentRecipe: (id: string, copyFromRecipeId: string | null) =>
    request<DocumentRecipeView>(
      `/api/documents/${encodeURIComponent(id)}/recipes`,
      jsonBody({ copy_from_recipe_id: copyFromRecipeId }),
    ),
  updateDocumentRecipe: (
    id: string,
    recipeId: string,
    body: DocumentProcessingConfig,
  ) =>
    request<DocumentRecipeView>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(recipeId)}`,
      { ...jsonBody(body), method: "PUT" },
    ),
  deleteDocumentRecipe: (id: string, recipeId: string) =>
    request<DocumentRecipeDeleteResult>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(recipeId)}`,
      { method: "DELETE" },
    ),
  listDocumentRecipeChunks: (id: string, recipeId: string) =>
    request<DocumentChunkView[]>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(recipeId)}/chunks`,
    ),
  previewDocumentRecipeChunks: (
    id: string,
    recipeId: string,
    body: DocumentChunkPreviewRequest,
  ) =>
    request<DocumentChunkPreviewResponse>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(
        recipeId,
      )}/chunk-preview`,
      jsonBody(body),
    ),
  exportDocumentRecipeExtraction: (
    id: string,
    recipeId: string,
    format: DocumentExtractionExportFormat = "markdown",
  ) => {
    const search = new URLSearchParams({ format });
    return request<DocumentExtractionExport>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(
        recipeId,
      )}/extraction-export?${search.toString()}`,
    );
  },
  /**
   * 抽出エクスポートのダウンロード URL（`download=true` で `Content-Disposition: attachment` の
   * ファイルを返す。ファイル名は backend が文書名とレシピから決める。#561）。
   */
  documentRecipeExtractionExportUrl: (
    id: string,
    recipeId: string,
    format: DocumentExtractionExportFormat,
  ) => {
    const search = new URLSearchParams({ format, download: "true" });
    return `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(
      recipeId,
    )}/extraction-export?${search.toString()}`;
  },
  listDocumentIngestionJobs: (id: string) =>
    request<IngestionJob[]>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-jobs`,
    ),
  listDocumentIngestionSegments: (id: string) =>
    request<IngestionSegment[]>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-segments`,
    ),
  // 削除は成功しても原本・artifact の後始末の失敗を warning_messages で返すため、併せて返す（#281）。
  deleteDocument: (id: string) =>
    requestDegradable<DocumentDeleteResult>(
      `/api/documents/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
  listDocumentKnowledgeBases: (id: string) =>
    request<KnowledgeBaseRef[]>(
      `/api/documents/${encodeURIComponent(id)}/knowledge-bases`,
    ),
  getDocumentDeleteImpact: (ids: string[]) => {
    const search = new URLSearchParams();
    for (const id of ids) search.append("document_id", id);
    return request<DocumentDeleteImpact[]>(`/api/documents/delete-impact?${search.toString()}`);
  },
  getDocumentClassificationOptions: () =>
    request<DocumentClassificationOptions>("/api/documents/classification-options"),
  saveDocumentClassification: (id: string, body: DocumentClassification) =>
    request<DocumentDetail>(`/api/documents/${encodeURIComponent(id)}/classification`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  replaceDocumentKnowledgeBases: (
    id: string,
    body: DocumentKnowledgeBaseReplaceRequest,
  ) =>
    request<KnowledgeBaseRef[]>(
      `/api/documents/${encodeURIComponent(id)}/knowledge-bases`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  uploadDocument: (
    file: File,
    knowledgeBaseIds: string[] = [],
    onUploadProgress?: (progress: UploadTransferProgress) => void,
  ) => {
    const form = new FormData();
    form.append("file", file);
    for (const id of knowledgeBaseIds) {
      form.append("knowledge_base_ids", id);
    }
    return requestUpload<UploadResult>("/api/documents/upload", form, onUploadProgress);
  },
  batchUploadDocuments: (
    files: File[],
    knowledgeBaseIds: string[] = [],
    onUploadProgress?: (progress: UploadTransferProgress) => void,
  ) => {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    for (const id of knowledgeBaseIds) {
      form.append("knowledge_base_ids", id);
    }
    return requestUpload<BatchUploadResult>(
      "/api/documents/batch-upload",
      form,
      onUploadProgress,
    );
  },
  enqueueDocumentIngestionJob: (
    id: string,
    force = false,
    phase: IngestionJobPhase = "PREPROCESS",
  ) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-jobs?${ingestionJobSearch(force, phase)}`,
      { method: "POST" },
    ),
  enqueueDocumentRecipeJob: (
    id: string,
    recipeId: string,
    phase: IngestionJobPhase = "PREPROCESS",
  ) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(
        recipeId,
      )}/ingestion-jobs?phase=${encodeURIComponent(phase)}`,
      { method: "POST" },
    ),
  retryFailedDocumentIngestionSegments: (
    id: string,
    recipeId?: string | null,
  ) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/ingestion-segments/retry${
        recipeId ? `?recipe_id=${encodeURIComponent(recipeId)}` : ""
      }`,
      { method: "POST" },
    ),
  approveDocumentRecipe: (
    id: string,
    recipeId: string,
    payload?: DocumentApproveRequest,
  ) =>
    request<IngestionJob>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(
        recipeId,
      )}/approve`,
      payload ? jsonBody(payload) : { method: "POST" },
    ),
  saveDocumentRecipeReviewEdits: (
    id: string,
    recipeId: string,
    payload: DocumentReviewEditsRequest,
  ) =>
    request<DocumentRecipeView>(
      `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(
        recipeId,
      )}/review-edits`,
      { ...jsonBody(payload), method: "PATCH" },
    ),
  listIngestionJobs: (
    params: {
      status?: IngestionJobStatus;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<IngestionJob>>(
      `/api/documents/ingestion-jobs${qs ? `?${qs}` : ""}`,
    );
  },
  getIngestionJob: (id: string) =>
    request<IngestionJob>(
      `/api/documents/ingestion-jobs/${encodeURIComponent(id)}`,
    ),
  drainIngestionJobs: (limit = 50) =>
    request<IngestionJob[]>(
      `/api/documents/ingestion-jobs/drain?limit=${limit}`,
      {
        method: "POST",
      },
    ),
  retryIngestionJob: (id: string, force = false) =>
    request<IngestionJob>(
      `/api/documents/ingestion-jobs/${encodeURIComponent(id)}/retry${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),
  cancelIngestionJob: (id: string) =>
    request<IngestionJob>(
      `/api/documents/ingestion-jobs/${encodeURIComponent(id)}/cancel`,
      {
        method: "POST",
      },
    ),
  /** 原本/処理後ファイルの配信 URL（プレビュー/ダウンロード用）。 */
  documentContentUrl: (
    id: string,
    options: {
      variant?: "original" | "prepared";
      disposition?: "inline" | "attachment";
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (options.variant) search.set("variant", options.variant);
    if (options.disposition) search.set("disposition", options.disposition);
    const qs = search.toString();
    return `/api/documents/${encodeURIComponent(id)}/content${qs ? `?${qs}` : ""}`;
  },
  documentRecipeContentUrl: (
    id: string,
    recipeId: string,
    options: {
      variant?: "original" | "prepared";
      disposition?: "inline" | "attachment";
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (options.variant) search.set("variant", options.variant);
    if (options.disposition) search.set("disposition", options.disposition);
    const qs = search.toString();
    return `/api/documents/${encodeURIComponent(id)}/recipes/${encodeURIComponent(
      recipeId,
    )}/content${qs ? `?${qs}` : ""}`;
  },
  /** PDF をページ画像で表示するためのページ一覧（bbox の強調を重ねる。#349）。 */
  getDocumentPreviewPages: (
    id: string,
    options: { recipeId?: string | null; variant?: "original" | "prepared" } = {},
  ) =>
    request<DocumentPreviewPages>(
      `${documentPreviewPagesBase(id, options.recipeId)}${
        options.variant ? `?variant=${options.variant}` : ""
      }`,
    ),
  documentPreviewPageImageUrl: (
    id: string,
    pageNumber: number,
    options: { recipeId?: string | null; variant?: "original" | "prepared"; dpi?: number } = {},
  ) => {
    const search = new URLSearchParams();
    if (options.variant) search.set("variant", options.variant);
    if (options.dpi) search.set("dpi", String(options.dpi));
    const qs = search.toString();
    return `${documentPreviewPagesBase(id, options.recipeId)}/${pageNumber}${qs ? `?${qs}` : ""}`;
  },

  // ナレッジベース
  listKnowledgeBases: (
    params: {
      status?: KnowledgeBaseStatus;
      q?: string;
      limit?: number;
      offset?: number;
      /** 指定した ID の KB だけを返す（選択済みの名前・状態の解決用。#302）。 */
      ids?: string[];
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.q) search.set("q", params.q);
    for (const id of params.ids ?? []) search.append("ids", id);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<KnowledgeBaseSummary>>(
      `/api/knowledge-bases${qs ? `?${qs}` : ""}`,
    );
  },
  getKnowledgeBase: (id: string) =>
    request<KnowledgeBaseDetail>(
      `/api/knowledge-bases/${encodeURIComponent(id)}`,
    ),
  getKnowledgeBaseGraph: (id: string, limit = 80) =>
    request<KnowledgeBaseGraphData>(
      `/api/knowledge-bases/${encodeURIComponent(id)}/graph?limit=${limit}`,
    ),
  // KB ごとの項目抽出の定義（#548）。fields: null で全体の既定に戻す。
  getKnowledgeBaseExtractionFields: (id: string) =>
    request<KnowledgeBaseExtractionFieldsData>(
      `/api/knowledge-bases/${encodeURIComponent(id)}/extraction-fields`,
    ),
  updateKnowledgeBaseExtractionFields: (
    id: string,
    body: { fields: ExtractionFieldDefinition[] | null },
  ) =>
    request<KnowledgeBaseExtractionFieldsData>(
      `/api/knowledge-bases/${encodeURIComponent(id)}/extraction-fields`,
      { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    ),
  // 検索の絞り込みに使える項目（選んだ業務ビューの KB の定義の和集合。#549）。
  getSearchExtractionFields: (businessViewIds: string[]) =>
    request<SearchExtractionFieldsData>(
      `/api/search/extraction-fields?${new URLSearchParams({
        business_view_ids: businessViewIds.join(","),
      }).toString()}`,
    ),
  createKnowledgeBase: (body: KnowledgeBaseCreateRequest) =>
    request<KnowledgeBaseDetail>("/api/knowledge-bases", jsonBody(body)),
  updateKnowledgeBase: (id: string, body: KnowledgeBaseUpdateRequest) =>
    request<KnowledgeBaseDetail>(
      `/api/knowledge-bases/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  archiveKnowledgeBase: (id: string) =>
    request<KnowledgeBaseDetail>(
      `/api/knowledge-bases/${encodeURIComponent(id)}/archive`,
      {
        method: "POST",
      },
    ),
  assignDocumentsToKnowledgeBase: (
    id: string,
    body: KnowledgeBaseDocumentAssignmentRequest,
  ) =>
    request<KnowledgeBaseDetail>(
      `/api/knowledge-bases/${encodeURIComponent(id)}/documents`,
      jsonBody(body),
    ),
  removeDocumentFromKnowledgeBase: (
    knowledgeBaseId: string,
    documentId: string,
  ) =>
    request<KnowledgeBaseDetail>(
      `/api/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents/${encodeURIComponent(
        documentId,
      )}`,
      { method: "DELETE" },
    ),

  // 業務ビュー(Business View)
  listBusinessViews: (
    params: {
      status?: BusinessViewStatus;
      q?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.q) search.set("q", params.q);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<BusinessViewSummary>>(
      `/api/business-views${qs ? `?${qs}` : ""}`,
    );
  },
  getBusinessView: (id: string) =>
    request<BusinessViewDetail>(
      `/api/business-views/${encodeURIComponent(id)}`,
    ),
  createBusinessView: (body: BusinessViewCreateRequest) =>
    request<BusinessViewDetail>("/api/business-views", jsonBody(body)),
  updateBusinessView: (id: string, body: BusinessViewUpdateRequest) =>
    request<BusinessViewDetail>(
      `/api/business-views/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  archiveBusinessView: (id: string) =>
    request<BusinessViewDetail>(
      `/api/business-views/${encodeURIComponent(id)}/archive`,
      {
        method: "POST",
      },
    ),
  getDomainKeywords: (id: string) =>
    request<DomainKeywordsData>(
      `/api/business-views/${encodeURIComponent(id)}/domain-keywords`,
    ),
  saveDomainKeywords: (id: string, keywords: string[]) =>
    request<DomainKeywordsData>(
      `/api/business-views/${encodeURIComponent(id)}/domain-keywords`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keywords }),
      },
    ),
  getApprovedFaq: (id: string) =>
    request<ApprovedFaqListData>(
      `/api/business-views/${encodeURIComponent(id)}/approved-faq`,
    ),
  addApprovedFaq: (id: string, body: { question: string; answer: string }) =>
    request<ApprovedFaqMutationData>(
      `/api/business-views/${encodeURIComponent(id)}/approved-faq`,
      jsonBody(body),
    ),
  deleteApprovedFaq: (id: string, ids: string[]) =>
    request<ApprovedFaqMutationData>(
      `/api/business-views/${encodeURIComponent(id)}/approved-faq/delete`,
      jsonBody({ ids }),
    ),
  previewApprovedFaqImport: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ApprovedFaqImportPreviewData>(
      `/api/business-views/${encodeURIComponent(id)}/approved-faq/import/preview`,
      { method: "POST", body: form },
    );
  },
  importApprovedFaq: (id: string, file: File, mode: ApprovedFaqImportMode) => {
    const form = new FormData();
    form.append("file", file);
    form.append("mode", mode);
    return request<ApprovedFaqMutationData>(
      `/api/business-views/${encodeURIComponent(id)}/approved-faq/import`,
      { method: "POST", body: form },
    );
  },
  suggestApprovedFaq: (id: string, query: string) =>
    request<ApprovedFaqSuggestionsData>(
      `/api/business-views/${encodeURIComponent(id)}/approved-faq/suggest`,
      jsonBody({ query }),
    ),
  listDocragAnswers: (params: {
    businessViewId: string;
    limit: number;
    offset?: number;
    /** 指定するとその回答だけを返す（チャットが会話の回答の保存有無を引き当てる）。 */
    traceIds?: string[];
  }) => {
    const search = new URLSearchParams({
      business_view_id: params.businessViewId,
      limit: String(params.limit),
      offset: String(params.offset ?? 0),
    });
    for (const traceId of params.traceIds ?? []) search.append("trace_id", traceId);
    return request<Page<DocragAnswerSummary>>(`/api/search/answers?${search.toString()}`);
  },
  getDocragAnswer: (traceId: string) =>
    request<DocragAnswerDetail>(
      `/api/search/answers/${encodeURIComponent(traceId)}`,
    ),
  evaluateDocragAnswer: (traceId: string, standardAnswer: string) =>
    request<DocragAnswerDetail>(
      `/api/search/answers/${encodeURIComponent(traceId)}/evaluation`,
      jsonBody({ standard_answer: standardAnswer }),
      { timeoutMs: ANSWER_EVALUATION_TIMEOUT_MS },
    ),
  deleteDocragAnswer: (traceId: string) =>
    request<{ trace_id: string }>(
      `/api/search/answers/${encodeURIComponent(traceId)}`,
      {
        method: "DELETE",
      },
    ),
  getRuntimeKnowledge: (id: string) =>
    request<RuntimeKnowledgeData>(
      `/api/business-views/${encodeURIComponent(id)}/runtime-knowledge`,
    ),
  editRuntimeKnowledge: (id: string, body: RuntimeKnowledgeEditRequest) =>
    request<RuntimeKnowledgeData>(
      `/api/business-views/${encodeURIComponent(id)}/runtime-knowledge/edit`,
      jsonBody(body),
    ),
  previewRuntimeKnowledge: (id: string, question: string) =>
    request<RuntimeKnowledgePreviewData>(
      `/api/business-views/${encodeURIComponent(id)}/runtime-knowledge/preview`,
      jsonBody({ question }),
    ),
  suggestDomainKeywords: (id: string) =>
    request<DomainKeywordSuggestionData>(
      `/api/business-views/${encodeURIComponent(id)}/domain-keywords/suggest`,
      { method: "POST" },
    ),

  // チャット（会話 / マルチモデル比較）
  listConversations: (
    params: { business_view_id?: string; limit?: number; offset?: number } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.business_view_id)
      search.set("business_view_id", params.business_view_id);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return requestDegradable<Page<ConversationSummary>>(
      `/api/chat/conversations${qs ? `?${qs}` : ""}`,
    );
  },
  createConversation: (body: ConversationCreateBody) =>
    request<ConversationDetail>("/api/chat/conversations", jsonBody(body)),
  getConversation: (id: string) =>
    request<ConversationDetail>(
      `/api/chat/conversations/${encodeURIComponent(id)}`,
    ),
  updateConversation: (id: string, body: ConversationUpdateBody) =>
    request<ConversationSummary>(
      `/api/chat/conversations/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  deleteConversation: (id: string) =>
    request<null>(`/api/chat/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  listCompareModels: () => request<CompareModel[]>("/api/chat/models"),

  // 検索
  // 業務ビュー / KB の範囲外の 403（RAG_SCOPE_FORBIDDEN）は理由をその場で見せる（#214 / #224）。
  search: (body: SearchRequestBody) =>
    request<SearchResponse>("/api/search", jsonBody(body), {
      timeoutMs: ANSWER_GENERATION_TIMEOUT_MS,
    }),
  submitFeedback: (body: FeedbackRequestBody) =>
    request<FeedbackSubmissionResponse>("/api/feedback", jsonBody(body)),
  getCurrentFeedback: (traceId: string) => {
    const search = new URLSearchParams({ trace_id: traceId });
    return request<CurrentFeedbackItem[]>(
      `/api/feedback/current?${search.toString()}`,
    );
  },
  listFeedback: (params: FeedbackListParams = {}) => {
    const search = new URLSearchParams();
    if (params.business_view_id)
      search.set("business_view_id", params.business_view_id);
    if (params.target_type) search.set("target_type", params.target_type);
    if (params.rating) search.set("rating", params.rating);
    if (params.reason) search.set("reason", params.reason);
    if (params.period_days != null)
      search.set("period_days", String(params.period_days));
    if (params.q) search.set("q", params.q);
    if (params.sort_order) search.set("sort_order", params.sort_order);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return request<FeedbackDashboard>(`/api/feedback${qs ? `?${qs}` : ""}`);
  },
  getFeedbackDetail: (id: string) =>
    request<FeedbackDetail>(`/api/feedback/${encodeURIComponent(id)}`),
  getQueryHistorySettings: () => request<QueryHistorySettingsData>("/api/settings/query-history"),
  updateQueryHistorySettings: (body: QueryHistorySettingsData) =>
    request<QueryHistorySettingsData>("/api/settings/query-history", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getQuerySuggestions: (businessViewId: string, query: string, filters: Record<string, string>) =>
    request<QuerySuggestionsData>(
      `/api/business-views/${encodeURIComponent(businessViewId)}/query-suggestions?${new URLSearchParams({
        q: query,
        ...filters,
      }).toString()}`,
    ),
  getDocragPrompts: () => request<DocragPromptsData>("/api/settings/docrag-prompts"),
  saveDocragPrompt: (key: DocragPromptKey, content: string) =>
    request<DocragPromptsData>(`/api/settings/docrag-prompts/${key}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  resetDocragPrompt: (key: DocragPromptKey) =>
    request<DocragPromptsData>(`/api/settings/docrag-prompts/${key}`, { method: "DELETE" }),
  promoteFeedbackToApprovedFaq: (id: string) =>
    request<FeedbackApprovedFaqPromotion>(
      `/api/feedback/${encodeURIComponent(id)}/approved-faq`,
      { method: "POST" },
    ),
  getFeedbackEvaluationCase: (id: string) =>
    request<EvaluationCase>(`/api/feedback/${encodeURIComponent(id)}/evaluation-case`),

  // 評価
  // 品質評価は job で実行する（#390）。同期の /api/evaluation/run・/compare は画面から使わない。
  submitRunEvaluationJob: (body: EvaluationRunRequestBody) =>
    request<EvaluationJob>("/api/evaluation/jobs/run", jsonBody(body)),
  submitCompareEvaluationJob: (body: EvaluationCompareRequestBody) =>
    request<EvaluationJob>("/api/evaluation/jobs/compare", jsonBody(body)),
  getEvaluationJob: (jobId: string) =>
    request<EvaluationJob>(`/api/evaluation/jobs/${encodeURIComponent(jobId)}`),
  cancelEvaluationJob: (jobId: string) =>
    request<EvaluationJob>(`/api/evaluation/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    }),

  // 設定: モデル
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

  // 設定: データベース
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
  getSystemTablesStatus: () =>
    request<SystemTablesStatusData>("/api/settings/database/system-tables"),
  initializeSystemTables: (body: SystemTablesInitializeRequest) =>
    request<SystemTablesOperationData>(
      "/api/settings/database/system-tables/initialize",
      jsonBody(body),
    ),
  deleteSystemTableOrphanedRows: (body: SystemTablesDeleteOrphansRequest) =>
    request<SystemTablesOrphanDeletionData>(
      "/api/settings/database/system-tables/orphaned-rows/delete",
      jsonBody(body),
    ),

  // 設定: Autonomous Database 管理
  getAdbInfo: () => request<AdbInfoData>("/api/settings/database/adb"),
  updateAdbSettings: (body: AdbSettingsUpdate) =>
    request<AdbInfoData>("/api/settings/database/adb/settings", jsonBody(body)),
  startAdb: () =>
    request<AdbInfoData>("/api/settings/database/adb/start", {
      method: "POST",
    }),
  stopAdb: () =>
    request<AdbInfoData>("/api/settings/database/adb/stop", { method: "POST" }),

  // 設定: HuggingFace モデルダウンロード
  getHuggingFaceSettings: () =>
    request<HuggingFaceSettingsData>("/api/settings/huggingface"),
  updateHuggingFaceSettings: (body: HuggingFaceSettingsUpdate) =>
    request<HuggingFaceSettingsData>("/api/settings/huggingface", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

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
  getExternalParserStatus: (backend: ExternalParserBackendName) =>
    request<ExternalParserConnectionStatusData>(
      `/api/settings/parser-adapters/${encodeURIComponent(backend)}/status`,
    ),
  updateParserAdapterSettings: (body: ParserAdapterSettingsUpdate) =>
    request<ParserAdapterSettingsData>("/api/settings/parser-adapters", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // サービス管理: 前処理 / Parser マイクロサービスの稼働可視化・起動/停止
  getServiceCatalog: () => request<ServiceCatalogData>("/api/services/catalog"),
  getServiceStatus: (serviceId: string) =>
    request<ServiceStatusData>(
      `/api/services/${encodeURIComponent(serviceId)}/status`,
    ),
  getServiceLogs: (serviceId: string, lines = 200) =>
    request<ServiceLogsData>(
      `/api/services/${encodeURIComponent(serviceId)}/logs?lines=${encodeURIComponent(String(lines))}`,
    ),
  controlService: (serviceId: string, action: ServiceAction) =>
    request<ServiceControlResultData>(
      `/api/services/${encodeURIComponent(serviceId)}/${action}`,
      { method: "POST" },
    ),

  // 設定: Chunking アダプター
  getPreprocessSettings: () =>
    request<PreprocessSettingsData>("/api/settings/preprocess"),
  updatePreprocessSettings: (body: PreprocessSettingsUpdate) =>
    request<PreprocessSettingsData>("/api/settings/preprocess", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  getChunkingSettings: () =>
    request<ChunkingSettingsData>("/api/settings/chunking"),
  updateChunkingSettings: (body: ChunkingSettingsUpdate) =>
    request<ChunkingSettingsData>("/api/settings/chunking", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Retrieval アダプター
  getRetrievalSettings: () =>
    request<RetrievalSettingsData>("/api/settings/retrieval"),
  updateRetrievalSettings: (body: RetrievalSettingsUpdate) =>
    request<RetrievalSettingsData>("/api/settings/retrieval", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Grounding アダプター
  getGroundingSettings: () =>
    request<GroundingSettingsData>("/api/settings/grounding"),
  updateGroundingSettings: (body: GroundingSettingsUpdate) =>
    request<GroundingSettingsData>("/api/settings/grounding", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Generation アダプター
  getGenerationSettings: () =>
    request<GenerationSettingsData>("/api/settings/generation"),
  getAnsweringSettings: () => request<AnsweringSettingsData>("/api/settings/answering"),
  updateAnsweringSettings: (body: AnsweringSettingsUpdate) =>
    request<AnsweringSettingsData>("/api/settings/answering", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getAnswerRecordSettings: () =>
    request<AnswerRecordSettingsData>("/api/settings/answer-records"),
  updateAnswerRecordSettings: (body: { retention_days: number }) =>
    request<AnswerRecordSettingsData>("/api/settings/answer-records", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  updateGenerationSettings: (body: GenerationSettingsUpdate) =>
    request<GenerationSettingsData>("/api/settings/generation", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: 回答プロンプト版
  getPromptVersions: () => request<PromptVersionsData>("/api/settings/prompts"),
  createPromptVersion: (body: PromptVersionCreate) =>
    request<PromptVersionsData>("/api/settings/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  activatePromptVersion: (versionId: string) =>
    request<PromptVersionsData>(
      `/api/settings/prompts/${encodeURIComponent(versionId)}/activate`,
      { method: "POST" },
    ),

  // 設定: Guardrail アダプター
  getExtractionFieldsSettings: () =>
    request<ExtractionFieldsSettingsData>("/api/settings/extraction-fields"),
  updateExtractionFieldsSettings: (body: { fields: ExtractionFieldDefinition[] }) =>
    request<ExtractionFieldsSettingsData>("/api/settings/extraction-fields", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  // 保存した全体の既定を消し、標準の項目に戻す（#556）。
  resetExtractionFieldsSettings: () =>
    request<ExtractionFieldsSettingsData>("/api/settings/extraction-fields", { method: "DELETE" }),
  getPipelineSettings: () => request<PipelineSettingsData>("/api/settings/pipeline"),
  updatePipelineSettings: (body: PipelineSettingsUpdate) =>
    request<PipelineSettingsData>("/api/settings/pipeline", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getGuardrailSettings: () =>
    request<GuardrailSettingsData>("/api/settings/guardrail"),
  updateGuardrailSettings: (body: GuardrailSettingsUpdate) =>
    request<GuardrailSettingsData>("/api/settings/guardrail", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Vector Index アダプター
  getVectorIndexSettings: () =>
    request<VectorIndexSettingsData>("/api/settings/vector-index"),
  updateVectorIndexSettings: (body: VectorIndexSettingsUpdate) =>
    request<VectorIndexSettingsData>("/api/settings/vector-index", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  // 設定: Evaluation アダプター
  getEvaluationSettings: () =>
    request<EvaluationSettingsData>("/api/settings/evaluation-suite"),
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
  getAgenticSettings: () =>
    request<AgenticSettingsData>("/api/settings/agentic"),
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

// --- 業務ビューの知識: ドメインキーワード(rag_poc DocRAG 由来) ---
export interface DomainKeywordsData {
  business_view_id: string;
  keywords: string[];
}

export interface DomainKeywordCandidateData {
  keyword: string;
  score: number;
  frequency: number;
  chunk_count: number;
  document_count: number;
}

export interface DomainKeywordSuggestionData {
  candidates: DomainKeywordCandidateData[];
  processed_chunk_count: number;
}

// --- 業務ビューの知識: Approved FAQ(類似問) ---
export type ApprovedFaqImportMode = "INSERT" | "DELETE_THEN_INSERT";

export interface ApprovedFaqRecordData {
  id: string;
  question: string;
  answer: string;
  alternate_questions: string[];
  status: string;
}

export interface ApprovedFaqListData {
  business_view_id: string;
  records: ApprovedFaqRecordData[];
}

export interface ApprovedFaqMutationData extends ApprovedFaqListData {
  inserted_count: number;
  deleted_count: number;
}

export interface ApprovedFaqImportPreviewData {
  total: number;
  rows: { question: string; answer: string; row: number }[];
}

export interface ApprovedFaqSuggestionData {
  id: string;
  question: string;
  matched_question: string;
  answer: string;
  score: number;
  direct: boolean;
}

export interface ApprovedFaqSuggestionsData {
  suggestions: ApprovedFaqSuggestionData[];
}

// --- 業務ビューの知識: 用語・ルール(runtime knowledge) ---
export type RuntimeKnowledgeKind = "terms" | "rules";

export interface RuntimeKnowledgeData {
  business_view_id: string;
  terms: Record<string, JsonValue>[];
  rules: Record<string, JsonValue>[];
}

export interface RuntimeKnowledgeEditRequest {
  kind: RuntimeKnowledgeKind;
  selected?: string | null;
  name?: string;
  title?: string;
  labels?: string;
  content?: string;
  source?: string;
  enabled?: boolean;
  delete?: boolean;
}

export interface RuntimeKnowledgePreviewData {
  expanded_question: string;
  matched_terms: string[];
  matched_rules: string[];
}

// --- 保存済み DocRAG 回答(rag_poc の answer JSON 相当) ---
export interface DocragAnswerSummary {
  trace_id: string;
  business_view_id: string | null;
  surface: "search" | "chat";
  answer_engine: string;
  question: string;
  rewritten_question: string | null;
  confidence: string | null;
  created_at: string;
}

export interface DocragAnswerDetail extends DocragAnswerSummary {
  answer: string;
  citations: RetrievedChunk[];
  docrag: Record<string, JsonValue>;
  /** 標準回答で評価できるか(この機能より前の回答は評価の入力を持たない)。 */
  evaluation_available?: boolean;
  /** 標準回答による評価の結果(未評価は null)。 */
  evaluation?: Record<string, JsonValue> | null;
}
