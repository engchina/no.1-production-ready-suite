"""共有根拠・検索打切り・二組の生成監査の合成再生（#2733）。"""

import pytest


def test_wrapped_execution_error_retains_the_original_frame_in_safe_diagnostics():
    """これは元の500の再現ではなく、根因の位置がログから消える部分の再現。"""
    from pr_backend_core.logging import safe_exception_fields

    from rag_engine.generation.execution_record import AnswerExecutionError, _execution_step, _record_answer_execution

    def fail_with_attribute_error():
        raise AttributeError("QUESTION_SENTINEL DOCUMENT_SENTINEL TOKEN_SENTINEL")

    @_record_answer_execution
    def synthetic_execution(question):
        with _execution_step("合成の工程"):
            fail_with_attribute_error()

    with pytest.raises(AnswerExecutionError) as raised:
        synthetic_execution("QUESTION_SENTINEL")
    fields = safe_exception_fields(raised.value)
    assert fields["exception_type"] == "AnswerExecutionError"
    assert fields["exception_causes"] == ["AttributeError"]
    causes = fields.get("exception_cause_details", [])
    assert causes and causes[0]["exception_type"] == "AttributeError"
    assert causes[0]["exception_frames"][-1]["function"] == "fail_with_attribute_error"
    assert "SENTINEL" not in str(fields)
