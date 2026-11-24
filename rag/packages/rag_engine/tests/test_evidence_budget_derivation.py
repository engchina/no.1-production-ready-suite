"""根拠の文字予算は回答のモデルの入力の上限の 1/2（1 文字 = 1 token）で、超えたときだけ分割読取する（#5813）。"""

import json
from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation.evidence_selection import select_evidence

LIMIT_TOKENS = 20_000


def settings():
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, max_output_tokens=100) for key, value in base.llm_providers.items()}
    return replace(base, llm_providers=providers, answer_max_input_tokens=LIMIT_TOKENS)


def span(index, text):
    return {"evidence_id": f"E{index}", "source_id": f"s{index}", "text": text, "start": 0, "end": len(text)}


def select(spans, read):
    return select_evidence(
        spans,
        settings(),
        question="合成の質問",
        render=lambda values: json.dumps(values, ensure_ascii=False),
        evidence_text=lambda values: "".join(value["text"] for value in values),
        tag=list,
        system="合成の指示",
        schema_for=lambda _values: {"type": "object"},
        read=read,
        unusable_output=lambda error: False,
    )


def test_budget_is_half_of_the_answer_model_input_limit():
    assert settings().evidence_budget_chars == LIMIT_TOKENS // 2


def test_evidence_within_half_of_the_limit_is_one_generation_without_reading():
    # 日本語 9,600 字（UTF-8 では 28,800 バイト）でも、文字数で数えて上限の 1/2 に入る。
    spans = [span(index, "合成の本文。" * 400) for index in range(4)]
    selected, _, diagnostic = select(spans, lambda *args: pytest.fail("上限の 1/2 以内では分割読取しない"))
    assert diagnostic["batch_count"] == 0
    assert diagnostic["rendered_chars"] <= LIMIT_TOKENS // 2
    assert len(selected) == len(spans)


def test_evidence_over_half_of_the_limit_is_split_into_batches_of_at_most_half():
    spans = [span(index, "合成の本文。" * 400) for index in range(5)]
    calls: list[str] = []

    def read(system, prompt, schema):
        calls.append(prompt)
        return schema.model_validate({"findings": []})

    _, _, diagnostic = select(spans, read)
    assert diagnostic["batch_count"] == len(calls) >= 2
    # 各組の原文（行の番号を付ける前の本文）も上限の 1/2 まで。
    assert max(batch["input_chars"] for batch in diagnostic["batches"]) <= LIMIT_TOKENS // 2
