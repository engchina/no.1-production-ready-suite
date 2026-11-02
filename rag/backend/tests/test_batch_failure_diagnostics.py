"""分割読取の契約違反を、最終回答生成と区別して API の診断へ残す。"""

import pytest
from rag_engine.generation.answer_models import AnswerQuestionResult

from app.rag.answer_engine import _outcome_from_result, _SearchState


@pytest.mark.parametrize("stage", ["evidence.batch_read", "answer.generate"])
def test_generation_failure_stage_survives_answer_conversion(stage: str) -> None:
    failure = {"error": "ValidationError", "stage": stage}
    result = AnswerQuestionResult(
        answer="合成の参考情報",
        question_display="合成の質問",
        original_question="合成の質問",
        selected_strategy="simple",
        effective_strategy="simple",
        generation_trace={"generation_failure": failure},
    )
    diagnostics = _outcome_from_result(result, _SearchState()).diagnostics
    assert diagnostics["generation_failure"] == failure
