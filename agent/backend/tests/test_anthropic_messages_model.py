"""クォータナリ接続（Anthropic Claude の Messages API）のモデルの呼び出しの経路（#5546）。

- 1 回の生成（品質評価の判定・下書きの提案・画像の読み取り）は `pr_anthropic_messages` で呼ぶ。
- 業務 Agent の実行（ツールの呼び出し・複数のターン）は、モデルを呼ばずに理由の分かる失敗にする
  （別のモデル・接続へ切り替えない）。
- ほかの接続のモデルは従来どおり OpenAI 互換の Responses API。

Messages API は呼ばない（`acreate_anthropic_message` を決定論のスタブに置き換える。SDK の要求の
組み方・応答と失敗の分類は `pr_anthropic_messages` のテストが確かめる）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anthropic
import anyio
import httpx2
import pytest
from agents import Agent, FunctionTool, Runner
from pr_anthropic_messages import (
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    AnthropicUsage,
    classify_anthropic_error,
)
from pr_backend_core.bounded_calls import ErrorKind
from pr_enterprise_ai.errors import EnterpriseAiIncomplete
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
from app.features.agent.evaluation import EvaluationCase
from app.settings import Settings

CLAUDE = "claude-model-a"
CASE = EvaluationCase(id="c1", question="合成の質問 A", expected="合成の期待する回答 A")


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


class _Stub:
    """`acreate_anthropic_message` の決定論のスタブ（要求を記録し、台本の結果か失敗を返す）。"""

    def __init__(self, *outcomes: str | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[AnthropicMessagesRequest, dict[str, Any]]] = []

    async def __call__(
        self, request: AnthropicMessagesRequest, **kwargs: Any
    ) -> AnthropicMessagesResult:
        self.calls.append((request, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return AnthropicMessagesResult(
            text=outcome,
            model=request.model_id,
            stop_reason="end_turn",
            usage=AnthropicUsage(
                input_tokens=10,
                output_tokens=5,
                cache_read_input_tokens=3,
                cache_creation_input_tokens=2,
                thinking_tokens=4,
            ),
            request_id="req_synthetic",
        )


def _install(monkeypatch: MonkeyPatch, stub: _Stub) -> None:
    monkeypatch.setattr(anthropic_model, "acreate_anthropic_message", stub)


def _judgement(verdict: str = "correct") -> str:
    return json.dumps(
        {"verdict": verdict, "score": 1, "summary": "合成の理由", "missing_points": []},
        ensure_ascii=False,
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
    assert isinstance(builtin_runtime._default_model_factory(target), AnthropicMessagesModel)  # noqa: SLF001

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
    assert isinstance(
        builtin_runtime._default_model_factory(target),  # noqa: SLF001
        builtin_runtime.OciResponsesModel,
    )
    builtin_runtime.require_runtime_model(target)


def test_runtime_status_reports_that_runs_need_another_connection(
    monkeypatch: MonkeyPatch,
) -> None:
    """既定のテキストモデルがクォータナリ接続なら、実行環境は準備できていないと示す。"""
    _use_settings(monkeypatch, _settings())

    status = builtin_runtime.runtime_status()

    assert (status["ready"], status["error_code"]) == (
        False,
        builtin_runtime.MODEL_CONNECTION_NOT_SUPPORTED_CODE,
    )
    assert "クォータナリ接続" in status["message"] and CLAUDE in status["message"]


# --------------------------------------------------------------------------- 1 回の生成


class _Proposal(BaseModel):
    title: str = Field(description="合成の項目の説明 A")
    count: int


def test_structured_single_turn_calls_messages_api(monkeypatch: MonkeyPatch) -> None:
    """構造化出力の 1 回の生成: 指示・schema・推論の量・出力の上限・接続で Messages API を呼ぶ。"""
    stub = _Stub(json.dumps({"title": "合成の値", "count": 2}, ensure_ascii=False))
    _install(monkeypatch, stub)
    target = _claude_target()
    agent = Agent(
        name="synthetic",
        instructions="## Step 1: 合成の指示",
        model=builtin_runtime.model_factory(target),
        model_settings=builtin_runtime.model_settings_for(target),
        output_type=_Proposal,
    )

    result = anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert result.final_output == _Proposal(title="合成の値", count=2)
    [(request, kwargs)] = stub.calls
    assert (request.model_id, request.user_text, request.images) == (CLAUDE, "合成の入力", ())
    assert (request.reasoning_effort, request.max_output_tokens) == ("medium", 2048)
    # 項目の説明は Responses API の経路と同じく指示の最後の Step に移し、schema から外す（#5238）。
    assert request.system.startswith("## Step 1: 合成の指示")
    assert "合成の項目の説明 A" in request.system
    assert request.response_schema is not None
    assert "合成の項目の説明 A" not in json.dumps(request.response_schema, ensure_ascii=False)
    assert request.response_schema["properties"].keys() == {"title", "count"}
    assert kwargs == {
        "api_key": "sk-ant-synthetic",
        "endpoint_url": ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT,
        "timeout_seconds": anthropic_model.DEFAULT_TIMEOUT_SECONDS,
        # 実行器を通す呼び出し（sdk_max_retries=0）は SDK の再試行を使わない。
        "max_retries": 0,
    }
    usage = result.context_wrapper.usage
    assert (usage.requests, usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        1,
        15,
        5,
        20,
    )
    assert usage.input_tokens_details.cached_tokens == 3
    assert usage.output_tokens_details.reasoning_tokens == 4


def test_reasoning_none_sends_no_effort(monkeypatch: MonkeyPatch) -> None:
    """推論の量 none は思考・effort を送らない（モデルの既定）。"""
    stub = _Stub("合成の回答")
    _install(monkeypatch, stub)
    target = _claude_target(reasoning_effort="none", sdk_max_retries=None)
    agent = Agent(
        name="synthetic",
        instructions="合成の指示",
        model=builtin_runtime.model_factory(target),
        model_settings=builtin_runtime.model_settings_for(target),
    )

    result = anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert result.final_output == "合成の回答"
    [(request, kwargs)] = stub.calls
    assert request.reasoning_effort == ""
    assert request.response_schema is None
    # 実行器を通さない呼び出しは openai SDK の経路と同じ既定の再試行。
    assert kwargs["max_retries"] == builtin_runtime.DEFAULT_MAX_RETRIES


def test_judge_answer_uses_messages_api_and_retries_output_limit(
    monkeypatch: MonkeyPatch,
) -> None:
    """品質評価の判定: 出力の上限で止まったら 1 回だけ送り直す（Responses API の経路と同じ）。"""
    incomplete = AnthropicMessagesError(
        "合成の未完了",
        kind="incomplete",
        model_id=CLAUDE,
        incomplete=EnterpriseAiIncomplete(
            status="incomplete",
            reason="max_output_tokens",
            max_output_tokens=4096,
            output_tokens=4096,
        ),
    )
    stub = _Stub(incomplete, _judgement())
    _install(monkeypatch, stub)
    _use_settings(
        monkeypatch,
        _settings(
            oci_enterprise_ai_models=[
                EnterpriseAiConfiguredModel(
                    model_id=CLAUDE,
                    connection_id="quaternary",
                    max_input_tokens=200000,
                    max_output_tokens=4096,
                    reasoning_effort="high",
                )
            ]
        ),
    )
    monkeypatch.setattr("app.features.agent.ai_calls.get_settings", builtin_runtime.get_settings)

    judgement, attempts = anyio.run(evaluation.judge_answer, CASE, "合成の回答")

    assert (judgement.verdict.value, attempts) == ("correct", 2)
    first, second = (request for request, _ in stub.calls)
    assert (first.max_output_tokens, first.reasoning_effort) == (4096, "high")
    # 上限がもうモデルの出力の上限なので、推論の量を 1 段下げて送り直す（retry_model_target）。
    assert (second.max_output_tokens, second.reasoning_effort) == (4096, "medium")
    assert "合成の回答" in first.user_text


def test_incomplete_output_is_the_runtime_incomplete_error(monkeypatch: MonkeyPatch) -> None:
    """未完了は Responses API の経路と同じ code（実行器の「出力の打ち切り」）にする。"""
    stub = _Stub(
        AnthropicMessagesError(
            "合成の未完了",
            kind="incomplete",
            incomplete=EnterpriseAiIncomplete(
                status="incomplete", reason="max_output_tokens", max_output_tokens=2048
            ),
        )
    )
    _install(monkeypatch, stub)
    target = _claude_target()
    agent = Agent(name="synthetic", model=builtin_runtime.model_factory(target))

    with pytest.raises(builtin_runtime.BuiltinRuntimeError) as raised:
        anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert raised.value.code == builtin_runtime.MODEL_OUTPUT_INCOMPLETE_CODE
    assert CLAUDE in raised.value.message
    assert isinstance(raised.value.__cause__, AnthropicMessagesError)
    assert (
        make_ai_caller("evaluation_judge", max_concurrency=1)
        ._classify(  # noqa: SLF001
            raised.value
        )
        .kind
        is ErrorKind.TRUNCATED
    )


def _status_error(
    error: Callable[..., anthropic.APIStatusError], status: int
) -> AnthropicMessagesError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, headers={"retry-after": "3"})
    sdk_error = error("合成の失敗", response=response, body=None)
    try:
        raise classify_anthropic_error(sdk_error) from sdk_error
    except AnthropicMessagesError as exc:
        return exc


@pytest.mark.parametrize(
    ("error", "status", "kind"),
    [
        (anthropic.RateLimitError, 429, ErrorKind.RATE_LIMITED),
        (anthropic.AuthenticationError, 401, ErrorKind.AUTHENTICATION),
    ],
    ids=["rate-limit", "authentication"],
)
def test_provider_failures_keep_the_shared_classification(
    monkeypatch: MonkeyPatch,
    error: Callable[..., anthropic.APIStatusError],
    status: int,
    kind: ErrorKind,
) -> None:
    """Messages API の失敗は部品の分類のまま上げ、実行器は SDK の HTTP status で種類を決める。"""
    failure = _status_error(error, status)
    _install(monkeypatch, _Stub(failure))
    target = _claude_target()
    agent = Agent(name="synthetic", model=builtin_runtime.model_factory(target))

    with pytest.raises(AnthropicMessagesError) as raised:
        anyio.run(lambda: Runner.run(agent, "合成の入力", max_turns=1))

    assert raised.value is failure
    classified = make_ai_caller("evaluation_judge", max_concurrency=1)._classify(failure)  # noqa: SLF001
    assert (classified.kind, classified.status) == (kind, status)


# --------------------------------------------------------------------------- 実行（ツール）


def test_tools_are_rejected_without_calling_the_model(monkeypatch: MonkeyPatch) -> None:
    """ツールを渡す呼び出し（業務 Agent の実行）は、Messages API を呼ばずに理由の分かる失敗。"""
    stub = _Stub("呼ばれない")
    _install(monkeypatch, stub)

    async def invoke(_context: Any, _arguments: str) -> str:
        return "{}"

    tool = FunctionTool(
        name="synthetic_tool",
        description="合成のツール",
        params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        on_invoke_tool=invoke,
    )
    agent = Agent(
        name="synthetic", model=builtin_runtime.model_factory(_claude_target()), tools=[tool]
    )

    with pytest.raises(builtin_runtime.BuiltinRuntimeError) as raised:
        anyio.run(lambda: Runner.run(agent, "合成の入力"))

    assert raised.value.code == builtin_runtime.MODEL_CONNECTION_NOT_SUPPORTED_CODE
    assert stub.calls == []


def test_conversation_history_is_rejected(monkeypatch: MonkeyPatch) -> None:
    """会話の履歴（複数のメッセージ）も 1 回の生成にできないので呼ばない。"""
    stub = _Stub("呼ばれない")
    _install(monkeypatch, stub)
    agent = Agent(name="synthetic", model=builtin_runtime.model_factory(_claude_target()))
    history: list[Any] = [
        {"role": "user", "content": "合成の質問 1"},
        {"role": "assistant", "content": "合成の回答 1"},
        {"role": "user", "content": "合成の質問 2"},
    ]

    with pytest.raises(builtin_runtime.BuiltinRuntimeError) as raised:
        anyio.run(lambda: Runner.run(agent, history, max_turns=1))

    assert raised.value.code == builtin_runtime.MODEL_CONNECTION_NOT_SUPPORTED_CODE
    assert stub.calls == []


# --------------------------------------------------------------------------- 画像の読み取り


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
