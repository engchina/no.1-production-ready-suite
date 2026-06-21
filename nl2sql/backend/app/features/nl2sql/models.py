"""NL2SQL API models.

外部サービスへ依存しない契約をここに集約する。Oracle / OCI の実呼び出しは
service 層の adapter に閉じ込め、API と UI は同じ shape を使い続ける。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

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
    description: str = ""
    allowed_tables: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    sql_rules: list[str] = Field(default_factory=list)
    default_row_limit: int = Field(default=100, ge=1, le=5000)
    safety_policy: str = "select_only"
    few_shot_examples: list[dict[str, str]] = Field(default_factory=list)


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
    limit: int = Field(default=3, ge=1, le=10)


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


class ProfileRecommendationData(BaseModel):
    """Profile recommendation response."""

    recommended_profile_id: str
    recommended_profile_name: str
    confidence: float
    reason: str
    rewritten_question: str
    recommended_allowed_objects: AllowedObjects
    candidates: list[ProfileRecommendationCandidate] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """SQL analysis request."""

    sql: str = Field(min_length=1)
    allowed_objects: AllowedObjects = Field(default_factory=AllowedObjects)
    row_limit: int | None = Field(default=None, ge=1, le=5000)


class AnalyzeData(BaseModel):
    """SQL analysis response."""

    safety: SafetyReport
    explanation: str
    recommendations: list[str]
    executable_sql: str
    repaired_sql: str = ""
    optimization_hints: list[str] = Field(default_factory=list)


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


class ReverseSqlData(BaseModel):
    """SQL reverse explanation response."""

    question: str
    explanation: str
    referenced_tables: list[str]


class CommentSuggestion(BaseModel):
    """Table / column comment suggestion."""

    object_name: str
    object_type: str
    suggested_comment: str


class CommentSuggestionData(BaseModel):
    """Comment suggestions response."""

    suggestions: list[CommentSuggestion]


class CommentApplyItem(BaseModel):
    """Table / column comment apply request item."""

    object_name: str = Field(min_length=1, max_length=260)
    object_type: str = Field(default="column", min_length=1, max_length=16)
    comment: str = Field(min_length=1, max_length=4000)


class CommentApplyRequest(BaseModel):
    """Restricted COMMENT ON dry-run / execution request."""

    items: list[CommentApplyItem] = Field(default_factory=list)
    execute: bool = False


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


class SyntheticCasesData(BaseModel):
    """Synthetic cases response."""

    cases: list[SyntheticCase]


class DiagnosticCheck(BaseModel):
    """接続/設定診断の 1 項目。"""

    name: str
    status: str
    message: str


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
