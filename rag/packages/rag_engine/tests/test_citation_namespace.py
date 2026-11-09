"""Guide識別子は要求のIDであり、文書の事実根拠IDにしない。"""

import pytest
from pydantic import ValidationError

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem


def check(identity: str, *, guide_ids: tuple[str, ...] = ("guide-step-a",)):
    span = {
        "evidence_id": "stable-a",
        "label": "E1",
        "source_id": "p-a",
        "source": "synthetic.pdf",
        "function": ("synthetic.pdf", "合成の操作"),
        "text": "空条件でも上限件数まで処理します。",
    }
    grounded.format_functions([span])
    citation = GroundedCitation(evidence_id=identity, lines="1")
    citation._wire_range = (1, 1)
    draft = GroundedDraft(
        items=[GroundedItem(kind="operation", text="合成の操作を行います。", citations=[citation])], summary="", confidence="low"
    )
    checked, notes = grounded.verify("操作方法は？", draft, [span], guide_ids=guide_ids)
    return checked[0], notes


def test_legacy_free_quote_does_not_turn_guide_identity_into_a_fact_source():
    item = GroundedItem(
        kind="operation", text="合成の操作", citations=[GroundedCitation(evidence_id="guide-step-a", quote="対象を選択します。")]
    )
    span = {"evidence_id": "stable-a", "label": "E1", "text": "対象を選択します。"}
    extracted, error = grounded.cited_quote(item, [span], guide_ids=("guide-step-a",))
    assert error and not extracted.quote
    assert extracted.citations[0]._extraction["reason_code"] == "citation_id_namespace"
