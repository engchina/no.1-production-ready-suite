"""解析が記録した原本の位置で、右の表示が左の表示範囲に追従する回帰（#4100）。"""

import hashlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.registry import _structured_records_jsonl_result
from rag_parser_core.source_map import (
    SourceRange,
    delimited_element_ranges,
    json_element_ranges,
    jsonl_element_ranges,
    source_map_artifact,
    xml_leaves,
)
from rag_parser_core.structured_records import (
    DocumentLine,
    RecordField,
    RecordLocation,
    RowLine,
    dump_structured_records,
)
from rag_parser_core.xml_tree_json import xml_tree_json_extraction

from app.rag.chunking import chunk_extraction_with_strategy
from app.services.document_inspection import extraction_window
from app.services.inspection_scope import source_inspection


def _windows(payload: dict[str, Any], data: bytes) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        window = source_inspection(payload, data, offset=offset, limit=8192)
        yield window
        if window["next_offset"] is None:
            return
        offset = window["next_offset"]


def _element_payload(data: bytes, scheme: str, starts: list[tuple[int, int]]) -> dict[str, Any]:
    text = data.decode()
    ranges = {f"e{i}": [SourceRange(a, b, f"char={a},{b}")] for i, (a, b) in enumerate(starts)}
    return {
        "elements": [{"element_id": f"e{i}", "text": text[a:b]} for i, (a, b) in enumerate(starts)],
        "parser_artifacts": {"source_map": source_map_artifact(data, text, scheme, ranges)},
    }


@pytest.mark.parametrize(
    "scheme", ["text_char", "markdown_source", "html_source", "xml_source", "jsonl_line"]
)
def test_elements_follow_windows_and_belong_to_the_start_window(scheme: str) -> None:
    lines = [f"合成の行 {i:05d} の本文です" for i in range(1500)]
    text = "\n".join(lines)
    data = text.encode()
    starts = []
    position = 0
    for line in lines:
        starts.append((position, position + len(line)))
        position += len(line) + 1
    payload = _element_payload(data, scheme, starts)
    windows = list(_windows(payload, data))
    assert len(windows) > 1
    assert "".join(window["text"] for window in windows) == text
    assert all(window["scope_status"] == "exact" for window in windows)
    # すべての要素が 1 つの範囲にだけ所属し、順番どおりに並ぶ。
    assert [eid for window in windows for eid in window["element_ids"]] == [
        f"e{i}" for i in range(1500)
    ]
    for window in windows:
        view = extraction_window(
            payload,
            mode="text",
            offset=0,
            limit=25,
            scope_element_ids=set(window["element_ids"]),
            scope_fragments=window["element_fragments"],
        )
        for line in view["payload"]["raw_text"].split("\n\n"):
            assert line in window["text"]


def test_crossing_element_belongs_only_to_the_start_window() -> None:
    text = "短い" * 10 + "長" * 20000 + "末尾"
    data = text.encode()
    payload = _element_payload(data, "text_char", [(0, 20), (20, 20020), (20020, 20022)])
    windows = list(_windows(payload, data))
    assert [eid for window in windows for eid in window["element_ids"]] == ["e0", "e1", "e2"]
    assert any(not window["element_ids"] for window in windows)


def test_version_mismatch_returns_no_sync_result() -> None:
    text = "本文の行\n" * 3000
    data = text.encode()
    payload = _element_payload(data, "text_char", [(0, 4)])
    changed = (text + "追記").encode()
    window = source_inspection(payload, changed, offset=0, limit=8192)
    assert window["scope_status"] == "version_mismatch"
    assert window["element_ids"] == []
    expected = source_inspection(payload, data, offset=0, limit=8192, expected_revision="old")
    assert expected["scope_status"] == "version_mismatch"
    assert expected["element_ids"] == []


def test_inconsistent_map_is_unresolved() -> None:
    text = "本文の行\n" * 3000
    data = text.encode()
    payload = _element_payload(data, "text_char", [(0, 4)])
    payload["parser_artifacts"]["source_map"]["source_length"] = 1
    window = source_inspection(payload, data, offset=0, limit=8192)
    assert (window["scope_status"], window["element_ids"]) == ("unresolved", [])


def test_missing_map_on_long_source_stays_unrecorded() -> None:
    data = ("本文の行\n" * 3000).encode()
    payload = {"elements": [{"element_id": "e", "text": "本文"}], "parser_artifacts": {}}
    window = source_inspection(payload, data, offset=0, limit=8192)
    assert (window["scope_status"], window["element_ids"]) == ("unrecorded", [])


@pytest.mark.parametrize("delimiter", [",", "\t"])
def test_table_rows_follow_windows_with_header(delimiter: str) -> None:
    rows = [f"列1{delimiter}列2"] + [f"{i:04d}{delimiter}値{i}" for i in range(2500)]
    text = "\n".join(rows) + "\n"
    data = text.encode()
    element_text = "\n".join(f"| {row.replace(delimiter, ' | ')} |" for row in rows)
    units = delimited_element_ranges(text, element_text, delimiter=delimiter)
    payload = {
        "elements": [{"element_id": "t", "kind": "table", "text": element_text}],
        "tables": [
            {
                "table_id": "t",
                "element_id": "t",
                "cells": [
                    {"row": r, "col": c, "text": value}
                    for r, row in enumerate(rows)
                    for c, value in enumerate(row.split(delimiter))
                ],
            }
        ],
        "parser_artifacts": {
            "source_map": source_map_artifact(data, text, "delimited_row", {"t": units})
        },
    }
    windows = list(_windows(payload, data))
    assert len(windows) > 1
    assert all(window["element_ids"] == ["t"] for window in windows)
    seen_rows: list[str] = []
    for window in windows:
        view = extraction_window(
            payload,
            mode="structure",
            offset=0,
            limit=25,
            scope_element_ids=set(window["element_ids"]),
            scope_fragments=window["element_fragments"],
        )
        element = view["payload"]["elements"][0]
        lines = element["text"].split("\n")
        # どの範囲でも表頭が付き、範囲の先頭の行は左の原本の先頭と同じ記録。
        assert lines[0] == "| 列1 | 列2 |"
        data_lines = lines[1:]
        seen_rows.extend(data_lines)
        first_record = window["text"].split("\n", 2)[0 if window["offset"] else 1]
        assert data_lines[0] == f"| {first_record.replace(delimiter, ' | ')} |"
        # 表のセルも表頭とその範囲の行だけ。
        cell_rows = {cell["row"] for cell in view["payload"]["tables"][0]["cells"]}
        assert 0 in cell_rows and len(cell_rows) == len(data_lines) + 1
    assert seen_rows == element_text.split("\n")[1:]
    first, later = windows[0]["chunk_rows"], windows[1]["chunk_rows"]
    assert first["at_start"] is True and later["at_start"] is False
    assert first["row_start"] == 0 and later["row_start"] == first["row_end"] + 1


def test_json_units_follow_windows_and_chunks_stay_in_start_window() -> None:
    value = {"a": [{"k": i, "v": "値" * 10} for i in range(900)], "z": "末尾"}
    text = json.dumps(value, ensure_ascii=False, indent=1)
    data = text.encode()
    element_text = "\n".join(
        line.strip() for line in json.dumps(value, indent=2, sort_keys=True).splitlines()
    )
    units = json_element_ranges(text, element_text)
    payload = {
        "elements": [{"element_id": "j", "text": element_text}],
        "parser_artifacts": {
            "source_map": source_map_artifact(data, text, "json_pointer", {"j": units})
        },
    }
    windows = list(_windows(payload, data))
    assert len(windows) > 1
    shown: list[str] = []
    for window in windows:
        view = extraction_window(
            payload,
            mode="text",
            offset=0,
            limit=25,
            scope_element_ids=set(window["element_ids"]),
            scope_fragments=window["element_fragments"],
        )
        shown.append(view["payload"]["raw_text"])
    # 全部の行がどこかの範囲に 1 回だけ出る。
    assert sorted("\n".join(shown).split("\n")) == sorted(element_text.split("\n"))
    # JSON の Chunk は、分割が記録した本文の位置で、その単位の範囲にだけ所属する。
    chunks = chunk_extraction_with_strategy(
        StructuredExtraction(
            raw_text=element_text,
            elements=[DocumentElement(kind="text", text=element_text, order=0, element_id="j")],
        )
    )
    assert len(chunks) > len(windows)
    owners: list[int] = []
    for chunk in chunks:
        owner = [
            index
            for index, window in enumerate(windows)
            if any(a <= chunk.start_offset <= b for a, b in window["chunk_rows"]["offset_ranges"])
        ]
        assert len(owner) == 1, chunk.start_offset
        owners.append(owner[0])
    assert len(set(owners)) > 1
    assert windows[0]["chunk_rows"]["at_start"] is True
    assert windows[1]["chunk_rows"]["at_start"] is False


def test_xml_tags_follow_windows_in_source_order() -> None:
    """要素が閉じる順は原本の順と違うことがある。開始位置で所属させる。"""
    text = (
        "<root>\n"
        + "".join(
            f'<item id="{i}">前の文字 {i}<name>名前 {i}</name><note>注記 {i}</note></item>\n'
            for i in range(600)
        )
        + "</root>"
    )
    data = text.encode()
    leaves = xml_leaves(text)
    ranges = {f"x{i}": [leaf.source_range] for i, leaf in enumerate(leaves)}
    payload = {
        "elements": [{"element_id": f"x{i}", "text": leaf.text} for i, leaf in enumerate(leaves)],
        "parser_artifacts": {"source_map": source_map_artifact(data, text, "xml_source", ranges)},
    }
    windows = list(_windows(payload, data))
    assert len(windows) > 1
    assert "".join(window["text"] for window in windows) == text
    assigned = [eid for window in windows for eid in window["element_ids"]]
    assert sorted(assigned) == sorted(ranges)
    for window in windows:
        view = extraction_window(
            payload,
            mode="structure",
            offset=0,
            limit=25,
            scope_element_ids=set(window["element_ids"]),
        )
        for element in view["payload"]["elements"]:
            start = ranges[element["element_id"]][0].source_start
            assert window["offset"] <= start < window["end"]


def test_xml_tree_json_with_recorded_map_follows_windows_and_rejects_other_version() -> None:
    def node(tag: str, *children: Any, **attrs: str) -> dict[str, Any]:
        return {"tag": tag, "attr": attrs, "children": list(children)}

    articles = [
        node(
            "Article",
            node("ArticleTitle", str(i)),
            node(
                "Paragraph",
                node("ParagraphSentence", node("Sentence", f"{i}合成の本文です。" * 40)),
            ),
            Num=str(i),
        )
        for i in range(1, 41)
    ]
    data = json.dumps(
        {
            "law_info": {},
            "revision_info": {"law_title": "合成文書"},
            "law_full_text": node("Law", node("LawBody", node("MainProvision", *articles))),
        },
        ensure_ascii=False,
    ).encode()
    extraction = xml_tree_json_extraction(data)
    assert extraction is not None
    payload = extraction.to_document_payload()
    assert "json_source_map" in extraction.parser_artifacts
    assert "source_map" not in extraction.parser_artifacts
    windows = list(_windows(payload, data))
    assert len(windows) > 1
    assert "".join(window["text"] for window in windows) == data.decode()
    assert all(window["scope_status"] == "exact" for window in windows)
    assigned = [eid for window in windows for eid in window["element_ids"]]
    assert assigned == [element.element_id for element in extraction.elements]
    changed = source_inspection(payload, data + b" ", offset=0, limit=8192)
    assert (changed["scope_status"], changed["element_ids"]) == ("version_mismatch", [])


def test_structured_records_jsonl_follows_windows_by_record_id() -> None:
    lines: list[DocumentLine | RowLine] = [
        DocumentLine(
            record_id="document",
            source_type="csv",
            converter="csv_to_jsonl",
            converter_version="v2",
            header_row=1,
        )
    ]
    for index in range(1, 301):
        lines.append(
            RowLine(
                record_id=f"row-{index:06d}",
                source_type="csv",
                location=RecordLocation(row_start=index + 1, row_end=index + 1),
                fields=[RecordField(name="項目", position=1, value=f"値 {index}" + "本文" * 10)],
            )
        )
    data = dump_structured_records(lines)
    result = _structured_records_jsonl_result(data)
    assert result.extraction is not None
    payload = result.extraction.to_document_payload()
    windows = list(_windows(payload, data))
    assert len(windows) > 1
    assert "".join(window["text"] for window in windows) == data.decode()
    assigned = [eid for window in windows for eid in window["element_ids"]]
    assert assigned == [f"row-{index:06d}" for index in range(1, 301)]
    for window in windows:
        for record_id in window["element_ids"]:
            assert f'"record_id":"{record_id}"' in window["text"].replace(": ", ":")


def test_jsonl_ranges_integrate_with_inspection() -> None:
    values = [{"k": i, "v": "値" * 20} for i in range(600)]
    text = "\n".join(json.dumps(v, ensure_ascii=False) for v in values)
    data = text.encode()
    texts = [json.dumps(v, indent=2, sort_keys=True) for v in values]
    ranges = jsonl_element_ranges(text, texts)
    payload = {
        "elements": [{"element_id": f"l{i}", "text": t} for i, t in enumerate(texts)],
        "parser_artifacts": {
            "source_map": source_map_artifact(
                data, text, "jsonl_line", {f"l{i}": [r] for i, r in enumerate(ranges)}
            )
        },
    }
    windows = list(_windows(payload, data))
    assert len(windows) > 1
    assert [e for w in windows for e in w["element_ids"]] == [f"l{i}" for i in range(600)]
    assert hashlib.sha256(data).hexdigest() == windows[0]["revision"]
