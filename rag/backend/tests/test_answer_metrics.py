"""標準回答による評価の指標と合否(評価の基準の閾値。#680)。"""

from collections.abc import Mapping

import pytest

from app.config import Settings
from app.rag.answer_metrics import score_answer_evaluation


def _score(evaluation: Mapping[str, object], suite: str = "standard") -> dict[str, object]:
    return score_answer_evaluation(evaluation, settings=Settings(rag_evaluation_suite=suite))


@pytest.mark.parametrize(
    ("suite", "threshold"),
    [("standard", 0.9), ("strict", 1.0)],
)
@pytest.mark.parametrize(("value", "passed"), [(1.0, True), (0.0, False), (None, None)])
def test_correctness_alone_decides_the_result(
    suite: str, threshold: float, value: float | None, passed: bool | None
) -> None:
    result = _score({"status": "completed", "answer_correctness": value}, suite)
    assert result["suite"] == suite
    assert result["passed"] is passed
    assert result["metrics"] == [
        {
            "name": "answer_correctness",
            "value": value,
            "threshold": threshold,
            "passed": passed,
            "reference": False,
        }
    ]


def test_unfinished_evaluation_is_returned_as_is() -> None:
    evaluation: dict[str, object] = {"status": "error", "message": "評価を完了できませんでした。"}
    assert _score(evaluation) == evaluation
