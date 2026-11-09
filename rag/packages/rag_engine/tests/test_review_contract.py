"""原判定・適用方針・引用修復を区別する契約。実モデルは呼ばない。"""

import pytest

from rag_engine.generation.grounded import CheckedItem, answer_use, verify
from rag_engine.models.llm import GroundedAudit, GroundedDraft, GroundedItem, GroundedItemReview


def span(text: str) -> dict:
    return {"evidence_id": "e1", "label": "E1", "text": text, "source_id": "c1", "source": "source.pdf", "page": 1, "tags": []}


def audit(applicability: str, support: str = "supported") -> GroundedAudit:
    return GroundedAudit.model_validate(
        {
            "reviews": [
                {"index": 0, "support": support, "applicability": applicability, "reason": "判定理由", "condition": "条件Aが成立するとき"}
            ],
            "summary_supported": True,
            "goal_alignment": "aligned",
        }
    )


def test_policy_is_not_part_of_the_model_response_contract() -> None:
    properties = GroundedItemReview.model_json_schema()["properties"]
    assert not {"applicability_action", "observed_applicability", "applicability_policy_reason"} & properties.keys()


def test_generation_audit_schema_has_no_unread_copy_fields() -> None:
    """本番に読み手の無い欄を、モデルの応答の schema に戻さない（#4152）。"""
    properties = GroundedItemReview.model_json_schema()["properties"]
    assert not {"support_quote", "supported_fragments"} & properties.keys()

