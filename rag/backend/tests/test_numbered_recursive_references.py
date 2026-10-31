"""原文からの再帰分割で番号参照と出所を保持する合成回帰。"""

import pytest

from app.rag.chunking import chunk_extraction_with_strategy
from app.rag.cross_references import (
    SectionIndex,
    annotate_cross_references,
    extract_references,
    reference_targets,
)
from app.schemas.extraction import StructuredExtraction


@pytest.mark.parametrize(
    "heading,reference",
    [
        ("第5章 準備", "第5章"),
        ("第 5 章 準備", "第5章"),
        ("第五章 準備", "第五章"),
        ("5.2 準備", "5.2節"),
    ],
)
def test_recursive_raw_headings_keep_numbers_and_resolve(heading: str, reference: str) -> None:
    parent = "第5章 概要" if heading.startswith("5.2") else "第1章 概要"
    expected = f"{parent} > {heading}" if heading.startswith("5.2") else heading
    text = f"{parent}\n詳細は{reference}を参照。\n{heading}\n合成の準備を行います。\n"
    chunks = chunk_extraction_with_strategy(
        StructuredExtraction(raw_text=text),
        strategy="recursive_character",
        chunk_size=240,
        overlap=0,
        min_chars=0,
    )
    target = next(chunk for chunk in chunks if "合成の準備" in chunk.text)
    assert target.metadata["section_path"] == expected
    assert target.metadata["section_title"] == heading
    assert target.metadata["section_level"] == (2 if heading.startswith("5.2") else 1)
    for chunk in chunks:
        assert chunk.text == " ".join(text[chunk.start_offset : chunk.end_offset].split())
    annotate_cross_references(chunks)
    source = next(chunk for chunk in chunks if "詳細は" in chunk.text)
    (resolved,) = reference_targets(source.metadata)
    assert resolved.resolved
    assert resolved.section_path == expected
    # 別文書の候補索引も分割結果の番号から解決する。
    index = SectionIndex([target.metadata["section_path"]])
    assert index.resolve(extract_references(f"{reference}を参照。")[0]) == expected


def test_recursive_hierarchy_keeps_parent_and_markdown_without_inventing_numbers() -> None:
    text = (
        "第2章 合成機能\n2.1 準備\n準備の本文。\n2.2 確認\n確認の本文。\n"
        "# 番号のない見出し\nその他の本文。\n"
    )
    chunks = chunk_extraction_with_strategy(
        StructuredExtraction(raw_text=text),
        strategy="recursive_character",
        chunk_size=240,
        overlap=0,
        min_chars=0,
    )
    assert (
        next(c for c in chunks if "準備の本文" in c.text).metadata["section_path"]
        == "第2章 合成機能 > 2.1 準備"
    )
    assert (
        next(c for c in chunks if "確認の本文" in c.text).metadata["section_path"]
        == "第2章 合成機能 > 2.2 確認"
    )
    assert chunks[-1].metadata["section_path"] == "番号のない見出し"
