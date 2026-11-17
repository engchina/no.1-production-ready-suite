"""本文の入力 token の見積もり（3 製品で共通の 1 つ。#1437・#3321）。

`pr_system_settings.model` の入力の予算（`input_token_budget`）と、検索の結果・観測の本文の大きさの
規則（`pr_backend_core.source_text`）が同じ見積もりを使う。
"""


def estimate_input_tokens(text: str) -> int:
    """本文の入力 token の見積もり。

    モデルごとの tokenizer を持たないため、多めに見積もる: ASCII は 4 字で 1 token、それ以外
    （かな・漢字など）は 1 字で 1 token。見積もりは文字数を超えないので、文字の予算を token の
    予算以下にすれば token も予算を超えない。
    """
    value = text or ""
    ascii_count = sum(1 for char in value if ord(char) < 0x80)
    return (len(value) - ascii_count) + (ascii_count + 3) // 4


# 画像 1 枚の入力 token の既定（#5316）。登録モデルの「画像 1 枚の入力（tokens）」に値が無い
# モデルに使う（値の置き場と検証は `pr_system_settings.model` の
# `EnterpriseAiConfiguredModel.image_input_tokens`）。画像の token はモデルごとに計算が違い
# （画像の大きさを区画で数える）、送る画像の base64 の長さには比例しない。
# 値の無いモデルでも上限の判定が実際より小さくならないよう、主要な Vision の API の公式の文書に
# ある 1 枚の最大より大きい値にする（2026-11-17 の確認: Anthropic の高い解像度の tier は 4,784、
# OpenAI の Responses API の `detail` を送らない（auto）呼び出しは patch の方式のモデルで
# 最大 12,000）。
DEFAULT_IMAGE_INPUT_TOKENS = 16_384
# Responses API の入力の画像の部品の type（`input_image`。SDK の出力の型の `image` も同じ扱い）。
_IMAGE_PART_TYPES = frozenset({"input_image", "image"})


def without_image_inputs(value: object) -> tuple[object, int]:
    """Responses API の要求から画像の部品を除いた写しと、除いた画像の数（#5316）。

    画像の部品（`{"type": "input_image", "image_url": "data:...;base64,..."}`）の data URL を
    本文の見積もり（`estimate_input_tokens`）に入れると、base64 の文字が token として数えられる。
    本文は除いた写しで見積もり、画像は数を「画像 1 枚の入力 token」（登録モデルの値）に掛けて
    数える。
    """
    if isinstance(value, dict):
        if value.get("type") in _IMAGE_PART_TYPES:
            return None, 1
        kept: dict[object, object] = {}
        count = 0
        for key, item in value.items():
            stripped, found = without_image_inputs(item)
            count += found
            if stripped is not None or item is None:
                kept[key] = stripped
        return kept, count
    if isinstance(value, list | tuple):
        items: list[object] = []
        count = 0
        for item in value:
            stripped, found = without_image_inputs(item)
            count += found
            if stripped is not None or item is None:
                items.append(stripped)
        return items, count
    return value, 0
