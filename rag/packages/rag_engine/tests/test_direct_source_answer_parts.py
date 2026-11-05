"""直接記載を送達原文から構成し、導出の公開門とは役割を分ける。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import generation_output_model


def case(text: str = "項目Aは12です。"):
    span = {"label": "E1", "evidence_id": "source-a", "source_id": "s-a", "source": "synthetic.txt", "text": text}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(text))], "explicit_units_v5")
    catalog = SentUnitCatalog([snapshot])
    model = generation_output_model("項目Aの値を教えてください。", unit_catalog=catalog)
    raw = {
        "answer_parts": [
            {
                "request_id": "Q1",
                "requirement": "資料の記録値",
                "answer": {"kind": "source", "basis": {"unit_ids": list(snapshot.unit_ids)}},
            }
        ],
        "summary_part_indexes": [0],
        "confidence": "high",
    }
    return raw, model, catalog, snapshot


def test_source_only_builds_direct_items_and_summary_without_application():
    raw, model, catalog, snapshot = case()
    original = deepcopy(raw)
    draft = canonical_generation_draft(model.model_validate(raw), unit_catalog=catalog)
    assert raw == original
    assert not draft.applications
    assert draft.items[0].kind == "rule"
    assert draft.items[0].text == snapshot.span()["text"]
    assert draft.items[0].citations[0]._unit_binding.text == snapshot.span()["text"]
    assert draft.answer_parts[0].result_kind == "direct"
    assert draft.answer_parts[0].item_indexes == [0]
    assert draft.answer_parts[0].application_indexes == []
    assert draft.summary == snapshot.span()["text"]
    assert draft.summary_item_indexes == [0]
    assert draft.summary_application_indexes == []


def test_mixed_source_derived_gap_keep_roles_dependencies_and_summary():
    raw, model, catalog, _ = case()
    basis = deepcopy(raw["answer_parts"][0]["answer"]["basis"])
    raw["answer_parts"].extend(
        [
            {
                "request_id": "Q1",
                "requirement": "2件の計算",
                "answer": {"kind": "answer", "basis": basis, "reasoning": "12×2=24", "result": "24です。"},
            },
            {"request_id": "Q1", "requirement": "未記録の項目", "answer": {"kind": "gap", "text": "項目Bの資料が必要です。"}},
        ]
    )
    raw["summary_part_indexes"] = [0, 1]
    draft = canonical_generation_draft(model.model_validate(raw), unit_catalog=catalog)
    assert [part.result_kind for part in draft.answer_parts] == ["direct", "derived", "gap"]
    assert len(draft.applications) == 1
    assert draft.applications[0].result == "24です。"
    assert draft.applications[0].item_indexes == draft.answer_parts[0].item_indexes
    assert draft.answer_parts[1].application_indexes == [0]
    assert not draft.answer_parts[1].item_indexes
    assert draft.summary_item_indexes == [0] and draft.summary_application_indexes == [0]
    assert draft.summary == "項目Aは12です。\n24です。"
    assert "項目B" not in draft.summary


def test_original_readback_through_answer_remains_derived():
    raw, model, catalog, snapshot = case()
    raw["answer_parts"][0]["answer"].update(kind="answer", reasoning="選択した原文を示す。", result=snapshot.span()["text"])
    draft = canonical_generation_draft(model.model_validate(raw), unit_catalog=catalog)
    assert draft.answer_parts[0].result_kind == "derived"
    assert len(draft.applications) == 1
    assert not draft.summary_item_indexes and draft.summary_application_indexes == [0]


@pytest.mark.parametrize("field", ["text", "result", "reasoning"])
def test_source_cannot_smuggle_generated_text_or_result(field):
    raw, model, _, _ = case()
    raw["answer_parts"][0]["answer"][field] = "原文に無い新しい結論"
    with pytest.raises(ValidationError):
        model.model_validate(raw)


@pytest.mark.parametrize("kind", ["direct", "rule", "operation", "confirmation", "unknown"])
def test_undeclared_answer_kind_is_not_silently_projected_to_gap(kind):
    raw, model, _, _ = case()
    raw["answer_parts"][0]["answer"]["kind"] = kind
    with pytest.raises(ValidationError):
        model.model_validate(raw)


@pytest.mark.parametrize("ids", [[], ["foreign-unit"]])
def test_source_requires_nonempty_sent_unit_ids(ids):
    raw, model, _, _ = case()
    raw["answer_parts"][0]["answer"]["basis"]["unit_ids"] = ids
    with pytest.raises(ValidationError):
        model.model_validate(raw)


def test_source_preserves_long_original_and_repeated_selection_without_rewriting():
    text = "条件と例外を含む原文です。" * 100
    raw, model, catalog, snapshot = case(text)
    raw["answer_parts"][0]["answer"]["basis"]["unit_ids"] *= 2
    draft = canonical_generation_draft(model.model_validate(raw), unit_catalog=catalog)
    assert len(draft.items) == 1
    assert draft.items[0].text == text and draft.summary == text
    assert draft.items[0].citations[0]._unit_binding.unit_ids == snapshot.unit_ids


def test_empty_catalog_keeps_only_gap():
    model = generation_output_model("記録値を教えてください。", unit_catalog=SentUnitCatalog(()))
    raw = {
        "answer_parts": [{"request_id": "Q1", "requirement": "資料の値", "answer": {"kind": "gap", "text": "資料が必要です。"}}],
        "confidence": "low",
    }
    model.model_validate(raw)
    raw["answer_parts"][0]["answer"] = {"kind": "source", "basis": {"unit_ids": []}}
    with pytest.raises(ValidationError):
        model.model_validate(raw)
