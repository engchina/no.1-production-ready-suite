"""回答の「当てはめ」（規則を質問の事実に当てはめた結果。#2155）を、LLM を差し替えて確かめる。"""

import unittest

from citation_unit_support import canonical_fixture
from test_grounded_answer import FakeModel, audit, context, draft, ids, record, run

from rag_engine.generation.applications import APPLICATIONS_SECTION_TITLE
from rag_engine.models.llm import GroundedDraft

RULE_TEXT = "延滞料の計算\n延滞料は、延滞日数に100円を掛けた額とする。"
QUESTION = "返却が遅れ、延滞日数が12日になりました。延滞料はいくらですか。"
FUNCTION = "料金-延滞料"


def rule_item(eid):
    return {
        "kind": "rule",
        "request_id": "Q1",
        "text": "延滞料は延滞日数に100円を掛けた額です。",
        "citations": [{"evidence_id": eid, "quote": "延滞料は、延滞日数に100円を掛けた額とする。"}],
    }


# 説明の段（#4393）の合成の応答。valid な推導の結論の数値を含む日本語の説明。
EXPLANATION = "延滞日数の12日に1日あたり100円を掛けると、12日×100円＝1200円です。"


def application(*, premises=("延滞日数が12日",), indexes=(0,), reasoning="12日×100円＝1200円", result="延滞料は1200円です。"):
    return {"request_id": "Q1", "premises": list(premises), "item_indexes": list(indexes), "reasoning": reasoning, "result": result}


def answer(applications, *, rule_review=("supported", "matched"), application_reviews=((0, True),)):
    ctx = context(record(0, RULE_TEXT, FUNCTION))
    eid = ids(ctx, QUESTION)[RULE_TEXT]
    value = draft(rule_item(eid), summary="延滞料は1200円です。")
    value["applications"] = list(applications)
    review = audit((0, *rule_review, ""), requests=[("Q1", "addressed")])
    review["application_reviews"] = [{"index": i, "valid": v, "reason": "r"} for i, v in application_reviews]
    return run(FakeModel([value], [review], explanations=[EXPLANATION]), ctx, QUESTION).response


class ApplicationTest(unittest.TestCase):
    def test_application_with_internal_labels_is_not_published(self):
        """「規則0」「E12」のような内部の番号を利用者向けの文に書いた当てはめは公開しない（#2165）。"""
        result = answer([application(reasoning="規則0により12日×100円＝1200円")])
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        self.assertTrue(any("内部の番号" in reason for reason in result.generation_trace["applications"][0]["reasons"]))

    def test_application_pointing_to_a_missing_item_is_not_published(self):
        result = answer([application(indexes=(3,))])
        self.assertNotIn(APPLICATIONS_SECTION_TITLE, result.answer_text)
        self.assertIn("items[3] は引用のある規則ではない", result.generation_trace["applications"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()


class SchemaModel:
    """schema の名前ごとに応答を返す LLM の代役（当てはめの段の schema も扱う）。"""

    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, system, prompt, settings, schema, **options):
        observed = schema
        self.calls.append(observed.__name__)
        value = self.responses[observed.__name__]
        if isinstance(value, Exception):
            raise value
        # 生成の既存fixtureはcanonical履歴として再生する。wireの型検証とは別。
        canonical_schema = GroundedDraft if schema.__name__ == "GroundedDraftWire" else schema
        return canonical_fixture(value) if canonical_schema is GroundedDraft else canonical_schema.model_validate(value)


def neutral_answer(question, step_draft, step_audit):
    """草稿が当てはめを書かず、監査が結論を支持しない（summary に警告が付く。#2738）回答。"""
    ctx = context(record(0, RULE_TEXT, FUNCTION))
    eid = ids(ctx, question)[RULE_TEXT]
    review = audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")], summary_supported=False)
    model = SchemaModel(
        {
            "GroundedDraftWire": draft(rule_item(eid), summary="延滞料は1200円です。"),
            "GroundedAudit": review,
            "ApplicationDraft": step_draft,
            "ApplicationAudit": step_audit,
        }
    )
    return run(model, ctx, question).response, model
