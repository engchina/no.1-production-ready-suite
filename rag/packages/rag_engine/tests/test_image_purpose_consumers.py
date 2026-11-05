"""画像用途を推導校正と引用修復へ、送達済みUと同版で渡す。"""

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest

from rag_engine.chunking.text_projection import IMAGE_TEXT_SOURCE_ROLES
from rag_engine.generation.application_review import application_documents
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.grounded import evidence_citation_units, evidence_unit_contract


def mixed_span() -> dict[str, Any]:
    parts = ["記録値は42です。", "例示値は900です。", "用途未記録の値は17です。"]
    text = "\n".join(parts)
    rows = []
    offset = 0
    for index, (part, purpose) in enumerate(zip(parts, ("record", "example", "unknown"), strict=True)):
        rows.append(
            {
                "start": offset,
                "end": offset + len(part),
                "role": sorted(IMAGE_TEXT_SOURCE_ROLES)[0],
                "image_id": f"private-image-{index}",
                "source_record_id": f"private-picture-{index}",
                "vision_image_purpose": purpose,
            }
        )
        offset += len(part) + 1
    return {
        "evidence_id": "E1",
        "label": "E1",
        "source_id": "source-a",
        "source": "synthetic.txt",
        "document_scope": ["synthetic-doc", "v1"],
        "text": text,
        "image_text_source_context": {
            "version": 1,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "scope": {"document_id": "private-doc", "recipe_id": "private-recipe", "chunk_set_id": "private-set"},
            "completeness": "partial",
            "source_signature": "private-signature",
            "ranges": rows,
            "native_text_ranges": [],
        },
    }


def snapshot(span: dict[str, Any]) -> EvidenceUnitSnapshot:
    return EvidenceUnitSnapshot.create(span, evidence_citation_units(span), evidence_unit_contract(span))


def test_repair_keeps_each_purpose_bound_to_its_own_unit_without_internal_map() -> None:
    original = snapshot(mixed_span())
    record = original.prompt_record()
    assert {row["purpose"] for row in record["image_purposes"]} == {"record", "example", "unknown"}
    catalog = SentUnitCatalog([original])
    for row in record["image_purposes"]:
        assert all(uid in record["text"] for uid in row["unit_ids"])
        bound = catalog.bind(row["unit_ids"])
        assert len(bound) == 1
        assert {"record": "42", "example": "900", "unknown": "17"}[row["purpose"]] in bound[0].text
    wire = json.dumps(record, ensure_ascii=False)
    assert "private-" not in wire
    assert "text_sha256" not in wire
    assert "image_text_source_context" not in wire


def test_application_originals_and_repair_share_same_purpose_projection() -> None:
    span = mixed_span()
    original = deepcopy(span)
    repair = snapshot(span).prompt_record()
    value = application_documents([span])[0]["evidence"][0]
    assert value["image_purposes"] == repair["image_purposes"]
    for row in value["image_purposes"]:
        assert all(uid in value["text"] for uid in row["unit_ids"])
    assert all(part in value["text"] for part in span["text"].splitlines())
    assert span == original
    assert "private-" not in json.dumps(value, ensure_ascii=False)


def test_adjacent_binding_does_not_erase_original_purpose_traceability() -> None:
    original = snapshot(mixed_span())
    bound = SentUnitCatalog([original]).bind(original.unit_ids)
    assert len(bound) == 1
    assert bound[0].unit_ids == original.unit_ids
    record = bound[0].snapshot.prompt_record()
    assert {row["purpose"] for row in record["image_purposes"]} == {"record", "example", "unknown"}


@pytest.mark.parametrize("bad", [None, {}, {"version": 1}, "forged"])
def test_missing_or_invalid_context_never_invents_image_purpose(bad: Any) -> None:
    span = mixed_span()
    span["image_text_source_context"] = bad
    assert "image_purposes" not in snapshot(span).prompt_record()
    assert "image_purposes" not in application_documents([span])[0]["evidence"][0]


def test_native_and_document_background_keep_existing_wire() -> None:
    span = mixed_span()
    span.pop("image_text_source_context")
    record = snapshot(span).prompt_record()
    assert set(record) == {"snapshot_id", "source", "text"}
    assert application_documents([span])[0]["evidence"][0]["text"] == span["text"]
    span = {**mixed_span(), "document_background_only": True, "background_ref": {"truncated": True}}
    value = application_documents([span])[0]["evidence"][0]
    assert "image_purposes" not in value
    assert value["document_background_only"] is True


def test_existing_application_call_adds_shared_policy_only_for_image_units() -> None:
    from types import SimpleNamespace

    from rag_engine.config import get_settings
    from rag_engine.generation.application_review import review_applications
    from rag_engine.generation.applications import APPLICATION_AUDIT_SYSTEM_PROMPT, CheckedApplication
    from rag_engine.generation.image_text_sources import IMAGE_PURPOSE_POLICY
    from rag_engine.models.llm import ApplicationAudit, GroundedApplication

    observed: list[tuple[str, dict[str, Any]]] = []

    def parse(system: str, prompt: str, settings: Any, schema: type[ApplicationAudit], **options: Any) -> ApplicationAudit:
        observed.append((system, json.loads(prompt)))
        return schema(application_reviews=[{"index": 0, "valid": True, "reason": "合成の受渡し確認"}], summary_supported=True)

    for image in (False, True):
        span = mixed_span()
        if not image:
            span.pop("image_text_source_context")
        quote = span["text"].splitlines()[0]
        item = SimpleNamespace(
            item=SimpleNamespace(kind="rule", applies="matched", text=quote, quote=quote),
            span=span,
            quote_only=False,
            removed=False,
            state="",
        )
        application = CheckedApplication(
            GroundedApplication(request_id="Q1", item_indexes=[0], reasoning="資料の記録に基づく。", result="42"),
            [item],
        )
        diagnostic = review_applications(
            "資料の記録を教えてください。",
            [application],
            evidence_spans=[span],
            existing_audit=None,
            audit_requested=False,
            parse_text=parse,
            settings=get_settings(environ={}, dotenv_path=None),
            provider_id=None,
        )
        assert diagnostic["calls"] == 1
    assert observed[0][0] == APPLICATION_AUDIT_SYSTEM_PROMPT
    assert IMAGE_PURPOSE_POLICY not in observed[0][0]
    assert observed[1][0].count(IMAGE_PURPOSE_POLICY) == 1
    assert observed[1][1]["documents"][0]["evidence"][0]["image_purposes"]
