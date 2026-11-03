"""候補の入出力と実際の判断を、その工程へ結び付ける合成回帰。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_document_selection import child, parent
from test_query_stage_io import Recorder

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.models.llm import RerankTextRank
from rag_engine.resources.tracing import bind_trace_observer


def settings(**values):
    return replace(get_settings(environ={}, dotenv_path=None), **values)


def test_rerank_io_records_real_calls_threshold_and_top_n(monkeypatch):
    records = [child(str(i), i, f"合成本文 {i}", f"合成{i}.txt", f"p{i}") for i in range(3)]
    calls = []

    def provider(question, documents, settings, top_n):
        calls.append(question)
        scores = [0.5, 0.3, 0.9] if question == "合成の原質問" else [0.8, 0.2, 0.6]
        return [RerankTextRank(i, score) for i, score in enumerate(scores)]

    monkeypatch.setattr(a, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(a, "rerank_text_with_scores", provider)
    recorder = Recorder()
    with bind_trace_observer(recorder.observe):
        ranked = a.rerank_records(
            "合成の原質問",
            records,
            settings(rerank_min_relevance_score=0.4, rerank_top_n=1),
            enabled=True,
            extra_queries=["合成の追加質問"],
        )
    stage = recorder.stage("Rerank")
    assert calls == ["合成の原質問", "合成の追加質問"]
    assert stage["input"]["関連度スコアの下限"] == 0.4
    assert stage["input"]["採用件数の上限"] == 1
    assert [item["ID"] for item in stage["input"]["候補"]] == [record.chunk_uid for record in records]
    assert [item["ID"] for item in stage["output"]["処理後の候補"]] == [record.chunk_uid for record in ranked] == ["run:2"]
    decisions = stage["output"]["判定"]
    assert decisions["状態"] == "採点完了"
    assert len(decisions["実際の採点呼出"]) == len(calls)
    assert {item["candidate_index"]: item["status"] for item in decisions["関連度下限の判断"]} == {0: "kept", 1: "excluded", 2: "kept"}
    assert decisions["採用件数の判断"]["applied"] is True


@pytest.mark.parametrize("mode", ["disabled", "empty", "failure", "unscored"])
def test_rerank_io_does_not_invent_scores_or_a_successful_call(monkeypatch, mode):
    records = [] if mode == "empty" else [child("a", 1, "合成の本文", "a.txt", "p")]
    calls = []

    def provider(*args, **kwargs):
        calls.append(args)
        if mode == "failure":
            raise ValueError("synthetic")
        return [RerankTextRank(0, None)]

    monkeypatch.setattr(a, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(a, "rerank_text_with_scores", provider)
    recorder = Recorder()
    with bind_trace_observer(recorder.observe):
        ranked = a.rerank_records("合成の質問", records, settings(rerank_top_n=1), enabled=mode != "disabled")
    assert [(item.chunk_uid, item.text) for item in ranked] == [(item.chunk_uid, item.text) for item in records]
    output = recorder.stage("Rerank")["output"]["判定"]
    if mode in {"disabled", "empty"}:
        assert output["状態"] == "未実行"
        assert output["理由"] == ("無効の設定" if mode == "disabled" else "対象の根拠なし")
        assert calls == []
    elif mode == "failure":
        assert output["理由"] == "採点失敗"
        assert output["エラーの種類"] == "ValueError"
        assert output["実際の採点呼出"][0]["logical_status"] == "error"
    else:
        assert output["採用件数の判断"] == {"applied": False, "stop_reason": "no_scores"}
        assert output["関連度下限の判断"][0]["status"] == "kept_unscored"


@pytest.mark.parametrize("last_score", [0.1, 0.6])
def test_candidate_document_and_anchor_io_follow_real_pipeline(monkeypatch, last_score):
    records = [
        child("a1", 1, "合成本文 A1", "a.txt", "pa"),
        child("a2", 2, "合成本文 A2", "a.txt", "pa"),
        child("b1", 3, "合成本文 B1", "b.txt", "pb"),
    ]
    parents = [parent("pa", 1, "合成本文 A1\n合成本文 A2", "a.txt"), parent("pb", 3, "合成本文 B1", "b.txt")]
    monkeypatch.setattr(
        a, "search_adb_hybrid_chunks", lambda **_: SimpleNamespace(child_chunks=[records[0], *records], all_chunks=[*records, *parents])
    )
    monkeypatch.setattr(a, "_stored_chunk_answer_record", lambda record: record)
    monkeypatch.setattr(a, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(
        a, "rerank_text_with_scores", lambda *_, **kw: [RerankTextRank(i, value) for i, value in enumerate([0.9, 0.8, last_score])]
    )
    recorder = Recorder()
    with bind_trace_observer(recorder.observe):
        result = a.build_adb_hybrid_answer_context(
            "合成の質問",
            "synthetic-run",
            ["docling"],
            settings(
                rerank_min_relevance_score=0,
                rerank_top_n=0,
                max_anchors_per_document=1,
                document_selection_enabled=True,
                article_catalog_enabled=False,
                screen_linking_enabled=False,
            ),
            top_k=1,
            neighbor_child_count=0,
            retrieval_scope=a.RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
        )
    candidate = recorder.stage("再ランキング候補の選択")
    assert len(candidate["input"]["候補"]) == 4
    assert len(candidate["output"]["採用候補"]) == 3
    assert candidate["output"]["判断"]["excluded"] == [{"id": "run:a1", "reason": "duplicate_original", "retained_id": "run:a1"}]
    documents = recorder.stage("文書の選択")
    assert len(documents["input"]["候補"]) == len(result.ranked_candidates)
    assert [item["ID"] for item in documents["output"]["後回しの候補"]] == (["run:b1"] if last_score == 0.1 else [])
    capped = recorder.stage("文書ごとの起点の上限")
    assert capped["input"]["文書ごとの上限"] == 1
    assert [item["ID"] for item in capped["output"]["処理後の候補"]] == (
        ["run:a1", "run:a2"] if last_score == 0.1 else ["run:a1", "run:b1", "run:a2"]
    )
