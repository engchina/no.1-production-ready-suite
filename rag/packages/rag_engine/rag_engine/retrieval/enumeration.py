"""列挙・網羅を求める質問の要求（件数・章の範囲・読む範囲）を決める（#1503）。

質問から列挙の要求を決定的に取り出す（``enumeration_request``）: 網羅の語（「すべて」「漏れなく」「章ごとに」）、
示された件数（「5 つすべて」）、章の範囲（「第 2 章から第 6 章まで」）、読む範囲（文書全体か、当たった章節か）。
章節の拡張（``section_expansion``）は読む範囲（``minimum_scope``。当たった章節 = ``leaf``、文書全体 = ``document``）まで
読む。

LLM・DB を呼ばない（決定論のテストができる）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from rag_parser_core.language import current_pack
from rag_parser_core.language.text import alternation, char_class, parse_number, text_patterns

# 列挙の観点の名前（``task_contract`` の required_aspects）。
ENUMERATION_ASPECT = "enumeration_coverage"

# 網羅を求める語・一覧を求める述語・範囲の語は言語資源（enumeration_cues。#2181 段 4）。


@dataclass(frozen=True)
class _NumberPatterns:
    # 示された件数（「5 つすべて」「6 件」）。助数詞は言語資源（「点」は「点検」と紛れるので既定に入れない）。
    count: re.Pattern[str]
    # 章の番号と、章の範囲（「第 2 章から第 6 章まで」）。前置き・単位・範囲のつなぎは言語資源（#2181）。
    chapter: re.Pattern[str]
    chapter_range: re.Pattern[str]
    # 網羅の語（「すべての〇〇に適用されますか」のような名詞の修飾は網羅の要求ではないので除く）と、一覧を求める述語
    # （網羅の語と組で列挙の要求とみなす）。
    exhaustive: re.Pattern[str]
    listing: re.Pattern[str]
    each: re.Pattern[str]
    numbered_section: re.Pattern[str]
    # 文書全体を範囲にする語と、文書の名前で範囲を示す語（「〜要領で、…をすべて」）。
    document_wide: re.Pattern[str]
    document_named: re.Pattern[str]


_number_patterns_by_hash: dict[str, _NumberPatterns] = {}


def _number_patterns() -> _NumberPatterns:
    pack = current_pack()
    cached = _number_patterns_by_hash.get(pack.hash)
    if cached is not None:
        return cached
    text = text_patterns(pack)
    counters = alternation(pack.words("numbers", "count_counters"))
    connectors = alternation(pack.words("numbers", "range_connectors"))
    chapter = rf"{text.prefix}\s*({text.number})\s*{text.chapter_units}"

    def alt(key: str) -> str:
        return alternation(pack.words("enumeration_cues", key))

    comma = char_class(pack.words("question_cues", "comma_chars"))
    patterns = _NumberPatterns(
        count=re.compile(rf"({text.number})\s*{counters}"),
        chapter=re.compile(chapter),
        chapter_range=re.compile(rf"{chapter}\s*{connectors}\s*{text.prefix}?\s*({text.number})\s*{text.chapter_units}"),
        exhaustive=re.compile(
            rf"{alt('exhaustive_quantifiers')}(?!{alt('quantifier_modifier_particles')})|{alt('exhaustive_words')}"
            rf"|{alt('per_unit_words')}"
        ),
        listing=re.compile(alt("listing_words")),
        each=re.compile(alt("each_words")),
        numbered_section=re.compile(rf"\d+(?:\.\d+)+\s*{text.section_units}|{text.prefix}\s*\d+\s*{text.section_units}"),
        document_wide=re.compile(alt("document_wide_words")),
        document_named=re.compile(rf"{alt('document_name_words')}{alt('document_inside_words')}?{alt('document_scope_particles')}{comma}"),
    )
    if len(_number_patterns_by_hash) >= 16:
        _number_patterns_by_hash.clear()
    _number_patterns_by_hash[pack.hash] = patterns
    return patterns


# 件数の上限（質問の数字の読み違いで無理な件数を求めない）。
MAX_EXPECTED_COUNT = 50
# 章の範囲の上限。
MAX_CHAPTERS = 30
# 評価器に挙げさせる項目の上限（章の範囲の問）と、件数も範囲も無い問で挙げさせる数（足りるかは評価器の判定と
# 読む範囲で決めるので、根拠のある項目が確かめられれば足りる）。
MAX_LISTED_ITEMS = 20
OPEN_LISTED_ITEMS = 5

Scope = Literal["section", "document"]


@dataclass(frozen=True)
class EnumerationRequest:
    """質問の列挙の要求。``expected_count`` は示された件数、``chapters`` は網羅する章の番号。"""

    scope: Scope
    expected_count: int | None = None
    chapters: tuple[int, ...] = ()

    @property
    def countable(self) -> bool:
        """根拠の数（件数・章）で足りるかを決められる要求か。"""
        return self.expected_count is not None or bool(self.chapters)

    @property
    def minimum_scope(self) -> str | None:
        """足りると判定する前に読む範囲。``leaf`` / ``document`` / None（範囲の規則なし）。

        件数だけの問は数えた結果で決める（None）。2 つ以上の章の範囲の問は、範囲の章が 1 つの章節に入らないため
        文書全体まで読んでから判定する（下の段で評価器を呼ばない）。件数も範囲も無い問は、範囲の語で決める。
        """
        if len(self.chapters) >= 2:
            return "document"
        if self.countable:
            return None
        return "document" if self.scope == "document" else "leaf"

    @property
    def max_items(self) -> int:
        """評価器に挙げさせる項目の上限（判定に要る数だけ。出力の token と時間を増やしすぎない）。"""
        if self.expected_count is not None:
            return min(MAX_EXPECTED_COUNT, self.expected_count + 2)
        if self.chapters:
            return min(MAX_LISTED_ITEMS, 2 * len(self.chapters))
        return OPEN_LISTED_ITEMS

    def to_payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "expected_count": self.expected_count,
            "chapters": list(self.chapters),
            "minimum_scope": self.minimum_scope,
            "max_items": self.max_items,
        }


def _number(value: str) -> int | None:
    return parse_number(value)


def enumeration_request(question: str) -> EnumerationRequest | None:
    """列挙・網羅を求める質問なら要求を返す（求めなければ None）。"""
    # 言語資源が変われば読み直す（cache の key に言語資源の hash を入れる）。
    return _enumeration_request(unicodedata.normalize("NFKC", str(question or "")), current_pack().hash)


@lru_cache(maxsize=256)
def _enumeration_request(text: str, _pack_hash: str) -> EnumerationRequest | None:
    numbers = _number_patterns()
    if not (numbers.exhaustive.search(text) and numbers.listing.search(text)):
        return None
    counts = [number for raw in numbers.count.findall(text) if (number := _number(raw)) is not None and 2 <= number <= MAX_EXPECTED_COUNT]
    chapters: tuple[int, ...] = ()
    span = numbers.chapter_range.search(text)
    bounds = (_number(span.group(1)), _number(span.group(2))) if span else (None, None)
    if bounds[0] is not None and bounds[1] is not None:
        start, end = sorted((bounds[0], bounds[1]))
        if end - start < MAX_CHAPTERS:
            chapters = tuple(range(start, end + 1))
    named = tuple(dict.fromkeys(n for value in numbers.chapter.findall(text) if (n := _number(value)) is not None))
    if not chapters and len(named) >= 2 and numbers.each.search(text):
        chapters = named
    if len(chapters) >= 2 or numbers.document_wide.search(text):
        scope: Scope = "document"
    elif not named and not numbers.numbered_section.search(text) and numbers.document_named.search(text):
        scope = "document"
    else:
        scope = "section"
    return EnumerationRequest(scope=scope, expected_count=max(counts) if counts else None, chapters=chapters)


def minimum_scope(question: str) -> str | None:
    """章節の拡張で、足りると判定する前に読む範囲（``EnumerationRequest.minimum_scope``）。"""
    request = enumeration_request(question)
    return request.minimum_scope if request is not None else None


def _folded(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()


__all__ = [
    "ENUMERATION_ASPECT",
    "EnumerationRequest",
    "enumeration_request",
    "minimum_scope",
]
