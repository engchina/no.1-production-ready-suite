"""回答・根拠取得の全体工程を実際の返却結果に対応付ける。"""

import pytest
from test_completion_stage_io import PolicyRecorder, run

from rag_engine.generation import answering as a


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mode", ["answer", "retrieval", "partial", "empty_answer", "empty_retrieval", "unavailable"])
def test_flow_snapshot_matches_every_return_path(monkeypatch, enabled, mode):
    recorder = PolicyRecorder(enabled)

    def empty(*args, **kwargs):
        return a.AnswerContext([], "")

    def unavailable(*args, **kwargs):
        raise a.AdbHybridSearchUnavailable("合成の未準備")

    generate = mode in {"answer", "empty_answer", "unavailable"}
    result = run(
        monkeypatch,
        recorder,
        generate=generate,
        complete=mode != "partial",
        search=unavailable if mode == "unavailable" else empty if mode.startswith("empty") else None,
    )
    stage = recorder.stage("回答生成フロー" if generate else a.EVIDENCE_FLOW_STEP)
    if not enabled:
        assert stage["input"] == stage["output"] == {}
        return
    assert stage["input"]["回答生成の要求"] is generate
    assert stage["input"]["根拠補完の要求"] is (mode != "partial")
    assert stage["input"]["分類条件"]["large_category"] == "synthetic-category"
    assert stage["output"]["回答本文"] == result.answer_text
    assert stage["output"]["回答の構造"] == result.envelope
    assert stage["output"]["返却する根拠"] == result.evidence_items
    assert stage["output"]["引用した根拠ID"] == result.cited_evidence_ids
    assert stage["output"]["検索計画"] == result.retrieval_query_plan
    assert stage["output"]["生成の記録"] == result.generation_trace
    assert stage["output"]["不足理由"] == result.insufficient_reason
    if mode.startswith("empty") or mode == "unavailable":
        assert not result.cited_evidence_ids
        assert not result.generation_trace.get("rounds")
    elif generate:
        assert result.generation_trace["rounds"]
