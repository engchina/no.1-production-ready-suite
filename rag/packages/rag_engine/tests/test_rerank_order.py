"""rerank の後の並び（7:3 の枠・MMR・合格点の 1 回のやり直し。#4664）。"""

from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import answering
from rag_engine.models.llm import RerankTextRank
from rag_engine.retrieval.article_catalog import ARTICLE_CATALOG_KEY
from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY
from rag_engine.retrieval.entity_expansion import is_reserved_entity_expansion
from rag_engine.retrieval.reference_follow import REFERENCE_EXPANSION_KEY
from rag_engine.retrieval.rerank_order import (
    COLUMN_RERANK,
    COLUMN_SEARCH,
    interleave_columns,
    keeps_position,
    mmr_order,
    validate_slot_ratio,
)
from tests.test_document_selection import child


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


def test_interleave_takes_seven_then_three_and_skips_placed_positions():
    first = list(range(20))
    second = list(reversed(first))
    placed = interleave_columns(first, second, (7, 3))
    assert [position for position, _ in placed[:10]] == [0, 1, 2, 3, 4, 5, 6, 19, 18, 17]
    assert [position for position, _ in placed[10:20]] == [7, 8, 9, 10, 11, 12, 13, 16, 15, 14]
    assert sorted(position for position, _ in placed) == first
    assert [label for _, label in placed[:10]] == [COLUMN_RERANK] * 7 + [COLUMN_SEARCH] * 3


def test_interleave_fills_from_the_other_column_when_one_runs_out():
    # 列 B の先頭は列 A の先頭と重なるので、列 B は入っていない候補まで進む。
    placed = interleave_columns([0, 1, 2], [0, 1, 2], (1, 1))
    assert [position for position, _ in placed] == [0, 1, 2]
    assert [position for position, _ in interleave_columns([0, 1, 2], [2, 1, 0], (0, 1))] == [2, 1, 0]
    assert [position for position, _ in interleave_columns([0, 1, 2], [2, 1, 0], (1, 0))] == [0, 1, 2]


def test_mmr_moves_similar_candidates_back_without_dropping_any():
    terms = [frozenset("abcd"), frozenset("abcd"), frozenset("wxyz")]
    # 2 番目は 1 番目と同じ内容なので、点数が高くても 3 番目の後ろへ回る。
    assert mmr_order([0.9, 0.85, 0.7], terms) == [0, 2, 1]
    assert mmr_order([], []) == []


def test_mmr_keeps_order_when_the_score_gap_outweighs_redundancy():
    terms = [frozenset("ab"), frozenset("ab"), frozenset("wx")]
    assert mmr_order([0.9, 0.88, 0.2], terms) == [0, 1, 2]


@pytest.mark.parametrize("ratio", [(7, 3), [1, 1], (0, 3), (3, 0)])
def test_validate_slot_ratio_accepts_counts(ratio):
    assert validate_slot_ratio(ratio) == tuple(ratio)


@pytest.mark.parametrize("ratio", [(0, 0), (-1, 2), (1.5, 1), (True, 1), [1], "73"])
def test_validate_slot_ratio_rejects_invalid(ratio):
    with pytest.raises(ValueError):
        validate_slot_ratio(ratio)


def test_slots_mix_rerank_and_search_order_before_top_n(monkeypatch):
    # rerank の順は検索の順の逆。上位 10 件は rerank の上位 7 件と、検索の上位 3 件になる。
    records = _records(20)
    scores = [round(0.3 + i * 0.03, 2) for i in range(20)]
    stages = []
    ranked, step = _run(monkeypatch, records, scores, _settings(rerank_top_n=10), stages=stages)
    assert [r.id for r in ranked] == ["c19", "c18", "c17", "c16", "c15", "c14", "c13", "c0", "c1", "c2"]
    # 点数は素点のまま（合成点を作らない）。
    assert ranked[7].metadata["rerank"]["relevance_score"] == scores[0]
    assert ranked[0].metadata["rerank"]["rank"] == 1
    trace = next(row for row in stages if row["stage"] == "rerank_slot_order")
    assert trace["ratio"] == [7, 3]
    assert (trace["rerank_slot_count"], trace["search_slot_count"]) == (14, 6)
    assert step.fields["並びの枠（rerank の順 : 検索の順）"] == "7 : 3"


def test_slot_ratio_setting_changes_the_columns(monkeypatch):
    records = _records(4)
    ranked, _ = _run(monkeypatch, records, [0.4, 0.5, 0.6, 0.7], _settings(rerank_slot_ratio=(1, 1)))
    assert [r.id for r in ranked] == ["c3", "c0", "c2", "c1"]


def test_similar_candidates_are_moved_back_only_in_the_rerank_column(monkeypatch):
    records = [
        child("d1", 0, "alpha bravo charlie delta", "1.txt", "p1"),
        child("d2", 1, "alpha bravo charlie delta", "2.txt", "p2"),
        child("u", 2, "uniform victor whiskey", "3.txt", "p3"),
    ]
    ranked, _ = _run(monkeypatch, records, [0.9, 0.88, 0.7], _settings(rerank_slot_ratio=(1, 0)))
    assert [r.id for r in ranked] == ["d1", "u", "d2"]


def test_fixed_candidates_stay_in_place(monkeypatch):
    records = _records(14)
    records[3].metadata[ATTACHMENT_EVIDENCE_KEY] = True
    records[5].metadata[ARTICLE_CATALOG_KEY] = {"added": True}
    records[9].metadata[REFERENCE_EXPANSION_KEY] = {"from_chunk_uid": "run:c0", "rank": 1}
    scores = [round(0.95 - i * 0.03, 2) for i in range(14)]
    ranked, _ = _run(monkeypatch, records, scores, _settings(rerank_slot_ratio=(1, 1)))
    assert [r.id for r in ranked].index("c3") == 3
    assert [r.id for r in ranked].index("c5") == 5
    # 参照先は参照元の直後へ移る（follow_reference_sources は rerank_records の外側の段）。
    assert sorted(r.id for r in ranked) == sorted(r.id for r in records)


def test_keeps_position_covers_unscored_and_reserved_candidates():
    scored = child("a", 1, "alpha", "a.txt", "p", 0.8)
    unscored = child("b", 2, "bravo", "b.txt", "p")
    expansion = child("c", 3, "charlie", "c.txt", "p", 0.8)
    expansion.metadata["entity_expansion"] = {"hop": 0}
    assert is_reserved_entity_expansion(expansion)
    assert keeps_position(unscored) is True
    assert keeps_position(expansion) is True
    assert keeps_position(scored) is False


def test_all_below_threshold_retries_once_with_a_lower_minimum(monkeypatch):
    records = _records(3)
    stages = []
    ranked, step = _run(monkeypatch, records, [0.25, 0.22, 0.2], _settings(rerank_min_relevance_score=0.3), stages=stages)
    # 0.3 × 0.7 = 0.21 以上の 2 件だけ。最高点の 1 件だけ残す救済はしない。
    assert [r.id for r in ranked] == ["c0", "c1"]
    assert ranked[0].metadata["rerank"]["relevance_score"] == 0.25
    trace = next(row for row in stages if row["stage"] == "rerank_threshold")
    assert (trace["relaxed_from"], trace["minimum"]) == (0.3, 0.21)
    assert "やり直し" in step.fields["関連度スコアの下限"]


def test_retry_ending_with_no_candidates_returns_zero_evidence(monkeypatch):
    records = _records(2)
    stages = []
    ranked, _ = _run(monkeypatch, records, [0.2, 0.1], _settings(rerank_min_relevance_score=0.3), stages=stages)
    assert ranked == []
    assert next(row for row in stages if row["stage"] == "rerank_threshold")["relaxed_from"] == 0.3


def test_partial_pass_does_not_retry(monkeypatch):
    records = _records(3)
    stages = []
    ranked, _ = _run(monkeypatch, records, [0.5, 0.25, 0.1], _settings(rerank_min_relevance_score=0.3), stages=stages)
    assert [r.id for r in ranked] == ["c0"]
    assert next(row for row in stages if row["stage"] == "rerank_threshold")["relaxed_from"] is None


def test_kept_attachment_is_not_treated_as_all_failed(monkeypatch):
    records = _records(2)
    records[0].metadata[ATTACHMENT_EVIDENCE_KEY] = True
    stages = []
    ranked, _ = _run(monkeypatch, records, [0.05, 0.25], _settings(rerank_min_relevance_score=0.3), stages=stages)
    assert [r.id for r in ranked] == ["c0"]
    assert next(row for row in stages if row["stage"] == "rerank_threshold")["relaxed_from"] is None


def test_rerank_failure_keeps_search_order_without_slots(monkeypatch):
    records = _records(12)
    stages = []
    monkeypatch.setattr(answering, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(answering, "record_retrieval_stage", stages.append)

    def boom(*args, **kwargs):
        raise RuntimeError("rerank failed")

    monkeypatch.setattr(answering, "rerank_text_with_scores", boom)
    ranked = answering._rerank_records("質問", records, _settings(), Step(), enabled=True, candidate_limit=None)
    assert [r.id for r in ranked] == [r.id for r in records]
    assert not any(row["stage"] == "rerank_slot_order" for row in stages)


def test_rerank_disabled_keeps_search_order_without_slots(monkeypatch):
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
    assert not any(row["stage"] == "rerank_slot_order" for row in stages)


def test_search_slot_chunk_protects_its_document_from_deferral():
    """列 B で上位 top_k に入った chunk の文書は、点数が低くても後回しにしない。"""
    high = child("h", 1, "alpha", "high.txt", "ph", 0.9)
    low = child("l", 2, "bravo", "low.txt", "pl", 0.1)
    settings = _settings(document_selection_enabled=True, document_selection_score_ratio=0.5)
    kept, deferred, _ = answering.select_documents([high, low], settings, top_k=2)
    assert [r.id for r in kept] == ["h", "l"] and deferred == []
    kept, deferred, _ = answering.select_documents([high, low], settings, top_k=1)
    assert [r.id for r in kept] == ["h"] and [r.id for r in deferred] == ["l"]
