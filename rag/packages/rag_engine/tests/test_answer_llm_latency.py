"""回答の LLM の時間の短縮 (#1499) の検証。

回答文の生成の推論の量の解釈を、外部の I/O を置き換えて確かめる。
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_answer_generation import _answer_context, _record

import rag_engine.generation.answering as generation
from rag_engine.config import (
    ENTERPRISE_AI_LLM_PROVIDER,
    ENTERPRISE_AI_VISION_LLM_PROVIDER,
    LLM_PURPOSE_GENERATION,
    get_settings,
    llm_reasoning_effort_note,
    settings_for_llm_purpose,
)


def _settings(*, answer_effort="medium", vision_effort="medium", **overrides):
    """外部の .env を読まない設定。モデルの登録値（送る値）は provider の reasoning_effort。"""
    base = get_settings(
        environ={
            "OCI_ENTERPRISE_AI_ENDPOINT": "https://example.invalid/openai/v1",
            "OCI_ENTERPRISE_AI_API_KEY": "test-key",
            "OCI_ENTERPRISE_AI_DEFAULT_MODEL": "test.model",
            "OCI_ENTERPRISE_AI_VLM_MODEL": "test.vision",
            "OCI_ENTERPRISE_AI_REASONING_EFFORT": answer_effort,
            "OCI_ENTERPRISE_AI_VLM_REASONING_EFFORT": vision_effort,
        },
        dotenv_path=None,
    )
    return replace(base, **overrides)


def _efforts(settings):
    return {key: provider.reasoning_effort for key, provider in settings.llm_providers.items()}


class PurposeReasoningEffortTests(unittest.TestCase):
    """用途ごとの推論の量の解釈（none は送らない・登録値が none のモデルには送らない）。"""

    def test_env_values_are_normalized(self):
        settings = get_settings(environ={"RAG_ENGINE_GENERATION_REASONING_EFFORT": "model"}, dotenv_path=None)
        # 選択肢に無い値（model など）はモデルの登録値のまま。
        self.assertEqual(settings.generation_reasoning_effort, "")
        self.assertEqual(
            get_settings(environ={"RAG_ENGINE_GENERATION_REASONING_EFFORT": " High "}, dotenv_path=None).generation_reasoning_effort,
            "high",
        )
        self.assertEqual(get_settings(environ={}, dotenv_path=None).generation_reasoning_effort, "")

    def test_override_replaces_only_sent_efforts(self):
        settings = _settings(vision_effort="", generation_reasoning_effort="low")
        effective = settings_for_llm_purpose(settings, LLM_PURPOSE_GENERATION)
        # 登録値が送らない（none）の Vision モデルには送らない。
        self.assertEqual(_efforts(effective), {ENTERPRISE_AI_LLM_PROVIDER: "low", ENTERPRISE_AI_VISION_LLM_PROVIDER: ""})
        # 元の設定は変えない。
        self.assertEqual(_efforts(settings)[ENTERPRISE_AI_LLM_PROVIDER], "medium")
        omitted = settings_for_llm_purpose(replace(settings, generation_reasoning_effort="none"), LLM_PURPOSE_GENERATION)
        self.assertEqual(_efforts(omitted)[ENTERPRISE_AI_LLM_PROVIDER], "")
        # 設定でない stub はそのまま返す（差し替え実装のテストを壊さない）。
        stub = object()
        self.assertIs(settings_for_llm_purpose(stub, LLM_PURPOSE_GENERATION), stub)  # type: ignore[arg-type]

    def test_note_shows_sent_effort_and_its_source(self):
        settings = _settings()
        self.assertEqual(llm_reasoning_effort_note(settings, LLM_PURPOSE_GENERATION), "推論の量: medium（モデルの登録値）")
        self.assertEqual(
            llm_reasoning_effort_note(replace(settings, generation_reasoning_effort="none"), LLM_PURPOSE_GENERATION),
            "推論の量: 送らない（回答文の生成の設定）",
        )
        self.assertEqual(
            llm_reasoning_effort_note(_settings(answer_effort="", generation_reasoning_effort="high"), LLM_PURPOSE_GENERATION),
            "推論の量: 送らない（モデルの登録値が none）",
        )


class PurposeEffortCallSiteTests(unittest.TestCase):
    """各呼び出しが用途の推論の量で LLM を呼ぶ。"""

    def setUp(self):
        self.settings = _settings(generation_reasoning_effort="high")
        self.calls = []

    def record(self, reply):
        def parse(system, prompt, settings, schema, **options):
            self.calls.append((schema, settings.llm_providers[ENTERPRISE_AI_LLM_PROVIDER].reasoning_effort))
            return reply(schema) if callable(reply) else reply

        return parse

    def test_generation_and_audit_use_generation_effort(self):
        from rag_engine.generation import grounded

        seen = []

        def fake_round(question, spans, settings, **options):
            seen.append(settings.llm_providers[ENTERPRISE_AI_LLM_PROVIDER].reasoning_effort)
            raise RuntimeError("stop after the first round")

        context = _answer_context([_record("r1", "docling", 1, 1, "経費精算の申請方法")])
        with patch.object(grounded, "run_round", side_effect=fake_round), self.assertRaises(RuntimeError):
            generation.synthesize_grounded_answer("経費精算の申請方法は？", context, self.settings, image_prompt_mode="text_only")
        self.assertEqual(seen, ["high"])
