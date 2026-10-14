"""是正の組の部分の作り直し（#1924）を、LLM を差し替えて確かめる。

是正の組では、指摘の説明の書き直しと新しい説明だけを生成させ（prompt の検査結果の見出し）、支持済みの説明は
前の組の並びのまま引き継ぐ（`carry_over(keep_order=True)`）。引き継いだ説明もその組の監査に掛ける（#1011）。
"""

import json
import unittest
from dataclasses import replace

from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.models.llm import GroundedItem

STEP_A = "一覧表を選択し、処理を選択します。"
STEP_B = "条件を入力します。"
STEP_C = "実行ボタンを押して完了します。"
STEPS_TEXT = "●集計表出力\n" + "\n".join((STEP_A, STEP_B, STEP_C))
QUESTION = "年次集計の明細一覧表を出力する手順を教えてください"


def run(model, ctx, *, partial=True):
    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    settings = replace(get_settings(), answer_partial_correction=partial)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(QUESTION, ctx, settings, image_prompt_mode="text_only")


class PartialCorrectionRoundTest(unittest.TestCase):
    def setUp(self):
        self.ctx = context(record(0, STEPS_TEXT, "売上-(3)年次集計明細作成"))
        self.eid = ids(self.ctx, QUESTION)[STEPS_TEXT]
        # 1 組目: B の説明が引用にない値を足していて、監査が unsupported（降格）とする。
        self.first = draft(
            item(self.eid, STEP_A, STEP_A),
            item(self.eid, "条件に年度 2025 を入力します。", STEP_B),
            item(self.eid, STEP_C, STEP_C),
        )
        self.first_audit = audit((0, "supported", "matched", ""), (1, "unsupported", "matched", ""), (2, "supported", "matched", ""))
        # 2 組目（部分の作り直し）: 指摘の B の書き直しだけを書く。
        self.rewrite = draft(item(self.eid, STEP_B, STEP_B))
        self.second_audit = audit((0, "supported", "matched", ""), (1, "supported", "matched", ""), (2, "supported", "matched", ""))

    def test_correction_round_writes_only_the_flagged_item_and_keeps_the_order(self):
        model = FakeModel([self.first, self.rewrite], [self.first_audit, self.second_audit])
        result = run(model, self.ctx).response
        prompts = [prompt for name, prompt in model.prompts if name == "GroundedDraft"]
        self.assertNotIn("部分の作り直し", prompts[0])
        self.assertIn(grounded.PARTIAL_FEEDBACK_HEADER.strip(), prompts[1])
        self.assertIn('"supported_items"', prompts[1])
        rounds = result.generation_trace["rounds"]
        self.assertEqual([r["accepted"] for r in rounds], [True, True])
        self.assertTrue(rounds[1]["timing"]["partial_correction"])
        # 引き継いだ A・C の間に、書き直した B が前の組の位置で入る。引き継いだ説明も監査に掛ける。
        self.assertEqual([(i["text"], i["carried"]) for i in rounds[1]["items"]], [(STEP_A, True), (STEP_B, False), (STEP_C, True)])
        second_audit = json.loads([prompt for name, prompt in model.prompts if name == "GroundedAudit"][1])
        self.assertEqual([i["text"] for i in second_audit["items"]], [STEP_A, STEP_B, STEP_C])
        self.assertIn(f"1. {STEP_A}\n", result.answer_text)
        self.assertIn(f"2. {STEP_B}\n", result.answer_text)
        self.assertIn(f"3. {STEP_C}\n", result.answer_text)

    def test_disabled_setting_keeps_the_full_regeneration_prompt(self):
        full = draft(item(self.eid, STEP_A, STEP_A), item(self.eid, STEP_B, STEP_B), item(self.eid, STEP_C, STEP_C))
        model = FakeModel([self.first, full], [self.first_audit, self.second_audit])
        result = run(model, self.ctx, partial=False).response
        prompts = [prompt for name, prompt in model.prompts if name == "GroundedDraft"]
        self.assertIn(grounded.FEEDBACK_HEADER.strip(), prompts[1])
        self.assertNotIn(grounded.PARTIAL_FEEDBACK_HEADER.strip(), prompts[1])
        self.assertNotIn("partial_correction", result.generation_trace["rounds"][1]["timing"])


def _checked(text, quote, *, function=("F1",), evidence_id="E1", request_id="", quote_only=False):
    span = {"evidence_id": evidence_id, "function": function, "text": quote}
    entry = grounded.CheckedItem(
        GroundedItem(kind="operation", text=text, evidence_id=evidence_id, quote=quote, request_id=request_id), span=span
    )
    entry.quote_only = quote_only
    return entry


class CarryOverOrderTest(unittest.TestCase):
    def test_same_set_with_or_without_keeping_the_order(self):
        previous = [_checked("A", "aaa"), _checked("B", "bbb", quote_only=True), _checked("C", "ccc")]
        candidate = [_checked("新しい D", "ddd", function=("F2",)), _checked("B2", "bbb")]
        appended = grounded.carry_over(candidate, previous)
        ordered = grounded.carry_over(candidate, previous, keep_order=True)
        self.assertEqual([e.item.text for e in appended], ["新しい D", "B2", "A", "C"])
        self.assertEqual([e.item.text for e in ordered], ["A", "B2", "C", "新しい D"])
        self.assertEqual(sorted(id(e) for e in candidate), sorted(id(e) for e in ordered if not e.carried))
        self.assertEqual([e.carried for e in ordered], [True, False, True, False])

    def test_new_item_goes_after_the_last_item_of_the_same_function_or_request(self):
        previous = [
            _checked("A", "aaa", request_id="Q1"),
            _checked("B", "bbb", function=("F2",), request_id="Q2"),
            _checked("C", "ccc", function=("F3",), request_id="Q1"),
        ]
        candidate = [
            _checked("F2 の新しい説明", "eee", function=("F2",), evidence_id="E2"),
            _checked("Q1 の新しい説明", "fff", function=("F9",), evidence_id="E3", request_id="Q1"),
        ]
        ordered = grounded.carry_over(candidate, previous, keep_order=True)
        self.assertEqual([e.item.text for e in ordered], ["A", "B", "F2 の新しい説明", "C", "Q1 の新しい説明"])

    def test_narrower_rewrite_keeps_the_previous_claim_and_takes_its_position(self):
        previous = [_checked("A", "aaa"), _checked("B", "bbb ccc", quote_only=True), _checked("D", "ddd")]
        candidate = [_checked("B の前半", "bbb")]
        ordered = grounded.carry_over(candidate, previous, keep_order=True)
        # 前の組の B は草稿の引用に含まれないので引き継ぎ（従来どおり）、その後に書き直しを置く。
        self.assertEqual([(e.item.text, e.carried) for e in ordered], [("A", True), ("B", True), ("B の前半", False), ("D", True)])


if __name__ == "__main__":
    unittest.main()
