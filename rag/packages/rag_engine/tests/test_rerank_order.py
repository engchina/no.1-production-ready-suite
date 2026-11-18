"""rerank の後の並び（rerank の点数の順だけ。MMR は #5443 で削除）と関連度の下限（#5165・#5443）。"""

from dataclasses import replace

from rag_engine.config import get_settings
from rag_engine.generation import answering
from rag_engine.models.llm import RerankTextRank
from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY
from rag_engine.retrieval.expansion_merge import ENTITY_EXPANSION_KEY
from rag_engine.retrieval.rerank_order import keeps_position
from tests.record_fixtures import child


class Step:
    def __init__(self):
        self.fields, self.lines, self.status = {}, [], ""

    def field(self, label, value):
        self.fields[label] = value

    def add(self, *lines):
        self.lines.extend(lines)


def _records(count):
    return [child(f"c{i}", i, f"unique{i}word alpha{i} beta{i}", f"{i}.txt", f"p{i}") for i in range(count)]


def _settings(**overrides):
    return replace(get_settings(environ={}, dotenv_path=None), **overrides)


def _run(monkeypatch, records, scores, settings, *, stages=None):
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(
        answering,
        "rerank_text_with_scores",
        lambda query, documents, settings, top_n: [
            RerankTextRank(i, scores[i]) for i in sorted(range(len(scores)), key=lambda i: -scores[i])
        ],
    )
    monkeypatch.setattr(answering, "record_retrieval_stage", (stages if stages is not None else []).append)
    step = Step()
    return answering._rerank_records("質問", records, settings, step, enabled=True, candidate_limit=None), step


def test_order_follows_rerank_scores_only(monkeypatch):
    # rerank の順は検索の順の逆。検索の順は混ぜず、点数の順に並ぶ（Top-N で切らない）。
    records = _records(20)
    scores = [round(0.3 + i * 0.03, 2) for i in range(20)]
    stages = []
    ranked, step = _run(monkeypatch, records, scores, _settings(), stages=stages)
    assert [r.id for r in ranked] == [f"c{i}" for i in reversed(range(20))]
    # 点数は素点のまま（合成点を作らない）。
    assert ranked[-1].metadata["rerank"]["relevance_score"] == scores[0]
    assert ranked[0].metadata["rerank"]["rank"] == 1
    trace = next(row for row in stages if row["stage"] == "rerank_order")
    assert trace["order"] == [r.chunk_uid for r in ranked]
    assert not any(row["stage"] in {"rerank_slot_order", "rerank_selection"} for row in stages)
    assert "並び" in step.fields


def test_similar_candidates_are_not_moved_back(monkeypatch):
    """内容の似た候補も rerank の点数の順のまま（MMR は #5443 で削除）。"""
    records = [
        child("d1", 0, "alpha bravo charlie delta", "1.txt", "p1"),
        child("d2", 1, "alpha bravo charlie delta", "2.txt", "p2"),
        child("u", 2, "uniform victor whiskey", "3.txt", "p3"),
    ]
    ranked, _ = _run(monkeypatch, records, [0.9, 0.88, 0.7], _settings())
    assert [r.id for r in ranked] == ["d1", "d2", "u"]


def test_fixed_candidates_stay_in_place(monkeypatch):
    records = _records(14)
    records[3].metadata[ATTACHMENT_EVIDENCE_KEY] = True
    scores = [round(0.95 - i * 0.03, 2) for i in range(14)]
    ranked, _ = _run(monkeypatch, records, scores, _settings())
    assert [r.id for r in ranked].index("c3") == 3
    assert sorted(r.id for r in ranked) == sorted(r.id for r in records)


def test_keeps_position_covers_unscored_and_attachment_candidates():
    scored = child("a", 1, "alpha", "a.txt", "p", 0.8)
    unscored = child("b", 2, "bravo", "b.txt", "p")
    attached = child("c", 3, "charlie", "c.txt", "p", 0.8)
    attached.metadata[ATTACHMENT_EVIDENCE_KEY] = True
    # 拡張の印は rerank の後の並びに効かない（検索拡張の根拠は rerank の候補に入らない。#5162）。
    marked = child("d", 4, "delta", "d.txt", "p", 0.8)
    marked.metadata[ENTITY_EXPANSION_KEY] = {"hop": 0}
    assert keeps_position(unscored) is True
    assert keeps_position(attached) is True
    assert keeps_position(scored) is False
    assert keeps_position(marked) is False


def test_all_below_threshold_lowers_the_minimum_once(monkeypatch):
    """資料の候補が 1 件も下限に届かないとき、下限を 0.5 倍にして 1 回だけ判断し直す（#5443）。"""
    records = _records(3)
    stages = []
    ranked, step = _run(monkeypatch, records, [0.29, 0.22, 0.1], _settings(rerank_min_relevance_score=0.3), stages=stages)
    assert [r.id for r in ranked] == ["c0", "c1"]
    trace = next(row for row in stages if row["stage"] == "rerank_threshold")
    assert trace["minimum"] == 0.15 and trace["configured_minimum"] == 0.3 and trace["relaxed"] is True
    assert trace["excluded_count"] == 1
    assert step.fields["関連度スコアの下限"].startswith("0.15")


def test_below_the_lowered_minimum_returns_no_evidence(monkeypatch):
    records = _records(3)
    stages = []
    ranked, _ = _run(monkeypatch, records, [0.14, 0.12, 0.1], _settings(rerank_min_relevance_score=0.3), stages=stages)
    assert ranked == []
    trace = next(row for row in stages if row["stage"] == "rerank_threshold")
    assert trace["minimum"] == 0.15 and trace["excluded_count"] == 3


def test_zero_ratio_does_not_lower_the_minimum(monkeypatch):
    records = _records(3)
    stages = []
    settings = _settings(rerank_min_relevance_score=0.3, rerank_min_relevance_fallback_ratio=0.0)
    ranked, step = _run(monkeypatch, records, [0.29, 0.22, 0.2], settings, stages=stages)
    assert ranked == []
    trace = next(row for row in stages if row["stage"] == "rerank_threshold")
    assert trace["minimum"] == 0.3 and trace["relaxed"] is False
    assert step.fields["関連度スコアの下限"] == "0.3"


def test_partial_pass_keeps_only_candidates_at_or_above_the_minimum(monkeypatch):
    records = _records(3)
    ranked, _ = _run(monkeypatch, records, [0.5, 0.3, 0.1], _settings(rerank_min_relevance_score=0.3))
    assert [r.id for r in ranked] == ["c0", "c1"]


def test_attachment_does_not_count_as_a_passing_candidate(monkeypatch):
    """添付は下限で外さないが、資料の候補が下限に届いたかの判断には数えない（#5443）。"""
    records = _records(2)
    records[0].metadata[ATTACHMENT_EVIDENCE_KEY] = True
    stages = []
    ranked, _ = _run(monkeypatch, records, [0.05, 0.25], _settings(rerank_min_relevance_score=0.3), stages=stages)
    assert sorted(r.id for r in ranked) == ["c0", "c1"]
    trace = next(row for row in stages if row["stage"] == "rerank_threshold")
    assert trace["relaxed"] is True


def test_rerank_failure_keeps_search_order(monkeypatch):
    records = _records(12)
    stages = []
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(answering, "record_retrieval_stage", stages.append)

    def boom(*args, **kwargs):
        raise RuntimeError("rerank failed")

    monkeypatch.setattr(answering, "rerank_text_with_scores", boom)
    ranked = answering._rerank_records("質問", records, _settings(), Step(), enabled=True, candidate_limit=None)
    # 失敗は根拠なしにせず、検索の順のまま全部を残す（下限・並べ替えをしない）。
    assert [r.id for r in ranked] == [r.id for r in records]
    assert not any(row["stage"] in {"rerank_order", "rerank_threshold"} for row in stages)


def test_rerank_disabled_keeps_search_order(monkeypatch):
    records = _records(12)
    ranked = answering._rerank_records("質問", records, _settings(), Step(), enabled=False, candidate_limit=None)
    assert [r.id for r in ranked] == [r.id for r in records]


def test_no_scores_keeps_order_and_does_not_reorder(monkeypatch):
    records = _records(12)
    stages = []
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(answering, "record_retrieval_stage", stages.append)
    monkeypatch.setattr(
        answering,
        "rerank_text_with_scores",
        lambda query, documents, settings, top_n: [RerankTextRank(i, None) for i in range(len(documents))],
    )
    ranked = answering._rerank_records("質問", records, _settings(), Step(), enabled=True, candidate_limit=None)
    assert [r.id for r in ranked] == [r.id for r in records]
    assert not any(row["stage"] == "rerank_order" for row in stages)
