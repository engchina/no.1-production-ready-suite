"""未校正の意味監査を後処理で自動裁決へ戻さない。外部サービスは使わない。"""

from typing import Any, cast

import pytest

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome, _outcome_from_result, _SearchState
from tests.test_answer_use import _EngineResult

POLICY = {"mode": "diagnostic_only", "calibration": "not_calibrated"}


def outcome() -> AnswerOutcome:
    return AnswerOutcome(
        answer="合成の説明を保持します。",
        citations=[],
        context_text="",
        diagnostics={
            "semantic_audit_policy": dict(POLICY),
            "outcome": "answered",
            "envelope": {"outcome": "answered"},
            "confidence": "high",
            "insufficient_reason": "既存の案内",
        },
    )


def engine(mode: str = "standard") -> AnswerEngine:
    return AnswerEngine(
        settings=Settings(rag_answer_audit_mode=cast(Any, mode)),
        oracle=cast(Any, object()),
        genai=cast(Any, object()),
    )


def test_engine_policy_reaches_backend_diagnostics() -> None:
    result = _EngineResult([])
    result.generation_trace = {"semantic_audit_policy": dict(POLICY)}
    projected = _outcome_from_result(result, _SearchState())
    assert projected.diagnostics["semantic_audit_policy"] == POLICY
    assert (
        projected.diagnostics["semantic_audit_policy"]
        is not result.generation_trace["semantic_audit_policy"]
    )


@pytest.mark.parametrize("alignment", ["aligned", "off_target", "partial"])
def test_raw_goal_alignment_is_not_adopted_under_diagnostic_policy(alignment: str) -> None:
    from app.rag.answer_engine import adopted_goal_alignment

    trace: dict[str, Any] = {"rounds": [{"accepted": True, "audit": {"goal_alignment": alignment}}]}
    assert adopted_goal_alignment(trace) == alignment
    trace["semantic_audit_policy"] = dict(POLICY)
    assert adopted_goal_alignment(trace) is None
    assert trace["rounds"][0]["audit"]["goal_alignment"] == alignment
