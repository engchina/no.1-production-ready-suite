"""モデルのカスケードの判断と記録（#4581）。"""

from __future__ import annotations

import pytest

from pr_backend_core.model_cascade import (
    ESCALATION_SIGNALS,
    SIGNAL_STRUCTURED_OUTPUT_INVALID,
    cascade_record,
    decide_escalation,
    resolve_model_cascade,
)


@pytest.mark.parametrize(
    ("primary", "escalation", "explicit", "expected"),
    [
        ("model-a", "model-b", False, "model-b"),
        ("model-a", " model-b ", False, "model-b"),
        ("model-a", "", False, ""),
        ("model-a", None, False, ""),
        # 1 つ目と同じモデルは使わない。
        ("model-a", "model-a", False, ""),
        # 利用者がモデルを明示した実行（モデルの比較・要求のモデル）ではカスケードしない。
        ("model-a", "model-b", True, ""),
    ],
    ids=["set", "trimmed", "empty", "none", "same", "explicit"],
)
def test_resolve_model_cascade(primary, escalation, explicit, expected) -> None:
    cascade = resolve_model_cascade(primary, escalation, explicit_model=explicit)
    assert cascade.escalation_model == expected
    assert cascade.enabled is bool(expected)


@pytest.mark.parametrize("signal", ESCALATION_SIGNALS, ids=list(ESCALATION_SIGNALS))
def test_each_signal_escalates_once(signal: str) -> None:
    cascade = resolve_model_cascade("model-a", "model-b")
    decision = decide_escalation([signal], cascade=cascade, escalated=False)
    assert decision.escalate is True
    assert decision.reasons == (signal,)
    # 1 回の質問で昇格は最多 1 回。
    assert decide_escalation([signal], cascade=cascade, escalated=True).escalate is False


@pytest.mark.parametrize(
    "signal",
    [
        "insufficient_evidence",
        "needs_clarification",
        "needs_human",
        "unverified_mark",
        "warning_mark",
        "rate_limited",
        "server_error",
        "connection_error",
    ],
)
def test_non_signals_do_not_escalate(signal: str) -> None:
    cascade = resolve_model_cascade("model-a", "model-b")
    decision = decide_escalation([signal], cascade=cascade, escalated=False)
    assert decision == decision.__class__(escalate=False, reasons=())


def test_no_cascade_never_escalates() -> None:
    cascade = resolve_model_cascade("model-a", "")
    assert not decide_escalation(
        [SIGNAL_STRUCTURED_OUTPUT_INVALID], cascade=cascade, escalated=False
    ).escalate


def test_reasons_keep_only_escalation_signals_and_are_unique() -> None:
    cascade = resolve_model_cascade("model-a", "model-b")
    decision = decide_escalation(
        [
            "needs_clarification",
            SIGNAL_STRUCTURED_OUTPUT_INVALID,
            SIGNAL_STRUCTURED_OUTPUT_INVALID,
        ],
        cascade=cascade,
        escalated=False,
    )
    assert decision.reasons == (SIGNAL_STRUCTURED_OUTPUT_INVALID,)


def test_cascade_record() -> None:
    cascade = resolve_model_cascade("model-a", "model-b")
    assert cascade_record(resolve_model_cascade("model-a", ""), escalated=False) == {}
    assert cascade_record(cascade, escalated=False, reasons=[SIGNAL_STRUCTURED_OUTPUT_INVALID]) == {
        "primary_model": "model-a",
        "escalation_model": "model-b",
        "escalated": False,
        "reasons": [],
    }
    assert cascade_record(
        cascade,
        escalated=True,
        reasons=[SIGNAL_STRUCTURED_OUTPUT_INVALID, "needs_human"],
        stage="answer.generate",
    ) == {
        "primary_model": "model-a",
        "escalation_model": "model-b",
        "escalated": True,
        "reasons": [SIGNAL_STRUCTURED_OUTPUT_INVALID],
        "stage": "answer.generate",
    }
