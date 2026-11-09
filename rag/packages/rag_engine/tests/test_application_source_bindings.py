"""推導監査へ、検証済み引用の出典・版・単位を欠落なく渡す。外部I/Oなし。"""

from types import SimpleNamespace

import pytest

from rag_engine.generation import grounded
from rag_engine.generation.citation_unit_ids import unit_generation_model
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import GroundedDraft

TEXT = "機器Aの測定値は28です。"
QUESTION = "機器Aの測定値を教えてください。"


def sources():
    return [
        {
            "evidence_id": f"evidence-{n}",
            "label": f"E{n}",
            "source_id": f"doc-{n}:cs-{n}:chunk-{n}",
            "source": "synthetic.pdf",
            "document_scope": ["synthetic.pdf", f"cs-{n}", f"doc-{n}"],
            "page": 1,
            "text": TEXT,
            "start": 0,
            "end": len(TEXT),
            "function": ("synthetic.pdf", "", "synthetic-sha", "docling", f"cs-{n}", f"doc-{n}", ""),
            "section_path": [],
            "source_order": [1, 1, 0],
        }
        for n in (1, 2)
    ]


def verified(selected=(0,), *, stable_units=True):
    spans = sources()
    grounded.format_functions(spans)
    catalog = grounded.generation_unit_catalog(spans)
    if stable_units:
        model = unit_generation_model(generation_draft_wire_model(), catalog)
        raw = model.model_validate(
            {
                "items": [
                    {
                        "request_id": "Q1",
                        "kind": "rule",
                        "text": TEXT,
                        "citations": [{"unit_ids": [catalog.snapshots[n].unit_ids[0]]} for n in selected],
                    }
                ],
                "request_coverage": [
                    {
                        "request_id": "Q1",
                        "evidence_scope": [
                            {"evidence_id": f"E{n + 1}", "target": "機器A", "relation": "direct", "link": None} for n in selected
                        ],
                    }
                ],
                "summary": "",
                "confidence": "low",
            }
        )
        draft = canonical_generation_draft(raw, unit_catalog=catalog)
    else:
        draft = GroundedDraft.model_validate(
            {
                "items": [
                    {
                        "request_id": "Q1",
                        "kind": "rule",
                        "text": TEXT,
                        "citations": [{"evidence_id": f"E{n + 1}", "lines": "1"} for n in selected],
                    }
                ],
                "request_coverage": [{"request_id": "Q1", "evidence_ids": [f"E{n + 1}" for n in selected]}],
                "summary": "",
                "confidence": "low",
            }
        )
    entries, dropped = grounded.verify(QUESTION, draft, spans)
    assert len(entries) == 1 and not dropped
    return entries[0], spans, catalog


def captured(entry, spans):
    """監査へ渡す引用の結び付き（`application_item_citations` の形）。"""
    return {"items": [{"citation_bindings": application_item_citations(entry)}]}


def test_different_verified_selection_cannot_have_identical_audit_input():
    left, spans, _ = verified((0,))
    right, _, _ = verified((1,))
    assert left.item.citations[0]._unit_binding.unit_ids != right.item.citations[0]._unit_binding.unit_ids
    assert captured(left, spans) != captured(right, spans)


@pytest.mark.parametrize("selected", [0, 1])
def test_selected_unit_and_document_version_are_kept(selected):
    entry, spans, catalog = verified((selected,))
    row = captured(entry, spans)["items"][0]["citation_bindings"][0]
    assert row["source_id"] == spans[selected]["source_id"]
    assert row["document_scope"] == spans[selected]["document_scope"]
    assert row["unit_ids"] == [catalog.snapshots[selected].unit_ids[0]]
    assert row["char_start"] == 0 and row["char_end"] == len(TEXT)


def test_also_keeps_both_sources_without_filename_or_text_deduplication():
    entry, spans, _ = verified((0, 1))
    assert len(entry.also) == 1
    rows = captured(entry, spans)["items"][0]["citation_bindings"]
    assert [row["source_id"] for row in rows] == [span["source_id"] for span in spans]
    assert rows[0]["unit_ids"] != rows[1]["unit_ids"]


def test_legacy_validated_range_has_identity_but_no_invented_unit_ids():
    entry, spans, _ = verified((0,), stable_units=False)
    assert entry.item.citations[0]._unit_binding is None
    row = captured(entry, spans)["items"][0]["citation_bindings"][0]
    assert row["source_id"] == spans[0]["source_id"]
    assert row["unit_ids"] == []
    assert row["char_start"] == 0 and row["char_end"] == len(TEXT)


@pytest.mark.parametrize("forgery", ["public_proof", "private_binding"])
def test_unverified_or_forged_identity_does_not_get_a_trusted_join(forgery):
    entry, spans, _ = verified((0,))
    citation = entry.item.citations[0]
    if forgery == "public_proof":
        citation._extraction = {}
        entry.citation_resolution = {"status": "extracted", "authenticity": "verified", "source_id": "forged"}
    else:
        citation._unit_binding = SimpleNamespace(unit_ids=("Uforged",), snapshot=citation._unit_binding.snapshot)
    assert captured(entry, spans)["items"][0]["citation_bindings"] == []
