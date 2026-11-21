"""完全な生成入力の独立した上限。"""

import json

import pytest

from pr_backend_core.generation_input import (
    GenerationInputLimitError,
    UnmeasuredGenerationInputError,
    complete_input_measurement,
    reported_input_tokens,
)


@pytest.mark.parametrize(("total", "fits"), [(95_999, True), (96_000, True), (96_001, False)])
def test_limit_counts_instruction_schema_and_escaped_content(total, fits):
    request = {
        "model": "model-a",
        "instructions": "元の指示",
        "input": [{"role": "user", "content": '引用 " と改行\n'}],
        "text": {"format": {"type": "json_schema", "schema": {"type": "object"}}},
    }
    remaining = total - complete_input_measurement(request).estimated_tokens
    request["input"][0]["content"] += "x" * remaining
    m = complete_input_measurement(request)
    assert m.estimated_tokens == total
    assert m.fits is fits
    if fits:
        m.validate()
    else:
        with pytest.raises(GenerationInputLimitError) as exc:
            m.validate()
        assert exc.value.limit_tokens == 96_000
    assert m.wire_bytes == len(
        json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    )


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
        complete_input_measurement(payload)
    assert complete_input_measurement(payload, external_tokens=500).media_tokens == 500


def test_caller_executed_tool_definitions_are_in_the_input_budget():
    tools = [
        {"type": "function", "name": "fn-a", "parameters": {"type": "object"}},
        {"name": "fn-b", "input_schema": {"type": "object"}},
    ]
    assert (
        complete_input_measurement({"tools": tools}).wire_bytes
        > complete_input_measurement({}).wire_bytes
    )


def test_schema_is_counted_even_when_it_describes_media():
    schema = {"const": {"type": "image", "description": "x" * 80_000}}
    measured = complete_input_measurement({"input": "a", "text": {"format": {"schema": schema}}})
    assert measured.media_tokens == 0
    assert not measured.fits


def test_usage_distinguishes_cached_unknown_and_zero():
    measured = complete_input_measurement({"input": "a"})
    assert measured.usage_status(reported_input_tokens(None)) == "unreported"
    assert reported_input_tokens({"input_tokens": None, "prompt_tokens": 0}) == 0
    assert measured.usage_status(reported_input_tokens({"input_tokens": 0})) == "within_limit"
    assert (
        measured.usage_status(
            reported_input_tokens(
                {"input_tokens": 96_001, "input_tokens_details": {"cached_tokens": 90_000}}
            )
        )
        == "over_limit"
    )
