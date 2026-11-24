"""完全な生成入力の独立した上限。"""

import json

import pytest

from pr_backend_core.generation_input import (
    GenerationInputLimitError,
    UnmeasuredGenerationInputError,
    complete_input_measurement,
    reported_input_tokens,
)

# 合成の登録モデルの入力の上限（tokens）。
LIMIT = 128_000


@pytest.mark.parametrize(
    ("total", "fits"),
    [
        (LIMIT - 1, True),
        (LIMIT, True),
        (LIMIT + 1, False),
    ],
)
def test_limit_counts_instruction_schema_and_escaped_content(total, fits):
    request = {
        "model": "model-a",
        "instructions": "元の指示",
        "input": [{"role": "user", "content": '引用 " と改行\n'}],
        "text": {"format": {"type": "json_schema", "schema": {"type": "object"}}},
    }
    remaining = total - complete_input_measurement(request, limit_tokens=LIMIT).estimated_tokens
    request["input"][0]["content"] += "x" * remaining
    m = complete_input_measurement(request, limit_tokens=LIMIT)
    assert m.estimated_tokens == total
    assert m.fits is fits
    if fits:
        m.validate()
    else:
        with pytest.raises(GenerationInputLimitError) as exc:
            m.validate()
        assert exc.value.limit_tokens == LIMIT
    assert m.wire_chars == len(json.dumps(request, ensure_ascii=False, separators=(",", ":")))


def test_registered_image_estimate_and_external_context_are_additive():
    part = {"type": "input_image", "image_url": "data:image/png;base64," + "X" * 90_000}
    request = {"input": [{"role": "user", "content": [part]}], "instructions": "指示", "model": "m"}
    m = complete_input_measurement(
        request, limit_tokens=8000, image_tokens=1000, external_tokens=80
    )
    assert m.media_tokens == 1080
    assert m.margin_tokens == 2_000
    assert m.fits


@pytest.mark.parametrize(
    "payload",
    [
        {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "id-a"}]}]},
        {"previous_response_id": "id-b"},
        {"tools": [{"type": "file_search", "vector_store_ids": ["id-c"]}]},
        {"tools": [{"type": "web_search_20250305"}]},
        {"tools": [{"type": "code_interpreter", "container": {"file_ids": ["id-d"]}}]},
    ],
)
def test_unpriced_external_input_requires_an_estimate(payload):
    with pytest.raises(UnmeasuredGenerationInputError):
        complete_input_measurement(payload, limit_tokens=LIMIT)
    assert (
        complete_input_measurement(payload, limit_tokens=LIMIT, external_tokens=500).media_tokens
        == 500
    )


def test_caller_executed_tool_definitions_are_in_the_input_budget():
    tools = [
        {"type": "function", "name": "fn-a", "parameters": {"type": "object"}},
        {"name": "fn-b", "input_schema": {"type": "object"}},
    ]
    assert (
        complete_input_measurement({"tools": tools}, limit_tokens=LIMIT).wire_chars
        > complete_input_measurement({}, limit_tokens=LIMIT).wire_chars
    )


def test_schema_is_counted_even_when_it_describes_media():
    schema = {"const": {"type": "image", "description": "x" * LIMIT}}
    measured = complete_input_measurement(
        {"input": "a", "text": {"format": {"schema": schema}}}, limit_tokens=LIMIT
    )
    assert measured.media_tokens == 0
    assert not measured.fits


def test_usage_distinguishes_cached_unknown_and_zero():
    measured = complete_input_measurement({"input": "a"}, limit_tokens=LIMIT)
    assert measured.usage_status(reported_input_tokens(None)) == "unreported"
    assert reported_input_tokens({"input_tokens": None, "prompt_tokens": 0}) == 0
    assert measured.usage_status(reported_input_tokens({"input_tokens": 0})) == "within_limit"
    assert (
        measured.usage_status(
            reported_input_tokens(
                {
                    "input_tokens": LIMIT + 1,
                    "input_tokens_details": {"cached_tokens": 90_000},
                }
            )
        )
        == "over_limit"
    )


def test_estimate_counts_characters_not_bytes():
    """日本語の 1 文字は UTF-8 で 3 バイトでも 1 token と数える（#5813）。"""
    m = complete_input_measurement({"input": "あ" * 1000}, limit_tokens=LIMIT)
    body = json.dumps({"input": "あ" * 1000}, ensure_ascii=False, separators=(",", ":"))
    assert m.wire_chars == len(body) < len(body.encode())


def test_registered_model_limit_is_used_without_a_fixed_cap():
    """登録モデルの上限をそのまま使い、固定の上限で切り詰めない（#5813）。"""
    assert complete_input_measurement({"input": "a"}, limit_tokens=96_000).limit_tokens == 96_000
    wide = complete_input_measurement({"input": "a"}, limit_tokens=1_000_000)
    assert wide.limit_tokens == 1_000_000
    assert wide.margin_tokens == 250_000
    # 上限の 3/4 までの入力は収まる（固定の 256K の上限を超えても）。
    big = complete_input_measurement({"input": "x" * 700_000}, limit_tokens=1_000_000)
    assert big.fits
