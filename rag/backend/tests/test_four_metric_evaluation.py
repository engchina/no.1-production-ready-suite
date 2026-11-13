"""四指標の評価情報・独立性・応答判断を合成データで検証する。"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.rag.evaluation import EvaluationRunner, score_case_answers, summarize_case_results
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EVALUATION_REPORT_ONLY_METRIC_NAMES,
    EvaluationAnswerJudgement,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationEvidence,
    EvaluationMetricName,
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
    assert set(metrics.metric_case_counts) == {
        *EVALUATION_METRIC_NAMES,
        *EVALUATION_REPORT_ONLY_METRIC_NAMES,
    }
    assert len(EVALUATION_METRIC_NAMES) == 4


@pytest.mark.parametrize("outcome", ["answered", "needs_clarification", "insufficient_evidence"])
@pytest.mark.parametrize("quality", [0.0, 1.0, None])
def test_decision_score_uses_independent_body_judgement_not_declared_label(
    outcome: str, quality: float | None
) -> None:
    case = EvaluationCase(id="synthetic", query="合成の質問", expected_response_decision="hold")
    judgement = EvaluationAnswerJudgement(
        status="completed", response_decision_accuracy=quality, reason="本文と期待する対応を比較"
    )
    scored = score_case_answers(case, [(response(outcome), {})], judgement)
    assert scored.response_decision_accuracy == quality
    assert scored.observed_outcome == outcome
    assert scored.expected_decisions == ["hold"]
    metrics = summarize_case_results([scored])
    assert metrics.metric_case_counts["response_decision_accuracy"] == int(quality is not None)


async def test_reasonable_inability_is_judged_even_without_standard_answer() -> None:
    captured = []

    class Pipeline:
        async def run(
            self, request: Any, trace_id: Any = None, progress_callback: Any = None, **kwargs: Any
        ) -> SearchResponse:
            return response("answered").model_copy(
                update={"answer": "資料に記載がないため、値を確認できません。"}
            )

    async def judge(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "status": "completed",
            "answer_correctness": None,
            "response_decision_accuracy": 1,
            "correctness_reason": "資料にない値を作らず、適切に説明している。",
        }

    metrics = await EvaluationRunner(
        pipeline=Pipeline(), answer_judge=judge, settings=Settings()
    ).run(
        [
            EvaluationCase(
                id="synthetic", query="資料にない項目の値は？", expected_response_decision="hold"
            )
        ],
        top_k=3,
    )
    assert len(captured) == 1
    data = captured[0]["evaluation_input"]
    assert data["response_expectations"][0]["expected_decisions"] == ["hold"]
    assert "確認できません" in data["actual_responses"][0]["answer_text"]
    assert set(data["response_expectations"][0]) == {"turn", "expected_decisions"}
    assert "outcome" not in data["response_expectations"][0]
    assert metrics.response_decision_accuracy == 1
    assert metrics.answer_correctness is None
    assert metrics.case_results[0].observed_outcome == "answered"
    assert metrics.case_results[0].failure_reasons == []


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


def test_incorrect_answer_fails_the_correctness_threshold() -> None:
    judgement = EvaluationAnswerJudgement(status="completed", answer_correctness=0)
    scored = score_case_answers(
        EvaluationCase(id="synthetic", query="合成の質問"), [(response(), {})], judgement
    )
    metrics = summarize_case_results(
        [scored], thresholds=EvaluationThresholds(answer_correctness=0.9)
    )
    assert metrics.answer_correctness == 0
    assert metrics.passed is False
    assert [item.metric for item in metrics.threshold_failures] == ["answer_correctness"]


async def test_case_without_standard_answer_is_not_judged() -> None:
    captured = []

    class Pipeline:
        async def run(
            self, request: Any, trace_id: Any = None, progress_callback: Any = None
        ) -> SearchResponse:
            return response()

    async def judge(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"status": "completed", "answer_correctness": 1}

    result = await EvaluationRunner(
        pipeline=Pipeline(), answer_judge=judge, settings=Settings()
    ).run([EvaluationCase(id="synthetic", query="合成の質問")], top_k=3)
    assert captured == []
    assert result.answer_correctness is None
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
    from app.rag.evaluation import _case_error_result

    case = EvaluationCase(id="failed", query="合成の質問", expected_response_decision="hold")
    failed = _case_error_result(
        case=case,
        trace_id="synthetic",
        elapsed=1,
        error=TimeoutError("合成の失敗"),
    )
    result = summarize_case_results([failed])
    breakdown = result.decision_breakdown["hold"]
    assert (breakdown.case_count, breakdown.error_count) == (1, 1)
    assert breakdown.metric_case_counts["response_decision_accuracy"] == 0
    assert result.response_decision_accuracy is None
    assert result.passed is False


def test_unassessed_completed_judgement_keeps_null_verdict() -> None:
    from app.rag.evaluation import summarize_answer_judgement

    summary = summarize_answer_judgement({"status": "completed", "passed": None})
    assert summary.passed is None
    assert summary.answer_correctness is None


@pytest.mark.parametrize("suite", ["standard", "strict"])
@pytest.mark.parametrize("metric", EVALUATION_METRIC_NAMES)
@pytest.mark.parametrize("offset", [-0.00001, 0.0, 0.00001])
def test_thresholds_use_unrounded_scores(
    suite: str, metric: EvaluationMetricName, offset: float
) -> None:
    from app.rag.evaluation_adapter import resolve_evaluation_suite

    thresholds = resolve_evaluation_suite(suite)
    target = getattr(thresholds, metric)
    value = min(1.0, target + offset)
    values = {name: 1.0 for name in EVALUATION_METRIC_NAMES}
    values[metric] = value
    judgement = EvaluationAnswerJudgement(
        status="completed", answer_correctness=values["answer_correctness"]
    )
    case = EvaluationCaseResult(
        case_id="synthetic-boundary",
        trace_id="synthetic",
        elapsed_ms=1,
        answer_evaluation=judgement,
        **values,
    )
    result = summarize_case_results([case], thresholds=thresholds)
    assert result.passed is (offset >= 0)
    assert [failure.metric for failure in result.threshold_failures] == (
        [metric] if offset < 0 else []
    )
    assert getattr(result, metric) == value


def test_thresholds_use_unrounded_mean_of_multiple_cases() -> None:
    cases = [
        EvaluationCaseResult(
            case_id=str(index), trace_id="synthetic", elapsed_ms=1, answer_correctness=value
        )
        for index, value in enumerate([0.9, 0.89998])
    ]
    result = summarize_case_results(cases, thresholds=EvaluationThresholds(answer_correctness=0.9))
    assert result.passed is False
    assert result.answer_correctness == pytest.approx(0.89999)
    assert result.threshold_failures[0].actual < 0.9


def test_retrieval_scores_preserve_precision_before_aggregation() -> None:
    answer = response()
    answer.citations.insert(
        1,
        RetrievedChunk(
            document_id="other-two",
            chunk_id="other-two:c1",
            text="合成の別の本文",
            score=0.5,
        ),
    )
    case = EvaluationCase(
        id="synthetic-precision",
        query="合成の質問",
        relevant_document_ids=["correct"],
        required_evidence=[
            EvaluationEvidence(id=str(index), document_id="correct", text=text)
            for index, text in enumerate(["必要な事実", "合成の不足 A", "合成の不足 B"])
        ],
    )
    result = score_case_answers(case, [(answer, {})])
    assert result.mrr == 1 / 3
    assert result.required_evidence_recall == 1 / 3


def test_result_preserves_applied_thresholds() -> None:
    result = summarize_case_results(
        [], thresholds=EvaluationThresholds(mrr=0.6, answer_correctness=0.9)
    )
    assert result.applied_thresholds == {"mrr": 0.6, "answer_correctness": 0.9}
