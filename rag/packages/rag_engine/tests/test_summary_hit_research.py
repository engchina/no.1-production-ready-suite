"""補いの検索で当たった文書全体の要約の chunk を、同じ文書の本文の根拠に置き換える（#5244）。

補いの検索（要求別の補完・業務ガイドの手順）が切った起点に要約の chunk（#4793 の派生の chunk）があれば、その文書の本文に
範囲を絞って同じ検索文で検索し直し（文書の範囲つきの検索。``page_ranges``）、当たった本文の chunk をその枠の根拠にする。
要約の chunk は切りの枠を取らない。検索し直しは追加の検索の同じ総枠から取り、回答の期限の残りが足りなければしない。
検索・rerank は合成のスタブで、DB と LLM を呼ばない（``test_supplement_merge`` の Harness を使う）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from rag_parser_core.extraction import DOCUMENT_SUMMARY_CONTENT_KIND
from test_supplement_merge import CHUNKS, QUESTION, SCORES, Harness, _evidence, _run

from rag_engine.resources.deadline import Deadline, bind_deadline
from rag_engine.retrieval.extra_retrieval import SUMMARY_BODY_KIND, ExtraRetrievalBudget
from rag_engine.retrieval.guide_steps import GuidePlan
from rag_engine.retrieval.summary_hits import SummaryHitResearch, document_scope, replace_derived_hits

_SUMMARY = CHUNKS["D9"]
# 文書 D の要約の chunk（文書の中の位置を持たない派生の chunk。番号は本文の後）。backend と同じに印を付ける。
CHUNKS_WITH_SUMMARY = {
    **CHUNKS,
    "DS": replace(
        _SUMMARY,
        id="DS",
        chunk_id="DS",
        chunk_uid="DS",
        chunk_seq=10,
        page=None,
        page_end=None,
        text="DS 灰10 の合成の要約",
        body_text="DS 灰10 の合成の要約",
        metadata={**_SUMMARY.metadata, "content_kind": DOCUMENT_SUMMARY_CONTENT_KIND},
    ),
}
SUMMARY_SCORES = {**SCORES, "DS": 0.95, "D7": 0.8, "D4": 0.5}


def _research(result: Any) -> list[dict[str, Any]]:
    return list(result.generation_trace.get("summary_hits", {}).get("searches", ()))


def _scoped_searches(harness: Harness) -> list[dict[str, Any]]:
    return [kwargs for _, kwargs in harness.searches if kwargs.get("page_ranges")]


def test_request_supplement_summary_hit_is_replaced_by_body_of_the_same_document(monkeypatch: pytest.MonkeyPatch) -> None:
    # 要求の検索は文書 D の要約 DS を最上位に当てる。文書 D の本文に絞った検索では、要約 DS（派生）を除き本文 D7 が当たる。
    harness = Harness(
        {QUESTION: ["A2", "C2"], "(1)": ["DS", "E1", "A2"]},
        SUMMARY_SCORES,
        chunks=CHUNKS_WITH_SUMMARY,
        scoped={"(1)": ["DS", "D7", "D4"]},
    )

    result = _run(monkeypatch, harness, request_coverage_max_chunks=2)

    # 同じ要求の文で、文書 D の全体に範囲を絞って 1 回だけ検索し直す（要約の枠 1 件）。
    scoped = _scoped_searches(harness)
    assert [kwargs["page_ranges"] for kwargs in scoped] == [[{"document_id": "D"}]]
    assert scoped[0]["retrieval_queries"][0] == result.generation_trace["query_overflow"]["requests"][0]["query"]
    # 補いの根拠は本文 D7 と E1。要約 DS は切りの枠を取らず、根拠に入らない。
    trace = result.generation_trace["query_overflow"]
    assert trace["candidate_chunk_ids"] == ["D7", "E1"]
    evidence = _evidence(result)
    assert "DS" not in evidence and "D7" in evidence and "E1" in evidence
    assert trace["added_chunk_ids"][:1] == ["D7"]
    # 記録: 置き換えた文書・要約・本文。LLM は呼ばない。追加の検索の総枠から 1 回取る。
    rows = _research(result)
    assert [(row["phase"], row["document_id"], row["summary_chunk_ids"], row["status"], row["body_chunk_ids"]) for row in rows] == [
        ("missing_requirement", "D", ["DS"], "replaced", ["D7"])
    ]
    assert result.generation_trace["summary_hits"]["llm_calls"] == 0
    assert result.generation_trace["summary_hits"]["budget"]["used"].get(SUMMARY_BODY_KIND) == 1


def test_summary_hit_is_kept_when_the_document_body_has_no_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    # 本文に絞った検索で本文が当たらなければ（下限で外れる）、要約 DS をそのまま残す（根拠を失わない）。
    harness = Harness(
        {QUESTION: ["A2", "C2"], "(1)": ["DS", "E1"]},
        {**SUMMARY_SCORES, "D7": 0.1},
        chunks=CHUNKS_WITH_SUMMARY,
        scoped={"(1)": ["DS", "D7"]},
    )

    result = _run(monkeypatch, harness, request_coverage_max_chunks=2)

    assert result.generation_trace["query_overflow"]["candidate_chunk_ids"] == ["DS", "E1"]
    assert "DS" in _evidence(result)
    assert [row["status"] for row in _research(result)] == ["no_body"]


def test_summary_hit_is_not_searched_again_without_the_answer_deadline_reserve(monkeypatch: pytest.MonkeyPatch) -> None:
    # 回答の期限の残り（5 秒）が、検索 1 回の見積もりと生成・確定に残す分の和より少なければ検索し直さない（章節の拡張と同じ）。
    harness = Harness(
        {QUESTION: ["A2", "C2"], "(1)": ["DS", "E1"]},
        SUMMARY_SCORES,
        chunks=CHUNKS_WITH_SUMMARY,
        scoped={"(1)": ["DS", "D7"]},
    )

    with bind_deadline(Deadline(5.0)):
        result = _run(monkeypatch, harness, request_coverage_max_chunks=2)

    assert _scoped_searches(harness) == []
    assert "DS" in _evidence(result)
    assert [row["status"] for row in _research(result)] == ["unsearched_deadline"]
    assert SUMMARY_BODY_KIND not in result.generation_trace["summary_hits"]["budget"]["used"]


def test_summary_hit_is_not_searched_again_without_extra_retrieval_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # 追加の検索の総枠（1 回）を要求の検索が使い切っていれば、検索し直さない（総枠を増やさない）。
    harness = Harness(
        {QUESTION: ["A2", "C2"], "(1)": ["DS", "E1"]},
        SUMMARY_SCORES,
        chunks=CHUNKS_WITH_SUMMARY,
        scoped={"(1)": ["DS", "D7"]},
    )

    result = _run(monkeypatch, harness, request_coverage_max_chunks=2, extra_retrieval_max_queries=1)

    assert _scoped_searches(harness) == []
    assert "DS" in _evidence(result)
    assert [row["status"] for row in _research(result)] == ["unsearched_budget"]


def test_guide_step_summary_hit_is_replaced_within_the_step_reference_range(monkeypatch: pytest.MonkeyPatch) -> None:
    # 手順の参照（文書 D の 3〜8 頁）に絞った検索が要約 DS を当てたら、その範囲の本文に絞って検索し直す（範囲を広げない）。
    guide = GuidePlan.from_payload(
        "g-1",
        1,
        [
            {
                "id": "s1",
                "title": "合成の参照",
                "retrieval_hints": ["参照H"],
                "references": [{"document_id": "D", "page_ranges": [{"page_start": 3, "page_end": 8}]}],
            }
        ],
    )
    assert guide is not None
    harness = Harness(
        {QUESTION: ["A2", "C2"]},
        SUMMARY_SCORES,
        chunks=CHUNKS_WITH_SUMMARY,
        scoped={"参照H": ["DS", "D7", "D4"]},
    )

    result = _run(monkeypatch, harness, guide=guide, request=None, guide_step_chunks_per_step=1)

    scoped = _scoped_searches(harness)
    # 1 回目は手順の参照の検索、2 回目は要約の当たりの検索し直し（同じ範囲・同じ検索文）。
    assert [kwargs["page_ranges"] for kwargs in scoped] == [[{"document_id": "D", "page_start": 3, "page_end": 8}]] * 2
    assert scoped[0]["retrieval_queries"] == scoped[1]["retrieval_queries"]
    guide_trace = result.generation_trace["guide_steps"]
    assert guide_trace["added_chunk_ids"] == ["D7"]
    assert "D7" in guide_trace["required_acquisition"]["targets"][0]["candidate_source_ids"]
    assert guide_trace["required_acquisition"]["incomplete_step_ids"] == []
    assert "DS" not in _evidence(result)
    assert [(row["phase"], row["status"], row["body_chunk_ids"]) for row in _research(result)] == [("guide_step", "replaced", ["D7"])]


def test_main_search_summary_anchor_is_not_searched_again(monkeypatch: pytest.MonkeyPatch) -> None:
    # 主の検索の起点の要約は変えない（主の検索の文脈の補完は #5221 のまま）。補いの検索が無ければ記録も無い。
    harness = Harness({QUESTION: ["DS", "C2"]}, SUMMARY_SCORES, chunks=CHUNKS_WITH_SUMMARY, scoped={QUESTION: ["D7"]})

    result = _run(monkeypatch, harness, request=None)

    assert _scoped_searches(harness) == []
    assert "DS" in _evidence(result)
    assert "summary_hits" not in result.generation_trace


def test_research_does_not_take_searches_already_planned_but_not_yet_counted() -> None:
    # 任意の手順の検索は終わってから総枠に数える。その間（held）は決めた回数を検索し直しに使わない。
    budget = ExtraRetrievalBudget(max_queries=3)
    research = SummaryHitResearch(budget=budget, reserve_seconds=0.0, deadline_remaining=lambda: None, held=3)
    assert research.admit(0.0) == "unsearched_budget"
    research.held = 2
    assert research.admit(0.0) == ""
    assert budget.used == {SUMMARY_BODY_KIND: 1}


def test_research_scope_never_widens_the_original_range() -> None:
    ranges = [{"document_id": "D", "page_start": 3, "page_end": 8}, {"document_id": "E", "page_start": 1, "page_end": 2}]
    assert document_scope("D", ()) == [{"document_id": "D"}]
    assert document_scope("D", ranges) == [ranges[0]]
    # 元の検索の範囲に無い文書・文書の id の無い派生の chunk は検索し直さない。
    assert document_scope("F", ranges) == []
    assert document_scope("", ()) == []


def test_derived_hits_are_replaced_in_place_and_others_are_kept() -> None:
    records = [CHUNKS["E1"], CHUNKS_WITH_SUMMARY["DS"], CHUNKS["A2"]]
    out = replace_derived_hits(records, {"D": [CHUNKS["D7"], CHUNKS["A2"]]}, lambda record: record.chunk_uid)
    # 本文は要約の位置に検索の順で入り、すでにある起点（A2）は重ねない。
    assert [record.chunk_uid for record in out] == ["E1", "D7", "A2"]
    # 本文が無い文書の要約はそのまま残す。
    assert [record.chunk_uid for record in replace_derived_hits(records, {}, lambda record: record.chunk_uid)] == ["E1", "DS", "A2"]
