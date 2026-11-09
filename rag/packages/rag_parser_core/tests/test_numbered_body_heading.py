"""文末の記号で終わる番号付きの行を見出しにしない規則(#4742)。"""

import pytest

from rag_parser_core.extraction import (
    DocumentElement,
    StructuredExtraction,
    infer_document_elements,
)


def _normalized(*elements: DocumentElement) -> list[DocumentElement]:
    return StructuredExtraction(raw_text="合成", elements=list(elements)).elements


@pytest.mark.parametrize(
    "line",
    [
        "1.1 合成の手順を確認する。",
        "1. 合成の値を入力する。",
        "(2) 合成の条件を満たすか！",
        "第 3 章 合成の範囲を定める。",
        "2.4 合成の値を保存するか？",
        "1.1 空白が続く文の行である。　 ",
    ],
)
def test_sentence_ending_numbered_line_at_document_start_is_body(line: str) -> None:
    body, following = _normalized(
        DocumentElement(kind="text", text=line),
        DocumentElement(kind="text", text="続く本文。"),
    )

    assert body.kind == "text"
    assert body.section_path == []
    assert "section_level" not in body.metadata
    assert following.section_path == []


def test_sentence_ending_numbered_line_keeps_the_current_section() -> None:
    heading, body, following = _normalized(
        DocumentElement(kind="text", text="1.1 概要"),
        DocumentElement(kind="text", text="1.2 合成の手順を確認する。"),
        DocumentElement(kind="text", text="続く本文。"),
    )

    assert heading.kind == "title"
    assert heading.section_path == ["概要"]
    assert body.kind == "text"
    assert body.section_path == ["概要"]
    assert following.section_path == ["概要"]


@pytest.mark.parametrize(
    ("line", "level", "title"),
    [
        ("1.1 概要", 2, "概要"),
        ("1. 概要", 1, "概要"),
        ("(2) 概要", 1, "概要"),
        ("第 3 章 概要", 1, "概要"),
        # 語尾で文を判定しない。文末の記号が無ければ見出しのまま。
        ("1.1 手順を確認する", 2, "手順を確認する"),
    ],
)
def test_numbered_heading_without_sentence_end_stays_heading(
    line: str, level: int, title: str
) -> None:
    heading, following = _normalized(
        DocumentElement(kind="text", text=line),
        DocumentElement(kind="text", text="続く本文。"),
    )

    assert heading.kind == "title"
    assert heading.section_path == [title]
    assert heading.metadata["section_level"] == level
    assert following.section_path == [title]


def test_markdown_heading_with_sentence_end_stays_heading() -> None:
    """書き手が付けた Markdown の見出しの印は、文末の記号があっても見出しのまま。"""
    heading, following = _normalized(
        DocumentElement(kind="text", text="## 1.1 合成の手順を確認する。"),
        DocumentElement(kind="text", text="続く本文。"),
    )

    assert heading.kind == "title"
    assert heading.section_path == ["1.1 合成の手順を確認する。"]
    assert following.section_path == ["1.1 合成の手順を確認する。"]


def test_parser_classification_and_section_path_are_kept() -> None:
    """解析エンジンが付けた種類と section_path は、番号付きの文の行でも変えない。"""
    numbered_list, parser_title, following = _normalized(
        DocumentElement(kind="list", text="1. 合成の値を入力する。", section_path=["節 A"]),
        DocumentElement(
            kind="title", text="2.1 合成の値を入力する。", section_path=["節 A", "節 B"]
        ),
        DocumentElement(kind="text", text="続く本文。"),
    )

    assert numbered_list.kind == "list"
    assert numbered_list.content_kind == "list"
    assert numbered_list.section_path == ["節 A"]
    assert parser_title.kind == "title"
    assert parser_title.section_path == ["節 A", "節 B"]
    assert following.section_path == ["節 A", "節 B"]


def test_inferred_elements_from_plain_text_follow_the_same_rule() -> None:
    elements = infer_document_elements(
        "1.1 合成の手順を確認する。\n続く本文。\n\n1.2 概要\n本文 B。"
    )

    headings = [element for element in elements if element.kind == "title"]
    assert [heading.text for heading in headings] == ["1.2 概要"]
    assert headings[0].section_path == ["概要"]
    assert elements[0].section_path == []
    assert elements[-1].section_path == ["概要"]
