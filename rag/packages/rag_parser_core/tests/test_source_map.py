"""解析の座標から原本の位置を作る処理の回帰（#4100）。本文の検索で位置を決めない。"""

import hashlib
import json
from xml.parsers import expat

import pytest

from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.registry import _with_structured_records_source_map
from rag_parser_core.source_map import (
    SCHEME_JSONL_LINE,
    SOURCE_UNIT_CODEPOINTS,
    LineIndex,
    SourceRange,
    annotate_html_positions,
    decode_source_text,
    delimited_element_ranges,
    delimited_record_ranges,
    html_tag_positions,
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


def test_html_positions_come_from_parser_and_annotation_keeps_source_text() -> None:
    text = (
        "<!doctype html>\n<!-- <p>注釈</p> --><body>\n"
        '<script>"<p>x</p>"</script><p class="c">本文<br/>二行</p>\n<IMG src=a><div>囲み</body>'
    )
    tags = html_tag_positions(text)
    assert [t.tag for t in tags] == ["body", "script", "p", "br", "img", "div"]
    for tag in tags:
        assert text[tag.start] == "<"
        assert text[tag.start + 1 : tag.start + 1 + len(tag.tag)].lower() == tag.tag
    paragraph = tags[2]
    assert text[paragraph.start : paragraph.end].endswith("二行")
    annotated = annotate_html_positions(text, tags)
    assert '<p data-page-number="3" class="c">' in annotated
    assert annotated.replace(' data-page-number="3"', "").count("本文") == 1
    assert [t.tag for t in html_tag_positions(annotated)] == [t.tag for t in tags]


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


def test_short_sources_and_unsupported_formats_record_nothing() -> None:
    def never(_: bytes) -> list[object]:
        raise AssertionError

    arguments = {
        "items": [],
        "ids": [],
        "element_texts": {},
        "repartition_html": never,
    }
    assert (
        unstructured_source_map(b"a" * 8192, extension=".csv", content_type="", **arguments) is None
    )
    assert (
        unstructured_source_map(b"a" * 9000, extension=".pdf", content_type="", **arguments) is None
    )


def test_recording_failure_does_not_stop_parsing() -> None:
    def fails(_: bytes) -> list[object]:
        raise RuntimeError("synthetic")

    html = ("<p>本文</p>" * 2000).encode()
    assert (
        unstructured_source_map(
            html,
            extension=".html",
            content_type="text/html",
            items=[],
            ids=[],
            element_texts={},
            repartition_html=fails,
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
