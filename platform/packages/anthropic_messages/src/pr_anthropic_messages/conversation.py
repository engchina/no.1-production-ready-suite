"""会話の履歴とツールの往復（tool_use / tool_result）で Messages API を呼ぶ部品（#5580）。

1 回の生成（:mod:`pr_anthropic_messages.messages`）と同じ規則（API key・Endpoint URL・推論の量・
構造化出力・常に streaming・失敗の分類）で、複数のターンの会話を 1 回呼ぶ。ツールを実行して
結果を返すループ（Agent の実行）は呼ぶ側が持ち、ここでは 1 回の呼び出しを組み、応答を
次の要求へそのまま戻せる形で返すだけにする。

## 使い方

```python
from pr_anthropic_messages import (
    AnthropicConversationRequest,
    AnthropicTool,
    acreate_anthropic_turn,
    anthropic_messages,
    anthropic_text_block,
    anthropic_tool_result_block,
)

messages = anthropic_messages(
    [
        ("user", [anthropic_text_block(question)]),
        ("assistant", list(previous.content)),  # 前の応答の block（思考の block も署名ごと）
        ("user", [anthropic_tool_result_block(tool_use.id, result_text)]),
    ]
)
turn = await acreate_anthropic_turn(
    AnthropicConversationRequest(
        model_id=model_id,
        system=system_prompt,
        messages=messages,
        tools=(AnthropicTool(name=..., input_schema=..., description=..., strict=True),),
        reasoning_effort=reasoning_effort,
        max_output_tokens=budget,
    ),
    api_key=connection.api_key,
    endpoint_url=connection.effective_endpoint,
)
turn.tool_uses  # 呼ぶツール（無ければ最終の回答。本文は turn.text）
turn.content    # 次の要求の assistant の message にそのまま入れる block
```

## 要求の組み方

- ツール: `strict` のツールは `input_schema` を構造化出力と同じ変換（`anthropic_response_schema`）で
  送る。`tool_choice` は `auto` / `any` / `none` / 名前を指定した `tool`。
  `parallel_tool_calls=False` は `disable_parallel_tool_use`（ツールを渡し、`none` でないとき
  だけ）。forced の `any` / `tool` を受け付けないモデル（Claude Opus 5.5 など）は
  400（`request`）になる。別の指定へ読み替えない。
- 会話: :func:`anthropic_messages` が同じ役割の続く message を 1 つにまとめ、user の message では
  tool_result の block を先頭に置く（並列のツールの結果は 1 つの user の message で返す）。
- 思考の block: 推論の量を送る（adaptive）呼び出しは、前の応答の thinking / redacted_thinking の
  block を変えずに戻し、`thinking.block_binding.prefix_mismatch_behavior="drop_block"`（beta
  `thinking-binding-controls-2026-08-01`）を付ける。会話の前の部分（指示・ツール・前の message）が
  変わって検証に通らない block は API が外して続ける（入力の上限に収めるための縮小・承認の後の
  再開で指示が変わっても 400 にしない）。外した数は結果の `dropped_thinking_blocks`。
  推論の量を送らない（none）呼び出しは `block_binding` を付けられない（adaptive のときだけ）ので、
  モデルの既定の思考の block を戻さない（会話の前の部分が変わったときの 400 を避ける。前から
  外すのは API が認める）。
- 停止の理由: `end_turn`・`stop_sequence`・`tool_use` を終わりとする。`pause_turn`（server tool の
  ループの途中。この部品は server tool を送らない）・`max_tokens` などは 1 回の生成と同じ
  `incomplete`、`refusal` は `refusal`。本文もツールの呼び出しも無い応答は `response_format`。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, cast

import anthropic

from pr_anthropic_messages.messages import (
    DEFAULT_TIMEOUT_SECONDS,
    AnthropicImage,
    AnthropicUsage,
    _empty_response_error,
    _raise_for_stop_reason,
    _reasoning_and_format_params,
    _require_api_key,
    _usage,
    _with_model,
    anthropic_base_url,
    anthropic_effort,
    anthropic_response_schema,
    classify_anthropic_error,
)

__all__ = [
    "THINKING_BINDING_BETA",
    "AnthropicConversationRequest",
    "AnthropicTool",
    "AnthropicToolChoice",
    "AnthropicToolUse",
    "AnthropicTurnResult",
    "acreate_anthropic_turn",
    "anthropic_messages",
    "anthropic_text_block",
    "anthropic_tool_result_block",
    "anthropic_tool_use_block",
]

AnthropicToolChoice = Literal["auto", "any", "none", "tool"]
_TOOL_CHOICES: Final[frozenset[str]] = frozenset({"auto", "any", "none", "tool"})
# 会話の前の部分が変わった思考の block を、400 にせず外して続ける beta（Anthropic の文書）。
THINKING_BINDING_BETA: Final = "thinking-binding-controls-2026-08-01"
# ツールの呼び出しで止まった応答も、この呼び出しとしては終わり（ツールの実行は呼ぶ側）。
_TURN_COMPLETED_STOP_REASONS: Final = frozenset({"end_turn", "stop_sequence", "tool_use"})
_THINKING_BLOCK_TYPES: Final = frozenset({"thinking", "redacted_thinking"})


# --------------------------------------------------------------------------- 入力


def anthropic_text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def anthropic_tool_use_block(
    tool_use_id: str, name: str, input: Mapping[str, Any]
) -> dict[str, Any]:
    """assistant のツールの呼び出し（前の応答の `tool_use` を戻すとき）。"""
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": dict(input)}


def anthropic_tool_result_block(
    tool_use_id: str,
    content: str | Sequence[Mapping[str, Any] | AnthropicImage],
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    """ツールの結果（本文か、text と画像の block の列）。空の本文の block は送らない。"""
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id}
    if isinstance(content, str):
        if content:
            block["content"] = content
    else:
        parts = [
            part.to_param() if isinstance(part, AnthropicImage) else dict(part)
            for part in content
            if isinstance(part, AnthropicImage) or part.get("type") != "text" or part.get("text")
        ]
        if parts:
            block["content"] = parts
    if is_error:
        block["is_error"] = True
    return block


def anthropic_messages(
    turns: Iterable[tuple[Literal["user", "assistant"], Sequence[Mapping[str, Any]]]],
) -> list[dict[str, Any]]:
    """役割と block の列を、Messages API の `messages` にする。

    同じ役割の続く message は 1 つにまとめ（並列のツールの結果を 1 つの user の message で返す）、
    user の message では tool_result の block を先頭に置く（Messages API の規則）。空の message は
    送らない。
    """
    messages: list[dict[str, Any]] = []
    for role, blocks in turns:
        content = [dict(block) for block in blocks]
        if not content:
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(content)
        else:
            messages.append({"role": role, "content": content})
    for message in messages:
        if message["role"] == "user":
            content = message["content"]
            message["content"] = [b for b in content if b.get("type") == "tool_result"] + [
                b for b in content if b.get("type") != "tool_result"
            ]
    return messages


@dataclass(frozen=True)
class AnthropicTool:
    """モデルに渡すツール（client tool）の定義。"""

    name: str
    input_schema: Mapping[str, Any]
    description: str = ""
    # 引数を schema どおりにする（strict tool use）。schema は構造化出力と同じ変換で送る。
    strict: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ツールの名前が空です。")

    def to_param(self) -> dict[str, Any]:
        schema = (
            anthropic_response_schema(self.input_schema) if self.strict else dict(self.input_schema)
        )
        param: dict[str, Any] = {"name": self.name, "input_schema": schema}
        if self.description:
            param["description"] = self.description
        if self.strict:
            param["strict"] = True
        return param


@dataclass(frozen=True)
class AnthropicConversationRequest:
    """会話の履歴とツールつきの 1 回の呼び出しの入力。"""

    model_id: str
    # Messages API の message（:func:`anthropic_messages` で組む）。最初は user の message。
    messages: Sequence[Mapping[str, Any]]
    max_output_tokens: int
    system: str = ""
    tools: Sequence[AnthropicTool] = ()
    tool_choice: AnthropicToolChoice = "auto"
    # `tool_choice="tool"` のときに呼ばせるツールの名前。
    tool_name: str = ""
    parallel_tool_calls: bool = True
    response_schema: Mapping[str, Any] | None = None
    reasoning_effort: str = ""

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("モデル ID が空です。")
        if self.max_output_tokens <= 0:
            raise ValueError("出力の上限は正の整数で指定してください。")
        if not self.messages:
            raise ValueError("会話の message が要ります。")
        if self.tool_choice not in _TOOL_CHOICES:
            raise ValueError(f"tool_choice {self.tool_choice!r} は Messages API に写せません。")
        if self.tool_choice == "tool" and not self.tool_name:
            raise ValueError("tool_choice が tool のときはツールの名前が要ります。")
        anthropic_effort(self.reasoning_effort)


@dataclass(frozen=True)
class AnthropicToolUse:
    """モデルが求めたツールの呼び出し。"""

    id: str
    name: str
    input: Mapping[str, Any]


@dataclass(frozen=True)
class AnthropicTurnResult:
    """1 回の呼び出しの結果。`content` は次の要求の assistant の message へそのまま戻す。"""

    # 応答の block（応答の順のまま。thinking・redacted_thinking は署名・データごと）。
    content: tuple[dict[str, Any], ...]
    text: str
    tool_uses: tuple[AnthropicToolUse, ...]
    model: str
    stop_reason: str
    usage: AnthropicUsage
    request_id: str = ""
    # 会話の前の部分が変わって API が外した思考の block の数（drop_block）。
    dropped_thinking_blocks: int = 0


# --------------------------------------------------------------------------- 要求


def _without_thinking(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, list):
            kept = [block for block in content if block.get("type") not in _THINKING_BLOCK_TYPES]
            if kept:
                result.append({**message, "content": kept})
            continue
        result.append(dict(message))
    return result


def _tool_choice_param(request: AnthropicConversationRequest) -> dict[str, Any]:
    choice: dict[str, Any] = {"type": request.tool_choice}
    if request.tool_choice == "tool":
        choice["name"] = request.tool_name
    if not request.parallel_tool_calls and request.tool_choice != "none":
        choice["disable_parallel_tool_use"] = True
    return choice


def _turn_params(request: AnthropicConversationRequest) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": request.model_id.strip(),
        "max_tokens": request.max_output_tokens,
    }
    if request.system:
        params["system"] = request.system
    params.update(_reasoning_and_format_params(request.reasoning_effort, request.response_schema))
    thinking = params.get("thinking")
    if thinking is not None:
        params["thinking"] = {
            **thinking,
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        }
        params["betas"] = [THINKING_BINDING_BETA]
        params["messages"] = [dict(message) for message in request.messages]
    else:
        params["messages"] = _without_thinking(request.messages)
    if request.tools:
        params["tools"] = [tool.to_param() for tool in request.tools]
        params["tool_choice"] = _tool_choice_param(request)
    return params


# --------------------------------------------------------------------------- 応答


def _dropped_thinking_blocks(message: Any) -> int:
    transformations = getattr(message, "input_transformations", None) or ()
    return sum(1 for item in transformations if getattr(item, "type", "") == "thinking_dropped")


def _turn_result(
    message: Any, request: AnthropicConversationRequest, *, request_id: str
) -> AnthropicTurnResult:
    model_id = request.model_id.strip()
    usage = _usage(message)
    stop_reason = _raise_for_stop_reason(
        message,
        model_id=model_id,
        max_output_tokens=request.max_output_tokens,
        usage=usage,
        request_id=request_id,
        completed=_TURN_COMPLETED_STOP_REASONS,
    )
    blocks = tuple(
        cast(dict[str, Any], block.model_dump(mode="json", exclude_none=True))
        for block in message.content
    )
    text = "".join(str(block.get("text") or "") for block in blocks if block["type"] == "text")
    tool_uses = tuple(
        AnthropicToolUse(
            id=str(block["id"]), name=str(block["name"]), input=dict(block.get("input") or {})
        )
        for block in blocks
        if block["type"] == "tool_use"
    )
    if (stop_reason == "tool_use" and not tool_uses) or (not tool_uses and not text.strip()):
        raise _empty_response_error(
            model_id=model_id, stop_reason=stop_reason, usage=usage, request_id=request_id
        )
    return AnthropicTurnResult(
        content=blocks,
        text=text,
        tool_uses=tool_uses,
        model=str(message.model or model_id),
        stop_reason=stop_reason,
        usage=usage,
        request_id=request_id,
        dropped_thinking_blocks=_dropped_thinking_blocks(message),
    )


# --------------------------------------------------------------------------- 呼び出し


async def acreate_anthropic_turn(
    request: AnthropicConversationRequest,
    *,
    api_key: str,
    endpoint_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 0,
    http_client: anthropic.DefaultAsyncHttpxClient | None = None,
) -> AnthropicTurnResult:
    """会話の履歴とツールで Messages API を 1 回呼ぶ（失敗は `AnthropicMessagesError`）。

    引数の意味は :func:`pr_anthropic_messages.acreate_anthropic_message` と同じ。
    `thinking.block_binding` は beta の要求でだけ送れるので、beta の messages で呼ぶ。
    """
    model_id = request.model_id.strip()
    key = _require_api_key(api_key, model_id)
    params = _turn_params(request)
    try:
        async with (
            anthropic.AsyncAnthropic(
                api_key=key,
                base_url=anthropic_base_url(endpoint_url),
                timeout=timeout_seconds,
                max_retries=max_retries,
                http_client=http_client,
            ) as client,
            client.beta.messages.stream(**params) as stream,
        ):
            message = await stream.get_final_message()
            request_id = stream.request_id or ""
    except anthropic.APIError as exc:
        raise _with_model(classify_anthropic_error(exc), model_id) from exc
    return _turn_result(message, request, request_id=request_id)
