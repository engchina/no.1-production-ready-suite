"""Responses API の要求を Messages API（`pr_anthropic_messages`）で送り、同じ形で返す（#5546）。

RAG のモデルの呼び出しは 2 か所（共有 core の `OciEnterpriseAiClient` と rag_engine の
`adapters/oci.py`）で、どちらも Responses API の要求（`model`・`instructions`・`input`・
`text.format`・`reasoning.effort`・`max_output_tokens`）を組み、応答（`status`・`output`・
`output_text`・`incomplete_details`・`usage`）を読む。クォータナリ接続（Anthropic Claude）の
モデルでは、この module が同じ要求を Messages API の 1 回の呼び出しに写し、結果を同じ形の応答に
写す。要求の組み方・応答の読み方・再試行・出力の上限の扱い・usage の記録は呼ぶ側のまま変えない。

- 本文（完了）: `status=completed` と `output_text`。
- 出力の上限などで途切れた（`stop_reason=max_tokens` ほか）: `status=incomplete` と
  `incomplete_details.reason`（`max_output_tokens` ほか）。呼ぶ側の「上限を広げて送り直す」判定が
  そのまま使える。
- 拒否（`stop_reason=refusal`）: `output` の content の `{"type": "refusal"}`。
- ほかの失敗（429・5xx・時間切れ・認証など）は `AnthropicMessagesError` のまま送出する
  （`kind`・`retryable`・`status_code` を持ち、呼ぶ側の再試行・失敗の分類が読む）。

構造化出力の JSON schema は `text.format` の schema をそのまま渡す。Messages API の structured
outputs が受け付けない書き方（数・文字列の制約・型の配列など）は、`pr_anthropic_messages` が送る
直前に 1 回だけ変換する（`anthropic_response_schema`）。本文は呼ぶ側が今までどおり Pydantic で
検証する。

この module は `pr_anthropic_messages` に依存する。モデルを呼ぶ backend の venv だけが入れるので、
呼ぶ側はクォータナリ接続の経路の中で import する（parser のサービスの venv では import しない）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from pr_anthropic_messages import (
    DEFAULT_TIMEOUT_SECONDS,
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    acreate_anthropic_message,
    create_anthropic_message,
)

__all__ = [
    "AnthropicMessagesError",
    "AnthropicRequestShapeError",
    "acreate_responses_body",
    "create_responses_body",
    "messages_request",
    "responses_body",
    "responses_body_for_failure",
]

# Responses API の content の text の部分の type。
_TEXT_PART_TYPES: Final = frozenset({"input_text", "text", "output_text"})
# Messages API の system にまとめる role。
_SYSTEM_ROLES: Final = frozenset({"system", "developer"})
# Responses API の画像の種類の別名（Messages API は image/jpeg だけを受け付ける）。
_IMAGE_MEDIA_TYPE_ALIASES: Final = {"image/jpg": "image/jpeg"}


class AnthropicRequestShapeError(ValueError):
    """Responses API の要求を Messages API に写せない（画像以外のファイルの入力など）。

    文はそのまま利用者に見せてよい（要求の本文・secret を含まない）。
    """


# --------------------------------------------------------------------------- 要求


def messages_request(payload: Mapping[str, Any]) -> AnthropicMessagesRequest:
    """Responses API の要求を Messages API の 1 回の呼び出しの入力にする。"""
    system_parts: list[str] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str):
        system_parts.append(instructions)
    user_texts: list[str] = []
    images: list[AnthropicImage] = []
    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        user_texts.append(raw_input)
    elif isinstance(raw_input, list):
        for message in raw_input:
            if not isinstance(message, Mapping):
                raise AnthropicRequestShapeError("Responses API の input の形が想定と違います。")
            role = str(message.get("role") or "user")
            texts, message_images = _content_parts(message.get("content"))
            if role in _SYSTEM_ROLES:
                if message_images:
                    raise AnthropicRequestShapeError("system の指示に画像は渡せません。")
                system_parts.extend(texts)
            elif role == "user":
                user_texts.extend(texts)
                images.extend(message_images)
            else:
                raise AnthropicRequestShapeError(
                    f"Responses API の input の role「{role}」は Messages API に写せません。"
                )
    else:
        raise AnthropicRequestShapeError(
            "Responses API の input がありません"
            "（payload template の要求は Messages API で送れません）。"
        )
    max_output_tokens = payload.get("max_output_tokens")
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise AnthropicRequestShapeError("Responses API の max_output_tokens がありません。")
    reasoning = payload.get("reasoning")
    effort = reasoning.get("effort") if isinstance(reasoning, Mapping) else None
    text = payload.get("text")
    text_format = text.get("format") if isinstance(text, Mapping) else None
    response_schema: dict[str, Any] | None = None
    if isinstance(text_format, Mapping) and text_format.get("type") == "json_schema":
        schema = text_format.get("schema")
        if not isinstance(schema, Mapping):
            raise AnthropicRequestShapeError(
                "Responses API の text.format に schema がありません。"
            )
        response_schema = dict(schema)
    return AnthropicMessagesRequest(
        model_id=str(payload.get("model") or ""),
        system="\n\n".join(part for part in system_parts if part),
        user_text="\n\n".join(part for part in user_texts if part),
        images=tuple(images),
        response_schema=response_schema,
        reasoning_effort=str(effort or ""),
        max_output_tokens=max_output_tokens,
    )


def _content_parts(content: object) -> tuple[list[str], list[AnthropicImage]]:
    if isinstance(content, str):
        return [content], []
    if not isinstance(content, list):
        raise AnthropicRequestShapeError("Responses API の input の content の形が想定と違います。")
    texts: list[str] = []
    images: list[AnthropicImage] = []
    for part in content:
        part_type = str(part.get("type") or "") if isinstance(part, Mapping) else ""
        if isinstance(part, Mapping) and part_type in _TEXT_PART_TYPES:
            texts.append(str(part.get("text") or ""))
        elif isinstance(part, Mapping) and part_type == "input_image":
            images.append(_image(part.get("image_url")))
        elif part_type == "input_file":
            raise AnthropicRequestShapeError(
                "クォータナリ接続（Anthropic Claude）のモデルには、画像（JPEG・PNG・GIF・WebP）"
                "だけを渡せます。PDF などのファイルは、ほかの接続の Vision モデルで読んでください。"
            )
        else:
            raise AnthropicRequestShapeError(
                f"Responses API の content の type「{part_type or '-'}」は "
                "Messages API に写せません。"
            )
    return texts, images


def _image(image_url: object) -> AnthropicImage:
    url = image_url.get("url") if isinstance(image_url, Mapping) else image_url
    if not isinstance(url, str):
        raise AnthropicRequestShapeError("Responses API の input_image に data URL がありません。")
    header, sep, data = url.strip().partition(",")
    media_type = header.removeprefix("data:").removesuffix(";base64").strip().casefold()
    if sep and media_type in _IMAGE_MEDIA_TYPE_ALIASES:
        url = f"data:{_IMAGE_MEDIA_TYPE_ALIASES[media_type]};base64,{data}"
    try:
        return AnthropicImage.from_data_url(url)
    except ValueError as exc:
        raise AnthropicRequestShapeError(
            "クォータナリ接続（Anthropic Claude）のモデルには、画像（JPEG・PNG・GIF・WebP）だけを"
            f"渡せます（{exc}）。"
        ) from exc


# --------------------------------------------------------------------------- 応答


def _message_output(content: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "message", "role": "assistant", "status": "completed", "content": list(content)}
    ]


def responses_body(
    result: AnthropicMessagesResult, request: AnthropicMessagesRequest
) -> dict[str, Any]:
    """完了した Messages API の結果を Responses API の応答の形にする。"""
    return {
        "id": result.request_id,
        "object": "response",
        "model": result.model,
        "status": "completed",
        "incomplete_details": None,
        "max_output_tokens": request.max_output_tokens,
        "output": _message_output(
            [{"type": "output_text", "text": result.text, "annotations": []}]
        ),
        "output_text": result.text,
        "usage": result.usage.as_responses_usage(),
    }


def responses_body_for_failure(
    error: AnthropicMessagesError, request: AnthropicMessagesRequest
) -> dict[str, Any] | None:
    """途切れた・拒否した結果を Responses API の応答の形にする（ほかの失敗は None）。"""
    base: dict[str, Any] = {
        "id": error.request_id,
        "object": "response",
        "model": request.model_id.strip(),
        "max_output_tokens": request.max_output_tokens,
        "usage": error.usage.as_responses_usage() if error.usage is not None else None,
    }
    if error.kind == "incomplete" and error.incomplete is not None:
        return {
            **base,
            "status": "incomplete",
            "incomplete_details": {"reason": error.incomplete.reason},
            "output": [],
        }
    if error.kind == "refusal":
        return {
            **base,
            "status": "completed",
            "incomplete_details": None,
            "output": _message_output([{"type": "refusal", "refusal": str(error)}]),
        }
    return None


# --------------------------------------------------------------------------- 呼び出し


def create_responses_body(
    payload: Mapping[str, Any],
    *,
    api_key: str,
    endpoint_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 0,
) -> dict[str, Any]:
    """Responses API の要求を Messages API で 1 回送り、Responses API の形の応答を返す。"""
    request = messages_request(payload)
    try:
        result = create_anthropic_message(
            request,
            api_key=api_key,
            endpoint_url=endpoint_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    except AnthropicMessagesError as error:
        body = responses_body_for_failure(error, request)
        if body is None:
            raise
        return body
    return responses_body(result, request)


async def acreate_responses_body(
    payload: Mapping[str, Any],
    *,
    api_key: str,
    endpoint_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 0,
) -> dict[str, Any]:
    """:func:`create_responses_body` の非同期版。"""
    request = messages_request(payload)
    try:
        result = await acreate_anthropic_message(
            request,
            api_key=api_key,
            endpoint_url=endpoint_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    except AnthropicMessagesError as error:
        body = responses_body_for_failure(error, request)
        if body is None:
            raise
        return body
    return responses_body(result, request)
