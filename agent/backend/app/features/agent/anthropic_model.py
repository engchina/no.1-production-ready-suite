"""クォータナリ接続（Anthropic Claude の Messages API）のモデルを呼ぶ層（#5546）。

Messages API の呼び出しは platform の共通の部品 `pr_anthropic_messages`（公式の SDK `anthropic`）が
行い、ここでは Agent の呼び出しの経路をつなぐだけにする。

- :func:`call_anthropic_messages`: 1 回の生成を呼び、Responses API の経路と同じ `gen_ai.chat` の
  span（usage は Responses API の形）を残す。Agent が Messages API を呼ぶのはこの関数だけ。
- :class:`AnthropicMessagesModel`: OpenAI Agents SDK の `Model`。品質評価の判定・下書きの提案の
  ように、ツールを渡さない 1 回の生成（`max_turns=1`・構造化出力）に使う。
  `builtin_runtime.model_factory` がクォータナリ接続のモデルでこれを返す。

業務 Agent の実行（ツールの呼び出し・複数のターン）は Messages API のツールの往復（tool_use /
tool_result）と思考の block の受け渡しが要り、まだ対応しない。実行の前に
`builtin_runtime.require_runtime_model` が理由の分かる失敗にし、この Model もツール・会話の
履歴を渡されたら呼ばずに同じ失敗にする（別のモデル・接続へ暗黙に切り替えない）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, Never

from agents import Model, ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import ModelTracing
from agents.tool import Tool
from agents.usage import Usage
from openai import DEFAULT_MAX_RETRIES
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_prompt_param import ResponsePromptParam
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
from pr_anthropic_messages import (
    DEFAULT_TIMEOUT_SECONDS,
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    acreate_anthropic_message,
)
from pr_backend_core.observability.genai import record_genai_usage
from pr_backend_core.observability.model_purpose import model_purpose_attributes
from pr_backend_core.observability.tracing import observed_span
from pr_backend_core.output_budget import output_limit_message
from pr_backend_core.response_schema import with_schema_field_guide

from app.features.agent.builtin_runtime import (
    MODEL_OUTPUT_INCOMPLETE_CODE,
    BuiltinRuntimeError,
    ModelTarget,
    runtime_model_not_supported,
)

logger = logging.getLogger(__name__)

__all__ = ["AnthropicMessagesModel", "call_anthropic_messages"]


async def call_anthropic_messages(
    request: AnthropicMessagesRequest,
    *,
    api_key: str,
    endpoint_url: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 0,
    purpose: str | None = None,
) -> AnthropicMessagesResult:
    """Messages API を 1 回呼ぶ（失敗は `AnthropicMessagesError`。分類は部品のまま）。

    `purpose` は span の用途（`pr.gen_ai.purpose`。None は呼び出しの文脈の用途）。
    """
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        **model_purpose_attributes(default="agent.choose_action"),
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": request.model_id,
    }
    if purpose:
        attributes["pr.gen_ai.purpose"] = purpose
    with observed_span("gen_ai.chat", kind="client", attributes=attributes) as span:
        # 本文と画像の量だけ（画像のデータ・API key は入れない）。
        span.set_call_input(
            {
                "model": request.model_id,
                "system": request.system,
                "user_text": request.user_text,
                "images": len(request.images),
                "structured_output": request.response_schema is not None,
                "reasoning_effort": request.reasoning_effort,
                "max_output_tokens": request.max_output_tokens,
            }
        )
        try:
            result = await acreate_anthropic_message(
                request,
                api_key=api_key,
                endpoint_url=endpoint_url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
        except AnthropicMessagesError as exc:
            span.set_call_output({"状態": "呼出失敗", "失敗の種類": exc.kind})
            if exc.usage is not None:
                record_genai_usage(span, exc.usage.as_responses_usage())
            raise
        span.set_call_output({"text": result.text, "stop_reason": result.stop_reason})
        span.set_attributes({"gen_ai.response.model": result.model})
        record_genai_usage(span, result.usage.as_responses_usage())
    return result


def _not_supported(target: ModelTarget, detail: str) -> Never:
    logger.warning(
        "agent_anthropic_messages_not_supported",
        extra={"model": target.model_id, "detail": detail},
    )
    raise runtime_model_not_supported(target)


def _user_input(
    target: ModelTarget, input: str | list[TResponseInputItem]
) -> tuple[str, tuple[AnthropicImage, ...]]:
    """SDK の入力（1 つの利用者のメッセージ）を、本文と画像にする。

    会話の履歴・ツールの結果を含む入力は Messages API の 1 回の生成にできないので、呼ばずに失敗に
    する（業務 Agent の実行は `require_runtime_model` が先に止める）。
    """
    if isinstance(input, str):
        return input, ()
    if len(input) != 1:
        _not_supported(target, "1 つのメッセージではない入力")
    data: Any = input[0]
    if (
        not isinstance(data, Mapping)
        or data.get("role") != "user"
        or data.get("type", "message") != "message"
    ):
        _not_supported(target, "利用者のメッセージではない入力")
    content = data.get("content")
    if isinstance(content, str):
        return content, ()
    texts: list[str] = []
    images: list[AnthropicImage] = []
    for part in content if isinstance(content, list) else []:
        kind = part.get("type") if isinstance(part, Mapping) else None
        if kind == "input_text":
            texts.append(str(part.get("text") or ""))
        elif kind == "input_image" and isinstance(part.get("image_url"), str):
            images.append(AnthropicImage.from_data_url(part["image_url"]))
        else:
            _not_supported(target, f"Messages API に渡せない入力（{kind}）")
    return "\n".join(texts), tuple(images)


def _usage(result: AnthropicMessagesResult) -> Usage:
    usage = result.usage
    return Usage(
        requests=1,
        input_tokens=usage.total_input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_input_tokens + usage.output_tokens,
        input_tokens_details=InputTokensDetails(
            cached_tokens=usage.cache_read_input_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens,
        ),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=usage.thinking_tokens or 0),
    )


class AnthropicMessagesModel(Model):
    """クォータナリ接続のモデルの 1 回の生成（OpenAI Agents SDK の `Model`。#5546）。

    推論の量は SDK の `ModelSettings.reasoning`（`model_settings_for` が登録モデルの値で作る）、
    出力の上限は `ModelSettings.max_tokens`、構造化出力は `output_schema` の JSON schema を使う。
    構造化出力の項目の説明は、Responses API の経路と同じく指示の最後の Step に移す（#5238）。
    """

    def __init__(self, target: ModelTarget) -> None:
        self._target = target

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        target = self._target
        if tools or handoffs or previous_response_id or conversation_id or prompt:
            _not_supported(target, "ツール・会話の続きを渡す呼び出し")
        user_text, images = _user_input(target, input)
        instructions = system_instructions or ""
        schema: dict[str, Any] | None = None
        if output_schema is not None and not output_schema.is_plain_text():
            instructions, schema = with_schema_field_guide(
                instructions, output_schema.json_schema()
            )
        reasoning = model_settings.reasoning
        effort = str(reasoning.effort or "") if reasoning is not None else ""
        max_tokens = model_settings.max_tokens or target.max_output_tokens
        request = AnthropicMessagesRequest(
            model_id=target.model_id,
            system=instructions,
            user_text=user_text,
            images=images,
            response_schema=schema,
            reasoning_effort=effort,
            max_output_tokens=max_tokens,
        )
        try:
            result = await call_anthropic_messages(
                request,
                api_key=target.api_key,
                endpoint_url=target.endpoint,
                # openai SDK の経路と同じ再試行（None は SDK の既定。実行器を通す呼び出しは 0）。
                max_retries=(
                    DEFAULT_MAX_RETRIES
                    if target.sdk_max_retries is None
                    else target.sdk_max_retries
                ),
            )
        except AnthropicMessagesError as exc:
            if exc.incomplete is None:
                raise
            # 出力の上限などの未完了は、Responses API の経路と同じ code にする（判定・下書きの
            # 実行器が「出力の打ち切り」として上限を広げて 1 回だけ送り直す。#1845・#2023）。
            message = (
                output_limit_message(model=target.model_id, capacity=max_tokens)
                + " 詳細: "
                + exc.incomplete.detail()
                if exc.incomplete.output_limit_reached
                else str(exc)
            )
            raise BuiltinRuntimeError(MODEL_OUTPUT_INCOMPLETE_CODE, message) from exc
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id=result.request_id or "msg_anthropic",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(type="output_text", text=result.text, annotations=[])
                    ],
                )
            ],
            usage=_usage(result),
            response_id=None,
            request_id=result.request_id or None,
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        # Agent は SDK の streaming の実行を使わない（Runner.run だけ）。
        _not_supported(self._target, "streaming の呼び出し")
