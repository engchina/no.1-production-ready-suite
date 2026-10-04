"""入力の検証エラー（422）の利用者向けの日本語のメッセージ（#979）。

FastAPI / Pydantic の検証エラーは英語の文（`Field required` など）と内部の位置
（`body.cases.0.query`）を持つ。画面はメッセージをそのまま出すため、位置は利用者が書いた
JSON の位置（`cases[0].query`）に直し、文は `type` ごとの日本語にする。自前の検証
（`ValueError`）の日本語のメッセージは、そのまま出す（`Value error, ` の接頭辞と位置は
付けない）。
"""

from collections.abc import Mapping, Sequence
from typing import Any

# loc の先頭の、値の出どころ（request の部分）を表す要素。利用者には見せない。
_LOCATION_SOURCES = frozenset({"body", "query", "path", "header", "cookie"})
# 自前の検証の例外のメッセージに Pydantic が付ける接頭辞。
_CUSTOM_MESSAGE_PREFIXES = ("Value error, ", "Assertion failed, ")

_FALLBACK_MESSAGE = "入力内容を確認してください。"

# Pydantic の error の type → 日本語。`{name}` は error の ctx の値で埋める。
_MESSAGES: dict[str, str] = {
    "missing": "必須の項目です。入力してください。",
    "extra_forbidden": "この項目は指定できません。",
    "string_type": "文字列で入力してください。",
    "string_too_short": "{min_length} 文字以上で入力してください。",
    "string_too_long": "{max_length} 文字以内で入力してください。",
    "string_pattern_mismatch": "入力形式を確認してください。",
    "int_type": "整数で入力してください。",
    "int_parsing": "整数で入力してください。",
    "int_from_float": "整数で入力してください。",
    "float_type": "数値で入力してください。",
    "float_parsing": "数値で入力してください。",
    "bool_type": "true か false で指定してください。",
    "bool_parsing": "true か false で指定してください。",
    "list_type": "配列（[ ]）で指定してください。",
    "dict_type": "オブジェクト（{{ }}）で指定してください。",
    "model_type": "オブジェクト（{{ }}）で指定してください。",
    "model_attributes_type": "オブジェクト（{{ }}）で指定してください。",
    "too_short": "{min_length} 件以上指定してください。",
    "too_long": "{max_length} 件以内にしてください。",
    "greater_than": "{gt} より大きい値を入力してください。",
    "greater_than_equal": "{ge} 以上の値を入力してください。",
    "less_than": "{lt} より小さい値を入力してください。",
    "less_than_equal": "{le} 以下の値を入力してください。",
    "literal_error": "指定できる値ではありません（指定できる値: {expected}）。",
    "enum": "指定できる値ではありません（指定できる値: {expected}）。",
    "json_invalid": "JSON の形式が正しくありません。",
    "json_type": "JSON の形式が正しくありません。",
    "url_parsing": "URL の形式で入力してください。",
    "url_type": "URL の形式で入力してください。",
    "datetime_parsing": "日時の形式で入力してください。",
    "datetime_type": "日時の形式で入力してください。",
    "date_parsing": "日付の形式で入力してください。",
    "uuid_parsing": "UUID の形式で入力してください。",
}


def validation_error_messages(errors: Sequence[Mapping[str, Any]]) -> list[str]:
    """検証エラーの一覧を、利用者向けの日本語のメッセージ（重複なし・順序は保つ）にする。"""
    messages: list[str] = []
    for error in errors:
        message = validation_error_message(error)
        if message not in messages:
            messages.append(message)
    return messages


def validation_error_message(error: Mapping[str, Any]) -> str:
    """1 件の検証エラーを `位置: 日本語の文`（位置が無ければ文だけ）にする。"""
    raw_message = str(error.get("msg") or "").strip()
    for prefix in _CUSTOM_MESSAGE_PREFIXES:
        if raw_message.startswith(prefix):
            custom = raw_message.removeprefix(prefix).strip()
            if custom:
                return custom
    text = _japanese_message(str(error.get("type") or ""), error.get("ctx"))
    location = _location(error.get("loc", ()))
    return f"{location}: {text}" if location else text


def _japanese_message(error_type: str, ctx: object) -> str:
    template = _MESSAGES.get(error_type)
    if template is None:
        return _FALLBACK_MESSAGE
    values = {str(key): value for key, value in ctx.items()} if isinstance(ctx, Mapping) else {}
    if "expected" in values:
        # Pydantic は「'a', 'b' or 'c'」の形にする。英語の or を読点にする。
        values["expected"] = str(values["expected"]).replace(" or ", "、").replace(", ", "、")
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return _FALLBACK_MESSAGE


def _location(loc: object) -> str:
    """`("body", "cases", 0, "query")` → `cases[0].query`。"""
    parts = list(loc) if isinstance(loc, list | tuple) else []
    if parts and parts[0] in _LOCATION_SOURCES:
        parts = parts[1:]
    location = ""
    for part in parts:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}" if location else str(part)
    return location
