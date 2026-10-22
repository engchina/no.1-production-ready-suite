"""出力の暴走（同じ並びの繰り返し・空白の連続）の早い打ち切りと送り直し（#2966）。

streaming の client と時計を差し替え、出力の文（``response.output_text.delta``）を台本にする。出力の上限まで待たずに
打ち切り、推論の量を下げて 1 回だけ送り直すこと、正当な長い出力（表・一覧・JSON の配列）は打ち切らないことを確かめる。
"""

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation import execution_record
from rag_engine.models.llm import TextOutput
from rag_engine.resources.deadline import Deadline, bind_deadline
from rag_engine.resources.llm_usage import LlmUsage, llm_usage, usage_line
from tests.test_llm_stall_resend import _Client, _Clock, _completed

PROVIDER = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="medium", base_url="https://stub")


def _text_script(chunks: list[str], *, rate: float = 200.0, final=None):
    """出力の文の delta の列（送信からの秒, event）。最後に完了の event。"""
    events = [(0.1, SimpleNamespace(type="response.created"))]
    for index, chunk in enumerate(chunks):
        events.append((0.1 + index / rate, SimpleNamespace(type="response.output_text.delta", delta=chunk)))
    done = final if final is not None else SimpleNamespace(type="response.completed", response=_completed())
    events.append((0.1 + len(chunks) / rate, done))
    return events


class _RecordingClient(_Client):
    """送信ごとの推論の量を記録する。"""

    def __init__(self, clock, scripts) -> None:
        super().__init__(clock, scripts)
        self.efforts: list[str] = []

    def _stream(self, **kwargs):
        self.efforts.append(str((kwargs.get("reasoning") or {}).get("effort") or ""))
        return super()._stream(**kwargs)


def _table_rows(count: int) -> list[str]:
    return [f"| 項目{index} | 値{index * 7 % 1000:03d} | 記録{index}と画面を照合 |\n" for index in range(count)]


class RunawayOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        oci._stream_baselines.clear()
        oci._output_rates.clear()
        self.clock = _Clock()
        patcher = mock.patch("time.monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(oci._stream_baselines.clear)
        self.addCleanup(oci._output_rates.clear)
        self.settings = replace(
            get_settings(environ={"RAG_ENGINE_LLM_STALL_DETECTION": "1", "RAG_ENGINE_LLM_RUNAWAY_DETECTION": "1"}, dotenv_path=None),
            answer_max_tokens=32768,
            llm_request_timeout_seconds=600,
        )

    def call(self, client: _Client, settings=None):
        steps: list = []
        step_token = execution_record._structured_steps.set(steps)
        usage_token = llm_usage.set(LlmUsage())
        try:
            with (
                mock.patch.object(oci, "_openai_client_for_provider", return_value=client),
                self.assertLogs(oci.__name__, level="INFO") as logs,
                bind_deadline(Deadline(600)),
                execution_record._execution_step("生成"),
            ):
                result = oci._openai_parsed([], settings or self.settings, TextOutput, PROVIDER)
        finally:
            llm_usage.reset(usage_token)
            execution_record._structured_steps.reset(step_token)
        self.step = steps[0]
        return result, steps[0].get("llm_usage", {}), logs.records

    def test_repeating_output_is_cut_early_and_resent_with_lower_effort(self):
        """同じ並びの繰り返しは、出力の上限まで待たずに打ち切り、推論の量を下げて送り直す。"""
        runaway = ['{"text": "答え', *(["、同じ説明"] * 20000)]
        client = _RecordingClient(self.clock, [_text_script(runaway), _text_script(['{"text": "ok"}'])])
        started = self.clock.now
        result, usage, records = self.call(client)
        self.assertEqual(result.text, "ok")
        self.assertEqual(len(client.streams), 2)
        self.assertTrue(client.streams[0].closed)
        self.assertEqual(client.efforts, ["medium", "low"])
        # 20,000 回の繰り返し（100 秒分）を最後まで受けず、最初の数千文字で打ち切る。
        self.assertLess(self.clock.now - started, 5.0)
        self.assertEqual(usage["runaway_resends"], 1)
        self.assertEqual(usage["incomplete_reasons"], {"runaway_repetition": 1})
        self.assertEqual(usage["calls"], 2)
        self.assertIn("出力の繰り返しを打ち切った送り直し 1 回", usage_line(usage))
        # 工程の記録は、呼び出しの回数（この試験では直接呼ぶので 0）と API への送信の回数を分けて残す。
        self.assertEqual((self.step["llm_calls"], self.step["llm_sends"]), (0, 2))
        self.assertEqual([record.llm_status for record in records], ["failed", "completed"])

    def test_whitespace_run_is_cut(self):
        client = _RecordingClient(self.clock, [_text_script(['{"text": "a', *(["\n   "] * 2000)]), _text_script(["ok"])])
        _result, usage, _records = self.call(client)
        self.assertEqual(usage["incomplete_reasons"], {"runaway_whitespace": 1})

    def test_long_tables_lists_and_json_arrays_are_not_cut(self):
        """行ごとに値が変わる長い出力は、同じ形の行が続いても打ち切らない。"""
        items = [json.dumps({"kind": "rule", "text": f"手順{index}: 画面{index % 9}で設定{index}を確認する"}) + "," for index in range(800)]
        for chunks in (_table_rows(1500), items, [f"{index}. 確認する。\n" for index in range(2000)]):
            client = _RecordingClient(self.clock, [_text_script(chunks)])
            result, usage, _records = self.call(client)
            self.assertEqual(result.text, "ok")
            self.assertEqual(len(client.streams), 1)
            self.assertNotIn("runaway_resends", usage)

    def test_resends_are_bounded(self):
        runaway = ["同じ説明。"] * 5000
        client = _RecordingClient(self.clock, [_text_script(runaway), _text_script(runaway)])
        with self.assertRaises(oci.LlmRunawayOutput):
            self.call(client)
        self.assertEqual(len(client.streams), 2)

    def test_detection_is_off_by_setting(self):
        settings = replace(self.settings, llm_runaway_detection=False)
        client = _RecordingClient(self.clock, [_text_script(["同じ説明。"] * 3000)])
        result, usage, _records = self.call(client, settings=settings)
        self.assertEqual((result.text, len(client.streams)), ("ok", 1))
        self.assertNotIn("runaway_resends", usage)


class RepeatingTailTest(unittest.TestCase):
    def test_periodic_tail_only(self):
        window = oci.RUNAWAY_WINDOW_CHARS + oci.RUNAWAY_MAX_PERIOD_CHARS
        self.assertTrue(oci.repeating_tail("前置き" + "abc、" * window))
        self.assertTrue(oci.repeating_tail("x" * window))
        self.assertFalse(oci.repeating_tail("".join(_table_rows(400))))
        self.assertFalse(oci.repeating_tail("abc、" * 10))  # 短い出力は見ない
