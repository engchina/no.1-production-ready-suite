"""複合要求の各要点の出力参照を検証し、意味支持と区別する。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from rag_engine.generation.answer_parts import check_answer_parts
from rag_engine.generation.grounded import CheckedItem
from rag_engine.models.llm import AnswerPart, GroundedApplication, GroundedDraft, GroundedItem


def case():
    fact = GroundedItem(kind="rule", request_id="Q1", text="資料の事実です。")
    item = CheckedItem(fact, span={"evidence_id": "E1"}, draft_index=0)
    application = GroundedApplication(request_id="Q1", item_indexes=[0], reasoning="資料から導きます。", result="導出結果です。")
    draft = GroundedDraft(
        items=[fact],
        applications=[application],
        summary="",
        confidence="medium",
        answer_parts=[
            AnswerPart(id="P1", request_id="Q1", text="事実", result_kind="direct", item_indexes=[0]),
            AnswerPart(id="P2", request_id="Q1", text="導出", result_kind="derived", application_indexes=[0]),
        ],
    )
    return draft, {0: item}, [SimpleNamespace(application=application, published=True)]


def test_each_compound_part_needs_its_own_output():
    draft, items, applications = case()
    assert check_answer_parts(draft, items, applications, {"Q1"})["status"] == "linked"
    result = check_answer_parts(draft, items, [], {"Q1"})
    assert result["status"] == "incomplete"
    assert [row["status"] for row in result["parts"]] == ["present", "missing"]


@pytest.mark.parametrize("change", ["kind", "request", "duplicate", "range", "rejected"])
def test_wrong_role_request_duplicate_and_removed_output_do_not_complete(change):
    draft, items, applications = case()
    if change == "kind":
        draft.answer_parts[1].result_kind = "direct"
    elif change == "request":
        draft.answer_parts[1].request_id = "unknown"
    elif change == "duplicate":
        draft.answer_parts[1].id = "P1"
    elif change == "range":
        draft.answer_parts[1].application_indexes = [5]
    else:
        applications[0].published = False
    assert check_answer_parts(draft, items, applications, {"Q1"})["status"] == "incomplete"


def test_missing_plan_is_unrecorded_instead_of_success_or_semantic_failure():
    draft, items, applications = case()
    draft.answer_parts = []
    result = check_answer_parts(draft, items, applications, {"Q1"})
    assert result == {"status": "unrecorded", "parts": [], "unrecorded_request_ids": ["Q1"]}


def test_verified_gap_keeps_index_so_the_declared_part_is_not_duplicated():
    from rag_engine.generation.grounded import verify

    gap = GroundedItem(kind="gap", request_id="Q1", text="利用時間を確認してください。")
    draft = GroundedDraft(
        items=[gap],
        summary="",
        confidence="low",
        answer_parts=[
            AnswerPart(id="P1", request_id="Q1", text="利用時間", result_kind="gap", item_indexes=[0]),
        ],
    )
    checked, _ = verify("料金を教えてください。", draft, [])
    assert checked[0].draft_index == 0
    result = check_answer_parts(draft, {entry.draft_index: entry for entry in checked}, [], {"Q1"})
    assert result["parts"][0]["status"] == "present"
