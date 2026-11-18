"""Unstructured の要素の本文に原本の改行・字下げ・空行を戻す回帰（#5422）。内容はすべて合成。"""

from typing import Any

from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.registry import _restored_line_breaks, _with_source_layout
from rag_parser_core.source_map import (
    SCHEME_MARKDOWN_SOURCE,
    SCHEME_TEXT_CHAR,
    SourceRange,
    source_map_artifact,
)

from app.rag.chunking import chunk_extraction_with_strategy


def _extraction(
    source: str, scheme: str, parts: list[tuple[str, str, int, int]]
) -> tuple[StructuredExtraction, dict[str, Any]]:
    data = source.encode()
    source_map = source_map_artifact(
        data,
        source,
        scheme,
        {key: [SourceRange(start, end, f"char={start},{end}")] for key, _, start, end in parts},
    )
    assert source_map is not None
    elements = [
        DocumentElement(element_id=key, text=text, order=order)
        for order, (key, text, _, _) in enumerate(parts)
    ]
    raw_text = "\n\n".join(text for _, text, _, _ in parts)
    return StructuredExtraction(raw_text=raw_text, elements=elements), source_map


def test_markdown_paragraph_line_breaks_are_restored_without_changing_characters() -> None:
    source = "# 手順\n\n①準備\nexport X=1\n②起動する\n\n合成の説明です。\n"
    paragraph = "①準備\nexport X=1\n②起動する"
    start = source.index(paragraph)
    extraction, source_map = _extraction(
        source,
        SCHEME_MARKDOWN_SOURCE,
        [
            ("h", "手順", 0, 4),
            ("p", "①準備 export X=1 ②起動する", start, start + len(paragraph) + 1),
            # 範囲が後ろの空行を含んでも、次の要素の前の空行を見落とさない。
            ("q", "合成の説明です。", source.index("合成"), len(source) - 1),
        ],
    )
    result = _with_source_layout(extraction, source.encode(), source_map)
    texts = [element.text for element in result.elements]
    assert texts == ["手順", "①準備\nexport X=1\n②起動する", "合成の説明です。"]
    # 要素の数・ID は変えない。前に空行がある要素に印を付ける。
    assert [element.element_id for element in result.elements] == ["h", "p", "q"]
    assert [element.metadata.get("source_blank_line_before") for element in result.elements] == [
        None,
        True,
        True,
    ]


def test_markup_between_characters_is_left_unchanged() -> None:
    """記法（リンクの URL・強調）を挟む所は空白のまま。文字は増減しない。"""
    value = "前の文 リンク 次の文"
    source = "前の文 [リンク](http://example.invalid/次)\n次の文"
    restored = _restored_line_breaks(value, source)
    assert restored.replace("\n", " ") == value
    assert _restored_line_breaks("太字 の文", "**太字**\nの文") == "太字 の文"


def test_text_indentation_and_blank_lines_are_restored_and_kept_by_chunking() -> None:
    source = "①事前作業\n\n export X=1\n export Y=2\n②起動する\n"
    lines = ["①事前作業", "export X=1", "export Y=2", "②起動する"]
    parts = []
    for index, line in enumerate(lines):
        start = source.index(line)
        parts.append((f"e{index}", line, start, start + len(line)))
    extraction, source_map = _extraction(source, SCHEME_TEXT_CHAR, parts)
    result = _with_source_layout(extraction, source.encode(), source_map)
    assert [element.text for element in result.elements] == [
        "①事前作業",
        " export X=1",
        " export Y=2",
        "②起動する",
    ]
    assert result.elements[1].metadata.get("source_blank_line_before") is True
    assert result.raw_text == "①事前作業\n\n export X=1\n export Y=2\n②起動する"
    for strategy in ("recursive_character", "small_to_big", "structure_aware"):
        chunks = chunk_extraction_with_strategy(
            result, strategy=strategy, chunk_size=500, overlap=0, min_chars=0
        )
        assert any(
            "①事前作業\n\n export X=1\n export Y=2\n②起動する" in chunk.text for chunk in chunks
        ), strategy


def test_other_schemes_are_left_unchanged() -> None:
    source = '{"a": "x y"}'
    extraction, source_map = _extraction(source, "json_pointer", [("a", "x y", 7, 10)])
    assert _with_source_layout(extraction, source.encode(), source_map) is extraction
