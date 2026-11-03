"""不足要求の再検索を、実際の再生成から分けて記録する。"""

import pytest
from test_completion_stage_io import PolicyRecorder
from test_unanswered_retrieval import NEW_TEXT, OLD_TEXT, QUESTION, _synthesize, context, gap, parent

from rag_engine.generation import grounded
from rag_engine.resources.tracing import bind_trace_observer


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mode", ["added", "empty", "failed", "deadline"])
def test_actual_request_recovery_inputs_and_outcomes(monkeypatch, enabled, mode):
    recorder = PolicyRecorder(enabled)
    calls = []

    def recover(ctx, unanswered):
        calls.append(unanswered)
        assert recorder.calls[("stage", "未回答の要求の探し直し")]
        if mode == "failed":
            raise RuntimeError("private exception sentinel")
        if mode == "empty":
            return ctx, {"applied": False}
        return context(parent("old", OLD_TEXT), parent("new", NEW_TEXT)), {"applied": True}

    if mode == "deadline":
        monkeypatch.setattr(grounded.RoundBudget, "allows_round", lambda self, number: False)
    with bind_trace_observer(recorder.observe):
        result, model = _synthesize(monkeypatch, gap(), recover)
    call = recorder.stage("未回答の要求の探し直し")
    assert len(calls) == (0 if mode == "deadline" else 1)
    assert len(model.prompts) == (2 if mode == "added" else 1)
    if not enabled:
        assert call["input"] == call["output"] == {}
        return
    assert call["input"]["質問"] == QUESTION
    assert call["input"]["元の回答の回"] == 1
    assert call["input"]["再検索する要求"] == [{"request_id": "Q1", "status": "missing"}]
    assert call["input"]["既存根拠"][0]["ID"] == "run:old"
    output = call["output"]
    assert output["新規追加ID"] == (["run:new"] if mode == "added" else [])
    trace = result.response.generation_trace["rounds"][0]["request_retrieval"]
    assert output["検索の記録"] == trace
    if mode == "failed":
        assert trace["error"] == "RuntimeError"
        assert "private exception sentinel" not in str(output)
    if mode == "deadline":
        assert trace["skipped"] == "deadline"
