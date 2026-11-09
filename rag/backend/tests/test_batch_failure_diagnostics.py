"""分割読取の契約違反を、最終回答生成と区別して API の診断へ残す。"""

import pytest
from rag_engine.generation.answer_models import AnswerQuestionResult

from app.rag.answer_engine import _outcome_from_result, _SearchState


@pytest.mark.parametrize(
    ("stage", "detail"),
    [
        (
            "evidence.batch_read",
            {
                "category": "batch_range_invalid",
                "batch_index": 1,
                "input_count": 80,
                "input_chars": 79000,
                "invalid_attempts": 2,
                "unmatched_count": 1,
                "unmatched_reasons": {"range_out_of_bounds": 1},
            },
        ),
        (
            "answer.generate",
            {
                "category": "schema_field",
                "model": "RequestScopePlan",
                "validation_error_types": {"missing": 1},
            },
        ),
    ],
)
def test_generation_failure_detail_survives_answer_conversion(
    stage: str, detail: dict[str, object]
) -> None:
    failure = {"error": "ValidationError", "stage": stage, **detail}
    result = AnswerQuestionResult(
        answer="合成の参考情報",
        question_display="合成の質問",
        original_question="合成の質問",
        generation_trace={"generation_failure": failure},
    )
    diagnostics = _outcome_from_result(result, _SearchState()).diagnostics
    assert diagnostics["generation_failure"] == failure
