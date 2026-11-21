"""合成の XML の文末を固定分割・項目抽出・要約まで保つ。"""

import json
from typing import Any

import pytest
from rag_parser_core.extraction import StructuredExtraction
from rag_parser_core.xml_tree_json import element_paragraphs, xml_tree_json_extraction
from rag_pipeline_core.chunking import chunk_extraction_with_strategy

from app.rag.document_summary import SummaryUnavailable, build_summary_input, summary_source
from app.rag.extraction_field_adapter import (
    FieldDefinition,
    FieldExtractionFailed,
    UnitExtraction,
    extract_fields_from_extraction,
)
from app.rag.field_extraction_units import (
    FieldExtractionUnit,
    plan_field_extraction_units,
    split_field_extraction_unit,
)


def _sample(*paragraphs: list[str]) -> StructuredExtraction:
    def node(tag: str, *children: Any, **attrs: str) -> dict[str, Any]:
        return {"tag": tag, "attr": attrs, "children": list(children)}

    blocks = [
        node(
            "Paragraph",
            node("ParagraphSentence", *(node("Sentence", part) for part in parts)),
            Num=str(i),
        )
        for i, parts in enumerate(paragraphs, 1)
    ]
    data = {
        "law_info": {},
        "revision_info": {"law_title": "見本"},
        "law_full_text": node("Law", node("LawBody", node("MainProvision", *blocks))),
    }
    extraction = xml_tree_json_extraction(json.dumps(data).encode())
    assert extraction is not None
    return StructuredExtraction.model_validate_json(extraction.model_dump_json())


def test_fixed_chunks_preserve_sentences_offsets_and_delimiter_text() -> None:
    sentences = ["あ" * 80 + "（補足。）を含む。", "い" * 80 + "。"]
    extraction = _sample(sentences, ["最後。"])
    chunks = chunk_extraction_with_strategy(
        extraction,
        strategy="fixed_delimiter",
        chunk_size=70,
        overlap=0,
        delimiter=["\\n見本 ", "。"],
    )
    assert all(extraction.raw_text[c.start_offset : c.end_offset] == c.text for c in chunks)
    assert "".join(c.text for c in chunks).replace("\n", "") == extraction.raw_text.replace(
        "\n", ""
    )
    for sentence in sentences:
        assert sum(sentence in c.text for c in chunks) == 1
    assert not any(all(sentence in c.text for sentence in sentences) for c in chunks)


def test_synthetic_heading_does_not_split_a_short_source_paragraph() -> None:
    parts = ["甲" * 50 + "。", "乙" * 50 + "。"]
    extraction = _sample(parts)
    chunks = chunk_extraction_with_strategy(
        extraction, strategy="fixed_delimiter", chunk_size=103, overlap=0, delimiter=["。"]
    )
    assert len(extraction.elements[0].text) > 103
    assert any("".join(parts) in c.text for c in chunks)


def test_field_units_and_overlap_stop_only_between_original_sentences() -> None:
    sentences = [f"{letter}" + letter * 112 + "（中。続）。" for letter in "甲乙丙丁"]
    last = "短い末尾。"
    extraction = _sample(
        [sentences[0], last], [sentences[1], last], [sentences[2], last], [sentences[3], last]
    )
    units = plan_field_extraction_units(extraction, text_budget_tokens=310)
    assert len(units) > 1
    assert [atom.text for unit in units for atom in unit.atoms] == [
        e.text for e in extraction.elements
    ]
    assert [atom.text for unit in units for atom in unit.overlap] == [last] * (len(units) - 1)

    one = _sample(sentences)
    original = plan_field_extraction_units(one, text_budget_tokens=1000)[0]
    parts = split_field_extraction_unit(original, text_budget_tokens=300)
    assert len(parts) > 1
    assert "".join(atom.text for u in parts for atom in u.atoms) == one.elements[0].text
    assert all(
        any(sentence in atom.text for u in parts for atom in u.atoms) for sentence in sentences
    )


def test_summary_prefers_full_paragraph_then_sentence() -> None:
    first = "あ" * 90 + "（途中。続きを含む）。"
    extraction = _sample([first, "い" * 100 + "。"], ["う" * 80 + "。", "末尾。"])
    source = summary_source(extraction)
    prefix = extraction.elements[0].text
    assert build_summary_input(source, max_chars=len(prefix) + 45).body_chars == len(prefix)
    limited = build_summary_input(source, max_chars=source.body.index(first) + len(first) + 15)
    assert source.body[: limited.body_chars].endswith(first)
    with pytest.raises(SummaryUnavailable, match="input_too_large"):
        build_summary_input(source, max_chars=20)


@pytest.mark.asyncio
async def test_indivisible_sentence_stops_before_first_model_call() -> None:
    extraction = _sample(["小段落。"], ["字" * 330 + "（注。続）。"])

    async def unexpected(unit: FieldExtractionUnit) -> UnitExtraction:
        pytest.fail("予算を超えた一文を含む抽出を送信した")

    with pytest.raises(FieldExtractionFailed, match="input_too_large"):
        await extract_fields_from_extraction(
            extraction, [FieldDefinition(name="項目")], unexpected, text_budget_tokens=280
        )


def test_old_artifacts_and_changed_element_fall_back_to_full_paragraph() -> None:
    extraction = _sample(["一文。", "二文。"])
    original = extraction.elements[0]
    paragraphs = element_paragraphs(extraction, original)
    assert paragraphs and paragraphs[0].sentence_ends
    changed = original.model_copy(update={"text": original.text.replace("一", "青")})
    paragraphs = element_paragraphs(extraction, changed)
    assert paragraphs and paragraphs[0].sentence_ends == ()
    extraction.parser_artifacts.pop("element_text_boundaries")
    paragraphs = element_paragraphs(extraction, original)
    assert paragraphs and paragraphs[0].sentence_ends == ()
