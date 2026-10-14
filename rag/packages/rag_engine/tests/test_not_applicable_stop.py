"""1 組目の指摘が not_applicable の降格だけなら作り直さない（#1925）を、LLM を差し替えて確かめる。"""

import unittest
from dataclasses import replace

from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering, grounded
from rag_engine.generation.answering import synthesize_grounded_answer

STEP_A = "一覧表を選択し、処理を選択します。"
STEP_B = "実行ボタンを押して完了します。"
TEXT = "●集計表出力\n" + STEP_A + "\n" + STEP_B
QUESTION = "年次集計の明細一覧表を出力する手順を教えてください"
NA = {"text": "x", "reasons": ["監査: supported/not_applicable 別の帳票向けの手順"]}


def _run(model, ctx):
    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    settings = replace(get_settings(environ={}, dotenv_path=None))
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(QUESTION, ctx, settings, image_prompt_mode="text_only").response


class NotApplicableOnlyUnitTest(unittest.TestCase):
    def test_not_applicable_only(self):
        self.assertTrue(grounded.not_applicable_only({"downgraded_items": [NA, NA], "supported_items": [{}]}))
        for reasons in (
            ["監査: unsupported/matched 引用にない値"],
            ["監査: supported/not_applicable r", "引用にない操作"],  # 検査の降格を含む
            ["監査: supported/conditional r"],
            [],
        ):
            with self.subTest(reasons=reasons):
                self.assertFalse(grounded.not_applicable_only({"downgraded_items": [NA, {"text": "y", "reasons": reasons}]}))
        # 答えていない要求（多段の問の途中の段を誤って not_applicable にした監査の揺れ）などが残るなら作り直す。
        for key, value in (
            ("unanswered_requests", [{"request_id": "Q1", "status": "partial"}]),
            ("dropped_items", [{}]),
            ("unused_evidence_ids", [{"evidence_id": "e"}]),
            ("goal_alignment", "partial"),
        ):
            with self.subTest(key=key):
                self.assertFalse(grounded.not_applicable_only({"downgraded_items": [NA], key: value}))

    def test_stop_reason_after_the_first_round(self):
        na = {"downgraded_items": [NA]}
        self.assertEqual(
            grounded.no_improvement_reason(0, accepted=True, feedback=na, previous=None), grounded.NO_IMPROVEMENT_NOT_APPLICABLE_ONLY
        )
        # 1 組目の後の unsupported の降格は従来どおり作り直す（付け足した前提を直せることがある）。
        unsupported = {"downgraded_items": [{"text": "y", "reasons": ["監査: unsupported/matched r"]}]}
        self.assertEqual(grounded.no_improvement_reason(0, accepted=True, feedback=unsupported, previous=None), "")
        self.assertIn(grounded.NO_IMPROVEMENT_NOT_APPLICABLE_ONLY, grounded.NO_IMPROVEMENT_REASONS)
        self.assertIn(grounded.NO_IMPROVEMENT_NOT_APPLICABLE_ONLY, answering._NO_IMPROVEMENT_RESULTS)


class NotApplicableOnlyRoundTest(unittest.TestCase):
    def setUp(self):
        self.ctx = context(record(0, TEXT, "売上-(3)年次集計明細作成"))
        self.eid = ids(self.ctx, QUESTION)[TEXT]
        self.first = draft(item(self.eid, STEP_A, STEP_A), item(self.eid, STEP_B, STEP_B))

    def test_first_round_with_only_not_applicable_downgrades_is_final(self):
        model = FakeModel([self.first], [audit((0, "supported", "matched", ""), (1, "supported", "not_applicable", ""))])
        result = _run(model, self.ctx)
        rounds = result.generation_trace["rounds"]
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["improvement_stop"], grounded.NO_IMPROVEMENT_NOT_APPLICABLE_ONLY)
        self.assertEqual([name for name, _ in model.prompts], ["GroundedDraft", "GroundedAudit"])
        self.assertIn("「" + STEP_B + "」", result.answer_text)  # 対象ではない説明は原文のまま示す

    def test_unsupported_downgrade_still_corrects(self):
        model = FakeModel(
            [self.first, self.first],
            [
                audit((0, "supported", "matched", ""), (1, "unsupported", "matched", "")),
                audit((0, "supported", "matched", ""), (1, "supported", "matched", "")),
            ],
        )
        rounds = _run(model, self.ctx).generation_trace["rounds"]
        self.assertGreaterEqual(len(rounds), 2)


if __name__ == "__main__":
    unittest.main()
