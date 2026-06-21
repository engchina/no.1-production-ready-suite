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

export interface ProfileRecommendationCandidate {
  profile_id: string;
  profile_name: string;
  score: number;
  matched_terms: string[];
  allowed_tables: string[];
}

export interface ProfileRecommendationData {
  recommended_profile_id: string;
  recommended_profile_name: string;
  confidence: number;
  reason: string;
  rewritten_question: string;
  recommended_allowed_objects: AllowedObjects;
  candidates: ProfileRecommendationCandidate[];
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

export interface ReverseSqlData {
  question: string;
  explanation: string;
  referenced_tables: string[];
}

export interface AnalyzeData {
  safety: SafetyReport;
  explanation: string;
  recommendations: string[];
  executable_sql: string;
  repaired_sql: string;
  optimization_hints: string[];
}

export interface CommentSuggestion {
  object_name: string;
  object_type: string;
  suggested_comment: string;
}

export interface CommentSuggestionData {
  suggestions: CommentSuggestion[];
}

export interface DiagnosticCheck {
  name: string;
  status: string;
  message: string;
}

export interface DiagnosticsData {
  checks: DiagnosticCheck[];
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
