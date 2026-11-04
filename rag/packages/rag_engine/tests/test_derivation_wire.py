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
    assert [item.text for item in result.items] == [raw["items"][0]["text"]]
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
