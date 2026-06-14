"""RAG 評価スキーマ。"""

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.search import (
    SearchDiagnostics,
    SearchMode,
    normalize_query_text,
    normalize_search_filters,
    validate_rerank_top_n,
)

EvaluationFailureReason = Literal[
    "retrieval_miss",
    "partial_recall",
    "unexpected_retrieval",
    "answer_keyword_miss",
    "low_groundedness",
    "guardrail_warning",
    "case_error",
]


class EvaluationCase(BaseModel):
    """1 件の評価ケース。"""

    id: str
    query: str = Field(..., min_length=1)
    relevant_document_ids: list[str] = Field(default_factory=list)
    expected_answer_keywords: list[str] = Field(default_factory=list)

    @field_validator("query")
    @classmethod
    def validate_query(cls, query: str) -> str:
        """SearchRequest と同じ規則で query を正規化する。"""
        return normalize_query_text(query)


class EvaluationMetrics(BaseModel):
    """評価結果の集計指標。"""

    case_count: int
    error_count: int = 0
    evaluated_k: int
    precision_at_k: float
    recall_at_k: float
    mrr: float
    answer_keyword_hit_rate: float
    groundedness_pass_rate: float
    passed: bool = True
    threshold_failures: list["EvaluationThresholdFailure"] = Field(default_factory=list)
    failure_reason_counts: dict[EvaluationFailureReason, int] = Field(default_factory=dict)
    case_results: list["EvaluationCaseResult"] = Field(default_factory=list)


class EvaluationCaseResult(BaseModel):
    """1 評価ケースごとの診断結果。"""

    case_id: str
    trace_id: str
    status: Literal["success", "error"] = "success"
    retrieved_document_ids: list[str] = Field(default_factory=list)
    relevant_document_ids: list[str] = Field(default_factory=list)
    hit_document_ids: list[str] = Field(default_factory=list)
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank: float
    answer_keyword_hit: bool
    groundedness_passed: bool
    groundedness_score: float
    grounding_overlap_count: int = 0
    grounding_answer_feature_count: int = 0
    guardrail_warnings: list[str] = Field(default_factory=list)
    failure_reasons: list[EvaluationFailureReason] = Field(default_factory=list)
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)
    elapsed_ms: float
    error_type: str | None = None
    error_message: str | None = None


EvaluationMetricName = Literal[
    "precision_at_k",
    "recall_at_k",
    "mrr",
    "answer_keyword_hit_rate",
    "groundedness_pass_rate",
]


class EvaluationThresholds(BaseModel):
    """CI gate に使う aggregate metric の最低値。"""

    precision_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    recall_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    mrr: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_keyword_hit_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    groundedness_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class EvaluationThresholdFailure(BaseModel):
    """閾値を下回った aggregate metric。"""

    metric: EvaluationMetricName
    actual: float
    threshold: float


class EvaluationRagOverrides(BaseModel):
    """評価 experiment ごとに一時適用する非 secret RAG 設定。"""

    rrf_k: int | None = Field(default=None, ge=1, le=1000)
    query_expansion_enabled: bool | None = None
    query_expansion_max_variants: int | None = Field(default=None, ge=1, le=8)
    context_window_chars: int | None = Field(default=None, ge=1000, le=100000)
    context_neighbor_window: int | None = Field(default=None, ge=0, le=5)
    context_diversity_lambda: float | None = Field(default=None, ge=0.0, le=1.0)
    context_group_expansion_enabled: bool | None = None
    context_group_max_chunks: int | None = Field(default=None, ge=1, le=20)
    context_compression_enabled: bool | None = None
    context_compression_max_sentences: int | None = Field(default=None, ge=1, le=10)
    context_compression_max_chars_per_chunk: int | None = Field(
        default=None,
        ge=200,
        le=8000,
    )
    oracle_vector_target_accuracy: int | None = Field(default=None, ge=1, le=100)


class EvaluationExperiment(BaseModel):
    """AutoRAG 風に比較する 1 つの検索設定。"""

    id: str = Field(..., min_length=1, max_length=80)
    top_k: int = Field(default=10, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=50)
    mode: SearchMode = SearchMode.HYBRID
    filters: dict[str, str] = Field(default_factory=dict)
    rag_overrides: EvaluationRagOverrides | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """比較結果で安全に表示できる短い ID に正規化する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("experiment id を入力してください。")
        return cleaned

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, filters: dict[str, str]) -> dict[str, str]:
        """検索評価に使う filters を SearchRequest と同じ規則で正規化する。"""
        return normalize_search_filters(filters)

    @model_validator(mode="after")
    def validate_rerank_depth(self) -> Self:
        """SearchRequest と同じ rerank 深さ制約を適用する。"""
        validate_rerank_top_n(self.top_k, self.rerank_top_n)
        return self


class EvaluationExperimentResult(BaseModel):
    """1 experiment の評価結果と ranking 情報。"""

    rank: int
    ranking_score: float
    experiment: EvaluationExperiment
    metrics: EvaluationMetrics


class EvaluationCompareResponse(BaseModel):
    """複数 experiment の比較結果。"""

    ranking_metric: EvaluationMetricName
    best_experiment_id: str | None
    results: list[EvaluationExperimentResult] = Field(default_factory=list)


class EvaluationCompareRequest(BaseModel):
    """複数検索設定の比較実行リクエスト。"""

    cases: list[EvaluationCase] = Field(..., min_length=1)
    experiments: list[EvaluationExperiment] = Field(..., min_length=1, max_length=20)
    ranking_metric: EvaluationMetricName = "mrr"
    thresholds: EvaluationThresholds | None = None

    @model_validator(mode="after")
    def validate_unique_experiment_ids(self) -> Self:
        """比較結果の識別を安定させるため experiment id の重複を拒否する。"""
        ids = [experiment.id for experiment in self.experiments]
        duplicates = sorted(
            {experiment_id for experiment_id in ids if ids.count(experiment_id) > 1}
        )
        if duplicates:
            raise ValueError(f"experiment id が重複しています: {', '.join(duplicates)}")
        return self


class EvaluationRunRequest(BaseModel):
    """評価実行リクエスト。"""

    cases: list[EvaluationCase] = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=50)
    mode: SearchMode = SearchMode.HYBRID
    filters: dict[str, str] = Field(default_factory=dict)
    thresholds: EvaluationThresholds | None = None
    rag_overrides: EvaluationRagOverrides | None = None

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, filters: dict[str, str]) -> dict[str, str]:
        """検索評価に使う filters を SearchRequest と同じ規則で正規化する。"""
        return normalize_search_filters(filters)

    @model_validator(mode="after")
    def validate_rerank_depth(self) -> Self:
        """SearchRequest と同じ rerank 深さ制約を適用する。"""
        validate_rerank_top_n(self.top_k, self.rerank_top_n)
        return self
