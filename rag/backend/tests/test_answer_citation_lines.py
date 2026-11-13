"""本文の出典行（「根拠：」）を引用の chunk に結ぶ（#1330・#2710）。"""

import json
from typing import Any

import pytest
from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedCitation, GroundedItem

from app.rag.answer_engine import (
    ANSWER_CITATION_CANDIDATE_LINES_KEY,
    ANSWER_CITATION_LINES_KEY,
    ANSWER_CITATION_PROVENANCE_CHILD,
    ANSWER_CITATION_PROVENANCE_KEY,
    _with_citation_lines,
)
from app.rag.evaluation_evidence import used_evidence_lines
from app.rag.stored_answer import stored_citations
from app.schemas.search import RetrievedChunk


def _chunk(chunk_id: str, page_start: int, page_end: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="d1",
        chunk_id=chunk_id,
        text="本文",
        score=0.5,
        file_name="文書 A.pdf",
        metadata={"page_start": page_start, "page_end": page_end or page_start},
    )


def _parent(parent_uid: str, *children: tuple[str, bool]) -> dict[str, Any]:
    return {
        "chunk_uid": parent_uid,
        "children": [{"chunk_id": chunk_id, "is_model_used": used} for chunk_id, used in children],
    }


def _lines(citations: list[RetrievedChunk], key: str = ANSWER_CITATION_LINES_KEY) -> dict[str, Any]:
    return {chunk.chunk_id: chunk.metadata[key] for chunk in citations if key in chunk.metadata}


def test_citation_lines_map_only_to_the_children_that_support_the_line() -> None:
    citations = [_chunk("c1", 2), _chunk("c2", 2), _chunk("c3", 5, 6), _chunk("c4", 9)]
    parents = [
        _parent("d1:g1", ("c1", False), ("c2", True)),
        _parent("d1:g2", ("c3", True), ("c4", True)),
    ]
    refs = [
        # 引用の位置が 1 つの子に決まっている（同じ頁の c1 と c2 を取り違えない）。
        {"source_id": "d1:g1", "scope_source_ids": ["c1"], "page": 2},
        # 同じ出典行にまとめた説明が 2 つの子に支えられていれば、両方に番号を付ける。
        {"source_ids": ["d1:g2"], "scope_source_ids": ["c3", "c4"], "page": 5, "page_end": 9},
    ]

    result = _with_citation_lines(citations, parents, refs)

    assert _lines(result) == {"c1": [1], "c3": [2], "c4": [2]}
    assert _lines(result, ANSWER_CITATION_CANDIDATE_LINES_KEY) == {}
    # 確定の番号には決め方の印を付ける（以前の回答の番号と見分ける）。
    assert {
        chunk.chunk_id for chunk in result if chunk.metadata.get(ANSWER_CITATION_PROVENANCE_KEY)
    } == {
        "c1",
        "c3",
        "c4",
    }
    assert result[0].metadata[ANSWER_CITATION_PROVENANCE_KEY] == ANSWER_CITATION_PROVENANCE_CHILD
    # 元の引用の metadata は変えない（新しい chunk を返す）。
    assert ANSWER_CITATION_LINES_KEY not in citations[0].metadata


def test_unresolved_line_is_not_bound_to_the_first_or_used_child() -> None:
    """子の決まらない出典行は、回答に使った印・頁・引用一覧の順で 1 つを選ばない（#2710）。

    候補として残す。
    """
    # 同じ親・同じ頁の見出し H と図の説明 F。どちらも回答に使った印があり、引用一覧は H が先。
    citations = [_chunk("h", 3), _chunk("f", 3), _chunk("other", 7)]
    parents = [_parent("d1:p", ("h", True), ("f", True), ("other", True))]
    refs = [
        # rag_engine が候補を挙げた。
        {
            "source_id": "d1:p",
            "scope_source_ids": [],
            "candidate_source_ids": ["h", "f"],
            "page": 3,
        },
        # 候補も無ければ、親の子のうち出典行の頁に重なるもの。
        {"source_id": "d1:p", "scope_source_ids": [], "page": 3},
    ]

    result = _with_citation_lines(citations, parents, refs)

    assert _lines(result) == {}
    assert _lines(result, ANSWER_CITATION_CANDIDATE_LINES_KEY) == {"h": [1, 2], "f": [1, 2]}
    assert not any(ANSWER_CITATION_PROVENANCE_KEY in chunk.metadata for chunk in result)


def test_unknown_parent_or_missing_refs_leave_citations_unchanged() -> None:
    citations = [_chunk("c1", 1)]

    assert _with_citation_lines(citations, [], None) == citations
    assert _with_citation_lines(citations, [], [{"source_id": "x", "page": 1}]) == citations
    # 引用に無い子は選ばない。
    unchanged = _with_citation_lines(
        citations, [_parent("p", ("gone", True))], [{"source_id": "p", "page": 1}]
    )
    assert _lines(unchanged) == {}
    assert _lines(unchanged, ANSWER_CITATION_CANDIDATE_LINES_KEY) == {}


def test_each_item_has_its_own_citation_line_even_when_reusing_same_page_or_child() -> None:
    citations = [_chunk("a", 1), _chunk("b", 1), _chunk("candidate", 1)]
    parents = [_parent("parent", ("a", True), ("b", True), ("candidate", False))]
    refs = [
        {"source_id": "parent", "scope_source_ids": ["a"], "page": 1},
        {"source_id": "parent", "scope_source_ids": ["b"], "page": 1},
        {"source_id": "parent", "scope_source_ids": ["a", "b"], "page": 1},
        {
            "source_id": "parent",
            "scope_source_ids": [],
            "candidate_source_ids": ["candidate"],
            "page": 1,
        },
    ]
    result = _with_citation_lines(citations, parents, refs)
    assert _lines(result) == {"a": [1, 3], "b": [2, 3]}
    assert _lines(result, ANSWER_CITATION_CANDIDATE_LINES_KEY) == {"candidate": [4]}
    assert result[2].metadata.get(ANSWER_CITATION_PROVENANCE_KEY) is None


def test_citation_page_ranges_are_stored_per_exact_line() -> None:
    citations = [_chunk("c1", 3, 4)]
    refs = [
        {"source_id": "p", "scope_source_ids": ["c1"], "page": 3, "page_end": 4},
        {"source_id": "p", "scope_source_ids": ["c1"], "page": 4, "page_end": 4},
        {"source_id": "p", "scope_source_ids": ["c1"], "page": 3},
    ]
    result = _with_citation_lines(citations, [_parent("p", ("c1", True))], refs)
    assert result[0].metadata["answer_citation_page_ranges"] == [
        {"line": 1, "page_start": 3, "page_end": 4},
        {"line": 2, "page_start": 4, "page_end": 4},
    ]
    assert "answer_citation_page_ranges" not in citations[0].metadata


@pytest.mark.parametrize("name", ["合成.csv", "合成.xlsx", "合成.docx", "合成.pdf"])
@pytest.mark.parametrize("source_ids", [None, ["v1:record-a"]])
def test_direct_record_reference_survives_storage_and_evidence_export(
    name: str, source_ids: list[str] | None
) -> None:
    """親へ昇格していない分割は、引用元と木の唯一の子が同じ ID を持つ。"""
    selected = _chunk("v1:record-a", 1).model_copy(
        update={
            "file_name": name,
            "metadata": {
                "evidence_model_used": True,
                "row_start": 18,
                "row_end": 18,
            },
        }
    )
    other = _chunk("v1:record-b", 1)
    parents = [_parent(selected.chunk_id, (selected.chunk_id, True))]
    span = {
        "evidence_id": "E1",
        "label": "E1",
        "source_id": selected.chunk_id,
        "source": name,
        "page": 1,
        "text": selected.text,
        "start": 0,
        "end": len(selected.text),
    }
    item, error = grounded.cited_quote(
        GroundedItem(
            kind="rule",
            text="合成の説明です。",
            citations=[GroundedCitation(evidence_id="E1", lines="1")],
        ),
        [span],
    )
    assert not error
    _, refs = grounded.render_with_citations(
        "",
        [grounded.CheckedItem(item, span, citation_resolution=grounded._citation_extraction(item))],
    )
    assert refs[0]["scope_source_ids"] == refs[0]["candidate_source_ids"] == []
    if source_ids is None:
        refs[0].pop("source_ids")

    result = _with_citation_lines([other, selected], parents, refs)

    assert _lines(result) == {selected.chunk_id: [1]}
    assert _lines(result, ANSWER_CITATION_CANDIDATE_LINES_KEY) == {}
    assert result[1].metadata[ANSWER_CITATION_PROVENANCE_KEY] == "child"
    expected = f"{name} | 行: 18" if name.endswith((".csv", ".xlsx")) else name
    saved = json.loads(json.dumps(stored_citations(result)))
    assert used_evidence_lines(saved) == expected
    assert ANSWER_CITATION_LINES_KEY not in selected.metadata


@pytest.mark.parametrize("children", [[], [("v1:self", True), ("v1:other", True)]])
def test_direct_record_does_not_promote_missing_or_mixed_children(
    children: list[tuple[str, bool]],
) -> None:
    citations = [_chunk("v1:self", 1), _chunk("v1:other", 1)]
    refs = [{"source_id": "v1:self", "page": 1}]
    result = _with_citation_lines(citations, [_parent("v1:self", *children)], refs)
    assert _lines(result) == {}


def test_direct_reference_does_not_match_another_version_or_an_unreferenced_record() -> None:
    citations = [_chunk("v2:record-a", 1), _chunk("v1:record-b", 1)]
    parents = [
        _parent("v1:record-a", ("v1:record-a", True)),
        _parent("v1:record-b", ("v1:record-b", True)),
    ]
    result = _with_citation_lines(citations, parents, [{"source_id": "v1:record-a", "page": 1}])
    assert _lines(result) == {}


def test_one_line_can_reference_multiple_direct_records_without_citing_their_neighbors() -> None:
    citations = [_chunk(identity, 1) for identity in ("a", "b", "neighbor")]
    parents = [_parent(chunk.chunk_id, (chunk.chunk_id, True)) for chunk in citations]
    refs = [{"source_ids": ["b", "a", "b"], "page": 1}]
    result = _with_citation_lines(citations, parents, refs)
    assert _lines(result) == {"a": [1], "b": [1]}
    assert _lines(result, ANSWER_CITATION_CANDIDATE_LINES_KEY) == {}
