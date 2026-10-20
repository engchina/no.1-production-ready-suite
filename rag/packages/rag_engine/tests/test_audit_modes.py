"""回答の監査の型（#2795）。standard は事実の信頼性だけを監査し、作り直しは事実の問題の 1 組まで。

完全性の不足（答えていない要求・未使用の根拠・説明していない手順）では作り直さず、本文の不足に示す。detailed は
業務ガイドの要件ごとの充足と未使用の根拠も監査する（従来）。誤った内容（根拠と矛盾・根拠に無い・引用の不一致）の
検出は standard でも変えない。
"""

from __future__ import annotations

import json
from dataclasses import replace

from test_grounded_answer import LIST_TEXT, FakeModel, audit, context, draft, ids, item, marks, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.models.llm import GroundedAudit, GroundedFactAudit

STEP = "一覧表を選択し、処理を選択します。"


def _run(model, ctx, mode, **overrides):
    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    settings = replace(get_settings(), answer_audit_mode=mode, **overrides)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(
            "年次集計の明細一覧表を出力する手順を教えてください", ctx, settings, image_prompt_mode="text_only"
        )


def _fact(value):
    """事実の監査の応答（standard の schema に無い項目を除く）。"""
    return {key: value[key] for key in value if key in GroundedFactAudit.model_fields}


def _audit_inputs(model):
    return [json.loads(prompt) for name, prompt in model.prompts if name in {"GroundedAudit", "GroundedFactAudit"}]


def _drafts(model):
    return [prompt for name, prompt in model.prompts if name == "GroundedDraft"]


def test_standard_prompt_drops_only_the_completeness_rules():
    detailed = grounded.audit_system_prompt()
    standard = grounded.audit_system_prompt(detailed=False)
    assert detailed == grounded.AUDIT_SYSTEM_PROMPT
    removed = [line for line in detailed.splitlines() if line not in standard.splitlines()]
    assert removed and all(line.startswith(grounded._DETAILED_AUDIT_LINES) for line in removed)
    # 事実の信頼性の規則（支持・矛盾・適用・要約・目的）は残す。
    for kept in ("3.1 support", "contradicted", "3.2 applicability", "summary_supported", "goal_alignment", "request_reviews"):
        assert kept in standard
    assert "unused_evidence" not in standard and "guide_requirement_reviews" not in standard


def test_standard_does_not_regenerate_for_an_unanswered_request_only():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    eid = ids(ctx)[LIST_TEXT]
    response = audit((0, "supported", "matched", ""), requests=[("Q1", "partial", "一部だけ")], unused=["E1"])

    standard = FakeModel([draft(item(eid, STEP, STEP))], [_fact(response)])
    result = _run(standard, ctx, grounded.AUDIT_MODE_STANDARD).response
    assert len(_drafts(standard)) == 1
    inputs = _audit_inputs(standard)[0]
    assert "unused_evidence" not in inputs and "guide_contracts" not in inputs
    assert [name for name, _prompt in standard.prompts if name != "GroundedDraft"] == ["GroundedFactAudit"]
    assert result.generation_trace["audit_mode"] == "standard"
    # 不足は本文に残る（答えた範囲は示す）。
    assert STEP in result.answer_text

    detailed = FakeModel([draft(item(eid, STEP, STEP))], [response])
    _run(detailed, ctx, grounded.AUDIT_MODE_DETAILED)
    assert len(_drafts(detailed)) > 1
    assert "unused_evidence" in _audit_inputs(detailed)[0]


def test_standard_still_corrects_and_marks_a_claim_without_support_once():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    eid = ids(ctx)[LIST_TEXT]
    wrong = "一覧表を選択し、自動で集計が完了します。"
    model = FakeModel([draft(item(eid, wrong, STEP))], [_fact(audit((0, "unsupported", "matched", "")))])

    result = _run(model, ctx, grounded.AUDIT_MODE_STANDARD, answer_max_correction_rounds=2).response

    # 作り直しは 1 組まで（1 組目と合わせて 2 回）。根拠に無い説明の印は standard でも付く。
    assert len(_drafts(model)) == grounded.STANDARD_MAX_ROUNDS
    assert marks(result)[wrong] == ["unsupported"]


def test_standard_removes_a_contradicted_claim():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    eid = ids(ctx)[LIST_TEXT]
    wrong = "一覧表は選択できません。"
    model = FakeModel([draft(item(eid, wrong, STEP))], [_fact(audit((0, "contradicted", "matched", "")))])

    result = _run(model, ctx, grounded.AUDIT_MODE_STANDARD).response

    assert wrong not in result.answer_text.split(grounded.answer_labels().removed_section)[0]
    assert [passage["text"] for passage in result.envelope["removed_passages"]] == [wrong]


def test_correction_feedback_keeps_factual_problems_only():
    feedback = {
        "dropped_items": [{"evidence_id": "E1"}],
        "unanswered_requests": [{"request_id": "Q1"}],
        "unused_evidence_ids": [{"evidence_id": "E2"}],
        "guide_steps_unexplained": [{"step_id": "s1"}],
        "goal_alignment": "partial",
        "supported_items": [{"text": "t"}],
    }
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_DETAILED) == feedback
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_STANDARD) == {
        "dropped_items": [{"evidence_id": "E1"}],
        "supported_items": [{"text": "t"}],
    }
    assert grounded.correction_feedback({**feedback, "dropped_items": [], "goal_alignment": "off_target"}, "standard") == {
        "goal_alignment": "off_target",
        "supported_items": [{"text": "t"}],
    }
    # 完全性の不足だけなら作り直さない（指摘は空）。
    assert grounded.correction_feedback({"unanswered_requests": [{"request_id": "Q1"}], "supported_items": []}, "standard") == {}


def test_fact_audit_is_read_as_a_full_audit_with_empty_completeness_fields():
    fact = GroundedFactAudit.model_validate(_fact(audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")])))
    full = GroundedAudit.model_validate(fact.model_dump())
    assert full.unused_evidence_ids == [] and full.guide_requirement_reviews == []
    assert full.reviews[0].support == "supported"


def test_failed_correction_round_is_an_answer_warning():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    eid = ids(ctx)[LIST_TEXT]
    wrong = "一覧表を選択し、自動で集計が完了します。"
    calls = {"draft": 0}

    class FailingSecondDraft(FakeModel):
        def __call__(self, system, prompt, settings, schema, **options):
            if schema.__name__ == "GroundedDraft":
                calls["draft"] += 1
                if calls["draft"] == 2:
                    raise RuntimeError("upstream")
            return super().__call__(system, prompt, settings, schema, **options)

    model = FailingSecondDraft([draft(item(eid, wrong, STEP))], [_fact(audit((0, "unsupported", "matched", "")))])
    result = _run(model, ctx, grounded.AUDIT_MODE_STANDARD).response

    assert [(warning["code"], warning["detail"]) for warning in result.envelope["answer_warnings"]] == [
        ("correction_failed", "RuntimeError")
    ]
    assert wrong in result.answer_text
