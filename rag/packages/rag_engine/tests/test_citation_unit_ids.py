"""安定した単位 ID を原文の版・送達範囲へ厳密に結ぶ。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog


def snapshot(**updates):
    span = {
        "evidence_id": "E1",
        "label": "E1",
        "source_id": "source-1",
        "source": "synthetic.txt",
        "text": "単位A\n単位B\n単位C",
        "start": 10,
        "end": 21,
        "document_scope": {"version": "v1"},
    }
    span.update(updates)
    return EvidenceUnitSnapshot.create(span, [(0, 3), (4, 7), (8, 11)], "synthetic-units-v1")


def test_display_order_does_not_change_unit_identity():
    first = snapshot()
    second = snapshot(evidence_id="E99", label="E99")
    assert first.snapshot_id == second.snapshot_id
    assert first.unit_ids == second.unit_ids


@pytest.mark.parametrize(
    "updates",
    [
        {"text": "新版A\n単位B\n単位C"},
        {"start": 20},
        {"source_id": "source-2"},
        {"document_scope": {"version": "v2"}},
        {"version": "v2"},
        {"source_run_id": "run-2"},
        {"origin": "image_extraction"},
    ],
)
def test_content_location_and_source_versions_change_identity(updates):
    assert set(snapshot().unit_ids).isdisjoint(snapshot(**updates).unit_ids)


def test_schema_has_only_units_that_were_sent_and_rejects_free_ranges():
    original = snapshot()
    catalog = SentUnitCatalog([original])
    model = catalog.selection_model()
    assert model.model_validate({"unit_ids": [original.unit_ids[0]]}).unit_ids == [original.unit_ids[0]]
    for value in (
        {"unit_ids": ["unknown"]},
        {"unit_ids": []},
        {"unit_ids": [snapshot(source_id="other").unit_ids[0]]},
        {"unit_ids": [original.unit_ids[0]], "line_start": 1, "line_end": 3},
    ):
        with pytest.raises(ValidationError):
            model.model_validate(value)
    assert "line_end" not in model.model_json_schema()["properties"]


def test_non_adjacent_selection_does_not_invent_middle_text():
    original = snapshot()
    catalog = SentUnitCatalog([original])
    selected = catalog.bind([original.unit_ids[2], original.unit_ids[0], original.unit_ids[0]])
    assert [part.text for part in selected] == ["単位A", "単位C"]
    assert [(part.first, part.last) for part in selected] == [(0, 0), (2, 2)]
    adjacent = catalog.bind(original.unit_ids[:2])
    assert len(adjacent) == 1 and adjacent[0].text == "単位A\n単位B"


def test_snapshot_is_independent_of_later_mutation_and_relabel():
    source = snapshot().span()
    copy = deepcopy(source)
    original = EvidenceUnitSnapshot.create(source, [(0, 3)], "synthetic")
    source["text"] = "別の本文"
    source["document_scope"]["version"] = "v9"
    assert original.span() == copy
    assert SentUnitCatalog([original]).bind(original.unit_ids)[0].text == "単位A"


def test_foreign_or_missing_unit_is_not_repaired_by_guessing():
    catalog = SentUnitCatalog([snapshot()])
    for ids in ([], ["unknown"], [snapshot(source_id="other").unit_ids[0]]):
        with pytest.raises(ValueError):
            catalog.bind(ids)


def test_generation_contract_has_no_free_source_or_range():
    from rag_engine.generation.citation_unit_ids import unit_generation_model
    from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model

    original = snapshot()
    catalog = SentUnitCatalog([original])
    model = unit_generation_model(generation_draft_wire_model(), catalog)
    schema = model.model_json_schema()
    selection = schema["$defs"]["CitationUnitSelection"]["properties"]
    assert set(selection) == {"unit_ids"}
    data = {
        "items": [{"kind": "rule", "text": "合成の説明", "citations": [{"unit_ids": [original.unit_ids[0], original.unit_ids[2]]}]}],
        "summary": "",
        "confidence": "low",
    }
    parsed = model.model_validate(data)
    draft = canonical_generation_draft(parsed, unit_catalog=catalog)
    assert [citation.lines for citation in draft.items[0].citations] == ["1", "3"]
    assert [citation._unit_binding.text for citation in draft.items[0].citations] == ["単位A", "単位C"]
    assert all(citation.evidence_id == "E1" for citation in draft.items[0].citations)
    data["items"][0]["citations"] = [{"evidence_id": "E1", "line_start": 1, "line_end": 99}]
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_generation_binding_keeps_original_source_after_new_display_labels():
    from test_citation_view_snapshots import span

    from rag_engine.generation import grounded
    from rag_engine.generation.citation_wire import canonical_generation_draft

    first, other = span(), span(2, "別の本文です。" * 60)
    grounded.format_functions([first])
    catalog = grounded.generation_unit_catalog([first])
    model = grounded.generation_output_model("合成の質問", unit_catalog=catalog)
    draft = canonical_generation_draft(
        model.model_validate(
            {
                "items": [
                    {
                        "kind": "operation",
                        "text": "対象の結果を確認します。",
                        "citations": [{"unit_ids": [catalog.snapshots[0].unit_ids[1]]}],
                    }
                ],
                "summary": "",
                "confidence": "low",
            }
        ),
        unit_catalog=catalog,
    )
    relabeled = dict(other, label="E1")
    grounded.format_functions([relabeled])
    resolved, error = grounded.cited_quote(draft.items[0], [relabeled])
    assert not error
    assert resolved.quote == draft.items[0].citations[0]._unit_binding.text.strip()
    proof = resolved.citations[0]._extraction
    assert proof["source_id"] == first["source_id"]
    assert proof["range_contract"] == "stable_unit_ids"
    assert proof["snapshot_id"] == catalog.snapshots[0].snapshot_id
    assert proof["sent_view_status"] == "matched"


def test_batch_unit_rejection_feedback_does_not_store_unknown_value():
    import json

    import pytest
    from pydantic import ValidationError

    from rag_engine.generation.batch_output_repair import batch_repair_prompt, batch_validation_detail
    from rag_engine.generation.batched_evidence import _reading_model

    model = _reading_model([{"source_id": "p1", "text": "合成の原文です。"}])
    with pytest.raises(ValidationError) as rejected:
        model.model_validate({"findings": [{"unit_ids": ["PRIVATE_UNKNOWN_UNIT"], "answer": "合成の回答"}]})
    detail = batch_validation_detail(rejected.value, model.model_json_schema())
    assert detail["validation_errors"][0]["protocol_code"] == "batch_unit_invalid"
    assert "PRIVATE_UNKNOWN_UNIT" not in json.dumps(detail)
    assert "合成の原文" not in json.dumps(detail, ensure_ascii=False)
    feedback = batch_repair_prompt("合成の入力", detail)
    assert "引用単位 ID だけ" in feedback
    assert "start_line" not in feedback
    assert "end_line" not in feedback


def test_scope_links_choose_sent_units_and_code_extracts_original():
    from rag_engine.generation import grounded
    from rag_engine.generation.citation_unit_ids import unit_scope_model
    from rag_engine.models.llm import RequestScopePlan

    span = {"label": "E1", "evidence_id": "E1", "source_id": "p1", "text": "前提です。\n後続です。", "start": 30}
    units = grounded.citation_units(span["text"])
    original = EvidenceUnitSnapshot.create(span, units, grounded.CITATION_UNIT_CONTRACT)
    catalog = SentUnitCatalog([original])
    assert all(uid in grounded.scope_evidence_text(span) for uid in original.unit_ids)
    model = unit_scope_model(RequestScopePlan, catalog)
    payload = {
        "request_coverage": [
            {
                "request_id": "R1",
                "evidence_scope": [
                    {
                        "evidence_id": "E1",
                        "target": "合成の対象",
                        "relation": "linked",
                        "link": {"unit_ids": [original.unit_ids[1]]},
                    }
                ],
            }
        ]
    }
    resolved = grounded.resolve_scope_plan(model.model_validate(payload), [span])
    assert resolved[0].evidence_scope[0].link_quote == "後続です。"
    assert "line_start" not in str(model.model_json_schema())
    assert "line_end" not in str(model.model_json_schema())
    payload["request_coverage"][0]["evidence_scope"][0]["link"] = {"unit_ids": ["unknown"]}
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_repair_keeps_original_snapshot_and_only_replaces_selected_units():
    from rag_engine.generation.citation_range_repair import apply_range_repair, unit_range_repair_model
    from rag_engine.generation.citation_unit_ids import unit_generation_model
    from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model

    original = snapshot()
    foreign = snapshot(source_id="other", evidence_id="E2", label="E2")
    catalog = SentUnitCatalog([original, foreign])
    generation = unit_generation_model(generation_draft_wire_model(), catalog)
    draft = canonical_generation_draft(
        generation.model_validate(
            {
                "items": [{"kind": "rule", "text": "合成の説明", "citations": [{"unit_ids": [original.unit_ids[0]]}]}],
                "summary": "変更しない要約",
                "confidence": "low",
            }
        ),
        unit_catalog=catalog,
    )
    targets = [
        {"item_index": 0, "citation_index": 0, "evidence_id": "E1", "unit_count": 3, "numbered": True, "snapshot_id": original.snapshot_id}
    ]
    model = unit_range_repair_model(catalog)
    assert "line_start" not in str(model.model_json_schema())
    assert "line_end" not in str(model.model_json_schema())
    for selected, reason in [
        ([original.unit_ids[1], original.unit_ids[2]], None),
        ([foreign.unit_ids[1]], "evidence_id_changed"),
        ([original.unit_ids[0], original.unit_ids[2]], "citation_range_disconnected"),
    ]:
        response = model.model_validate({"ranges": [{"item_index": 0, "citation_index": 0, "unit_ids": selected}]})
        repaired, diagnostic = apply_range_repair(draft, response, targets, unit_catalog=catalog)
        assert repaired.items[0].text == draft.items[0].text
        assert repaired.summary == draft.summary
        if reason:
            assert diagnostic["rejected"][0]["reason_code"] == reason
            assert repaired.items[0].citations[0]._unit_binding == draft.items[0].citations[0]._unit_binding
        else:
            assert diagnostic["status"] == "completed"
            assert repaired.items[0].citations[0]._unit_binding.text == "単位B\n単位C"
            assert repaired.items[0].citations[0]._unit_binding.snapshot == original


def test_empty_sent_catalog_cannot_return_any_citation_or_link_range():
    from rag_engine.generation.citation_unit_ids import unit_generation_model
    from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model

    empty = SentUnitCatalog([])
    model = unit_generation_model(generation_draft_wire_model(), empty)
    payload = {"items": [{"kind": "gap", "text": "合成の不足", "citations": []}], "summary": "", "confidence": "low"}
    draft = canonical_generation_draft(model.model_validate(payload), unit_catalog=empty)
    assert draft.items[0].citations == []
    for citation in [{"unit_ids": ["unknown"]}, {"unit_ids": []}, {"evidence_id": "E1", "line_start": 1, "line_end": 1}]:
        payload["items"][0]["citations"] = [citation]
        with pytest.raises(ValidationError):
            model.model_validate(payload)
    assert "line_start" not in str(model.model_json_schema())
    assert "line_end" not in str(model.model_json_schema())


def test_catalog_uses_presented_snapshot_after_input_dictionary_mutation():
    from test_citation_view_snapshots import span

    from rag_engine.generation import grounded

    original = span()
    before = deepcopy(original)
    prompt = grounded.format_functions([original])
    initial = grounded.generation_unit_catalog([original]).snapshots[0]
    original["text"] = "変更された別の本文。"
    original["source_id"] = "other-source"
    original["child_ranges"] = []
    selected = grounded.generation_unit_catalog([original]).snapshots[0]
    assert selected == initial
    assert selected.span() == {**before, "function": list(before["function"]), "source_order": list(before["source_order"])}
    assert all(uid in prompt for uid in selected.unit_ids)
    assert SentUnitCatalog([selected]).bind(selected.unit_ids[:1])[0].text in before["text"]


def test_repair_prompt_decodes_each_snapshot_once(monkeypatch):
    original = snapshot()
    calls = []
    method = EvidenceUnitSnapshot.span

    def counted(self):
        calls.append(self.snapshot_id)
        return method(self)

    monkeypatch.setattr(EvidenceUnitSnapshot, "span", counted)
    record = original.prompt_record()
    assert calls == [original.snapshot_id]
    assert record["snapshot_id"] == original.snapshot_id
    assert record["source"] == "synthetic.txt"
    assert all(uid in record["text"] for uid in original.unit_ids)
    assert "単位A" in record["text"] and "単位C" in record["text"]


def test_annotation_does_not_replace_number_like_content_inside_a_unit():
    from rag_engine.generation import grounded

    text = "原文の最初。\n2| 原文内の番号。\n次の単位。"
    boundary = text.index("次の単位")
    span = {"evidence_id": "E1", "label": "E1", "source_id": "p1", "text": text}
    units = [(0, boundary), (boundary, len(text))]
    snapshot = EvidenceUnitSnapshot.create(span, units, grounded.CITATION_UNIT_CONTRACT)
    rendered = grounded._rendered_evidence_span(span, units=units, numbered=True)
    assert f"1| [{snapshot.unit_ids[0]}] 原文の最初。" in rendered
    assert "\n2| 原文内の番号。" in rendered
    assert f"2| [{snapshot.unit_ids[1]}] 次の単位。" in rendered
