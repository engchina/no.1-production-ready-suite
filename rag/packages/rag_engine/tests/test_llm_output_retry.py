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
        return self.responses_left.pop(0)


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
            with mock.patch.object(oci, "_openai_client_for_provider", return_value=client), \
                    self.assertLogs(oci.__name__, level="INFO") as logs, \
                    execution_record._execution_step("生成"):
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
        self.assertEqual([(r.llm_status, r.llm_incomplete_reason, r.llm_attempt, r.llm_max_output_tokens)
                          for r in records],
                         [("incomplete", "max_output_tokens", 0, 1000), ("completed", "", 1, 2000)])

    def test_other_incomplete_reasons_are_recorded_without_retry(self):
        client = _Client([_incomplete("content_filter")])
        usage, _records = self.call(client)
        self.assertEqual(client.budgets, [1000])
        self.assertEqual(usage["incomplete_reasons"], {"content_filter": 1})
        self.assertNotIn("output_retries", usage)

    def test_completed_call_keeps_the_usage_shape(self):
        usage, _records = self.call(_Client([_completed()]))
        self.assertEqual(set(usage), {"calls", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens",
                                      "seconds"})


if __name__ == "__main__":
    unittest.main()
