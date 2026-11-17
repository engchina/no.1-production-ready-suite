"""回答文の生成の段の出力の上限（#5368）を、決定論のスタブで確かめる。

実効の上限は min（登録モデルの出力の上限, オンライン生成の出力予算, 段の上限）。上限で未完了になったときの扱い
（オンライン生成は送り直さずに失敗）は変えない。
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from rag_engine.adapters import oci
from rag_engine.config import (
    DEFAULT_ANSWER_GENERATION_MAX_OUTPUT_TOKENS,
    get_settings,
    settings_for_answer_generation,
)
from rag_engine.generation import grounded
from rag_engine.generation.answering import AnswerContext, AnswerRecord, _grounded_spans
from rag_engine.models.llm import TextOutput

USAGE = SimpleNamespace(input_tokens=100, output_tokens=50, input_tokens_details=None, output_tokens_details=None)


def _provider(capacity: int) -> SimpleNamespace:
    return SimpleNamespace(model="stub-model", label="stub", reasoning_effort="", max_output_tokens=capacity)


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


def _completed():
    return SimpleNamespace(status="completed", output_text=TextOutput(text="ok").model_dump_json(), usage=USAGE)


def _incomplete():
    return SimpleNamespace(status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"), usage=USAGE)


def _settings(**env: str):
    return get_settings(environ=env, dotenv_path=None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_ANSWER_GENERATION_MAX_OUTPUT_TOKENS),
        ("8192", 8192),
        ("0", 0),
        ("-1", DEFAULT_ANSWER_GENERATION_MAX_OUTPUT_TOKENS),
        ("abc", DEFAULT_ANSWER_GENERATION_MAX_OUTPUT_TOKENS),
    ],
    ids=["unset", "positive", "zero_disables", "negative_default", "non_integer_default"],
)
def test_env_value_is_read_with_default_for_unset_and_invalid(value, expected):
    env = {} if value is None else {"RAG_ENGINE_ANSWER_GENERATION_MAX_OUTPUT_TOKENS": value}
    assert _settings(**env).answer_generation_max_output_tokens == expected


def test_stage_cap_lowers_only_the_online_budget():
    base = replace(_settings(), online_max_output_tokens=65536, answer_generation_max_output_tokens=16384)
    capped = settings_for_answer_generation(base)
    assert capped.online_max_output_tokens == 16384
    assert capped.answer_max_tokens == base.answer_max_tokens
    assert base.online_max_output_tokens == 65536


@pytest.mark.parametrize("cap", [0, 65536, 100000], ids=["disabled", "equal_to_online", "above_online"])
def test_stage_cap_keeps_settings_when_disabled_or_not_smaller(cap):
    base = replace(_settings(), online_max_output_tokens=65536, answer_generation_max_output_tokens=cap)
    assert settings_for_answer_generation(base) is base


def test_non_settings_are_passed_through():
    assert settings_for_answer_generation(None) is None


@pytest.mark.parametrize(
    ("cap", "capacity", "expected"),
    [(16384, 32768, 16384), (16384, 8192, 8192), (0, 32768, 32768)],
    ids=["stage_cap_wins", "model_capacity_wins", "disabled_uses_model_capacity"],
)
def test_effective_request_budget_is_the_minimum(monkeypatch, cap, capacity, expected):
    client = _Client([_completed()])
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
    settings = settings_for_answer_generation(replace(_settings(), answer_generation_max_output_tokens=cap))
    assert oci._openai_parsed([], settings, TextOutput, _provider(capacity)).text == "ok"
    assert client.budgets == [expected]


def test_incomplete_at_stage_cap_fails_without_widening(monkeypatch):
    client = _Client([_incomplete(), _completed()])
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_: client)
    monkeypatch.setattr(oci.time, "sleep", lambda _: None)
    settings = settings_for_answer_generation(replace(_settings(), answer_generation_max_output_tokens=4096))
    with pytest.raises(oci.IncompleteResponse):
        oci._openai_parsed([], settings, TextOutput, _provider(32768))
    assert client.budgets == [4096]


class _Stop(Exception):
    pass


@pytest.mark.parametrize("with_images", [False, True], ids=["text", "images"])
def test_run_round_passes_the_stage_cap_only_to_answer_generation(with_images):
    text = "合成の資料の本文。\n手順 A を行います。"
    record = AnswerRecord(
        id="r0",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="合成の資料.pdf",
        chunk_level="parent",
        chunk_id="r0",
        source_run_id="run",
        metadata={"section_path": ["合成の資料", "節 A"]},
    )
    question = "手順 A を教えてください"
    spans = _grounded_spans(question, AnswerContext(records=[record], text=text), (), None)
    settings = replace(_settings(), online_max_output_tokens=65536, answer_generation_max_output_tokens=12000)
    seen: list = []

    def parse(*args, **_options):
        seen.append(next(value for value in args if hasattr(value, "online_max_output_tokens")))
        raise _Stop

    with pytest.raises(_Stop):
        grounded.run_round(
            question,
            spans,
            settings,
            prompt="p",
            image_paths=["image.png"] if with_images else [],
            provider_id=None,
            parse_text=parse,
            parse_images=parse,
        )
    assert [value.online_max_output_tokens for value in seen] == [12000]
    assert settings.online_max_output_tokens == 65536
