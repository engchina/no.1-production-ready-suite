"""六指標の評価情報・独立性・応答判断を合成データで検証する。"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.rag.evaluation import EvaluationRunner, score_case_answers, summarize_case_results
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EvaluationAnswerJudgement,
    EvaluationCase,
    EvaluationEvidence,
    EvaluationThresholds,
)
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchResponse


def response(outcome: str = "answered") -> SearchResponse:
    return SearchResponse(
        answer="合成の回答",
        trace_id="synthetic-trace",
        elapsed_ms=1,
        citations=[
            RetrievedChunk(document_id=doc, chunk_id=f"{doc}:c1", text="合成の必要な事実", score=1)
            for doc in ("other", "correct", "correct")
        ],
        diagnostics=SearchDiagnostics(answer={"outcome": outcome}),
    )


def test_required_fragments_and_document_ranks_are_independent() -> None:
    case = EvaluationCase(
        id="synthetic",
        query="合成の質問",
        relevant_document_ids=["correct"],
        required_evidence=[
            EvaluationEvidence(id="e1", document_id="correct", text="必要な事実"),
            EvaluationEvidence(id="e2", document_id="correct", text="見つからない事実"),
        ],
    )
    scored = score_case_answers(case, [(response(), {})])
    assert scored.required_evidence_recall == scored.mrr == 0.5
    assert scored.missing_evidence == ["e2"]
    metrics = summarize_case_results([scored])
    assert set(metrics.metric_case_counts) == set(EVALUATION_METRIC_NAMES)
    assert len(EVALUATION_METRIC_NAMES) == 6


@pytest.mark.parametrize("expected", ["answer", "clarify", "hold"])
@pytest.mark.parametrize(
    "actual,outcome",
    [("answer", "answered"), ("clarify", "needs_clarification"), ("hold", "insufficient_evidence")],
)
def test_three_way_decision(expected: Any, actual: str, outcome: str) -> None:
    case = EvaluationCase(id="synthetic", query="合成の質問", expected_response_decision=expected)
    scored = score_case_answers(case, [(response(outcome), {})])
    assert scored.response_decision_accuracy == float(expected == actual)
    metrics = summarize_case_results([scored])
    assert (
        metrics.decision_breakdown[expected].metric_case_counts["response_decision_accuracy"] == 1
    )


def test_missing_materials_are_not_zero_or_passed() -> None:
    scored = score_case_answers(
        EvaluationCase(id="synthetic", query="合成の質問"), [(response(), {})]
    )
    metrics = summarize_case_results(
        [scored], thresholds=EvaluationThresholds(answer_correctness=1)
    )
    assert all(getattr(metrics, name) is None for name in EVALUATION_METRIC_NAMES)
    assert metrics.passed is None
    assert all(count == 0 for count in metrics.metric_case_counts.values())


def test_correctness_does_not_follow_faithfulness_or_coverage() -> None:
    judgement = EvaluationAnswerJudgement(
        status="completed", answer_correctness=0, requirement_coverage=1, claim_support_rate=1
    )
    scored = score_case_answers(
        EvaluationCase(id="synthetic", query="合成の質問"), [(response(), {})], judgement
    )
    metrics = summarize_case_results(
        [scored], thresholds=EvaluationThresholds(answer_correctness=0.9)
    )
    assert metrics.answer_correctness == 0
    assert metrics.requirement_coverage == metrics.claim_support_rate == 1
    assert metrics.passed is False
    assert [item.metric for item in metrics.threshold_failures] == ["answer_correctness"]


async def test_faithfulness_without_standard_or_saved_record() -> None:
    captured = []

    class Pipeline:
        async def run(
            self, request: Any, trace_id: Any = None, progress_callback: Any = None
        ) -> SearchResponse:
            return response()

    async def judge(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "status": "completed",
            "claim_support_rate": 1,
            "requirement_coverage": None,
            "answer_correctness": None,
        }

    result = await EvaluationRunner(
        pipeline=Pipeline(), answer_judge=judge, settings=Settings()
    ).run([EvaluationCase(id="synthetic", query="合成の質問")], top_k=3)
    assert captured[0]["standard_answer"] == ""
    assert captured[0]["evaluation_input"]["evidence_items"]
    assert result.claim_support_rate == 1
    assert result.answer_correctness is None
    assert result.metric_case_counts["claim_support_rate"] == 1
    assert result.metric_case_counts["answer_correctness"] == 0


@pytest.mark.parametrize(
    "name",
    [
        "context_recall",
        "faithfulness",
        "answer_pass_rate",
        "handling_accuracy",
        "step_order_score",
        "safe_answer_rate",
    ],
)
def test_removed_thresholds_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        EvaluationThresholds.model_validate({name: 1})


def test_failed_case_stays_in_expected_decision_breakdown() -> None:
    from app.rag.evaluation import case_error_result

    case = EvaluationCase(id="failed", query="合成の質問", expected_response_decision="hold")
    failed = case_error_result(
        case,
        trace_id="synthetic",
        elapsed_ms=1,
        error_type="TimeoutError",
        error_message="合成の失敗",
    )
    result = summarize_case_results([failed])
    breakdown = result.decision_breakdown["hold"]
    assert (breakdown.case_count, breakdown.error_count) == (1, 1)
    assert breakdown.metric_case_counts["response_decision_accuracy"] == 0
    assert result.response_decision_accuracy is None
    assert result.passed is False
