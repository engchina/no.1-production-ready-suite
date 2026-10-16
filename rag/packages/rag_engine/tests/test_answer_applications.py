"""回答の「当てはめ」（規則を質問の事実に当てはめた結果。#2155）を、LLM を差し替えて確かめる。"""

import unittest

from test_grounded_answer import FakeModel, audit, context, draft, ids, record, run

from rag_engine.generation.applications import APPLICATIONS_SECTION_TITLE, arithmetic_mismatch

RULE_TEXT = "延滞料の計算\n延滞料は、延滞日数に100円を掛けた額とする。"
QUESTION = "返却が遅れ、延滞日数が12日になりました。延滞料はいくらですか。"
FUNCTION = "料金-延滞料"


def rule_item(eid):
    return {
        "kind": "rule",
        "request_id": "Q1",
        "text": "延滞料は延滞日数に100円を掛けた額です。",
        "evidence_id": eid,
        "quote": "延滞料は、延滞日数に100円を掛けた額とする。",
    }


def application(*, premises=("延滞日数が12日",), indexes=(0,), reasoning="12日×100円＝1200円", result="延滞料は1200円です。"):
    return {"request_id": "Q1", "premises": list(premises), "item_indexes": list(indexes), "reasoning": reasoning, "result": result}


def answer(applications, *, rule_review=("supported", "matched"), application_reviews=((0, True),)):
    ctx = context(record(0, RULE_TEXT, FUNCTION))
    eid = ids(ctx, QUESTION)[RULE_TEXT]
    value = draft(rule_item(eid), summary="延滞料は1200円です。")
    value["applications"] = list(applications)
    review = audit((0, *rule_review, ""), requests=[("Q1", "addressed")])
    review["application_reviews"] = [{"index": i, "valid": v, "reason": "r"} for i, v in application_reviews]
    return run(FakeModel([value], [review]), ctx, QUESTION).response


class ApplicationTest(unittest.TestCase):
    def test_valid_application_is_published_with_premises_reasoning_and_citation(self):
        result = answer([application()])
        self.assertTrue(result.answer_text.startswith("延滞料は1200円です。"))
        section = result.answer_text.split(APPLICATIONS_SECTION_TITLE, 1)[1]
        self.assertIn("・延滞料は1200円です。", section)
        self.assertIn("前提：「延滞日数が12日」", section)
        self.assertIn("当てはめ：12日×100円＝1200円", section)
        self.assertIn("根拠：", section)
        (trace,) = result.generation_trace["applications"]
        self.assertTrue(trace["published"])

    def test_application_with_wrong_arithmetic_is_not_published(self):
        result = answer([application(reasoning="12日×100円＝1300円", result="延滞料は1300円です。")])
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        (trace,) = result.generation_trace["applications"]
        self.assertFalse(trace["published"])
        self.assertTrue(any(reason.startswith("算式が合わない") for reason in trace["reasons"]))

    def test_application_with_premise_not_in_question_is_not_published(self):
        result = answer([application(premises=("延滞日数が20日",))])
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        (trace,) = result.generation_trace["applications"]
        self.assertIn("前提『延滞日数が20日』は質問に無い", trace["reasons"])

    def test_application_rejected_by_audit_is_not_published(self):
        result = answer([application()], application_reviews=((0, False),))
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        self.assertFalse(result.generation_trace["applications"][0]["published"])

    def test_application_is_not_published_when_its_rule_is_downgraded(self):
        result = answer([application()], rule_review=("unsupported", "matched"))
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        self.assertFalse(result.generation_trace["applications"][0]["published"])

    def test_application_pointing_to_a_missing_item_is_not_published(self):
        result = answer([application(indexes=(3,))])
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        self.assertIn("items[3] は引用のある規則ではない", result.generation_trace["applications"][0]["reasons"])


class ArithmeticTest(unittest.TestCase):
    def test_checks_equations_with_units_and_scales(self):
        self.assertEqual(arithmetic_mismatch("44万円＋180万円÷12＝59万円"), "")
        self.assertEqual(arithmetic_mismatch("（71万円－51万円）÷2＝10万円"), "")
        self.assertEqual(arithmetic_mismatch("7＋12＋6＝25か月"), "")
        self.assertEqual(arithmetic_mismatch("44万円＋60万円÷12＝50万円"), "44万円+60万円/12=50万円")

    def test_skips_dates_and_text_without_equations(self):
        self.assertEqual(arithmetic_mismatch("令和8年9月−平成28年9月＝10年"), "")
        self.assertEqual(arithmetic_mismatch("期限は11月21日です。"), "")


if __name__ == "__main__":
    unittest.main()
