"""回答の構造（AnswerEnvelope。#1235）。LLM を差し替え、回答の確定の分岐ごとに対応を確かめる。"""

import json
import unittest
from pathlib import Path

from test_grounded_answer import LIST_TEXT, FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.generation.answer_envelope import ENVELOPE_SCHEMA_VERSION, build_envelope, envelope_requests, fallback_envelope


class AnswerEnvelopeFromGroundedAnswerTest(unittest.TestCase):

    def test_refusal_is_insufficient_evidence_even_when_related_quotes_are_shown(self):
        rule = "⑤本部からの連絡メモが届くと「連」と表示されるので、ボタンを押します。"
        ctx = context(record(0, rule, "［販売管理⇒関連情報⇒取引先メモ管理］", page=3))
        question = "メインメニューに「本部からの連絡メモがあります」と表示されるが、どのような場合に表示されるのか。"
        refusal = draft({"kind": "gap", "text": "表示条件は資料に記載がありません。"}, confidence="low")
        envelope = run(FakeModel([refusal, refusal], [audit()]), ctx, question).response.envelope
        self.assertEqual(envelope["outcome"], "insufficient_evidence")
        self.assertEqual(envelope["items"], [])
        self.assertEqual(envelope["gaps"], ["表示条件は資料に記載がありません。"])

    def test_external_data_makes_the_answer_need_environment_data(self):
        ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
        eid = ids(ctx)[LIST_TEXT]
        steps = {
            **draft(item(eid, "一覧表を選択し、処理を選択します。", "一覧表を選択し、処理を選択します。", request_id="Q1")),
            "external_data_required": True,
            "external_data_items": ["対象の帳票の出力履歴"],
        }
        response = run(FakeModel([steps], [audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")])]), ctx).response
        self.assertEqual(response.envelope["outcome"], "needs_environment_data")
        self.assertIn("environment_data", response.envelope["handoff_reasons"])


class AnswerEnvelopeBuildTest(unittest.TestCase):

    def test_fallback_envelope_for_answers_without_grounded_structure(self):
        self.assertEqual(fallback_envelope(answered=False, insufficient_reason="根拠なし")["outcome"], "insufficient_evidence")
        self.assertEqual(fallback_envelope(answered=True)["outcome"], "answered")
        self.assertEqual(fallback_envelope(answered=True, needs_human_review=True)["outcome"], "conditional")
        self.assertEqual(fallback_envelope(answered=True, external_data_required=True)["outcome"], "needs_environment_data")


E1467 = json.loads((Path(__file__).with_name("fixtures") / "answer_envelope_e1467.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
