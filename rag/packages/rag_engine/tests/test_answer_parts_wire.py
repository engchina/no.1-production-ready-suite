"""要点と結果のinline契約に、モデルによる重複分類・内部番号を要求しない。"""

import json

import pytest
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import ValidationError
from test_citation_unit_ids import snapshot

from rag_engine.generation.answer_parts_wire import answer_parts_model
from rag_engine.generation.citation_unit_ids import SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model


def case():
    original = snapshot()
    catalog = SentUnitCatalog([original])
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw = {
        "response_kind": "answered",
        "answer_parts": [
            {
                "request_id": "Q1",
                "requirement": "元の値",
                "answer": {
                    "kind": "answer",
                    "basis": {"unit_ids": [original.unit_ids[0]]},
                    "reasoning": "原文の記載を示す。",
                    "result": "単位A",
                },
            },
            {
                "request_id": "Q1",
                "requirement": "導出結果",
                "answer": {
                    "kind": "answer",
                    "basis": {"unit_ids": [original.unit_ids[0]]},
                    "result": "導いた結果",
                    "reasoning": "資料の値から導く。",
                },
            },
        ],
        "summary_part_indexes": [1],
        "confidence": "high",
    }
    return raw, schema, catalog


@pytest.mark.parametrize("change", ["missing_result", "missing_basis", "invented_source", "direct_text"])
def test_invalid_declared_derived_or_free_direct_fact_cannot_parse(change):
    raw, schema, _ = case()
    answer = raw["answer_parts"][1]["answer"]
    if change == "missing_result":
        answer.pop("result")
    elif change == "missing_basis":
        answer.pop("basis")
    elif change == "invented_source":
        answer["basis"]["unit_ids"] = ["not-sent"]
    else:
        raw["answer_parts"][0]["answer"]["text"] = "新しい結論"
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_gap_and_summary_have_no_fabricated_source():
    raw, schema, catalog = case()
    raw["answer_parts"] = [{"request_id": "Q1", "requirement": "不足の値", "answer": {"kind": "gap", "text": "値が必要です。"}}]
    raw["summary_part_indexes"] = [0]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.items[0].kind == "gap"
    assert not draft.items[0].citations
    assert not draft.applications
    assert not draft.summary


def test_no_sent_source_allows_only_a_gap_at_the_generation_boundary():
    catalog = SentUnitCatalog(())
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw = {
        "response_kind": "answered",
        "answer_parts": [{"request_id": "Q1", "requirement": "不足の値", "answer": {"kind": "gap", "text": "資料が必要です。"}}],
        "confidence": "low",
    }
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.items[0].kind == "gap"
    assert not draft.applications
    assert "SupportedAnswerPart" not in schema.model_json_schema().get("$defs", {})
    raw["answer_parts"][0]["answer"] = {"kind": "answer", "basis": {"unit_ids": []}, "reasoning": "根拠がない。", "result": "推測値"}
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_question_copy_is_not_generated_again_as_a_premise():
    raw, schema, catalog = case()
    raw["answer_parts"][1]["answer"]["question_premises"] = ["合成の質問"]
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_source_method_and_result_are_typed_and_the_plan_is_not_generated():
    """生成の schema に計画（request_coverage）は無く、モデルが計画の行を返しても読まない（#4611）。"""
    raw, schema, catalog = case()
    document = schema.model_json_schema()
    fields = list(document["properties"])
    # 質問の種類（導出が要るか）を要点より先に生成させる（#4611）。
    assert fields[:3] == ["response_language", "question_type", "answer_parts"]
    strict = type_to_text_format_param(schema)["schema"]
    assert list(strict["properties"])[:3] == strict["required"][:3] == ["response_language", "question_type", "answer_parts"]
    assert "request_coverage" not in fields
    assert "relation" not in json.dumps(document)
    supported = document["$defs"]["SupportedAnswerPart"]
    assert list(supported["properties"]) == ["kind", "basis", "reasoning", "result"]
    assert set(supported["required"]) == {"kind", "basis", "reasoning", "result"}
    assert document["$defs"]["CitationUnitSelection"]["properties"]["unit_ids"]["minItems"] == 1
    raw["request_coverage"] = [
        {"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "項目A", "relation": "unrelated", "link": None}]}
    ]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert {scope.relation for plan in draft.request_coverage for scope in plan.evidence_scope} == {"direct"}


def test_forty_answer_parts_preserve_every_result_and_last_summary_dependency():
    from copy import deepcopy

    raw, schema, catalog = case()
    template = raw["answer_parts"][0]
    raw["answer_parts"] = []
    for index in range(40):
        part = deepcopy(template)
        part["requirement"] = f"要点{index + 1}"
        part["answer"]["result"] = f"結論{index + 1}"
        raw["answer_parts"].append(part)
    raw["summary_part_indexes"] = [39]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert len(draft.applications) == 40
    assert len(draft.items) == 1
    assert draft.answer_parts[-1].application_indexes == [39]
    assert draft.summary_application_indexes == [39]
    assert draft.summary == "結論40"


def test_selected_long_original_is_preserved_instead_of_rejected_or_truncated():
    from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot

    original = snapshot().span()
    text = "対象の条件と例外を省略しない原文です。" * 80
    original["text"] = text
    source = EvidenceUnitSnapshot.create(original, [(0, len(text))], "synthetic-long-source")
    catalog = SentUnitCatalog([source])
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw, _, _ = case()
    raw["answer_parts"] = raw["answer_parts"][:1]
    raw["answer_parts"][0]["answer"]["basis"]["unit_ids"] = list(source.unit_ids)
    raw["summary_part_indexes"] = [0]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.items[0].text == text
    assert draft.items[0].citations[0]._unit_binding.text == text
    assert draft.applications[0].item_indexes == [0]
    assert len(draft.items[0].text) > 600


def test_generated_answer_still_has_a_length_limit_separate_from_source_length():
    raw, schema, _ = case()
    raw["answer_parts"][0]["answer"]["result"] = "あ" * 601
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_formal_generation_entry_uses_answer_parts_and_keeps_every_plan_hidden():
    from rag_engine.generation import grounded

    _, _, catalog = case()
    schema = grounded.generation_output_model("対象の値は何ですか。", unit_catalog=catalog)
    assert schema._uses_typed_answer_parts
    assert "items" not in schema.model_json_schema()["properties"]
    assert "request_coverage" not in schema.model_json_schema()["properties"]


@pytest.mark.parametrize("request_count,source_count", [(1, 7), (40, 2)])
def test_selected_facts_are_not_limited_by_the_number_of_generated_parts(request_count, source_count):
    from copy import deepcopy

    sources = [snapshot(evidence_id=f"source-{index}", label=f"E{index + 1}", source_id=f"source-{index}") for index in range(source_count)]
    catalog = SentUnitCatalog(sources)
    schema = answer_parts_model(generation_draft_wire_model(), catalog)
    raw, _, _ = case()
    part = raw["answer_parts"][0]
    part["answer"]["basis"]["unit_ids"] = [source.unit_ids[0] for source in sources]
    raw["answer_parts"] = []
    for index in range(request_count):
        current = deepcopy(part)
        current["request_id"] = f"Q{index + 1}"
        current["requirement"] = f"合成の要点{index + 1}"
        raw["answer_parts"].append(current)
    raw["summary_part_indexes"] = [request_count - 1]
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert len(draft.items) == request_count * source_count
    assert len(draft.applications) == request_count
    assert len(draft.applications[-1].item_indexes) == source_count
    assert draft.applications[-1].item_indexes[-1] == len(draft.items) - 1
    assert draft.summary_application_indexes == [request_count - 1]


def test_maximum_length_result_survives_canonicalization_without_truncation():
    raw, schema, catalog = case()
    raw["answer_parts"][0]["answer"]["result"] = "あ" * 600
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.applications[0].result == "あ" * 600
