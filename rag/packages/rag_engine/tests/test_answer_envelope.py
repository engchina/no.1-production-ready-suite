"""回答の構造（AnswerEnvelope。#1235）。LLM を差し替え、回答の確定の分岐ごとに対応を確かめる。"""
import unittest

from rag_engine.generation.answer_envelope import build_envelope, envelope_requests, fallback_envelope
from test_grounded_answer import LIST_TEXT, QUESTION, FakeModel, audit, context, draft, ids, item, record, run


class AnswerEnvelopeFromGroundedAnswerTest(unittest.TestCase):
    def test_supported_matched_items_are_answered_with_items_and_requests(self):
        ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
        eid = ids(ctx)[LIST_TEXT]
        steps = draft(item(eid, "一覧表を選択し、処理を選択します。", "一覧表を選択し、処理を選択します。", request_id="Q1"))
        envelope = run(FakeModel([steps], [audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")])]), ctx).response.envelope
        self.assertEqual(envelope["schema_version"], 1)
        self.assertEqual(envelope["outcome"], "answered")
        self.assertEqual(envelope["requests"][0]["status"], "addressed")
        self.assertEqual(envelope["items"][0]["kind"], "operation")
        self.assertEqual(envelope["items"][0]["evidence_id"], eid)
        self.assertEqual(envelope["items"][0]["location"], "p.20")
        self.assertEqual(envelope["handoff_reasons"], [])

    def test_conditional_item_makes_the_answer_conditional_with_the_condition(self):
        ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
        eid = ids(ctx)[LIST_TEXT]
        steps = draft(item(eid, "一覧表を選択し、処理を選択します。", "一覧表を選択し、処理を選択します。", request_id="Q1"))
        conditional = audit((0, "supported", "conditional", "処理を選択"), requests=[("Q1", "addressed")])
        envelope = run(FakeModel([steps], [conditional]), ctx).response.envelope
        self.assertEqual(envelope["outcome"], "conditional")
        self.assertEqual(envelope["conditions"], ["処理を選択"])
        self.assertEqual(envelope["items"][0]["applies"], "conditional")

    def test_missing_request_is_conditional_and_listed(self):
        ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
        eid = ids(ctx)[LIST_TEXT]
        steps = draft(item(eid, "一覧表を選択し、処理を選択します。", "一覧表を選択し、処理を選択します。", request_id="Q1"))
        partial = audit((0, "supported", "matched", ""), requests=[("Q1", "missing", "印刷の手順が無い")])
        envelope = run(FakeModel([steps], [partial]), ctx).response.envelope
        self.assertEqual(envelope["outcome"], "conditional")
        statuses = {r["id"]: r["status"] for r in envelope["requests"]}
        self.assertEqual(statuses["Q1"], "missing")
        self.assertIn("unanswered_requests", envelope["handoff_reasons"])

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
        steps = {**draft(item(eid, "一覧表を選択し、処理を選択します。", "一覧表を選択し、処理を選択します。", request_id="Q1")),
                 "external_data_required": True, "external_data_items": ["対象の帳票の出力履歴"]}
        response = run(FakeModel([steps], [audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")])]), ctx).response
        self.assertEqual(response.envelope["outcome"], "needs_environment_data")
        self.assertIn("environment_data", response.envelope["handoff_reasons"])


class AnswerEnvelopeBuildTest(unittest.TestCase):
    def test_requests_skip_context_units_and_mark_unaudited_requests_unknown(self):
        units = [{"id": "Q1", "kind": "request", "text": "説明 原文: 出力したい"}, {"id": "Q2", "kind": "context", "text": "背景"}]
        self.assertEqual(envelope_requests(units, None), [{"id": "Q1", "text": "出力したい", "status": "unknown"}])
        # 監査が返さなかった id は欠落と決めつけない。
        self.assertEqual(envelope_requests(units, [])[0]["status"], "addressed")

    def test_quote_only_items_are_conditional_and_confirmations_without_items_need_data(self):
        quote = {"kind": "rule", "text": "原文", "applies": "quote_only", "condition": ""}
        common = {"requests": (), "reviews": [], "gaps": (), "external_data_required": False}
        self.assertEqual(build_envelope(items=[quote], confirmations=(), **common)["outcome"], "conditional")
        self.assertEqual(build_envelope(items=[], confirmations=["ログを確認"], **common)["outcome"], "needs_environment_data")
        self.assertEqual(build_envelope(items=[], confirmations=(), **common)["outcome"], "insufficient_evidence")

    def test_fallback_envelope_for_answers_without_grounded_structure(self):
        self.assertEqual(fallback_envelope(answered=False, insufficient_reason="根拠なし")["outcome"], "insufficient_evidence")
        self.assertEqual(fallback_envelope(answered=True)["outcome"], "answered")
        self.assertEqual(fallback_envelope(answered=True, needs_human_review=True)["outcome"], "conditional")
        self.assertEqual(fallback_envelope(answered=True, external_data_required=True)["outcome"], "needs_environment_data")


if __name__ == "__main__":
    unittest.main()
