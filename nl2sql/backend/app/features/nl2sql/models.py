"""NL2SQL API models.

外部サービスへ依存しない契約をここに集約する。Oracle / OCI の実呼び出しは
service 層の adapter に閉じ込め、API と UI は同じ shape を使い続ける。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Nl2SqlEngine(StrEnum):
    """NL2SQL 実行エンジン。"""

    AUTO = "auto"
    SELECT_AI = "select_ai"
    SELECT_AI_AGENT = "select_ai_agent"
    ENTERPRISE_AI_DIRECT = "enterprise_ai_direct"


class JobStatus(StrEnum):
    """非同期ジョブ状態。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class FeedbackRating(StrEnum):
    """検索結果へのフィードバック。"""

    GOOD = "good"
    BAD = "bad"
    NEEDS_REVIEW = "needs_review"


class SampleDataStep(StrEnum):
    """SQL Assist sample data import step."""

    TABLES = "tables"
    VIEWS = "views"
    DATA = "data"
    ALL = "all"


class StageTiming(BaseModel):
    """処理段階ごとの経過時間。"""

    stage: str
    elapsed_ms: int


class TimingEnvelope(BaseModel):
    """同期/非同期レスポンス共通の時間情報。"""

    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_ms: int | None = None
    stage_timings: list[StageTiming] = Field(default_factory=list)


class SchemaColumn(BaseModel):
    """Oracle column metadata for UI schema picking."""

    column_name: str
    logical_name: str
    data_type: str
    nullable: bool = True
    comment: str = ""
    sample_values: list[str] = Field(default_factory=list)


class SchemaTable(BaseModel):
    """Oracle table metadata for NL2SQL object restriction."""

    table_name: str
    logical_name: str
    owner: str = "APP"
    table_type: str = "table"
    comment: str = ""
    row_count: int | None = None
    columns: list[SchemaColumn] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class SchemaCatalog(BaseModel):
    """UI が表示する schema catalog."""

    refreshed_at: str
    tables: list[SchemaTable]


class AllowedObjects(BaseModel):
    """ユーザーが今回の質問で許可する table / column 範囲。"""

    table_names: list[str] = Field(default_factory=list)
    columns: dict[str, list[str]] = Field(default_factory=dict)


class Nl2SqlProfile(BaseModel):
    """業務/Profile 単位の NL2SQL 設定。"""

    id: str
    name: str
    category: str = ""
    description: str = ""
    allowed_tables: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    sql_rules: list[str] = Field(default_factory=list)
    default_row_limit: int = 100
    safety_policy: str = "select_only"
    few_shot_examples: list[dict[str, str]] = Field(default_factory=list)
    archived: bool = False


class ProfileUpsertRequest(BaseModel):
    """Profile 作成/更新 request."""

    name: str = Field(min_length=1)
    category: str = ""
    description: str = ""
    allowed_tables: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    sql_rules: list[str] = Field(default_factory=list)
    default_row_limit: int = Field(default=100, ge=1, le=5000)
    safety_policy: str = "select_only"
    few_shot_examples: list[dict[str, str]] = Field(default_factory=list)


class ProfileLearningMaterialImportData(BaseModel):
    """Terms / rules / few-shot learning material import response."""

    profile_id: str
    profile_name: str
    mode: str = "merge"
    imported_terms: int = 0
    imported_rules: int = 0
    imported_examples: int = 0
    skipped_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    profile: Nl2SqlProfile


class LegacySqlRuleEntry(BaseModel):
    """旧版 rules.xlsx の 1 行."""

    category: str = "共通"
    rule: str


class LegacyLearningMaterialData(BaseModel):
    """旧版 terms.xlsx / rules.xlsx 互換の用語・ルール."""

    glossary: dict[str, str] = Field(default_factory=dict)
    rule_entries: list[LegacySqlRuleEntry] = Field(default_factory=list)


class SafetyReport(BaseModel):
    """SQL safety analysis."""

    is_safe: bool
    is_select_only: bool
    row_limit_applied: int
    blocked_reason: str = ""
    warnings: list[str] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)


class QueryResults(BaseModel):
    """SQL execution results."""

    columns: list[str]
    rows: list[dict[str, Any]]
    total: int


class AdminExecutionConfirmation(BaseModel):
    """Common confirmation fields for admin/destructive operations."""

    execute: bool = False
    confirmation: str = ""
    reason: str = ""


class DbAdminObjectSummary(BaseModel):
    """Database admin table/view summary."""

    name: str
    owner: str = ""
    object_type: str = "table"
    row_count: int | None = None
    comment: str = ""


class DbAdminObjectDetail(BaseModel):
    """Database admin table/view detail with columns and DDL."""

    name: str
    owner: str = ""
    object_type: str = "table"
    row_count: int | None = None
    comment: str = ""
    columns: list[SchemaColumn] = Field(default_factory=list)
    ddl: str = ""
    warnings: list[str] = Field(default_factory=list)


class DbAdminObjectsData(BaseModel):
    """Database admin object list response."""

    runtime: str = "deterministic"
    items: list[DbAdminObjectSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DbAdminDropTableRequest(AdminExecutionConfirmation):
    """Drop table dry-run / execution request."""

    table_name: str = Field(min_length=1)
    purge: bool = True


class DbAdminStatementResult(BaseModel):
    """One admin SQL statement execution result."""

    index: int
    statement_type: str
    status: str
    sql: str = ""
    row_count: int | None = None
    message: str = ""
    elapsed_ms: int = 0
    error_message: str = ""


class DbAdminExecuteRequest(AdminExecutionConfirmation):
    """Admin SQL executor request.

    This intentionally lives outside the normal SELECT-only NL2SQL query path.
    """

    sql: str = Field(min_length=1)
    row_limit: int = Field(default=100, ge=1, le=5000)


class DbAdminExecuteData(BaseModel):
    """Admin SQL executor response."""

    executed: bool = False
    runtime: str = "deterministic"
    select_result: QueryResults | None = None
    statements: list[DbAdminStatementResult] = Field(default_factory=list)
    committed: bool = False
    rolled_back: bool = False
    warnings: list[str] = Field(default_factory=list)
    timing: TimingEnvelope


class SampleDataInfo(BaseModel):
    """Optional SQL Assist sample package status."""

    runtime: str = "deterministic"
    profile_id: str = "sql_assist_sample"
    confirmation: str = "SQL_ASSIST_SAMPLE"
    objects: list[str] = Field(default_factory=list)
    imported_objects: list[str] = Field(default_factory=list)
    sql: dict[str, list[str]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SampleDataMutationRequest(AdminExecutionConfirmation):
    """Sample data import/delete dry-run / execution request."""

    step: SampleDataStep = SampleDataStep.ALL


class SampleDataMutationData(BaseModel):
    """Sample data import/delete response."""

    operation: str
    step: SampleDataStep = SampleDataStep.ALL
    runtime: str = "deterministic"
    executed: bool = False
    dry_run: bool = True
    objects: list[str] = Field(default_factory=list)
    statements: list[DbAdminStatementResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    profile_id: str = "sql_assist_sample"
    timing: TimingEnvelope


class DbAdminImportTabularRequest(AdminExecutionConfirmation):
    """CSV/XLSX tabular import dry-run / execution request."""

    table_name: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)
    filename: str = "upload.csv"
    sheet_name: str = ""
    mode: str = "create"
    max_rows: int | None = Field(default=None, ge=1, le=50000)


DbAdminStatementPolicy = Literal[
    "table_ddl",
    "view_ddl",
    "data_dml",
    "comment_sql",
    "annotation_sql",
]


class DbAdminStatementsRequest(AdminExecutionConfirmation):
    """文種 whitelist 付き複数 statement 実行 request(SQL Assist 移植)。"""

    sql: str = Field(min_length=1)
    policy: DbAdminStatementPolicy


class DbAdminDropViewRequest(AdminExecutionConfirmation):
    """Drop view dry-run / execution request."""

    view_name: str = Field(min_length=1)


class DbAdminDataPreviewRequest(BaseModel):
    """テーブル/ビューのデータ表示 request。"""

    object_name: str = Field(min_length=1)
    limit: int = Field(default=100, ge=1, le=10000)
    where_clause: str = ""


class DbAdminDataPreviewData(BaseModel):
    """テーブル/ビューのデータ表示 response。"""

    runtime: str = "deterministic"
    sql: str = ""
    results: QueryResults
    warnings: list[str] = Field(default_factory=list)


class DbAdminCsvUploadRequest(AdminExecutionConfirmation):
    """既存テーブルへの CSV アップロード(INSERT / TRUNCATE&INSERT)request。"""

    table_name: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)
    filename: str = "upload.csv"
    mode: Literal["insert", "truncate_insert"] = "insert"
    max_rows: int | None = Field(default=None, ge=1, le=50000)


class DbAdminCsvUploadData(BaseModel):
    """CSV アップロード dry-run / execution response。"""

    table_name: str
    filename: str = ""
    mode: str = "insert"
    matched_columns: list[str] = Field(default_factory=list)
    unmatched_csv_columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    success_count: int = 0
    error_count: int = 0
    row_errors: list[str] = Field(default_factory=list)
    hint: str = ""
    dry_run: bool = True
    executed: bool = False
    runtime: str = "deterministic"
    sample_rows: list[dict[str, str | None]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timing: TimingEnvelope


class DbAdminAiAnalysisRequest(BaseModel):
    """Admin SQL 実行結果の AI 分析 request。"""

    sql: str = ""
    result_text: str = ""
    target: Literal["table", "view", "data", "comment", "annotation"] = "table"


class DbAdminAiAnalysisData(BaseModel):
    """Admin SQL 実行結果の AI 分析 response。"""

    analysis: str = ""
    source: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)


class DbAdminJoinWhereRequest(BaseModel):
    """ビュー DDL の JOIN/WHERE 条件抽出 request。"""

    ddl: str = Field(min_length=1)


class DbAdminJoinWhereData(BaseModel):
    """ビュー DDL の JOIN/WHERE 条件抽出 response。"""

    join_text: str = "None"
    where_text: str = "None"
    source: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)


class Nl2SqlResult(BaseModel):
    """NL2SQL job result."""

    engine: Nl2SqlEngine
    engine_meta: dict[str, Any] = Field(default_factory=dict)
    fallback_reason: str = ""
    original_question: str
    rewritten_question: str
    generated_sql: str
    executable_sql: str
    explanation: str
    safety: SafetyReport
    recommendations: list[str] = Field(default_factory=list)
    repaired_sql: str = ""
    optimization_hints: list[str] = Field(default_factory=list)
    results: QueryResults
    timing: TimingEnvelope


class PreviewRequest(BaseModel):
    """自然言語から SQL を生成し、実行せずに safety を返す。"""

    question: str = Field(min_length=1)
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    profile_id: str | None = None
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)


class PreviewData(BaseModel):
    """Preview response.

    既存テスト互換のため sql/is_safe/row_limit/note を残し、詳細情報を足す。
    """

    sql: str
    is_safe: bool
    row_limit: int
    note: str
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    engine_meta: dict[str, Any] = Field(default_factory=dict)
    fallback_reason: str = ""
    rewritten_question: str = ""
    executable_sql: str = ""
    safety: SafetyReport | None = None
    recommendations: list[str] = Field(default_factory=list)
    repaired_sql: str = ""
    optimization_hints: list[str] = Field(default_factory=list)
    timing: TimingEnvelope | None = None


class ExecuteRequest(BaseModel):
    """SQL execution request."""

    sql: str = Field(min_length=1)
    profile_id: str | None = None
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)


class JobCreateRequest(BaseModel):
    """非同期 NL2SQL job create request."""

    question: str = Field(min_length=1)
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    profile_id: str | None = None
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)


class JobCreateData(BaseModel):
    """非同期 job create response."""

    job_id: str
    status: JobStatus
    created_at: str


class JobData(BaseModel):
    """非同期 job status response."""

    job_id: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_ms: int | None = None
    result: Nl2SqlResult | None = None
    error_message: str | None = None
    timing: TimingEnvelope | None = None


class HistoryItem(BaseModel):
    """検索履歴。"""

    id: str
    question: str
    engine: Nl2SqlEngine
    generated_sql: str
    created_at: str
    elapsed_ms: int | None = None
    feedback_rating: FeedbackRating | None = None
    profile_id: str = ""
    profile_name: str = ""
    rewritten_question: str = ""
    executable_sql: str = ""
    safety_is_safe: bool = True
    result_row_count: int = 0
    result_columns: list[str] = Field(default_factory=list)
    feedback_comment: str = ""


class HistoryData(BaseModel):
    """検索履歴 response."""

    items: list[HistoryItem]


class FeedbackRequest(BaseModel):
    """検索結果への feedback request."""

    history_id: str
    rating: FeedbackRating
    comment: str = ""


class FeedbackData(BaseModel):
    """Feedback response."""

    history_id: str
    rating: FeedbackRating
    saved: bool
    comment: str = ""


class FeedbackIndexRequest(BaseModel):
    """Feedback learning index management request."""

    execute: bool = False
    include_bad: bool = False


class FeedbackIndexData(BaseModel):
    """Feedback learning vector index status / operation response."""

    operation: str
    status: str
    executed: bool = False
    runtime: str = "deterministic"
    source_history_count: int = 0
    indexable_count: int = 0
    indexed_count: int = 0
    vector_dimension: int = 1536
    vector_backend: str = "oracle_26ai"
    embedding_provider: str = "oci_genai"
    embedding_model: str = ""
    embedding_configured: bool = False
    ddl: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timing: TimingEnvelope


class FeedbackVectorEntry(BaseModel):
    """Feedback vector/index management row shown in Learning operations."""

    history_id: str
    question: str
    generated_sql: str
    profile_id: str = ""
    profile_name: str = ""
    feedback_rating: FeedbackRating | None = None
    feedback_comment: str = ""
    indexed: bool = False
    created_at: str = ""


class FeedbackEntriesData(BaseModel):
    """Feedback management entries response."""

    items: list[FeedbackVectorEntry] = Field(default_factory=list)
    total: int = 0
    indexed_count: int = 0


class FeedbackEntriesDeleteRequest(BaseModel):
    """Delete feedback/history entries from the learning store."""

    history_ids: list[str] = Field(default_factory=list)


class FeedbackSearchConfigRequest(BaseModel):
    """Similarity search defaults used when request does not override them."""

    similarity_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    match_limit: int = Field(default=3, ge=1, le=20)


class FeedbackSearchConfigData(BaseModel):
    """Current feedback learning retrieval config."""

    similarity_threshold: float = 0.0
    match_limit: int = 3


class DemoLearningData(BaseModel):
    """Demo learning data seed response."""

    seeded_history_count: int
    seeded_feedback_count: int
    history_ids: list[str] = Field(default_factory=list)
    profile_ids: list[str] = Field(default_factory=list)
    message: str


class SimilarHistoryRequest(BaseModel):
    """類似履歴検索 request."""

    question: str = Field(min_length=1)
    profile_id: str | None = None
    limit: int | None = Field(default=None, ge=1, le=20)


class SimilarHistoryItem(BaseModel):
    """類似履歴の 1 件。"""

    item: HistoryItem
    score: float
    reason: str


class SimilarHistoryData(BaseModel):
    """類似履歴検索 response."""

    items: list[SimilarHistoryItem] = Field(default_factory=list)


class ProfileRecommendationRequest(BaseModel):
    """質問から業務 profile / schema 範囲を推薦する request."""

    question: str = Field(min_length=1)
    current_profile_id: str | None = None


class ProfileRecommendationCandidate(BaseModel):
    """Profile recommendation candidate."""

    profile_id: str
    profile_name: str
    score: float
    matched_terms: list[str] = Field(default_factory=list)
    allowed_tables: list[str] = Field(default_factory=list)
    category: str = ""


class ProfileRecommendationData(BaseModel):
    """Profile recommendation response."""

    recommended_profile_id: str
    recommended_profile_name: str
    confidence: float
    reason: str
    rewritten_question: str
    recommended_allowed_objects: AllowedObjects
    candidates: list[ProfileRecommendationCandidate] = Field(default_factory=list)
    recommendation_source: str = "deterministic"
    classifier_version: str = ""
    category_scores: dict[str, float] = Field(default_factory=dict)


class ClassifierTrainingExample(BaseModel):
    """Imported classifier training example."""

    id: str
    category: str
    text: str
    profile_id: str = ""
    source: str = ""


class ClassifierImportData(BaseModel):
    """Classifier training data import response."""

    imported_count: int
    skipped_count: int = 0
    total_examples: int
    categories: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    examples: list[ClassifierTrainingExample] = Field(default_factory=list)


class ClassifierStatusData(BaseModel):
    """Classifier training/runtime status."""

    ready: bool = False
    trained: bool = False
    classifier_version: str = ""
    updated_at: str = ""
    example_count: int = 0
    category_count: int = 0
    categories: list[str] = Field(default_factory=list)
    embedding_model: str = ""
    vector_dimension: int = 1536
    persistence_mode: str = "memory"
    recommendation_source: str = "deterministic"
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ClassifierModelInfo(BaseModel):
    """Persisted classifier model version metadata."""

    version: str
    active: bool = False
    updated_at: str = ""
    category_count: int = 0
    categories: list[str] = Field(default_factory=list)
    embedding_model: str = ""
    vector_dimension: int = 1536
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    source: str = "oracle_state"


class ClassifierModelsData(BaseModel):
    """Classifier model registry response."""

    active_version: str = ""
    models: list[ClassifierModelInfo] = Field(default_factory=list)


class ClassifierModelImportData(BaseModel):
    """Legacy joblib/meta classifier artifact import response."""

    imported: bool = False
    active_version: str = ""
    model: ClassifierModelInfo | None = None
    warnings: list[str] = Field(default_factory=list)


class ClassifierModelActivateData(BaseModel):
    """Classifier model activation response."""

    active_version: str = ""
    model: ClassifierModelInfo | None = None
    warnings: list[str] = Field(default_factory=list)


class ClassifierTrainRequest(BaseModel):
    """Train LogisticRegression classifier from imported examples."""

    min_examples_per_category: int = Field(default=1, ge=1, le=100)


class ClassifierPredictRequest(BaseModel):
    """Classifier prediction request."""

    question: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class ClassifierPredictionCandidate(BaseModel):
    """Classifier prediction candidate mapped to a profile when possible."""

    category: str
    score: float
    profile_id: str = ""
    profile_name: str = ""


class ClassifierPredictionData(BaseModel):
    """Classifier prediction response."""

    recommendation_source: str
    classifier_version: str = ""
    predicted_category: str = ""
    confidence: float = 0.0
    candidates: list[ClassifierPredictionCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RewriteRequest(BaseModel):
    """Question rewrite request."""

    question: str = Field(min_length=1)
    profile_id: str | None = None
    use_glossary: bool = True
    use_schema: bool = True
    extra_prompt: str = ""


class RewriteData(BaseModel):
    """Question rewrite response."""

    original_question: str
    rewritten_question: str
    source: str = "deterministic"
    model: str = ""
    warnings: list[str] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """SQL analysis request."""

    sql: str = Field(min_length=1)
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)
    use_llm: bool = False


class AnalyzeData(BaseModel):
    """SQL analysis response."""

    safety: SafetyReport
    explanation: str
    recommendations: list[str]
    executable_sql: str
    repaired_sql: str = ""
    optimization_hints: list[str] = Field(default_factory=list)
    structure_summary: str = ""
    risk_level: str = "low"
    statement_type: str = "SELECT"
    object_names: list[str] = Field(default_factory=list)
    column_names: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    risk_findings: list[str] = Field(default_factory=list)
    repair_candidates: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    aggregations: list[str] = Field(default_factory=list)
    llm_enhanced: bool = False
    llm_warnings: list[str] = Field(default_factory=list)


class RepairRequest(BaseModel):
    """Oracle error message を使った SQL repair request."""

    sql: str = Field(min_length=1)
    error_message: str = Field(min_length=1)
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)


class RepairData(BaseModel):
    """Oracle error-aware SQL repair response."""

    error_code: str = ""
    repaired_sql: str = ""
    explanation: str
    recommendations: list[str] = Field(default_factory=list)
    safety: SafetyReport
    executable_sql: str = ""


class SyntheticCase(BaseModel):
    """Synthetic / persisted NL2SQL evaluation case."""

    question: str
    expected_sql: str
    profile_id: str = "default"


class EvaluateRequest(BaseModel):
    """Deterministic NL2SQL evaluation request."""

    cases: list[dict[str, str]] = Field(default_factory=list)
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    profile_id: str | None = None
    evaluation_set_id: str | None = None


class EvaluateData(BaseModel):
    """Evaluation response."""

    evaluation_suite: str
    total_cases: int
    executable_rate: float
    select_only_rate: float
    findings: list[str]


class AssetRefreshData(BaseModel):
    """Select AI / Agent asset refresh response."""

    engine: Nl2SqlEngine
    refreshed: bool
    status: str = "ready"
    refreshed_at: str = ""
    profile_name: str = ""
    team_name: str = ""
    warning: str = ""
    asset_names: dict[str, str] = Field(default_factory=dict)
    engine_meta: dict[str, Any] = Field(default_factory=dict)


class AssetCleanupData(BaseModel):
    """Select AI / Agent asset cleanup response."""

    engine: Nl2SqlEngine
    executed: bool
    status: str = "dry_run"
    cleaned_at: str = ""
    profile_name: str = ""
    team_name: str = ""
    warning: str = ""
    asset_names: dict[str, str] = Field(default_factory=dict)
    engine_meta: dict[str, Any] = Field(default_factory=dict)


class AssetCleanupRequest(BaseModel):
    """Select AI / Agent asset cleanup request."""

    profile_id: str | None = None
    engines: list[Nl2SqlEngine] = Field(
        default_factory=lambda: [Nl2SqlEngine.SELECT_AI_AGENT, Nl2SqlEngine.SELECT_AI]
    )
    execute: bool = False
    confirmation: str = ""
    reason: str = ""


class SelectAiDbProfile(BaseModel):
    """Oracle DBMS_CLOUD_AI profile metadata."""

    name: str
    status: str = "unknown"
    owner: str = ""
    created_at: str = ""
    description: str = ""
    category: str = ""
    object_list: list[dict[str, Any]] = Field(default_factory=list)
    schema_text: str = ""
    context_ddl: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class SelectAiDbProfilesData(BaseModel):
    """Oracle Select AI profile list response."""

    runtime: str = "deterministic"
    profiles: list[SelectAiDbProfile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SelectAiDbProfileDropRequest(BaseModel):
    """Drop an Oracle Select AI profile by exact profile name."""

    execute: bool = False
    confirmation: str = ""
    reason: str = ""


class SelectAiDbProfileDetailData(BaseModel):
    """Oracle Select AI profile detail response."""

    runtime: str = "deterministic"
    profile: SelectAiDbProfile
    warnings: list[str] = Field(default_factory=list)


class SelectAiDbProfileUpsertRequest(AdminExecutionConfirmation):
    """Create/update an Oracle Select AI profile from low-level attributes JSON."""

    profile_name: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    category: str = ""
    original_name: str = ""


class SelectAiDbProfileMutationData(BaseModel):
    """Oracle Select AI profile create/update/import/export mutation response."""

    runtime: str = "deterministic"
    executed: bool = False
    status: str = "dry_run"
    profile_name: str = ""
    original_name: str = ""
    ddl: list[str] = Field(default_factory=list)
    profile: SelectAiDbProfile | None = None
    warnings: list[str] = Field(default_factory=list)
    engine_meta: dict[str, Any] = Field(default_factory=dict)


class SelectAiProfilesExportData(BaseModel):
    """Select AI profiles JSON export response."""

    profiles: list[SelectAiDbProfile] = Field(default_factory=list)
    exported_at: str = ""


class SelectAiProfilesImportRequest(AdminExecutionConfirmation):
    """Import Select AI profile JSON definitions."""

    profiles: list[SelectAiDbProfile] = Field(default_factory=list)
    replace_existing: bool = False


class SelectAiFeedbackEntry(BaseModel):
    """Oracle Select AI feedback vector table row."""

    content: str = ""
    sql_id: str = ""
    sql_text: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw_attributes: str = ""


class SelectAiFeedbackEntriesData(BaseModel):
    """Oracle Select AI feedback management list response."""

    runtime: str = "deterministic"
    profile_name: str = ""
    index_name: str = ""
    table_name: str = ""
    items: list[SelectAiFeedbackEntry] = Field(default_factory=list)
    total: int = 0
    warnings: list[str] = Field(default_factory=list)


class SelectAiFeedbackDeleteRequest(BaseModel):
    """Delete one Oracle Select AI feedback entry by SQL text."""

    profile_name: str = Field(min_length=1)
    sql_text: str = Field(min_length=1)


class SelectAiFeedbackVectorIndexRequest(BaseModel):
    """Update Oracle Select AI feedback vector index attributes."""

    profile_name: str = Field(min_length=1)
    similarity_threshold: float = Field(default=0.9, ge=0.1, le=0.95)
    match_limit: int = Field(default=3, ge=1, le=5)


class SelectAiFeedbackMutationData(BaseModel):
    """Oracle Select AI feedback mutation response."""

    runtime: str = "deterministic"
    executed: bool = False
    status: str = "dry_run"
    profile_name: str = ""
    index_name: str = ""
    table_name: str = ""
    warnings: list[str] = Field(default_factory=list)
    engine_meta: dict[str, Any] = Field(default_factory=dict)


class AgentTeamRunRequest(BaseModel):
    """Select AI Agent team run request."""

    prompt: str = Field(min_length=1)
    team_name: str = ""
    profile_id: str | None = None
    conversation_id: str = ""
    tool_name: str = ""


class AgentTeamRunData(BaseModel):
    """Select AI Agent team run response."""

    team_name: str
    prompt: str
    generated_sql: str = ""
    conversation_id: str = ""
    runtime: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)
    engine_meta: dict[str, Any] = Field(default_factory=dict)


class SelectAiAgentAsset(BaseModel):
    """Select AI Agent asset names and attributes."""

    profile_id: str = ""
    profile_name: str = ""
    tool_name: str = ""
    agent_name: str = ""
    task_name: str = ""
    team_name: str = ""
    source: str = "state"
    attributes: dict[str, Any] = Field(default_factory=dict)


class SelectAiAgentAssetsData(BaseModel):
    """Select AI Agent assets response."""

    runtime: str = "deterministic"
    items: list[SelectAiAgentAsset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentToolRunRequest(BaseModel):
    """Select AI Agent tool run request."""

    prompt: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    conversation_id: str = ""


class AgentConversationCreateRequest(BaseModel):
    """Create Select AI Agent conversation request."""

    profile_id: str | None = None
    team_name: str = ""


class AgentConversationCreateData(BaseModel):
    """Create Select AI Agent conversation response."""

    conversation_id: str = ""
    runtime: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)


class AgentConversationItem(BaseModel):
    """Select AI Agent conversation prompt item."""

    conversation_id: str
    prompt: str
    response: str = ""
    created_at: str = ""
    team_name: str = ""


class AgentConversationsData(BaseModel):
    """Select AI Agent conversation history response."""

    runtime: str = "deterministic"
    items: list[AgentConversationItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CompareRequest(BaseModel):
    """Select AI と Select AI Agent の比較 request."""

    question: str = Field(min_length=1)
    profile_id: str | None = None
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)
    execute: bool = False
    engines: list[Nl2SqlEngine] = Field(
        default_factory=lambda: [Nl2SqlEngine.SELECT_AI_AGENT, Nl2SqlEngine.SELECT_AI]
    )


class CompareExecutionData(BaseModel):
    """Engine comparison execution result."""

    engine: Nl2SqlEngine
    executed: bool
    row_count: int = 0
    error_message: str = ""
    results: QueryResults | None = None
    elapsed_ms: int | None = None


class CompareData(BaseModel):
    """Engine comparison response."""

    question: str
    results: list[PreviewData]
    execution_results: list[CompareExecutionData] = Field(default_factory=list)
    error_rate: float = 0.0
    recommendation: str


class CompareRecord(BaseModel):
    """Persisted engine comparison record for evaluation operations."""

    id: str
    created_at: str
    profile_id: str = ""
    profile_name: str = ""
    question: str
    engines: list[Nl2SqlEngine] = Field(default_factory=list)
    execute: bool = False
    report: str = ""
    comparison: CompareData


class CompareHistoryData(BaseModel):
    """Recent engine comparison records."""

    items: list[CompareRecord] = Field(default_factory=list)


class EvaluationSet(BaseModel):
    """Persisted deterministic NL2SQL evaluation set."""

    id: str
    name: str
    description: str = ""
    profile_id: str = "default"
    profile_name: str = ""
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    cases: list[SyntheticCase] = Field(default_factory=list)
    created_at: str
    updated_at: str
    archived: bool = False


class EvaluationSetUpsertRequest(BaseModel):
    """Evaluation set create/update request."""

    name: str = Field(min_length=1)
    description: str = ""
    profile_id: str | None = None
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    cases: list[SyntheticCase] = Field(default_factory=list)


class EvaluationSetsData(BaseModel):
    """Evaluation set list response."""

    items: list[EvaluationSet] = Field(default_factory=list)


class EvaluationRunRecord(BaseModel):
    """Persisted deterministic NL2SQL evaluation run result."""

    id: str
    created_at: str
    evaluation_set_id: str = ""
    evaluation_set_name: str = ""
    profile_id: str = ""
    profile_name: str = ""
    engine: Nl2SqlEngine = Nl2SqlEngine.AUTO
    cases: list[SyntheticCase] = Field(default_factory=list)
    result: EvaluateData
    report: str = ""


class EvaluationRunsData(BaseModel):
    """Recent evaluation run records."""

    items: list[EvaluationRunRecord] = Field(default_factory=list)


class ReverseSqlRequest(BaseModel):
    """SQL から自然言語説明を生成する request."""

    sql: str = Field(min_length=1)
    profile_id: str | None = None
    use_glossary: bool = False


class ReverseSqlData(BaseModel):
    """SQL reverse explanation response."""

    question: str
    explanation: str
    referenced_tables: list[str]
    logical_structure: str = ""
    logical_steps: list[str] = Field(default_factory=list)
    source: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)


class CommentSuggestion(BaseModel):
    """Table / column comment suggestion."""

    object_name: str
    object_type: str
    suggested_comment: str


class CommentSuggestionRequest(BaseModel):
    """Comment generation options."""

    use_llm: bool = False
    max_items: int = Field(default=120, ge=1, le=500)


class CommentSuggestionData(BaseModel):
    """Comment suggestions response."""

    suggestions: list[CommentSuggestion]
    source: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)


class CommentApplyItem(BaseModel):
    """Table / column comment apply request item."""

    object_name: str = Field(min_length=1, max_length=260)
    object_type: str = Field(default="column", min_length=1, max_length=16)
    comment: str = Field(min_length=1, max_length=4000)


class CommentApplyRequest(BaseModel):
    """Restricted COMMENT ON dry-run / execution request."""

    items: list[CommentApplyItem] = Field(default_factory=list)
    execute: bool = False
    confirmation: str = ""
    reason: str = ""


class CommentApplyStatement(BaseModel):
    """Generated COMMENT ON statement result."""

    object_name: str
    object_type: str
    comment: str
    sql: str
    status: str = "dry_run"
    error_message: str = ""


class CommentApplyData(BaseModel):
    """Restricted COMMENT ON dry-run / execution response."""

    executed: bool = False
    runtime: str = "deterministic"
    statements: list[CommentApplyStatement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timing: TimingEnvelope


class AnnotationSuggestion(BaseModel):
    """Oracle 23ai annotation suggestion for a table/view/column."""

    object_name: str
    object_type: str = "table"
    annotation_name: str = "Display"
    annotation_value: str


class AnnotationSuggestionData(BaseModel):
    """Annotation suggestions response."""

    suggestions: list[AnnotationSuggestion] = Field(default_factory=list)
    source: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)


class AnnotationApplyItem(BaseModel):
    """Annotation apply request item."""

    object_name: str = Field(min_length=1, max_length=260)
    object_type: str = Field(default="table", min_length=1, max_length=16)
    annotation_name: str = Field(default="Display", min_length=1, max_length=64)
    annotation_value: str = Field(min_length=1, max_length=4000)


class AnnotationApplyRequest(BaseModel):
    """Restricted Oracle annotation dry-run / execution request."""

    items: list[AnnotationApplyItem] = Field(default_factory=list)
    execute: bool = False
    confirmation: str = ""
    reason: str = ""


class AnnotationApplyStatement(BaseModel):
    """Generated Oracle annotation statement result."""

    object_name: str
    object_type: str
    annotation_name: str
    annotation_value: str
    sql: str
    status: str = "dry_run"
    error_message: str = ""


class AnnotationApplyData(BaseModel):
    """Oracle annotation apply response."""

    executed: bool = False
    runtime: str = "deterministic"
    statements: list[AnnotationApplyStatement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timing: TimingEnvelope


class MetadataSqlTarget(BaseModel):
    """Comment / annotation generation target object."""

    object_name: str = Field(min_length=1, max_length=260)
    object_type: Literal["table", "view"] = "table"


class MetadataSqlGenerateRequest(BaseModel):
    """SQL Assist comment / annotation generation input."""

    targets: list[MetadataSqlTarget] = Field(default_factory=list)
    structure_text: str = ""
    primary_key_text: str = ""
    foreign_key_text: str = ""
    sample_text: str = ""
    extra_text: str = ""


class MetadataSqlGenerateData(BaseModel):
    """Generated comment / annotation SQL."""

    sql: str = ""
    source: str = "deterministic"
    warnings: list[str] = Field(default_factory=list)
    timing: TimingEnvelope


class SyntheticCasesData(BaseModel):
    """Synthetic cases response."""

    cases: list[SyntheticCase]


class SyntheticDataGenerateRequest(BaseModel):
    """DBMS_CLOUD_AI synthetic table data generation request."""

    table_name: str = ""
    object_list: list[str] = Field(default_factory=list)
    row_count: int = Field(default=10, ge=1, le=10000)
    rows_per_table: int | None = Field(default=None, ge=1, le=10000)
    profile_id: str | None = None
    profile_name: str = ""
    user_prompt: str = ""
    extra_prompt: str = ""
    sample_rows: int = Field(default=0, ge=0, le=100)
    use_comments: bool = True
    execute: bool = False
    confirmation: str = ""
    reason: str = ""


class SyntheticDataOperationData(BaseModel):
    """Synthetic DB data generation operation response."""

    operation_id: str = ""
    table_name: str
    object_list: list[str] = Field(default_factory=list)
    row_count: int
    executed: bool = False
    runtime: str = "deterministic"
    status: str = "dry_run"
    message: str = ""
    warnings: list[str] = Field(default_factory=list)
    engine_meta: dict[str, Any] = Field(default_factory=dict)
    timing: TimingEnvelope


class SyntheticDataOperationStatusData(BaseModel):
    """Synthetic DB data generation operation status."""

    operation_id: str
    runtime: str = "deterministic"
    status: str = "unknown"
    message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class SyntheticDataResultsData(BaseModel):
    """Synthetic DB data result preview from a generated table."""

    table_name: str
    runtime: str = "deterministic"
    results: QueryResults
    warnings: list[str] = Field(default_factory=list)


class DiagnosticCheck(BaseModel):
    """接続/設定診断の 1 項目。"""

    name: str
    status: str
    message: str


class AgentPrivilegeCheckData(BaseModel):
    """Select AI Agent privilege / dictionary-view readiness check response."""

    runtime: str = "deterministic"
    status: str = "warning"
    checks: list[DiagnosticCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DiagnosticReadiness(BaseModel):
    """運用 readiness の集約表示。"""

    area: str
    label: str
    status: str
    summary: str
    next_action: str = ""
    related_checks: list[str] = Field(default_factory=list)


class DiagnosticSmokeCheck(BaseModel):
    """Manual/live smoke check item for Oracle / OCI NL2SQL engines."""

    id: str
    label: str
    category: str
    status: str
    method: str = ""
    endpoint: str = ""
    request_hint: str = ""
    command: str = ""
    expected: str
    next_action: str = ""
    related_readiness: list[str] = Field(default_factory=list)


class DiagnosticConfigVar(BaseModel):
    """診断設定ガイドで表示する env var。値は返さず状態だけ返す。"""

    name: str
    status: str
    required: bool = True
    note: str = ""


class DiagnosticConfigGuide(BaseModel):
    """OCI / Oracle 設定を完了するための非 secret ガイド。"""

    id: str
    label: str
    status: str
    summary: str
    next_action: str = ""
    required_env_vars: list[DiagnosticConfigVar] = Field(default_factory=list)
    optional_env_vars: list[DiagnosticConfigVar] = Field(default_factory=list)
    env_template: str = ""
    smoke_command: str = ""
    related_readiness: list[str] = Field(default_factory=list)


class DiagnosticsData(BaseModel):
    """OCI / Oracle / engine 設定診断 response."""

    checks: list[DiagnosticCheck]
    readiness: list[DiagnosticReadiness] = Field(default_factory=list)
    smoke_checks: list[DiagnosticSmokeCheck] = Field(default_factory=list)
    config_guides: list[DiagnosticConfigGuide] = Field(default_factory=list)


class CsvImportRequest(BaseModel):
    """CSV sample data import request.

    `execute=false` は dry-run として DDL / parsed rows preview だけを返す。
    `execute=true` でも Oracle runtime 以外では dry-run に縮退する。
    """

    table_name: str = Field(min_length=1)
    csv_text: str = Field(min_length=1)
    replace_existing: bool = False
    execute: bool = False
    max_rows: int | None = Field(default=None, ge=1, le=50000)


class CsvImportColumn(BaseModel):
    """CSV import column mapping."""

    source_name: str
    column_name: str
    data_type: str
    nullable: bool = True


class CsvImportData(BaseModel):
    """CSV import dry-run / execution response."""

    table_name: str
    columns: list[CsvImportColumn]
    row_count: int
    dry_run: bool
    executed: bool
    ddl: str
    insert_sql: str
    warnings: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, str | None]] = Field(default_factory=list)
    timing: TimingEnvelope


class DbAdminImportTabularData(BaseModel):
    """Tabular import preview / execution response."""

    table_name: str
    filename: str = ""
    sheet_name: str = ""
    mode: str = "create"
    columns: list[CsvImportColumn]
    row_count: int
    dry_run: bool
    executed: bool
    ddl: str
    insert_sql: str
    warnings: list[str] = Field(default_factory=list)
    sample_rows: list[dict[str, str | None]] = Field(default_factory=list)
    timing: TimingEnvelope
