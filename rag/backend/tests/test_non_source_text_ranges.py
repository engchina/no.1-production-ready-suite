"""解析器が本文に付けた原文に無い範囲を、分割と回答の chunk の座標へ移す（#5305）。

fixture は合成の XML ツリー JSON（rag_parser_core の tests/fixtures/xml_tree_sample.json）と
合成の本文。
外部 I/O なし。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag_parser_core.non_source_text import (
    NON_SOURCE_RANGES_KEY,
    PARENT_NON_SOURCE_RANGES_KEY,
    TextRanges,
    dump_ranges,
    load_ranges,
)
from rag_parser_core.xml_tree_json import xml_tree_json_extraction
from rag_pipeline_core.chunking import (
    Chunk,
    _absorb_small_chunks,
    chunk_extraction,
    chunk_extraction_with_strategy,
)

from app.rag.answer_engine import _SearchState, _stored_child, _stored_parents
from app.rag.chunking_small_to_big import build_parent_child_chunks
from app.rag.chunking_strategy import SmallToBigParams
from app.schemas.search import RetrievedChunk

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "rag_parser_core"
    / "tests"
    / "fixtures"
    / "xml_tree_sample.json"
)


def _extraction() -> Any:
    extraction = xml_tree_json_extraction(FIXTURE.read_bytes())
    assert extraction is not None
    return extraction


def _ranges(text: str, metadata: dict[str, Any], key: str = NON_SOURCE_RANGES_KEY) -> TextRanges:
    return load_ranges(metadata.get(key), len(text))


def _assert_first_line_only(chunk: Chunk) -> None:
    heading = chunk.text.split("\n", 1)[0]
    assert _ranges(chunk.text, chunk.metadata) == ((0, len(heading)),)


def test_structure_aware_chunk_declares_only_its_heading_line() -> None:
    chunks = chunk_extraction_with_strategy(
        _extraction(), strategy="structure_aware", chunk_size=800, overlap=120, min_chars=50
    )
    assert chunks
    for chunk in chunks:
        _assert_first_line_only(chunk)


def test_split_parts_repeat_the_declared_heading_and_parent_covers_each_paragraph() -> None:
    extraction = _extraction()
    chunks = build_parent_child_chunks(
        extraction, params=SmallToBigParams(child_target_chars=60, parent_target_chars=6000)
    )
    per_element: dict[str, int] = {}
    for chunk in chunks:
        key = str(chunk.metadata.get("element_ids"))
        per_element[key] = per_element.get(key, 0) + 1
    # 長い別表を行で分けた部分がある（各部分の先頭に見出しの行を繰り返す）。
    assert max(per_element.values()) > 1
    for chunk in chunks:
        _assert_first_line_only(chunk)
    headings = {element.text.split("\n", 1)[0] for element in extraction.elements}
    for chunk in chunks:
        parent_text = str(chunk.metadata["parent_text"])
        ranges = _ranges(parent_text, chunk.metadata, PARENT_NON_SOURCE_RANGES_KEY)
        assert ranges
        # 親の本文の中の範囲は、段落ごとの見出しの行だけ（本文の行に掛からない）。
        for start, end in ranges:
            assert parent_text[start:end] in headings
            assert start == 0 or parent_text[start - 1] == "\n"
            assert end == len(parent_text) or parent_text[end] == "\n"
        assert len(ranges) == sum(1 for line in parent_text.split("\n") if line in headings)


def test_long_paragraph_parts_each_declare_the_repeated_heading() -> None:
    extraction = _extraction()
    paragraph = next(
        element for element in extraction.elements if element.metadata.get("xml_tree_label_arabic")
    )
    heading = paragraph.text.split("\n", 1)[0]
    body = "\n".join(f"合成の本文の行{index}で条件を確かめます。" for index in range(12))
    long = paragraph.model_copy(update={"text": heading + "\n" + body})
    chunks = chunk_extraction(
        extraction.model_copy(update={"elements": [long]}), chunk_size=120, overlap=0
    )
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.split("\n", 1)[0] == heading
        _assert_first_line_only(chunk)


def test_absorbing_a_small_part_keeps_both_declared_lines() -> None:
    metadata = {"chunk_group_id": "group-a", "content_kind": "text"}
    first = Chunk(
        text="見出しA\n本文の一つ目の行です。",
        index=0,
        start_offset=0,
        end_offset=10,
        metadata={**metadata, NON_SOURCE_RANGES_KEY: dump_ranges([(0, 4)])},
    )
    second = Chunk(
        text="見出しA\n短い",
        index=1,
        start_offset=10,
        end_offset=20,
        metadata={**metadata, NON_SOURCE_RANGES_KEY: dump_ranges([(0, 4)])},
    )
    (merged,) = _absorb_small_chunks([first, second], min_chars=20, chunk_size=800)
    offset = len(first.text) + 1
    assert _ranges(merged.text, merged.metadata) == ((0, 4), (offset, offset + 4))
    assert merged.text[offset : offset + 4] == "見出しA"


def _child(index: int, text: str, parent_text: str, **metadata: Any) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="document-a",
        chunk_id=f"child-{index}",
        text=text,
        score=1.0,
        file_name="synthetic.json",
        metadata={
            "chunk_strategy": "small_to_big",
            "chunk_set_id": "version-a",
            "recipe_id": "recipe-a",
            "chunk_group_id": "parent-a",
            "parent_text": parent_text,
            "engine_chunk_seq": index,
            **metadata,
        },
    )


def test_answer_chunks_carry_child_ranges_and_parents_use_the_recorded_parent_ranges() -> None:
    first, second = "見出しA\n本文Aの行です。", "見出しB\n本文Bの行です。"
    parent_text = first + "\n" + second
    parent_ranges = dump_ranges([(0, 4), (len(first) + 1, len(first) + 5)])
    children = [
        _child(
            index,
            body,
            parent_text,
            **{
                NON_SOURCE_RANGES_KEY: dump_ranges([(0, 4)]),
                PARENT_NON_SOURCE_RANGES_KEY: parent_ranges,
            },
        )
        for index, body in enumerate((first, second))
    ]
    stored = [_stored_child(value, rrf_score=1.0) for value in children]
    assert stored[0].metadata[NON_SOURCE_RANGES_KEY] == dump_ranges([(0, 4)])
    (parent,) = _stored_parents(stored, _SearchState(chunks={c.chunk_id: c for c in children}))
    assert parent.text == parent_text
    # 子の座標の範囲を親へ写さず、分割が記録した親の座標の範囲を使う。
    assert json.loads(parent.metadata[NON_SOURCE_RANGES_KEY]) == json.loads(parent_ranges)


def test_parent_without_recorded_ranges_drops_the_child_ranges() -> None:
    body = "見出しA\n本文Aの行です。"
    children = [
        _child(0, body, "前置き\n" + body, **{NON_SOURCE_RANGES_KEY: dump_ranges([(0, 4)])})
    ]
    stored = [_stored_child(value, rrf_score=1.0) for value in children]
    (parent,) = _stored_parents(stored, _SearchState(chunks={c.chunk_id: c for c in children}))
    assert NON_SOURCE_RANGES_KEY not in parent.metadata
    plain = _child(1, "本文だけの行です。", "本文だけの行です。")
    assert NON_SOURCE_RANGES_KEY not in _stored_child(plain, rrf_score=1.0).metadata
