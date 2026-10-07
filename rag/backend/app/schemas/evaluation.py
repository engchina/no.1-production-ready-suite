"""RAG 評価スキーマ。"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import AnswerFlow, EvaluationSuite, QueryStrategy
from app.schemas.search import (
    SearchDiagnostics,
    format_search_id_filter,
    normalize_query_text,
    normalize_search_filters,
    normalize_search_id_list,
    parse_search_id_filter,
)

EvaluationSuiteName = EvaluationSuite

# 評価ケースの id の長さの上限(文字)。job の `current_case_id` の列(VARCHAR2(200 CHAR))。
EVALUATION_CASE_ID_MAX_CHARS = 200

# 標準回答の長さの上限(文字)。標準回答による評価の入力の予算(48,000 bytes)に収まる長さにする。
STANDARD_ANSWER_MAX_CHARS = 8000

# 評価のケースの分類（#1226。業務支援の改修 #1218 の評価の契約）。答えられるかの種類で結果を
# 分けて見る。
# - document_answerable: 資料だけで答えられる
# - clarification_required: 利用者に条件を確かめる必要がある
# - environment_data_required: 現場の値・記録（参照の道具）が要る
# - knowledge_missing: 資料に答えが無い（拒答・人への引き継ぎが正しい）
# - conflicting_sources: 資料どうしが矛盾する
EvaluationCaseCategory = Literal[
    "document_answerable",
    "clarification_required",
    "environment_data_required",
    "knowledge_missing",
    "conflicting_sources",
]
EVALUATION_UNCATEGORIZED = "uncategorized"

# 回答の対応（#1231。業務支援の評価）。評価のケースは受け入れる対応を複数持てる
# （例: 確認が要る質問は、確認の質問か、条件ごとに分けた回答のどちらでもよい）。
# - answered: 資料にもとづいて答えた
# - conditional: 条件・不足を示して答えた（一部に確認が要る）
# - needs_clarification: 利用者に条件を確かめた
# - needs_environment_data: 現場の値・記録が要ると示した
# - needs_human: 人への引き継ぎを示した
# - insufficient_evidence: 資料から答えられないと示した（拒答）
EvaluationOutcome = Literal[
    "answered",
    "conditional",
    "needs_clarification",
    "needs_environment_data",
    "needs_human",
    "insufficient_evidence",
]

# 失敗理由(#591)。保存済みの結果には削除した理由(content_kind_miss など)が残るため、
# 結果の model は str で受ける。
EvaluationFailureReason = Literal[
    "retrieval_miss",
    "partial_recall",
    "unexpected_answer",
    "unexpected_refusal",
    "answer_keyword_miss",
    "low_groundedness",
    "unsupported_claim",
    "missing_content",
    "answer_failed",
    "answer_evaluation_error",
    "guardrail_warning",
    "unexpected_handling",
    "step_missing",
    "forbidden_action",
    "condition_missing",
    "case_error",
]

# 評価の指標(#591)。検索・根拠・回答の 3 つの観点に整理した 9 つと、業務支援の対応の 4 つ（#1231）。
EvaluationMetricName = Literal[
    "context_recall",
    "mrr",
    "faithfulness",
    "citation_traceability_coverage",
    "claim_support_rate",
    "answer_keyword_hit_rate",
    "refusal_accuracy",
    "requirement_coverage",
    "answer_pass_rate",
    "handling_accuracy",
    "step_order_score",
    "safe_answer_rate",
    "condition_coverage",
]
EVALUATION_METRIC_NAMES: tuple[EvaluationMetricName, ...] = (
    "context_recall",
    "mrr",
    "faithfulness",
    "citation_traceability_coverage",
    "claim_support_rate",
    "answer_keyword_hit_rate",
    "refusal_accuracy",
    "requirement_coverage",
    "answer_pass_rate",
    "handling_accuracy",
    "step_order_score",
    "safe_answer_rate",
    "condition_coverage",
)


class EvaluationCase(BaseModel):
    """1 件の評価ケース。

    `answerable=false` のケース(資料に答えが無い質問)は、拒答の正しさだけを測る。省略したときは、
    正解の文書・期待する語・標準回答のどれも無いケースを答えるべきでない質問とみなす(#301)。
    `standard_answer` があるケースは、回答を標準回答と LLM で比較する(4 軸の採点・主張の監査・
    必要な項目の網羅)。削除した期待値の欄(`expected_content_kind`・`expected_section_paths`。
    #591)は、既存の golden set を読めるように無視する。
    """

    # 結果の表・job の実行中のケース（`rag_evaluation_jobs.current_case_id` は 200 文字）で
    # ケースを区別するため、空でない 200 文字までにする（#977）。
    id: str = Field(..., min_length=1, max_length=EVALUATION_CASE_ID_MAX_CHARS)
    query: str = Field(..., min_length=1)
    relevant_document_ids: list[str] = Field(default_factory=list)
    expected_answer_keywords: list[str] = Field(default_factory=list)
    standard_answer: str | None = Field(default=None, max_length=STANDARD_ANSWER_MAX_CHARS)
    answerable: bool | None = None
    # 分類（任意。#1226）。結果の分類ごとの内訳に使う。採点の方法は変えない。
    category: EvaluationCaseCategory | None = None
    # 業務支援の採点（任意。#1231）。どれも空ならその指標の対象外。
    # 受け入れる回答の対応（1 つでも一致すれば正しい）。
    expected_outcomes: list[EvaluationOutcome] = Field(default_factory=list, max_length=6)
    # 回答に出るべき手順の語（期待の順）。網羅と順序を採点する。
    expected_steps: list[str] = Field(default_factory=list, max_length=30)
    # 勧めてはいけない操作の表現（影響範囲を広げる操作など）。回答に含まれたら危険な回答。
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=30)
    # 回答が触れるべき条件（確かめる条件・適用の前提）。
    required_conditions: list[str] = Field(default_factory=list, max_length=30)

    @property
    def expects_answer(self) -> bool:
        """答えるべき質問か(明示が無ければ、期待する内容を 1 つでも持つか)。"""
        if self.answerable is not None:
            return self.answerable
        return bool(
            self.relevant_document_ids or self.expected_answer_keywords or self.standard_answer
        )

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        """前後の空白を除き、空の id を拒否する。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("評価ケースの id を入力してください。")
        return cleaned

    @field_validator("query")
    @classmethod
    def validate_query(cls, query: str) -> str:
        """SearchRequest と同じ規則で query を正規化する。"""
        return normalize_query_text(query)

    @field_validator("expected_steps", "forbidden_phrases", "required_conditions")
    @classmethod
    def validate_phrases(cls, value: list[str]) -> list[str]:
        """前後の空白を除き、空の語を捨てる。"""
        return [item.strip() for item in value if item.strip()]

    @field_validator("standard_answer")
    @classmethod
    def validate_standard_answer(cls, value: str | None) -> str | None:
        """空白だけの標準回答は無いものとして扱う。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class EvaluationAnswerJudgement(BaseModel):
    """標準回答による評価(評価の基準の指標と閾値による合否・主張の監査・必要な項目の網羅)の要約。

    詳細(指標ごとの値・主張ごとの判定)は trace_id の回答の記録に保存する(#680)。`status` は
    completed / error / input_too_large / timeout / unavailable(評価の記録が無いなど)。
    """

    status: str
    passed: bool | None = None
    claims_supported: bool | None = None
    requirement_coverage: float | None = None
    # 原質問に必要な項目のうち、説明の無い項目があったか(rag_poc の「必要内容欠落」)。
    missing_content: bool | None = None
    message: str | None = None


class EvaluationCaseResult(BaseModel):
    """1 評価ケースごとの診断結果。測れない指標は None。"""

    case_id: str
    trace_id: str
    category: EvaluationCaseCategory | None = None
    status: Literal["success", "error"] = "success"
    retrieved_document_ids: list[str] = Field(default_factory=list)
    relevant_document_ids: list[str] = Field(default_factory=list)
    hit_document_ids: list[str] = Field(default_factory=list)
    context_recall: float | None = None
    reciprocal_rank: float | None = None
    faithfulness: float | None = None
    grounding_overlap_count: int = 0
    grounding_answer_feature_count: int = 0
    citation_traceability_coverage: float | None = None
    answer_keyword_hit: bool | None = None
    # 回答が「資料から答えられない」旨だけだったか(拒答)と、それが期待どおりだったか。
    abstained: bool | None = None
    refusal_correct: bool | None = None
    # 業務支援の採点（#1231）。ケースに期待が無い項目は None / 空。
    observed_outcome: EvaluationOutcome | None = None
    # explicit（回答の記録が対応を持っていた）/ inferred（診断から推定した）
    outcome_source: str | None = None
    handling_correct: bool | None = None
    step_order_score: float | None = None
    missing_steps: list[str] = Field(default_factory=list)
    # 勧めてはいけない操作の表現を照合したか（ケースに forbidden_phrases があったか）。
    forbidden_checked: bool = False
    forbidden_hits: list[str] = Field(default_factory=list)
    condition_coverage: float | None = None
    missing_conditions: list[str] = Field(default_factory=list)
    answer_evaluation: EvaluationAnswerJudgement | None = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)
    elapsed_ms: float
    error_type: str | None = None
    # 時間切れになった工程（進捗の stage と同じ名前。例: answer）。
    # 時間切れ以外は None（#383）。
    error_stage: str | None = None
    error_message: str | None = None


class EvaluationThresholdFailure(BaseModel):
    """閾値を下回った aggregate metric。保存済みの結果の削除した指標も読めるよう str で受ける。"""

    metric: str
    actual: float
    threshold: float


class EvaluationCategorySummary(BaseModel):
    """分類ごとの結果の内訳（#1226）。率は測れるケースだけの割合で、測れなければ None。"""

    case_count: int
    error_count: int = 0
    answer_pass_rate: float | None = None
    answer_keyword_hit_rate: float | None = None
    # 回答が「資料から答えられない」旨だけだった割合と、拒答の判断が期待どおりだった割合。
    abstain_rate: float | None = None
    refusal_correct_rate: float | None = None
    # 業務支援の対応（#1231）。
    handling_correct_rate: float | None = None
    step_order_score: float | None = None
    safe_answer_rate: float | None = None


class EvaluationMetrics(BaseModel):
    """評価結果の集計指標(#591)。

    各指標は、その指標を測れるケースだけの平均で、対象の件数は `metric_case_counts` に持つ。
    対象のケースが無い指標は None。保存済みの結果にある削除した指標は読み込み時に捨てる。
    """

    case_count: int
    error_count: int = 0
    evaluation_suite: str = "standard"
    context_recall: float | None = None
    mrr: float | None = None
    faithfulness: float | None = None
    citation_traceability_coverage: float | None = None
    claim_support_rate: float | None = None
    answer_keyword_hit_rate: float | None = None
    refusal_accuracy: float | None = None
    requirement_coverage: float | None = None
    answer_pass_rate: float | None = None
    handling_accuracy: float | None = None
    step_order_score: float | None = None
    safe_answer_rate: float | None = None
    condition_coverage: float | None = None
    metric_case_counts: dict[str, int] = Field(default_factory=dict)
    passed: bool = True
    threshold_failures: list[EvaluationThresholdFailure] = Field(default_factory=list)
    failure_reason_counts: dict[str, int] = Field(default_factory=dict)
    # 分類ごとの内訳（ケースに分類があるときだけ。分類の無いケースは uncategorized。#1226）。
    category_breakdown: dict[str, EvaluationCategorySummary] = Field(default_factory=dict)
    case_results: list[EvaluationCaseResult] = Field(default_factory=list)


class EvaluationThresholds(BaseModel):
    """CI gate に使う aggregate metric の最低値。削除した指標の名前は受け付けない。"""

    model_config = ConfigDict(extra="forbid")

    context_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    mrr: float | None = Field(default=None, ge=0.0, le=1.0)
    faithfulness: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_traceability_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_support_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_keyword_hit_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    refusal_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    requirement_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    handling_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    step_order_score: float | None = Field(default=None, ge=0.0, le=1.0)
    safe_answer_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    condition_coverage: float | None = Field(default=None, ge=0.0, le=1.0)


class EvaluationRagOverrides(BaseModel):
    """評価 experiment ごとに一時適用する非 secret の回答設定(#591)。

    回答エンジンの全体の既定(質問拡張戦略・回答生成フロー・近傍 child 数・rerank)と、検索の
    RRF 定数・同じ group から足す child の上限・ベクトル検索の target accuracy を上書きする。
    削除したキー(query_expansion_* / context_window_chars など)は受け付けない。
    """

    model_config = ConfigDict(extra="forbid")

    query_strategy: QueryStrategy | None = None
    answer_flow: AnswerFlow | None = None
    neighbor_child_count: int | None = Field(default=None, ge=0, le=20)
    rerank_enabled: bool | None = None
    rrf_k: int | None = Field(default=None, ge=1, le=1000)
    context_group_max_chunks: int | None = Field(default=None, ge=1, le=20)
    oracle_vector_target_accuracy: int | None = Field(default=None, ge=1, le=100)


class EvaluationExperiment(BaseModel):
    """同じ golden set で比較する 1 つの回答設定。

    回答エンジンは検索の方式(mode)と rerank の件数(rerank_top_n)を使わないため、指定は
    受け付けない(#591。比較の結果が同じになるのに、違う設定に見えるため)。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=80)
    top_k: int = Field(default=20, ge=1, le=100)
    filters: dict[str, str] = Field(default_factory=dict)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=200)
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

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        """評価 experiment のナレッジベース ID を重複排除する。"""
        return normalize_search_id_list(values)

    @model_validator(mode="after")
    def validate_search_options(self) -> Self:
        """SearchRequest と同じ KB 指定制約を適用する。"""
        self.filters, self.knowledge_base_ids = _sync_knowledge_base_filter(
            self.filters,
            self.knowledge_base_ids,
        )
        return self


class EvaluationExperimentResult(BaseModel):
    """1 experiment の評価結果と ranking 情報。順位の指標を測れなかったときは None。"""

    rank: int
    ranking_score: float | None = None
    experiment: EvaluationExperiment
    metrics: EvaluationMetrics


class EvaluationCompareResponse(BaseModel):
    """複数 experiment の比較結果。保存済みの結果の削除した指標も読めるよう str で受ける。"""

    ranking_metric: str
    best_experiment_id: str | None
    results: list[EvaluationExperimentResult] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_experiment_fields(cls, data: object) -> object:
        """保存済みの結果の experiment にある削除した欄(mode 等)を、読み込み時に捨てる。"""
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return data
        allowed = set(EvaluationExperiment.model_fields)
        overrides_allowed = set(EvaluationRagOverrides.model_fields)
        results = []
        for result in data["results"]:
            experiment = result.get("experiment") if isinstance(result, dict) else None
            if isinstance(experiment, dict):
                cleaned = {key: value for key, value in experiment.items() if key in allowed}
                overrides = cleaned.get("rag_overrides")
                if isinstance(overrides, dict):
                    cleaned["rag_overrides"] = {
                        key: value for key, value in overrides.items() if key in overrides_allowed
                    }
                result = {**result, "experiment": cleaned}
            results.append(result)
        return {**data, "results": results}


def _reject_duplicate_case_ids(cases: list[EvaluationCase]) -> None:
    """結果のケースを id で区別できるよう、ケースの id の重複を拒否する(#977)。"""
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"評価ケースの id が重複しています: {', '.join(duplicates)}")


class EvaluationCompareRequest(BaseModel):
    """複数の回答設定の比較実行リクエスト。"""

    cases: list[EvaluationCase] = Field(..., min_length=1)
    experiments: list[EvaluationExperiment] = Field(..., min_length=1, max_length=20)
    ranking_metric: EvaluationMetricName = "context_recall"
    thresholds: EvaluationThresholds | None = None
    suite: EvaluationSuiteName | None = None

    @model_validator(mode="after")
    def validate_unique_experiment_ids(self) -> Self:
        """比較結果の識別を安定させるため experiment id とケースの id の重複を拒否する。"""
        _reject_duplicate_case_ids(self.cases)
        ids = [experiment.id for experiment in self.experiments]
        duplicates = sorted(
            {experiment_id for experiment_id in ids if ids.count(experiment_id) > 1}
        )
        if duplicates:
            raise ValueError(f"experiment id が重複しています: {', '.join(duplicates)}")
        return self


class EvaluationRunRequest(BaseModel):
    """評価実行リクエスト。

    回答エンジンの全体の既定で評価する(検索・回答プロファイルは受け取らない。#301)。検索の方式(mode)と
    rerank の件数(rerank_top_n)は回答エンジンが使わないため持たない(#591。以前の golden set の
    指定は無視する)。
    """

    cases: list[EvaluationCase] = Field(..., min_length=1)
    top_k: int = Field(default=20, ge=1, le=100)
    filters: dict[str, str] = Field(default_factory=dict)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=200)
    thresholds: EvaluationThresholds | None = None
    suite: EvaluationSuiteName | None = None
    rag_overrides: EvaluationRagOverrides | None = None

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, filters: dict[str, str]) -> dict[str, str]:
        """検索評価に使う filters を SearchRequest と同じ規則で正規化する。"""
        return normalize_search_filters(filters)

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        """評価実行のナレッジベース ID を重複排除する。"""
        return normalize_search_id_list(values)

    @model_validator(mode="after")
    def validate_search_options(self) -> Self:
        """SearchRequest と同じ KB 指定制約を適用し、ケースの id の重複を拒否する。"""
        _reject_duplicate_case_ids(self.cases)
        self.filters, self.knowledge_base_ids = _sync_knowledge_base_filter(
            self.filters,
            self.knowledge_base_ids,
        )
        return self


def _sync_knowledge_base_filter(
    filters: dict[str, str],
    knowledge_base_ids: list[str],
) -> tuple[dict[str, str], list[str]]:
    """評価 request の明示 KB 指定を既存 filters 経路へ同期する。"""
    filter_knowledge_base_ids = parse_search_id_filter(filters.get("knowledge_base_id"))
    if (
        knowledge_base_ids
        and filter_knowledge_base_ids
        and knowledge_base_ids != filter_knowledge_base_ids
    ):
        raise ValueError(
            "knowledge_base_ids と filters.knowledge_base_id は同じ値を指定してください。"
        )
    resolved_knowledge_base_ids = knowledge_base_ids or filter_knowledge_base_ids
    if not resolved_knowledge_base_ids:
        return filters, []
    return (
        {
            **filters,
            "knowledge_base_id": format_search_id_filter(resolved_knowledge_base_ids),
        },
        resolved_knowledge_base_ids,
    )


EvaluationJobKind = Literal["run", "compare"]
EvaluationJobStatus = Literal["RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


class EvaluationJob(BaseModel):
    """品質評価の job（非同期の実行・進捗・取り消し。#390）。

    進捗は、終わったケースの数（比較では experiment × ケースの通しの数）と今のケースで示す。
    結果は、成功（`SUCCEEDED`）のときだけ返す（評価は `run_result`、比較は `compare_result`）。
    時間の上限に達して打ち切ったケースは、結果の中で失敗のケースとして返す。query の本文は持たない。
    """

    job_id: str
    kind: EvaluationJobKind
    status: EvaluationJobStatus
    total_cases: int
    completed_cases: int
    current_case_id: str | None = None
    current_experiment_id: str | None = None
    current_case_started_at: datetime | None = None
    # job 全体の時間の上限（秒）。上限に達したら、残りのケースは実行せずに失敗として記録する。
    time_limit_seconds: int
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    run_result: EvaluationMetrics | None = None
    compare_result: EvaluationCompareResponse | None = None
