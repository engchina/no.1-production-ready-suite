"""業務ガイドの手順ごとの充足（#2491）を、合成の資料と決定論の LLM の差し替えで確かめる。

手順 A → B のガイドで、手順 B の根拠を回答の材料に取れているのに、B の説明が「参考情報」（原文のみ提示）に
留まった回答を、全手順を充足したとして確定しない。根拠が材料にあれば B を対象に作り直し、直らなければ
本文の不足と回答の構造の要求（`guide:<手順 id>`）に示す。
"""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from test_grounded_answer import FakeModel, audit, context, draft, item, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_envelope import GUIDE_STEP_REQUEST_PREFIX, build_envelope, guide_step_requests
from rag_engine.generation.answering import _grounded_spans, synthesize_grounded_answer
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.models.llm import GroundedItem
from rag_engine.retrieval.guide_steps import GuidePlan

QUESTION = "設定 A を変更する手順を教えてください"
STEP_A = "設定画面で設定 A の値を入力します。"
STEP_B = "入力したあとは、反映画面で反映ボタンを押して変更を確定します。"
STEP_C = "種類が乙の場合は、承認画面で承認を依頼します。"


def _record(index, text, function, uid):
    return replace(record(index, text, function, page=10 + index, source="操作説明.pdf"), chunk_uid=uid)


def _context(*records):
    return context(*records)


def _knowledge(guide_steps, optional=()):
    return RuntimeKnowledgeContext(
        original_question=QUESTION,
        expanded_question=QUESTION,
        path=Path("rk.json"),
        guide_steps=guide_steps,
        guide_optional_steps=tuple(optional),
    )


def _run(model, ctx, knowledge, question=QUESTION):
    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(question, ctx, get_settings(), image_prompt_mode="text_only", runtime_knowledge=knowledge)


def _ids(ctx, knowledge, question=QUESTION):
    return {span["text"]: span["evidence_id"] for span in _grounded_spans(question, ctx, (), knowledge)}


def _drafts(model):
    return [prompt for name, prompt in model.prompts if name == "GroundedDraft"]


def _audits(model):
    return [json.loads(prompt) for name, prompt in model.prompts if name == "GroundedAudit"]


def _requests(response):
    return {entry["id"]: entry["status"] for entry in response.envelope["requests"]}


GUIDE = (("s1", "設定 A を入力する", ("run:a",)), ("s2", "反映ボタンで確定する", ("run:b",)))


def _ab_context():
    return _context(_record(0, STEP_A, "設定画面", "run:a"), _record(1, STEP_B, "反映画面", "run:b"))


class GuideStepCoverageUnitTest(unittest.TestCase):
    def _spans(self):
        return [
            {"source_id": "run:a", "evidence_id": "ea", "label": "E1", "text": STEP_A},
            {"source_id": "run:b", "evidence_id": "eb", "label": "E2", "text": STEP_B},
            {"source_id": "run:x", "evidence_id": "ex", "label": "E3", "text": "前置き。" + STEP_B},
        ]

    def _entry(self, span, *, quote_only=False, applies="matched", kind="operation", quote=None):
        entry = grounded.CheckedItem(
            GroundedItem(kind=kind, text="t", quote=span["text"] if quote is None else quote, applies=applies), span=span
        )
        entry.quote_only = quote_only
        return entry

    def test_statuses(self):
        spans = self._spans()
        steps = (("s1", "A", ("run:a",)), ("s2", "B", ("run:b",)), ("s3", "C", ("run:c",)))
        checked = [self._entry(spans[0]), self._entry(spans[1], quote_only=True)]
        coverage = grounded.guide_step_coverage(steps, checked, spans)
        self.assertEqual(
            [(step["step_id"], step["status"]) for step in coverage],
            [("s1", "explained"), ("s2", "reference_only"), ("s3", "no_evidence")],
        )
        # 適用未確認（unverified）の説明は「参考情報」の節に出るので、手順の説明に数えない。
        unverified = grounded.guide_step_coverage(steps[1:2], [self._entry(spans[1], applies="unverified")], spans)
        self.assertEqual(unverified[0]["status"], "reference_only")
        # 根拠が材料にあるのに説明に引いていない。
        self.assertEqual(grounded.guide_step_coverage(steps[1:2], [], spans)[0]["status"], "unused")
        # gap の item は手順の説明に数えない。
        gap = self._entry(spans[1], kind="gap")
        self.assertEqual(grounded.guide_step_coverage(steps[1:2], [gap], spans)[0]["status"], "unused")

    def test_same_text_in_another_chunk_counts_as_the_step_evidence(self):
        spans = self._spans()
        steps = (("s2", "B", ("run:b",)),)
        other = self._entry(spans[2], quote=STEP_B)
        self.assertEqual(grounded.guide_step_coverage(steps, [other], spans)[0]["status"], "explained")
        # 短い引用（どの本文にも当たる語句）は数えない。
        short = self._entry(spans[2], quote="反映ボタン")
        self.assertEqual(grounded.guide_step_coverage(steps, [short], spans)[0]["status"], "unused")

    def test_optional_steps_are_not_required(self):
        spans = self._spans()
        coverage = grounded.guide_step_coverage((("s1", "A", ("run:a",)), ("s2", "B", ("run:b",))), [], spans, optional=("s2",))
        self.assertEqual([step["required"] for step in coverage], [True, False])
        self.assertEqual([step["step_id"] for step in grounded.unexplained_guide_steps(coverage)], ["s1"])

    def test_feedback_keys_keep_correction_going(self):
        feedback = {
            "downgraded_items": [{"text": "t", "reasons": ["監査: supported/not_applicable r"]}],
            "guide_steps_unexplained": [{"step_id": "s2", "status": "reference_only"}],
        }
        # 監査が質問の対象ではないとした降格だけ、とは扱わない（手順を対象に作り直す）。
        self.assertFalse(grounded.not_applicable_only(feedback))
        self.assertFalse(grounded.downgraded_only(feedback))
        self.assertFalse(grounded.evidence_shortage_only(feedback, grounded.timed_out_round(QUESTION)))
        self.assertIn("説明していない業務ガイドの手順 1 件", grounded.pending_feedback_labels(feedback))
        changed = {**feedback, "guide_steps_unexplained": [{"step_id": "s2", "status": "unused"}]}
        self.assertNotEqual(grounded.feedback_signature(feedback), grounded.feedback_signature(changed))


class GuideStepEnvelopeTest(unittest.TestCase):
    def test_guide_step_requests(self):
        coverage = [
            {"step_id": "s1", "title": "A", "required": True, "status": "explained"},
            {"step_id": "s2", "title": "B", "required": True, "status": "reference_only"},
            {"step_id": "s3", "title": "C", "required": True, "status": "unused"},
            {"step_id": "s4", "title": "D", "required": True, "status": "no_evidence"},
            {"step_id": "s5", "title": "E", "required": False, "status": "no_evidence"},
            {"step_id": "x" * 70, "title": "F", "required": True, "status": "explained"},
        ]
        requests = guide_step_requests(coverage)
        self.assertEqual(
            [(entry["id"], entry["status"]) for entry in requests[:4]],
            [("guide:s1", "addressed"), ("guide:s2", "partial"), ("guide:s3", "missing"), ("guide:s4", "missing")],
        )
        self.assertEqual(requests[0]["text"], "業務ガイドの手順: A")
        # 必須でない手順は載せず、ID は要求の ID の上限（64 文字）に収める。
        self.assertEqual(len(requests), 5)
        self.assertEqual(len(requests[-1]["id"]), 64)
        self.assertTrue(requests[-1]["id"].startswith(GUIDE_STEP_REQUEST_PREFIX))

    def test_unexplained_step_makes_the_answer_conditional(self):
        item = {"kind": "operation", "text": "t", "applies": "matched"}
        base = {"requests": [{"id": "Q1", "text": "q"}], "reviews": [{"request_id": "Q1", "status": "addressed"}], "items": [item]}
        answered = build_envelope(**base, gaps=[], confirmations=[], external_data_required=False)
        self.assertEqual(answered["outcome"], "answered")
        partial = build_envelope(
            **base,
            gaps=[],
            confirmations=[],
            external_data_required=False,
            guide_steps=[{"step_id": "s2", "title": "B", "required": True, "status": "reference_only"}],
        )
        self.assertEqual(partial["outcome"], "conditional")
        self.assertIn("unanswered_requests", partial["handoff_reasons"])


class GuideStepFulfillmentTest(unittest.TestCase):
    def test_reference_only_step_is_regenerated_and_explained(self):
        """B が監査で質問の対象ではないとされても、作り直しの指摘に手順を渡し、説明できれば充足にする。"""
        ctx, knowledge = _ab_context(), _knowledge(GUIDE)
        eids = _ids(ctx, knowledge)
        a = item(eids[STEP_A], "設定画面で設定 A の値を入力します。", STEP_A, request_id="Q1")
        b = item(eids[STEP_B], "反映画面で反映ボタンを押して確定します。", STEP_B, request_id="Q1")
        model = FakeModel(
            [draft(a, b)],
            [
                audit((0, "supported", "matched", ""), (1, "supported", "not_applicable", ""), requests=[("Q1", "addressed")]),
                audit((0, "supported", "matched", ""), (1, "supported", "matched", ""), requests=[("Q1", "addressed")]),
            ],
        )
        result = _run(model, ctx, knowledge).response

        drafts = _drafts(model)
        self.assertEqual(len(drafts), 2)
        # 作り直しの指摘に、説明していない手順と手順の根拠の表示 ID が載る。
        self.assertIn("guide_steps_unexplained", drafts[1])
        self.assertIn("反映ボタンで確定する", drafts[1])
        audits = _audits(model)
        self.assertEqual([step["step_id"] for step in audits[0]["guide_steps"]], ["s1", "s2"])
        self.assertTrue(all(step["evidence_ids"] for step in audits[0]["guide_steps"]))
        rounds = result.generation_trace["rounds"]
        self.assertNotIn("improvement_stop", rounds[0])
        self.assertEqual([step["status"] for step in rounds[0]["guide_steps"]], ["explained", "reference_only"])
        self.assertEqual(_requests(result), {"Q1": "addressed", "guide:s1": "addressed", "guide:s2": "addressed"})
        self.assertEqual(result.envelope["outcome"], "answered")
        self.assertNotIn("業務ガイドの手順「", result.answer_text)

    def test_step_left_as_reference_is_not_settled_as_answered(self):
        """作り直しても B が参考情報に留まれば、要求 Q1 の判定とは別に B を一部の充足として示す。"""
        ctx, knowledge = _ab_context(), _knowledge(GUIDE)
        eids = _ids(ctx, knowledge)
        a = item(eids[STEP_A], "設定画面で設定 A の値を入力します。", STEP_A, request_id="Q1")
        b = item(eids[STEP_B], "反映画面で反映ボタンを押して確定します。", STEP_B, request_id="Q1")
        model = FakeModel(
            [draft(a, b)],
            [audit((0, "supported", "matched", ""), (1, "supported", "not_applicable", ""), requests=[("Q1", "addressed")])],
        )
        result = _run(model, ctx, knowledge).response

        self.assertEqual(_requests(result), {"Q1": "addressed", "guide:s1": "addressed", "guide:s2": "partial"})
        self.assertEqual(result.envelope["outcome"], "conditional")
        self.assertTrue(result.needs_human_review)
        self.assertIn("業務ガイドの手順「反映ボタンで確定する」は、資料の記載を参考情報として示しただけ", result.answer_text)
        self.assertIn("反映ボタンで確定する", result.insufficient_reason)
        coverage = result.generation_trace["guide_step_coverage"]
        self.assertEqual([step["status"] for step in coverage], ["explained", "reference_only"])
        # 監査の正当な降格は無効にしない（B の説明は公開せず、原文のまま示す）。
        self.assertNotIn("反映画面で反映ボタンを押して確定します。", result.answer_text)

    def test_missing_step_evidence_is_shown_without_regeneration(self):
        """手順 B の根拠が回答の材料に無ければ、同じ根拠での作り直しはせず、不足として示す。"""
        ctx, knowledge = _context(_record(0, STEP_A, "設定画面", "run:a")), _knowledge(GUIDE)
        eids = _ids(ctx, knowledge)
        a = item(eids[STEP_A], "設定画面で設定 A の値を入力します。", STEP_A, request_id="Q1")
        model = FakeModel([draft(a)], [audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")])])
        result = _run(model, ctx, knowledge).response

        self.assertEqual(len(_drafts(model)), 1)
        self.assertEqual(_requests(result)["guide:s2"], "missing")
        self.assertEqual(result.envelope["outcome"], "conditional")
        self.assertIn("業務ガイドの手順「反映ボタンで確定する」は、取得した資料で説明を確かめられませんでした。", result.answer_text)

    def test_optional_branch_step_is_not_required(self):
        """条件が決まらない分岐の手順は、説明が無くても充足の判定に数えない。"""
        guide = (*GUIDE, ("s3", "乙の場合に承認を依頼する", ("run:c",)))
        ctx = _context(
            _record(0, STEP_A, "設定画面", "run:a"), _record(1, STEP_B, "反映画面", "run:b"), _record(2, STEP_C, "承認画面", "run:c")
        )
        knowledge = _knowledge(guide, optional=("s3",))
        eids = _ids(ctx, knowledge)
        a = item(eids[STEP_A], "設定画面で設定 A の値を入力します。", STEP_A, request_id="Q1")
        b = item(eids[STEP_B], "反映画面で反映ボタンを押して確定します。", STEP_B, request_id="Q1")
        model = FakeModel(
            [draft(a, b)], [audit((0, "supported", "matched", ""), (1, "supported", "matched", ""), requests=[("Q1", "addressed")])]
        )
        result = _run(model, ctx, knowledge).response

        self.assertEqual(len(_drafts(model)), 1)
        self.assertNotIn("guide:s3", _requests(result))
        self.assertEqual(result.envelope["outcome"], "answered")
        self.assertEqual([step["required"] for step in result.generation_trace["guide_step_coverage"]], [True, True, False])

    def test_explicit_requests_and_guide_steps_are_judged_separately(self):
        """明示的な複数要求の充足（監査）と、ガイドの後続手順の充足を別の単位で示す。"""
        question = "設定 A を変更する手順を教えてください。また、変更できる権限も教えてください。"
        rule = "設定 A を変更できるのは管理者です。"
        ctx = _context(_record(0, STEP_A, "設定画面", "run:a"), _record(1, STEP_B, "反映画面", "run:b"), _record(2, rule, "権限", "run:r"))
        knowledge = _knowledge(GUIDE)
        eids = _ids(ctx, knowledge, question)
        a = item(eids[STEP_A], "設定画面で設定 A の値を入力します。", STEP_A, request_id="Q1")
        b = item(eids[STEP_B], "反映画面で反映ボタンを押して確定します。", STEP_B, request_id="Q1")
        r = {**item(eids[rule], rule, rule, request_id="Q2"), "kind": "rule"}
        model = FakeModel(
            [draft(a, b, r)],
            [
                audit(
                    (0, "supported", "matched", ""),
                    (1, "supported", "not_applicable", ""),
                    (2, "supported", "matched", ""),
                    requests=[("Q1", "addressed"), ("Q2", "addressed")],
                )
            ],
        )
        result = _run(model, ctx, knowledge, question).response

        requests = _requests(result)
        self.assertEqual(
            {key: value for key, value in requests.items() if not key.startswith("guide:")}, {"Q1": "addressed", "Q2": "addressed"}
        )
        self.assertEqual(requests["guide:s2"], "partial")
        self.assertEqual(result.envelope["outcome"], "conditional")

    def test_without_guide_steps_nothing_changes(self):
        ctx = _ab_context()
        knowledge = _knowledge(())
        eids = _ids(ctx, knowledge)
        a = item(eids[STEP_A], "設定画面で設定 A の値を入力します。", STEP_A, request_id="Q1")
        model = FakeModel([draft(a)], [audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")])])
        result = _run(model, ctx, knowledge).response

        self.assertNotIn("guide_steps", _audits(model)[0])
        self.assertEqual(_requests(result), {"Q1": "addressed"})
        self.assertNotIn("guide_step_coverage", result.generation_trace)


class GuidePlanRequiredTest(unittest.TestCase):
    def test_required_flag_from_payload(self):
        plan = GuidePlan.from_payload(
            "g",
            1,
            [{"id": "s1", "title": "A"}, {"id": "s2", "title": "B", "required": False}, {"id": "s3", "title": "C", "required": True}],
        )
        assert plan is not None
        self.assertEqual([step.required for step in plan.steps], [True, False, True])
        self.assertEqual(plan.optional_step_ids, ("s2",))


if __name__ == "__main__":
    unittest.main()
