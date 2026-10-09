"""回答の根拠に解析の要素の ID を渡し、別レシピの同じ箇所の根拠を 1 つにまとめる (#1331)。"""

from __future__ import annotations

from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.retrieval.evidence_selection import evidence_spans

from app.rag.answer_engine import _SearchState, _stored_child, _stored_parents
from app.schemas.search import RetrievedChunk

_PARAGRAPHS = (
    "経費精算の申請は、支出した日から30日以内に経費精算画面で行います。",
    "領収書は原本を経理部へ郵送し、申請番号を封筒に記入します。",
    "立替金が5万円を超える場合は、部長の事前承認を添付します。",
)


def _chunk(chunk_id: str, metadata: dict[str, object], text: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        chunk_id=chunk_id,
        text=text,
        score=1.0,
        file_name="経費精算マニュアル.pdf",
        metadata={"page_start": 3, "section_path": "経費精算", **metadata},
    )


def test_element_ids_reach_parents_and_two_recipes_of_the_same_passage_collapse() -> None:
    parent_text = "\n".join(_PARAGRAPHS)
    # レシピ A: 親子分割。見つかった子は 2 つ目の段落で、親の本文は 3 段落。
    small_to_big = _chunk(
        "cs-a:child-2",
        {
            "chunk_group_id": "cs-a:parent-1",
            "parent_text": parent_text,
            "element_ids": "docling-p3-1,docling-p3-2",
        },
        "\n".join(_PARAGRAPHS[1:]),
    )
    # レシピ B: 構造で分けた chunk。同じ解析の要素の 2 つ目の段落だけ。
    structure = _chunk("cs-b:chunk-7", {"element_ids": "docling-p3-1"}, _PARAGRAPHS[1])
    state = _SearchState(chunks={chunk.chunk_id: chunk for chunk in (small_to_big, structure)})

    children = [_stored_child(chunk, rrf_score=1.0) for chunk in (small_to_big, structure)]
    assert children[0].metadata["element_ids"] == ["docling-p3-1", "docling-p3-2"]
    parents = _stored_parents(children, state)
    assert [parent.metadata["element_ids"] for parent in parents] == [
        ["docling-p3-1", "docling-p3-2"],
        ["docling-p3-1"],
    ]

    records = [_stored_chunk_answer_record(parent) for parent in parents]
    spans = evidence_spans("経費精算の申請方法", records)

    assert {span["source_id"] for span in spans} == {"doc-1:cs-a:parent-1"}
    assert spans[0]["source_aliases"] == ["doc-1:cs-a:parent-1", "doc-1:cs-b:chunk-7"]


def test_chunks_without_element_ids_do_not_get_the_key() -> None:
    child = _stored_child(_chunk("cs-c:fixed-1", {}, _PARAGRAPHS[0]), rrf_score=1.0)

    assert "element_ids" not in child.metadata
