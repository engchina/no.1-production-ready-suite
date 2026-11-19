"""Messages API の部品の振る舞い（録画の応答で確かめる。実の API は呼ばない）。"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx2
import pytest
from conftest import API_KEY, MODEL_ID, FakeMessagesApi, error_body, recorded_stream
from pr_enterprise_ai.errors import enterprise_ai_failure_actions

from pr_anthropic_messages import (
    ANTHROPIC_EFFORT_BY_REASONING_EFFORT,
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    acreate_anthropic_message,
    anthropic_base_url,
    anthropic_effort,
    create_anthropic_message,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _request(**overrides: Any) -> AnthropicMessagesRequest:
    values: dict[str, Any] = {
        "model_id": MODEL_ID,
        "user_text": "質問 A に答えてください。",
        "max_output_tokens": 2048,
    }
    values.update(overrides)
    return AnthropicMessagesRequest(**values)


def _call(fake_api: FakeMessagesApi, request: AnthropicMessagesRequest, **kwargs: Any) -> Any:
    return create_anthropic_message(
        request, api_key=API_KEY, http_client=fake_api.client(), **kwargs
    )


# --------------------------------------------------------------------------- 要求の形


def test_text_request_is_streamed_with_system_and_without_thinking(
    fake_api: FakeMessagesApi,
) -> None:
    fake_api.reply_stream(recorded_stream())
    result = _call(fake_api, _request(system="Step 1: 役割"))

    request = fake_api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    # anthropic 2.x は API key を Authorization の Bearer で送る。
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    body = fake_api.body()
    assert body["stream"] is True
    assert body["model"] == MODEL_ID
    assert body["max_tokens"] == 2048
    assert body["system"] == "Step 1: 役割"
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "質問 A に答えてください。"}]}
    ]
    # 推論の量を指定しない（none / 空）ときは思考・effort・形式を送らない。
    assert "thinking" not in body
    assert "output_config" not in body
    # サンプリングの引数は送らない（新しいモデルは 400 にする）。
    assert not {"temperature", "top_p", "top_k"} & body.keys()
    assert result.text == "こんにちは"


@pytest.mark.parametrize(
    ("setting", "effort"),
    [("low", "low"), ("medium", "medium"), ("high", "high"), (" High ", "high")],
    ids=["low", "medium", "high", "normalized"],
)
def test_reasoning_effort_maps_to_adaptive_thinking_and_effort(
    fake_api: FakeMessagesApi, setting: str, effort: str
) -> None:
    fake_api.reply_stream(recorded_stream())
    _call(fake_api, _request(reasoning_effort=setting))
    body = fake_api.body()
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": effort}


def test_effort_table_covers_settings_choices() -> None:
    # pr_system_settings の選択肢（none / low / medium / high）は全部写せる。
    assert anthropic_effort("none") is None
    assert anthropic_effort("") is None
    assert {anthropic_effort(value) for value in ("low", "medium", "high")} == {
        "low",
        "medium",
        "high",
    }
    assert set(ANTHROPIC_EFFORT_BY_REASONING_EFFORT.values()) - {None} == {
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }
    with pytest.raises(ValueError):
        anthropic_effort("minimal")


def test_structured_output_uses_output_config_format(fake_api: FakeMessagesApi) -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    fake_api.reply_stream(recorded_stream(blocks=({"type": "text", "text": '{"answer": "B"}'},)))
    result = _call(fake_api, _request(response_schema=schema, reasoning_effort="low"))

    assert fake_api.body()["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": schema},
    }
    assert json.loads(result.text) == {"answer": "B"}


def test_images_are_sent_as_base64_blocks_before_text(fake_api: FakeMessagesApi) -> None:
    data_url = "data:image/png;base64," + base64.standard_b64encode(PNG_BYTES).decode()
    fake_api.reply_stream(recorded_stream(blocks=({"type": "text", "text": "画像の文字"},)))
    _call(
        fake_api,
        _request(
            user_text="画像の文字を読んでください。",
            images=(
                AnthropicImage.from_data_url(data_url),
                AnthropicImage.from_bytes("image/jpeg", b"jpg"),
            ),
        ),
    )
    content = fake_api.body()["messages"][0]["content"]
    assert [part["type"] for part in content] == ["image", "image", "text"]
    assert content[0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(PNG_BYTES).decode(),
    }
    assert content[1]["source"]["media_type"] == "image/jpeg"


@pytest.mark.parametrize(
    "data_url",
    ["data:image/bmp;base64,AAAA", "https://example.com/a.png", "data:image/png;base64,@@@"],
    ids=["unsupported-type", "not-data-url", "broken-base64"],
)
def test_invalid_images_are_rejected_before_calling(data_url: str) -> None:
    with pytest.raises(ValueError):
        AnthropicImage.from_data_url(data_url)


@pytest.mark.parametrize(
    ("endpoint", "url"),
    [
        ("", "https://api.anthropic.com/v1/messages"),
        (
            "  https://gateway.example.com/anthropic/ ",
            "https://gateway.example.com/anthropic/v1/messages",
        ),
    ],
    ids=["sdk-default", "custom-base-url"],
)
def test_endpoint_url_is_sdk_default_or_base_url(
    fake_api: FakeMessagesApi, endpoint: str, url: str
) -> None:
    fake_api.reply_stream(recorded_stream())
    _call(fake_api, _request(), endpoint_url=endpoint)
    assert str(fake_api.requests[0].url) == url
    assert anthropic_base_url(endpoint) == (endpoint.strip().rstrip("/") or None)


def test_empty_api_key_fails_without_calling(fake_api: FakeMessagesApi) -> None:
    with pytest.raises(AnthropicMessagesError) as caught:
        create_anthropic_message(_request(), api_key="  ", http_client=fake_api.client())
    assert caught.value.kind == "authentication"
    assert caught.value.configuration_error
    assert fake_api.requests == []


@pytest.mark.parametrize(
    "overrides",
    [{"model_id": " "}, {"max_output_tokens": 0}, {"user_text": ""}, {"reasoning_effort": "x"}],
    ids=["no-model", "no-budget", "no-input", "bad-effort"],
)
def test_invalid_requests_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _request(**overrides)


# --------------------------------------------------------------------------- 応答の写し方


def test_text_and_usage_skip_thinking_blocks(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_stream(
        recorded_stream(
            blocks=(
                {"type": "thinking"},
                {"type": "text", "text": "回答の"},
                {"type": "text", "text": "本文"},
            ),
            input_tokens=100,
            cache_read_input_tokens=50,
            cache_creation_input_tokens=10,
            output_tokens=70,
            thinking_tokens=30,
        ),
        request_id="req_abc",
    )
    result = _call(fake_api, _request(reasoning_effort="medium"))

    assert result.text == "回答の本文"
    assert result.stop_reason == "end_turn"
    assert result.model == MODEL_ID
    assert result.request_id == "req_abc"
    assert result.usage.input_tokens == 100
    assert result.usage.total_input_tokens == 160
    assert result.usage.thinking_tokens == 30
    assert result.usage.as_responses_usage() == {
        "input_tokens": 160,
        "output_tokens": 70,
        "total_tokens": 230,
        "input_tokens_details": {"cached_tokens": 50},
        "output_tokens_details": {"reasoning_tokens": 30},
    }


def test_usage_without_thinking_details(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_stream(recorded_stream(thinking_tokens=None))
    usage = _call(fake_api, _request()).usage
    assert usage.thinking_tokens is None
    assert "output_tokens_details" not in usage.as_responses_usage()


def test_refusal_is_classified(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_stream(
        recorded_stream(
            blocks=(),
            stop_reason="refusal",
            stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"},
        )
    )
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    error = caught.value
    assert error.kind == "refusal"
    assert error.error_code == "ENTERPRISE_AI_REFUSED"
    assert error.refusal_category == "cyber"
    assert error.provider_message == "declined"
    assert not error.retryable
    assert "category=cyber" in str(error)
    assert MODEL_ID in str(error)


def test_max_tokens_is_incomplete_output_limit(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_stream(
        recorded_stream(
            blocks=({"type": "text", "text": "途中まで"},),
            stop_reason="max_tokens",
            output_tokens=2048,
            thinking_tokens=2000,
        )
    )
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    error = caught.value
    assert error.kind == "incomplete"
    assert error.error_code == "ENTERPRISE_AI_OUTPUT_INCOMPLETE"
    assert error.incomplete is not None
    # 製品の「上限を広げて送り直す」判定（output_limit_reached）がそのまま使える。
    assert error.incomplete.output_limit_reached
    assert error.incomplete.max_output_tokens == 2048
    assert error.incomplete.output_tokens == 2048
    assert error.incomplete.reasoning_tokens == 2000
    assert error.usage is not None and error.usage.output_tokens == 2048
    assert "出力の上限" in str(error)


def test_context_window_stop_is_incomplete_but_not_output_limit(
    fake_api: FakeMessagesApi,
) -> None:
    fake_api.reply_stream(recorded_stream(stop_reason="model_context_window_exceeded"))
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    assert caught.value.kind == "incomplete"
    assert caught.value.incomplete is not None
    assert caught.value.incomplete.reason == "model_context_window_exceeded"
    assert not caught.value.incomplete.output_limit_reached


def test_empty_text_is_response_format_failure(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_stream(recorded_stream(blocks=({"type": "thinking"},)))
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    assert caught.value.kind == "response_format"


# --------------------------------------------------------------------------- 失敗の写し方


@pytest.mark.parametrize(
    ("status", "error_type", "message", "kind", "retryable"),
    [
        (400, "invalid_request_error", "max_tokens: too large", "request", False),
        (401, "authentication_error", "invalid x-api-key", "authentication", False),
        (403, "permission_error", "not allowed", "authorization", False),
        (404, "not_found_error", f"model: {MODEL_ID}", "model_not_found", False),
        (404, "not_found_error", "Not Found", "request", False),
        (413, "request_too_large", "too large", "request", False),
        (429, "rate_limit_error", "rate limited", "rate_limit", True),
        (500, "api_error", "internal", "unavailable", True),
        (529, "overloaded_error", "overloaded", "unavailable", True),
    ],
    ids=[
        "400",
        "401",
        "403",
        "404-model",
        "404-path",
        "413",
        "429",
        "500",
        "529",
    ],
)
def test_http_failures_are_classified(
    fake_api: FakeMessagesApi,
    status: int,
    error_type: str,
    message: str,
    kind: str,
    retryable: bool,
) -> None:
    fake_api.reply_error(status, error_body(error_type, message))
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    error = caught.value
    assert error.kind == kind
    assert error.status_code == status
    assert error.retryable is retryable
    assert error.request_id == "req_err"
    assert message in error.provider_message
    assert f"HTTP {status}" in str(error)
    assert error.http_failure is not None
    assert error.http_failure.status_code == status
    # 製品の案内の文（pr_enterprise_ai）に渡せる。
    assert enterprise_ai_failure_actions(error.http_failure, model_id=MODEL_ID)
    # SDK の自動の再試行は既定で使わない（1 回だけ送る）。
    assert len(fake_api.requests) == 1


def test_sdk_retries_only_when_asked(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_error(429, error_body("rate_limit_error", "rate limited"))
    fake_api.reply_stream(recorded_stream())
    # retry-after の待ちを短くするため、応答の header を付けない既定の待ち（約 0.5 秒）で送り直す。
    result = _call(fake_api, _request(), max_retries=1)
    assert result.text == "こんにちは"
    assert len(fake_api.requests) == 2


def test_timeout_and_connection_failures(fake_api: FakeMessagesApi) -> None:
    fake_api.raise_error(httpx2.ReadTimeout("timed out"))
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    assert caught.value.kind == "timeout"
    assert caught.value.retryable
    assert caught.value.status_code is None
    assert caught.value.http_failure is None

    fake_api.raise_error(httpx2.ConnectError("refused"))
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    assert caught.value.kind == "connection"
    assert caught.value.retryable


def test_mid_stream_error_event_is_classified(fake_api: FakeMessagesApi) -> None:
    events = recorded_stream()[:2] + [
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    ]
    fake_api.reply_stream(events)
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    assert caught.value.kind == "unavailable"
    assert caught.value.retryable


def test_api_key_never_appears_in_errors(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_error(
        401, error_body("authentication_error", f"invalid key {API_KEY} authorization: {API_KEY}")
    )
    with pytest.raises(AnthropicMessagesError) as caught:
        _call(fake_api, _request())
    error = caught.value
    assert API_KEY not in str(error)
    assert API_KEY not in error.provider_message
    assert API_KEY not in repr(error.args)


# --------------------------------------------------------------------------- 非同期


async def test_async_call_matches_sync(fake_api: FakeMessagesApi) -> None:
    fake_api.reply_stream(recorded_stream(blocks=({"type": "text", "text": "非同期"},)))
    result = await acreate_anthropic_message(
        _request(reasoning_effort="high"),
        api_key=API_KEY,
        http_client=fake_api.async_client(),
    )
    assert result.text == "非同期"
    assert fake_api.body()["output_config"] == {"effort": "high"}

    fake_api.reply_error(429, error_body("rate_limit_error", "rate limited"))
    with pytest.raises(AnthropicMessagesError) as caught:
        await acreate_anthropic_message(
            _request(), api_key=API_KEY, http_client=fake_api.async_client()
        )
    assert caught.value.kind == "rate_limit"
