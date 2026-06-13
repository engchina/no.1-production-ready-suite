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
    passed: bool = True
    threshold_failures: list["EvaluationThresholdFailure"] = Field(default_factory=list)
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
    guardrail_warnings: list[str] = Field(default_factory=list)
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)
    elapsed_ms: float
    error_type: str | None = None
    error_message: str | None = None


EvaluationMetricName = Literal[
    "precision_at_k",
    "recall_at_k",
    "mrr",
    "answer_keyword_hit_rate",
]


class EvaluationThresholds(BaseModel):
    """CI gate に使う aggregate metric の最低値。"""

    precision_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    recall_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    mrr: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_keyword_hit_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class EvaluationThresholdFailure(BaseModel):
    """閾値を下回った aggregate metric。"""

    metric: EvaluationMetricName
    actual: float
    threshold: float


class EvaluationRunRequest(BaseModel):
    """評価実行リクエスト。"""

    cases: list[EvaluationCase] = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=50)
    mode: SearchMode = SearchMode.HYBRID
    filters: dict[str, str] = Field(default_factory=dict)
    thresholds: EvaluationThresholds | None = None

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
