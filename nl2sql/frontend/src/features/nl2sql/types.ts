export type Nl2SqlEngine = "auto" | "select_ai" | "select_ai_agent" | "enterprise_ai_direct";

export type JobStatus = "pending" | "running" | "done" | "error";

export interface StageTiming {
  stage: string;
  elapsed_ms: number;
}

export interface TimingEnvelope {
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_ms?: number | null;
  stage_timings: StageTiming[];
}

export interface SchemaColumn {
  column_name: string;
  logical_name: string;
  data_type: string;
  nullable: boolean;
  comment: string;
  sample_values: string[];
}

export interface SchemaTable {
  table_name: string;
  logical_name: string;
  owner: string;
  table_type: string;
  comment: string;
  row_count?: number | null;
  columns: SchemaColumn[];
  constraints: string[];
}

export interface SchemaCatalog {
  refreshed_at: string;
  tables: SchemaTable[];
}

export interface AllowedObjects {
  table_names: string[];
  columns: Record<string, string[]>;
}

export interface Nl2SqlProfile {
  id: string;
  name: string;
  description: string;
  allowed_tables: string[];
  glossary: Record<string, string>;
  sql_rules: string[];
  default_row_limit: number;
  safety_policy: string;
  few_shot_examples: Array<Record<string, string>>;
  archived: boolean;
}

export interface ProfileUpsertPayload {
  name: string;
  description: string;
  allowed_tables: string[];
  glossary: Record<string, string>;
  sql_rules: string[];
  default_row_limit: number;
  safety_policy: string;
  few_shot_examples: Array<Record<string, string>>;
}

export interface ProfileLearningMaterialImportData {
  profile_id: string;
  profile_name: string;
  mode: string;
  imported_terms: number;
  imported_rules: number;
  imported_examples: number;
  skipped_count: number;
  warnings: string[];
  profile: Nl2SqlProfile;
}

export interface ProfileRecommendationCandidate {
  profile_id: string;
  profile_name: string;
  score: number;
  matched_terms: string[];
  allowed_tables: string[];
  category?: string;
}

export interface ProfileRecommendationData {
  recommended_profile_id: string;
  recommended_profile_name: string;
  confidence: number;
  reason: string;
  rewritten_question: string;
  recommended_allowed_objects: AllowedObjects;
  candidates: ProfileRecommendationCandidate[];
  recommendation_source?: string;
  classifier_version?: string;
  category_scores?: Record<string, number>;
}

export interface ClassifierTrainingExample {
  id: string;
  category: string;
  text: string;
  profile_id: string;
  source: string;
}

export interface ClassifierImportData {
  imported_count: number;
  skipped_count: number;
  total_examples: number;
  categories: string[];
  warnings: string[];
  examples: ClassifierTrainingExample[];
}

export interface ClassifierStatusData {
  ready: boolean;
  trained: boolean;
  classifier_version: string;
  updated_at: string;
  example_count: number;
  category_count: number;
  categories: string[];
  embedding_model: string;
  vector_dimension: number;
  persistence_mode: string;
  recommendation_source: string;
  metrics: Record<string, string | number>;
  warnings: string[];
}

export interface ClassifierPredictionCandidate {
  category: string;
  score: number;
  profile_id: string;
  profile_name: string;
}

export interface ClassifierPredictionData {
  recommendation_source: string;
  classifier_version: string;
  predicted_category: string;
  confidence: number;
  candidates: ClassifierPredictionCandidate[];
  warnings: string[];
}

export interface RewriteData {
  original_question: string;
  rewritten_question: string;
  source: string;
  model: string;
  warnings: string[];
}

export interface SafetyReport {
  is_safe: boolean;
  is_select_only: boolean;
  row_limit_applied: number;
  blocked_reason: string;
  warnings: string[];
  referenced_tables: string[];
  referenced_columns: string[];
}

export interface QueryResults {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  total: number;
}

export interface Nl2SqlResult {
  engine: Nl2SqlEngine;
  engine_meta: Record<string, unknown>;
  fallback_reason: string;
  original_question: string;
  rewritten_question: string;
  generated_sql: string;
  executable_sql: string;
  explanation: string;
  safety: SafetyReport;
  recommendations: string[];
  repaired_sql: string;
  optimization_hints: string[];
  results: QueryResults;
  timing: TimingEnvelope;
}

export interface GeneratedSqlPanelData {
  engine: Nl2SqlEngine;
  engine_meta: Record<string, unknown>;
  fallback_reason: string;
  generated_sql: string;
  executable_sql: string;
  explanation: string;
  safety: SafetyReport;
  recommendations: string[];
  repaired_sql: string;
  optimization_hints: string[];
  rewritten_question: string;
}

export interface PreviewData {
  sql: string;
  is_safe: boolean;
  row_limit: number;
  note: string;
  engine: Nl2SqlEngine;
  engine_meta: Record<string, unknown>;
  fallback_reason: string;
  rewritten_question: string;
  executable_sql: string;
  safety?: SafetyReport | null;
  recommendations: string[];
  repaired_sql: string;
  optimization_hints: string[];
  timing?: TimingEnvelope | null;
}

export interface JobCreateData {
  job_id: string;
  status: JobStatus;
  created_at: string;
}

export interface JobData {
  job_id: string;
  status: JobStatus;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_ms?: number | null;
  result?: Nl2SqlResult | null;
  error_message?: string | null;
  timing?: TimingEnvelope | null;
}

export interface HistoryItem {
  id: string;
  question: string;
  engine: Nl2SqlEngine;
  generated_sql: string;
  created_at: string;
  elapsed_ms?: number | null;
  feedback_rating?: "good" | "bad" | "needs_review" | null;
  profile_id: string;
  profile_name: string;
  rewritten_question: string;
  executable_sql: string;
  safety_is_safe: boolean;
  result_row_count: number;
  result_columns: string[];
  feedback_comment: string;
}

export interface HistoryData {
  items: HistoryItem[];
}

export type FeedbackRating = "good" | "bad" | "needs_review";

export interface FeedbackData {
  history_id: string;
  rating: FeedbackRating;
  saved: boolean;
  comment: string;
}

export interface FeedbackIndexData {
  operation: string;
  status: string;
  executed: boolean;
  runtime: string;
  source_history_count: number;
  indexable_count: number;
  indexed_count: number;
  vector_dimension: number;
  vector_backend: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_configured: boolean;
  ddl: string[];
  warnings: string[];
  timing: TimingEnvelope;
}

export interface FeedbackVectorEntry {
  history_id: string;
  question: string;
  generated_sql: string;
  profile_id: string;
  profile_name: string;
  feedback_rating?: FeedbackRating | null;
  feedback_comment: string;
  indexed: boolean;
  created_at: string;
}

export interface FeedbackEntriesData {
  items: FeedbackVectorEntry[];
  total: number;
  indexed_count: number;
}

export interface FeedbackSearchConfigData {
  similarity_threshold: number;
  match_limit: number;
}

export interface DemoLearningData {
  seeded_history_count: number;
  seeded_feedback_count: number;
  history_ids: string[];
  profile_ids: string[];
  message: string;
}

export interface SimilarHistoryItem {
  item: HistoryItem;
  score: number;
  reason: string;
}

export interface SimilarHistoryData {
  items: SimilarHistoryItem[];
}

export interface CompareData {
  question: string;
  results: PreviewData[];
  execution_results: CompareExecutionData[];
  error_rate: number;
  recommendation: string;
}

export interface CompareRecord {
  id: string;
  created_at: string;
  profile_id: string;
  profile_name: string;
  question: string;
  engines: Nl2SqlEngine[];
  execute: boolean;
  report: string;
  comparison: CompareData;
}

export interface CompareHistoryData {
  items: CompareRecord[];
}

export interface CompareExecutionData {
  engine: Nl2SqlEngine;
  executed: boolean;
  row_count: number;
  error_message: string;
  results?: QueryResults | null;
  elapsed_ms?: number | null;
}

export interface EvaluateData {
  evaluation_suite: string;
  total_cases: number;
  executable_rate: number;
  select_only_rate: number;
  findings: string[];
}

export interface SyntheticCase {
  question: string;
  expected_sql: string;
  profile_id: string;
}

export interface SyntheticCasesData {
  cases: SyntheticCase[];
}

export interface EvaluationSet {
  id: string;
  name: string;
  description: string;
  profile_id: string;
  profile_name: string;
  engine: Nl2SqlEngine;
  cases: SyntheticCase[];
  created_at: string;
  updated_at: string;
  archived: boolean;
}

export interface EvaluationSetsData {
  items: EvaluationSet[];
}

export interface EvaluationSetPayload {
  name: string;
  description: string;
  profile_id: string;
  engine: Nl2SqlEngine;
  cases: SyntheticCase[];
}

export interface EvaluationRunRecord {
  id: string;
  created_at: string;
  evaluation_set_id: string;
  evaluation_set_name: string;
  profile_id: string;
  profile_name: string;
  engine: Nl2SqlEngine;
  cases: SyntheticCase[];
  result: EvaluateData;
  report: string;
}

export interface EvaluationRunsData {
  items: EvaluationRunRecord[];
}

export interface ReverseSqlData {
  question: string;
  explanation: string;
  referenced_tables: string[];
  logical_steps?: string[];
  source?: string;
  warnings?: string[];
}

export interface AnalyzeData {
  safety: SafetyReport;
  explanation: string;
  recommendations: string[];
  executable_sql: string;
  repaired_sql: string;
  optimization_hints: string[];
  structure_summary?: string;
  risk_level?: string;
  operations?: string[];
  filters?: string[];
  joins?: string[];
  aggregations?: string[];
  llm_enhanced?: boolean;
  llm_warnings?: string[];
}

export interface RepairData {
  error_code: string;
  repaired_sql: string;
  explanation: string;
  recommendations: string[];
  safety: SafetyReport;
  executable_sql: string;
}

export interface CommentSuggestion {
  object_name: string;
  object_type: string;
  suggested_comment: string;
}

export interface CommentSuggestionData {
  suggestions: CommentSuggestion[];
  source?: string;
  warnings?: string[];
}

export interface CommentApplyItem {
  object_name: string;
  object_type: string;
  comment: string;
}

export interface CommentApplyStatement {
  object_name: string;
  object_type: string;
  comment: string;
  sql: string;
  status: string;
  error_message: string;
}

export interface CommentApplyData {
  executed: boolean;
  runtime: string;
  statements: CommentApplyStatement[];
  warnings: string[];
  timing: TimingEnvelope;
}

export interface AnnotationSuggestion {
  object_name: string;
  object_type: string;
  annotation_name: string;
  annotation_value: string;
}

export interface AnnotationSuggestionData {
  suggestions: AnnotationSuggestion[];
  source: string;
  warnings: string[];
}

export interface AnnotationApplyItem {
  object_name: string;
  object_type: string;
  annotation_name: string;
  annotation_value: string;
}

export interface AnnotationApplyStatement {
  object_name: string;
  object_type: string;
  annotation_name: string;
  annotation_value: string;
  sql: string;
  status: string;
  error_message: string;
}

export interface AnnotationApplyData {
  executed: boolean;
  runtime: string;
  statements: AnnotationApplyStatement[];
  warnings: string[];
  timing: TimingEnvelope;
}

export interface DiagnosticCheck {
  name: string;
  status: string;
  message: string;
}

export interface DiagnosticReadiness {
  area: string;
  label: string;
  status: string;
  summary: string;
  next_action: string;
  related_checks: string[];
}

export interface DiagnosticSmokeCheck {
  id: string;
  label: string;
  category: string;
  status: string;
  method: string;
  endpoint: string;
  request_hint: string;
  command: string;
  expected: string;
  next_action: string;
  related_readiness: string[];
}

export interface DiagnosticConfigVar {
  name: string;
  status: string;
  required: boolean;
  note: string;
}

export interface DiagnosticConfigGuide {
  id: string;
  label: string;
  status: string;
  summary: string;
  next_action: string;
  required_env_vars: DiagnosticConfigVar[];
  optional_env_vars: DiagnosticConfigVar[];
  env_template: string;
  smoke_command: string;
  related_readiness: string[];
}

export interface DiagnosticsData {
  checks: DiagnosticCheck[];
  readiness?: DiagnosticReadiness[];
  smoke_checks?: DiagnosticSmokeCheck[];
  config_guides?: DiagnosticConfigGuide[];
}

export interface AssetRefreshData {
  engine: Nl2SqlEngine;
  refreshed: boolean;
  status: string;
  refreshed_at: string;
  profile_name: string;
  team_name: string;
  warning: string;
  asset_names: Record<string, string>;
  engine_meta: Record<string, unknown>;
}

export interface AssetCleanupData {
  engine: Nl2SqlEngine;
  executed: boolean;
  status: string;
  cleaned_at: string;
  profile_name: string;
  team_name: string;
  warning: string;
  asset_names: Record<string, string>;
  engine_meta: Record<string, unknown>;
}

export interface SelectAiDbProfile {
  name: string;
  status: string;
  owner: string;
  created_at: string;
  attributes: Record<string, unknown>;
}

export interface SelectAiDbProfilesData {
  runtime: string;
  profiles: SelectAiDbProfile[];
  warnings: string[];
}

export interface AgentTeamRunData {
  team_name: string;
  prompt: string;
  generated_sql: string;
  conversation_id: string;
  runtime: string;
  warnings: string[];
  engine_meta: Record<string, unknown>;
}

export interface AgentConversationItem {
  conversation_id: string;
  prompt: string;
  response: string;
  created_at: string;
  team_name: string;
}

export interface AgentConversationsData {
  runtime: string;
  items: AgentConversationItem[];
  warnings: string[];
}

export interface AgentPrivilegeCheckData {
  runtime: string;
  status: string;
  checks: DiagnosticCheck[];
  warnings: string[];
}

export interface SyntheticDataOperationData {
  operation_id: string;
  table_name: string;
  row_count: number;
  executed: boolean;
  runtime: string;
  status: string;
  message: string;
  warnings: string[];
  engine_meta: Record<string, unknown>;
  timing: TimingEnvelope;
}

export interface SyntheticDataOperationStatusData {
  operation_id: string;
  runtime: string;
  status: string;
  message: string;
  result: Record<string, unknown>;
  warnings: string[];
}

export interface CsvImportColumn {
  source_name: string;
  column_name: string;
  data_type: string;
  nullable: boolean;
}

export interface CsvImportData {
  table_name: string;
  columns: CsvImportColumn[];
  row_count: number;
  dry_run: boolean;
  executed: boolean;
  ddl: string;
  insert_sql: string;
  warnings: string[];
  sample_rows: Array<Record<string, string | null>>;
  timing: TimingEnvelope;
}
