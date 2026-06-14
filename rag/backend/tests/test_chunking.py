"""チャンク分割のテスト。"""

import pytest

from app.rag.chunking import chunk_extraction, chunk_text
from app.schemas.extraction import DocumentElement, StructuredExtraction


def test_chunk_text_respects_overlap_and_offsets() -> None:
    text = "これは一つ目の文です。これは二つ目の文です。これは三つ目の文です。"
    chunks = chunk_text(text, chunk_size=18, overlap=4)
    assert len(chunks) >= 2
    assert chunks[0].index == 0
    assert chunks[1].text.startswith(chunks[0].text[-4:])


def test_chunk_text_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("テスト", chunk_size=10, overlap=10)


def test_chunk_text_adds_section_and_content_metadata() -> None:
    """見出し・箇条書き・表の構造 metadata を chunk に付ける。"""
    text = """# 経費申請
## 承認
- 部門長が承認します。
- 経理部が確認します。
## 支払条件
|項目|条件|
|期限|月末|
"""

    chunks = chunk_text(text, chunk_size=80, overlap=0)

    approval = next(chunk for chunk in chunks if "部門長" in chunk.text)
    assert approval.metadata["section_title"] == "承認"
    assert approval.metadata["section_path"] == "経費申請 > 承認"
    assert approval.metadata["section_level"] == 2
    assert approval.metadata["content_kind"] == "list"
    assert isinstance(approval.metadata["chunk_group_id"], str)
    assert approval.metadata["chunk_group_kind"] == "section"
    assert approval.metadata["chunk_part_index"] == 1
    approval_part_count = approval.metadata["chunk_part_count"]
    assert isinstance(approval_part_count, int)
    assert approval_part_count >= 1
    assert approval.metadata["text_chars"] == len(approval.text)
    assert isinstance(approval.metadata["text_sha256"], str)
    assert len(str(approval.metadata["text_sha256"])) == 64

    payment = next(chunk for chunk in chunks if "月末" in chunk.text)
    assert payment.metadata["section_title"] == "支払条件"
    assert payment.metadata["content_kind"] == "table"


def test_structured_extraction_infers_elements_from_raw_text() -> None:
    """raw_text だけでも title/list/table 要素へ軽量正規化する。"""
    extraction = StructuredExtraction(raw_text="""# 経費申請
- 部門長が承認します。
|項目|条件|
|期限|月末|
""")

    assert [element.kind for element in extraction.elements] == ["title", "list", "table"]
    assert extraction.elements[1].section_path == ["経費申請"]
    raw_start = extraction.elements[2].metadata["raw_start"]
    assert isinstance(raw_start, int)
    assert raw_start >= 0


def test_structured_extraction_builds_raw_text_from_elements() -> None:
    """elements だけの VLM 出力でも raw_text fallback を合成する。"""
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(kind="title", text="第1章 概要", page_number=1),
            DocumentElement(kind="text", text="本文です。", page_number=1),
        ]
    )

    assert "本文です。" in extraction.raw_text
    assert [element.order for element in extraction.elements] == [0, 1]
    assert extraction.elements[1].section_path == ["概要"]


def test_chunk_extraction_isolates_tables_and_adds_element_metadata() -> None:
    """表は他の本文と混ぜず、ページ・要素 metadata を付ける。"""
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(kind="title", text="## 支払条件", page_number=2),
            DocumentElement(kind="text", text="支払条件の概要です。", page_number=2),
            DocumentElement(
                kind="table",
                text="|項目|条件|\n|期限|月末|",
                page_number=2,
                metadata={"element_id": "tbl-1"},
            ),
            DocumentElement(kind="text", text="補足説明です。", page_number=3),
        ]
    )

    chunks = chunk_extraction(extraction, chunk_size=80, overlap=8)

    table_chunk = next(chunk for chunk in chunks if "|期限|月末|" in chunk.text)
    assert table_chunk.text == "|項目|条件|\n|期限|月末|"
    assert table_chunk.metadata["chunk_profile"] == "structure_v1"
    assert table_chunk.metadata["content_kind"] == "table"
    assert table_chunk.metadata["section_title"] == "支払条件"
    assert table_chunk.metadata["page_start"] == 2
    assert table_chunk.metadata["page_end"] == 2
    assert table_chunk.metadata["element_ids"] == "tbl-1"
    assert table_chunk.metadata["text_chars"] == len(table_chunk.text)


def test_chunk_extraction_does_not_overlap_across_sections() -> None:
    """章節が変わるところでは overlap で前章末尾を混ぜない。"""
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(kind="title", text="# 第1章", page_number=1),
            DocumentElement(kind="text", text="前章の最後です。", page_number=1),
            DocumentElement(kind="title", text="# 第2章", page_number=1),
            DocumentElement(kind="text", text="次章の本文です。", page_number=1),
        ]
    )

    chunks = chunk_extraction(extraction, chunk_size=80, overlap=6)

    second = next(chunk for chunk in chunks if "次章の本文" in chunk.text)
    assert not second.text.startswith("最後です。")
    assert second.metadata["section_title"] == "第2章"


def test_chunk_extraction_groups_figure_with_caption() -> None:
    """図と図注は multimodal RAG 用の figure chunk として結合する。"""
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(kind="title", text="## アーキテクチャ", page_number=4),
            DocumentElement(
                kind="figure",
                text="RAG パイプライン構成図: 取込、検索、生成の流れ。",
                page_number=4,
                metadata={"element_id": "fig-1"},
            ),
            DocumentElement(
                kind="figure_caption",
                text="図1: Oracle 26ai と OCI Enterprise AI の連携。",
                page_number=4,
                metadata={"element_id": "fig-1-caption"},
            ),
            DocumentElement(kind="text", text="本文の補足説明です。", page_number=4),
        ]
    )

    chunks = chunk_extraction(extraction, chunk_size=120, overlap=8)

    figure_chunk = next(chunk for chunk in chunks if "構成図" in chunk.text)
    assert "図1" in figure_chunk.text
    assert "本文の補足説明" not in figure_chunk.text
    assert figure_chunk.metadata["content_kind"] == "figure"
    assert figure_chunk.metadata["element_kinds"] == "figure,figure_caption"
    assert figure_chunk.metadata["element_ids"] == "fig-1,fig-1-caption"
    assert figure_chunk.metadata["section_title"] == "アーキテクチャ"
    assert figure_chunk.metadata["page_start"] == 4
    assert figure_chunk.metadata["page_end"] == 4


def test_chunk_extraction_adds_parent_group_metadata_to_split_table() -> None:
    """分割された表 chunk は同一 parent group と part 順序を保持する。"""
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(kind="title", text="## 料金表", page_number=1),
            DocumentElement(
                kind="table",
                text="|項目|金額|\n|A|100|\n|B|200|\n|C|300|",
                page_number=1,
                metadata={"element_id": "tbl-price"},
            ),
        ]
    )

    chunks = chunk_extraction(extraction, chunk_size=16, overlap=0)
    table_chunks = [chunk for chunk in chunks if chunk.metadata["content_kind"] == "table"]

    assert len(table_chunks) >= 2
    group_ids = {chunk.metadata["chunk_group_id"] for chunk in table_chunks}
    assert len(group_ids) == 1
    assert all(chunk.metadata["chunk_group_kind"] == "table" for chunk in table_chunks)
    part_indexes: list[int] = []
    for chunk in table_chunks:
        part_index = chunk.metadata["chunk_part_index"]
        assert isinstance(part_index, int)
        part_indexes.append(part_index)
    assert part_indexes == list(range(1, len(table_chunks) + 1))
    part_counts: set[int] = set()
    for chunk in table_chunks:
        part_count = chunk.metadata["chunk_part_count"]
        assert isinstance(part_count, int)
        part_counts.add(part_count)
    assert part_counts == {len(table_chunks)}
    assert all(chunk.metadata["element_ids"] == "tbl-price" for chunk in table_chunks)
