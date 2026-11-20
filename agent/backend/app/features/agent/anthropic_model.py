"""クォータナリ接続（Anthropic Claude の Messages API）のモデルを呼ぶ層（#5546・#5580）。

Messages API の呼び出しは platform の共通の部品 `pr_anthropic_messages`（公式の SDK `anthropic`）が
行い、ここでは Agent の呼び出しの経路をつなぐだけにする。

- :class:`AnthropicMessagesModel`: OpenAI Agents SDK の `Model`。業務 Agent の実行（ツールの
  呼び出し・並列のツール・複数のターン・承認待ちからの再開）と、品質評価の判定・下書きの提案のような
  1 回の生成のどちらにも使う。`builtin_runtime.model_factory` がクォータナリ接続のモデルで返す。
  Responses API の経路の `OciResponsesModel` と同じ層（`_OciResponses`）を通し、単一の要求の予算・
  Run の実行予算・入力の上限の縮小と超過時の送り直し・未完了の判定・usage と span の記録を
  同じ意味で保つ。違うのは一番内側の `responses.create` だけで、:class:`_AnthropicResponses` が
  SDK の要求（Responses API の形）を Messages API の要求に写し、応答を Responses API の形に戻す。
  SDK の会話の item・保存する `RunState` は Responses API の形のまま。
- :func:`call_anthropic_messages`: 会話の添付の画像の読み取り（SDK の外の 1 回の生成）を呼び、
  Responses API の経路と同じ `gen_ai.chat` の span（usage は Responses API の形）を残す。

## Responses API の形と Messages API の形

- `instructions` → `system`
- user の `message`（`input_text`・data URL の `input_image`）→ user の text・image の block
- assistant の `message`（`output_text`）→ assistant の text の block
- `function_call` → assistant の `tool_use`（`id` は `call_id`）
- `function_call_output`（文字列・`input_text`・`input_image`）→ user の `tool_result`
  （続く結果は 1 つの user の message にまとめる）
- `reasoning`（`encrypted_content` に思考の block を JSON で持つ）→ assistant の thinking /
  redacted_thinking の block（変えずに戻す）
- `tools`（function・strict）→ `tools`（strict の schema は部品が変換する）
- `tool_choice` の auto / none → auto / none。forced の required・名前の指定も auto（#5590）
- `parallel_tool_calls=False` → `disable_parallel_tool_use`
- `text.format` の JSON schema → `output_config.format`
- `reasoning.effort` → adaptive thinking と `output_config.effort`（none は送らない）
- `max_output_tokens` → `max_tokens`

思考の block は `reasoning` item の `encrypted_content` に入れる（SDK は `provider_data` のある
reasoning item を要求から外し、`_oci_request` は reasoning item の `content` を外すため、どちらも
使わない）。別の接続の reasoning item（`encrypted_content` がこの形でないもの）は送らない。
未対応の要求（streaming・会話の続きの ID・function 以外のツール・ファイルの入力など）は呼ばずに
失敗にする（別のモデル・接続へ切り替えない）。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, Never, cast

from openai import DEFAULT_MAX_RETRIES, AsyncOpenAI, NotGiven, Omit
from openai.types.responses import Response
from pr_anthropic_messages import (
    DEFAULT_TIMEOUT_SECONDS,
    AnthropicConversationRequest,
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    AnthropicTool,
    AnthropicTurnResult,
    AnthropicUsage,
    acreate_anthropic_message,
    acreate_anthropic_turn,
    anthropic_messages,
    anthropic_text_block,
    anthropic_tool_result_block,
    anthropic_tool_use_block,
)
from pr_backend_core.observability.genai import record_genai_usage
from pr_backend_core.observability.model_purpose import model_purpose_attributes
from pr_backend_core.observability.tracing import observed_span

if TYPE_CHECKING:
    import anthropic

from app.features.agent.builtin_runtime import (
    BuiltinRuntimeError,
    ModelTarget,
    OciResponsesModel,
    _request_reasoning_effort,
)

logger = logging.getLogger(__name__)

__all__ = ["AnthropicMessagesModel", "call_anthropic_messages"]

# reasoning item の `encrypted_content` に入れる思考の block の印（ほかの接続の値と分ける）。
_THINKING_ENVELOPE_KEY = "anthropic_thinking_block"
# SDK の要求のうち、Messages API へ写さずに読み捨てる引数（送り先の無い指定・SDK の付帯情報）。
_IGNORED_REQUEST_KEYS = frozenset(
    {
        "model",
        "instructions",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "text",
        "reasoning",
        "max_output_tokens",
        "stream",
        "include",
        "store",
        "metadata",
        "extra_headers",
        "extra_query",
        "extra_body",
        "prompt_cache_retention",
        "prompt_cache_options",
        # サンプリングの引数は送らない（部品の方針。Agent は指定しない）。
        "temperature",
        "top_p",
    }
)

# 会話の続き（サーバーに保存した会話）は Messages API に無い（Agent は使わない）。
_UNSUPPORTED_KEYS = ("previous_response_id", "conversation", "prompt", "context_management")


# テストは Messages API の録画の応答を返す HTTP の client に差し替える（呼び出しごとに作る。
# 部品の client が呼び出しの終わりに閉じるため）。None は SDK の既定。
def _default_http_client() -> anthropic.DefaultAsyncHttpxClient | None:
    return None


anthropic_http_client: Callable[[], anthropic.DefaultAsyncHttpxClient | None] = _default_http_client


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
                http_client=anthropic_http_client(),
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


# --------------------------------------------------------------------------- 要求の写し方


def _unsupported(target: ModelTarget, detail: str) -> Never:
    logger.warning(
        "agent_anthropic_messages_request_not_supported",
        extra={"model": target.model_id, "detail": detail},
    )
    raise BuiltinRuntimeError(
        "runtime.model_failed",
        f"モデル「{target.model_id}」（Anthropic Claude の Messages API）に渡せない要求です"
        f"（{detail}）。",
    )


def _given(value: Any) -> bool:
    return value is not None and not isinstance(value, Omit | NotGiven)


def _plain(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    if hasattr(item, "model_dump"):
        return cast(dict[str, Any], item.model_dump(exclude_none=True))
    return {}


def _image_block(target: ModelTarget, part: Mapping[str, Any]) -> dict[str, Any]:
    url = part.get("image_url")
    if not isinstance(url, str) or not url.startswith("data:"):
        _unsupported(target, "data URL でない画像")
    return AnthropicImage.from_data_url(url).to_param()


def _content_blocks(target: ModelTarget, content: Any, *, role: str) -> list[dict[str, Any]]:
    """message・ツールの結果の本文を、text・image の block にする（空の本文は送らない）。"""
    if isinstance(content, str):
        return [anthropic_text_block(content)] if content else []
    blocks: list[dict[str, Any]] = []
    for raw in content if isinstance(content, list) else []:
        part = _plain(raw)
        kind = part.get("type")
        if kind in {"input_text", "output_text", "text"}:
            text = str(part.get("text") or "")
            if text:
                blocks.append(anthropic_text_block(text))
        elif kind == "input_image" and role != "assistant":
            blocks.append(_image_block(target, part))
        else:
            _unsupported(target, f"Messages API に写せない本文（{kind}）")
    return blocks


def _thinking_block(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """reasoning item に入れた思考の block（この接続の形でなければ None）。"""
    encrypted = item.get("encrypted_content")
    if not isinstance(encrypted, str):
        return None
    try:
        envelope = json.loads(encrypted)
    except ValueError:
        return None
    block = envelope.get(_THINKING_ENVELOPE_KEY) if isinstance(envelope, dict) else None
    return dict(block) if isinstance(block, dict) else None


def _tool_use_input(target: ModelTarget, arguments: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) and arguments else {}
    except ValueError:
        _unsupported(target, "JSON でないツールの引数")
    if not isinstance(parsed, dict):
        _unsupported(target, "object でないツールの引数")
    return parsed


def _messages(target: ModelTarget, input: Any) -> list[dict[str, Any]]:
    """SDK の入力（Responses API の item）を Messages API の message にする。"""
    if isinstance(input, str):
        return anthropic_messages([("user", [anthropic_text_block(input)])])
    turns: list[tuple[Literal["user", "assistant"], list[dict[str, Any]]]] = []
    for raw in input if isinstance(input, list) else []:
        item = _plain(raw)
        kind = item.get("type") or ("message" if "role" in item else None)
        if kind == "message":
            role = item.get("role")
            if role not in {"user", "assistant"}:
                _unsupported(target, f"{role} の役割のメッセージ")
            turns.append(
                (
                    cast(Literal["user", "assistant"], role),
                    _content_blocks(target, item.get("content"), role=str(role)),
                )
            )
        elif kind == "function_call":
            turns.append(
                (
                    "assistant",
                    [
                        anthropic_tool_use_block(
                            str(item.get("call_id") or ""),
                            str(item.get("name") or ""),
                            _tool_use_input(target, item.get("arguments")),
                        )
                    ],
                )
            )
        elif kind == "function_call_output":
            output = item.get("output")
            content: str | list[dict[str, Any]] = (
                output if isinstance(output, str) else _content_blocks(target, output, role="tool")
            )
            turns.append(
                ("user", [anthropic_tool_result_block(str(item.get("call_id") or ""), content)])
            )
        elif kind == "reasoning":
            block = _thinking_block(item)
            if block is not None:
                turns.append(("assistant", [block]))
        else:
            _unsupported(target, f"Messages API に写せない入力（{kind}）")
    return anthropic_messages(turns)


def _tools(target: ModelTarget, tools: Any) -> tuple[AnthropicTool, ...]:
    converted: list[AnthropicTool] = []
    for raw in tools if isinstance(tools, list) else []:
        tool = _plain(raw)
        if tool.get("type") != "function":
            _unsupported(target, f"function でないツール（{tool.get('type')}）")
        converted.append(
            AnthropicTool(
                name=str(tool.get("name") or ""),
                input_schema=cast(Mapping[str, Any], tool.get("parameters") or {"type": "object"}),
                description=str(tool.get("description") or ""),
                strict=bool(tool.get("strict")),
            )
        )
    return tuple(converted)


def _tool_choice(target: ModelTarget, value: Any) -> str:
    """SDK の `tool_choice` を Messages API の指定にする（forced の指定は auto。#5590）。

    Claude Opus 5.5・Claude Sonnet 5.5 などは、思考の設定にかかわらず forced の `any` / `tool` を
    400（`tool_choice: type "tool" and "any" are not supported for this model.`）で拒否する。
    Anthropic の文書（Define tools の「Forcing tool use」・API errors の「Forced tool use not
    supported」）が示す代わりは `auto`（strict のツール・指示でツールを選ばせる）。forced を
    受け付けるモデルでも forced の呼び出しは思考を飛ばす（同じ文書）。モデルごとの一覧は持たず、
    この接続では forced の指定（`required`・名前の指定）を常に `auto` で送る。ツールを呼ばずに
    答えた応答は、呼ぶ側の検証（回答案の選択など）がそのまま扱う（ここで補わない）。
    """
    if not _given(value) or value == "auto" or value == "required":
        return "auto"
    if value == "none":
        return "none"
    choice = _plain(value)
    if choice.get("type") == "function" and choice.get("name"):
        return "auto"
    _unsupported(target, f"Messages API に写せない tool_choice（{choice.get('type') or value}）")


def _response_schema(text: Any) -> Mapping[str, Any] | None:
    format_ = _plain(text).get("format") if _given(text) else None
    if isinstance(format_, Mapping) and format_.get("type") == "json_schema":
        schema = format_.get("schema")
        return schema if isinstance(schema, Mapping) else None
    return None


def anthropic_request(
    target: ModelTarget, request: Mapping[str, Any]
) -> AnthropicConversationRequest:
    """SDK の要求（Responses API の `responses.create` の引数）を Messages API の要求にする。"""
    for key in _UNSUPPORTED_KEYS:
        if _given(request.get(key)):
            _unsupported(target, f"{key} の指定")
    if request.get("stream") is True:
        _unsupported(target, "streaming の呼び出し")
    unknown = sorted(
        key
        for key, value in request.items()
        if key not in _IGNORED_REQUEST_KEYS
        and _given(value)
        and key not in {"previous_response_id", "conversation", "prompt", "context_management"}
    )
    if unknown:
        _unsupported(target, "未対応の引数 " + ", ".join(unknown))
    tools = _tools(target, request.get("tools")) if _given(request.get("tools")) else ()
    choice = _tool_choice(target, request.get("tool_choice"))
    instructions = request.get("instructions")
    max_tokens = request.get("max_output_tokens")
    return AnthropicConversationRequest(
        model_id=target.model_id,
        system=instructions if isinstance(instructions, str) else "",
        messages=_messages(target, request.get("input")),
        tools=tools,
        tool_choice=cast(Any, choice),
        parallel_tool_calls=request.get("parallel_tool_calls") is not False,
        response_schema=_response_schema(request.get("text")),
        reasoning_effort=_request_reasoning_effort(request),
        max_output_tokens=max_tokens if isinstance(max_tokens, int) else target.max_output_tokens,
    )


# --------------------------------------------------------------------------- 応答の写し方


def _usage(usage: AnthropicUsage) -> dict[str, Any]:
    return {
        "input_tokens": usage.total_input_tokens,
        "input_tokens_details": {
            "cached_tokens": usage.cache_read_input_tokens,
            "cache_write_tokens": usage.cache_creation_input_tokens,
        },
        "output_tokens": usage.output_tokens,
        "output_tokens_details": {"reasoning_tokens": usage.thinking_tokens or 0},
        "total_tokens": usage.total_input_tokens + usage.output_tokens,
    }


def _output_items(turn: AnthropicTurnResult) -> list[dict[str, Any]]:
    """応答の block を、順のまま Responses API の output の item にする。"""
    response_id = turn.request_id or "msg_anthropic"
    items: list[dict[str, Any]] = []
    for index, block in enumerate(turn.content):
        kind = block.get("type")
        if kind in {"thinking", "redacted_thinking"}:
            items.append(
                {
                    "type": "reasoning",
                    "id": f"rs_{response_id}_{index}",
                    "summary": [],
                    "encrypted_content": json.dumps(
                        {_THINKING_ENVELOPE_KEY: block}, ensure_ascii=False
                    ),
                }
            )
        elif kind == "text":
            items.append(
                {
                    "type": "message",
                    "id": f"msg_{response_id}_{index}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": block.get("text") or "", "annotations": []}
                    ],
                }
            )
        elif kind == "tool_use":
            items.append(
                {
                    "type": "function_call",
                    "id": f"fc_{response_id}_{index}",
                    "call_id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    "status": "completed",
                }
            )
        # ほかの block（server tool など）は送っていないので返らない。
    return items


def _response(
    request: Mapping[str, Any],
    *,
    model: str,
    output: Sequence[dict[str, Any]],
    usage: AnthropicUsage | None,
    request_id: str,
    incomplete_reason: str = "",
) -> Response:
    response = Response.model_validate(
        {
            "id": request_id or "resp_anthropic",
            "created_at": time.time(),
            "model": model,
            "object": "response",
            "output": list(output),
            "parallel_tool_calls": request.get("parallel_tool_calls") is not False,
            "tool_choice": "auto",
            "tools": [],
            "status": "incomplete" if incomplete_reason else "completed",
            "incomplete_details": {"reason": incomplete_reason} if incomplete_reason else None,
            "usage": _usage(usage) if usage is not None else None,
        }
    )
    response._request_id = request_id or None  # noqa: SLF001 - SDK の ModelResponse が読む
    return response


class _AnthropicResponses:
    """`responses.create`（Responses API の形）を Messages API の 1 回の呼び出しで行う。

    `_OciResponses`（予算・入力の上限・未完了の判定・span）の内側に置く。未完了（出力の上限など）は
    失敗にせず `status=incomplete` の応答で返し、`_OciResponses` が Responses API の経路と同じ
    失敗（`runtime.model_output_incomplete`）と予算の精算にする。ほかの失敗は部品の
    `AnthropicMessagesError` のまま上げる（共通の実行器が SDK の HTTP status で分類する）。
    """

    def __init__(self, target: ModelTarget) -> None:
        self._target = target

    async def create(self, **request: Any) -> Response:
        target = self._target
        converted = anthropic_request(target, request)
        try:
            turn = await acreate_anthropic_turn(
                converted,
                api_key=target.api_key,
                endpoint_url=target.endpoint,
                # openai SDK の経路と同じ再試行（None は SDK の既定。実行器を通す呼び出しは 0）。
                max_retries=(
                    DEFAULT_MAX_RETRIES
                    if target.sdk_max_retries is None
                    else target.sdk_max_retries
                ),
                http_client=anthropic_http_client(),
            )
        except AnthropicMessagesError as exc:
            if exc.incomplete is None:
                raise
            return _response(
                request,
                model=target.model_id,
                output=[],
                usage=exc.usage,
                request_id=exc.request_id,
                incomplete_reason=exc.incomplete.reason or "max_output_tokens",
            )
        if turn.dropped_thinking_blocks:
            # 会話の前の部分（指示・縮めたツールの結果など）が変わり、API が思考の block を外した。
            logger.info(
                "agent_anthropic_thinking_blocks_dropped",
                extra={"model": target.model_id, "dropped": turn.dropped_thinking_blocks},
            )
        return _response(
            request,
            model=turn.model,
            output=_output_items(turn),
            usage=turn.usage,
            request_id=turn.request_id,
        )


class _AnthropicClient:
    """`OpenAIResponsesModel` が使う client の形（`responses` だけを Messages API で行う）。"""

    # 公式の OpenAI の endpoint ではない（SDK の判定が読む）。
    base_url = None

    def __init__(self, target: ModelTarget) -> None:
        self.responses = _AnthropicResponses(target)

    def with_options(self, **_options: Any) -> _AnthropicClient:
        # SDK が再試行を自分で行うときに呼ぶ。再試行は部品の呼び出しの `max_retries` が持つ。
        return self


class AnthropicMessagesModel(OciResponsesModel):
    """クォータナリ接続のモデル（OpenAI Agents SDK の `Model`。#5546・#5580）。

    推論の量は SDK の `ModelSettings.reasoning`（`model_settings_for` が登録モデルの値で作る）、
    出力の上限は `ModelSettings.max_tokens`、構造化出力は `output_schema` の JSON schema を使う
    （項目の説明は Responses API の経路と同じく指示の最後の Step に移す。#5238）。
    """

    provider_name = "anthropic"

    def __init__(self, target: ModelTarget) -> None:
        super().__init__(
            model=target.model_id, openai_client=cast(AsyncOpenAI, _AnthropicClient(target))
        )
        # 上限つきの並行の実行器を通す呼び出し（SDK の再試行 0）は、送り直しを実行器に任せる。
        self.online_policy = target.sdk_max_retries != 0
        self.output_capacity = target.max_output_tokens
        self.input_capacity = target.max_input_tokens
        self.online_max_output_tokens = target.online_max_output_tokens
