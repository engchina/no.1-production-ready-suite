"""出力の上限で未完了になった応答の送り直しの記録（#1527）を、OpenAI の client を差し替えて確かめる。

オンラインの未完了は登録容量内の3段階で扱い、固定予算と取り込みは旧契約を保つ。
試行ごとのusageと未完了の理由を記録する。
"""

import unittest
from contextlib import suppress
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation import execution_record
from rag_engine.models.llm import TextOutput
from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage, usage_line

PROVIDER = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="", max_output_tokens=65536)
MEDIUM_PROVIDER = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="medium", max_output_tokens=65536)
USAGE = SimpleNamespace(input_tokens=100, output_tokens=50, input_tokens_details=None, output_tokens_details=None)


class _Client:
    """`responses.parse` が順に応答を返し、渡した出力の上限を記録する。"""

    def __init__(self, responses):
        self.responses_left, self.budgets, self.efforts = list(responses), [], []
        self.responses = SimpleNamespace(parse=self._parse)

    def with_options(self, **_options):
        return self

    def _parse(self, **kwargs):
        self.budgets.append(kwargs["max_output_tokens"])
        self.efforts.append((kwargs.get("reasoning") or {}).get("effort", ""))
        response = self.responses_left.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _incomplete(reason):
    return SimpleNamespace(status="incomplete", incomplete_details=SimpleNamespace(reason=reason), usage=USAGE)


def _completed():
    return SimpleNamespace(status="completed", output_text=TextOutput(text="ok").model_dump_json(), usage=USAGE)


class OutputRetryRecordTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(oci.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, client, provider=PROVIDER, *, retries=1):
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000, llm_max_retries=retries)
        steps: list = []
        step_token = execution_record._structured_steps.set(steps)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with (
                mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
                self.assertLogs(oci.__name__, level="INFO") as logs,
                execution_record._execution_step("生成"),
                suppress(oci.IncompleteResponse),
            ):
                oci._openai_parsed([], settings, TextOutput, provider)
        finally:
            llm_usage.reset(usage_token)
            execution_record._structured_steps.reset(step_token)
        return steps[0]["llm_usage"], logs.records

    def test_retry_after_the_output_limit_is_recorded(self):
        client = _Client([_incomplete("max_output_tokens"), _completed()])
        usage, records = self.call(client)
        self.assertEqual(client.budgets, [16384, 32768])
        self.assertEqual(usage["calls"], 2)
        self.assertEqual(usage["incomplete_responses"], 1)
        self.assertEqual(usage["output_retries"], 1)
        self.assertEqual(usage["incomplete_reasons"], {"max_output_tokens": 1})
        self.assertIn("未完了の応答 1 回（max_output_tokens 1 回）・出力の上限を広げた送り直し 1 回", usage_line(usage))
        self.assertEqual(
            [
                (r.llm_status, r.llm_incomplete_reason, r.llm_attempt, r.llm_max_output_tokens)
                for r in records
                if getattr(r, "event", "") == "llm_call_usage"
            ],
            [("incomplete", "max_output_tokens", 0, 16384), ("completed", "", 1, 32768)],
        )
        # 未完了は理由・上限・推論の量・次にすることを WARNING で残す（#2023。INFO が出ない配備でも残る）。
        warned = [r for r in records if getattr(r, "event", "") == "llm_incomplete_response"]
        self.assertEqual(
            [(r.levelname, r.llm_incomplete_reason, r.llm_max_output_tokens, r.llm_next_action) for r in warned],
            [("WARNING", "max_output_tokens", 16384, "widen")],
        )

    def test_other_incomplete_reasons_are_recorded_without_retry(self):
        client = _Client([_incomplete("content_filter")])
        usage, _records = self.call(client)
        self.assertEqual(client.budgets, [16384])
        self.assertEqual(usage["incomplete_reasons"], {"content_filter": 1})
        self.assertNotIn("output_retries", usage)

    def test_corrupted_generated_text_retries_without_repairing_characters(self):
        corrupted = SimpleNamespace(status="completed", output_text=TextOutput(text="説明\ufffd").model_dump_json(), usage=USAGE)
        client = _Client([corrupted, _completed()])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=client):
            result = oci._parse_text_with_retry("system", "prompt", settings, TextOutput, PROVIDER)
        self.assertEqual(result.text, "ok")
        self.assertEqual(len(client.budgets), 2)

    def test_repeated_corruption_fails_with_existing_retry_limit(self):
        corrupted = SimpleNamespace(status="completed", output_text=TextOutput(text="説明\ufffd").model_dump_json(), usage=USAGE)
        client = _Client([corrupted] * (1 + oci.LLM_CALL_MAX_RETRIES))
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=client), self.assertRaises(oci.InvalidStructuredOutput):
            oci._parse_text_with_retry("system", "prompt", settings, TextOutput, PROVIDER)
        self.assertEqual(len(client.budgets), 1 + oci.LLM_CALL_MAX_RETRIES)

    def test_rejected_corruption_names_the_fields_but_not_the_text(self):
        # 退けた出力の原文は残らないので、どの欄に置換文字があったかを例外と log に残す（#3849）。本文は残さない。
        corrupted = SimpleNamespace(status="completed", output_text=TextOutput(text="合成の説明�").model_dump_json(), usage=USAGE)
        client = _Client([corrupted] * (1 + oci.LLM_CALL_MAX_RETRIES))
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            self.assertLogs(oci.__name__, level="WARNING") as logs,
            self.assertRaises(oci.InvalidStructuredOutput) as raised,
        ):
            oci._parse_text_with_retry("system", "prompt", settings, TextOutput, PROVIDER)
        self.assertIn("欄: text", str(raised.exception))
        self.assertNotIn("合成の説明", str(raised.exception))
        rejected = [record for record in logs.records if getattr(record, "event", "") == "llm_output_rejected"]
        self.assertEqual(len(rejected), 1 + oci.LLM_CALL_MAX_RETRIES)
        self.assertEqual(rejected[0].llm_rejected_paths, ["text"])
        self.assertEqual(rejected[0].llm_rejected_reason, "replacement_character")
        self.assertNotIn("合成の説明", rejected[0].getMessage())

    def test_replacement_char_paths_point_into_nested_lists(self):
        value = {"request_coverage": [{"evidence_scope": [{"target": "a"}, {"target": "b�"}]}], "items": [{"text": "�"}]}
        self.assertEqual(oci.replacement_char_paths(value), ["request_coverage[0].evidence_scope[1].target", "items[0].text"])
        self.assertEqual(oci.replacement_char_paths({"xs": ["�"] * 20}, limit=2), ["xs[0]", "xs[1]"])

    def test_original_replacement_character_is_preserved(self):
        corrupted = SimpleNamespace(status="completed", output_text=TextOutput(text="原文\ufffd").model_dump_json(), usage=USAGE)
        client = _Client([corrupted])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=client):
            result = oci._parse_text_with_retry("system", "引用: 原文\ufffd", settings, TextOutput, PROVIDER)
        self.assertEqual(result.text, "原文\ufffd")
        self.assertEqual(len(client.budgets), 1)

    def test_text_call_reaches_the_responses_api(self):
        """text の呼び出し（`_parse_text_with_retry` → `_parse_openai_text_once`）が Responses API まで届く。"""
        client = _Client([_completed()])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=client), self.assertLogs(oci.__name__, level="INFO"):
            result = oci._parse_text_with_retry("system", "prompt", settings, TextOutput, PROVIDER)
        self.assertEqual((result.text, client.budgets), ("ok", [16384]))

    def test_unsupported_next_tier_does_not_send_an_intermediate_limit(self):
        client = _Client([_incomplete("max_output_tokens"), _completed()])
        provider = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="", max_output_tokens=1500)
        settings = get_settings(environ={}, dotenv_path=None)
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=client), self.assertRaises(oci.IncompleteResponse):
            oci._openai_parsed([], settings, TextOutput, provider)
        self.assertEqual(client.budgets, [1500])

    def test_first_budget_is_capped_and_not_retried_when_it_cannot_widen(self):
        """最初の上限はモデルの出力の上限で抑え、それ以上に広げられなければ送り直さない（#1719）。

        失敗の文には、理由・送った上限・使った出力（うち推論）の token を出す。
        """
        client = _Client([_incomplete("max_output_tokens"), _completed()])
        provider = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="", max_output_tokens=1000)
        settings = get_settings(environ={}, dotenv_path=None)
        self.assertEqual(settings.answer_max_tokens, 32768)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with (
                mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
                self.assertLogs(oci.__name__, level="INFO"),
                self.assertRaises(oci.IncompleteResponse) as raised,
            ):
                oci._openai_parsed([], settings, TextOutput, provider)
            usage = llm_usage.get().snapshot()
        finally:
            llm_usage.reset(usage_token)
        self.assertEqual(client.budgets, [1000])
        self.assertNotIn("output_retries", usage)
        message = str(raised.exception)
        self.assertEqual(raised.exception.reason, "max_output_tokens")
        for expected in ("stub-model", "理由=max_output_tokens", "出力の上限=1000 tokens", "使った出力=50 tokens（うち推論 0）"):
            self.assertIn(expected, message)
        self.assertNotIn("送り直しても", message)

    def test_incomplete_after_retry_reports_the_widened_limit(self):
        client = _Client([_incomplete("max_output_tokens")] * 3)
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            self.assertLogs(oci.__name__, level="INFO"),
            self.assertRaises(oci.IncompleteResponse) as raised,
        ):
            oci._openai_parsed([], settings, TextOutput, PROVIDER)
        self.assertEqual(client.budgets, [16384, 32768, 65536])
        self.assertIn("3回の試行", str(raised.exception))
        self.assertIn("出力の上限=65536 tokens", str(raised.exception))

    def test_reasoning_effort_is_kept_across_three_tiers(self):
        """出力の上限だけを3段階で増やし、推論量と入力は変えない。"""
        client = _Client([_incomplete("max_output_tokens"), _incomplete("max_output_tokens"), _completed()])
        usage, records = self.call(client, provider=MEDIUM_PROVIDER, retries=2)
        self.assertEqual(client.budgets, [16384, 32768, 65536])
        self.assertEqual(client.efforts, ["medium", "medium", "medium"])
        self.assertEqual(usage["output_retries"], 2)
        self.assertNotIn("effort_retries", usage)
        warned = [r for r in records if getattr(r, "event", "") == "llm_incomplete_response"]
        self.assertEqual(
            [(r.llm_reasoning_effort, r.llm_next_action) for r in warned],
            [("medium", "widen"), ("medium", "widen")],
        )

    def test_reasoning_effort_is_kept_when_capacity_is_insufficient(self):
        """容量不足では推論量を変えた追加要求を送らない。"""
        client = _Client([_incomplete("max_output_tokens"), _completed()])
        provider = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="high", max_output_tokens=1000)
        self.call(client, provider=provider)
        self.assertEqual(client.budgets, [1000])
        self.assertEqual(client.efforts, ["high"])

    def test_incomplete_after_both_retries_names_the_fixes(self):
        """送り直しても未完了なら、利用者向けの文（何が起きたかと直し方）と「詳細」の文を分けて送出する。"""
        client = _Client([_incomplete("max_output_tokens")] * 3)
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000, llm_max_retries=2)
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            self.assertLogs(oci.__name__, level="INFO"),
            self.assertRaises(oci.IncompleteResponse) as raised,
        ):
            oci._openai_parsed([], settings, TextOutput, MEDIUM_PROVIDER)
        error = raised.exception
        self.assertEqual(client.efforts, ["medium", "medium", "medium"])
        self.assertIn("出力の上限に達し", error.user_message)
        self.assertIn("3回の試行", error.user_message)
        self.assertIn("登録された出力容量", error.user_message)
        self.assertIn("質問を分け", error.user_message)
        # 推論量の変更を勧めず、診断のtoken内訳は詳細へ分ける。
        self.assertNotIn("「推論の量」を下げる", error.user_message)
        self.assertNotIn("tokens（うち推論", error.user_message)
        self.assertIn("出力の上限=65536 tokens", error.detail)
        self.assertIn("推論の量=medium", error.detail)

    def test_low_reasoning_effort_is_not_lowered_further(self):
        """low は下げない（none は「送らない」で endpoint の既定になり、推論が増えることがある）。"""
        client = _Client([_incomplete("max_output_tokens"), _incomplete("max_output_tokens"), _completed()])
        provider = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="low")
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            self.assertLogs(oci.__name__, level="INFO"),
            self.assertRaises(oci.IncompleteResponse),
        ):
            oci._openai_parsed([], settings, TextOutput, provider)
        self.assertEqual(client.efforts, ["low", "low"])

    def test_bound_output_cap_limits_the_budget_and_never_widens(self):
        """役目に合わせた上限（`bind_output_cap`）は最初の上限を抑え、上限を広げて送り直さない。"""
        client = _Client([_incomplete("max_output_tokens"), _incomplete("max_output_tokens"), _completed()])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            self.assertLogs(oci.__name__, level="INFO"),
            self.assertRaises(oci.IncompleteResponse),
            oci.bind_output_cap(300),
        ):
            oci._openai_parsed([], settings, TextOutput, MEDIUM_PROVIDER)
        # 広げずに推論の量を 1 段下げて 1 回だけ送り直し、それでも未完了なら失敗にする。
        self.assertEqual(client.budgets, [300, 300])
        self.assertEqual(client.efforts, ["medium", "low"])

    def test_output_cap_is_dropped_outside_the_context(self):
        client = _Client([_completed()])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with oci.bind_output_cap(300):
            pass
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=client), oci.bind_output_cap(0):
            oci._openai_parsed([], settings, TextOutput, PROVIDER)
        self.assertEqual(client.budgets, [16384])

    def test_content_filter_is_not_retried_and_says_so(self):
        client = _Client([_incomplete("content_filter")])
        settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            self.assertLogs(oci.__name__, level="INFO"),
            self.assertRaises(oci.IncompleteResponse) as raised,
        ):
            oci._openai_parsed([], settings, TextOutput, MEDIUM_PROVIDER)
        self.assertEqual(client.budgets, [16384])
        self.assertIn("安全の判定", raised.exception.user_message)

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
        self.assertEqual(client.budgets, [16384, 32768])

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
        self.assertEqual((logged.llm_status, logged.llm_incomplete_reason, logged.llm_max_output_tokens), ("failed", "TimeoutError", 16384))
        self.assertFalse(hasattr(logged, "llm_deadline_capped"))


if __name__ == "__main__":
    unittest.main()


def _http_response(status: str, text: str, reason: str | None = None) -> dict:
    """Responses API の合成の HTTP 応答。"""
    return {
        "id": "r",
        "object": "response",
        "created_at": 0,
        "model": "synthetic",
        "status": status,
        "incomplete_details": {"reason": reason} if reason else None,
        "output": [
            {
                "id": "o",
                "type": "message",
                "role": "assistant",
                "status": status,
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1,
            "output_tokens": 3,
            "total_tokens": 4,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _real_sdk_client(monkeypatch, responses: list[dict]) -> list[dict]:
    """実の OpenAI SDK に合成の HTTP 応答を返す（スタブの parse では SDK の本文の検証を確かめられない）。"""
    import json

    import httpx
    from openai import OpenAI

    bodies: list[dict] = []
    values = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=next(values))

    client = OpenAI(api_key="x", base_url="http://synthetic/v1", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda provider, settings: client)
    monkeypatch.setattr(oci, "_openai_client_with_deadline", lambda value, settings: value)
    return bodies


def test_a_truncated_response_from_the_real_sdk_reaches_the_widening_resend(monkeypatch):
    """#4145: 上限で切れた応答が検証の失敗にならず、上限を広げる送り直しに届く。"""
    bodies = _real_sdk_client(
        monkeypatch,
        [_http_response("incomplete", '{"text":"途中', "max_output_tokens"), _http_response("completed", '{"text":"完了"}')],
    )
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
    result = oci._openai_parsed([], settings, TextOutput, SimpleNamespace(**{**vars(PROVIDER), "max_output_tokens": 65536}))
    assert result.text == "完了"
    assert [body["max_output_tokens"] for body in bodies] == [16384, 32768]
    assert all("text" in body and body["text"]["format"]["type"] == "json_schema" for body in bodies)


def test_invalid_json_in_a_completed_response_stays_a_validation_error(monkeypatch):
    """#4145: 完了した応答の不正な JSON は従来どおり検証の失敗（再実行の対象）。"""
    from pydantic import ValidationError

    _real_sdk_client(monkeypatch, [_http_response("completed", '{"text":"途中')])
    with pytest.raises(ValidationError) as raised:
        oci._openai_parsed(
            [],
            replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000),
            TextOutput,
            SimpleNamespace(**{**vars(PROVIDER), "max_output_tokens": 65536}),
        )
    assert oci.is_invalid_structured_output(raised.value)


def test_streamed_output_uses_three_tiers_and_keeps_reasoning(monkeypatch):
    from tests.test_llm_stall_resend import _Client as StreamClient
    from tests.test_llm_stall_resend import _Clock, _script

    clock = _Clock()
    monkeypatch.setattr(oci.time, "monotonic", clock)
    monkeypatch.setattr(oci.time, "sleep", lambda _: None)
    incomplete = SimpleNamespace(type="response.incomplete", response=_incomplete("max_output_tokens"))
    client = StreamClient(
        clock,
        [
            _script(1, 200.0, final=incomplete),
            _script(1, 200.0, final=incomplete),
            _script(1, 200.0),
        ],
    )
    requests = []
    create = client.responses.create

    def record(**kwargs):
        requests.append(kwargs)
        return create(**kwargs)

    client.responses.create = record
    settings = replace(get_settings(environ={}, dotenv_path=None), llm_stall_detection=True)
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
    with bind_deadline(Deadline(600)):
        result = oci._openai_parsed([], settings, TextOutput, MEDIUM_PROVIDER)
    assert result.text == "ok"
    assert [r["max_output_tokens"] for r in requests] == [16384, 32768, 65536]
    assert all(r["reasoning"] == {"effort": "medium"} for r in requests)
    assert all(r["input"] == requests[0]["input"] for r in requests)


def test_picture_ingestion_retains_its_existing_budget_and_retry_owner(monkeypatch):
    from rag_engine.models.llm import PictureDescriptionOutput

    client = _Client([_incomplete("max_output_tokens")] * 2)
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_tokens=1000)
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
    monkeypatch.setattr(oci.time, "sleep", lambda _: None)
    with pytest.raises(oci.IncompleteResponse):
        oci._openai_parsed([], settings, PictureDescriptionOutput, MEDIUM_PROVIDER)
    assert client.budgets == [1000, 2000]
