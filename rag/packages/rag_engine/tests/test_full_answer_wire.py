"""合成の同じ本文を、引用と種別の違いで書き換えない。"""

import pytest
from pydantic import ValidationError

from rag_engine.generation import grounded
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.full_answer_wire import canonical_full_answer, full_answer_model, render_full_answer


def catalog():
    text = "合成装置Aの入力電圧は12 Vです。"
    span = {
        "text": text,
        "source": "synthetic.txt",
        "source_id": "child-a",
        "evidence_id": "child-a",
        "label": "E1",
        "page": "",
        "scope_source_ids": ["child-a"],
    }
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(text))], "synthetic")
    return SentUnitCatalog([snapshot]), snapshot.unit_ids[0]


@pytest.mark.parametrize("kind", ["answered", "insufficient_evidence", "conditional", "needs_clarification", "needs_environment_data"])
@pytest.mark.parametrize("cited", [False, True])
def test_preserves_complete_body_regardless_of_type_or_citation_count(kind, cited):
    units, unit = catalog()
    body = "入力電圧は12 Vです。" + ("[C1]" if cited else "")
    wire = full_answer_model(units).model_validate(
        {"answer_body": body, "response_kind": kind, "confidence": "high", "citations": [{"unit_ids": [unit]}] if cited else []}
    )
    draft = canonical_full_answer(wire, units)
    checked, dropped = grounded.verify("入力電圧は？", draft, [units.snapshots[0].span()])
    result = render_full_answer(draft, checked)
    assert "入力電圧は12 Vです。" in result.text
    assert "確認できなかった" not in result.text
    assert draft._response_kind == kind
    assert dropped == []
    assert len(result.citation_lines) == int(cited)
    if cited:
        assert result.citation_lines[0]["scope_source_ids"] == ["child-a"]


def test_unused_references_are_not_recorded_as_adopted_evidence():
    units, unit = catalog()
    wire = full_answer_model(units).model_validate(
        {
            "answer_body": "資料に記載がなく、回答できません。",
            "response_kind": "insufficient_evidence",
            "confidence": "low",
            "citations": [{"unit_ids": [unit]}],
        }
    )
    draft = canonical_full_answer(wire, units)
    assert draft.items == []
    assert render_full_answer(draft, []).text == wire.answer_body


def test_unavailable_unit_is_a_format_failure():
    units, _unit = catalog()
    with pytest.raises(ValidationError):
        full_answer_model(units).model_validate(
            {
                "answer_body": "合成の回答[C1]",
                "response_kind": "answered",
                "confidence": "high",
                "citations": [{"unit_ids": ["U-not-sent"]}],
            }
        )


def test_shared_entry_keeps_body_with_an_explicit_reference():
    from test_answer_flow_single_generation import ROW, _run, context, record

    body = "保存年数は5年です。"
    response, calls = _run(
        "保存年数は？",
        context(record(0, ROW, "合成の規則", source="synthetic.md")),
        lambda units: {
            "answer_body": body + "[C1]",
            "response_kind": "answered",
            "confidence": "high",
            "citations": [{"unit_ids": [units[ROW]]}],
        },
    )
    assert calls == ["FullAnswerWire"]
    assert body in response.answer_text
    assert response.envelope["outcome"] == "answered"
    assert response.cited_evidence
    assert response.generation_trace["citation_lines"]
    assert response.generation_trace["llm_calls"] == 1


def test_replays_the_correct_text_misclassified_as_a_gap_before_the_new_contract():
    from rag_engine.generation.answer_parts_wire import answer_parts_model
    from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model

    units, _unit = catalog()
    correct = "入力電圧は12 Vです。"
    old_schema = answer_parts_model(generation_draft_wire_model(), units)
    old = canonical_generation_draft(
        old_schema.model_validate(
            {
                "response_kind": "answered",
                "confidence": "high",
                "answer_parts": [
                    {
                        "request_id": "Q1",
                        "requirement": "入力電圧",
                        "answer": {"kind": "gap", "text": correct},
                    }
                ],
                "summary_part_indexes": [],
            }
        ),
        unit_catalog=units,
    )
    assert old._response_kind == "insufficient_evidence"
    new_schema = full_answer_model(units)
    new = canonical_full_answer(
        new_schema.model_validate(
            {
                "answer_body": correct,
                "response_kind": "answered",
                "confidence": "high",
                "citations": [],
            }
        ),
        units,
    )
    assert new._response_kind == "answered"
    assert render_full_answer(new, []).text == correct


def test_inline_citations_do_not_split_a_sentence_or_a_table_row():
    units, unit = catalog()
    body = "値は12 Vです[C1]。\n\n| 項目 | 値 |\n| --- | --- |\n| A | 12 V[C1] |"
    wire = full_answer_model(units).model_validate(
        {"answer_body": body, "response_kind": "answered", "confidence": "high", "citations": [{"unit_ids": [unit]}]}
    )
    draft = canonical_full_answer(wire, units)
    checked, _ = grounded.verify("入力電圧は？", draft, [units.snapshots[0].span()])
    result = render_full_answer(draft, checked)
    assert result.text.startswith("値は12 Vです。\n根拠：")
    assert "| 項目 | 値 |\n| --- | --- |\n| A | 12 V |\n根拠：" in result.text
    assert len(result.citation_lines) == 2
