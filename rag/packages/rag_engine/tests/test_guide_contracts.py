"""手順の全要件と順序の検査。"""

import pytest

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedAudit, GroundedItem
from rag_engine.retrieval.guide_steps import GuidePlan


def entry():
    return grounded.CheckedItem(
        GroundedItem(kind="operation", text="対象を選択します", quote="対象を選択します"),
        span={"text": "対象を選択します", "evidence_id": "E1"},
    )


def audit(*reviews):
    return GroundedAudit(reviews=[], summary_supported=True, goal_alignment="aligned", guide_requirement_reviews=list(reviews))


def review(key, indexes, status="addressed"):
    return {"requirement_id": key, "status": status, "item_indexes": indexes, "reason": "", "answer_quote": "対象を選択します"}


def contract(step, *requirements, dependencies=()):
    return {
        "step_id": step,
        "requirements": [{"id": f"{step}:{i}", "text": text} for i, text in enumerate(requirements)],
        "depends_on": dependencies,
    }


def coverage(*ids):
    return [{"step_id": key, "status": "explained", "required": True} for key in ids]


def test_all_requirements_required_even_when_primary_operation_is_explained():
    result = grounded.apply_guide_contracts(
        coverage("a"), [entry()], [contract("a", "対象選択", "保存して確定")], audit(review("a:0", [0]), review("a:1", [], "missing"))
    )
    assert result[0]["status"] == "unused"
    assert result[0]["requirements"][1]["status"] == "missing"


@pytest.mark.parametrize(
    "reviews", [[], [review("a:0", [0]), review("a:0", [0])], [review("a:0", [-1])], [review("a:0", [20])], [review("other", [0])]]
)
def test_omitted_duplicate_and_invalid_reviews_cannot_complete_requirement(reviews):
    result = grounded.apply_guide_contracts(coverage("a"), [entry()], [contract("a", "対象選択")], audit(*reviews))
    assert result[0]["status"] != "explained"


@pytest.mark.parametrize("flag", ["marked", "added"])
def test_unverified_and_system_added_quote_do_not_complete_requirement(flag):
    item = entry()
    if flag == "marked":
        item.mark("unassessed", "未確認")
    else:
        item.added = True
    result = grounded.apply_guide_contracts(coverage("a"), [item], [contract("a", "対象選択")], audit(review("a:0", [0])))
    assert result[0]["status"] != "explained"


def test_dependency_order_is_checked():
    contracts = [contract("open", "画面を開く"), contract("edit", "値を変更", dependencies=["open"])]
    result = grounded.apply_guide_contracts(
        coverage("open", "edit"), [entry(), entry()], contracts, audit(review("open:0", [1]), review("edit:0", [0]))
    )
    assert result[1]["status"] == "unused"
    assert result[1]["order_errors"] == ["open"]
    result = grounded.apply_guide_contracts(
        coverage("open", "edit"), [entry(), entry()], contracts, audit(review("open:0", [0]), review("edit:0", [1]))
    )
    assert all(step["status"] == "explained" for step in result)


def test_all_requirements_can_use_several_supported_items():
    result = grounded.apply_guide_contracts(
        coverage("a"), [entry(), entry()], [contract("a", "対象選択", "保存して確定")], audit(review("a:0", [0]), review("a:1", [1]))
    )
    assert result[0]["status"] == "explained"


def test_plan_preserves_configured_requirements_and_dependencies():
    plan = GuidePlan.from_payload(
        "guide",
        1,
        [{"id": "a", "purpose": "値を編集", "evidence_requirements": ["入力方法"], "done_when": "保存済み", "depends_on": ["open"]}],
    )
    assert plan is not None
    assert [r["text"] for r in plan.step_contracts[0]["requirements"]] == ["入力方法", "保存済み"]
    assert [r["kind"] for r in plan.step_contracts[0]["requirements"]] == ["evidence", "completion"]
    assert plan.step_contracts[0]["depends_on"] == ["open"]
    assert plan.step_contracts[0]["purpose"] == "値を編集"


def test_missing_requirement_is_reported_in_answer_without_asserting_it_as_fact():
    result = grounded.apply_guide_contracts(
        coverage("a"), [entry()], [contract("a", "対象選択", "保存して確定")], audit(review("a:0", [0]), review("a:1", [], "missing"))
    )
    result[0]["title"] = "編集"
    assert "保存して確定" in grounded.guide_step_gap_line(result[0])
    assert "確かめられていない" in grounded.guide_step_gap_line(result[0])


def test_requirement_to_explain_unknown_scope_can_use_an_explicit_gap():
    gap = grounded.CheckedItem(GroundedItem(kind="gap", text="列の定義を確認できません。"))
    result = grounded.apply_guide_contracts(
        coverage("a"),
        [gap],
        [contract("a", "列が未確認ならその範囲を明示する")],
        audit({**review("a:0", [0]), "answer_quote": "列の定義を確認できません。"}),
    )
    assert result[0]["status"] == "explained"


@pytest.mark.parametrize("quote", ["", "資料にあるので案内できます", "別の操作を説明します"])
def test_review_requires_a_quote_that_actually_exists_in_answer(quote):
    result = grounded.apply_guide_contracts(
        coverage("a"), [entry()], [contract("a", "対象選択")], audit({**review("a:0", [0]), "answer_quote": quote})
    )
    assert result[0]["status"] != "explained"


def test_one_answer_item_cannot_complete_two_distinct_steps():
    result = grounded.apply_guide_contracts(
        coverage("a", "b"), [entry()], [contract("a", "対象選択"), contract("b", "保存確定")], audit(review("a:0", [0]), review("b:0", [0]))
    )
    assert [step["status"] for step in result] == ["explained", "unused"]


def test_generation_receives_requirements_and_missing_audit_rows_are_retried():
    from dataclasses import replace

    from test_grounded_answer import FakeModel, draft, item
    from test_grounded_answer import audit as make_audit
    from test_guide_step_fulfillment import GUIDE, STEP_A, STEP_B, _ab_context, _audits, _drafts, _ids, _knowledge, _run

    from rag_engine.models.llm import GuideRequirementReview

    ctx = _ab_context()
    knowledge = replace(_knowledge(GUIDE), guide_step_contracts=(contract("s1", STEP_A), contract("s2", STEP_B, dependencies=["s1"])))
    labels = _ids(ctx, knowledge)
    incomplete = make_audit((0, "supported", "matched", ""), (1, "supported", "matched", ""), requests=[("Q1", "addressed")])
    complete = {
        **incomplete,
        "guide_requirement_reviews": [
            GuideRequirementReview(requirement_id="s1:0", status="addressed", item_indexes=[0], answer_quote=STEP_A, reason=""),
            GuideRequirementReview(requirement_id="s2:0", status="addressed", item_indexes=[1], answer_quote=STEP_B, reason=""),
        ],
    }
    model = FakeModel([draft(item(labels[STEP_A], STEP_A, STEP_A), item(labels[STEP_B], STEP_B, STEP_B))], [incomplete, complete])
    result = _run(model, ctx, knowledge).response
    assert "guide_contracts" in _drafts(model)[0]
    assert _audits(model)[1]["validation_error"]["required_once"] == ["s1:0", "s2:0"]
    assert all(step["status"] == "explained" for step in result.generation_trace["rounds"][0]["guide_steps"])
    assert len(_audits(model)) == 2


@pytest.mark.parametrize("supported", [True, False])
def test_evidence_condition_checks_support_without_requiring_its_instruction_in_answer(supported):
    plan = GuidePlan.from_payload(
        "guide",
        1,
        [{"id": "a", "evidence_requirements": ["対象の項目名と入力先が同じ行にあること"], "done_when": "対象を選択する操作を説明する"}],
    )
    assert plan is not None
    item = entry()
    if not supported:
        item.mark("unassessed", "引用元の行を確認できない")
    result = grounded.apply_guide_contracts(coverage("a"), [item], plan.step_contracts, audit(review("a:0", [0]), review("a:1", [0])))
    assert (result[0]["status"] == "explained") is supported
    assert "同じ行" not in item.item.text
