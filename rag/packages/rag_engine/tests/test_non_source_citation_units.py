"""解析器が本文に付けた原文に無い行は引用単位にしない。原文の行は今までどおり引用できる（#5305）。

根拠は合成の本文。XML ツリー JSON の段落の chunk（解析器が組み立てた見出しの行を範囲で宣言する）、PDF の
見出しを含む chunk、Markdown の見出しを含む chunk を比べる。モデルは使わない。
"""

from dataclasses import replace
from typing import Any

from rag_parser_core.non_source_text import NON_SOURCE_RANGES_KEY
from test_rag_evidence_recovery import rec

from rag_engine.generation import grounded
from rag_engine.generation.batched_evidence import _slice, reading_units
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import GroundedItem
from rag_engine.retrieval.evidence_selection import evidence_spans, record_non_source_ranges

HEADING = "合成文書 第三条（対象の範囲）"
BODY = ["一　第一の区分の対象を選ぶ。", "二　第二の区分の対象を保存する。", "三　前二号の手順は画面Aで行う。"]
XML_TEXT = "\n".join([HEADING, *BODY])


def span(text: str, **fields: Any) -> dict[str, Any]:
    return {
        "evidence_id": "stable-a",
        "label": "E1",
        "source_id": "parent-a",
        "source": "synthetic",
        "text": text,
        "function": ("synthetic", "合成の説明"),
        "tags": [],
        **fields,
    }


def xml_span(**fields: Any) -> dict[str, Any]:
    return span(XML_TEXT, **{NON_SOURCE_RANGES_KEY: [[0, len(HEADING)]]}, **fields)


def pieces(value: dict[str, Any]) -> list[str]:
    return [value["text"][start:end] for start, end in grounded.span_citation_units(value)]


def item(start: int, end: int) -> GroundedItem:
    raw = {
        "items": [
            {
                "kind": "operation",
                "request_id": "Q1",
                "text": "合成の手順を案内します。",
                "citations": [{"evidence_id": "E1", "line_start": start, "line_end": end}],
            }
        ],
        "summary": "合成の案内です。",
        "confidence": "low",
    }
    return canonical_generation_draft(generation_draft_wire_model().model_validate(raw)).items[0]


def test_parser_heading_line_is_not_a_citation_unit() -> None:
    value = xml_span()
    assert pieces(value) == BODY
    grounded.format_functions([value])
    catalog = grounded.generation_unit_catalog([value])
    # モデルが選べる単位の列挙に、見出しの行が無い。
    assert catalog.unit_labels == ("E1.1", "E1.2", "E1.3")
    assert [binding.text for binding in catalog.bind(["E1.1"])] == [BODY[0]]


def test_evidence_block_keeps_the_heading_without_a_number() -> None:
    rendered = grounded.format_functions([xml_span()])
    lines = rendered.splitlines()
    assert HEADING in lines
    assert f"1| [E1.1] {BODY[0]}" in lines
    assert not any(line.endswith(HEADING) and "|" in line for line in lines)
    # 見出しの行は本文の位置（番号の付いた行の前）に残す。
    assert lines.index(HEADING) < lines.index(f"1| [E1.1] {BODY[0]}")


def test_cited_range_extracts_only_source_lines() -> None:
    value = xml_span()
    grounded.format_functions([value])
    cited, error = grounded.cited_quote(item(1, 2), [value])
    assert not error
    assert cited.quote == "\n".join(BODY[:2])
    assert HEADING not in cited.quote
    # 見出しの行の分の番号は無い（範囲の外は今までどおり引用にしない）。
    _cited, error = grounded.cited_quote(item(1, 4), [value])
    assert error
    assert grounded._citation_extraction(_cited)["reason_code"] == "citation_range_out_of_bounds"


def test_unnumbered_short_evidence_quotes_only_the_source_text() -> None:
    # 番号を付けない旧い表示（全文の引用）でも、解析器の行は引用の外になる。
    value = span(f"{HEADING}\n{BODY[0]}", **{NON_SOURCE_RANGES_KEY: [[0, len(HEADING)]]})
    view = grounded._citation_view(value, explicit_units=False)
    assert view.contract_version == "legacy_threshold_v1"
    assert [value["text"][a:b] for a, b in view.units] == [BODY[0]]


def test_source_heading_lines_stay_citable_without_declared_ranges() -> None:
    # PDF・Markdown の見出しは原文の文字列なので、範囲を宣言しない。今までどおり引用単位になる。
    pdf = span("第3章 対象の範囲\n対象を選び、保存します。")
    markdown = span("## 対象の範囲\n対象を選び、保存します。")
    assert pieces(pdf) == ["第3章 対象の範囲", "対象を選び、保存します。"]
    assert pieces(markdown) == ["## 対象の範囲", "対象を選び、保存します。"]
    assert pieces(span(XML_TEXT)) == [HEADING, *BODY]


def test_merged_unit_is_trimmed_to_its_source_part() -> None:
    # 画像の説明・OCR は短い行を 1 単位にまとめる。宣言した範囲が単位の一部なら、残りの原文だけを単位にする。
    text = "見出しの行\n欄A\n欄B"
    value = span(text, origin="image_extraction", **{NON_SOURCE_RANGES_KEY: [[0, len("見出しの行")]]})
    assert grounded.citation_units(text, image_text=True) == [(0, len(text))]
    assert pieces(value) == ["欄A\n欄B"]
    # 範囲が単位の中ほどなら、前後の原文を別の単位にする。
    middle = span("前の行\n付けた行\n後の行", origin="image_extraction", **{NON_SOURCE_RANGES_KEY: [[4, 8]]})
    assert pieces(middle) == ["前の行", "後の行"]


def test_broken_ranges_fall_back_to_the_current_units() -> None:
    for broken in ("not-json", [[0]], [["0", "3"]], {"start": 0}):
        assert pieces(span(XML_TEXT, **{NON_SOURCE_RANGES_KEY: broken})) == [HEADING, *BODY]


def test_snapshot_identity_includes_the_declared_ranges() -> None:
    snapshots = []
    for value in (span(XML_TEXT), xml_span()):
        grounded.format_functions([value])
        snapshots.extend(grounded.generation_unit_catalog([value]).snapshots)
    plain, declared = snapshots
    assert plain.snapshot_id != declared.snapshot_id


def test_evidence_spans_move_record_ranges_to_the_excerpt() -> None:
    record = rec("p1", "parent", 1, XML_TEXT)
    record = replace(record, metadata={**record.metadata, NON_SOURCE_RANGES_KEY: f"[[0,{len(HEADING)}]]"})
    assert record_non_source_ranges(record) == ((0, len(HEADING)),)
    spans = evidence_spans("第一の区分の対象は？", [record])
    assert spans
    covered = [(value["start"] + a, value["start"] + b) for value in spans for a, b in value.get(NON_SOURCE_RANGES_KEY, [])]
    assert covered == [(0, len(HEADING))]
    assert all(HEADING not in "\n".join(pieces({**value, "function": ("s", "")})) for value in spans)
    # 回答用の本文が chunk の本文と違う record（画像の説明の投影など）は、位置が決まらないので範囲を使わない。
    projected = replace(record, body_text=XML_TEXT + "\n別の本文")
    assert record_non_source_ranges(projected) == ()


def test_batched_slices_keep_ranges_in_their_own_coordinates() -> None:
    value = xml_span(start=0, end=len(XML_TEXT))
    head = _slice(value, 0, len(HEADING) + 1 + len(BODY[0]))
    tail = _slice(value, len(HEADING) + 1, len(XML_TEXT))
    assert head[NON_SOURCE_RANGES_KEY] == [[0, len(HEADING)]]
    assert NON_SOURCE_RANGES_KEY not in tail
    assert [head["text"][a:b] for a, b in reading_units(head)] == [BODY[0]]


def test_range_across_paragraphs_skips_the_inner_heading_line() -> None:
    # 親の本文は段落ごとに解析器の見出しの行を持つ。続く単位の範囲の中ほどにある見出しも引用に入れない。
    first, second = "見出しA", "見出しB"
    text = "\n".join([first, BODY[0], second, BODY[1]])
    offset = len(first) + 1 + len(BODY[0]) + 1
    value = span(text, **{NON_SOURCE_RANGES_KEY: [[0, len(first)], [offset, offset + len(second)]]})
    assert pieces(value) == BODY[:2]
    grounded.format_functions([value])
    cited, error = grounded.cited_quote(item(1, 2), [value])
    assert not error
    assert cited.quote == "\n".join(BODY[:2])


def test_system_quotes_show_only_source_text() -> None:
    value = xml_span(evidence_id="E-xml", location="", page=1)
    heading_only = span(HEADING, evidence_id="E-head", **{NON_SOURCE_RANGES_KEY: [[0, len(HEADING)]]})
    quotes = grounded.deadline_document_quotes("合成の質問", [heading_only, value])
    assert [entry.item.quote for entry in quotes] == ["\n".join(BODY)]
