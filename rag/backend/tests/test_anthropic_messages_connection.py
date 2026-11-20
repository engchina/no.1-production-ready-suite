"""クォータナリ接続（Anthropic Claude の Messages API）のモデルを部品で呼ぶ（#5546）。

RAG の 2 つの呼び出しの層（共有 core の `OciEnterpriseAiClient` と rag_engine の
`adapters/oci.py`）は、接続がクォータナリ接続なら同じ Responses API の要求を Messages API で送り、
Responses API の形の応答を読む（`rag_parser_core.anthropic_responses`）。実の API は呼ばず、
`pr_anthropic_messages` の呼び出し（`create_anthropic_message` / `acreate_anthropic_message`）を
決定論の stub に置き換えて、要求の写し方と、本文・構造化出力・画像・usage・失敗の写し方を確かめる。
ほかの接続（Responses API）の経路が変わらないことも確かめる。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from pr_anthropic_messages import (
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    AnthropicUsage,
)
from pr_enterprise_ai.errors import EnterpriseAiIncomplete
from rag_parser_core import anthropic_responses
from rag_parser_core.anthropic_responses import (
    AnthropicRequestShapeError,
    messages_json_schema,
    messages_request,
    responses_body_for_failure,
)
from rag_parser_core.model_api import MODEL_API_ANTHROPIC_MESSAGES, MODEL_API_RESPONSES

from app.clients.oci_enterprise_ai import (
    EnterpriseAiHttpTransport,
    EnterpriseAiIncompleteResponseError,
    EnterpriseAiTimeoutError,
    EnterpriseAiUnsupportedInputError,
    OciEnterpriseAiClient,
    config_from_settings,
)
from app.config import EnterpriseAiConfiguredModel, Settings
from app.services.control import service_runtime_env

PRIMARY = "https://primary.example/openai/v1"
CLAUDE_ENDPOINT = "https://claude-gateway.example"
# 1x1 の PNG（合成のデータ）。
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)
USAGE = AnthropicUsage(
    input_tokens=100,
    output_tokens=20,
    cache_read_input_tokens=30,
    cache_creation_input_tokens=5,
    thinking_tokens=7,
)


def _settings(
    *, text_connection: str = "quaternary", quaternary_endpoint: str = "", **overrides: Any
) -> Settings:
    values: dict[str, Any] = {
        "oci_compartment_id": "ocid1.compartment.oc1..example",
        "oci_enterprise_ai_endpoint": PRIMARY,
        "oci_enterprise_ai_project_ocid": "ocid1.project.primary",
        "oci_enterprise_ai_api_key": "sk-primary",
        "oci_enterprise_ai_quaternary_endpoint": quaternary_endpoint,
        "oci_enterprise_ai_quaternary_api_key": "sk-ant-test",
        "oci_enterprise_ai_models": [
            EnterpriseAiConfiguredModel(
                model_id="claude-text",
                display_name="Claude",
                vision_enabled=True,
                connection_id=text_connection,
                reasoning_effort="medium",
            ),
            EnterpriseAiConfiguredModel(model_id="vision-oci", vision_enabled=True),
        ],
        "oci_enterprise_ai_default_text_model": "claude-text",
        "oci_enterprise_ai_default_vision_model": "claude-text",
        "oci_enterprise_ai_llm_path": "/responses",
        "oci_enterprise_ai_vlm_path": "/responses",
        "oci_enterprise_ai_vlm_input_mode": "files_api",
        "oci_enterprise_ai_timeout_seconds": 5.0,
        "oci_enterprise_ai_max_retries": 2,
    }
    values.update(overrides)
    return Settings.model_construct(**values)


class _Calls:
    """部品の呼び出しの stub（要求と引数を記録し、決めた結果を返すか失敗を送出する）。"""

    def __init__(self, *outcomes: AnthropicMessagesResult | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[AnthropicMessagesRequest] = []
        self.kwargs: list[dict[str, Any]] = []

    def _next(self, request: AnthropicMessagesRequest, kwargs: dict[str, Any]) -> Any:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sync(self, request: AnthropicMessagesRequest, **kwargs: Any) -> AnthropicMessagesResult:
        return cast(AnthropicMessagesResult, self._next(request, kwargs))

    async def asynchronous(
        self, request: AnthropicMessagesRequest, **kwargs: Any
    ) -> AnthropicMessagesResult:
        return cast(AnthropicMessagesResult, self._next(request, kwargs))


@pytest.fixture
def stub_messages(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Calls]:
    def install(*outcomes: AnthropicMessagesResult | Exception) -> _Calls:
        calls = _Calls(*outcomes)
        monkeypatch.setattr(anthropic_responses, "create_anthropic_message", calls.sync)
        monkeypatch.setattr(anthropic_responses, "acreate_anthropic_message", calls.asynchronous)
        return calls

    return install


def _result(text: str, *, stop_reason: str = "end_turn") -> AnthropicMessagesResult:
    return AnthropicMessagesResult(
        text=text, model="claude-text", stop_reason=stop_reason, usage=USAGE, request_id="req_1"
    )


def _incomplete(max_output_tokens: int) -> AnthropicMessagesError:
    return AnthropicMessagesError(
        "出力の上限で途切れました。",
        kind="incomplete",
        model_id="claude-text",
        incomplete=EnterpriseAiIncomplete(
            status="incomplete",
            reason="max_output_tokens",
            max_output_tokens=max_output_tokens,
            output_tokens=max_output_tokens,
        ),
        usage=AnthropicUsage(input_tokens=10, output_tokens=max_output_tokens),
    )


class _NoHttpTransport:
    """Responses API の HTTP を呼んだら失敗にする（Messages API の経路は transport を通らない）。"""

    async def post_json(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        raise AssertionError("Responses API の transport を呼んではいけない")

    async def upload_file(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        raise AssertionError("Files API を呼んではいけない")


def _client(settings: Settings) -> OciEnterpriseAiClient:
    return OciEnterpriseAiClient(
        settings=settings, http_transport=cast(EnterpriseAiHttpTransport, _NoHttpTransport())
    )


# --------------------------------------------------------------------------- 接続の値


def test_config_uses_messages_api_only_for_quaternary_connection() -> None:
    claude = config_from_settings(_settings())
    assert claude.oci_enterprise_ai_model_api == MODEL_API_ANTHROPIC_MESSAGES
    # Endpoint URL が空なら Anthropic の公式の API（platform の effective_endpoint）。
    assert claude.oci_enterprise_ai_endpoint == "https://api.anthropic.com"
    assert claude.oci_enterprise_ai_api_key == "sk-ant-test"
    assert claude.uses_anthropic_messages

    other = config_from_settings(_settings(text_connection="primary"))
    assert other.oci_enterprise_ai_model_api == MODEL_API_RESPONSES
    assert other.oci_enterprise_ai_endpoint == PRIMARY
    assert not other.uses_anthropic_messages


def test_config_keeps_vision_connection_api_separate() -> None:
    config = config_from_settings(
        _settings(oci_enterprise_ai_default_vision_model="vision-oci"),
    )
    assert config.uses_anthropic_messages
    vision = config.for_vision()
    assert vision.oci_enterprise_ai_model_api == MODEL_API_RESPONSES
    assert vision.oci_enterprise_ai_endpoint == PRIMARY


def test_service_runtime_env_accepts_quaternary_vision_model() -> None:
    """parser のサービスの env を書く処理が、Messages API の接続で失敗しない。"""
    env = service_runtime_env(_settings(quaternary_endpoint=CLAUDE_ENDPOINT))
    assert env["PLATFORM_OCI_ENTERPRISE_AI_ENDPOINT"] == CLAUDE_ENDPOINT
    assert env["PLATFORM_OCI_ENTERPRISE_AI_API_KEY"] == "sk-ant-test"


def test_engine_settings_pass_model_api_per_connection(tmp_path: Path) -> None:
    from rag_engine.config import ENTERPRISE_AI_LLM_PROVIDER, ENTERPRISE_AI_VISION_LLM_PROVIDER

    from app.rag.answer_engine import build_engine_settings

    engine = build_engine_settings(
        _settings(
            quaternary_endpoint=CLAUDE_ENDPOINT,
            oci_enterprise_ai_default_vision_model="vision-oci",
        ),
        output_dir=tmp_path,
    )
    answer = engine.llm_providers[ENTERPRISE_AI_LLM_PROVIDER]
    vision = engine.llm_providers[ENTERPRISE_AI_VISION_LLM_PROVIDER]
    assert answer.model_api == MODEL_API_ANTHROPIC_MESSAGES
    assert answer.base_url == CLAUDE_ENDPOINT
    assert vision.model_api == MODEL_API_RESPONSES
    assert vision.base_url == PRIMARY


# --------------------------------------------------------------------------- 要求の写し方


def test_request_maps_instructions_text_effort_and_limit() -> None:
    request = messages_request(
        {
            "model": "claude-text",
            "instructions": "## Step 1: 合成の指示",
            "input": [
                {"role": "system", "content": "追加の指示"},
                {"role": "user", "content": "合成の質問"},
            ],
            "temperature": 0.0,
            "max_output_tokens": 1234,
            "reasoning": {"effort": "low"},
        }
    )
    assert request.model_id == "claude-text"
    assert request.system == "## Step 1: 合成の指示\n\n追加の指示"
    assert request.user_text == "合成の質問"
    assert request.max_output_tokens == 1234
    assert request.reasoning_effort == "low"
    assert request.response_schema is None
    assert request.images == ()


def test_request_maps_images_and_jpg_alias() -> None:
    data = base64.b64encode(PNG_BYTES).decode()
    request = messages_request(
        {
            "model": "claude-text",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "図を読む"},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{data}"},
                        {"type": "input_image", "image_url": f"data:image/jpg;base64,{data}"},
                    ],
                }
            ],
            "max_output_tokens": 100,
        }
    )
    assert request.user_text == "図を読む"
    assert [image.media_type for image in request.images] == ["image/png", "image/jpeg"]


@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_file", "file_id": "file_1"},
        {"type": "input_image", "image_url": "data:application/pdf;base64,JVBERi0="},
    ],
    ids=["files-api", "pdf-data-url"],
)
def test_request_rejects_non_image_files(part: dict[str, Any]) -> None:
    payload = {"model": "m", "input": [{"role": "user", "content": [part]}], "max_output_tokens": 1}
    with pytest.raises(AnthropicRequestShapeError, match="画像"):
        messages_request(payload)


def test_request_rejects_template_payload_without_input() -> None:
    with pytest.raises(AnthropicRequestShapeError, match="payload template"):
        messages_request({"model": "m", "prompt": "x", "max_output_tokens": 1})


def _walk(node: Any) -> list[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        nested = [item for value in node.values() for item in _walk(value)]
        return [node, *nested]
    if isinstance(node, list):
        return [item for value in node for item in _walk(value)]
    return []


_UNSUPPORTED_KEYWORDS = {"minimum", "maximum", "minLength", "maxLength", "maxItems"}


def _assert_messages_schema(schema: Mapping[str, Any]) -> None:
    for node in _walk(schema):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
        assert not isinstance(node.get("type"), list)
        # properties の中は項目の名前なので見ない。
        keywords = set(node) - {"properties"}
        if "type" in node or "anyOf" in node or "enum" in node:
            assert not keywords & _UNSUPPORTED_KEYWORDS, node
            assert node.get("minItems", 0) in (0, 1), node


def _rag_response_schemas() -> dict[str, Mapping[str, Any]]:
    """RAG が構造化出力に使う schema（固定の定数・実行時に組む schema・rag_engine の出力の型）。"""
    from openai.lib._parsing._responses import type_to_text_format_param
    from pr_backend_core.response_schema import with_schema_field_guide
    from rag_engine.adapters.oci import picture_response_schema
    from rag_engine.generation.answer_parts_wire import _table_answer_model
    from rag_engine.models.llm import PictureDescriptionOutput, TextOutput
    from rag_parser_core.oci_enterprise_ai import _vlm_structured_extraction_schema

    from app.rag.document_summary import DOCUMENT_SUMMARY_RESPONSE_SCHEMA
    from app.rag.extraction_field_adapter import FIELD_EXTRACTION_RESPONSE_SCHEMA
    from app.rag.wiki_models import WikiFilePlan, context_schema, link_schema, scoped_plan

    schemas: dict[str, Mapping[str, Any]] = {
        "vlm_structured_extraction": _vlm_structured_extraction_schema(),
        "picture_response": picture_response_schema(),
        "document_summary": DOCUMENT_SUMMARY_RESPONSE_SCHEMA,
        "field_extraction": FIELD_EXTRACTION_RESPONSE_SCHEMA,
        "wiki_context": context_schema(["u1", "u2"], ["u1", "u2", "u3"]).model_json_schema(),
        "wiki_links": link_schema(["u1"], ["u2", "u3"], max_links=2).model_json_schema(),
        "wiki_plan": scoped_plan(
            WikiFilePlan, {"document_ids": (["d1", "d2"], 2)}
        ).model_json_schema(),
    }
    for model in (TextOutput, PictureDescriptionOutput, _table_answer_model(str)):
        # rag_engine は OpenAI SDK の strict な schema を送る（`_response_wire_request`）。
        text_format = cast(Mapping[str, Any], type_to_text_format_param(model))
        schemas[model.__name__] = text_format["schema"]
    return {name: with_schema_field_guide("", schema)[1] for name, schema in schemas.items()}


@pytest.mark.parametrize("name", sorted(_rag_response_schemas()))
def test_rag_response_schemas_fit_messages_structured_outputs(name: str) -> None:
    """数・文字列・件数の制約と型の配列は送る schema から外れ、object はすべて閉じる。"""
    _assert_messages_schema(messages_json_schema(_rag_response_schemas()[name]))


def test_schema_keeps_nullable_and_enum_meaning() -> None:
    schema = messages_json_schema(
        {
            "type": "object",
            "properties": {
                "id": {"type": ["string", "null"], "maxLength": 8},
                "kind": {"type": ["string", "null"], "enum": ["a", None]},
            },
            "required": ["id", "kind"],
        }
    )
    assert schema["properties"]["id"]["anyOf"][0]["type"] == "string"
    assert schema["properties"]["id"]["anyOf"][1] == {"type": "null"}
    assert "maxLength: 8" in schema["properties"]["id"]["anyOf"][0]["description"]
    assert schema["properties"]["kind"] == {"enum": ["a", None]}
    assert schema["additionalProperties"] is False


# --------------------------------------------------------------------------- 応答の写し方


def test_failure_body_only_for_incomplete_and_refusal() -> None:
    request = AnthropicMessagesRequest(model_id="m", user_text="x", max_output_tokens=50)
    incomplete = responses_body_for_failure(_incomplete(50), request)
    assert incomplete is not None
    assert incomplete["status"] == "incomplete"
    assert incomplete["incomplete_details"] == {"reason": "max_output_tokens"}
    assert incomplete["usage"]["output_tokens"] == 50
    refusal = responses_body_for_failure(
        AnthropicMessagesError("モデル「m」が応答を拒否しました。", kind="refusal"), request
    )
    assert refusal is not None
    assert refusal["output"][0]["content"][0]["type"] == "refusal"
    assert "output_text" not in refusal
    rate_limited = AnthropicMessagesError("上限", kind="rate_limit", status_code=429)
    assert responses_body_for_failure(rate_limited, request) is None


# ------------------------------------------------------------------- 共有 core（rag_parser_core）


async def test_parser_core_generate_text_and_usage_log(
    stub_messages: Callable[..., _Calls], caplog: pytest.LogCaptureFixture
) -> None:
    calls = stub_messages(_result("合成の回答"))
    client = _client(_settings(quaternary_endpoint=CLAUDE_ENDPOINT))

    with caplog.at_level(logging.INFO, logger="app.clients.oci_enterprise_ai"):
        text = await client.generate("合成の質問", "合成の根拠", system_prompt="## Step 1: 指示")

    assert text == "合成の回答"
    request = calls.requests[0]
    assert request.model_id == "claude-text"
    assert request.system == "## Step 1: 指示"
    assert "合成の質問" in request.user_text and "合成の根拠" in request.user_text
    assert request.reasoning_effort == "medium"
    assert calls.kwargs[0] == {
        "api_key": "sk-ant-test",
        "endpoint_url": CLAUDE_ENDPOINT,
        "timeout_seconds": 5.0,
        "max_retries": 2,
    }
    record = next(item for item in caplog.records if item.getMessage() == "llm_call_usage")
    fields = cast(Any, record)
    assert fields.llm_model == "claude-text"
    assert fields.llm_status == "completed"
    assert fields.input_tokens == 135
    assert fields.cached_tokens == 30
    assert fields.output_tokens == 20
    assert fields.reasoning_tokens == 7


async def test_parser_core_structured_output_sends_schema(
    stub_messages: Callable[..., _Calls],
) -> None:
    calls = stub_messages(_result('{"answer": "合成"}'))
    client = _client(_settings())
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string", "maxLength": 20, "description": "答え"}},
        "required": ["answer"],
    }

    text = await client.generate("", "合成のデータ", system_prompt="指示", response_schema=schema)

    assert text == '{"answer": "合成"}'
    sent = calls.requests[0].response_schema
    assert sent is not None
    assert sent["additionalProperties"] is False
    assert "maxLength" not in sent["properties"]["answer"]
    # 項目の説明は指示の最後の Step にして送る（#5230。Responses API の経路と同じ）。
    assert "答え" in calls.requests[0].system


async def test_parser_core_images_go_inline(stub_messages: Callable[..., _Calls]) -> None:
    calls = stub_messages(_result('{"description": "合成"}'))
    client = _client(_settings())

    await client.generate_from_images([PNG_BYTES, PNG_BYTES], "図を読む", system_prompt="指示")

    assert [image.media_type for image in calls.requests[0].images] == ["image/png", "image/png"]


async def test_parser_core_vlm_extraction_reads_image_without_files_api(
    stub_messages: Callable[..., _Calls],
) -> None:
    stub_messages(_result('{"raw_text": "合成の本文", "elements": []}'))
    client = _client(_settings())

    extracted = await client.extract_with_vlm(PNG_BYTES, "読む", mime_type="image/png")

    assert "合成の本文" in str(extracted)


async def test_parser_core_vlm_rejects_pdf_without_calling(
    stub_messages: Callable[..., _Calls],
) -> None:
    calls = stub_messages()
    client = _client(_settings())

    with pytest.raises(EnterpriseAiUnsupportedInputError, match="画像"):
        await client.extract_with_vlm(b"%PDF-1.7", "読む", mime_type="application/pdf")

    assert calls.requests == []


async def test_parser_core_incomplete_maps_to_existing_error(
    stub_messages: Callable[..., _Calls],
) -> None:
    stub_messages(_incomplete(32))
    client = _client(_settings())

    with pytest.raises(EnterpriseAiIncompleteResponseError, match="出力の上限"):
        await client.generate("", "合成のデータ", system_prompt="指示")


async def test_parser_core_timeout_maps_to_existing_error(
    stub_messages: Callable[..., _Calls],
) -> None:
    stub_messages(AnthropicMessagesError("待ち時間の上限", kind="timeout"))
    client = _client(_settings())

    with pytest.raises(EnterpriseAiTimeoutError):
        await client.generate("", "合成のデータ", system_prompt="指示")


async def test_parser_core_http_failure_keeps_status(
    stub_messages: Callable[..., _Calls], caplog: pytest.LogCaptureFixture
) -> None:
    from pr_backend_core.bounded_calls import ErrorKind, classify_error

    stub_messages(AnthropicMessagesError("上限", kind="rate_limit", status_code=429))
    client = _client(_settings())

    with (
        caplog.at_level(logging.INFO, logger="app.clients.oci_enterprise_ai"),
        pytest.raises(AnthropicMessagesError) as raised,
    ):
        await client.generate("", "合成のデータ", system_prompt="指示")

    assert raised.value.retryable
    # 取込の上限つきの並行の実行器（#2002）と同じ分類で、429 として待って送り直せる。
    assert classify_error(raised.value).kind is ErrorKind.RATE_LIMITED
    record = next(item for item in caplog.records if item.getMessage() == "llm_call_usage")
    assert cast(Any, record).llm_failure == {
        "error_type": "AnthropicMessagesError",
        "status_code": 429,
    }


async def test_parser_core_other_connection_still_uses_responses(
    stub_messages: Callable[..., _Calls],
) -> None:
    calls = stub_messages()
    posted: list[str] = []

    class Transport:
        async def post_json(
            self, url: str, payload: Mapping[str, Any], **kwargs: Any
        ) -> Mapping[str, Any]:
            posted.append(url)
            return {"output_text": "ok"}

    client = OciEnterpriseAiClient(
        settings=_settings(text_connection="primary"),
        http_transport=cast(EnterpriseAiHttpTransport, Transport()),
    )

    assert await client.generate("", "合成のデータ", system_prompt="指示") == "ok"
    assert posted == [f"{PRIMARY}/responses"]
    assert calls.requests == []


# --------------------------------------------------------------------------- rag_engine


def _engine_settings(tmp_path: Path, settings: Settings | None = None) -> Any:
    from app.rag.answer_engine import build_engine_settings

    return build_engine_settings(settings or _settings(), output_dir=tmp_path)


def test_engine_parses_structured_output(
    tmp_path: Path, stub_messages: Callable[..., _Calls]
) -> None:
    from rag_engine.adapters import oci
    from rag_engine.models.llm import TextOutput

    calls = stub_messages(_result('{"text": "合成の回答"}'))

    parsed = oci.parse_text_response(
        "## Step 1: 指示", "合成の質問", _engine_settings(tmp_path), TextOutput
    )

    assert parsed.text == "合成の回答"
    request = calls.requests[0]
    assert request.model_id == "claude-text"
    assert request.response_schema is not None
    assert request.response_schema["additionalProperties"] is False
    assert request.system.startswith("## Step 1: 指示")
    # SDK の再試行はしない（やり直しは rag_engine の共通の枠）。
    assert calls.kwargs[0]["max_retries"] == 0
    assert calls.kwargs[0]["endpoint_url"] == "https://api.anthropic.com"


def test_engine_retries_rate_limit_in_shared_budget(
    tmp_path: Path, stub_messages: Callable[..., _Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rag_engine.adapters import oci
    from rag_engine.models.llm import TextOutput

    monkeypatch.setattr(oci, "_wait_before_retry", lambda *args, **kwargs: None)
    calls = stub_messages(
        AnthropicMessagesError("上限", kind="rate_limit", status_code=429),
        _result('{"text": "合成"}'),
    )

    parsed = oci.parse_text_response("指示", "質問", _engine_settings(tmp_path), TextOutput)

    assert parsed.text == "合成"
    assert len(calls.requests) == 2


def test_engine_does_not_retry_authentication_failure(
    tmp_path: Path, stub_messages: Callable[..., _Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rag_engine.adapters import oci
    from rag_engine.models.llm import TextOutput

    monkeypatch.setattr(oci, "_wait_before_retry", lambda *args, **kwargs: None)
    calls = stub_messages(AnthropicMessagesError("key", kind="authentication", status_code=401))

    with pytest.raises(AnthropicMessagesError):
        oci.parse_text_response("指示", "質問", _engine_settings(tmp_path), TextOutput)

    assert len(calls.requests) == 1


def test_engine_retries_stream_error_event_by_component_classification(
    tmp_path: Path, stub_messages: Callable[..., _Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    """streaming の途中の overloaded（HTTP 200）も、部品の分類で送り直す。"""
    from rag_engine.adapters import oci
    from rag_engine.models.llm import TextOutput

    monkeypatch.setattr(oci, "_wait_before_retry", lambda *args, **kwargs: None)
    calls = stub_messages(
        AnthropicMessagesError("overloaded", kind="unavailable", status_code=200),
        _result('{"text": "合成"}'),
    )

    oci.parse_text_response("指示", "質問", _engine_settings(tmp_path), TextOutput)

    assert len(calls.requests) == 2


def test_engine_incomplete_uses_existing_handling(
    tmp_path: Path, stub_messages: Callable[..., _Calls]
) -> None:
    from rag_engine.adapters import oci
    from rag_engine.models.llm import TextOutput

    stub_messages(_incomplete(64))

    with pytest.raises(oci.IncompleteResponse) as raised:
        oci.parse_text_response("指示", "質問", _engine_settings(tmp_path), TextOutput)

    assert raised.value.reason == "max_output_tokens"
    assert "claude-text" in raised.value.user_message


def test_engine_picture_widens_output_limit_after_incomplete(
    tmp_path: Path, stub_messages: Callable[..., _Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    """取込の画像の説明は、出力の上限で途切れたら上限を広げて送り直す（Responses API と同じ）。"""
    from rag_engine.adapters import oci
    from rag_engine.config import ENTERPRISE_AI_VISION_LLM_PROVIDER, llm_output_token_limit
    from rag_engine.models.llm import PictureDescriptionOutput

    monkeypatch.setattr(oci, "_wait_before_retry", lambda *args, **kwargs: None)
    image = tmp_path / "figure.png"
    image.write_bytes(PNG_BYTES)
    settings = _settings(
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(
                model_id="claude-text",
                vision_enabled=True,
                connection_id="quaternary",
                max_output_tokens=64000,
            ),
        ],
    )
    engine = _engine_settings(tmp_path, settings)
    provider = engine.llm_providers[ENTERPRISE_AI_VISION_LLM_PROVIDER]
    first_budget = llm_output_token_limit(engine, provider)
    # 2 回目は送り直さない失敗で止め、送った要求だけを確かめる。
    calls = stub_messages(
        _incomplete(first_budget),
        AnthropicMessagesError("key", kind="authentication", status_code=401),
    )

    with pytest.raises(AnthropicMessagesError):
        oci.parse_image_response("指示", "図を読む", image, engine, PictureDescriptionOutput)

    assert len(calls.requests) == 2
    assert calls.requests[0].max_output_tokens == first_budget
    assert calls.requests[0].images[0].media_type == "image/png"
    assert calls.requests[1].max_output_tokens > first_budget


def test_engine_other_connection_does_not_call_messages_api(
    tmp_path: Path, stub_messages: Callable[..., _Calls], monkeypatch: pytest.MonkeyPatch
) -> None:
    from rag_engine.adapters import oci
    from rag_engine.models.llm import TextOutput

    calls = stub_messages()
    sent: list[dict[str, Any]] = []

    class Responses:
        def parse(self, **request: Any) -> Any:
            sent.append(request)
            return {
                "status": "completed",
                "output_text": '{"text": "ok"}',
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    class Client:
        responses = Responses()

        def with_options(self, **kwargs: Any) -> Client:
            return self

    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda provider, settings: Client())
    engine = _engine_settings(tmp_path, _settings(text_connection="primary"))

    assert oci.parse_text_response("指示", "質問", engine, TextOutput).text == "ok"
    assert len(sent) == 1
    assert calls.requests == []
