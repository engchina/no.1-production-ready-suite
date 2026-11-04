"""修正の実生成と終了判断の入出力を実行結果に照合する。"""

import pytest
from test_completion_stage_io import PolicyRecorder
from test_correction_trace import fixture, run_recorded, test_correction_time_limit_is_skipped_without_changing_budget as check_time_limit
from test_evidence_refinement_flow import Parser, run
from test_grounded_answer import FakeModel

from rag_engine.resources.tracing import bind_trace_observer


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("corrections", [0, 2])
def test_finalization_and_count_skip_preserve_actual_adoption(enabled, corrections):
    ctx, wrong, fixed, review = fixture()
    recorder = PolicyRecorder(enabled)
    with bind_trace_observer(recorder.observe):
        response, steps = run_recorded(FakeModel([wrong, fixed], [review]), ctx, corrections=corrections)
    final = recorder.stage("是正の打ち切り")
    if not enabled:
        assert final["input"] == final["output"] == {}
    else:
        assert final["input"]["rounds"] == final["output"]["rounds"]
        assert final["input"]["evidence_refinement"] == response.generation_trace["evidence_refinement"]
        assert final["output"]["adopted_round"] == (1 if corrections == 0 else 2)
    if corrections == 0:
        skipped = recorder.stage("回答の修正")
        assert next(step for step in steps if step["name"] == "回答の修正")["status"] == "skipped"
        if enabled:
            assert skipped["input"]["issues"]
            assert skipped["input"]["max_rounds"] == 1
            assert skipped["output"]["executed"] is False
        else:
            assert skipped["input"] == skipped["output"] == {}


@pytest.mark.parametrize("enabled", [True, False])
def test_refinement_records_the_actual_selected_prompt_and_result(enabled):
    recorder = PolicyRecorder(enabled)
    parser = Parser()
    with bind_trace_observer(recorder.observe):
        result = run(parser)
    refinement = recorder.stage("選別した根拠で回答を作り直す")
    assert len(parser.calls) == 2
    if not enabled:
        assert refinement["input"] == refinement["output"] == {}
        return
    assert refinement["input"]["生成への入力"] == parser.calls[1]["prompt"]
    assert len(refinement["input"]["選別した根拠"]) == 1
    actual = result.response.generation_trace["rounds"][-1]
    assert refinement["output"] == {key: value for key, value in actual.items() if key not in {"accepted", "remedy"}}


@pytest.mark.parametrize("enabled", [True, False])
def test_time_skip_exposes_the_actual_budget_and_unexecuted_plan(enabled):
    recorder = PolicyRecorder(enabled)
    with bind_trace_observer(recorder.observe):
        check_time_limit()
    skipped = recorder.stage("回答の修正")
    if not enabled:
        assert skipped["input"] == skipped["output"] == {}
        return
    assert skipped["input"]["issues"]
    assert skipped["input"]["plan"]["remedy"]
    assert skipped["input"]["budget"] == skipped["output"]["budget"]
    assert skipped["output"]["reason"] == "time_budget"
    assert skipped["output"]["executed"] is False
