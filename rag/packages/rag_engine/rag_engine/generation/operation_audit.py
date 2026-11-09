"""回答の本文を段落・文に分ける形式の解析。LLM を呼ばず、入力を変更しない。"""

from __future__ import annotations

import re

from rag_engine.parsing.structural_text import is_table_rule as is_table_rule

_QUOTE_ONLY_LINE = re.compile(r"\s*(?:・|[0-9]+[.)]\s*)?「.*」\s*")
# 中の「。」で文を分けない括弧（開きと閉じ。#1455）。
_BRACKET_PAIRS = {"「": "」", "『": "』", "（": "）", "(": ")", "【": "】"}
_CLOSING_BRACKETS = frozenset(_BRACKET_PAIRS.values())
# 前の文に付ける断片（太字・強調の印と閉じ括弧・句点だけ。#1455）。
_MARKS_ONLY = re.compile(r"[\s*_`」』）)】。]*")


def sentence_ranges(line: str, *, ends: str = "。") -> list[tuple[int, int]]:
    """1 行を文に分けた範囲（開始・終了の位置。前後の空白を含む）。

    「。」で文を分けるが、開いた括弧（「」『』（）()【】）の中の「。」では分けない（括弧の深さを数える。
    #1455）。閉じない括弧は行の終わりまで続くとみなし、行の終わりで切る。開いていない閉じ括弧は数えない。
    文の区切りの「。」の直後の閉じ括弧は前の文に含め、太字の印（`**`）・閉じ括弧だけの断片は前の文に付ける。
    Agent の ``answer_passages.sentence_ranges`` と同じ規則（両方のテストが
    platform/contracts/answer-passages/sentence-splits.json の同じ事例で確かめる）。``ends`` は文の終わりの
    文字（既定は回答の段落の「。」。標準回答の単位は「！」「？」でも分ける。#3575）。
    """
    ranges: list[tuple[int, int]] = []
    expected: list[str] = []
    start = index = 0
    while index < len(line):
        char = line[index]
        if char in _BRACKET_PAIRS:
            expected.append(_BRACKET_PAIRS[char])
        elif char in _CLOSING_BRACKETS and char in expected:
            # 対応する開きの括弧まで閉じる（その間の閉じていない括弧も閉じたとみなす）。
            while expected.pop() != char:
                pass
        elif char in ends and not expected:
            end = index + 1
            # 続く閉じ括弧と文の終わりの文字（「！？」「。。」）も前の文に含める。
            while end < len(line) and (line[end] in _CLOSING_BRACKETS or line[end] in ends):
                end += 1
            ranges.append((start, end))
            start = index = end
            continue
        index += 1
    if start < len(line):
        ranges.append((start, len(line)))
    merged: list[tuple[int, int]] = []
    for begin, end in ranges:
        if merged and _MARKS_ONLY.fullmatch(line[begin:end]):
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    return merged


_PASSAGE_CHARS = 600


def answer_passage_ranges(text: str) -> list[tuple[str, int, int]]:
    """``answer_passages`` の段落の ID と、``text`` の中の位置（開始・終了。``text[start:end]`` が段落の原文）。

    MCP の ``rag_validate_answer`` は段落の判定にこの位置を付けて返し、呼び出し側は段落の分け方を写さずに
    位置で段落を特定する（#3240）。
    """
    return text_unit_ranges(text, prefix="A", whole_line=_QUOTE_ONLY_LINE)


# 1 行を 1 つの単位にする表の行（Markdown の表は「|」で始まる。セルの中の句点で行を切らない）。
_TABLE_ROW = re.compile(r"\s*\|.*")


def standard_answer_unit_ranges(text: str) -> list[tuple[str, int, int]]:
    """標準回答の単位（品質評価の比較範囲の固定でモデルが ID で指す）の ID（``S1``…）と位置（#3575）。

    回答の段落（``answer_passage_ranges``）と同じ分け方（行 → 括弧の外の文の終わり。空行は除く）に、
    「！」「？」でも文を分け、表の行と「…」だけの行は 1 行を 1 つの単位にする。箇条書きの項目は行なので
    前後の項目と混ざらない。
    """
    return text_unit_ranges(text, prefix="S", ends="。！？", whole_line=_STANDARD_WHOLE_LINE)


_STANDARD_WHOLE_LINE = re.compile(f"(?:{_QUOTE_ONLY_LINE.pattern})|(?:{_TABLE_ROW.pattern})")


def text_unit_ranges(text: str, *, prefix: str, ends: str = "。", whole_line: re.Pattern[str]) -> list[tuple[str, int, int]]:
    """本文を行・文に分けた単位の ID（``prefix`` と 1 始まりの番号）と位置（``text[start:end]`` が単位の原文）。

    行ごとに、``whole_line`` に合う行は 1 つの単位、それ以外は ``sentence_ranges`` で文に分ける。前後の空白を
    除き、空の行・断片は単位にしない。長い単位は ``_PASSAGE_CHARS`` 字ごとの連続した範囲に分ける。
    """
    result: list[tuple[str, int, int]] = []
    offset = 0
    for line in text.split("\n"):
        ranges = [(0, len(line))] if whole_line.fullmatch(line) else sentence_ranges(line, ends=ends)
        for begin, end in ranges:
            piece = line[begin:end]
            value = piece.strip()
            if not value:
                continue
            lead = begin + len(piece) - len(piece.lstrip())
            for start in range(0, len(value), _PASSAGE_CHARS):
                stop = min(start + _PASSAGE_CHARS, len(value))
                result.append((f"{prefix}{len(result) + 1}", offset + lead + start, offset + lead + stop))
        offset += len(line) + 1
    return result


def answer_passages(text: str) -> list[dict[str, str]]:
    """改写せず文・改行で区切る。長文も600文字の連続範囲として保持する。

    句点直後の閉じ括弧は同じ文に含める。括弧の中の「。」では切らない（``sentence_ranges``。#1455）。
    括弧の途中で切ると、検証で前の半分を外したときに閉じ括弧の残る断片が本文に残る。行全体が1つの
    「…」の引用（原文のみ提示の行。表の引用は複数の文を含む）は、引用の途中で切らずに1段落として扱う。
    段落の位置は ``answer_passage_ranges``。
    """
    return [{"id": passage_id, "text": text[start:end]} for passage_id, start, end in answer_passage_ranges(text)]
