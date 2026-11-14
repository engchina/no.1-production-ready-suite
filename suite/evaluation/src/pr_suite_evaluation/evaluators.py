"""Evaluator: 採点の入力（`CaseView`）を、評価セットの期待と照らして採点する（#3240）。

指標は製品の評価の指標と同じ名前・同じ意味で、照合・順位は platform の採点の部品
（`pr_evaluation_scoring`）を使う。各指標は使う観測を宣言し（`METRIC_REQUIREMENTS`）、指標ごとに
次のどれかを記録する。

- scored: 測れた（値は 0〜1）。
- not_applicable: ケースに期待が無い（正解の文書・必要な根拠・標準回答が無い）か、
  検索をしない回答（引用の無い確認の質問・人への引き継ぎ）で、指標の対象外。
- unevaluable: 期待はあるが、観測に必要な値が無いので測れない（理由を `reason` に残す）。成功や
  推測の点数に置き換えず、平均にも分母にも入れない。

理由（unevaluable）:

- `outcome_missing`: 回答の対応が分からない（`outcome` の無い完了の Run）。
- `evidence_text_omitted`: 場所の条件に合う根拠の本文が省かれていて、本文の条件を照合できない。
- `evidence_withheld`: 見る人の今の権限で見られない根拠があり、取れなかった根拠・順位がその根拠の
  せいかを確かめられない。
- `answer_judge_unavailable`: 標準回答はあるが、回答の判定（LLM による照合）を suite は行わない。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pr_evaluation_scoring import (
    document_hits,
    location_matches,
    reciprocal_rank,
    skips_retrieval,
    text_matches,
    unique_in_order,
)
from pydantic import BaseModel, Field

from pr_suite_evaluation.dataset import Case, CaseCategory, CaseSplit, ReasoningType
from pr_suite_evaluation.views import CaseView, Citation, TurnView

EVALUATORS_VERSION = "five_metrics_v1"
OUTCOME_MISSING = "outcome_missing"
EVIDENCE_TEXT_OMITTED = "evidence_text_omitted"
EVIDENCE_WITHHELD = "evidence_withheld"
ANSWER_JUDGE_UNAVAILABLE = "answer_judge_unavailable"
ANSWER_METRICS = ("answer_correctness", "requirement_coverage", "claim_support_rate")
# 指標が使う観測（Evaluator の宣言）。
METRIC_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "required_evidence_recall": ("citations.document_id", "citations.locator", "citations.text"),
    "mrr": ("citations.document_id", "outcome"),
    "answer_correctness": ("answer_judgement",),
    "requirement_coverage": ("answer_judgement",),
    "claim_support_rate": ("answer_judgement",),
}

MetricStatusName = Literal["scored", "not_applicable", "unevaluable"]


class MetricStatus(BaseModel):
    status: MetricStatusName
    reason: str | None = None


class TurnScore(BaseModel):
    """往復ごとの観測（自己申告の対応と時間。期待と比べて点数にしない）。"""

    turn: int
    trace_id: str
    observed_outcome: str | None = None
    outcome_source: str | None = None
    elapsed_ms: float


class CaseScore(BaseModel):
    """1 ケースの採点（製品の評価のケースの結果と同じ名前の項目と、指標ごとの状態）。"""

    case_id: str
    trace_id: str
    category: CaseCategory | None = None
    split: CaseSplit | None = None
    reasoning_type: ReasoningType | None = None
    hops: int | None = None
    status: Literal["success", "error"] = "success"
    retrieved_document_ids: list[str] = Field(default_factory=list)
    relevant_document_ids: list[str] = Field(default_factory=list)
    hit_document_ids: list[str] = Field(default_factory=list)
    required_evidence_recall: float | None = None
    mrr: float | None = None
    answer_correctness: float | None = None
    requirement_coverage: float | None = None
    claim_support_rate: float | None = None
    observed_outcome: str | None = None
    outcome_source: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    # 本文を省かれた・見られない根拠のため、取れたか分からない必要な根拠。
    undetermined_evidence: list[str] = Field(default_factory=list)
    turn_results: list[TurnScore] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    metric_status: dict[str, MetricStatus] = Field(default_factory=dict)
    elapsed_ms: float
    error_type: str | None = None
    error_stage: str | None = None
    error_message: str | None = None


def _scored() -> MetricStatus:
    return MetricStatus(status="scored")


def _not_applicable() -> MetricStatus:
    return MetricStatus(status="not_applicable")


def _unevaluable(reason: str) -> MetricStatus:
    return MetricStatus(status="unevaluable", reason=reason)


def _evidence_state(case: Case, citations: Sequence[Citation]) -> tuple[list[str], list[str]]:
    """取れなかった根拠と、取れたか分からない根拠（本文を省かれた根拠しか場所が合わない）。"""
    missing: list[str] = []
    undetermined: list[str] = []
    for evidence in case.required_evidence:
        found = False
        unknown = False
        for chunk in citations:
            document_id = case.canonical_document_id(chunk.document_id)
            if not location_matches(evidence, document_id=document_id, locator=chunk.locator):
                continue
            if evidence.text is None:
                found = True
                break
            if chunk.text_omitted and not chunk.text:
                unknown = True
                continue
            if text_matches(chunk.text, evidence.text):
                found = True
                break
        if found:
            continue
        (undetermined if unknown else missing).append(evidence.id)
    return missing, undetermined


def _turn_scores(views: Sequence[TurnView]) -> list[TurnScore]:
    return [
        TurnScore(
            turn=view.turn,
            trace_id=view.trace_id,
            observed_outcome=view.outcome,
            outcome_source=view.outcome_source,
            elapsed_ms=view.elapsed_ms,
        )
        for view in views
    ]


def score_case(case: Case, view: CaseView) -> CaseScore:
    """1 ケースを採点する（失敗したケースは指標を測らず、ケースの失敗にする）。"""
    if view.failure is not None or not view.turns:
        failure = view.failure
        return CaseScore(
            case_id=case.id,
            trace_id=failure.trace_id if failure else "",
            category=case.category,
            split=case.split,
            reasoning_type=case.reasoning_type,
            hops=case.hops,
            status="error",
            relevant_document_ids=list(case.relevant_document_ids),
            failure_reasons=["case_error"],
            elapsed_ms=view.elapsed_ms,
            error_type=failure.error_type if failure else "TargetError",
            error_stage=failure.error_stage if failure else None,
            error_message=failure.error_message if failure else None,
        )
    final = view.turns[-1]
    citations = final.citations
    retrieved = unique_in_order(case.canonical_document_id(item.document_id) for item in citations)
    relevant = set(case.relevant_document_ids)
    status: dict[str, MetricStatus] = {}
    failure_reasons: list[str] = []
    outcome_unknown = final.outcome is None and not citations
    skip = skips_retrieval(final.outcome, has_citations=bool(citations))
    withheld = final.withheld_evidence_count > 0

    hits: list[str] = []
    mrr: float | None = None
    if not relevant or skip:
        status["mrr"] = _not_applicable()
    elif outcome_unknown:
        status["mrr"] = _unevaluable(OUTCOME_MISSING)
    else:
        hits = document_hits(retrieved, relevant)
        rank = reciprocal_rank(retrieved, relevant)
        if withheld and rank < 1:
            status["mrr"] = _unevaluable(EVIDENCE_WITHHELD)
        else:
            mrr = rank
            status["mrr"] = _scored()
            if not hits:
                failure_reasons.append("retrieval_miss")

    recall: float | None = None
    missing: list[str] = []
    undetermined: list[str] = []
    if not case.required_evidence or skip:
        status["required_evidence_recall"] = _not_applicable()
    elif outcome_unknown:
        status["required_evidence_recall"] = _unevaluable(OUTCOME_MISSING)
    else:
        missing, undetermined = _evidence_state(case, citations)
        if undetermined:
            status["required_evidence_recall"] = _unevaluable(EVIDENCE_TEXT_OMITTED)
        elif missing and withheld:
            status["required_evidence_recall"] = _unevaluable(EVIDENCE_WITHHELD)
            undetermined, missing = missing, []
        else:
            total = len(case.required_evidence)
            recall = (total - len(missing)) / total
            status["required_evidence_recall"] = _scored()
            if missing:
                failure_reasons.insert(0, "evidence_miss")

    for name in ANSWER_METRICS:
        status[name] = (
            _unevaluable(ANSWER_JUDGE_UNAVAILABLE) if case.standard_answer else _not_applicable()
        )

    return CaseScore(
        case_id=case.id,
        trace_id=final.trace_id,
        category=case.category,
        split=case.split,
        reasoning_type=case.reasoning_type,
        hops=case.hops,
        retrieved_document_ids=retrieved,
        relevant_document_ids=list(case.relevant_document_ids),
        hit_document_ids=hits,
        required_evidence_recall=recall,
        mrr=mrr,
        observed_outcome=final.outcome,
        outcome_source=final.outcome_source,
        missing_evidence=missing,
        undetermined_evidence=undetermined,
        turn_results=_turn_scores(view.turns) if case.turns else [],
        failure_reasons=failure_reasons,
        metric_status={name: status[name] for name in METRIC_REQUIREMENTS},
        elapsed_ms=view.elapsed_ms,
    )
