"""回答漏れ・材料不足・未検証の補救経路を決定論的に確かめる。"""

import pytest

from rag_engine.generation.request_recovery import request_recovery_plan


@pytest.mark.parametrize(
    ("evidence_status", "refs", "expected"),
    [
        ("available", ["E1"], ("answer_omission", "correction")),
        ("available", [], ("unverified_evidence", "retain")),
        ("available", ["E1", "unknown"], ("unverified_evidence", "retain")),
        ("missing", [], ("evidence_missing", "retrieval")),
        ("unknown", [], ("unverified_evidence", "retain")),
    ],
)
def test_reviewed_material_status_selects_the_remedy(evidence_status, refs, expected):
    rows = [{"request_id": "Q1", "status": "partial", "reviewed": True, "evidence_status": evidence_status, "evidence_ids": refs}]
    plan = request_recovery_plan(rows, (), {"E1"})
    assert len(plan) == 1
    assert (plan[0]["cause"], plan[0]["action"]) == expected
    assert plan[0]["evidence_ids"] == (refs if expected[1] == "correction" else [])


def test_unexecuted_review_is_not_a_missing_material_verdict():
    assert request_recovery_plan([{"request_id": "Q1", "status": "missing", "reviewed": False}], (), set()) == []


def test_explicit_generated_gap_can_retrieve_without_an_audit():
    plan = request_recovery_plan([], ["Q1", "Q1"], set())
    assert [(row["request_id"], row["action"]) for row in plan] == [("Q1", "retrieval")]


def test_available_material_overrides_generated_gap_without_searching_again():
    rows = [{"request_id": "Q1", "status": "missing", "reviewed": True, "evidence_status": "available", "evidence_ids": ["E1", "E1"]}]
    plan = request_recovery_plan(rows, ["Q1"], {"E1"})
    assert plan[0]["action"] == "correction"
    assert plan[0]["evidence_ids"] == ["E1"]
