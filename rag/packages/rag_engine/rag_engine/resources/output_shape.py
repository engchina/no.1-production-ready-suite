"""未完了の LLM の出力の形の要約（#5368）。

出力の上限で未完了になった応答の文から、何を出し続けたかが分かる数値だけを作る。本文の文字列・断片の中身は
返さない（業務の内容になりうるため）。意味は判定せず、文字の種類・反復の周期・JSON の字句の状態の形式だけを見る。
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any

# 文字の種類と反復を数える末尾の範囲（文字）。
TAIL_CHARS = 2000
# 末尾に続く反復を探す断片の長さの上限（文字）。
REPEAT_UNIT_MAX_CHARS = 64
# 末尾の範囲で最も多く現れた断片を数える長さ（文字）。
FRAGMENT_LENGTHS = (8, 32)

_JSON_SYNTAX = frozenset('{}[]:,"\\')


def _char_class(char: str) -> str:
    if char in "\r\n":
        return "newline"
    if char.isspace():
        return "space"
    if char in _JSON_SYNTAX:
        return "json_syntax"
    if char.isdigit():
        return "digit"
    if char.isascii() and char.isalpha():
        return "latin"
    code = ord(char)
    if (
        0x3040 <= code <= 0x30FF  # ひらがな・カタカナ
        or 0x31F0 <= code <= 0x31FF
        or 0xFF66 <= code <= 0xFF9F  # 半角カタカナ
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or code == 0x3005  # 々
    ):
        return "kana_kanji"
    category = unicodedata.category(char)
    if category.startswith("P"):
        return "punctuation"
    if category.startswith("S"):
        return "symbol"
    if category.startswith("L"):
        return "other_letter"
    return "other"


def _tail_repeat(tail: str) -> tuple[int, int]:
    """末尾に続く反復の (断片の長さ, 回数)。反復が無ければ (0, 0)。

    周期 p ごとに、末尾から ``tail[i] == tail[i + p]`` が続く範囲を測る（上限で切れた途中の断片も含めて数える）。
    覆う字数が最も長い周期を選び、同じなら短い周期を選ぶ。
    """
    best_unit, best_span = 0, 0
    for unit in range(1, min(REPEAT_UNIT_MAX_CHARS, len(tail) // 2) + 1):
        index = len(tail) - unit - 1
        while index >= 0 and tail[index] == tail[index + unit]:
            index -= 1
        span = len(tail) - index - 1
        if span >= 2 * unit and span > best_span:
            best_unit, best_span = unit, span
    return (best_unit, best_span // best_unit) if best_unit else (0, 0)


def _json_state(text: str, tail_start: int) -> dict[str, Any]:
    """JSON の字句の状態（文字列の内か外か・エスケープ・入れ子の深さ）を先頭から追う。"""
    in_string = escaped = False
    depth = 0
    open_string_start = -1
    whitespace_run = 0
    tail_in_string = 0
    for index, char in enumerate(text):
        if in_string:
            if index >= tail_start:
                tail_in_string += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char in " \t\r\n":
            whitespace_run += 1
            continue
        whitespace_run = 0
        if char == '"':
            in_string, open_string_start = True, index
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth = max(0, depth - 1)
    return {
        "json_in_string": in_string,
        "json_depth": depth,
        # 閉じていない文字列の字数（文字列の外なら 0）。
        "json_open_string_chars": len(text) - open_string_start - 1 if in_string else 0,
        # 末尾に続く、文字列の外の空白の字数（#5077 の打ち切りの対象）。
        "json_trailing_whitespace_chars": 0 if in_string else whitespace_run,
        "tail_in_string_chars": tail_in_string,
    }


def summarize_output_shape(text: str, *, tail_chars: int = TAIL_CHARS) -> dict[str, Any]:
    """未完了の出力 ``text`` の形の要約（字数・末尾の文字の種類・反復・JSON の状態）。本文は含めない。"""
    tail_start = max(0, len(text) - tail_chars)
    tail = text[tail_start:]
    unit, count = _tail_repeat(tail)
    fragments: dict[str, int] = {}
    for length in FRAGMENT_LENGTHS:
        counts = Counter(tail[i : i + length] for i in range(len(tail) - length + 1))
        fragments[str(length)] = counts.most_common(1)[0][1] if counts else 0
    return {
        "output_chars": len(text),
        "tail_chars": len(tail),
        "tail_classes": dict(sorted(Counter(_char_class(char) for char in tail).items())),
        "tail_repeat_unit_chars": unit,
        "tail_repeat_count": count,
        # 末尾の範囲で最も多く現れた断片の回数（断片の長さごと。重なりも数える）。
        "tail_top_fragment_counts": fragments,
        **_json_state(text, tail_start),
    }
