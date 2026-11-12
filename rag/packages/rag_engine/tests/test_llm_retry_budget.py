"""LLM の 1 回の呼び出しのやり直しの共通の枠（#4736）を、合成の client で確かめる。

このテストでは設定でやり直しを 5 回に増やし、送信最大 6 回を検査する。
止まった判定は無音だけで、やり直しの種類が違っても、内側と外側のループをまたいでも
同じ枠で数える。待ちは指数 backoff と full jitter とし、`Retry-After` は下限にする。
"""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

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
        patcher = mock.patch.object(oci.random, "random", return_value=0.5)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sleep = oci.time.sleep
        token = llm_usage.set(LlmUsage())
        self.addCleanup(llm_usage.reset, token)
        self.stream_settings = replace(
            get_settings(environ={"RAG_ENGINE_LLM_STALL_DETECTION": "1"}, dotenv_path=None),
            answer_max_tokens=1000,
            llm_max_retries=5,
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
        """失敗 event・不正出力・無音が混ざっても、送信は合計 6 回で止まる。"""
        failures = [
            _script(1, 200.0, final=FAILED_EVENT),
            _script(1, 200.0, final=_completed_event(INVALID)),
            _script(1, 200.0, raise_at=0),
        ]
        client = _Client(self.clock, failures * 2 + [_script(1, 200.0)])
        with self.assertRaises(oci.LlmStreamStalled):
            self.call(client, self.stream_settings)
        self.assertEqual(len(client.streams), 6)
        self.assertEqual(sum(self.waits()), 15.5)

    def test_replacement_character_is_counted(self):
        """U+FFFD の検査とやり直しは残し、同じ枠で数える。"""
        client = _Client(self.clock, [_script(1, 200.0, final=_completed_event(REPLACEMENT)) for _ in range(7)])
        with self.assertRaises(oci.InvalidStructuredOutput):
            self.call(client, self.stream_settings)
        self.assertEqual(len(client.streams), 6)

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
        client = _ParseClient([TimeoutError("a")] * 6 + [VALID])
        with self.assertRaises(TimeoutError):
            self.call(client, self.plain_settings)
        self.assertEqual(client.calls, 6)


class RetryWaitTest(_Base):
    def test_transport_retries_use_exponential_full_jitter(self):
        client = _ParseClient([TimeoutError("a"), ConnectionError("b"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 1.5)
        self.assertEqual(client.calls, 3)

    def test_retry_after_is_honoured(self):
        client = _ParseClient([_Http429("7"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 7.5)

    def test_retry_after_is_not_shortened(self):
        client = _ParseClient([_Http429("100"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 100.5)

    def test_short_retry_after_does_not_shorten_the_backoff(self):
        client = _ParseClient([_Http429("0"), VALID])
        self.assertEqual(self.call(client, self.plain_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 0.5)

    def test_stalled_stream_waits_before_the_resend(self):
        client = _Client(self.clock, [_script(1, 200.0, raise_at=0), _script(1, 200.0)])
        self.assertEqual(self.call(client, self.stream_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 0.5)

    def test_invalid_output_also_uses_jitter(self):
        """モデルの出力の不合格も他の再試行と同じ jitter の待ちにする。"""
        client = _Client(self.clock, [_script(1, 200.0, final=_completed_event(INVALID)), _script(1, 200.0)])
        self.assertEqual(self.call(client, self.stream_settings).text, "ok")
        self.assertEqual(sum(self.waits()), 0.5)

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
        client = _Client(self.clock, [_script(1, 200.0, raise_at=0) for _ in range(6)])
        with self.assertRaises(oci.LlmStreamStalled) as raised:
            self.call(client, self.stream_settings)
        self.assertEqual(raised.exception.reason, "stall_idle")
        self.assertEqual(client.read_timeouts, [120.0] * 6)


if __name__ == "__main__":
    unittest.main()


def test_every_stream_retry_is_numbered_and_jittered(monkeypatch, caplog):
    import logging

    import httpx
    import pytest
    from openai import APIError

    error = APIError(
        "Upstream Responses stream returned an error event", httpx.Request("POST", "https://stub"), body={"message": "synthetic"}
    )
    clock = _Clock()
    client = _Client(clock, [[(None, error)] for _ in range(7)])
    waits = []
    monkeypatch.setattr(oci.time, "monotonic", clock)
    monkeypatch.setattr(oci.time, "sleep", waits.append)
    monkeypatch.setattr(oci.random, "random", lambda: 0.5)
    settings = replace(get_settings(environ={}, dotenv_path=None), llm_stall_detection=True)
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *args, **kwargs: client)
    with bind_deadline(Deadline(600)), caplog.at_level(logging.INFO, logger=oci.__name__), pytest.raises(oci.LlmStreamFailed):
        oci._parse_with_retry(lambda: oci._openai_parsed([], settings, TextOutput, PROVIDER), "synthetic", settings)
    calls = [r for r in caplog.records if getattr(r, "llm_status", None)]
    assert [r.llm_attempt for r in calls] == [0, 1]
    assert len(client.streams) == 2
    assert sum(waits) == 0.5


def test_embedding_transport_also_uses_jitter_and_retry_after(monkeypatch):
    import pytest

    delays = []
    monkeypatch.setattr(oci.time, "sleep", delays.append)
    monkeypatch.setattr(oci.random, "random", lambda: 0.5)
    attempts = 0

    def operation():
        nonlocal attempts
        attempts += 1
        raise _Http429("3")

    with pytest.raises(_Http429):
        oci._retry_transport(operation, retries=5, initial_wait_seconds=1, max_wait_seconds=16)
    assert attempts == 6
    assert sum(delays) == 30.5


def test_permanent_sdk_stream_error_is_not_resent(monkeypatch):
    import httpx
    import pytest
    from openai import APIError

    clock = _Clock()
    error = APIError("synthetic invalid request", httpx.Request("POST", "https://stub"), body={"error": {"code": "invalid_argument"}})
    client = _Client(clock, [[(None, error)], _script(1, 200.0)])
    monkeypatch.setattr(oci.time, "monotonic", clock)
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *args, **kwargs: client)
    settings = replace(get_settings(environ={}, dotenv_path=None), llm_stall_detection=True)
    with bind_deadline(Deadline(600)), pytest.raises(oci.LlmStreamFailed):
        oci._parse_with_retry(lambda: oci._openai_parsed([], settings, TextOutput, PROVIDER), "synthetic", settings)
    assert len(client.streams) == 1


def test_retry_count_is_configurable_and_defaults_to_one(monkeypatch):
    monkeypatch.setattr(oci, "_wait_before_retry", lambda *_args, **_kwargs: None)
    assert get_settings(environ={}, dotenv_path=None).llm_max_retries == 1
    for retries in (0, 1, 3):
        settings = get_settings(environ={"RAG_ENGINE_LLM_MAX_RETRIES": str(retries)}, dotenv_path=None)
        calls = []

        def failing(calls=calls):
            calls.append(None)
            raise TimeoutError("合成の一時障害")

        with pytest.raises(TimeoutError):
            oci._parse_with_retry(failing, "synthetic", settings)
        assert len(calls) == retries + 1
