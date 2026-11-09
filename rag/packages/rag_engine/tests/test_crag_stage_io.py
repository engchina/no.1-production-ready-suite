"""CRAGの実検索・評価・文脈復元の関係を合成入力で検証する。"""

from dataclasses import replace

import pytest
from test_completion_stage_io import PolicyRecorder
from test_crag_context_recovery import record

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.knowledge.classification import ClassificationFilter
from rag_engine.resources.tracing import bind_trace_observer

QUESTION = "合成の設定条件は？"


def config():
    return get_settings(environ={}, dotenv_path=None)


def ctx(uid="c1"):
    row = record(uid, 1 if uid == "c1" else 2, f"合成本文 {uid}")
    return a.AnswerContext([row], row.text)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mode", ["sufficient", "failure", "rewrite"])
def test_search_and_grade_inputs_are_bound_to_the_actual_round(monkeypatch, enabled, mode):
    recorder = PolicyRecorder(enabled)
    searches, grades = [], []

    def search(question, *args, **kwargs):
        searches.append(kwargs)
        assert recorder.calls[("stage", f"文書検索（{len(searches)}回目）")]
        return ctx("c1" if len(searches) == 1 else "c2")

    def grade(number, question, active, queries, context, settings, **kwargs):
        grades.append((number, active, tuple(queries)))
        if mode == "failure":
            raise ValueError("private exception sentinel")
        return a.CragRetrievalAttempt(
            number,
            active,
            tuple(queries),
            mode != "rewrite" or number > 1,
            relevant_chunk_ids=tuple(row.chunk_uid for row in context.records),
            missing_aspects=("procedure",) if mode == "rewrite" and number == 1 else (),
            rewritten_query="合成の追加条件" if mode == "rewrite" and number == 1 else "",
        )

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", search)
    monkeypatch.setattr(a, "_grade_crag_retrieval", grade)
    if not enabled:
        monkeypatch.setattr(a, "context_snapshot", lambda *args, **kwargs: pytest.fail("disabled snapshot built"))
    with bind_trace_observer(recorder.observe):
        result, rewrites, attempts = a.build_crag_answer_context(
            QUESTION,
            "synthetic-run",
            ["docling"],
            config(),
            top_k=4,
            classification_filter=ClassificationFilter(large_category="synthetic"),
            retrieval_scope=a.RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
        )
    assert len(searches) == len(grades) == (2 if mode == "rewrite" else 1)
    for number, active, queries in grades:
        call = recorder.stage(f"根拠確認（{number}回目）")
        search_call = recorder.stage(f"文書検索（{number}回目）")
        if not enabled:
            assert call["input"] == call["output"] == search_call["input"] == search_call["output"] == {}
            continue
        assert call["input"]["質問"] == QUESTION
        assert call["input"]["現在の検索文"] == active
        assert call["input"]["検索文"] == queries
        assert call["output"]["attempt"] == number
        assert search_call["input"]["分類条件"]["large_category"] == "synthetic"
        assert search_call["input"]["起点数"] == 4
        assert search_call["output"]["根拠レコード"][0]["ID"] == f"run:c{number}"
    if mode == "failure":
        assert attempts[0].grade_error == "ValueError"
        assert "private exception sentinel" not in str(recorder.calls)
        if enabled:
            assert recorder.stage("根拠確認（1回目）")["output"]["grade_execution"] == "error"
        assert result.records
    if mode == "rewrite" and enabled:
        prepare = recorder.stage("補正検索の準備")
        assert prepare["input"]["既存の補正検索文"] == ()
        assert prepare["output"]["追加する検索文"] == rewrites[0]
        assert prepare["output"]["次の回"] == 2


@pytest.mark.parametrize("mode", ["added", "foreign", "budget", "invalid_anchor"])
def test_recovery_records_actual_checks_including_completed_zero(mode):
    child = record("c1", 1, "合成の起点")
    following = record("c2", 2, "合成の追加")
    if mode == "foreign":
        following = replace(following, source="other.pdf")
    original = a.AnswerContext([child], child.text, expansion_records=(child, following))
    attempt = a.CragRetrievalAttempt(
        1,
        QUESTION,
        (QUESTION,),
        False,
        recovery_action="neighbor_children",
        recovery_anchor_ids=("run:unknown" if mode == "invalid_anchor" else "run:c1",),
    )
    recorder = PolicyRecorder()
    with bind_trace_observer(recorder.observe):
        result, trace = a._recover_crag_context(original, attempt, max_records=4, max_chars=1 if mode == "budget" else 1000)
    call = recorder.stage("文脈の追加")
    assert call["input"]["評価結果"]["recovery_anchor_ids"] == list(attempt.recovery_anchor_ids)
    output = call["output"]
    assert output["診断"] == trace
    assert len(output["試行結果"]) == 1
    if mode == "added":
        assert result is not None
        assert [row["ID"] for row in output["返す文脈"]["根拠レコード"]] == ["run:c2"]
    else:
        assert result is None
        assert output["返す文脈"] is None
        assert output["試行結果"][0]["理由"]


def test_deferred_restore_records_read_context_even_when_no_new_body_is_added():
    original = ctx()
    original = replace(
        original,
        ranked_candidates=tuple(original.records),
        expansion_records=tuple(original.records),
        deferred_records=tuple(original.records),
    )
    recorder = PolicyRecorder()
    with bind_trace_observer(recorder.observe):
        restored = a._restore_deferred_context(QUESTION, original, config(), top_k=1, neighbor_child_count=0, max_records=2, max_chars=1000)
    call = recorder.stage("文書の選択の解除")
    assert call["input"]["後回し候補"][0]["ID"] == "run:c1"
    assert call["output"]["後回し候補"] == []
    assert call["output"]["文書選択"]["restored"] is True
    assert call["output"]["新規の根拠がある"] is a._adds_new_records(original, restored)


@pytest.mark.parametrize("reject_all", [True, False])
def test_conclusion_exposes_the_actual_final_context(monkeypatch, reject_all):
    original = ctx()
    grade = a.CragRetrievalAttempt(
        1, QUESTION, (QUESTION,), False, rejected_chunk_ids=("run:c1",) if reject_all else (), missing_aspects=("procedure",)
    )
    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", lambda *args, **kwargs: original)
    monkeypatch.setattr(a, "_grade_crag_retrieval", lambda *args, **kwargs: grade)
    recorder = PolicyRecorder()
    with bind_trace_observer(recorder.observe):
        result, _, attempts = a.build_crag_answer_context(QUESTION, "synthetic-run", ["docling"], config())
    call = recorder.stage("根拠確認の結論")
    assert call["input"]["除外ID"] == grade.rejected_chunk_ids
    assert call["output"]["最終評価"] == attempts[-1].to_payload()
    assert [row["ID"] for row in call["output"]["根拠レコード"]] == [row.chunk_uid for row in result.records]
    assert bool(result.records) is not reject_all


@pytest.mark.parametrize("enabled", [True, False])
def test_standard_search_parent_uses_the_same_snapshot_contract(monkeypatch, enabled):
    from test_completion_stage_io import run

    recorder = PolicyRecorder(enabled)
    result = run(monkeypatch, recorder)
    call = recorder.stage("文書検索")
    if not enabled:
        assert call["input"] == call["output"] == {}
        return
    assert call["input"]["質問"] == "合成の質問"
    assert call["input"]["分類条件"]["large_category"] == "synthetic-category"
    assert call["input"]["検索文"] == tuple(result.retrieval_query_plan["text_query_variants"])
    assert call["output"]["根拠レコード"][0]["ID"] == "run:old"
