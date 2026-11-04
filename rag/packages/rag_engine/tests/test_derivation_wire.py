"""導出を直接記載へ混ぜず、引用の private binding と依存を保持する。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_citation_unit_ids import snapshot

from rag_engine.generation.citation_unit_ids import SentUnitCatalog, unit_generation_model
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model


def case():
    original = snapshot()
    catalog = SentUnitCatalog([original])
    schema = unit_generation_model(generation_draft_wire_model(), catalog)
    fact = {"kind": "rule", "text": "資料の事実です。", "request_id": "Q1", "citations": [{"unit_ids": [original.unit_ids[0]]}]}
    derived = {
        "kind": "derived",
        "text": "導出した結論です。",
        "request_id": "Q1",
        "citations": [],
        "derivation": {"premises": [], "item_indexes": [0], "reasoning": "資料の事実を組み合わせます。"},
    }
    raw = {"items": [fact, derived], "summary": derived["text"], "summary_item_indexes": [1], "confidence": "medium"}
    return raw, schema, catalog


def test_derived_result_has_one_destination_and_summary_references_it():
    raw, schema, catalog = case()
    result = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert [item.text for item in result.items] == ["単位A"]
    assert raw["items"][0]["text"] not in result.items[0].text
    assert result.applications[0].result == raw["items"][1]["text"]
    assert result.applications[0].item_indexes == [0]
    assert not result.summary_item_indexes
    assert result.summary_application_indexes == [0]
    assert result.items[0].citations[0]._unit_binding.text == "単位A"


@pytest.mark.parametrize("change", ["missing", "quote", "direct", "boolean", "negative"])
def test_invalid_derivation_shape_is_not_accepted_as_a_direct_fact(change):
    raw, schema, _ = case()
    derived = raw["items"][1]
    if change == "missing":
        derived.pop("derivation")
    elif change == "quote":
        derived["citations"] = raw["items"][0]["citations"]
    elif change == "direct":
        derived["kind"] = "rule"
    else:
        derived["derivation"]["item_indexes"] = [True if change == "boolean" else -1]
    with pytest.raises(ValidationError):
        schema.model_validate(raw)


def test_reordering_keeps_direct_and_summary_dependencies():
    raw, schema, catalog = case()
    raw["items"].reverse()
    raw["items"][0]["derivation"]["item_indexes"] = [1]
    raw["summary_item_indexes"] = [0, 1]
    result = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert result.applications[0].item_indexes == [0]
    assert result.summary_item_indexes == [0]
    assert result.summary_application_indexes == [0]


def test_duplicate_sources_reindex_derived_dependencies_before_conversion():
    raw, schema, catalog = case()
    raw["items"].insert(1, deepcopy(raw["items"][0]))
    raw["items"][2]["derivation"]["item_indexes"] = [1]
    raw["summary_item_indexes"] = [2]
    result = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert len(result.items) == 1
    assert result.applications[0].item_indexes == [0]
    assert result.summary_application_indexes == [0]


def test_derived_dependency_is_not_redirected_to_another_source():
    raw, schema, catalog = case()
    raw["items"][1]["derivation"]["item_indexes"] = [1]
    result = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert result.applications[0].item_indexes[0] >= len(result.items)


def test_one_generation_call_publishes_derived_result_and_keeps_source_facts():
    import re
    from dataclasses import replace

    from test_grounded_answer import context, record

    from rag_engine.config import get_settings
    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.answering import synthesize_grounded_answer

    calls = []

    def generated(system, prompt, settings, schema, **options):
        calls.append(schema.__name__)
        assert getattr(schema, "_uses_unit_ids", False)
        units = {text: uid for uid, text in re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\] (.*)$", prompt)}
        return schema.model_validate(
            {
                "answer_parts": [{"id": "P1", "request_id": "Q1", "text": "合計", "result_kind": "derived", "item_indexes": [2]}],
                "items": [
                    {"kind": "rule", "request_id": "Q1", "text": text, "citations": [{"unit_ids": [units[text]]}]}
                    for text in ("項目Aは12です。", "項目Bは8です。")
                ]
                + [
                    {
                        "kind": "derived",
                        "request_id": "Q1",
                        "text": "合計は20です。",
                        "citations": [],
                        "derivation": {"premises": [], "item_indexes": [0, 1], "reasoning": "12＋8＝20"},
                    }
                ],
                "summary": "合計は20です。",
                "summary_item_indexes": [2],
                "confidence": "high",
            }
        )

    def unused(*args, **kwargs):
        raise AssertionError("不要な外部呼出")

    ctx = context(record(0, "項目Aは12です。\n項目Bは8です。", "合成の値", source="synthetic.txt"))
    settings = replace(get_settings(), answer_audit_mode="none", answer_max_correction_rounds=0)
    with bind_dependencies(AnswerDependencies(unused, unused, generated, unused, unused)):
        result = synthesize_grounded_answer("項目Aと項目Bの合計を教えてください。", ctx, settings, image_prompt_mode="text_only").response
    assert len(calls) == 1
    assert result.answer_text.startswith("合計は20です。")
    assert result.generation_trace["applications"][0]["published"]
    assert result.generation_trace["applications"][0]["result"] == "合計は20です。"
    assert result.generation_trace["finalization"]["summary"]["citations"]["links"][0]["application_index"] == 0
    assert result.generation_trace["rounds"][0]["timing"]["answer_parts"]["status"] == "linked"


def test_production_schema_has_no_free_direct_fact_or_summary_text():
    raw, schema, catalog = case()
    document = schema.model_json_schema()
    assert "summary" not in document["properties"]
    alternatives = schema.model_fields["items"].annotation.__args__[0].__args__
    for alternative in alternatives:
        kind = alternative.model_fields["kind"].annotation.__args__
        properties = alternative.model_json_schema()["properties"]
        if "derived" not in kind and "gap" not in kind:
            assert "text" not in properties
        else:
            assert "text" in properties
    raw["summary"] = "出力にない新しい結論"
    result = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert result.summary == raw["items"][1]["text"]
