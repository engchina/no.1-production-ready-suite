"""LLM の 1 回の呼び出しのやり直しの共通の枠（#4736）を、合成の client で確かめる。

送信は最大 3 回（やり直しは 2 回）。止まった判定は無音だけで、やり直しの種類が違っても、内側と外側のループをまたいでも
同じ枠で数える。通信系の待ちは 1 秒・2 秒で、`Retry-After` があれば 30 秒まで従う。
"""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.models.llm import TextOutput
from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage
from tests.test_llm_stall_resend import PROVIDER, USAGE, _Client, _Clock, _script

FAILED_EVENT = SimpleNamespace(type="response.failed", response=SimpleNamespace(error=SimpleNamespace(message="overloaded")))


def _completed_event(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(status="completed", output_text=text, usage=USAGE))


def _incomplete_event() -> SimpleNamespace:
    return SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"), usage=USAGE),
    )


VALID = TextOutput(text="ok").model_dump_json()
INVALID = '{"unexpected": true}'
REPLACEMENT = TextOutput(text="合成の�出力").model_dump_json()


class _Http429(Exception):
    status_code = 429

    def __init__(self, retry_after: str | None) -> None:
        super().__init__("rate limited")
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after is not None else {})


class _ParseClient:
    """`responses.parse` が順に例外か応答の本文を返す（streaming しない経路）。"""

    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0
        self.responses = SimpleNamespace(parse=self._parse)

    def with_options(self, **_options):
        return self

    def _parse(self, **_kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(status="completed", output_text=result, usage=USAGE)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        oci._output_rates.clear()
        self.addCleanup(oci._output_rates.clear)
        self.clock = _Clock()
        for target, replacement in (("time.monotonic", self.clock), ("time.sleep", mock.Mock())):
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sleep = oci.time.sleep
        token = llm_usage.set(LlmUsage())
        self.addCleanup(llm_usage.reset, token)
        self.stream_settings = replace(
            get_settings(environ={"RAG_ENGINE_LLM_STALL_DETECTION": "1"}, dotenv_path=None),
            answer_max_tokens=1000,
            llm_request_timeout_seconds=600,
        )
        self.plain_settings = replace(self.stream_settings, llm_stall_detection=False)

    def waits(self) -> list[float]:
        total: list[float] = []
        for call in self.sleep.call_args_list:
            total.append(call.args[0])
        return total

    def call(self, client, settings, *, deadline: Deadline | None = None):
        with (
            mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
            bind_deadline(deadline if deadline is not None else Deadline(600)),
        ):
            return oci._parse_with_retry(
                lambda: oci._openai_parsed([{"role": "user", "content": "合成の質問"}], settings, TextOutput, PROVIDER),
                "synthetic",
                settings,
            )


class SharedBudgetTest(_Base):
    def test_inner_and_outer_retries_share_one_budget(self):
        """失敗の event（内側）→ 不正な出力（外側）→ 無音（内側）で枠を使い切り、送信は 3 回で止まる。"""
        client = _Client(
            self.clock,
            [
                _script(1, 200.0, final=FAILED_EVENT),
                _script(1, 200.0, final=_completed_event(INVALID)),
                _script(1, 200.0, raise_at=0),
                _script(1, 200.0),
                _script(1, 200.0),
            ],
        )
        with self.assertRaises(oci.LlmStreamStalled):
            self.call(client, self.stream_settings)
        self.assertEqual(len(client.streams), 3)

    def test_replacement_character_is_counted(self):
        """U+FFFD の検査とやり直しは残し、同じ枠で数える。"""
        client = _Client(self.clock, [_script(1, 200.0, final=_completed_event(REPLACEMENT)) for _ in range(5)])
        with self.assertRaises(oci.InvalidStructuredOutput):
            self.call(client, self.stream_settings)
        self.assertEqual(len(client.streams), 3)

    def test_replacement_character_recovers_within_budget(self):
        client = _Client(self.clock, [_script(1, 200.0, final=_completed_event(REPLACEMENT)), _script(1, 200.0)])
        self.assertEqual(self.call(client, self.stream_settings).text, "ok")
        self.assertEqual(len(client.streams), 2)

    def test_output_limit_resends_are_counted(self):
        """出力の上限での送り直しも同じ枠で数える（無音 → 上限 → 上限で 3 回）。"""
        client = _Client(
            self.clock,
            [
                _script(1, 200.0, raise_at=0),
                _script(1, 200.0, final=_incomplete_event()),
                _script(1, 200.0, final=_incomplete_event()),
                _script(1, 200.0),
            ],
        )
        with self.assertRaises(oci.IncompleteResponse):
            self.call(client, self.stream_settings)
        self.assertEqual(len(client.streams), 3)

    def test_transport_errors_are_counted(self):
        client = _ParseClient([TimeoutError("a"), ConnectionError("b"), TimeoutError("c"), VALID])
        with self.assertRaises(TimeoutError):
            self.call(client, self.plain_settings)
        self.assertEqual(client.calls, 3)


class RetryWaitTest(_Base):
    def test_transport_retries_wait_one_then_two_seconds(self):
        client = _ParseClient([TimeoutError("a"), ConnectionError("b"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 3.0)
        self.assertEqual(client.calls, 3)

    def test_retry_after_is_honoured(self):
        client = _ParseClient([_Http429("7"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 7.0)

    def test_retry_after_is_capped_at_thirty_seconds(self):
        client = _ParseClient([_Http429("100"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 30.0)

    def test_short_retry_after_does_not_shorten_the_backoff(self):
        client = _ParseClient([_Http429("0"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 1.0)

    def test_stalled_stream_waits_before_the_resend(self):
        client = _Client(self.clock, [_script(1, 200.0, raise_at=0), _script(1, 200.0)])
        self.assertEqual(self.call(client, self.stream_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 1.0)

    def test_invalid_output_is_resent_without_waiting(self):
        """モデルの出力の不合格は待っても変わらないので、待たずに送り直す。"""
        client = _Client(self.clock, [_script(1, 200.0, final=_completed_event(INVALID)), _script(1, 200.0)])
        self.assertEqual(self.call(client, self.stream_settings).text, "ok")
        self.assertEqual(self.waits(), [])

    def test_cancel_during_the_wait_stops_the_call(self):
        """待っている間に取り消されたら、残りを待たず、次の送信もしない。"""
        deadline = Deadline(600)
        self.sleep.side_effect = lambda _seconds: deadline.cancel()
        client = _ParseClient([_Http429("30"), VALID])
        with self.assertRaises(DeadlineExceeded):
            self.call(client, self.plain_settings, deadline=deadline)
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.sleep.call_count, 1)

    def test_wait_longer_than_the_deadline_is_not_taken(self):
        client = _ParseClient([_Http429("30"), VALID])
        with self.assertRaises(DeadlineExceeded):
            self.call(client, self.plain_settings, deadline=Deadline(5))
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.waits(), [])


class SilenceOnlyStallTest(_Base):
    def test_slow_output_is_not_cut(self):
        """出力が流れている間は、遅くても打ち切らない（速さの判定は無い）。"""
        client = _Client(self.clock, [_script(3000, 20.0)])
        self.assertEqual(self.call(client, self.stream_settings).text, "ok")
        self.assertEqual(len(client.streams), 1)

    def test_default_silence_limit_is_applied_to_every_send(self):
        """無音の既定は 120 秒。基準の学習は無く最初の呼び出しから効き、最後の送信でも無音は止まった扱いにする。"""
        client = _Client(self.clock, [_script(1, 200.0, raise_at=0) for _ in range(3)])
        with self.assertRaises(oci.LlmStreamStalled) as raised:
            self.call(client, self.stream_settings)
        self.assertEqual(raised.exception.reason, "stall_idle")
        self.assertEqual(client.read_timeouts, [120.0, 120.0, 120.0])


if __name__ == "__main__":
    unittest.main()
