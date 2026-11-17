"""評価で、回答生成の退避と評価の処理の失敗を区別する（#4104）。

最終生成の round から ValidationError が出ても、回答エンジンは参考情報へ退避する（#4091）。
評価はそれを成功の生成に数えず、`case_error`（評価の処理の失敗）とも分ける。
実装の例外は成功にしない。退避した問は正確性に関わらず不合格で、全体も合格にしない（#5320）。
"""

from typing import Any

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch
from rag_engine.config import get_settings as engine_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_models import AnswerContext, AnswerQuestionResult, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.models.llm import GroundedApplication

from app.rag.answer_engine import _outcome_from_result, _SearchState
from app.rag.evaluation import EvaluationRunner, score_case_answers
from app.rag.pipeline import SearchStageProgressCallback
from app.schemas.evaluation import (
    EvaluationAnswerJudgement,
    EvaluationCase,
    EvaluationMetrics,
    EvaluationThresholds,
)
from app.schemas.search import SearchDiagnostics, SearchRequest, SearchResponse

QUESTION = "合成の項目の意味は？"
EVIDENCE = "合成の項目は入力を区別する識別子です。"


def _case() -> EvaluationCase:
    return EvaluationCase(id="case-a", query=QUESTION, relevant_document_ids=["doc-1"])


def _implementation_validation_error() -> ValidationError:
    """説明の字数の上限を超えた型の検証の例外（#4091 の実装の誤りと同じ形）。"""
    with pytest.raises(ValidationError) as raised:
        GroundedApplication(reasoning="合" * 601, result="合成の結果")
    return raised.value


def _engine_failure(error: Exception, monkeypatch: MonkeyPatch) -> dict[str, Any]:
    """最終生成の round が ``error`` を出したときの、回答エンジンの結果の診断。"""

    def broken_round(*args: Any, **kwargs: Any) -> Any:
        raise error

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("モデルを呼ばない")

    monkeypatch.setattr(grounded, "run_round", broken_round)
    record = AnswerRecord(
        id="r0",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=EVIDENCE,
        source="合成の文書.pdf",
        chunk_level="parent",
        chunk_id="r0",
        source_run_id="run",
        metadata={"section_path": ["合成の章", "合成の項目"]},
    )
    with bind_dependencies(AnswerDependencies(unused, unused, unused, unused, unused)):
        result = synthesize_grounded_answer(
            QUESTION,
            AnswerContext(records=[record], text=EVIDENCE),
            engine_settings(environ={}, dotenv_path=None),
            image_prompt_mode="text_only",
        ).response
    converted = AnswerQuestionResult(
        answer=result.answer_text,
        question_display=QUESTION,
        original_question=QUESTION,
        generation_trace=result.generation_trace,
    )
    return dict(_outcome_from_result(converted, _SearchState()).diagnostics)


class _Pipeline:
    """回答エンジンの診断を返す、または例外を出す評価用の pipeline。"""

    def __init__(self, *, answer: dict[str, Any] | None = None, error: Exception | None = None):
        self.answer, self.error = answer, error

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        if self.error is not None:
            raise self.error
        return SearchResponse(
            answer="合成の参考情報",
            citations=[],
            trace_id=trace_id or "trace-a",
            guardrail_warnings=[],
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics(knowledge_base_count=1, answer=self.answer),
        )


async def _result(pipeline: _Pipeline) -> Any:
    metrics = await EvaluationRunner(pipeline=pipeline).run(cases=[_case()], top_k=5)
    return metrics, metrics.case_results[0]


async def test_validation_error_from_the_final_generation_is_a_fallback_not_a_case_error(
    monkeypatch: MonkeyPatch,
) -> None:
    """#4104: 最終生成の検証の失敗は、ケースの失敗にせず退避の理由で数える。"""
    answer = _engine_failure(_implementation_validation_error(), monkeypatch)
    assert answer["generation_failure"]["error"] == "ValidationError"
    assert answer["generation_failure"]["stage"] == "answer.generate"
    assert answer["generation_failure"]["model"] == "GroundedApplication"

    metrics, result = await _result(_Pipeline(answer=answer))

    assert result.status == "success"
    assert result.error_type is None
    assert "answer_generation_fallback" in result.failure_reasons
    assert "case_error" not in result.failure_reasons
    assert metrics.error_count == 0
    assert metrics.failure_reason_counts["answer_generation_fallback"] == 1
    assert result.diagnostics.answer["generation_failure"]["model"] == "GroundedApplication"


async def test_a_plan_failure_is_counted_as_the_same_fallback(monkeypatch: MonkeyPatch) -> None:
    answer = _engine_failure(grounded.DraftGenerationInvalid("request_scope_invalid"), monkeypatch)
    _metrics, result = await _result(_Pipeline(answer=answer))
    assert "answer_generation_fallback" in result.failure_reasons
    assert result.diagnostics.answer["generation_failure"]["error"] == "request_scope_invalid"


async def test_a_normal_answer_has_no_fallback_reason() -> None:
    _metrics, result = await _result(_Pipeline(answer={"generation_failure": {}}))
    assert "answer_generation_fallback" not in result.failure_reasons


@pytest.mark.parametrize(
    "error",
    [TypeError("合成の実装の誤り"), KeyError("合成の実装の誤り")],
    ids=lambda e: type(e).__name__,
)
def test_implementation_errors_of_the_round_are_not_answers(
    monkeypatch: MonkeyPatch, error: Exception
) -> None:
    """#4104: 検証の失敗でない実装の例外は、退避にせず送出する。"""
    with pytest.raises(type(error)):
        _engine_failure(error, monkeypatch)


async def test_an_internal_error_that_reaches_the_evaluation_is_not_a_success() -> None:
    """#4104: 評価へ届いた内部の例外はケースの失敗。退避に数えず、本文を残さない。"""
    metrics, result = await _result(_Pipeline(error=TypeError("合成の実装の誤り")))

    assert result.status == "error"
    assert result.error_type == "TypeError"
    assert result.failure_reasons == ["case_error"]
    assert metrics.error_count == 1
    assert metrics.passed is False
    assert "合成の実装の誤り" not in str(result.model_dump(mode="json"))


async def test_a_validation_error_that_reaches_the_evaluation_stays_a_case_error() -> None:
    """#4104: 回答エンジンの外へ抜けた検証の例外は、case_error のまま区別できる。"""
    with pytest.raises(ValidationError) as raised:
        GroundedApplication(reasoning="合" * 601, result="合成の結果")
    metrics, result = await _result(_Pipeline(error=raised.value))

    assert result.status == "error"
    assert result.error_type == "ValidationError"
    assert result.failure_reasons == ["case_error"]
    assert metrics.error_count == 1


# --- この問の合否と全体の判定（#5320） ---

FALLBACK = {"generation_failure": {"error": "ValidationError", "stage": "answer.generate"}}
STANDARD_ANSWER = "合成の項目は入力を区別する識別子です。"


class _SupportedJudge:
    """参考情報の原文が標準回答と合う、という判定（正確性 1・合格）の決定論スタブ。"""

    async def __call__(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "completed",
            "passed": True,
            "answer_correctness": 1.0,
            "answer_coverage": 1.0,
            "correctness_reason": "標準回答と矛盾する主張はありません。",
            "rubric_version": 34,
        }


async def _judged_metrics(
    *answers: dict[str, Any] | None, threshold: float | None = 0.5
) -> EvaluationMetrics:
    """``answers`` の回答の診断ごとに 1 ケース（どれも正確性 1 の判定）を評価する。"""

    class _Pipelines:
        def __init__(self) -> None:
            self.index = 0

        async def run(
            self,
            request: SearchRequest,
            trace_id: str | None = None,
            progress_callback: SearchStageProgressCallback | None = None,
        ) -> SearchResponse:
            answer = answers[self.index]
            self.index += 1
            return await _Pipeline(answer=answer).run(request, trace_id)

    cases = [
        EvaluationCase(id=f"case-{index}", query=QUESTION, standard_answer=STANDARD_ANSWER)
        for index in range(len(answers))
    ]
    return await EvaluationRunner(pipeline=_Pipelines(), answer_judge=_SupportedJudge()).run(
        cases=cases,
        top_k=5,
        thresholds=EvaluationThresholds(answer_correctness=threshold),
    )


async def test_a_fallback_case_fails_even_when_the_reference_text_is_correct() -> None:
    """#5320: 退避した問は、参考情報の原文で正確性が 1 でも不合格。指標の値は判定のまま。"""
    metrics = await _judged_metrics(FALLBACK)
    result = metrics.case_results[0]

    assert result.answer_evaluation is not None
    assert result.answer_evaluation.passed is False
    assert result.answer_correctness == 1.0
    assert result.answer_coverage == 1.0
    assert result.answer_evaluation.answer_correctness == 1.0
    assert result.answer_evaluation.reason == "標準回答と矛盾する主張はありません。"
    assert result.failure_reasons == ["answer_generation_fallback"]
    # 正確性の平均は変えず、全体の判定は合格にしない（閾値は満たす）。
    assert metrics.answer_correctness == 1.0
    assert metrics.threshold_failures == []
    assert metrics.passed is False


async def test_a_case_without_fallback_keeps_the_judged_verdict() -> None:
    """#5320: 退避していない問の合否と全体の判定は今どおり。"""
    metrics = await _judged_metrics({"generation_failure": {}}, None)
    result = metrics.case_results[0]

    assert result.answer_evaluation is not None
    assert result.answer_evaluation.passed is True
    assert result.failure_reasons == []
    assert metrics.passed is True


async def test_one_fallback_case_fails_the_whole_run_without_changing_the_mean() -> None:
    """#5320: 退避した問が 1 つでもあれば全体を合格にしない。ほかの問の合否は変えない。"""
    metrics = await _judged_metrics(None, FALLBACK, None)
    verdicts = [
        result.answer_evaluation.passed if result.answer_evaluation else None
        for result in metrics.case_results
    ]

    assert verdicts == [True, False, True]
    assert metrics.answer_correctness == 1.0
    assert metrics.metric_case_counts["answer_correctness"] == 3
    assert metrics.failure_reason_counts == {"answer_generation_fallback": 1}
    assert metrics.passed is False


def test_an_incomplete_judgement_of_a_fallback_case_stays_unassessed() -> None:
    """#5320: 判定を完了できなかった退避の問は「評価できず」のまま（合否を作らない）。"""
    response = SearchResponse(
        answer="合成の参考情報",
        citations=[],
        trace_id="trace-a",
        guardrail_warnings=[],
        elapsed_ms=1.0,
        diagnostics=SearchDiagnostics(knowledge_base_count=1, answer=FALLBACK),
    )
    judgement = EvaluationAnswerJudgement(status="timeout", message="合成の時間切れ")
    case = EvaluationCase(id="case-a", query=QUESTION, standard_answer=STANDARD_ANSWER)
    result = score_case_answers(case, [(response, {})], judgement)

    assert result.answer_evaluation == judgement
    assert result.failure_reasons == ["answer_evaluation_error", "answer_generation_fallback"]
