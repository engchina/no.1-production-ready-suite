"""Unstructured の要素から section_path を作る規則(#4741)。"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from rag_parser_core.registry import _structured_from_adapter_elements, _unstructured_adapter_result

BODY = (
    "この段落では合成の処理の流れについて説明する。入力の版を記録し、同じ版の入力には同じ結果を返す。"
    "途中で止まった場合は、最後に完了した境界から再開する。"
)


def _element(category, text, *, depth=None, filetype="text/plain"):
    return SimpleNamespace(
        category=category,
        text=text,
        metadata=SimpleNamespace(category_depth=depth, filetype=filetype),
    )


def _parse(monkeypatch, elements, content_type):
    module = ModuleType("unstructured.partition.auto")
    module.partition = lambda **_: elements
    monkeypatch.setitem(sys.modules, "unstructured.partition.auto", module)
    return _unstructured_adapter_result(
        b"source", source_profile=None, content_type=content_type
    ).extraction.elements


def _paths(elements):
    return [(element.kind, element.text[:12], element.section_path) for element in elements]


def test_plain_text_paragraph_classified_as_title_stays_out_of_section_path(monkeypatch):
    elements = _parse(
        monkeypatch,
        [
            _element("Title", "第1章 合成の概要"),
            _element("Title", BODY),
            _element("Title", BODY),
        ],
        "text/plain",
    )
    assert [element.kind for element in elements] == ["title", "text", "text"]
    assert [element.section_path for element in elements] == [["第1章 合成の概要"]] * 3


def test_plain_text_numbered_heading_classified_as_narrative_starts_a_section(monkeypatch):
    elements = _parse(
        monkeypatch,
        [
            _element("Title", "第1章 合成の概要"),
            _element("NarrativeText", BODY),
            _element("NarrativeText", "第2章 合成の構成"),
            _element("NarrativeText", BODY),
        ],
        "text/plain",
    )
    assert [element.kind for element in elements] == ["title", "text", "title", "text"]
    assert [element.section_path for element in elements] == [
        ["第1章 合成の概要"],
        ["第1章 合成の概要"],
        ["第2章 合成の構成"],
        ["第2章 合成の構成"],
    ]


def test_plain_text_numbered_headings_take_their_level_from_the_number(monkeypatch):
    elements = _parse(
        monkeypatch,
        [
            _element("Title", "第1章 合成の概要"),
            _element("NarrativeText", "1.1 合成の節"),
            _element("NarrativeText", BODY),
            _element("Title", "第2章 合成の構成"),
        ],
        "text/plain",
    )
    assert elements[2].section_path == ["第1章 合成の概要", "1.1 合成の節"]
    assert elements[3].section_path == ["第2章 合成の構成"]


@pytest.mark.parametrize(
    "line",
    [
        "10 合成の値 2024-01-01",  # 区切りの番号だけの行(表の行に近い)
        "(3) 合成の項目",
        "第1章は合成の手順を説明する。",  # 文で終わる
    ],
)
def test_plain_text_body_lines_with_number_like_prefix_are_not_promoted(monkeypatch, line):
    elements = _parse(
        monkeypatch,
        [_element("Title", "第1章 合成の概要"), _element("NarrativeText", line)],
        "text/plain",
    )
    assert elements[1].section_path == ["第1章 合成の概要"]


def test_long_title_without_terminal_is_body(monkeypatch):
    long_line = "合成の値が続く行" * 20
    elements = _parse(
        monkeypatch,
        [_element("Title", "第1章 合成の概要"), _element("Title", long_line)],
        "text/plain",
    )
    assert elements[1].kind == "text"
    assert elements[1].section_path == ["第1章 合成の概要"]


@pytest.mark.parametrize(
    ("filetype", "content_type"),
    [("text/markdown", "text/markdown"), ("text/html", "text/html")],
)
def test_markup_headings_build_hierarchy_from_category_depth(monkeypatch, filetype, content_type):
    elements = _parse(
        monkeypatch,
        [
            _element("Title", "合成の文書", depth=0, filetype=filetype),
            _element("Title", "第1章 合成の概要", depth=1, filetype=filetype),
            _element("Title", "項目 1", depth=2, filetype=filetype),
            _element("NarrativeText", BODY, filetype=filetype),
            _element("Title", "項目 2", depth=2, filetype=filetype),
            _element("Title", "第2章 合成の構成", depth=1, filetype=filetype),
            _element("Title", "項目 1", depth=2, filetype=filetype),
        ],
        content_type,
    )
    assert [element.section_path for element in elements] == [
        ["合成の文書"],
        ["合成の文書", "第1章 合成の概要"],
        ["合成の文書", "第1章 合成の概要", "項目 1"],
        ["合成の文書", "第1章 合成の概要", "項目 1"],
        ["合成の文書", "第1章 合成の概要", "項目 2"],
        ["合成の文書", "第2章 合成の構成"],
        ["合成の文書", "第2章 合成の構成", "項目 1"],
    ]


def test_markup_heading_tag_ending_with_a_sentence_mark_stays_a_heading(monkeypatch):
    elements = _parse(
        monkeypatch,
        [_element("Title", "合成の手順を確認する。", depth=0, filetype="text/html")],
        "text/html",
    )
    assert elements[0].kind == "title"
    assert elements[0].section_path == ["合成の手順を確認する。"]


def test_html_paragraph_inferred_as_title_without_tag_depth_is_body(monkeypatch):
    elements = _parse(
        monkeypatch,
        [
            _element("Title", "第1章 合成の概要", depth=0, filetype="text/html"),
            _element("Title", BODY, filetype="text/html"),
        ],
        "text/html",
    )
    assert elements[1].kind == "text"
    assert elements[1].section_path == ["第1章 合成の概要"]


def test_office_depth_builds_hierarchy_and_sentence_title_is_body(monkeypatch):
    filetype = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    elements = _parse(
        monkeypatch,
        [
            _element("Title", "合成のスライド", depth=0, filetype=filetype),
            _element("Title", "合成の要点", depth=1, filetype=filetype),
            _element("Title", "合成の値を順番に確認する。", depth=1, filetype=filetype),
        ],
        filetype,
    )
    assert elements[1].section_path == ["合成のスライド", "合成の要点"]
    assert elements[2].kind == "text"
    assert elements[2].section_path == ["合成のスライド", "合成の要点"]


def test_narrative_numbered_line_is_not_promoted_outside_plain_text(monkeypatch):
    filetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elements = _parse(
        monkeypatch,
        [
            _element("Title", "合成の見出し", depth=0, filetype=filetype),
            _element("NarrativeText", "第2章 合成の参照", depth=0, filetype=filetype),
        ],
        filetype,
    )
    assert elements[1].section_path == ["合成の見出し"]


def test_other_adapters_keep_their_title_as_is():
    """Unstructured 以外の adapter の変換は文字の形で見出しを直さない。"""
    extraction = _structured_from_adapter_elements(
        [_element("Title", BODY)],
        document_type="text",
        source_parser="other_adapter",
        template="text_blocks",
        parser_backend="other",
        parser_version="1",
    )
    assert extraction.elements[0].kind == "title"
    assert extraction.elements[0].section_path == [BODY]
