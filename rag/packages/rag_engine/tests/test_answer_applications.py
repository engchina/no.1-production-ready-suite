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

    def test_application_with_internal_labels_is_not_published(self):
        """「規則0」「E12」のような内部の番号を利用者向けの文に書いた当てはめは公開しない（#2165）。"""
        result = answer([application(reasoning="規則0により12日×100円＝1200円")])
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        self.assertTrue(any("内部の番号" in reason for reason in result.generation_trace["applications"][0]["reasons"]))

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


class SchemaModel:
    """schema の名前ごとに応答を返す LLM の代役（当てはめの段の schema も扱う）。"""

    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, system, prompt, settings, schema, **options):
        self.calls.append(schema.__name__)
        value = self.responses[schema.__name__]
        if isinstance(value, Exception):
            raise value
        return schema.model_validate(value)


def neutral_answer(question, step_draft, step_audit):
    """草稿が当てはめを書かず、監査が結論を支持しない（中立の文になる）回答。"""
    ctx = context(record(0, RULE_TEXT, FUNCTION))
    eid = ids(ctx, question)[RULE_TEXT]
    review = audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")], summary_supported=False)
    model = SchemaModel(
        {
            "GroundedDraft": draft(rule_item(eid), summary="延滞料は1200円です。"),
            "GroundedAudit": review,
            "ApplicationDraft": step_draft,
            "ApplicationAudit": step_audit,
        }
    )
    return run(model, ctx, question).response, model


class ApplicationStepTest(unittest.TestCase):
    STEP_DRAFT = {"applications": [application()], "summary": "延滞料は1200円です。"}
    STEP_AUDIT = {"application_reviews": [{"index": 0, "valid": True, "reason": "r"}], "summary_supported": True}

    def test_step_makes_application_when_draft_left_summary_neutral(self):
        """草稿が当てはめを書かず結論が中立の文なら、当てはめだけの段で作り、結論の文も出す（#2159）。"""
        result, model = neutral_answer(QUESTION, self.STEP_DRAFT, self.STEP_AUDIT)
        self.assertIn("ApplicationDraft", model.calls)
        self.assertTrue(result.answer_text.startswith("延滞料は1200円です。"))
        self.assertIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        (trace,) = result.generation_trace["applications"]
        self.assertEqual((trace["published"], trace["source"]), (True, "step"))
        self.assertEqual(result.generation_trace["application_step"], {"ran": True, "published": 1})

    def test_step_keeps_neutral_summary_when_audit_rejects(self):
        rejected = {"application_reviews": [{"index": 0, "valid": False, "reason": "r"}], "summary_supported": True}
        result, _ = neutral_answer(QUESTION, self.STEP_DRAFT, rejected)
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        self.assertTrue(result.answer_text.startswith("資料の記載を以下に示します。"))

    def test_step_is_skipped_without_facts_and_survives_failure(self):
        _, model = neutral_answer("延滞料の計算方法を教えてください。", self.STEP_DRAFT, self.STEP_AUDIT)
        self.assertNotIn("ApplicationDraft", model.calls)
        result, _ = neutral_answer(QUESTION, RuntimeError("LLM 失敗"), self.STEP_AUDIT)
        self.assertTrue(result.answer_text.startswith("資料の記載を以下に示します。"))
        self.assertIn("RuntimeError", result.generation_trace["application_step"]["error"])
