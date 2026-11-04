"""親工程の実行範囲と結果を子の生成結果に照合する。"""

import pytest
from test_completion_stage_io import PolicyRecorder
from test_correction_trace import fixture, run_recorded
from test_evidence_refinement_flow import Parser, run
from test_grounded_answer import FakeModel

from rag_engine.adapters.oci import InvalidStructuredOutput
from rag_engine.generation.answering import _record_search_not_ready
from rag_engine.knowledge.classification import ClassificationFilter
from rag_engine.resources.tracing import bind_trace_observer


@pytest.mark.parametrize("enabled", [True, False])
def test_generation_parent_records_each_round_and_actual_adoption(enabled):
    ctx, wrong, fixed, review = fixture()
    recorder = PolicyRecorder(enabled)
    with bind_trace_observer(recorder.observe):
        response, _ = run_recorded(FakeModel([wrong, fixed], [review]), ctx)
    for index, result in enumerate(response.generation_trace["rounds"], start=1):
        stage = recorder.stage(f"回答文の生成と根拠確認（{index}回目）")
        if not enabled:
            assert stage["input"] == stage["output"] == {}
            continue
        assert stage["input"]["round"] == stage["output"]["round"] == index
        assert all(row["evidence_id"] and row["source_id"] for row in stage["input"]["根拠"])
        assert stage["output"]["accepted"] == result["accepted"]
        assert stage["output"]["result"]["draft"] == result["draft"]


def test_failed_generation_retains_input_and_records_fallback_without_success_output():
    recorder = PolicyRecorder()
    with bind_trace_observer(recorder.observe):
        run(Parser([InvalidStructuredOutput("private-error-sentinel")]), mode="legacy")
    stage = recorder.stage("回答文の生成と根拠確認（1回目）")
    assert stage["input"]["根拠"]
    assert stage["output"]["accepted"] is False
    assert stage["output"]["fallback_available"] is True
    assert stage["output"]["error_type"]
    assert "result" not in stage["output"]
    assert "private-error-sentinel" not in str(stage)


@pytest.mark.parametrize("enabled", [True, False])
def test_search_not_ready_records_only_actual_unexecuted_work(enabled):
    recorder = PolicyRecorder(enabled)
    with bind_trace_observer(recorder.observe):
        _record_search_not_ready(ClassificationFilter(large_category="synthetic-category"), "合成の未準備理由")
    stage = recorder.stage("文書検索")
    if not enabled:
        assert stage["input"] == stage["output"] == {}
        return
    assert stage["input"]["分類条件"]["large_category"] == "synthetic-category"
    assert stage["output"] == {
        "executed": False,
        "reason": "合成の未準備理由",
        "query_expansion_executed": False,
        "generation_executed": False,
    }
