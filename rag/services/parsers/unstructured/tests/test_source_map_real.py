"""実際の Unstructured で原本の位置を記録する回帰（#4100）。版を上げて前提が変わったら失敗する。"""

import json

import pytest
from rag_parser_core.registry import _unstructured_adapter_result
from rag_parser_core.source import SourceModality, SourceProfile


def _parse(data: bytes, content_type: str, extension: str, modality: str = "text"):
    profile = SourceProfile.model_construct(
        extension=extension,
        content_type=content_type,
        modality=SourceModality(modality),
        sanitized_file_name=f"synthetic{extension}",
        unsupported_reason=None,
    )
    result = _unstructured_adapter_result(data, source_profile=profile, content_type=content_type)
    assert result.extraction is not None
    return result.extraction


def _assert_every_element_recorded(extraction, data: bytes, scheme: str) -> dict:
    source_map = extraction.parser_artifacts["source_map"]
    assert source_map["scheme"] == scheme
    assert source_map["source_length"] == len(data.decode())
    ids = {element.element_id for element in extraction.elements}
    assert set(source_map["elements"]) == ids
    return source_map


@pytest.mark.parametrize(
    ("source", "expected_line"),
    [
        (
            "\n\n".join(f"段落{i}の一行目です\n段落{i}の二行目です" for i in range(500)),
            "段落0の二行目です",
        ),
        ("\n".join(f"一行 {i} の本文です" for i in range(2000)), "一行 1 の本文です"),
    ],
)
def test_text_records_character_and_line_positions(source: str, expected_line: str) -> None:
    data = source.encode()
    extraction = _parse(data, "text/plain", ".txt")
    source_map = _assert_every_element_recorded(extraction, data, "text_char")
    second = source_map["elements"][extraction.elements[1].element_id][0]
    assert source[second["source_start"] :].startswith(expected_line)
    assert second["locator"].startswith("char=")


def test_broken_paragraph_lines_join_to_one_element_at_paragraph_start() -> None:
    source = "\n\n".join(
        f"段落 {i} の 1 行目 です よ と\n段落 {i} の 2 行目 です よ" for i in range(500)
    )
    extraction = _parse(source.encode(), "text/plain", ".txt")
    assert len(extraction.elements) == 500
    source_map = _assert_every_element_recorded(extraction, source.encode(), "text_char")
    last = source_map["elements"][extraction.elements[-1].element_id][0]
    assert source[last["source_start"] :].startswith("段落 499 の 1 行目")


def test_markdown_records_block_lines_without_blank_separated_lists() -> None:
    body = "".join(
        f"## 節 {i}\n\n段落 {i} **強調** [参照][r]。\n- 空行なしの項目 {i}\n\n続きの段落 {i}\n\n"
        f"- 項目 {i}\n- 項目 {i}b\n\n| 列1 | 列2 |\n|---|---|\n| a{i} | b{i} |\n\n"
        for i in range(120)
    )
    source = f"# 見出し\n\n{body}[r]: http://example.invalid/a\n"
    extraction = _parse(source.encode(), "text/markdown", ".md")
    source_map = _assert_every_element_recorded(extraction, source.encode(), "markdown_source")
    lines = source.split("\n")

    def first_line(prefix: str) -> str:
        element = next(e for e in extraction.elements if e.text.startswith(prefix))
        locator = source_map["elements"][element.element_id][0]["locator"]
        return lines[int(locator.removeprefix("line=").split(",")[0])]

    # リストの項目は項目の行、空行の無いブロックは段落の行を指す（python-markdown と同じ単位）。
    assert first_line("項目 3b") == "- 項目 3b"
    assert first_line("段落 3 ") == "段落 3 **強調** [参照][r]。"
    assert first_line("続きの段落 3") == "続きの段落 3"


def test_html_segments_match_whole_parse_and_point_to_source() -> None:
    source = (
        "<!doctype html>\n<html><head><title>題</title></head><body><main><h1>見出し</h1>\n"
        + "".join(
            f"<p data-page-number='9'>段落 <b>{i}</b> です</p>\n<div>囲み<p>内側 {i}</p></div>\n"
            for i in range(400)
        )
        + "<table><tr><th>列</th></tr><tr><td>値</td></tr></table>\n"
        + "<ul><li>項目 a</li><li>項目 b</li></ul></main>\n<footer>版</footer></body></html>"
    )
    data = source.encode()
    extraction = _parse(data, "text/html", ".html", "html")
    source_map = _assert_every_element_recorded(extraction, data, "html_source")
    # main の置換の後の位置ではなく、原本の位置を指す。範囲の中にその要素の文字がある。
    for element in extraction.elements[:40]:
        span = source_map["elements"][element.element_id][0]
        assert source[span["source_start"]] == "<"
        assert element.text.split(" ")[0] in source[span["source_start"] : span["source_end"]]
    # 後ろの要素ほど後ろの位置になり、表示範囲に分かれる。
    starts = [source_map["elements"][e.element_id][0]["source_start"] for e in extraction.elements]
    assert starts == sorted(starts)
    assert len(set(starts)) > 10
    assert max(starts) > 8192


def test_html_position_does_not_add_page_numbers() -> None:
    source = "<html><body>" + "<p>段落の本文です</p>\n" * 800 + "</body></html>"
    extraction = _parse(source.encode(), "text/html", ".html", "html")
    assert "source_map" in extraction.parser_artifacts
    assert all(element.page_number is None for element in extraction.elements)


@pytest.mark.parametrize(
    ("delimiter", "content_type", "extension"),
    [(",", "text/csv", ".csv"), ("\t", "text/tab-separated-values", ".tsv")],
)
def test_delimited_rows_become_units_with_converted_values(
    delimiter: str, content_type: str, extension: str
) -> None:
    # セルの中の改行を含む記録も 1 行として、行の番号で対応する。
    rows = [f"列1{delimiter}列2"] + [f'{i:03d}{delimiter}"値{i}\nつづき"' for i in range(900)]
    source = "\n".join(rows) + "\n"
    data = source.encode()
    extraction = _parse(data, content_type, extension)
    assert len(extraction.elements) == 1
    source_map = _assert_every_element_recorded(extraction, data, "delimited_row")
    units = source_map["elements"][extraction.elements[0].element_id]
    assert len(units) > 1
    assert units[-1]["row_end"] == 901
    text = extraction.elements[0].text
    assert text[units[1]["start"] :].startswith(f"| {units[1]['row_start'] - 2:03d} |")


def test_json_text_keeps_source_characters_and_key_order() -> None:
    value = {"z": "先頭の値", "a": {"c": "日本語", "b": [1, 2.5, None, True]}}
    extraction = _parse(json.dumps(value, ensure_ascii=True).encode(), "application/json", ".json")
    text = extraction.elements[0].text
    assert "先頭の値" in text and "日本語" in text and "\\u" not in text
    assert text.index('"z"') < text.index('"a"') and text.index('"c"') < text.index('"b"')
    assert json.loads(text) == value


def test_jsonl_text_keeps_source_characters_for_each_line() -> None:
    lines = [{"k": 2, "a": "値 1"}, {"k": 3, "a": "値 2"}]
    data = "\n".join(json.dumps(line, ensure_ascii=True) for line in lines).encode()
    extraction = _parse(data, "application/x-ndjson", ".jsonl")
    texts = [element.text for element in extraction.elements]
    assert [json.loads(text) for text in texts] == lines
    assert "値 1" in texts[0] and "値 2" in texts[1]
    assert all(text.index('"k"') < text.index('"a"') for text in texts)


def test_element_shaped_jsonl_text_is_left_as_parsed() -> None:
    lines = [json.dumps({"type": "Title", "text": f"見出し {i}"}) for i in range(3)]
    extraction = _parse("\n".join(lines).encode(), "application/x-ndjson", ".jsonl")
    assert [element.text for element in extraction.elements] == ["見出し 0", "見出し 1", "見出し 2"]


@pytest.mark.parametrize(
    "values",
    [
        [f"値{i}" for i in range(3000)],
        ["abc", "abd", "abe"],
        [f"合成 値 {i}" if i % 7 else f"合成{i}" for i in range(400)],
    ],
)
def test_single_column_csv_keeps_one_line_per_record(values: list[str]) -> None:
    source = "\n".join(values) + "\n"
    extraction = _parse(source.encode(), "text/csv", ".csv")
    assert len(extraction.elements) == 1
    assert extraction.elements[0].kind == "table"
    lines = extraction.elements[0].text.split("\n")
    assert len(lines) == len(values)
    assert values[-1] in lines[-1]


def test_long_single_column_csv_records_row_units() -> None:
    source = "\n".join(f"値{i}" for i in range(3000))
    data = source.encode()
    extraction = _parse(data, "text/csv", ".csv")
    source_map = _assert_every_element_recorded(extraction, data, "delimited_row")
    units = source_map["elements"][extraction.elements[0].element_id]
    assert units[-1]["row_end"] == 3000


def test_multi_column_csv_still_uses_unstructured_table() -> None:
    extraction = _parse("列1,列2\na,b\nc,d\n".encode(), "text/csv", ".csv")
    assert {cell.col for cell in extraction.tables[0].cells} == {0, 1}


def test_jsonl_records_line_numbers() -> None:
    lines = [json.dumps({"k": i, "v": "値" * 5}, ensure_ascii=False) for i in range(500)]
    data = ("\n".join(lines) + "\n").encode()
    extraction = _parse(data, "application/x-ndjson", ".jsonl")
    source_map = _assert_every_element_recorded(extraction, data, "jsonl_line")
    assert source_map["elements"][extraction.elements[3].element_id][0]["locator"] == "line=4"


def test_xml_records_tag_positions_in_close_order() -> None:
    source = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<root>\n'
        + "".join(
            f'<item id="{i}">\n  前の文字 {i}<name>名前 {i}</name>\n'
            f"  <note>注記 {i} &amp; 本文</note>\n</item>\n"
            for i in range(300)
        )
        + "</root>\n"
    )
    data = source.encode()
    for content_type in ("application/xml", "text/xml"):
        extraction = _parse(data, content_type, ".xml")
        source_map = _assert_every_element_recorded(extraction, data, "xml_source")
        by_text = {element.text: element for element in extraction.elements}
        for text, opening in (
            ("名前 7", "<name>名前 7"),
            ("注記 7 & 本文", "<note>注記 7"),
            ("前の文字 7", '<item id="7">'),
        ):
            span = source_map["elements"][by_text[text].element_id][0]
            assert source[span["source_start"] :].startswith(opening)
            assert span["locator"].startswith("line=")


def test_broken_xml_keeps_parser_behaviour() -> None:
    from rag_parser_core.unstructured_source_map import unstructured_source_map

    class Item:
        text = "本文"

    assert (
        unstructured_source_map(
            ("<a>" + "本文" * 5000).encode(),
            extension=".xml",
            content_type="application/xml",
            items=[Item()],
            ids=["e"],
            element_texts={},
        )
        is None
    )


def test_element_shaped_jsonl_is_not_recorded_when_output_differs() -> None:
    # 要素の形の行は Unstructured が要素に戻す（本文が整形した JSON にならない）ので記録しない。
    lines = [json.dumps({"type": "Title", "text": f"見出し {i}" + "値" * 20}) for i in range(400)]
    data = ("\n".join(lines) + "\n").encode()
    extraction = _parse(data, "application/x-ndjson", ".jsonl")
    assert extraction.elements
    assert "source_map" not in extraction.parser_artifacts


def test_blank_lines_in_jsonl_keep_physical_line_numbers() -> None:
    lines: list[str] = []
    for i in range(500):
        lines.extend([json.dumps({"k": i, "v": "値" * 20}, ensure_ascii=False), ""])
    data = "\n".join(lines).encode()
    extraction = _parse(data, "application/x-ndjson", ".jsonl")
    source_map = _assert_every_element_recorded(extraction, data, "jsonl_line")
    assert source_map["elements"][extraction.elements[2].element_id][0]["locator"] == "line=5"


def test_json_records_pointer_units_with_sorted_output() -> None:
    value = {"z": "先頭", "a": [{"k": i, "v": "値" * 5} for i in range(600)], "m": {"x": 1}}
    data = json.dumps(value, ensure_ascii=False, indent=1).encode()
    extraction = _parse(data, "application/json", ".json")
    assert len(extraction.elements) == 1
    source_map = _assert_every_element_recorded(extraction, data, "json_pointer")
    units = source_map["elements"][extraction.elements[0].element_id]
    assert len(units) > 1
    # 本文は原本の文字とキーの順（#4101）。
    assert units[0]["locator"].startswith("/z")
    assert units[0]["start"] == 0
    assert "先頭" in extraction.elements[0].text
    assert "\\u" not in extraction.elements[0].text
    assert units[-1]["end"] == len(extraction.elements[0].text)


def test_short_source_records_nothing() -> None:
    extraction = _parse("短い本文\n\n二つ目".encode(), "text/plain", ".txt")
    assert "source_map" not in extraction.parser_artifacts


def test_unverified_order_records_nothing_and_keeps_parsing() -> None:
    from rag_parser_core.unstructured_source_map import unstructured_source_map

    source = "\n\n".join(f"段落{i}の本文です" for i in range(1500))
    data = source.encode()
    extraction = _parse(data, "text/plain", ".txt")
    assert "source_map" in extraction.parser_artifacts

    class Item:
        def __init__(self, text: str) -> None:
            self.text = text

    texts = [element.text for element in extraction.elements]
    texts[1], texts[2] = texts[2], texts[1]

    assert (
        unstructured_source_map(
            data,
            extension=".txt",
            content_type="text/plain",
            items=[Item(text) for text in texts],
            ids=[str(index) for index in range(len(texts))],
            element_texts={},
        )
        is None
    )
