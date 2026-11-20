"""クォータナリ接続（Anthropic Claude の Messages API。#5546）のモデルを NL2SQL から呼ぶ。

SDK の HTTP（httpx2）を録画の応答（Messages API の SSE）に置き換え、実の API は呼ばない。
OpenAI 互換の接続の HTTP（httpx）は、クォータナリ接続のモデルでは呼ばないことを確かめる。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Iterable, Mapping
from typing import Any, cast

import anthropic
import httpx
import httpx2
import pytest
from pr_anthropic_messages import AnthropicMessagesError, anthropic_failure_actions
from pr_backend_core.bounded_calls import BoundedCallAborted

from app.features.nl2sql import enterprise_ai_client
from app.features.nl2sql.ai_calls import BoundedEnterpriseAiClient, make_ai_caller
from app.features.nl2sql.enterprise_ai_client import (
    EnterpriseAiDirectError,
    OciEnterpriseAiDirectClient,
    _anthropic_direct_error,
    enterprise_ai_configuration_failure_message,
    enterprise_ai_direct_provider,
)
from app.features.nl2sql.reverse_generation import REASONS, ReverseStageError, generate_stage
from app.features.nl2sql.structured_outputs import SqlOutput, TextOutput, response_format
from app.settings import EnterpriseAiConfiguredModel, Settings

CLAUDE = "claude-opus-5-5"
CLAUDE_VISION = "claude-vision-a"
API_KEY = "sk-ant-test-0000000000000000"
PRIMARY = "https://primary.example/openai/v1"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "oci_enterprise_ai_endpoint": PRIMARY,
        "oci_enterprise_ai_api_key": "sk-primary",
        "oci_enterprise_ai_quaternary_api_key": API_KEY,
        "oci_enterprise_ai_models": [
            EnterpriseAiConfiguredModel.model_validate(
                {
                    "model_id": CLAUDE,
                    "connection_id": "quaternary",
                    "reasoning_effort": "high",
                    "max_output_tokens": 4096,
                }
            ),
            EnterpriseAiConfiguredModel.model_validate(
                {
                    "model_id": CLAUDE_VISION,
                    "connection_id": "quaternary",
                    "vision_enabled": True,
                    "reasoning_effort": "none",
                    "max_output_tokens": 2048,
                }
            ),
            EnterpriseAiConfiguredModel(model_id="text-a"),
        ],
        "oci_enterprise_ai_default_text_model": CLAUDE,
        "oci_enterprise_ai_default_vision_model": CLAUDE_VISION,
        "oci_enterprise_ai_max_retries": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _sse(events: Iterable[Mapping[str, Any]]) -> bytes:
    lines: list[str] = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event, ensure_ascii=False)}", ""]
    return ("\n".join(lines) + "\n").encode()


def _stream(
    text: str,
    *,
    stop_reason: str = "end_turn",
    stop_details: Mapping[str, Any] | None = None,
    output_tokens: int = 40,
) -> httpx2.Response:
    """1 回の応答の録画（message_start → text の block → message_delta → message_stop）。"""
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": CLAUDE,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    ]
    if text:
        events += [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
            {"type": "content_block_stop", "index": 0},
        ]
    events += [
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "stop_details": dict(stop_details) if stop_details is not None else None,
            },
            "usage": {
                "output_tokens": output_tokens,
                "output_tokens_details": {"thinking_tokens": 7},
            },
        },
        {"type": "message_stop"},
    ]
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream", "request-id": "req_claude"},
        content=_sse(events),
    )


def _error(status: int, error_type: str, message: str) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers={"request-id": "req_err"},
        json={"type": "error", "error": {"type": error_type, "message": message}},
    )


class FakeMessagesApi:
    """POST /v1/messages の応答を順に返し、届いた要求を残す。"""

    def __init__(self) -> None:
        self.responses: list[httpx2.Response | Exception] = []
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int = -1) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.requests[index].content)
        return loaded


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> FakeMessagesApi:
    """SDK の client の HTTP を録画の応答に差し替え、OpenAI 互換の HTTP は失敗にする。"""
    fake = FakeMessagesApi()
    real_client = anthropic.Anthropic

    def client(**kwargs: Any) -> anthropic.Anthropic:
        kwargs["http_client"] = anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(fake.handle)
        )
        return real_client(**kwargs)

    # pr_anthropic_messages は呼ぶたびに `anthropic.Anthropic` を引く。
    monkeypatch.setattr(anthropic, "Anthropic", client)

    def openai_compatible(**_kwargs: Any) -> httpx.Client:
        raise AssertionError("クォータナリ接続のモデルで OpenAI 互換の HTTP を呼んだ")

    monkeypatch.setattr(httpx, "Client", openai_compatible)
    # 再試行の待ち（0.2 秒〜）を飛ばす。
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return fake


@pytest.fixture
def usages(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    recorded: list[Any] = []
    monkeypatch.setattr(
        enterprise_ai_client,
        "record_genai_usage",
        lambda _span, usage: recorded.append(usage),
    )
    return recorded


def _generate(settings: Settings, **kwargs: Any) -> str:
    values: dict[str, Any] = {
        "prompt": "質問 A",
        "context": "表 T の説明",
        "system_prompt": "## Step 1: SQL を作る",
        "response_format": response_format(SqlOutput),
    }
    values.update(kwargs)
    return OciEnterpriseAiDirectClient(settings).generate(**values)


def test_direct_provider_follows_the_model_connection() -> None:
    """直接の生成の engine_meta の provider・runtime は、モデルの接続で決める（#5593）。"""
    claude_client = OciEnterpriseAiDirectClient(_settings())
    oci_client = OciEnterpriseAiDirectClient(
        _settings(oci_enterprise_ai_default_text_model="text-a")
    )
    wrapped = BoundedEnterpriseAiClient(claude_client, cast(Any, None))

    assert enterprise_ai_direct_provider(claude_client, CLAUDE) == "anthropic_messages"
    assert enterprise_ai_direct_provider(wrapped, CLAUDE) == "anthropic_messages"
    # ほかの接続（OpenAI 互換）の値は変えない。
    assert enterprise_ai_direct_provider(oci_client, "text-a") == "oci_enterprise_ai"
    assert enterprise_ai_direct_provider(object(), CLAUDE) == "oci_enterprise_ai"


# --------------------------------------------------------------------------- 成功


def test_structured_generate_calls_messages_api_with_same_inputs(
    claude: FakeMessagesApi, usages: list[Any]
) -> None:
    claude.responses.append(_stream('{"sql": "SELECT 1 FROM DUAL", "explanation": "e"}'))

    raw = _generate(_settings())

    assert json.loads(raw) == {"sql": "SELECT 1 FROM DUAL", "explanation": "e"}
    request = claude.requests[0]
    # Endpoint URL が空なら Anthropic の公式の API。API key はクォータナリ接続の値。
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    body = claude.body()
    assert body["model"] == CLAUDE
    assert body["stream"] is True
    # 指示（schema の項目の説明の Step を含む）は system、文脈と質問は user の本文。
    assert body["system"].startswith("## Step 1: SQL を作る")
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "表 T の説明\n\n質問:\n質問 A"}]}
    ]
    # 登録モデルの推論の量（high）は adaptive の思考と effort。サンプリングの引数は送らない。
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"
    assert "temperature" not in body
    schema = body["output_config"]["format"]["schema"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert set(schema["properties"]) == {"sql", "explanation"}
    assert schema["additionalProperties"] is False
    assert 0 < body["max_tokens"] <= 4096
    # usage は Responses API の形で記録する（cache の読み込みを含む入力の全体）。
    assert usages == [
        {
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens_details": {"reasoning_tokens": 7},
        }
    ]


def test_free_text_generate_and_endpoint_override(claude: FakeMessagesApi) -> None:
    claude.responses.append(_stream("回答の本文"))

    raw = _generate(
        _settings(oci_enterprise_ai_quaternary_endpoint="https://claude-proxy.example/"),
        response_format=None,
    )

    assert raw == "回答の本文"
    assert str(claude.requests[0].url) == "https://claude-proxy.example/v1/messages"
    assert "output_config" in claude.body()  # effort だけ（format は送らない）
    assert "format" not in claude.body()["output_config"]


def test_openai_compatible_template_and_response_path_are_not_used(
    claude: FakeMessagesApi,
) -> None:
    # payload template・response path は OpenAI 互換 API 用。Messages API の接続では使わない。
    claude.responses.append(_stream('{"sql": "SELECT 1 FROM DUAL", "explanation": ""}'))

    raw = _generate(
        _settings(
            oci_enterprise_ai_llm_payload_template='{"model": "${model}", "prompt": "${prompt}"}',
            oci_enterprise_ai_llm_response_path="/choices/0/message/content",
        )
    )

    assert json.loads(raw)["sql"] == "SELECT 1 FROM DUAL"
    assert claude.body()["messages"][0]["role"] == "user"


def test_image_call_sends_base64_block_before_text(claude: FakeMessagesApi) -> None:
    claude.responses.append(_stream('{"text": "画像の文字"}'))

    raw = OciEnterpriseAiDirectClient(_settings()).generate_from_image(
        PNG_BYTES, "画像を読む", mime_type="image/png", response_format=response_format(TextOutput)
    )

    assert json.loads(raw) == {"text": "画像の文字"}
    body = claude.body()
    assert body["model"] == CLAUDE_VISION
    image, text = body["messages"][0]["content"]
    assert image == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(PNG_BYTES).decode(),
        },
    }
    assert text["type"] == "text" and text["text"].startswith("画像を読む")
    # 推論の量 none は思考・effort を送らない（モデルの既定）。
    assert "thinking" not in body
    assert "effort" not in body["output_config"]


# --------------------------------------------------------------------------- 未完了の送り直し


def test_image_call_lowers_effort_after_max_tokens(claude: FakeMessagesApi) -> None:
    # 出力の上限で未完了なら、Responses API の経路と同じく推論の量を 1 段下げて送り直す。
    claude.responses += [
        _stream('{"text": "途中', stop_reason="max_tokens", output_tokens=2048),
        _stream('{"text": "全文"}'),
    ]

    raw = OciEnterpriseAiDirectClient(
        _settings(
            oci_enterprise_ai_models=[
                EnterpriseAiConfiguredModel.model_validate(
                    {
                        "model_id": CLAUDE_VISION,
                        "connection_id": "quaternary",
                        "vision_enabled": True,
                        "reasoning_effort": "high",
                        "max_output_tokens": 2048,
                    }
                ),
            ],
            oci_enterprise_ai_default_text_model="",
        )
    ).generate_from_image(PNG_BYTES, "画像を読む", response_format=response_format(TextOutput))

    assert json.loads(raw) == {"text": "全文"}
    assert claude.body(0)["output_config"]["effort"] == "high"
    assert claude.body(1)["output_config"]["effort"] == "medium"
    assert claude.body(0)["max_tokens"] == claude.body(1)["max_tokens"] == 2048


def test_strict_generate_lowers_effort_after_max_tokens(claude: FakeMessagesApi) -> None:
    claude.responses += [
        _stream("", stop_reason="max_tokens", output_tokens=512),
        _stream('{"sql": "SELECT 1 FROM DUAL", "explanation": ""}'),
    ]

    raw = _generate(_settings(), max_output_tokens=512, strict_max_output_tokens=True)

    assert json.loads(raw)["sql"] == "SELECT 1 FROM DUAL"
    assert claude.body(0)["output_config"]["effort"] == "high"
    assert claude.body(1)["output_config"]["effort"] == "medium"
    assert claude.body(0)["max_tokens"] == claude.body(1)["max_tokens"] == 512


def test_online_generate_reports_output_limit(claude: FakeMessagesApi) -> None:
    claude.responses.append(_stream("", stop_reason="max_tokens", output_tokens=4096))

    with pytest.raises(EnterpriseAiDirectError) as caught:
        _generate(_settings())

    error = caught.value
    assert error.code == "incomplete"
    assert error.failure_kind == "incomplete"
    assert error.incomplete is not None and error.incomplete.output_limit_reached
    assert error.attempts == 1
    assert error.user_message
    assert len(claude.requests) == 1


# --------------------------------------------------------------------------- 失敗の分類


def test_rate_limit_is_retried_with_the_same_policy(claude: FakeMessagesApi) -> None:
    claude.responses += [
        _error(429, "rate_limit_error", "rate limited"),
        _error(429, "rate_limit_error", "rate limited"),
    ]

    with pytest.raises(EnterpriseAiDirectError) as caught:
        _generate(_settings(oci_enterprise_ai_max_retries=1))

    error = caught.value
    assert len(claude.requests) == 2
    assert error.code == "rate_limit"
    assert error.status_code == 429
    assert error.retryable is True
    assert error.attempts == 2
    assert error.upstream_request_id == "req_err"
    assert error.endpoint_host == "api.anthropic.com"
    assert API_KEY not in str(error)


def test_authentication_failure_is_a_configuration_error_and_not_retried(
    claude: FakeMessagesApi,
) -> None:
    claude.responses.append(_error(401, "authentication_error", "invalid x-api-key"))

    with pytest.raises(EnterpriseAiDirectError) as caught:
        _generate(_settings(oci_enterprise_ai_max_retries=2))

    error = caught.value
    assert len(claude.requests) == 1
    assert error.code == "authentication"
    assert error.configuration_error is True
    assert error.failure_actions()


def test_refusal_is_reported_as_refusal(claude: FakeMessagesApi) -> None:
    claude.responses.append(
        _stream("", stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber"})
    )

    with pytest.raises(EnterpriseAiDirectError) as caught:
        _generate(_settings(oci_enterprise_ai_max_retries=2))

    assert caught.value.code == "refusal"
    assert caught.value.failure_kind == "refusal"
    assert len(claude.requests) == 1


def test_timeout_is_retryable_timeout(claude: FakeMessagesApi) -> None:
    claude.responses.append(httpx2.ReadTimeout("timed out"))

    with pytest.raises(EnterpriseAiDirectError) as caught:
        _generate(_settings())

    assert caught.value.code == "timeout"
    assert caught.value.retryable is True


def test_schema_mismatch_is_a_response_format_error(claude: FakeMessagesApi) -> None:
    claude.responses.append(_stream('{"unexpected": 1}'))

    with pytest.raises(EnterpriseAiDirectError) as caught:
        _generate(_settings())

    assert caught.value.code == "response_format"


def test_unsupported_image_type_fails_before_calling(claude: FakeMessagesApi) -> None:
    with pytest.raises(EnterpriseAiDirectError):
        OciEnterpriseAiDirectClient(_settings()).generate_from_image(
            PNG_BYTES, "画像を読む", mime_type="image/bmp"
        )
    assert claude.requests == []


# --------------------------------------------------------------------------- ほかの接続


def test_other_connections_keep_the_responses_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_messages_api(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("OpenAI 互換の接続のモデルで Messages API を呼んだ")

    monkeypatch.setattr(enterprise_ai_client, "create_anthropic_message", no_messages_api)
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"output_text": json.dumps({"text": "ok"})})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    raw = _generate(
        _settings(oci_enterprise_ai_default_text_model="text-a"),
        response_format=response_format(TextOutput),
    )

    assert json.loads(raw) == {"text": "ok"}
    assert urls == [f"{PRIMARY}/responses"]


# --------------------------------------------------------------------------- 失敗の案内（#5587）

# OpenAI 互換の接続（OCI Enterprise AI）の案内にだけある語。クォータナリ接続の失敗には出さない。
OCI_ONLY_WORDS = ("IAM", "Project OCID", "リージョン", "OCI", "Region")

FAILURES = [
    (_error(401, "authentication_error", "invalid x-api-key"), "authentication", 401),
    (_error(403, "permission_error", "not allowed"), "authorization", 403),
    (_error(404, "not_found_error", f"model: {CLAUDE}"), "model_not_found", 404),
    (_error(429, "rate_limit_error", "rate limited"), "rate_limit", 429),
    (_error(500, "api_error", "internal"), "unavailable", 500),
    (httpx2.ReadTimeout("timed out"), "timeout", None),
    (httpx2.ConnectError("refused"), "connection", None),
]
FAILURE_IDS = ["401", "403", "404", "429", "5xx", "timeout", "connection"]


@pytest.mark.parametrize(("response", "kind", "status_code"), FAILURES, ids=FAILURE_IDS)
def test_failure_actions_use_the_platform_quaternary_text(
    claude: FakeMessagesApi,
    response: httpx2.Response | Exception,
    kind: str,
    status_code: int | None,
) -> None:
    claude.responses.append(response)

    with pytest.raises(EnterpriseAiDirectError) as caught:
        _generate(_settings())

    error = caught.value
    assert error.code == kind
    actions = error.failure_actions()
    assert actions == anthropic_failure_actions(
        kind, status_code=status_code, endpoint="api.anthropic.com"
    )
    for word in OCI_ONLY_WORDS:
        assert word not in " ".join(actions)
    if error.configuration_error:
        message = enterprise_ai_configuration_failure_message(error, "SQL の生成で ")
        assert message.startswith("SQL の生成で Anthropic Claude（クォータナリ接続）の呼び出し")
        assert actions[0] in message
        for word in OCI_ONLY_WORDS:
            assert word not in message


def _quaternary_authorization_error() -> EnterpriseAiDirectError:
    cause = AnthropicMessagesError("失敗", kind="authorization", status_code=403)
    error = _anthropic_direct_error(
        cause,
        model_id=CLAUDE,
        endpoint_host="api.anthropic.com",
        attempts=1,
        timeout_seconds=10.0,
    )
    error.__cause__ = cause
    return error


def test_reverse_generation_warning_uses_the_platform_quaternary_text() -> None:
    error = _quaternary_authorization_error()

    class FailingClient:
        def generate(self, **_kwargs: Any) -> str:
            raise error

    with pytest.raises(ReverseStageError) as caught:
        generate_stage(
            client=cast(Any, FailingClient()),
            stage="structure_analysis",
            prompt="SQL A",
            context="",
            system_prompt="## Step 1",
            parse=str,
            timeout_seconds=10.0,
            max_retries=2,
            deadline=time.monotonic() + 60,
        )

    warning = caught.value.warning()
    assert anthropic_failure_actions("authorization", status_code=403)[0] in warning
    assert REASONS["authorization"] not in warning
    for word in OCI_ONLY_WORDS:
        assert word not in warning


def test_bounded_caller_hint_uses_the_platform_quaternary_text() -> None:
    error = _quaternary_authorization_error()

    async def fail(_attempt: Any) -> str:
        raise error

    caller = make_ai_caller(_settings(), "metadata_sql")
    with pytest.raises(BoundedCallAborted) as caught:
        asyncio.run(caller.call(fail, label="x"))

    assert anthropic_failure_actions("authorization", status_code=403)[0] in str(caught.value)
    assert "IAM" not in str(caught.value)
