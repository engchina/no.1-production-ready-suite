"""引用の候補対応と、実際の本文の意味的な全充足を混同しない。"""

import pytest

from rag_engine.generation import grounded
from rag_engine.generation.answer_envelope import guide_step_requests
from rag_engine.models.llm import GroundedAudit, GroundedCitation, GroundedItem

FIRST = "パネルAを開いて設定値を入力します。"
SECOND = "プレビューを選び内容を確認して反映ボタンを押します。"
SPAN = {"source_id": "sample", "evidence_id": "e1", "label": "E1", "text": FIRST + SECOND}
STEPS = (("s1", FIRST, ("sample",)), ("s2", SECOND, ("sample",)))
CONTRACTS = [
    {"step_id": "s1", "requirements": [{"id": "r1", "text": FIRST, "kind": "completion"}]},
    {"step_id": "s2", "depends_on": ["s1"], "requirements": [{"id": "r2", "text": SECOND, "kind": "completion"}]},
]


def item(text, quote=FIRST + SECOND):
    return grounded.CheckedItem(
        GroundedItem(kind="operation", text=text, applies="matched", citations=[GroundedCitation(evidence_id="e1", quote=quote)]), span=SPAN
    )


def observation(requirement, indexes, text, state="explained"):
    return {"requirement_id": requirement, "item_indexes": indexes, "answer_quote": text, "answer_state": state, "reason": "合成の同次観察"}


def audit(*observations):
    return GroundedAudit(reviews=[], guide_requirement_reviews=list(observations), summary_supported=True, goal_alignment="aligned")


def coverage(items):
    return grounded.guide_step_coverage(
        STEPS, items, [SPAN], step_terms={"s1": ("パネルA", "設定値", "入力"), "s2": ("プレビュー", "内容", "確認", "反映ボタン")}
    )


@pytest.mark.parametrize("text", [FIRST, FIRST + SECOND, "操作を完了します。"])
def test_source_or_quote_anchors_do_not_prove_unreviewed_body_completeness(text):
    result = coverage([item(text)])
    assert [row["status"] for row in result] == ["unknown", "unknown"]
    assert all(row["evidence_flow"]["context_evidence_ids"] == ["e1"] for row in result)
    assert all(row["validation"]["body_coverage"]["execution"] == "not_run" for row in result)
    assert [row["status"] for row in guide_step_requests(result)] == ["unknown", "unknown"]


def test_body_proofs_can_share_one_item_and_preserve_correct_operation_order():
    items = [item(FIRST + SECOND)]
    reviews = audit(observation("r1", [0], FIRST), observation("r2", [0], SECOND))
    result = grounded.apply_guide_contracts(coverage(items), items, CONTRACTS, reviews)
    assert [row["status"] for row in result] == ["explained", "explained"]
    assert result[0]["requirements"][0]["body_spans"][0]["start"] == 0
    assert result[1]["requirements"][0]["body_spans"][0]["start"] == len(FIRST)
    assert items[0].item.text == FIRST + SECOND


def test_source_only_requirement_quote_does_not_complete_omitted_body():
    items = [item(FIRST)]
    result = grounded.apply_guide_contracts(
        coverage(items), items, CONTRACTS, audit(observation("r1", [0], FIRST), observation("r2", [0], SECOND))
    )
    assert result[0]["status"] == "explained"
    assert result[1]["status"] == "unknown"
    assert result[1]["requirements"][0]["body_spans"] == []
    assert items[0].item.text == FIRST


def test_same_item_reversed_operations_are_not_complete():
    items = [item(SECOND + FIRST)]
    result = grounded.apply_guide_contracts(
        coverage(items), items, CONTRACTS, audit(observation("r1", [0], FIRST), observation("r2", [0], SECOND))
    )
    assert result[0]["status"] == "explained"
    assert result[1]["status"] == "partial" and result[1]["order_errors"] == ["s1"]
    assert items[0].item.text == SECOND + FIRST


def test_ambiguous_body_positions_do_not_claim_dependency_order_verified():
    items = [item(FIRST + SECOND + FIRST)]
    result = grounded.apply_guide_contracts(
        coverage(items), items, CONTRACTS, audit(observation("r1", [0], FIRST), observation("r2", [0], SECOND))
    )
    assert result[1]["status"] == "unknown"
    assert result[1]["validation"]["body_coverage"]["reason"] == "operation_order_unverified"


def test_multiple_items_jointly_prove_one_step_without_exclusive_assignment():
    left, right = "パネルAを開いて", "設定値を入力します。"
    items = [item(left, left), item(right, right), item(SECOND, SECOND)]
    result = grounded.apply_guide_contracts(
        coverage(items), items, CONTRACTS, audit(observation("r1", [0, 1], left + "\n" + right), observation("r2", [2], SECOND))
    )
    assert [row["status"] for row in result] == ["explained", "explained"]
    assert {row["item_index"] for row in result[0]["requirements"][0]["body_spans"]} == {0, 1}


@pytest.mark.parametrize("execution", ["not_run", "error", "timed_out"])
def test_unperformed_or_failed_semantic_review_preserves_body_and_known_sources(execution):
    items = [item(FIRST + SECOND)]
    result = grounded.apply_guide_contracts(coverage(items), items, CONTRACTS, None, review_execution=execution)
    assert [row["status"] for row in result] == ["unknown", "unknown"]
    assert all(row["validation"]["body_coverage"]["execution"] == execution for row in result)
    assert items[0].published and items[0].item.text == FIRST + SECOND
    assert all(row["status"] == "unknown" for row in guide_step_requests(result))
