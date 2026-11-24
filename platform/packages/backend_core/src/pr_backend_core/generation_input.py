"""生成要求を送る前に、入力だけの保守的な予算を測る。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pr_backend_core.token_estimate import DEFAULT_IMAGE_INPUT_TOKENS, without_image_inputs


class GenerationInputLimitError(ValueError):
    """完全な要求の見積もりが上限を超え、送信しなかった。"""

    def __init__(self, estimated_tokens: int, limit_tokens: int) -> None:
        self.estimated_tokens = estimated_tokens
        self.limit_tokens = limit_tokens
        super().__init__(
            f"生成入力の見積もり {estimated_tokens} が上限 {limit_tokens} を超えました。"
        )


class UnmeasuredGenerationInputError(ValueError):
    """参照先を展開したときの入力が不明なため、計測できない。"""


def _requires_external_budget(payload: Mapping[str, Any]) -> bool:
    if payload.get("previous_response_id") or payload.get("conversation"):
        return True
    prompt = payload.get("prompt")
    if isinstance(prompt, Mapping) and prompt.get("id"):
        return True

    def contains_reference(value: object) -> bool:
        if isinstance(value, list | tuple):
            return any(contains_reference(item) for item in value)
        if not isinstance(value, Mapping):
            return False
        if value.get("type") in (
            "input_file",
            "file",
            "document",
            "item_reference",
            "input_audio",
            "audio",
            "input_video",
            "video",
        ):
            return True
        return any(contains_reference(value.get(key)) for key in ("content", "input", "messages"))

    # クライアントが実行する関数の結果は次の要求に現れる。サーバー側のツールは
    # 1 回の呼び出し中に追加の入力を取得できるので、参照先の分も予算に含める。
    for tool in payload.get("tools") or ():
        if not isinstance(tool, Mapping):
            return True
        if tool.get("type") in ("function", "custom"):
            continue
        if (
            not tool.get("type")
            and tool.get("name")
            and isinstance(tool.get("input_schema"), Mapping)
        ):
            continue
        return True
    return contains_reference(payload)


def reported_input_tokens(usage: object) -> int | None:
    """Responses または Chat Completions の入力合計。未報告と実測の 0 を区別する。"""
    for key in ("input_tokens", "prompt_tokens"):
        value = usage.get(key) if isinstance(usage, Mapping) else getattr(usage, key, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


@dataclass(frozen=True)
class CompleteInputMeasurement:
    """画像以外の JSON 全体の文字数、画像、呼び先による計数の差の余白を別々に記録する。"""

    wire_chars: int
    media_tokens: int
    margin_tokens: int
    limit_tokens: int

    @property
    def estimated_tokens(self) -> int:
        return self.wire_chars + self.media_tokens + self.margin_tokens

    @property
    def fits(self) -> bool:
        return self.estimated_tokens <= self.limit_tokens

    def validate(self) -> None:
        if not self.fits:
            raise GenerationInputLimitError(self.estimated_tokens, self.limit_tokens)

    def usage_status(self, actual_tokens: int | None) -> str:
        if actual_tokens is None:
            return "unreported"
        return "over_limit" if actual_tokens > self.limit_tokens else "within_limit"


def complete_input_measurement(
    payload: Mapping[str, Any],
    *,
    limit_tokens: int,
    image_tokens: int = DEFAULT_IMAGE_INPUT_TOKENS,
    external_tokens: int = 0,
) -> CompleteInputMeasurement:
    """最終 JSON を 1 文字 1 token と見積もり、入力上限の 1/4 を余白にする。

    上限は呼ぶモデルの登録値（`limit_tokens`）だけを使い、固定の上限は掛けない（#5813）。
    画像以外の JSON の文字数・画像・参照先の合計が上限の 3/4 以下なら収まる。
    画像は呼ぶモデルの画像 1 枚分を使う。ファイルやサーバーに保存された会話を参照する
    呼び出しは、参照先の入力も `external_tokens` に含める。出力の予約はこの計数に含めない。
    """
    if limit_tokens < 1 or image_tokens < 1 or external_tokens < 0:
        raise ValueError("入力予算の設定が正しくありません。")
    if not external_tokens and _requires_external_budget(payload):
        raise UnmeasuredGenerationInputError("参照先を含めた生成入力の見積もりが必要です。")
    stripped = dict(payload)
    count = 0
    for key in ("input", "messages"):
        if key in stripped:
            stripped[key], found = without_image_inputs(stripped[key])
            count += found
    body = json.dumps(stripped, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return CompleteInputMeasurement(
        len(body), count * image_tokens + external_tokens, limit_tokens // 4, limit_tokens
    )
