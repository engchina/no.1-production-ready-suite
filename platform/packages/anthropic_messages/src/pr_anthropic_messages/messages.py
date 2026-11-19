"""Anthropic Claude の Messages API を公式の Python SDK（`anthropic`）で呼ぶ薄い部品。

3 製品の OCI Enterprise AI の層（Responses API）と同じ形の結果（本文・usage・失敗の分類）を返す。
製品ごとの処理の流れ（プロンプト・再試行の方針・出力の上限の決め方）は呼ぶ側が持ち、ここでは
1 回の呼び出しを要求の形に組み、応答と失敗を共通の形に写すだけにする。

## 使い方（クォータナリ接続のモデルを呼ぶ製品の層）

```python
from pr_anthropic_messages import (
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    create_anthropic_message,
)

try:
    result = create_anthropic_message(
        AnthropicMessagesRequest(
            model_id=model_id,                  # 登録モデルの ID（例 claude-opus-5-5）
            system=system_prompt,
            user_text=prompt,
            images=(AnthropicImage.from_data_url(data_url),),  # 画像が無ければ省く
            response_schema=schema,             # JSON schema（構造化出力。無ければ None）
            reasoning_effort=reasoning_effort,  # 設定の値 none / low / medium / high
            max_output_tokens=budget,
        ),
        api_key=connection.api_key,
        endpoint_url=connection.endpoint_url,   # 空なら SDK の既定（https://api.anthropic.com）
        timeout_seconds=timeout_seconds,
    )
except AnthropicMessagesError as exc:
    exc.kind          # pr_enterprise_ai の EnterpriseAiCallFailureKind
    exc.retryable     # 429・5xx・時間切れ・接続の失敗なら True（再試行は呼ぶ側が決める）
    exc.incomplete    # 出力の上限で途切れたとき（enterprise_ai_incomplete_message に渡せる）
    raise
result.text                          # 本文（構造化出力なら JSON の文字列。Pydantic で検証して使う）
result.usage.as_responses_usage()    # Responses API の形の usage（llm_call_usage・trace へ）
```

非同期の層は :func:`acreate_anthropic_message` を同じ引数で呼ぶ。

## 要求の組み方

- 構造化出力は `output_config.format`（`{"type": "json_schema", "schema": ...}`）。
- 思考は adaptive。推論の量は :data:`ANTHROPIC_EFFORT_BY_REASONING_EFFORT` で
  `output_config.effort` に写す（`none` と空は思考・effort を送らず、モデルの既定にする）。
- 出力の長い呼び出しでも HTTP の時間切れにならないよう、常に streaming で送り
  `get_final_message()` で応答の全体を受け取る。
- 画像は base64 の image block を本文の前に置く。
- 拒否の時の別モデルへの切り替え（server-side fallback）は使わない。呼ぶモデル・接続を
  暗黙に変えない（docs/agents/prompts.md「別のモデル・接続へ暗黙に切り替えたりしない」）。

## 失敗の写し方

SDK の例外は型の連鎖（具体的な型から順）で pr_enterprise_ai の種類に写す。

| SDK の例外・応答 | 種類（kind） |
|---|---|
| `APITimeoutError` | `timeout` |
| `APIConnectionError` | `connection` |
| `AuthenticationError`（401） | `authentication` |
| `PermissionDeniedError`（403） | `authorization` |
| `NotFoundError`（404。本文が `model:` で始まる） | `model_not_found` |
| `NotFoundError`（404。それ以外: Endpoint URL の誤りなど） | `request` |
| `RateLimitError`（429） | `rate_limit` |
| `InternalServerError`（5xx・529 overloaded） | `unavailable` |
| ほかの `APIStatusError` で `error.type` が `rate_limit_error` | `rate_limit` |
| ほかの `APIStatusError` で `error.type` が `overloaded_error`・`api_error`（※） | `unavailable` |
| ほかの `APIStatusError`（400・402・413 など） | `request` |
| ほかの `APIError`（応答の検証の失敗など） | `response_format` |
| `stop_reason=refusal` | `refusal` |
| `stop_reason=max_tokens` | `incomplete`（理由 `max_output_tokens`） |
| `stop_reason=model_context_window_exceeded` ほか終わりでない理由 | `incomplete` |
| 本文が空 | `response_format` |

※ streaming の途中の error event（HTTP は 200）。

API key は SDK の client に渡すだけで、例外の文・ログに出さない（応答の本文の抜粋は
`enterprise_ai_provider_message` で秘密を伏せる）。
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, cast, get_args

import anthropic
from pr_enterprise_ai.errors import (
    ENTERPRISE_AI_CALL_FAILURE_ERROR_CODES,
    ENTERPRISE_AI_CONFIGURATION_FAILURE_KINDS,
    ENTERPRISE_AI_INCOMPLETE_OUTPUT_LIMIT_REASON,
    EnterpriseAiCallFailureKind,
    EnterpriseAiHttpFailure,
    EnterpriseAiIncomplete,
    enterprise_ai_incomplete_message,
    enterprise_ai_provider_message,
)

if TYPE_CHECKING:
    from anthropic.types import Message, MessageParam

__all__ = [
    "ANTHROPIC_EFFORT_BY_REASONING_EFFORT",
    "ANTHROPIC_IMAGE_MEDIA_TYPES",
    "DEFAULT_TIMEOUT_SECONDS",
    "AnthropicEffort",
    "AnthropicImage",
    "AnthropicMessagesError",
    "AnthropicMessagesRequest",
    "AnthropicMessagesResult",
    "AnthropicUsage",
    "acreate_anthropic_message",
    "anthropic_base_url",
    "anthropic_effort",
    "classify_anthropic_error",
    "create_anthropic_message",
]

AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]

# 設定の推論の量（pr_system_settings の EnterpriseAiReasoningEffort: none / low / medium / high）を
# Messages API の `output_config.effort` に写す表。None は思考・effort を送らない（モデルの既定。
# Claude Opus 5.5 は adaptive・effort medium、Claude Opus 4.8 / 4.7 は思考なし）。
# xhigh / max は設定の選択肢に無いが、Messages API が受け付ける値なのでそのまま通す。
ANTHROPIC_EFFORT_BY_REASONING_EFFORT: Final[Mapping[str, AnthropicEffort | None]] = {
    "": None,
    "none": None,
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}

# Messages API が受け付ける画像の種類（base64 の image block）。
_ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
ANTHROPIC_IMAGE_MEDIA_TYPES: Final[frozenset[str]] = frozenset(get_args(_ImageMediaType))

# 終わりとして扱う停止の理由。ほかは refusal・incomplete に写す。
_COMPLETED_STOP_REASONS: Final = frozenset({"end_turn", "stop_sequence"})
# 再試行で直る見込みのある種類（再試行するかは呼ぶ側が決める）。
_RETRYABLE_KINDS: Final[frozenset[EnterpriseAiCallFailureKind]] = frozenset(
    {"rate_limit", "unavailable", "timeout", "connection"}
)
# HTTP の status code を持つ種類（EnterpriseAiHttpFailure にして案内の文を作れる）。
_HTTP_KINDS: Final = frozenset(
    {"model_not_found", "authentication", "authorization", "rate_limit", "unavailable", "request"}
)
# SDK の既定の待ち時間（10 分）と同じ。
DEFAULT_TIMEOUT_SECONDS: Final = 600.0


def anthropic_effort(reasoning_effort: str) -> AnthropicEffort | None:
    """設定の推論の量を `output_config.effort` の値にする（None は送らない）。"""
    normalized = (reasoning_effort or "").strip().casefold()
    if normalized not in ANTHROPIC_EFFORT_BY_REASONING_EFFORT:
        raise ValueError(f"推論の量 {reasoning_effort!r} は Messages API の effort に写せません。")
    return ANTHROPIC_EFFORT_BY_REASONING_EFFORT[normalized]


def anthropic_base_url(endpoint_url: str) -> str | None:
    """接続の Endpoint URL を SDK の `base_url` にする（空は None = SDK の既定）。

    入力どおり使い、前後の空白と末尾の `/` だけを除く（pr_enterprise_ai の
    `enterprise_ai_base_url` と同じ規則。path は補わない）。SDK が `/v1/messages` を足すので、
    Endpoint URL は `https://api.anthropic.com` の形（`/v1` を含めない）で書く。
    """
    text = (endpoint_url or "").strip().rstrip("/")
    return text or None


@dataclass(frozen=True)
class AnthropicImage:
    """入力の画像（base64 の image block）。"""

    media_type: str
    data_base64: str

    def __post_init__(self) -> None:
        if self.media_type not in ANTHROPIC_IMAGE_MEDIA_TYPES:
            raise ValueError(
                f"画像の種類 {self.media_type!r} は Messages API が受け付けません"
                f"（{', '.join(sorted(ANTHROPIC_IMAGE_MEDIA_TYPES))}）。"
            )
        if not self.data_base64:
            raise ValueError("画像のデータが空です。")

    @classmethod
    def from_bytes(cls, media_type: str, data: bytes) -> AnthropicImage:
        return cls(media_type=media_type, data_base64=base64.standard_b64encode(data).decode())

    @classmethod
    def from_data_url(cls, data_url: str) -> AnthropicImage:
        """`data:image/png;base64,...` の形（3 製品の Responses API の `input_image`）から作る。"""
        header, sep, payload = (data_url or "").strip().partition(",")
        if not sep or not header.startswith("data:") or not header.endswith(";base64"):
            raise ValueError("画像は data:<種類>;base64,<データ> の形で渡してください。")
        media_type = header[len("data:") : -len(";base64")].strip().casefold()
        try:
            base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("画像の base64 のデータが壊れています。") from exc
        return cls(media_type=media_type, data_base64=payload)

    def to_param(self) -> dict[str, Any]:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": cast(_ImageMediaType, self.media_type),
                "data": self.data_base64,
            },
        }


@dataclass(frozen=True)
class AnthropicMessagesRequest:
    """1 回の呼び出しの入力（製品の層の共通の入力）。"""

    model_id: str
    user_text: str
    max_output_tokens: int
    system: str = ""
    images: Sequence[AnthropicImage] = ()
    # 構造化出力の JSON schema（`output_config.format`）。None は自由な本文。
    response_schema: Mapping[str, Any] | None = None
    # 設定の推論の量（none / low / medium / high）。
    reasoning_effort: str = ""

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("モデル ID が空です。")
        if self.max_output_tokens <= 0:
            raise ValueError("出力の上限は正の整数で指定してください。")
        if not self.user_text and not self.images:
            raise ValueError("入力のテキストか画像が要ります。")
        anthropic_effort(self.reasoning_effort)


@dataclass(frozen=True)
class AnthropicUsage:
    """token の使用量。`input_tokens` は cache の外の入力（Messages API の意味のまま）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # 出力のうち思考の token（応答に無ければ None）。
    thinking_tokens: int | None = None

    @property
    def total_input_tokens(self) -> int:
        """cache を含む入力の全体（Responses API の `input_tokens` と同じ意味）。"""
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    def as_responses_usage(self) -> dict[str, Any]:
        """Responses API の `usage` と同じ形（3 製品の `llm_call_usage`・`record_genai_usage`）。"""
        usage: dict[str, Any] = {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_input_tokens + self.output_tokens,
            "input_tokens_details": {"cached_tokens": self.cache_read_input_tokens},
        }
        if self.thinking_tokens is not None:
            usage["output_tokens_details"] = {"reasoning_tokens": self.thinking_tokens}
        return usage


@dataclass(frozen=True)
class AnthropicMessagesResult:
    """1 回の呼び出しの結果（本文・usage・停止の理由）。"""

    text: str
    model: str
    stop_reason: str
    usage: AnthropicUsage
    request_id: str = ""


class AnthropicMessagesError(RuntimeError):
    """Messages API の呼び出しの失敗（pr_enterprise_ai の種類に分類したもの）。

    `str(exc)` は利用者に見せてよい日本語の文（API key・要求の本文を含まない）。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: EnterpriseAiCallFailureKind,
        model_id: str = "",
        status_code: int | None = None,
        provider_message: str = "",
        request_id: str = "",
        incomplete: EnterpriseAiIncomplete | None = None,
        refusal_category: str = "",
        usage: AnthropicUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: EnterpriseAiCallFailureKind = kind
        self.model_id = model_id
        self.status_code = status_code
        self.provider_message = provider_message
        self.request_id = request_id
        self.incomplete = incomplete
        self.refusal_category = refusal_category
        self.usage = usage

    @property
    def retryable(self) -> bool:
        """再試行で直る見込みがあるか（429・5xx・時間切れ・接続の失敗）。"""
        return self.kind in _RETRYABLE_KINDS

    @property
    def configuration_error(self) -> bool:
        """設定の誤りか（モデル・API key・権限。時間をおいても直らない）。"""
        return self.kind in ENTERPRISE_AI_CONFIGURATION_FAILURE_KINDS

    @property
    def error_code(self) -> str:
        """ジョブ・API の `error_code`（3 製品で同じ値）。"""
        return ENTERPRISE_AI_CALL_FAILURE_ERROR_CODES[self.kind]

    @property
    def http_failure(self) -> EnterpriseAiHttpFailure | None:
        """HTTP の失敗なら、案内の文（`enterprise_ai_failure_actions`）に渡せる分類。"""
        if self.status_code is None or self.kind not in _HTTP_KINDS:
            return None
        return EnterpriseAiHttpFailure(kind=cast(Any, self.kind), status_code=self.status_code)


# --------------------------------------------------------------------------- 要求


def _message_params(request: AnthropicMessagesRequest) -> dict[str, Any]:
    content: list[dict[str, Any]] = [image.to_param() for image in request.images]
    if request.user_text:
        content.append({"type": "text", "text": request.user_text})
    messages: list[MessageParam] = [cast("MessageParam", {"role": "user", "content": content})]
    params: dict[str, Any] = {
        "model": request.model_id.strip(),
        "max_tokens": request.max_output_tokens,
        "messages": messages,
    }
    if request.system:
        params["system"] = request.system
    output_config: dict[str, Any] = {}
    effort = anthropic_effort(request.reasoning_effort)
    if effort is not None:
        params["thinking"] = {"type": "adaptive"}
        output_config["effort"] = effort
    if request.response_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": dict(request.response_schema)}
    if output_config:
        params["output_config"] = output_config
    return params


def _require_api_key(api_key: str, model_id: str) -> str:
    # 空の key を SDK に渡すと環境変数・profile の credential を探すので、ここで止める。
    key = (api_key or "").strip()
    if not key:
        raise AnthropicMessagesError(
            f"モデル「{model_id}」の接続の API key が未設定です。",
            kind="authentication",
            model_id=model_id,
        )
    return key


# --------------------------------------------------------------------------- 応答


def _usage(message: Message) -> AnthropicUsage:
    usage = message.usage
    details = usage.output_tokens_details
    return AnthropicUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        thinking_tokens=details.thinking_tokens if details is not None else None,
    )


def _result(
    message: Message, request: AnthropicMessagesRequest, *, request_id: str
) -> AnthropicMessagesResult:
    model_id = request.model_id.strip()
    usage = _usage(message)
    stop_reason = str(message.stop_reason or "")
    if stop_reason == "refusal":
        category = ""
        explanation = ""
        if message.stop_details is not None:
            category = str(message.stop_details.category or "")
            explanation = str(message.stop_details.explanation or "")
        detail = "・".join(
            part
            for part in ("stop_reason=refusal", f"category={category}" if category else "")
            if part
        )
        raise AnthropicMessagesError(
            f"モデル「{model_id}」が応答を拒否しました（{detail}）。"
            "表現を変えるか、対象を分けてやり直してください。",
            kind="refusal",
            model_id=model_id,
            provider_message=enterprise_ai_provider_message(explanation),
            request_id=request_id,
            refusal_category=category,
            usage=usage,
        )
    if stop_reason not in _COMPLETED_STOP_REASONS:
        incomplete = EnterpriseAiIncomplete(
            status="incomplete",
            reason=(
                ENTERPRISE_AI_INCOMPLETE_OUTPUT_LIMIT_REASON
                if stop_reason == "max_tokens"
                else stop_reason
            ),
            max_output_tokens=request.max_output_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.thinking_tokens,
        )
        raise AnthropicMessagesError(
            enterprise_ai_incomplete_message(incomplete, model_id=model_id),
            kind="incomplete",
            model_id=model_id,
            provider_message=f"stop_reason={stop_reason or '-'}",
            request_id=request_id,
            incomplete=incomplete,
            usage=usage,
        )
    # 思考の block（thinking / redacted_thinking）は本文に使わない。
    text = "".join(block.text for block in message.content if block.type == "text")
    if not text.strip():
        raise AnthropicMessagesError(
            f"モデル「{model_id}」の応答に本文がありませんでした（stop_reason={stop_reason}）。",
            kind="response_format",
            model_id=model_id,
            provider_message=f"stop_reason={stop_reason}",
            request_id=request_id,
            usage=usage,
        )
    return AnthropicMessagesResult(
        text=text,
        model=str(message.model or model_id),
        stop_reason=stop_reason,
        usage=usage,
        request_id=request_id,
    )


# --------------------------------------------------------------------------- 失敗


def _status_provider_message(exc: anthropic.APIStatusError) -> str:
    body = exc.body
    text = json.dumps(body, ensure_ascii=False) if isinstance(body, Mapping) else str(body or "")
    return enterprise_ai_provider_message(text or str(exc.api_message or ""))


def classify_anthropic_error(exc: anthropic.APIError) -> AnthropicMessagesError:
    """SDK の例外を、型の連鎖（具体的な型から順）で pr_enterprise_ai の種類に分類する。"""
    status_code: int | None = None
    provider_message = ""
    request_id = ""
    kind: EnterpriseAiCallFailureKind
    if isinstance(exc, anthropic.APITimeoutError):
        kind = "timeout"
    elif isinstance(exc, anthropic.APIConnectionError):
        kind = "connection"
    elif isinstance(exc, anthropic.APIStatusError):
        status_code = exc.status_code
        provider_message = _status_provider_message(exc)
        request_id = str(exc.request_id or "")
        if isinstance(exc, anthropic.AuthenticationError):
            kind = "authentication"
        elif isinstance(exc, anthropic.PermissionDeniedError):
            kind = "authorization"
        elif isinstance(exc, anthropic.NotFoundError):
            # 無いモデル・使えないモデルは `model: <id>` で始まる not_found_error（Anthropic の
            # 文書）。それ以外の 404 は Endpoint URL の誤りなど。
            api_message = str(exc.api_message or "").strip().casefold()
            kind = "model_not_found" if api_message.startswith("model:") else "request"
        elif isinstance(exc, anthropic.RateLimitError):
            kind = "rate_limit"
        elif isinstance(exc, anthropic.InternalServerError):
            kind = "unavailable"
        elif exc.type == "rate_limit_error":
            kind = "rate_limit"
        elif exc.type in {"overloaded_error", "api_error"}:
            # streaming の途中の error event（HTTP は 200）も、本文の error.type で分ける。
            kind = "unavailable"
        else:
            # 400・402・408・413 など（要求・課金・大きさの誤り）。
            kind = "request"
    else:
        kind = "response_format"
    return AnthropicMessagesError(
        _failure_message(kind, status_code=status_code, provider_message=provider_message),
        kind=kind,
        status_code=status_code,
        provider_message=provider_message,
        request_id=request_id,
    )


_FAILURE_SUMMARIES_JA: Final[dict[EnterpriseAiCallFailureKind, str]] = {
    "model_not_found": "モデルが見つからないか、この API key では使えません",
    "authentication": "API key が受け付けられませんでした",
    "authorization": "この API key には呼び出しの権限がありません",
    "rate_limit": "呼び出しの上限に達しました",
    "unavailable": "サービスが一時的に応答できません",
    "request": "要求が受け付けられませんでした",
    "timeout": "応答が待ち時間の上限までに届きませんでした",
    "connection": "接続できませんでした",
    "response_format": "応答の形式が想定と違います",
}


def _failure_message(
    kind: EnterpriseAiCallFailureKind, *, status_code: int | None, provider_message: str
) -> str:
    summary = _FAILURE_SUMMARIES_JA.get(kind, "呼び出しに失敗しました")
    status = f"HTTP {status_code}" if status_code is not None else ""
    detail = "・".join(part for part in (status, provider_message) if part)
    return f"Anthropic Messages API: {summary}" + (f"（{detail}）。" if detail else "。")


def _with_model(error: AnthropicMessagesError, model_id: str) -> AnthropicMessagesError:
    error.model_id = model_id
    error.args = (f"モデル「{model_id}」の呼び出しで、{error.args[0]}",)
    return error


# --------------------------------------------------------------------------- 呼び出し


def create_anthropic_message(
    request: AnthropicMessagesRequest,
    *,
    api_key: str,
    endpoint_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 0,
    http_client: anthropic.DefaultHttpxClient | None = None,
) -> AnthropicMessagesResult:
    """Messages API を 1 回呼び、本文・usage を返す（失敗は :class:`AnthropicMessagesError`）。

    `max_retries` は SDK の自動の再試行（既定 0。再試行の方針は製品の層が持つ）。
    `http_client` は proxy・テストの差し替え用（`anthropic.DefaultHttpxClient`）。
    """
    model_id = request.model_id.strip()
    key = _require_api_key(api_key, model_id)
    params = _message_params(request)
    try:
        with (
            anthropic.Anthropic(
                api_key=key,
                base_url=anthropic_base_url(endpoint_url),
                timeout=timeout_seconds,
                max_retries=max_retries,
                http_client=http_client,
            ) as client,
            client.messages.stream(**params) as stream,
        ):
            message = stream.get_final_message()
            request_id = stream.request_id or ""
    except anthropic.APIError as exc:
        raise _with_model(classify_anthropic_error(exc), model_id) from exc
    return _result(message, request, request_id=request_id)


async def acreate_anthropic_message(
    request: AnthropicMessagesRequest,
    *,
    api_key: str,
    endpoint_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 0,
    http_client: anthropic.DefaultAsyncHttpxClient | None = None,
) -> AnthropicMessagesResult:
    """:func:`create_anthropic_message` の非同期版（引数・結果・失敗は同じ）。"""
    model_id = request.model_id.strip()
    key = _require_api_key(api_key, model_id)
    params = _message_params(request)
    try:
        async with (
            anthropic.AsyncAnthropic(
                api_key=key,
                base_url=anthropic_base_url(endpoint_url),
                timeout=timeout_seconds,
                max_retries=max_retries,
                http_client=http_client,
            ) as client,
            client.messages.stream(**params) as stream,
        ):
            message = await stream.get_final_message()
            request_id = stream.request_id or ""
    except anthropic.APIError as exc:
        raise _with_model(classify_anthropic_error(exc), model_id) from exc
    return _result(message, request, request_id=request_id)
