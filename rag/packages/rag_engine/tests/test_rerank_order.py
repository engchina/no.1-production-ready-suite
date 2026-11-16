"""rerank の後の並び（rerank の点数の順と MMR だけ）と関連度の下限（#5165）。"""

import random
from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import answering
from rag_engine.models.llm import RerankTextRank
from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY
from rag_engine.retrieval.expansion_merge import ENTITY_EXPANSION_KEY
from rag_engine.retrieval.rerank_order import keeps_position, mmr_order
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


def test_mmr_moves_similar_candidates_back_without_dropping_any():
    terms = [frozenset("abcd"), frozenset("abcd"), frozenset("wxyz")]
    # 2 番目は 1 番目と同じ内容なので、点数が高くても 3 番目の後ろへ回る。
    assert mmr_order([0.9, 0.85, 0.7], terms) == [0, 2, 1]
    assert mmr_order([], []) == []


def test_mmr_keeps_order_when_the_score_gap_outweighs_redundancy():
    terms = [frozenset("ab"), frozenset("ab"), frozenset("wx")]
    assert mmr_order([0.9, 0.88, 0.2], terms) == [0, 1, 2]


# 改修前（#4679 の前）の mmr_order で記録した順序。計算の方法を変えても順序は変えない。
_RECORDED_MMR_ORDERS = [
    (4679, 4, 0.0, "0 1 2 4 3 5 6 8 9 7 10 12 11 13 14 20 15 16 17 19 18 21 22 24 23 26 25 28 27 30 29 32 33 34 31 39 35 37 36 38"),
    # 点数の同点が多い場合。同点の中では先の候補を取る。
    (4680, 1, 0.0, "0 1 2 5 7 4 6 3 8 9 10 12 11 13 15 16 14 17 18 20 22 21 19 23 26 24 25 27 29 35 28 31 33 30 32 34 37 38 36 39"),
    # 語の無い候補を含む場合。
    (4681, 2, 0.2, "0 3 1 2 6 4 7 5 9 10 13 8 12 11 14 15 21 16 18 17 23 19 20 22 24 29 31 25 33 26 32 34 28 27 30 37 35 36 39 38"),
]


@pytest.mark.parametrize(("seed", "digits", "empty_ratio", "expected"), _RECORDED_MMR_ORDERS)
def test_mmr_order_keeps_the_recorded_order(seed, digits, empty_ratio, expected):
    rng = random.Random(seed)
    vocabulary = [f"term{i}" for i in range(150)]
    scores = sorted((round(rng.uniform(0.1, 0.99), digits) for _ in range(40)), reverse=True)
    terms = [frozenset() if rng.random() < empty_ratio else frozenset(rng.sample(vocabulary, rng.randint(1, 60))) for _ in range(40)]
    assert mmr_order(scores, terms) == [int(position) for position in expected.split()]


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


def test_similar_candidates_are_moved_back(monkeypatch):
    records = [
        child("d1", 0, "alpha bravo charlie delta", "1.txt", "p1"),
        child("d2", 1, "alpha bravo charlie delta", "2.txt", "p2"),
        child("u", 2, "uniform victor whiskey", "3.txt", "p3"),
    ]
    ranked, _ = _run(monkeypatch, records, [0.9, 0.88, 0.7], _settings())
    assert [r.id for r in ranked] == ["d1", "u", "d2"]


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


def test_all_below_threshold_returns_no_evidence(monkeypatch):
    records = _records(3)
    stages = []
    ranked, step = _run(monkeypatch, records, [0.29, 0.22, 0.2], _settings(rerank_min_relevance_score=0.3), stages=stages)
    # 下限を下げてやり直さず、最高点の 1 件を残す救済もしない（根拠なし）。
    assert ranked == []
    trace = next(row for row in stages if row["stage"] == "rerank_threshold")
    assert trace["minimum"] == 0.3 and trace["excluded_count"] == 3
    assert "relaxed_from" not in trace
    assert step.fields["関連度スコアの下限"] == "0.3"


def test_partial_pass_keeps_only_candidates_at_or_above_the_minimum(monkeypatch):
    records = _records(3)
    ranked, _ = _run(monkeypatch, records, [0.5, 0.3, 0.1], _settings(rerank_min_relevance_score=0.3))
    assert [r.id for r in ranked] == ["c0", "c1"]


def test_kept_attachment_is_not_treated_as_all_failed(monkeypatch):
    records = _records(2)
    records[0].metadata[ATTACHMENT_EVIDENCE_KEY] = True
    stages = []
    ranked, _ = _run(monkeypatch, records, [0.05, 0.25], _settings(rerank_min_relevance_score=0.3), stages=stages)
    assert [r.id for r in ranked] == ["c0"]


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
