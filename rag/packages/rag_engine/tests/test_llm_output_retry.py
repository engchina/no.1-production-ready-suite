"""出力の上限で未完了になった応答の送り直しの記録（#1527）を、OpenAI の client を差し替えて確かめる。

上限（`answer_max_tokens`）で未完了なら上限を 2 倍にして 1 回だけ送り直す（#987）。その回数と未完了の理由を、
工程の記録（execution_steps の llm_usage）と log（`llm_call_usage`）に残す。
"""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation import execution_record
from rag_engine.models.llm import TextOutput
from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage, usage_line

PROVIDER = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="")
USAGE = SimpleNamespace(input_tokens=100, output_tokens=50, input_tokens_details=None, output_tokens_details=None)


class _Client:
    """`responses.parse` が順に応答を返し、渡した出力の上限を記録する。"""

    def __init__(self, responses):
        self.responses_left, self.budgets = list(responses), []
        self.responses = SimpleNamespace(parse=self._parse)

    def with_options(self, **_options):
        return self

    def _parse(self, **kwargs):
        self.budgets.append(kwargs["max_output_tokens"])
        response = self.responses_left.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _incomplete(reason):
    return SimpleNamespace(status="incomplete", incomplete_details=SimpleNamespace(reason=reason), usage=USAGE)


def _completed():
    return SimpleNamespace(status="completed", output_parsed=TextOutput(text="ok"), usage=USAGE)


class OutputRetryRecordTest(unittest.TestCase):
    def call(self, client):
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        steps: list = []
        step_token = execution_record._structured_steps.set(steps)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with (
                mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
                self.assertLogs(oci.__name__, level="INFO") as logs,
                execution_record._execution_step("生成"),
            ):
                try:
                    oci._openai_parsed([], settings, TextOutput, PROVIDER)
                except oci.IncompleteResponse:
                    pass
        finally:
            llm_usage.reset(usage_token)
            execution_record._structured_steps.reset(step_token)
        return steps[0]["llm_usage"], logs.records

    def test_retry_after_the_output_limit_is_recorded(self):
        client = _Client([_incomplete("max_output_tokens"), _completed()])
        usage, records = self.call(client)
        self.assertEqual(client.budgets, [1000, 2000])
        self.assertEqual(usage["calls"], 2)
        self.assertEqual(usage["incomplete_responses"], 1)
        self.assertEqual(usage["output_retries"], 1)
        self.assertEqual(usage["incomplete_reasons"], {"max_output_tokens": 1})
        self.assertIn("未完了の応答 1 回（max_output_tokens 1 回）・出力の上限を広げた送り直し 1 回", usage_line(usage))
        self.assertEqual(
            [(r.llm_status, r.llm_incomplete_reason, r.llm_attempt, r.llm_max_output_tokens) for r in records],
            [("incomplete", "max_output_tokens", 0, 1000), ("completed", "", 1, 2000)],
        )

    def test_other_incomplete_reasons_are_recorded_without_retry(self):
        client = _Client([_incomplete("content_filter")])
        usage, _records = self.call(client)
        self.assertEqual(client.budgets, [1000])
        self.assertEqual(usage["incomplete_reasons"], {"content_filter": 1})
        self.assertNotIn("output_retries", usage)

    def test_text_call_reaches_the_responses_api(self):
        """text の呼び出し（`_parse_text_with_retry` → `_parse_openai_text_once`）が Responses API まで届く。"""
        client = _Client([_completed()])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=client), self.assertLogs(oci.__name__, level="INFO"):
            result = oci._parse_text_with_retry("system", "prompt", settings, TextOutput, PROVIDER)
        self.assertEqual((result.text, client.budgets), ("ok", [1000]))

    def test_completed_call_keeps_the_usage_shape(self):
        usage, _records = self.call(_Client([_completed()]))
        self.assertEqual(set(usage), {"calls", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "seconds"})


class DeadlineOutputBudgetTest(unittest.TestCase):
    """回答の期限の残りに合わせた出力の上限（#1548）。"""

    def setUp(self):
        oci._output_rates.clear()
        self.addCleanup(oci._output_rates.clear)

    def test_budget_is_kept_without_deadline_or_measured_rate(self):
        self.assertEqual(oci.deadline_output_budget(1000, PROVIDER), (1000, False))
        with bind_deadline(Deadline(10.0)):
            # 速さの実測が無ければ抑えない。
            self.assertEqual(oci.deadline_output_budget(1000, PROVIDER), (1000, False))
        oci._note_output_rate(PROVIDER, 4000, 100.0)  # 40 token/秒
        self.assertEqual(oci.deadline_output_budget(1000, PROVIDER), (1000, False))

    def test_budget_is_capped_by_the_fastest_rate_with_margin(self):
        oci._note_output_rate(PROVIDER, 3000, 100.0)  # 30 token/秒
        oci._note_output_rate(PROVIDER, 4000, 100.0)  # 40 token/秒
        oci._note_output_rate(PROVIDER, 100, 0.5)  # 出力の短い呼び出しは速さに数えない
        with bind_deadline(Deadline(10.0)):
            budget, capped = oci.deadline_output_budget(24576, PROVIDER)
        self.assertTrue(capped)
        # 40 token/秒 × 1.25 × 残り（約 10 秒）。
        self.assertLessEqual(budget, 500)
        self.assertGreater(budget, 490)
        # 別のモデルの速さは使わない。
        with bind_deadline(Deadline(10.0)):
            self.assertEqual(oci.deadline_output_budget(24576, SimpleNamespace(model="other")), (24576, False))

    def test_capped_incomplete_response_cuts_the_deadline_without_retry(self):
        oci._note_output_rate(PROVIDER, 4000, 100.0)
        client = _Client([_incomplete("max_output_tokens"), _completed()])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=24576)
        outer = Deadline(600.0)
        child = Deadline(10.0, parent=outer)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with (
                mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
                self.assertLogs(oci.__name__, level="INFO") as logs,
                bind_deadline(child),
                self.assertRaises(DeadlineExceeded),
            ):
                oci._openai_parsed([], settings, TextOutput, PROVIDER)
            usage = llm_usage.get().snapshot()
        finally:
            llm_usage.reset(usage_token)
        # 残りの時間で出せる分で抑え、届いたら送り直さない。子の期限を切り、外側は残す（確かめ終わった分で確定する）。
        self.assertEqual(len(client.budgets), 1)
        self.assertLess(client.budgets[0], 24576)
        self.assertTrue(child.cut_short)
        self.assertFalse(outer.expired)
        self.assertEqual(usage["incomplete_reasons"], {"max_output_tokens": 1})
        self.assertNotIn("output_retries", usage)
        self.assertTrue(logs.records[0].llm_deadline_capped)

    def test_uncapped_incomplete_response_is_still_retried_once(self):
        client = _Client([_incomplete("max_output_tokens"), _completed()])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            self.assertLogs(oci.__name__, level="INFO"),
            bind_deadline(Deadline(600.0)),
        ):
            oci._openai_parsed([], settings, TextOutput, PROVIDER)
        self.assertEqual(client.budgets, [1000, 2000])

    def test_failed_call_is_recorded_with_seconds_and_reason(self):
        client = _Client([TimeoutError("read timeout")])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with (
                mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
                self.assertLogs(oci.__name__, level="INFO") as logs,
                self.assertRaises(TimeoutError),
            ):
                oci._openai_parsed([], settings, TextOutput, PROVIDER)
            usage = llm_usage.get().snapshot()
        finally:
            llm_usage.reset(usage_token)
        self.assertEqual((usage["calls"], usage["output_tokens"], usage["incomplete_reasons"]), (1, 0, {"TimeoutError": 1}))
        logged = logs.records[0]
        self.assertEqual((logged.llm_status, logged.llm_incomplete_reason, logged.llm_max_output_tokens), ("failed", "TimeoutError", 1000))
        self.assertFalse(hasattr(logged, "llm_deadline_capped"))


if __name__ == "__main__":
    unittest.main()
