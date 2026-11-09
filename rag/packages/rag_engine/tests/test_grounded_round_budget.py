"""生成と監査の是正の round の時間の配分（#1516）を、LLM と時刻を差し替えて確かめる。

回答の期限の残りが 1 組の見積もり（直前の組の実測）と確定の分に足りなければ是正をせずに確定し、round の途中で
確定の分を残した期限が切れても error にせず、確かめ終わった分で条件つきの回答を返す。
"""

import unittest
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import patch

from test_grounded_answer import LIST_TEXT, FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.adapters import oci
from rag_engine.generation import execution_record, grounded
from rag_engine.models.llm import GroundedAudit, GroundedDraft
from rag_engine.resources import deadline as deadlines
from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline, check_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage, record_llm_usage, usage_line

GOOD = "一覧表を選択し、処理を選択します。"


def _context():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    return ctx, ids(ctx)[LIST_TEXT]


def _draft_with_dropped(eid):
    """公開できる説明 1 件と、原文と一致しない引用 1 件（是正の指摘が残る）。"""
    return draft(item(eid, GOOD, GOOD), item("E-unknown", "CSVを保存先へ出力します。", "CSVを C:\\temp へ出力します。"))


class _Clock:
    """回答の期限の残り（`remaining_seconds`）と round の秒（grounded の `monotonic`）を同じ偽の時刻で進める。"""

    def __init__(self, limit):
        self.limit, self.now = limit, 0.0

    def remaining(self):
        return max(0.0, self.limit - self.now)


def _drafts(model):
    return [prompt for name, prompt in model.prompts if name == "GroundedDraftWire"]


class DeadlineDocumentQuotesTest(unittest.TestCase):
    """1 組目の生成が切れたときに示す根拠の抜粋（#1548）。"""

    @staticmethod
    def _span(eid, text, *section, **extra):
        return {"evidence_id": eid, "text": text, "section_path": list(section), "tags": [], **extra}

    def test_question_terms_first_then_evidence_order_up_to_the_limit(self):
        question = "サーバー室の定期点検の記録には、何を書きますか？"
        spans = [
            self._span("E1", "点検の前に、入室の申請をします。", "サーバー室の定期点検", "第 2 章 入室"),
            self._span("E2", "第 5 章 点検の記録"),  # 見出しだけ
            self._span("E3", "画面の説明です。", tags=["image_generated"]),
            self._span("E4", "点検の記録には、点検の日時と点検者の氏名を書きます。", "サーバー室の定期点検", "第 5 章 点検の記録"),
            self._span("E5", "空調の温度を確かめます。", "第 3 章 空調"),
            self._span("E6", "UPS の表示を確かめます。", "第 3 章 電源"),
        ]
        quotes = grounded.deadline_document_quotes(question, spans, limit=3)
        # 質問の語（サーバー室・定期点検・記録）を多く含む E4・E1 が先、続けて根拠の順（E2・E3 は除く）。
        self.assertEqual([entry.span["evidence_id"] for entry in quotes], ["E4", "E1", "E5"])
        self.assertTrue(all(entry.quote_only and entry.item.applies == "unverified" for entry in quotes))
        self.assertEqual(grounded.deadline_document_quotes(question, []), [])


class DeadlineCutShortTest(unittest.TestCase):
    def test_child_deadline_cut_short_only_while_the_outer_deadline_is_alive(self):
        outer = Deadline(100.0)
        child = Deadline(0.0, parent=outer)
        self.assertTrue(child.cut_short)
        outer.cancel()
        # 外側が取り消された（利用者が止めた）ときは、確かめ終わった分で確定しない。
        self.assertFalse(child.cut_short)
        self.assertFalse(Deadline(100.0).cut_short)
        self.assertFalse(Deadline(None).cut_short)


class SynthesizeRoundBudgetTest(unittest.TestCase):
    def run_with_clock(self, model, ctx, clock):
        with patch.object(deadlines, "remaining_seconds", clock.remaining), patch.object(grounded, "monotonic", lambda: clock.now):
            return run(model, ctx)

    def test_outer_deadline_cancel_is_not_absorbed(self):
        ctx, _eid = _context()
        outer = Deadline(100.0)
        outer.cancel()

        def cancelled_draft(prompt):
            check_deadline()
            raise AssertionError("取り消されているはず")

        model = FakeModel([cancelled_draft], [audit()])
        with bind_deadline(outer), self.assertRaises(DeadlineExceeded):
            run(model, ctx)


class LlmUsageTest(unittest.TestCase):
    def test_usage_is_recorded_per_step(self):
        steps: list = []
        step_token = execution_record._structured_steps.set(steps)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with execution_record._execution_step("生成"):
                record_llm_usage(
                    {"input_tokens": 1200, "cached_tokens": 200, "output_tokens": 300, "reasoning_tokens": 250, "seconds": 12.34}
                )
                # 並列の呼び出し（抽出）は文脈を写した thread から同じ累積に足す。
                copy_context().run(record_llm_usage, {"input_tokens": 100, "output_tokens": 10, "seconds": 1.0})
            with execution_record._execution_step("LLM なし"):
                pass
        finally:
            llm_usage.reset(usage_token)
            execution_record._structured_steps.reset(step_token)
        self.assertEqual(
            steps[0]["llm_usage"],
            {"calls": 2, "input_tokens": 1300, "cached_tokens": 200, "output_tokens": 310, "reasoning_tokens": 250, "seconds": 13.3},
        )
        self.assertNotIn("llm_usage", steps[1])
        self.assertEqual(usage_line(steps[0]["llm_usage"]), "入力 1,300 tokens（cache 200）・出力 310 tokens（推論 250）・13.3 秒")

    def test_adapter_reads_responses_usage(self):
        response = SimpleNamespace(
            status="completed",
            usage=SimpleNamespace(
                input_tokens=5000,
                input_tokens_details=SimpleNamespace(cached_tokens=1000),
                output_tokens=800,
                output_tokens_details=SimpleNamespace(reasoning_tokens=600),
            ),
        )
        usage_token = llm_usage.set(LlmUsage())
        try:
            with self.assertLogs(oci.__name__, level="INFO") as logs:
                oci._record_usage(response, SimpleNamespace(model="m"), GroundedDraft, 42.0)
            snapshot = llm_usage.get().snapshot()
        finally:
            llm_usage.reset(usage_token)
        self.assertEqual(
            snapshot,
            {"calls": 1, "input_tokens": 5000, "cached_tokens": 1000, "output_tokens": 800, "reasoning_tokens": 600, "seconds": 42.0},
        )
        self.assertIn("schema=GroundedDraft seconds=42.0 input_tokens=5000", logs.output[0])
        # backend の構造化 log は外部の logger の本文を出さないので、値は extra の項目でも渡す。
        logged = logs.records[0]
        self.assertEqual(
            (logged.event, logged.llm_schema, logged.llm_seconds, logged.input_tokens, logged.reasoning_tokens),
            ("llm_call_usage", "GroundedDraft", 42.0, 5000, 600),
        )
        # usage の無い応答（スタブ・古い endpoint）でも失敗しない。
        oci._record_usage(SimpleNamespace(status="completed"), SimpleNamespace(model="m"), GroundedAudit, 1.0)


if __name__ == "__main__":
    unittest.main()
