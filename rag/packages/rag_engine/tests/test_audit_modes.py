"""回答の監査の型（#2795）。standard は未使用の根拠を列挙させず、作り直しは事実の問題の 1 組まで。

完全性の不足（答えていない要求・未使用の根拠・説明していない手順）では作り直さず、本文の不足に示す（要求・業務ガイドの
要件の充足は同じ 1 回の監査で判定する）。detailed は未使用の根拠も列挙させ、完全性の不足でも作り直す（従来）。誤った
内容（根拠と矛盾・根拠に無い・引用の不一致）の検出は standard でも変えない。
"""

from __future__ import annotations

import json
from dataclasses import replace

from test_grounded_answer import LIST_TEXT, FakeModel, audit, context, draft, ids, item, marks, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.models.llm import GroundedAudit, GroundedFactAudit, GroundedItem, GuideRequirementReview

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
    # 主張の信頼性（支持・矛盾・適用・要約・目的）と、要求・業務ガイドの要件の充足の規則は残す。
    for kept in (
        "3.1 support",
        "contradicted",
        "3.2 applicability",
        "summary_supported",
        "goal_alignment",
        "request_reviews",
        "guide_requirement_reviews",
    ):
        assert kept in standard
    assert "unused_evidence" not in standard


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
    assert full.unused_evidence_ids == []
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


# ---- 監査なしの型（#2800）と、要件の監査の観察（#2800） ------------------------------------


def test_none_mode_calls_no_audit_and_does_not_regenerate():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    eid = ids(ctx)[LIST_TEXT]
    model = FakeModel([draft(item(eid, STEP, STEP))], [_fact(audit((0, "contradicted", "matched", "")))])

    result = _run(model, ctx, grounded.AUDIT_MODE_NONE, answer_max_correction_rounds=2).response

    assert [name for name, _prompt in model.prompts] == ["GroundedDraft"]
    assert result.generation_trace["audit_mode"] == "none"
    # 決定的な検査を通った説明は確かめた説明として出る（未確認の印を付けない）。
    assert STEP in result.answer_text
    assert STEP not in marks(result)
    assert result.envelope["removed_passages"] == []


def test_none_mode_still_marks_a_quote_that_is_not_in_the_document():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    eid = ids(ctx)[LIST_TEXT]
    invented = "一覧表を選択すると自動で集計が完了します。"
    model = FakeModel([draft(item(eid, invented, invented))], [_fact(audit())])

    result = _run(model, ctx, grounded.AUDIT_MODE_NONE).response

    assert [name for name, _prompt in model.prompts] == ["GroundedDraft"]
    assert marks(result)[invented] == ["quote_unmatched"]
    # 資料の出典の行を付けない（#2792）。
    following = result.answer_text.split("・" + invented + "\n", 1)[1]
    assert not following.startswith(grounded.answer_labels().citation_prefix)


def test_deterministic_requests_follow_the_explanations():
    question = "年次集計の明細一覧表を出力する手順を教えてください"
    confirmed = grounded.CheckedItem(GroundedItem(kind="operation", text=STEP, quote=STEP, evidence_id="E1"), span={"evidence_id": "E1"})
    gap = grounded.CheckedItem(GroundedItem(kind="gap", text="資料に無い"))

    def statuses(checked):
        return {r.status for r in grounded.deterministic_audit(question, checked).request_reviews}

    assert statuses([confirmed]) == {"addressed"}
    assert statuses([gap]) == {"missing"}
    confirmed.mark("item_check", "検査")
    assert statuses([confirmed]) == {"partial"}


def test_requirement_status_decides_unavailable_scope_by_the_requirement_kind():
    gap = grounded.CheckedItem(GroundedItem(kind="gap", text="対応は資料に記載がありません。"))
    step = grounded.CheckedItem(GroundedItem(kind="operation", text=STEP, quote=STEP, evidence_id="E1"), span={"evidence_id": "E1"})
    checked = [step, gap]

    def review(state, indexes):
        return GuideRequirementReview(requirement_id="a:0", answer_state=state, item_indexes=indexes, answer_quote="", reason="")

    completion, evidence = {"kind": "completion"}, {"kind": "evidence"}
    # 資料に無いことを gap で示したのは、完了条件の充足（製品の規則）。必要な根拠の充足ではない。
    assert grounded.requirement_status(review("stated_unavailable", [1]), completion, checked) == "addressed"
    assert grounded.requirement_status(review("stated_unavailable", [1]), evidence, checked) == "missing"
    # 「無い」と言いながら gap も案内の行も引かないなら、一部だけ。
    assert grounded.requirement_status(review("stated_unavailable", [0]), completion, checked) == "partial"
    assert grounded.requirement_status(review("stated_unavailable", [2]), completion, checked, ["窓口へ"]) == "addressed"
    assert grounded.requirement_status(review("explained", [0]), evidence, checked) == "addressed"
    assert grounded.requirement_status(review("partly_explained", [0]), completion, checked) == "partial"
    assert grounded.requirement_status(review("not_mentioned", []), completion, checked) == "missing"
    assert grounded.requirement_status(None, completion, checked) == "missing"


# ---- 前提を示して回答する（未回答の確認事項。#3069） ------------------------------------

_ASSUME_QUESTION = "利用料の上限はいくらですか？"
_ROW_A = "区分Aの利用料の上限は8000円です。"
_ROW_B = "区分Bの利用料の上限は10000円です。"


def _assume_run(model, *, assume):
    from pathlib import Path

    from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext

    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    ctx = context(record(0, _ROW_A, "区分A"), record(1, _ROW_B, "区分B"))
    knowledge = RuntimeKnowledgeContext(
        original_question=_ASSUME_QUESTION,
        expanded_question=_ASSUME_QUESTION,
        path=Path("runtime_knowledge.json"),
        loaded=True,
        assume_unconfirmed=assume,
        unconfirmed_conditions=(
            ({"condition_id": "rule:a", "question": "区分はどちらですか？", "options": ["区分A", "区分B"]},) if assume else ()
        ),
    )
    settings = replace(get_settings(), answer_audit_mode=grounded.AUDIT_MODE_NONE, answer_max_correction_rounds=2)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = synthesize_grounded_answer(_ASSUME_QUESTION, ctx, settings, runtime_knowledge=knowledge, image_prompt_mode="text_only")
    return result.response, ids(ctx, _ASSUME_QUESTION)


def _gap_only():
    return draft(item("", "区分が分からないため上限を答えられません。", "", kind="gap", request_id="Q1"), summary="")


def test_assumed_answer_redrafts_a_refusal_that_cites_no_evidence():
    ctx = context(record(0, _ROW_A, "区分A"), record(1, _ROW_B, "区分B"))
    eid = ids(ctx, _ASSUME_QUESTION)
    conditional = draft(
        item(eid[_ROW_A], _ROW_A, _ROW_A, applies="conditional", condition="区分A", request_id="Q1"),
        item(eid[_ROW_B], _ROW_B, _ROW_B, applies="conditional", condition="区分B", request_id="Q1"),
        summary="上限は区分Aなら8000円、区分Bなら10000円です。",
    )
    model = FakeModel([_gap_only(), conditional], [])

    result, _ = _assume_run(model, assume=True)

    # 確認事項を理由に拒答した草稿は、選択肢ごとの条件付きの説明を求めて 1 回だけ作り直す。
    assert [name for name, _prompt in model.prompts] == ["GroundedDraft", "GroundedDraft"]
    assert grounded._ASSUMPTION_NO_CITED_HINT in model.prompts[1][1]
    assert _ROW_A in result.answer_text and _ROW_B in result.answer_text
    assert result.envelope["outcome"] != "insufficient_evidence"


def test_refusal_without_assumption_is_not_redrafted_in_none_mode():
    model = FakeModel([_gap_only()], [])

    result, _ = _assume_run(model, assume=False)

    assert [name for name, _prompt in model.prompts] == ["GroundedDraft"]
    assert result.envelope["outcome"] == "insufficient_evidence"


def test_assumed_answer_with_cited_evidence_is_not_redrafted():
    ctx = context(record(0, _ROW_A, "区分A"), record(1, _ROW_B, "区分B"))
    eid = ids(ctx, _ASSUME_QUESTION)
    model = FakeModel([draft(item(eid[_ROW_A], _ROW_A, _ROW_A, applies="conditional", condition="区分A", request_id="Q1"))], [])

    _assume_run(model, assume=True)

    assert [name for name, _prompt in model.prompts] == ["GroundedDraft"]


def test_correction_feedback_in_none_mode_keeps_only_the_assumed_refusal():
    feedback = {"no_cited_evidence": "根拠を引用した item がありません。", "unanswered_requests": [{"request_id": "Q1"}]}
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_NONE) == {}
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_NONE, assume_unconfirmed=True) == {
        "no_cited_evidence": grounded._ASSUMPTION_NO_CITED_HINT
    }
    assert grounded.correction_feedback({"dropped_items": [{"evidence_id": "E1"}]}, "none", assume_unconfirmed=True) == {}
