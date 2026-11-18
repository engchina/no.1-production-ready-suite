"""rerank の後は件数で切らず、起点数（top_k）で 1 回だけ切る（#5165）。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

import rag_engine.generation.answering as answering
from rag_engine.config import get_settings
from rag_engine.generation.answering import AnswerRecord, rerank_records
from tests.record_fixtures import child as context_child
from tests.record_fixtures import parent


def child(index, *, attachment=False):
    return AnswerRecord(
        id=str(index),
        engine="text",
        engine_label="Text",
        page=1,
        seq_no=index,
        category="Chunk",
        text=f"合成の説明 {index}",
        source="sample.txt",
        chunk_level="child",
        chunk_id=str(index),
        chunk_uid=f"uid:{index}",
        source_run_id="run",
        metadata={"is_attachment_chunk": True} if attachment else {},
    )


def _scores(monkeypatch, scores):
    """rerank の点数を固定する（候補の位置 → 点数。点数の高い順に返す）。"""
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(
        answering,
        "rerank_text_with_scores",
        lambda q, docs, s, top_n=None: [
            SimpleNamespace(index=i, relevance_score=scores[i]) for i in sorted(range(len(docs)), key=lambda i: -scores[i])
        ],
    )


@pytest.mark.parametrize("mode", ["off", "unconfigured", "failed", "unscored", "success"])
def test_rerank_does_not_cut_by_count(monkeypatch, mode):
    records = [child(i) for i in range(4)]
    settings = SimpleNamespace(rerank_model="test", rerank_min_relevance_score=0)
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: mode != "unconfigured")
    requests = []

    def rank(question, documents, settings, top_n=None):
        requests.append(top_n)
        if mode == "failed":
            raise RuntimeError("synthetic failure")
        # 検索の順の逆に点数を付ける。OCI の rerank の結果は点数の高い順に返る（backend の client が並べる）。
        if mode == "unscored":
            return [SimpleNamespace(index=i, relevance_score=None) for i in range(len(documents))]
        return [SimpleNamespace(index=i, relevance_score=(i + 1) / 10) for i in reversed(range(len(documents)))]

    monkeypatch.setattr(answering, "rerank_text_with_scores", rank)
    result = rerank_records("合成の質問", records, settings, enabled=mode != "off")
    # 成功したときは点数の順、それ以外（無効・未設定・失敗・点数なし）は検索の順。どれも件数は切らない。
    assert [r.id for r in result] == (["3", "2", "1", "0"] if mode == "success" else ["0", "1", "2", "3"])
    if requests:
        assert requests == [4]  # OCI へは全候補の採点を依頼する。


def test_top_k_is_the_only_cut_after_rerank(monkeypatch):
    records = [context_child(str(i), i + 1, f"合成本文 {i}", f"sample-{i}.txt", f"p{i}") for i in range(6)]
    parents = [parent(f"p{i}", i + 1, f"合成本文 {i}", f"sample-{i}.txt") for i in range(6)]
    settings = replace(get_settings(environ={}, dotenv_path=None), rerank_min_relevance_score=0.3)
    # 5 件が下限以上で、点数は検索の順の逆。
    _scores(monkeypatch, [0.4, 0.5, 0.6, 0.7, 0.8, 0.1])
    ranked = rerank_records("合成の質問", records, settings, enabled=True)
    assert [r.id for r in ranked] == ["4", "3", "2", "1", "0"]
    bundle = answering._bundle_context(
        "合成の質問", ranked, [*records, *parents], settings, top_k=3, neighbor_child_count=0, max_records=50, max_chars=5000
    )
    # 起点は rerank の点数の上位 3 件だけ（下限未満の 5 は入らない）。
    assert [r.id for r in bundle.records] == ["p4", "p3", "p2"]
    wide = answering._bundle_context(
        "合成の質問", ranked, [*records, *parents], settings, top_k=50, neighbor_child_count=0, max_records=50, max_chars=5000
    )
    # top_k が広ければ、下限を通った候補はすべて起点になる（Top-N のような別の切りは無い）。
    assert [r.id for r in wide.records] == ["p4", "p3", "p2", "p1", "p0"]


def test_attachment_below_the_minimum_is_kept(monkeypatch):
    records = [child(0), child(1, attachment=True), child(2)]
    settings = SimpleNamespace(rerank_model="test", rerank_min_relevance_score=0.5)
    _scores(monkeypatch, [0.9, 0.1, 0.2])
    result = rerank_records("合成の質問", records, settings, enabled=True)
    # 明示の添付は下限で外さない（位置は rerank の工程が決めたまま）。
    assert [r.id for r in result] == ["0", "1"]
