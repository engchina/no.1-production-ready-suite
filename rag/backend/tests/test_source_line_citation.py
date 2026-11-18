"""頁の無い原本の根拠の場所を、最下位の見出しと原本の行の範囲にする（#5450）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from rag_engine.generation.grounded import citation_source
from rag_engine.retrieval.evidence_selection import record_location_label
from rag_parser_core.source_map import SourceRange, source_map_artifact

from app.rag.answer_engine import _SearchState, _stored_child, _stored_parents
from app.rag.source_lines import apply_source_lines
from app.schemas.search import RetrievedChunk

TEXT_SOURCE = "節 A\n\n本文 A1\n本文 A2\n\n本文 A3\n\n節 B\n\n本文 B1\n本文 B2\n"
MARKDOWN_SOURCE = (
    "# 文書\n\n## 節 A\n\n本文 A1\n本文 A2\n\n本文 A3\n\n## 節 B\n\n本文 B1\n本文 B2\n"
)


def _artifacts(text: str, scheme: str, blocks: dict[str, str]) -> dict[str, Any]:
    ranges = {}
    for element_id, block in blocks.items():
        start = text.index(block)
        ranges[element_id] = [SourceRange(start, start + len(block), element_id)]
    artifact = source_map_artifact(text.encode(), text, scheme, ranges)
    assert artifact is not None
    return {"source_map": artifact}


def _chunks() -> list[SimpleNamespace]:
    """small-to-big の子 3 つ（節 A の 2 つが同じ親、節 B が別の親）。"""
    common = {"chunk_strategy": "small_to_big", "parent_text": "親の本文"}
    return [
        SimpleNamespace(
            metadata={
                **common,
                "section_title": "節 A",
                "element_ids": "a-title,a-1",
                "parent_chunk_id": "p-a",
            }
        ),
        SimpleNamespace(
            metadata={
                **common,
                "section_title": "節 A",
                "element_ids": "a-2",
                "parent_chunk_id": "p-a",
            }
        ),
        SimpleNamespace(
            metadata={
                **common,
                "section_title": "節 B",
                "element_ids": "b-1",
                "parent_chunk_id": "p-b",
            }
        ),
    ]


def _retrieved(chunk_id: str, metadata: dict[str, Any], file_name: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-a",
        chunk_id=chunk_id,
        text="本文",
        score=0.9,
        file_name=file_name,
        metadata={"chunk_group_id": metadata.get("parent_chunk_id", chunk_id), **metadata},
    )


def _citation(stored: Any) -> str:
    return citation_source(
        {
            "source": stored.source_file_name,
            "page": stored.page_start,
            "location": record_location_label(stored),
        }
    )


@pytest.mark.parametrize(
    ("file_name", "text", "scheme", "title_line"),
    [
        ("文書 A.txt", TEXT_SOURCE, "text_char", 1),
        ("文書 A.md", MARKDOWN_SOURCE, "markdown_source", 3),
    ],
)
def test_chunks_of_a_source_without_pages_cite_their_heading_and_lines(
    file_name: str, text: str, scheme: str, title_line: int
) -> None:
    heading = "## 節 A" if scheme == "markdown_source" else "節 A"
    artifacts = _artifacts(
        text,
        scheme,
        {
            "a-title": heading,
            "a-1": "本文 A1\n本文 A2",
            "a-2": "本文 A3",
            "b-1": "本文 B1\n本文 B2",
        },
    )
    chunks = _chunks()

    apply_source_lines(chunks, artifacts)

    first, second, third = (chunk.metadata for chunk in chunks)
    offset = title_line - 1
    assert (first["source_line_start"], first["source_line_end"]) == (1 + offset, 4 + offset)
    assert (second["source_line_start"], second["source_line_end"]) == (6 + offset, 6 + offset)
    assert (third["source_line_start"], third["source_line_end"]) == (10 + offset, 11 + offset)
    # 親は同じ親の子の全体の範囲。見出しがそろう親だけ見出しを持つ。
    assert (first["parent_source_line_start"], first["parent_source_line_end"]) == (
        1 + offset,
        6 + offset,
    )
    assert first["parent_section_title"] == "節 A"

    retrieved = [
        _retrieved(f"c-{index}", chunk.metadata, file_name) for index, chunk in enumerate(chunks)
    ]
    stored = [_stored_child(chunk, rrf_score=0.9) for chunk in retrieved]
    assert [_citation(item) for item in stored] == [
        f"{file_name} 「節 A」{1 + offset}〜{4 + offset} 行目",
        f"{file_name} 「節 A」{6 + offset} 行目",
        f"{file_name} 「節 B」{10 + offset}〜{11 + offset} 行目",
    ]

    state = _SearchState()
    state.chunks.update({chunk.chunk_id: chunk for chunk in retrieved})
    parents = _stored_parents(stored[:2], state)
    assert [_citation(parent) for parent in parents] == [
        f"{file_name} 「節 A」{1 + offset}〜{6 + offset} 行目"
    ]


def test_parent_spanning_sections_cites_lines_without_a_heading() -> None:
    artifacts = _artifacts(TEXT_SOURCE, "text_char", {"a-1": "本文 A1", "b-1": "本文 B1"})
    chunks = [
        SimpleNamespace(
            metadata={
                "section_title": "節 A",
                "element_ids": "a-1",
                "parent_chunk_id": "p",
                "parent_text": "x",
            }
        ),
        SimpleNamespace(
            metadata={
                "section_title": "節 B",
                "element_ids": "b-1",
                "parent_chunk_id": "p",
                "parent_text": "x",
            }
        ),
    ]

    apply_source_lines(chunks, artifacts)

    assert chunks[0].metadata["parent_source_line_start"] == 3
    assert chunks[0].metadata["parent_source_line_end"] == 10
    assert "parent_section_title" not in chunks[0].metadata


def test_heading_only_without_lines_and_file_name_only_without_either() -> None:
    """行の分からない chunk（element_ids の無い分割）は見出しだけ、見出しも無ければ文書名だけ。"""
    titled = _stored_child(
        _retrieved("c-1", {"section_title": "節 A"}, "文書 A.txt"), rrf_score=0.9
    )
    bare = _stored_child(_retrieved("c-2", {}, "文書 A.txt"), rrf_score=0.9)

    assert _citation(titled) == "文書 A.txt 「節 A」"
    assert _citation(bare) == "文書 A.txt"


def test_source_with_pages_keeps_page_citation() -> None:
    """頁のある原本（PDF など）は見出し・行があっても頁の表示のまま。"""
    stored = _stored_child(
        _retrieved(
            "c-1",
            {"page_start": 3, "page_end": 3, "section_title": "節 A", "source_line_start": 5},
            "文書 A.pdf",
        ),
        rrf_score=0.9,
    )

    assert "location_label" not in stored.metadata
    assert _citation(stored) == "文書 A.pdf p.3"


def test_fragment_scheme_ranges_do_not_become_chunk_lines() -> None:
    """1 つの要素が文書全体になる形式（CSV・JSON の単位）は、要素の行の範囲を chunk に付けない。"""
    text = "a,b\n1,2\n3,4\n"
    artifacts = {
        "source_map": source_map_artifact(
            text.encode(),
            text,
            "delimited_row",
            {"table": [SourceRange(0, 4, "r1"), SourceRange(4, 8, "r2"), SourceRange(8, 12, "r3")]},
        )
    }
    chunks = [SimpleNamespace(metadata={"element_ids": "table"})]

    apply_source_lines(chunks, artifacts)

    assert "source_line_start" not in chunks[0].metadata
