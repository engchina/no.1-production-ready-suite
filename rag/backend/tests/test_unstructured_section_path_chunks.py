"""Unstructured の解析結果の Chunk の section_path（#4741）。"""

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from rag_parser_core.registry import _unstructured_adapter_result

from app.rag.chunking_small_to_big import build_parent_child_chunks

BODY = (
    "この段落では合成の処理の流れについて説明する。"
    "途中で止まった場合は、最後に完了した境界から再開する。"
)


def _element(category: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        category=category,
        text=text,
        metadata=SimpleNamespace(category_depth=None, filetype="text/plain"),
    )


def test_chunks_of_plain_text_keep_chapters_apart_and_paths_are_headings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elements = [
        _element("Title", "第1章 合成の概要"),
        *[_element("Title", BODY)] * 3,
        _element("NarrativeText", "第2章 合成の構成"),
        *[_element("NarrativeText", BODY)] * 3,
    ]
    module: Any = ModuleType("unstructured.partition.auto")
    module.partition = lambda **_: elements
    monkeypatch.setitem(sys.modules, "unstructured.partition.auto", module)
    extraction = _unstructured_adapter_result(
        b"source", source_profile=None, content_type="text/plain"
    ).extraction
    assert extraction is not None

    chunks = build_parent_child_chunks(extraction)

    assert [chunk.metadata["section_path"] for chunk in chunks] == [
        "第1章 合成の概要",
        "第2章 合成の構成",
    ]
    assert chunks[1].text.startswith("第2章 合成の構成")


def test_plain_text_title_inside_a_block_is_body_not_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """段の途中の行の Title（手順のコマンドの行など）は見出しにしない（#5425）。"""
    source = (
        "手順A　合成の確認\n\n"
        " 合成の説明です。\n"
        " export SYN_VAR=1\n"
        " export SYN_PATH=/opt/synthetic\n"
        " /opt/synthetic/tool --check\n"
    )
    elements = [
        _element("Title", "手順A　合成の確認"),
        _element("Title", "合成の説明です。"),
        _element("NarrativeText", "export SYN_VAR=1"),
        _element("Title", "export SYN_PATH=/opt/synthetic"),
        _element("NarrativeText", "/opt/synthetic/tool --check"),
    ]
    module: Any = ModuleType("unstructured.partition.auto")
    module.partition = lambda **_: elements
    monkeypatch.setitem(sys.modules, "unstructured.partition.auto", module)
    extraction = _unstructured_adapter_result(
        source.encode(), source_profile=None, content_type="text/plain"
    ).extraction
    assert extraction is not None

    kinds = {element.text.strip(): element.kind for element in extraction.elements}
    assert kinds["手順A　合成の確認"] == "title"
    assert kinds["export SYN_PATH=/opt/synthetic"] == "text"
    last = next(e for e in extraction.elements if "--check" in e.text)
    assert last.section_path == ["手順A 合成の確認"]
