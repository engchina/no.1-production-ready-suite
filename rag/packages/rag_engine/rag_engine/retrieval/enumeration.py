"""列挙・網羅を求める質問の要求（件数・章の範囲・読む範囲）と、根拠で満たせた項目の数え方（#1503）。

CRAG の十分性の判定（評価器）は「見えている根拠で答えられるか」を判定するため、「すべて挙げて」の問で、
ほかにも該当する記載が残っているかを判定できない（見えている数件で「足りる」と答える）。そこで、

- 質問から列挙の要求を決定的に取り出す（``enumeration_request``）: 網羅の語（「すべて」「漏れなく」「章ごとに」）、
  示された件数（「5 つすべて」）、章の範囲（「第 2 章から第 6 章まで」）、読む範囲（文書全体か、当たった章節か）。
- 評価器には項目ごとの根拠（``enumerated_items``。項目・根拠の chunk_uid・根拠の原文の写し）を出させ、プログラムで
  原文の写しが根拠の本文にあるかを確かめてから、件数・章の範囲の欠けを数える（``enumeration_check``）。評価器の
  ``status`` は件数・範囲の無い問でだけ使い、件数・範囲のある問では数えた結果を正にする（自己申告に頼らない）。
- 件数も範囲も無い網羅の問は、根拠の数からは足りるかを決められない。章節の拡張（``section_expansion``）は、
  評価器が足りると判定しても、読む範囲（``minimum_scope``。当たった章節 = ``leaf``、文書全体 = ``document``）まで
  読んでから判定し直す。

LLM・DB を呼ばない（決定論のテストができる）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

# 列挙の観点の名前（``task_contract`` の required_aspects と CRAG の aspect_checks）。
ENUMERATION_ASPECT = "enumeration_coverage"

# 網羅を求める語。「すべての〇〇に適用されますか」のような名詞の修飾（「すべての」）は網羅の要求ではないので除く。
_EXHAUSTIVE = re.compile(
    r"(?:すべて|全て|全部)(?!の)|漏れなく|もれなく|洩れなく|残らず|網羅|余さず|列挙|洗い出"
    r"|(?:章|節)ごとに|各(?:章|節)"
)
# 一覧を求める述語（網羅の語と組で列挙の要求とみなす）。
_LISTING = re.compile(
    r"挙げ|あげて|教え|示し|示して|列挙|書き出|洗い出|答え|まとめ|知りたい|一覧|ありますか|どれ|どのような|何を"
)
# 示された件数（「5 つすべて」「6 件」）。「点」は「点検」と紛れるので数えない。
_COUNT = re.compile(r"(\d+|[一二三四五六七八九十]+)\s*(?:つ|件|個|項目|種類|か所|箇所|ヶ所|カ所)")
_KANJI_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CHAPTER = re.compile(r"第\s*(\d+)\s*章")
_CHAPTER_RANGE = re.compile(r"第\s*(\d+)\s*章\s*(?:から|より|〜|～|~|－|-)\s*第?\s*(\d+)\s*章")
# 文書全体を範囲にする語。
_DOCUMENT_WIDE = re.compile(r"全体を通して|全体で|全体から|全体の中で|文書全体|資料全体|全章|すべての章|全ての章|各章|章ごと")
# 文書の名前で範囲を示す（「〜要領で、…をすべて」）。章・節を名指ししないときだけ文書全体にする。
_DOCUMENT_NAMED = re.compile(
    r"(?:要領|規程|規則|規定|手順書|マニュアル|ガイド|要綱|計画|台帳|資料|文書)(?:の中)?(?:で|では|には)[、,]"
)
# 件数の上限（質問の数字の読み違いで無理な件数を求めない）。
MAX_EXPECTED_COUNT = 50
# 章の範囲の上限。
MAX_CHAPTERS = 30

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
        """足りると判定する前に読む範囲（件数・範囲の無い網羅の問だけ）。``leaf`` / ``document`` / None。"""
        if self.countable:
            return None
        return "document" if self.scope == "document" else "leaf"

    def to_payload(self) -> dict[str, Any]:
        return {"scope": self.scope, "expected_count": self.expected_count, "chapters": list(self.chapters),
                "minimum_scope": self.minimum_scope}


def _number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if len(value) == 2 and value[0] == "十":
        return 10 + _KANJI_DIGITS.get(value[1], 0)
    if len(value) == 2 and value[1] == "十":
        return _KANJI_DIGITS.get(value[0], 0) * 10
    return _KANJI_DIGITS.get(value)


def enumeration_request(question: str) -> EnumerationRequest | None:
    """列挙・網羅を求める質問なら要求を返す（求めなければ None）。"""
    return _enumeration_request(unicodedata.normalize("NFKC", str(question or "")))


@lru_cache(maxsize=256)
def _enumeration_request(text: str) -> EnumerationRequest | None:
    if not (_EXHAUSTIVE.search(text) and _LISTING.search(text)):
        return None
    counts = [number for raw in _COUNT.findall(text)
              if (number := _number(raw)) is not None and 2 <= number <= MAX_EXPECTED_COUNT]
    chapters: tuple[int, ...] = ()
    span = _CHAPTER_RANGE.search(text)
    if span:
        start, end = sorted((int(span.group(1)), int(span.group(2))))
        if end - start < MAX_CHAPTERS:
            chapters = tuple(range(start, end + 1))
    named = tuple(dict.fromkeys(int(value) for value in _CHAPTER.findall(text)))
    if not chapters and len(named) >= 2 and re.search(r"各|ごと|それぞれ", text):
        chapters = named
    if len(chapters) >= 2 or _DOCUMENT_WIDE.search(text):
        scope: Scope = "document"
    elif not named and not re.search(r"\d+(?:\.\d+)+\s*節|第\s*\d+\s*節", text) and _DOCUMENT_NAMED.search(text):
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


def _chapters_of(texts: Sequence[str]) -> set[int]:
    return {int(value) for text in texts for value in _CHAPTER.findall(unicodedata.normalize("NFKC", text))}


@dataclass(frozen=True)
class _Unit:
    text: str
    sections: tuple[str, ...]


def _units(context: Any, records: Sequence[Any]) -> dict[str, _Unit]:
    """候補の chunk_uid → 根拠の本文（親・同じ親の子）と見出し。子の uid も親の単位に写す。"""
    units: dict[str, _Unit] = {}

    def heading(record: Any) -> list[str]:
        return [str(item) for item in (getattr(record, "metadata", None) or {}).get("section_path") or [] if item]

    for parent in getattr(context, "evidence_tree", ()) or ():
        members = [parent.record, *(child.record for child in parent.children)]
        unit = _Unit(text="\n".join(str(getattr(item, "text", "") or "") for item in members),
                     sections=tuple(dict.fromkeys(title for item in members for title in heading(item))))
        for item in members:
            uid = str(getattr(item, "chunk_uid", "") or getattr(item, "id", "") or "")
            if uid:
                units.setdefault(uid, unit)
    for record in records:
        uid = str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")
        if uid and uid not in units:
            units[uid] = _Unit(text=str(getattr(record, "text", "") or ""), sections=tuple(heading(record)))
    return units


def enumeration_check(
    question: str,
    checks: Sequence[Mapping[str, Any]],
    items: Sequence[Any],
    context: Any,
    records: Sequence[Any],
    canonical: Callable[[str], str],
) -> list[dict[str, Any]]:
    """評価器の観点の判定に、列挙の観点（``ENUMERATION_ASPECT``）の数えた結果を入れて返す。

    ``items`` は評価器の ``enumerated_items``（``item`` / ``source_ids`` / ``quote``）。原文の写し（``quote``）が根拠の
    本文（親と同じ親の子）にある項目だけを数え、同じ原文の項目は 1 つにする。

    - 件数のある問: 数えた項目が件数に足りなければ missing。
    - 章の範囲のある問: 範囲の章のうち、数えた項目の根拠の見出しに無い章があれば missing。
    - どちらも無い問: 評価器の status（根拠のある項目が 1 つも無ければ missing）。読む範囲の規則は章節の拡張が持つ。

    列挙の問でなければ ``checks`` をそのまま返す。
    """
    request = enumeration_request(question)
    result = [dict(item) for item in checks]
    if request is None:
        return result
    units = _units(context, records)
    counted: list[tuple[str, str]] = []  # (原文の写しの形, 根拠の uid)
    seen: set[str] = set()
    for item in items:
        quote = _folded(getattr(item, "quote", "") if not isinstance(item, Mapping) else item.get("quote", ""))
        raw_sources = getattr(item, "source_ids", None) if not isinstance(item, Mapping) else item.get("source_ids")
        sources = [uid for value in raw_sources or () if (uid := canonical(str(value))) and uid in units]
        if len(quote) < 2 or quote in seen:
            continue
        found = next((uid for uid in sources if quote in _folded(units[uid].text)), "")
        if not found:
            continue
        seen.add(quote)
        counted.append((quote, found))
    source_ids = list(dict.fromkeys(uid for _quote, uid in counted))
    reasons: list[str] = []
    llm = [item for item in result if item.get("aspect") == ENUMERATION_ASPECT]
    if request.countable:
        ok = True
        if request.expected_count is not None:
            if len(counted) < request.expected_count:
                ok = False
            reasons.append(f"要求 {request.expected_count} 件に対し、原文を確かめた項目 {len(counted)} 件")
        if request.chapters:
            covered = _chapters_of([title for uid in source_ids for title in units[uid].sections])
            lacking = [number for number in request.chapters if number not in covered]
            if lacking:
                ok = False
                reasons.append("根拠の無い章: " + "、".join(f"第 {number} 章" for number in lacking))
            else:
                reasons.append("範囲の章（" + "、".join(f"第 {number} 章" for number in request.chapters)
                               + "）すべてに根拠がある")
        status = "supported" if ok else "missing"
    else:
        claimed = llm[0].get("status") if len(llm) == 1 else "missing"
        status = "supported" if claimed == "supported" and counted else "missing"
        reasons.append(f"原文を確かめた項目 {len(counted)} 件"
                       + ("" if claimed == "supported" else "（評価器は網羅を確かめていない）"))
    entry = {"aspect": ENUMERATION_ASPECT, "status": status, "source_ids": source_ids if status == "supported" else [],
             "reason": "。".join(reasons), "counted_items": len(counted),
             "expected_count": request.expected_count, "chapters": list(request.chapters)}
    return [*(item for item in result if item.get("aspect") != ENUMERATION_ASPECT), entry]


__all__ = [
    "ENUMERATION_ASPECT",
    "EnumerationRequest",
    "enumeration_check",
    "enumeration_request",
    "minimum_scope",
]
