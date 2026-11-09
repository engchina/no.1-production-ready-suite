"""手順の全要件と順序の検査。"""

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedAudit, GroundedCitation, GroundedItem
from rag_engine.retrieval.guide_steps import GuidePlan


def entry():
    return grounded.CheckedItem(
        GroundedItem(kind="operation", text="対象を選択します", citations=[GroundedCitation(evidence_id="", quote="対象を選択します")]),
        span={"text": "対象を選択します", "evidence_id": "E1"},
    )


def audit(*reviews):
    return GroundedAudit(reviews=[], summary_supported=True, goal_alignment="aligned", guide_requirement_reviews=list(reviews))


_STATES = {"addressed": "explained", "partial": "partly_explained", "missing": "not_mentioned"}


def review(key, indexes, status="addressed"):
    """要件の監査の観察（#2800）。status は充足の名前（addressed / partial / missing）か観察の名前。"""
    state = _STATES.get(status, status)
    return {"requirement_id": key, "answer_state": state, "item_indexes": indexes, "reason": "", "answer_quote": "対象を選択します"}


def contract(step, *requirements, dependencies=()):
    return {
        "step_id": step,
        "requirements": [{"id": f"{step}:{i}", "text": text} for i, text in enumerate(requirements)],
        "depends_on": dependencies,
    }


def coverage(*ids):
    return [{"step_id": key, "status": "explained", "required": True} for key in ids]


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


# #2649 の最小再現と同じ形の合成の資料（本文と引用を意図的に違える。#3753 の受入）。
_SHARED_A = "パネルAを開いて設定値を入力します。"
_SHARED_B = "プレビューを選び内容を確認して反映ボタンを押します。"
_SHARED_END = "操作を完了します。"
_SHARED_TEXT = _SHARED_A + _SHARED_B + _SHARED_END
_SHARED_STEPS = (
    ("s1", "パネルAで設定値を入力する", ("run:shared",)),
    ("s2", "プレビューを選び内容を確認して反映ボタンで反映する", ("run:shared",)),
)
_SHARED_TERMS = (("s1", ("パネルA", "設定値", "入力")), ("s2", ("プレビュー", "内容", "確認", "反映ボタン")))
_SHARED_CONTRACTS = (contract("s1", _SHARED_STEPS[0][1]), contract("s2", _SHARED_STEPS[1][1], dependencies=["s1"]))


def _observed(step, state, quote="", indexes=(0,)):
    return {
        "requirement_id": f"{step}:0",
        "answer_state": state,
        "item_indexes": list(indexes) if state == "explained" else [],
        "answer_quote": quote,
        "reason": "合成の観察",
    }


_NOT_MENTIONED = ("not_mentioned", "")


def _statuses_from_audit(texts, reviews):
    """決定論の監査の観察を、充足の確定（apply_guide_contracts）へ渡した手順の状態。"""
    span = {"source_id": "run:shared", "evidence_id": "E1", "label": "E1", "text": _SHARED_TEXT}
    checked = [
        grounded.CheckedItem(
            GroundedItem(
                kind="operation", text=text, applies="matched", citations=[GroundedCitation(evidence_id="E1", quote=_SHARED_TEXT)]
            ),
            span=span,
        )
        for text in texts
    ]
    coverage = grounded.guide_step_coverage(_SHARED_STEPS, checked, [span], (), dict(_SHARED_TERMS))
    result = grounded.apply_guide_contracts(coverage, checked, _SHARED_CONTRACTS, audit(*reviews))
    return [step["status"] for step in result]
