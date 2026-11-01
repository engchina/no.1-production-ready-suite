"""合成 JSON の原ノードと抽出範囲を、文字列の検索に頼らず対応させる。"""

import hashlib
import json
from typing import Any

import pytest

from rag_parser_core.xml_tree_json import xml_tree_json_extraction


def _node(tag: str, *children: Any, **attrs: str) -> dict[str, Any]:
    return {"tag": tag, "attr": attrs, "children": list(children)}


def _document(*sentences: str) -> dict[str, Any]:
    return {
        "law_info": {},
        "revision_info": {"law_title": "合成文書"},
        "law_full_text": _node(
            "Law",
            _node(
                "LawBody",
                _node(
                    "MainProvision",
                    *(
                        _node(
                            "Article",
                            _node("ArticleTitle", str(i)),
                            _node(
                                "Paragraph",
                                _node("ParagraphSentence", _node("Sentence", text)),
                                Num="1",
                            ),
                            Num=str(i),
                        )
                        for i, text in enumerate(sentences, 1)
                    ),
                ),
            ),
        ),
    }


def _resolve(payload: Any, pointer: str) -> Any:
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        payload = payload[int(key)] if isinstance(payload, list) else payload[key]
    return payload


@pytest.mark.parametrize("ascii_only", [False, True])
def test_extraction_records_exact_node_ranges_without_synthetic_heading(ascii_only: bool) -> None:
    payload = _document('同じ本文 "A" 😀', '同じ本文 "A" 😀')
    raw = b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=ascii_only).encode()
    extracted = xml_tree_json_extraction(raw)
    assert extracted is not None
    locator = extracted.parser_artifacts.get("json_source_map")
    assert isinstance(locator, dict), "原本ノードへの対応が保存されていない"
    assert locator["sha256"] == hashlib.sha256(raw).hexdigest()
    assert locator["coordinate_unit"] == "unicode_codepoint"
    assert locator["version"] == 1
    maps = locator["elements"]
    assert isinstance(maps, dict)
    pointers = []
    for element in extracted.elements:
        assert element.element_id
        spans = maps[element.element_id]
        assert isinstance(spans, list) and spans
        for span in spans:
            assert isinstance(span, dict)
            assert span["start"] >= element.text.index("\n") + 1
            leaf = _resolve(payload, span["pointer"])
            assert (
                element.text[span["start"] : span["end"]]
                == leaf[span["source_start"] : span["source_end"]]
            )
            pointers.append(span["pointer"])
    assert len(set(pointers)) == 2, "同一本文でも異なる原本ノードへ対応させる"


def test_normalized_whitespace_is_not_claimed_as_source() -> None:
    payload = _document("  Alpha\t\tBeta\nGamma  ")
    extracted = xml_tree_json_extraction(json.dumps(payload).encode())
    assert extracted is not None
    locator = extracted.parser_artifacts.get("json_source_map")
    assert isinstance(locator, dict)
    element = extracted.elements[0]
    spans = locator["elements"][element.element_id]
    assert "Alpha Beta Gamma" in element.text
    covered = "".join(element.text[s["start"] : s["end"]] for s in spans)
    assert covered == "AlphaBetaGamma"
    for span in spans:
        leaf = _resolve(payload, span["pointer"])
        assert (
            element.text[span["start"] : span["end"]]
            == leaf[span["source_start"] : span["source_end"]]
        )


def test_every_recorded_fixture_range_is_exact_and_survives_serialization() -> None:
    from pathlib import Path

    from rag_parser_core.extraction import StructuredExtraction

    raw = (Path(__file__).parent / "fixtures/xml_tree_sample.json").read_bytes()
    extracted = xml_tree_json_extraction(raw)
    assert extracted is not None
    extracted = StructuredExtraction.model_validate_json(extracted.model_dump_json())
    payload = json.loads(raw)
    maps = extracted.parser_artifacts["json_source_map"]["elements"]
    count = 0
    for element in extracted.elements:
        for span in maps[element.element_id]:
            count += 1
            leaf = _resolve(payload, span["pointer"])
            assert (
                element.text[span["start"] : span["end"]]
                == leaf[span["source_start"] : span["source_end"]]
            )
    assert count > 30


def test_mapping_operations_keep_escaped_pointers_and_one_to_many_ranges() -> None:
    from rag_parser_core.json_source_map import mapped_join, mapped_payload, mapped_slice

    source = {"a~/b": ["same"]}
    wrapped = mapped_payload(source)
    leaf = wrapped["a~/b"][0]
    result = mapped_slice(mapped_join(" / ", [leaf, leaf]), 1, 10)
    assert str(result) == "ame / sam"
    assert len(result.spans) == 2
    assert {s.pointer for s in result.spans} == {"/a~0~1b/0"}
    assert [s.source_start for s in result.spans] == [1, 0]
    assert source == {"a~/b": ["same"]}


@pytest.mark.parametrize("ascii_only", [False, True])
def test_raw_json_locator_reads_exact_escaped_node(ascii_only: bool) -> None:
    from rag_parser_core.json_source_map import raw_string_ranges

    text = 'A😀"\\\nZ'
    data = b"\xef\xbb\xbf" + json.dumps({"a~/b": [text, text]}, ensure_ascii=ascii_only).encode()
    source, nodes = raw_string_ranges(data, {"/a~0~1b/1"})
    node = nodes["/a~0~1b/1"]
    assert node.value == text
    a, b = node.raw_range(1, 2)
    assert json.loads('"' + source[a:b] + '"') == "😀"
    assert a >= source.index(",")
    assert node.raw_range(0, len(text)) == (node.checkpoints[0], node.end)


def test_raw_json_locator_rejects_ambiguous_duplicate_keys() -> None:
    from rag_parser_core.json_source_map import raw_string_ranges

    with pytest.raises(ValueError, match="duplicate"):
        raw_string_ranges(b'{"a":{"b":"first"},"a":{"b":"second"}}', {"/a/b"})


def test_formatting_marks_do_not_shift_persisted_source_ranges() -> None:
    payload = _document("A\u0332BC")
    extracted = xml_tree_json_extraction(json.dumps(payload).encode())
    assert extracted is not None
    element = extracted.elements[0]
    maps = extracted.parser_artifacts["json_source_map"]["elements"][element.element_id]
    for span in maps:
        source = _resolve(payload, span["pointer"])
        assert (
            element.text[span["start"] : span["end"]]
            == source[span["source_start"] : span["source_end"]]
        )


def test_large_node_uses_sparse_checkpoints_and_preserves_boundary_positions() -> None:
    from rag_parser_core.json_source_map import raw_string_ranges

    value = ("A😀\n" * 40000) + "終端"
    data = json.dumps([value], ensure_ascii=True).encode()
    source, nodes = raw_string_ranges(data, {"/0"})
    node = nodes["/0"]
    assert len(node.checkpoints) <= len(value) // 1024 + 1
    for start, end in [(0, 3), (1023, 1026), (len(value) - 2, len(value))]:
        a, b = node.raw_range(start, end)
        assert json.loads('"' + source[a:b] + '"') == value[start:end]


def test_merged_table_cell_records_one_to_many_mapping_without_markdown_syntax() -> None:
    payload = _document("Body")
    article = payload["law_full_text"]["children"][0]["children"][0]["children"][0]
    paragraph = article["children"][1]
    paragraph["children"].append(
        _node(
            "TableStruct",
            _node("Table", _node("TableRow", _node("TableHeaderColumn", "Repeated", colspan="2"))),
        )
    )
    raw = json.dumps(payload).encode()
    extraction = xml_tree_json_extraction(raw)
    assert extraction is not None
    element = extraction.elements[0]
    spans = extraction.parser_artifacts["json_source_map"]["elements"][element.element_id]
    repeated = [span for span in spans if element.text[span["start"] : span["end"]] == "Repeated"]
    assert len(repeated) == 2
    assert repeated[0]["pointer"] == repeated[1]["pointer"]
    assert repeated[0]["start"] != repeated[1]["start"]
    assert all("|" not in element.text[span["start"] : span["end"]] for span in spans)
