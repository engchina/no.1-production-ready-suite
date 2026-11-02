"""解析の座標から原本の位置を作る処理の回帰（#4100）。本文の検索で位置を決めない。"""

import ast
import hashlib
import json
from pathlib import Path
from xml.parsers import expat

import pytest

from rag_parser_core import unstructured_source_map as unstructured_source_map_module
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.registry import _with_structured_records_source_map
from rag_parser_core.source_map import (
    SCHEME_JSONL_LINE,
    SOURCE_UNIT_CODEPOINTS,
    LineIndex,
    SourceRange,
    decode_source_text,
    delimited_element_ranges,
    delimited_record_ranges,
    html_segments,
    iter_text_blocks,
    json_element_ranges,
    jsonl_element_ranges,
    nonblank_line_ranges,
    source_map_artifact,
    structured_record_ranges,
    text_locator,
    xml_leaves,
)
from rag_parser_core.unstructured_source_map import source_format, unstructured_source_map


def test_csv_record_ranges_follow_quotes_and_line_ends() -> None:
    text = 'a,"b\nc"\r\n\r\nd,"e""f"\nx"y,z'
    assert [text[a:b] for a, b in delimited_record_ranges(text, delimiter=",")] == [
        'a,"b\nc"',
        'd,"e""f"',
        'x"y,z',
    ]


def test_tsv_quote_only_opens_at_field_start() -> None:
    text = 'a"b\tc\n"d\ne"\tf'
    assert [text[a:b] for a, b in delimited_record_ranges(text, delimiter="\t")] == [
        'a"b\tc',
        '"d\ne"\tf',
    ]


def test_delimited_units_cover_every_record_once_and_map_rows_to_table_lines() -> None:
    rows = [f"{i:03d},値{i}" for i in range(400)]
    text = "\n".join(rows) + "\n"
    element_text = "\n".join(f"| {i} | 値{i} |" for i in range(400))
    units = delimited_element_ranges(text, element_text, delimiter=",")
    assert len(units) > 1
    assert units[0].source_start == 0
    assert units[-1].row_end == 400
    previous_row = 0
    for unit in units:
        assert unit.row_start == previous_row + 1
        previous_row = unit.row_end
        # 値の表記が原本と違っても、行の番号と文字の範囲で対応する。
        lines = element_text[unit.start : unit.end].split("\n")
        assert len(lines) == unit.row_end - unit.row_start + 1
        assert (
            text[unit.source_start : unit.source_end]
            .split("\n")[0]
            .startswith(f"{unit.row_start - 1:03d},")
        )
        assert unit.locator == f"row={unit.row_start}-{unit.row_end}"
        assert unit.source_end - unit.source_start >= SOURCE_UNIT_CODEPOINTS or unit is units[-1]


def test_delimited_rejects_record_count_mismatch() -> None:
    assert delimited_element_ranges("a,b\nc,d\n", "| a | b |", delimiter=",") == []


def test_jsonl_lines_use_same_split_and_verify_each_value() -> None:
    values = [{"b": i, "a": "値"} for i in range(3)]
    text = "\n".join(json.dumps(v, ensure_ascii=False) for v in values) + "\n\n"
    texts = [json.dumps(v, indent=2, sort_keys=True) for v in values]
    ranges = jsonl_element_ranges(text, texts)
    assert [r.locator for r in ranges] == ["line=1", "line=2", "line=3"]
    assert json.loads(text[ranges[1].source_start : ranges[1].source_end]) == values[1]
    assert jsonl_element_ranges(text, texts[:-1]) == []
    assert jsonl_element_ranges(text, [texts[0], texts[2], texts[1]]) == []


def test_json_units_follow_pointer_with_sorted_output_and_source_positions() -> None:
    value = {"z": "先頭", "a": [{"k": i, "v": "値" * 8} for i in range(300)], "m": {"x": 1}}
    text = json.dumps(value, ensure_ascii=False, indent=1)
    element_text = "\n".join(
        line.strip() for line in json.dumps(value, indent=2, sort_keys=True).splitlines()
    )
    units = json_element_ranges(text, element_text)
    assert len(units) > 2
    # 本文の範囲は隙間なく全体を覆う（括弧の行は最初と最後の単位に入る）。
    assert units[0].start == 0
    assert units[-1].end == len(element_text)
    for current, following in zip(units, units[1:], strict=False):
        assert following.start == current.end + 1
    # 原本の範囲は、その JSON Pointer の値を指す。
    for unit in units:
        pointer = unit.locator.split("..")[0]
        node = value
        for part in pointer.lstrip("/").split("/") if pointer else []:
            node = node[int(part)] if isinstance(node, list) else node[part]
        raw = text[unit.source_start : unit.source_end]
        if ".." not in unit.locator:
            assert json.loads(raw) == node


def test_json_duplicate_keys_have_no_unique_position() -> None:
    text = '{"a": 1, "a": 2, "b": "' + "x" * 3000 + '"}'
    element_text = json.dumps(json.loads(text), indent=2, sort_keys=True)
    with pytest.raises(ValueError):
        json_element_ranges(text, element_text)


def test_json_trailing_content_is_rejected() -> None:
    with pytest.raises(ValueError):
        json_element_ranges('{"a": 1} trailing', '{\n"a": 1\n}')


def test_json_rejects_text_that_differs_from_formatted_source() -> None:
    text = json.dumps({"a": 1})
    assert json_element_ranges(text, '{\n"a": 2\n}') == []


def test_html_segments_split_containers_and_keep_blocks_whole() -> None:
    rows = "".join(f"<tr><td>{i}</td></tr>" for i in range(200))
    paragraphs = "".join(f"<p>本文 {i} の段落です</p>\n" for i in range(200))
    text = (
        "<!doctype html>\n<!-- <p>注釈</p> --><html><head><title>題</title></head>\n"
        f'<body><div id="a">{paragraphs}</div>\n<table>{rows}</table>\n'
        f"<div>囲み<p>内側</p>{paragraphs}</div>\n<img src=a><script>'<p>x</p>'</script>"
        "</body></html>"
    )
    segments = html_segments(text)
    assert len(segments) > 3
    slices = [text[node.start : node.end] for node in segments]
    # 順番どおりで重ならない。
    assert all(a.end <= b.start for a, b in zip(segments, segments[1:], strict=False))
    # 文字のある入れ物と表は分けない。段落は分けて目安の文字数にまとめる。
    tables = [value for value in slices if value.startswith("<table>")]
    assert len(tables) == 1 and tables[0].endswith("</table>")
    assert sum(1 for value in slices if value.startswith("<div>囲み")) == 1
    assert sum(1 for value in slices if value.startswith("<p>")) > 1
    assert all(text[node.start] == "<" for node in segments)
    assert all(value.count("<p>") == value.count("</p>") for value in slices)


def test_html_text_outside_tags_keeps_one_segment() -> None:
    text = "地の文<p>本文</p>" * 1000
    segments = html_segments(text)
    assert [(node.start, node.end) for node in segments] == [(0, len(text))]


def test_xml_leaves_follow_close_order_and_text_before_first_child() -> None:
    text = (
        '<?xml version="1.0" encoding="Shift_JIS"?>\n<!-- 注釈 -->\n<root>\n'
        '  <item id="1">前の文字<sub>内側 &amp; 値</sub>後ろの文字</item>\n'
        "  <note>コメントの前<!-- x -->コメントの後</note>\n"
        "  <空></空>\n"
        "  <![CDATA[無視]]>\n"
        "</root>"
    )
    leaves = xml_leaves(text)
    assert [leaf.text for leaf in leaves] == ["内側 & 値", "前の文字", "コメントの前"]
    inner, outer, note = (leaf.source_range for leaf in leaves)
    assert text[inner.source_start :].startswith("<sub>")
    assert text[inner.source_start : inner.source_end].endswith("値")
    assert text[outer.source_start :].startswith('<item id="1">')
    assert text[note.source_start :].startswith("<note>")
    assert outer.locator == "line=4;col=2;tag=item"


def test_xml_rejects_broken_source() -> None:
    with pytest.raises(expat.ExpatError):
        xml_leaves("<a><b></a>")


def test_text_blocks_and_locator_use_character_and_line_coordinates() -> None:
    text = "一行目\n  二行目  \n\n\n三行目\n"
    index = LineIndex(text)
    import re

    blocks = list(iter_text_blocks(text, re.compile(r"\n\s*\n")))
    assert [text[a:b].strip() for a, b in blocks] == ["一行目\n  二行目", "三行目"]
    lines = nonblank_line_ranges(text, *blocks[0])
    assert [text[a:b] for a, b in lines] == ["一行目", "二行目"]
    a, b = lines[1]
    assert text_locator(index, a, b) == f"char={a},{b};line=1,2"


def test_source_format_prefers_extension() -> None:
    assert source_format(".rst", "text/plain") == "text"
    assert source_format(".md", "text/plain") == "markdown"
    assert source_format(".json", "text/plain") == "json"
    assert source_format("", "text/html; charset=utf-8") == "html"
    assert source_format("", "application/x-ndjson") == "jsonl"
    assert source_format(".xml", "text/plain") == "xml"
    assert source_format("", "text/xml") == "xml"
    assert source_format(".pdf", "application/pdf") is None


def test_artifact_records_version_and_source_digest() -> None:
    data = "﻿本文".encode()
    text = decode_source_text(data)
    artifact = source_map_artifact(data, text, "text_char", {"e": [SourceRange(0, 2, "char=0,2")]})
    assert artifact == {
        "version": 2,
        "coordinate_unit": "unicode_codepoint",
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_length": 2,
        "scheme": "text_char",
        "elements": {"e": [{"source_start": 0, "source_end": 2, "locator": "char=0,2"}]},
    }
    assert source_map_artifact(data, text, "text_char", {}) is None


def test_no_private_unstructured_names_are_imported() -> None:
    """Unstructured の private な名前に依存しない（版を上げても位置の記録を保つ）。"""
    source = Path(unstructured_source_map_module.__file__).read_text(encoding="utf-8")
    imported = [
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("unstructured")
        for alias in node.names
    ]
    assert imported
    assert [name for name in imported if name.startswith("_")] == []


def test_json_format_follows_escape_and_key_order_of_the_element_text() -> None:
    value = {"z": "先頭", "a": [{"k": i, "v": "値" * 8} for i in range(300)]}
    text = json.dumps(value, ensure_ascii=False, indent=1)
    for ensure_ascii in (True, False):
        for sort_keys in (True, False):
            element_text = json.dumps(
                value, indent=2, ensure_ascii=ensure_ascii, sort_keys=sort_keys
            )
            units = json_element_ranges(text, element_text)
            assert units[0].start == 0
            assert units[-1].end == len(element_text)
            if not sort_keys:
                assert units[0].locator.startswith("/z")


def test_short_sources_and_unsupported_formats_record_nothing() -> None:
    arguments = {"items": [], "ids": [], "element_texts": {}}
    assert (
        unstructured_source_map(b"a" * 8192, extension=".csv", content_type="", **arguments) is None
    )
    assert (
        unstructured_source_map(b"a" * 9000, extension=".pdf", content_type="", **arguments) is None
    )


def test_recording_failure_does_not_stop_parsing() -> None:
    # 壊れた JSON は記録の処理で例外になるが、解析の結果には影響しない。
    assert (
        unstructured_source_map(
            ("{" + "x" * 9000).encode(),
            extension=".json",
            content_type="application/json",
            items=[object()],
            ids=["e"],
            element_texts={"e": "本文"},
        )
        is None
    )


def test_structured_records_map_lines_by_record_id() -> None:
    lines = [json.dumps({"record_id": f"r{i}", "text": "値" * 20}) for i in range(400)]
    data = ("\n".join(lines) + "\n").encode()
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(kind="text", text=f"本文 {i}", order=i, element_id=f"r{i}")
            for i in range(400)
        ]
    )
    mapped = _with_structured_records_source_map(extraction, data).parser_artifacts["source_map"]
    assert mapped["scheme"] == SCHEME_JSONL_LINE
    assert mapped["elements"]["r2"][0]["locator"] == "line=3"
    assert structured_record_ranges(data.decode())["r2"].locator == "line=3"
