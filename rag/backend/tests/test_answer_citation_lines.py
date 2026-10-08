"""本文の出典行（「根拠：」）を引用の chunk に結ぶ（#1330）。"""

from typing import Any

from app.rag.answer_engine import ANSWER_CITATION_LINES_KEY, _with_citation_lines
from app.schemas.search import RetrievedChunk


def _chunk(chunk_id: str, page_start: int, page_end: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="d1",
        chunk_id=chunk_id,
        text="本文",
        score=0.5,
        file_name="規程.pdf",
        metadata={"page_start": page_start, "page_end": page_end or page_start},
    )


def _parent(parent_uid: str, *children: tuple[str, bool]) -> dict[str, Any]:
    return {
        "chunk_uid": parent_uid,
        "children": [{"chunk_id": chunk_id, "is_model_used": used} for chunk_id, used in children],
    }


def _lines(citations: list[RetrievedChunk]) -> dict[str, list[int]]:
    return {
        chunk.chunk_id: chunk.metadata[ANSWER_CITATION_LINES_KEY]
        for chunk in citations
        if ANSWER_CITATION_LINES_KEY in chunk.metadata
    }


def test_citation_lines_map_to_scoped_child_then_used_child_on_the_page() -> None:
    citations = [_chunk("c1", 2), _chunk("c2", 2), _chunk("c3", 5, 6), _chunk("c4", 9)]
    parents = [
        _parent("d1:g1", ("c1", False), ("c2", True)),
        _parent("d1:g2", ("c3", True), ("c4", True)),
    ]
    refs = [
        # 引用の位置が 1 つの子に決まっている（同じ頁の c1 と c2 を取り違えない）。
        {"source_id": "d1:g1", "scope_source_ids": ["c1"], "page": 2},
        # 決まっていなければ、回答に使った子を先に。
        {"source_id": "d1:g1", "scope_source_ids": [], "page": 2},
        # 回答に使った子が複数なら、頁の範囲に入る子。
        {"source_id": "d1:g2", "scope_source_ids": [], "page": 9},
        {"source_id": "d1:g2", "scope_source_ids": [], "page": 6},
    ]

    result = _with_citation_lines(citations, parents, refs)

    assert _lines(result) == {"c1": [1], "c2": [2], "c4": [3], "c3": [4]}
    # 元の引用の metadata は変えない（新しい chunk を返す）。
    assert ANSWER_CITATION_LINES_KEY not in citations[0].metadata


def test_unknown_parent_or_missing_refs_leave_citations_unchanged() -> None:
    citations = [_chunk("c1", 1)]

    assert _with_citation_lines(citations, [], None) == citations
    assert _with_citation_lines(citations, [], [{"source_id": "x", "page": 1}]) == citations
    # 引用に無い子は選ばない。
    unchanged = _with_citation_lines(
        citations, [_parent("p", ("gone", True))], [{"source_id": "p", "page": 1}]
    )
    assert _lines(unchanged) == {}
