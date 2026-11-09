"""LLM の呼び出しの無音の検出と送り直し（#1926・#4736）を、streaming の client と時計を差し替えて確かめる。

回答の期限がある文脈で停滞の検出が ON なら、Responses API を streaming で受ける。event が設定の秒だけ来なかったら
（`stall_idle`）打ち切って同じ要求を送り直す。送り直しの回数・理由と、最初の event までの秒・出力の速さを工程の記録
（execution_steps の llm_usage）と log に残す。
"""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import httpx

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation import execution_record
from rag_engine.models.llm import TextOutput
from rag_engine.resources.deadline import Deadline, bind_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage, usage_line

PROVIDER = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="", base_url="https://stub")
USAGE = SimpleNamespace(input_tokens=100, output_tokens=300, input_tokens_details=None, output_tokens_details=None)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _completed(text: str = "ok"):
    return SimpleNamespace(status="completed", output_text=TextOutput(text=text).model_dump_json(), usage=USAGE)


def _script(deltas: int, rate: float, *, start: float = 0.1, final=None, raise_at: int | None = None):
    """(送信からの秒, event) の列。``rate`` は delta の event/秒。``raise_at`` 番目の delta の前で read timeout。"""
    events = [(start, SimpleNamespace(type="response.created"))]
    for index in range(deltas):
        if raise_at is not None and index == raise_at:
            events.append((None, httpx.ReadTimeout("read timeout")))
            return events
        events.append((start + index / rate, SimpleNamespace(type="response.reasoning_text.delta")))
    done = final if final is not None else SimpleNamespace(type="response.completed", response=_completed())
    events.append((start + deltas / rate, done))
    return events


class _Stream:
    def __init__(self, clock: _Clock, started: float, events) -> None:
        self.clock, self.started, self.events, self.closed = clock, started, events, False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True

    def __iter__(self):
        for at, event in self.events:
            if isinstance(event, Exception):
                raise event
            self.clock.now = self.started + at
            yield event


class _Client:
    """`responses.create(stream=True)` が順に event の列を返す。送信ごとの read timeout と stream を記録する。"""

    def __init__(self, clock: _Clock, scripts) -> None:
        self.clock, self.scripts = clock, list(scripts)
        self.read_timeouts: list[float] = []
        self.streams: list[_Stream] = []
        self.parse_calls = 0
        self.responses = SimpleNamespace(create=self._create, parse=self._parse)

    def with_options(self, **options):
        timeout = options.get("timeout")
        if isinstance(timeout, httpx.Timeout):
            self.read_timeouts.append(timeout.read)
        return self

    def _create(self, **kwargs):
        assert kwargs["stream"] is True
        return self._stream(**kwargs)

    def _stream(self, **_kwargs):
        stream = _Stream(self.clock, self.clock.now, self.scripts.pop(0))
        self.streams.append(stream)
        return stream

    def _parse(self, **_kwargs):
        self.parse_calls += 1
        return _completed("parse")


class StallResendTest(unittest.TestCase):
    def setUp(self) -> None:
        oci._output_rates.clear()
        self.clock = _Clock()
        for target, replacement in (("time.monotonic", self.clock), ("time.sleep", mock.Mock())):
            patcher = mock.patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(oci._output_rates.clear)
        self.settings = replace(
            get_settings(environ={"RAG_ENGINE_LLM_STALL_DETECTION": "1"}, dotenv_path=None),
            answer_max_tokens=1000,
            llm_request_timeout_seconds=600,
        )

    def call(self, client: _Client, *, deadline: Deadline | None = None, settings=None):
        steps: list = []
        step_token = execution_record._structured_steps.set(steps)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with (
                mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
                self.assertLogs(oci.__name__, level="INFO") as logs,
                bind_deadline(deadline if deadline is not None else Deadline(600)),
                execution_record._execution_step("生成"),
            ):
                result = oci._openai_parsed([], settings or self.settings, TextOutput, PROVIDER)
        finally:
            llm_usage.reset(usage_token)
            execution_record._structured_steps.reset(step_token)
        return result, steps[0].get("llm_usage", {}), logs.records

    def test_silence_is_cut_from_the_first_call_and_resent(self):
        """event が設定の秒（read timeout）来なかったら打ち切って送り直す（基準の学習は無く、最初の呼び出しから効く）。"""
        client = _Client(self.clock, [_script(300, 200.0, raise_at=5), _script(300, 200.0)])
        result, usage, records = self.call(client)
        self.assertEqual(result.text, "ok")
        self.assertEqual(usage["stall_resends"], 1)
        self.assertEqual(usage["incomplete_reasons"], {"stall_idle": 1})
        self.assertEqual(usage["calls"], 2)
        self.assertIn("停滞による送り直し 1 回", usage_line(usage))
        self.assertEqual([r.llm_status for r in records], ["failed", "completed"])
        self.assertEqual(records[0].llm_incomplete_reason, "stall_idle")
        self.assertEqual(records[1].llm_first_event_seconds, 0.1)
        self.assertEqual(client.read_timeouts, [120.0, 120.0])

    def test_slow_output_is_kept_and_its_rate_is_recorded(self):
        """遅いだけの出力は打ち切らない。速さは工程の記録に残す。"""
        client = _Client(self.clock, [_script(3000, 20.0)])
        result, usage, _records = self.call(client)
        self.assertEqual((result.text, len(client.streams)), ("ok", 1))
        self.assertNotIn("stall_resends", usage)
        self.assertEqual(usage["delta_rate_min"], 20.0)

    def test_idle_beyond_the_deadline_is_not_a_stall(self):
        """期限の残りが無音の秒より短い送信の timeout は、止まったのではなく期限切れ（送り直さない）。"""
        client = _Client(self.clock, [_script(300, 200.0, raise_at=5)])
        with self.assertRaises(httpx.ReadTimeout):
            self.call(client, deadline=Deadline(10))
        self.assertEqual(client.read_timeouts, [10])

    def test_incomplete_event_widens_the_budget_as_before(self):
        """streaming でも、出力の上限で未完了の応答は上限を 2 倍にして 1 回だけ送り直す（#987）。"""
        incomplete = SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"), usage=USAGE),
        )
        client = _Client(self.clock, [_script(10, 200.0, final=incomplete), _script(10, 200.0)])
        result, usage, _records = self.call(client)
        self.assertEqual(result.text, "ok")
        self.assertEqual(usage["output_retries"], 1)

    def test_failed_event_is_a_retryable_connection_error(self):
        failed = SimpleNamespace(type="response.failed", response=SimpleNamespace(error=SimpleNamespace(message="overloaded")))
        client = _Client(self.clock, [_script(10, 200.0, final=failed) for _ in range(1 + oci.LLM_CALL_MAX_RETRIES)])
        with self.assertRaises(oci.LlmStreamFailed) as raised:
            self.call(client)
        self.assertIn("overloaded", str(raised.exception))
        self.assertTrue(oci._is_retryable_error(raised.exception))
        self.assertEqual(len(client.streams), 1 + oci.LLM_CALL_MAX_RETRIES)  # 送り直しは共通の枠の上限まで

    def test_failed_event_is_resent_once(self):
        """失敗の event で終わった呼び出しは同じ要求を 1 回送り直す（#2814）。"""
        failed = SimpleNamespace(type="response.failed", response=SimpleNamespace(error=SimpleNamespace(message="overloaded")))
        client = _Client(self.clock, [_script(0, 200.0, final=failed), _script(10, 200.0)])
        result, usage, _records = self.call(client)
        self.assertEqual((result.text, len(client.streams)), ("ok", 2))
        self.assertEqual(usage["incomplete_reasons"], {"stream_error": 1})

    def test_sdk_stream_error_is_resent_once(self):
        """OpenAI SDK は stream の error の event を APIError として送出する。失敗の event と同じく送り直す（#2814）。"""
        import openai

        error = openai.APIError("upstream error", httpx.Request("POST", "https://stub"), body=None)
        client = _Client(self.clock, [[(0.1, SimpleNamespace(type="response.created")), (None, error)], _script(10, 200.0)])
        result, usage, _records = self.call(client)
        self.assertEqual((result.text, len(client.streams)), ("ok", 2))
        self.assertEqual(usage["incomplete_reasons"], {"stream_error": 1})

    def test_errors_while_reading_events_are_not_hidden(self):
        """event を読む途中の IndexError・AssertionError は実装の不具合なので、送り直さずそのまま送出する（#4197）。"""
        for error in (IndexError("ours"), AssertionError("ours")):
            client = _Client(self.clock, [[(None, error)], _script(10, 200.0)])
            with self.assertRaises(type(error)):
                self.call(client)
            self.assertEqual(len(client.streams), 1)

    def test_without_a_deadline_the_call_is_not_streamed(self):
        """回答の期限が無い文脈（取込など）は従来どおり `responses.parse` で受ける。"""
        client = _Client(self.clock, [])
        usage_token = llm_usage.set(LlmUsage())
        try:
            with mock.patch.object(oci, "_openai_client_for_provider", return_value=client), self.assertLogs(oci.__name__, level="INFO"):
                result = oci._openai_parsed([], self.settings, TextOutput, PROVIDER)
        finally:
            llm_usage.reset(usage_token)
        self.assertEqual((result.text, client.parse_calls, client.streams), ("parse", 1, []))

    def test_disabled_setting_is_not_streamed(self):
        settings = replace(self.settings, llm_stall_detection=False)
        client = _Client(self.clock, [])
        result, _usage, _records = self.call(client, settings=settings)
        self.assertEqual((result.text, client.parse_calls), ("parse", 1))


if __name__ == "__main__":
    unittest.main()
