"""クォータナリ接続（Anthropic Claude の Messages API）のモデルの呼び出しの経路（#5546・#5580）。

- 業務 Agent の実行（ツールの呼び出し・並列のツール・複数のターン・思考の block の受け渡し）と
  1 回の生成（品質評価の判定・下書きの提案）は、`AnthropicMessagesModel` が Responses API の経路と
  同じ `_OciResponses` の層を通して `pr_anthropic_messages` で呼ぶ。
- 画像の読み取りは `call_anthropic_messages`（1 回の生成）。
- ほかの接続のモデルは従来どおり OpenAI 互換の Responses API。

実の Anthropic の API は呼ばない。SDK の HTTP を Messages API の録画の応答（`anthropic_support`）に
置き換えるか、画像の読み取りは `acreate_anthropic_message` を決定論のスタブに置き換える。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from agents import Agent, FunctionTool, Runner, ToolOutputImage, ToolOutputText
from anthropic_support import FakeMessagesApi, recorded_turn, text, thinking, tool_use
from openai import DEFAULT_MAX_RETRIES
from pr_anthropic_messages import (
    THINKING_BINDING_BETA,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    AnthropicUsage,
    acreate_anthropic_turn,
)
from pr_backend_core.bounded_calls import ErrorKind
from pr_system_settings.model import (
    ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT,
    EnterpriseAiConfiguredModel,
)
from pydantic import BaseModel, Field
from pytest import MonkeyPatch

import app.features.agent.anthropic_model as anthropic_model
import app.features.agent.chat_attachments as attachment_module
from app.features.agent import builtin_runtime, evaluation
from app.features.agent.ai_calls import make_ai_caller
from app.features.agent.anthropic_model import AnthropicMessagesModel
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.context_window import ProviderOverflow, provider_overflow
from app.features.agent.evaluation import EvaluationCase
from app.settings import Settings

CLAUDE = "claude-model-a"
CASE = EvaluationCase(id="c1", question="合成の質問 A", expected="合成の期待する回答 A")
PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "oci_enterprise_ai_endpoint": "https://primary.example/openai/v1",
        "oci_enterprise_ai_api_key": "sk-primary",
        "oci_enterprise_ai_quaternary_api_key": "sk-ant-synthetic",
        "oci_enterprise_ai_models": [
            EnterpriseAiConfiguredModel(model_id="text-a"),
            EnterpriseAiConfiguredModel(
                model_id=CLAUDE,
                connection_id="quaternary",
                vision_enabled=True,
                reasoning_effort="high",
                max_output_tokens=4096,
            ),
        ],
        "oci_enterprise_ai_default_text_model": CLAUDE,
        "oci_enterprise_ai_default_vision_model": CLAUDE,
    }
    values.update(overrides)
    return Settings(**values)


def _use_settings(monkeypatch: MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(builtin_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(attachment_module, "get_settings", lambda: settings)


def _claude_target(**overrides: Any) -> ModelTarget:
    values: dict[str, Any] = {
        "model_id": CLAUDE,
        "endpoint": ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT,
        "project_ocid": "",
        "api_key": "sk-ant-synthetic",
        "reasoning_effort": "medium",
        "max_output_tokens": 2048,
        "sdk_max_retries": 0,
        "uses_anthropic_messages": True,
    }
    values.update(overrides)
    return ModelTarget(**values)


@pytest.fixture
def api(monkeypatch: MonkeyPatch) -> FakeMessagesApi:
    return FakeMessagesApi().install(monkeypatch)


def _agent(target: ModelTarget, **kwargs: Any) -> Agent[Any]:
    return Agent(
        name="synthetic",
        model=builtin_runtime.model_factory(target),
        model_settings=builtin_runtime.model_settings_for(target),
        **kwargs,
    )


def _tool(name: str, result: Any, calls: list[dict[str, Any]], **kwargs: Any) -> FunctionTool:
    async def invoke(_context: Any, arguments: str) -> Any:
        calls.append(json.loads(arguments))
        return result

    return FunctionTool(
        name=name,
        description=f"合成のツール {name}",
        params_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        on_invoke_tool=invoke,
        **kwargs,
    )


# --------------------------------------------------------------------------- モデルと接続


def test_quaternary_model_target_uses_messages_api_endpoint(monkeypatch: MonkeyPatch) -> None:
    """クォータナリ接続のモデルは Messages API の Endpoint URL（空なら公式の API）で決まる。"""
    _use_settings(monkeypatch, _settings())

    target = builtin_runtime.resolve_model_target("")

    assert target.uses_anthropic_messages
    assert (target.model_id, target.endpoint, target.project_ocid, target.api_key) == (
        CLAUDE,
        ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT,
        "",
        "sk-ant-synthetic",
    )
    assert (target.reasoning_effort, target.max_output_tokens) == ("high", 4096)
    model = builtin_runtime._default_model_factory(target)  # noqa: SLF001
    assert isinstance(model, AnthropicMessagesModel)
    assert model.provider_name == "anthropic"

    _use_settings(
        monkeypatch,
        _settings(oci_enterprise_ai_quaternary_endpoint="https://claude-gateway.example"),
    )
    assert builtin_runtime.resolve_model_target("").endpoint == "https://claude-gateway.example"


def test_other_connections_keep_the_responses_api(monkeypatch: MonkeyPatch) -> None:
    """ほかの接続のモデルは従来どおり OpenAI 互換の Responses API（base URL のまま）。"""
    _use_settings(monkeypatch, _settings())

    target = builtin_runtime.resolve_model_target("text-a")

    assert not target.uses_anthropic_messages
    assert target.endpoint == "https://primary.example/openai/v1"
    model = builtin_runtime._default_model_factory(target)  # noqa: SLF001
    assert type(model) is builtin_runtime.OciResponsesModel
    assert model.provider_name == "oracle"


def test_runtime_status_is_ready_with_the_messages_api(monkeypatch: MonkeyPatch) -> None:
    """既定のテキストモデルがクォータナリ接続でも、実行環境は準備できている。"""
    _use_settings(monkeypatch, _settings())

    status = builtin_runtime.runtime_status()

    assert (status["ready"], status["error_code"], status["model_id"]) == (True, None, CLAUDE)
    assert status["model_provider"] == "Anthropic Claude（Messages API）"


# --------------------------------------------------------------------------- 1 回の生成


class _Proposal(BaseModel):
    title: str = Field(description="合成の項目の説明 A")
    count: int


def test_structured_single_turn_calls_messages_api(
    monkeypatch: MonkeyPatch, api: FakeMessagesApi
) -> None:
    """構造化出力の 1 回の生成: 指示・schema・推論の量・出力の上限・接続で Messages API を呼ぶ。"""
    seen: list[dict[str, Any]] = []
    original = acreate_anthropic_turn

    async def spy(request: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return await original(request, **kwargs)

    monkeypatch.setattr(anthropic_model, "acreate_anthropic_turn", spy)
    api.reply(
        recorded_turn(
            text(json.dumps({"title": "合成の値", "count": 2}, ensure_ascii=False)),
            input_tokens=10,
            output_tokens=5,
            thinking_tokens=4,
        )
    )
    agent = _agent(_claude_target(), instructions="## Step 1: 合成の指示", output_type=_Proposal)

    result = anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert result.final_output == _Proposal(title="合成の値", count=2)
    request = api.requests[0]
    assert str(request.url).startswith(f"{ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT}/v1/messages")
    assert request.headers["authorization"] == "Bearer sk-ant-synthetic"
    body = api.body()
    assert body["model"] == CLAUDE
    assert body["max_tokens"] == 2048
    assert body["messages"] == [{"role": "user", "content": [text("合成の入力")]}]
    # 項目の説明は Responses API の経路と同じく指示の最後の Step に移し、schema から外す（#5238）。
    assert body["system"].startswith("## Step 1: 合成の指示")
    assert "合成の項目の説明 A" in body["system"]
    assert body["output_config"]["effort"] == "medium"
    schema = body["output_config"]["format"]["schema"]
    assert schema["properties"].keys() == {"title", "count"}
    assert "合成の項目の説明 A" not in json.dumps(schema, ensure_ascii=False)
    assert "tools" not in body and "tool_choice" not in body
    # 実行器を通す呼び出し（sdk_max_retries=0）は SDK の再試行を使わない。
    [kwargs] = seen
    assert (kwargs["endpoint_url"], kwargs["max_retries"]) == (
        ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT,
        0,
    )
    usage = result.context_wrapper.usage
    assert (usage.requests, usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        1,
        10,
        5,
        15,
    )
    assert usage.output_tokens_details.reasoning_tokens == 4


def test_reasoning_none_sends_no_effort(monkeypatch: MonkeyPatch, api: FakeMessagesApi) -> None:
    """推論の量 none は思考・effort を送らない。再試行は openai SDK の経路と同じ。"""
    seen: list[dict[str, Any]] = []
    original = acreate_anthropic_turn

    async def spy(request: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return await original(request, **kwargs)

    monkeypatch.setattr(anthropic_model, "acreate_anthropic_turn", spy)
    api.reply(recorded_turn(text("合成の回答")))
    agent = _agent(
        _claude_target(reasoning_effort="none", sdk_max_retries=None), instructions="合成の指示"
    )

    result = anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert result.final_output == "合成の回答"
    body = api.body()
    assert "thinking" not in body and "output_config" not in body
    assert "anthropic-beta" not in api.requests[0].headers
    assert seen[0]["max_retries"] == DEFAULT_MAX_RETRIES


def test_judge_answer_uses_messages_api_and_retries_output_limit(
    monkeypatch: MonkeyPatch, api: FakeMessagesApi
) -> None:
    """品質評価の判定: 出力の上限で止まったら 1 回だけ送り直す（Responses API の経路と同じ）。"""
    api.reply(recorded_turn(text('{"verdict"'), stop_reason="max_tokens", output_tokens=4096))
    api.reply(
        recorded_turn(
            text(
                json.dumps(
                    {
                        "verdict": "correct",
                        "score": 1,
                        "summary": "合成の理由",
                        "missing_points": [],
                    },
                    ensure_ascii=False,
                )
            )
        )
    )
    settings = _settings(
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(
                model_id=CLAUDE,
                connection_id="quaternary",
                max_input_tokens=200000,
                max_output_tokens=4096,
                reasoning_effort="high",
            )
        ]
    )
    _use_settings(monkeypatch, settings)
    monkeypatch.setattr("app.features.agent.ai_calls.get_settings", lambda: settings)

    judgement, attempts = anyio.run(evaluation.judge_answer, CASE, "合成の回答")

    assert (judgement.verdict.value, attempts) == ("correct", 2)
    first, second = api.body(0), api.body(1)
    assert (first["max_tokens"], first["output_config"]["effort"]) == (4096, "high")
    # 上限がもうモデルの出力の上限なので、推論の量を 1 段下げて送り直す（retry_model_target）。
    assert (second["max_tokens"], second["output_config"]["effort"]) == (4096, "medium")
    assert "合成の回答" in json.dumps(first["messages"], ensure_ascii=False)


def test_incomplete_output_is_the_runtime_incomplete_error(api: FakeMessagesApi) -> None:
    """未完了は Responses API の経路と同じ code（実行器の「出力の打ち切り」）にする。"""
    api.reply(recorded_turn(text("途中"), stop_reason="max_tokens", output_tokens=2048))
    agent = _agent(_claude_target())

    with pytest.raises(builtin_runtime.BuiltinRuntimeError) as raised:
        anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert raised.value.code == builtin_runtime.MODEL_OUTPUT_INCOMPLETE_CODE
    assert CLAUDE in raised.value.message
    caller = make_ai_caller("evaluation_judge", max_concurrency=1)
    assert caller._classify(raised.value).kind is ErrorKind.TRUNCATED  # noqa: SLF001


@pytest.mark.parametrize(
    ("status", "error_type", "kind"),
    [
        (429, "rate_limit_error", ErrorKind.RATE_LIMITED),
        (401, "authentication_error", ErrorKind.AUTHENTICATION),
    ],
    ids=["rate-limit", "authentication"],
)
def test_provider_failures_keep_the_shared_classification(
    api: FakeMessagesApi, status: int, error_type: str, kind: ErrorKind
) -> None:
    """Messages API の失敗は部品の分類のまま上げ、実行器は SDK の HTTP status で種類を決める。"""
    api.reply_error(status, error_type, "合成の失敗")
    agent = _agent(_claude_target())

    with pytest.raises(AnthropicMessagesError) as raised:
        anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert raised.value.status_code == status
    classified = make_ai_caller("evaluation_judge", max_concurrency=1)._classify(raised.value)  # noqa: SLF001
    assert (classified.kind, classified.status) == (kind, status)


# --------------------------------------------------------------------------- 実行（ツール）


def test_parallel_tool_calls_round_trip_with_thinking(api: FakeMessagesApi) -> None:
    """並列のツールの呼び出し: 結果を 1 つの user の message で返し、思考の block を戻す。"""
    api.reply(
        recorded_turn(
            thinking("合成の思考 1", "sig-1"),
            tool_use("toolu_a", "lookup", {"query": "A"}),
            tool_use("toolu_b", "lookup", {"query": "B"}),
            stop_reason="tool_use",
            input_tokens=100,
            output_tokens=30,
        )
    )
    api.reply(
        recorded_turn(
            thinking("合成の思考 2", "sig-2"),
            text("合成の回答"),
            input_tokens=160,
            output_tokens=12,
        )
    )
    calls: list[dict[str, Any]] = []
    agent = _agent(
        _claude_target(),
        instructions="合成の指示",
        tools=[_tool("lookup", "合成の結果", calls)],
    )

    result = anyio.run(lambda: Runner.run(agent, "合成の質問"))

    assert result.final_output == "合成の回答"
    assert calls == [{"query": "A"}, {"query": "B"}]
    first, second = api.body(0), api.body(1)
    [sent_tool] = first["tools"]
    assert (sent_tool["name"], sent_tool["strict"]) == ("lookup", True)
    # strict のツールは構造化出力と同じ変換で送る（受け付けない制約を外す）。
    assert "minLength" not in sent_tool["input_schema"]["properties"]["query"]
    assert first["tool_choice"] == {"type": "auto"}
    # 推論の量を送る呼び出しは、会話の前の部分が変わった思考の block を外して続ける。
    assert first["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert api.requests[0].headers["anthropic-beta"] == THINKING_BINDING_BETA
    assert second["messages"] == [
        {"role": "user", "content": [text("合成の質問")]},
        {
            "role": "assistant",
            "content": [
                thinking("合成の思考 1", "sig-1"),
                tool_use("toolu_a", "lookup", {"query": "A"}),
                tool_use("toolu_b", "lookup", {"query": "B"}),
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_a", "content": "合成の結果"},
                {"type": "tool_result", "tool_use_id": "toolu_b", "content": "合成の結果"},
            ],
        },
    ]
    usage = result.context_wrapper.usage
    assert (usage.requests, usage.input_tokens, usage.output_tokens) == (2, 260, 42)


def test_tool_result_images_are_sent_as_image_blocks(api: FakeMessagesApi) -> None:
    api.reply(recorded_turn(tool_use("toolu_a", "lookup", {"query": "A"}), stop_reason="tool_use"))
    api.reply(recorded_turn(text("合成の回答")))
    output = [ToolOutputText(text="合成の本文"), ToolOutputImage(image_url=PNG_DATA_URL)]
    agent = _agent(_claude_target(reasoning_effort="none"), tools=[_tool("lookup", output, [])])

    anyio.run(lambda: Runner.run(agent, "合成の質問"))

    [result_block] = api.body(1)["messages"][2]["content"]
    assert result_block["content"] == [
        text("合成の本文"),
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
        },
    ]


def test_conversation_history_and_forced_tool_choice_are_sent_as_given(
    api: FakeMessagesApi,
) -> None:
    """会話の履歴は user の message にまとめ、tool_choice は指定どおり（required は any）送る。"""
    api.reply(recorded_turn(tool_use("toolu_a", "lookup", {"query": "A"}), stop_reason="tool_use"))
    api.reply(recorded_turn(text("合成の回答")))
    target = _claude_target(reasoning_effort="none")
    settings = builtin_runtime.model_settings_for(target)
    agent = Agent(
        name="synthetic",
        model=builtin_runtime.model_factory(target),
        model_settings=settings.resolve(
            type(settings)(tool_choice="required", parallel_tool_calls=False)
        ),
        tools=[_tool("lookup", "合成の結果", [])],
        reset_tool_choice=True,
    )
    history: list[Any] = [
        {"role": "user", "content": "合成の履歴"},
        {"role": "user", "content": "合成の質問"},
    ]

    anyio.run(lambda: Runner.run(agent, history))

    first = api.body(0)
    assert first["messages"] == [
        {"role": "user", "content": [text("合成の履歴"), text("合成の質問")]}
    ]
    assert first["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}


def test_unsupported_requests_fail_without_calling_the_model(api: FakeMessagesApi) -> None:
    """Messages API に写せない要求は、呼ばずに理由の分かる失敗にする（別の接続へ切り替えない）。"""
    agent = _agent(_claude_target())
    history: list[Any] = [{"role": "developer", "content": "合成の指示"}]

    with pytest.raises(builtin_runtime.BuiltinRuntimeError) as raised:
        anyio.run(lambda: Runner.run(agent, history, max_turns=1))

    assert raised.value.code == "runtime.model_failed"
    assert "developer" in raised.value.message
    assert api.requests == []


def test_messages_api_overflow_is_parsed_like_other_providers() -> None:
    assert provider_overflow(
        None, "prompt is too long: 210000 tokens > 200000 maximum"
    ) == ProviderOverflow(prompt_tokens=210000, limit_tokens=200000)


def test_messages_api_overflow_compacts_and_retries_once(
    monkeypatch: MonkeyPatch, api: FakeMessagesApi
) -> None:
    """入力の超過（400）は Responses API の経路と同じく、縮めた要求で 1 回だけ送り直す。"""
    api.reply_error(
        400, "invalid_request_error", "prompt is too long: 210000 tokens > 200000 maximum"
    )
    api.reply(recorded_turn(text("合成の回答")))
    retried: list[ProviderOverflow] = []

    class _Window:
        provider_limit = None
        last_input = None

        def retry_request(self, request: dict[str, Any], overflow: ProviderOverflow) -> Any:
            retried.append(overflow)
            return {**request, "input": [{"role": "user", "content": "縮めた入力"}]}

        def fail_after_retry(self, overflow: ProviderOverflow) -> Any:
            pytest.fail("送り直しは 1 回で通る")

        def measure(self, response: Any) -> None:
            del response

    token = builtin_runtime._ACTIVE_CONTEXT_WINDOW.set(_Window())  # type: ignore[arg-type]  # noqa: SLF001
    try:
        result = anyio.run(lambda: Runner.run(_agent(_claude_target()), "長い入力", max_turns=1))
    finally:
        builtin_runtime._ACTIVE_CONTEXT_WINDOW.reset(token)  # noqa: SLF001

    assert result.final_output == "合成の回答"
    assert retried == [ProviderOverflow(prompt_tokens=210000, limit_tokens=200000)]
    assert api.body(1)["messages"] == [{"role": "user", "content": [text("縮めた入力")]}]


def test_model_call_span_records_the_anthropic_provider(api: FakeMessagesApi) -> None:
    """モデルの呼び出しの span は Responses API の経路と同じ層で残し、provider は anthropic。"""
    from pr_backend_core.observability.call_policy import call_content_policy
    from pr_backend_core.observability.tracing import operation_trace

    api.reply(recorded_turn(tool_use("toolu_a", "lookup", {"query": "A"}), stop_reason="tool_use"))
    api.reply(recorded_turn(text("合成の回答")))
    agent = _agent(_claude_target(), tools=[_tool("lookup", "合成の結果", [])])

    async def run() -> SimpleNamespace:
        with (
            call_content_policy(lambda: True),
            operation_trace("agent.execute", operation_id="synthetic-anthropic-run") as summary,
        ):
            await Runner.run(agent, "合成の質問")
        return SimpleNamespace(summary=summary)

    summary = anyio.run(run).summary
    chats = [span for span in summary.spans if span.operation_name == "gen_ai.chat"]
    assert len(chats) == 2
    assert {span.attributes["gen_ai.provider.name"] for span in chats} == {"anthropic"}
    assert {span.attributes["gen_ai.request.model"] for span in chats} == {CLAUDE}
    assert "sk-ant-synthetic" not in summary.model_dump_json()


# --------------------------------------------------------------------------- 画像の読み取り


class _Stub:
    """`acreate_anthropic_message` の決定論のスタブ（要求を記録し、台本の結果を返す）。"""

    def __init__(self, *outcomes: str) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[AnthropicMessagesRequest, dict[str, Any]]] = []

    async def __call__(
        self, request: AnthropicMessagesRequest, **kwargs: Any
    ) -> AnthropicMessagesResult:
        self.calls.append((request, kwargs))
        return AnthropicMessagesResult(
            text=self.outcomes.pop(0),
            model=request.model_id,
            stop_reason="end_turn",
            usage=AnthropicUsage(input_tokens=10, output_tokens=5),
            request_id="req_synthetic",
        )


def _install(monkeypatch: MonkeyPatch, stub: _Stub) -> None:
    monkeypatch.setattr(anthropic_model, "acreate_anthropic_message", stub)


def test_image_reader_calls_messages_api(monkeypatch: MonkeyPatch) -> None:
    """既定の Vision モデルがクォータナリ接続なら、画像の読み取りも Messages API で呼ぶ。"""
    import openai

    stub = _Stub("合成の読み取り結果")
    _install(monkeypatch, stub)
    _use_settings(monkeypatch, _settings())
    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **_: pytest.fail("Responses API を呼んではいけない"),
    )

    reader = attachment_module._image_reader("合成の指示")  # noqa: SLF001
    assert reader is not None
    assert anyio.run(reader, b"synthetic-image", "image/png") == "合成の読み取り結果"

    [(request, kwargs)] = stub.calls
    assert (request.model_id, request.user_text, request.reasoning_effort) == (
        CLAUDE,
        "合成の指示",
        "high",
    )
    [image] = request.images
    assert (image.media_type, image.data_base64) == ("image/png", "c3ludGhldGljLWltYWdl")
    assert request.max_output_tokens == 4096
    assert kwargs["timeout_seconds"] == attachment_module.IMAGE_READ_TIMEOUT_SECONDS
    assert kwargs["endpoint_url"] == ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT


def test_image_reader_span_records_purpose_without_image_data(monkeypatch: MonkeyPatch) -> None:
    """画像の読み取りの span は用途と usage を残し、画像のデータ・API key を入れない。"""
    from pr_backend_core.observability.call_policy import call_content_policy
    from pr_backend_core.observability.tracing import operation_trace

    _install(monkeypatch, _Stub("合成の読み取り結果"))
    _use_settings(monkeypatch, _settings())
    reader = attachment_module._image_reader("合成の指示")  # noqa: SLF001
    assert reader is not None

    async def read() -> SimpleNamespace:
        with (
            call_content_policy(lambda: True),
            operation_trace("agent.execute", operation_id="synthetic-anthropic-read") as summary,
        ):
            await reader(b"synthetic-image", "image/png")
        return SimpleNamespace(summary=summary)

    summary = anyio.run(read).summary
    chat = next(span for span in summary.spans if span.operation_name == "gen_ai.chat")
    assert chat.attributes["pr.gen_ai.purpose"] == "document.read"
    assert chat.attributes["gen_ai.provider.name"] == "anthropic"
    assert chat.attributes["gen_ai.request.model"] == CLAUDE
    dumped = summary.model_dump_json()
    assert "c3ludGhldGljLWltYWdl" not in dumped and "sk-ant-synthetic" not in dumped
