"""質問の記号で分かる小問の単位と、入力の写しを固定する。語で質問の種類を判定しない。"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from rag_parser_core.language import LanguagePack, current_pack, per_pack
from rag_parser_core.language.text import alternation

# 小問の印の型は言語資源（question_cues）。このモジュールは印を見つける文法だけを持つ。

# CRAG の評価器が確かめる観点（質問によらず固定）。観点の語は応答の項目（aspect_checks）の値でもある。
REQUIRED_ASPECTS = ("business_object", "requested_result", "applicability")


@dataclass(frozen=True)
class _Cues:
    """言語資源から組み立てた、小問の印の並びと型。"""

    # 丸数字の並び（範囲ごと）・番号の型（(正規表現, 文中のどこでもよいか)）・印の後に続けば
    # 資料の番号への言及になる語・印の前の区切り（文頭・空白のほかに、文末・読点・コロンの字）。
    circled: tuple[str, ...]
    numbers: tuple[tuple[re.Pattern[str], bool], ...]
    reference: re.Pattern[str]
    boundary: str


def _build_cues(pack: LanguagePack) -> _Cues:
    def words(key: str) -> tuple[str, ...]:
        return pack.words("question_cues", key)

    return _Cues(
        circled=tuple("".join(_expand((item,))) for item in words("subquestion_circled_numbers")),
        # 記号で始まる型（「(N)」）は文中のどこでも、数・字で始まる型（「N)」「問N」）は区切りの後だけを印にする。
        numbers=tuple(
            (re.compile(re.escape(pattern).replace("N", r"([0-9０-９]{1,2})")), not pattern[0].isalnum())
            for pattern in words("subquestion_number_patterns")
            if "N" in pattern
        ),
        reference=re.compile(rf"\s*{alternation(words('subquestion_reference_followers'))}"),
        boundary="".join((*words("unit_split_chars"), *words("comma_chars"), *pack.words("evidence_cues", "label_colon_chars"))),
    )


def _expand(items: tuple[str, ...]) -> list[str]:
    """1 文字か「最初-最後」の範囲の並びを字の並びにする。"""
    chars: list[str] = []
    for item in items:
        if len(item) == 3 and item[1] == "-":
            chars.extend(chr(code) for code in range(ord(item[0]), ord(item[2]) + 1))
        elif len(item) == 1:
            chars.append(item)
    return chars


_cues = per_pack(_build_cues)


def task_contract(question: str) -> dict[str, Any]:
    """入力の写しと記号で分かる小問を返す。解析は質問ごとに 1 回だけ行い、呼出側が書き換えても共有されない写しを返す。"""
    # 小問の印の型で結果が変わるので、言語資源の hash も cache の鍵にする（#2181）。
    return copy.deepcopy(_task_contract(question, current_pack().hash))


@lru_cache(maxsize=256)
def _task_contract(question: str, _pack_hash: str = "") -> dict[str, Any]:
    return {
        "original_question": question,
        "request_units": request_units(question),
        "required_aspects": list(REQUIRED_ASPECTS),
    }


def subquestion_markers(question: str) -> list[tuple[int, str]]:
    """質問に並べた小問の印の (位置, 印の原文) を返す (#2399)。小問として並んでいなければ空。

    印は言語資源の小問の印（丸数字の範囲・「(N)」などの型）。1 から始まり 1 ずつ増える同じ種類の印が 2 つ以上
    あるときだけ小問とみなす（1 つだけの「③」や「①と②の違い」は資料の番号への言及）。印の直後に言及の語
    （「の」「と」「～」など）が続く印は数えない。数・字で始まる型（「1)」）は文頭・空白・文末・読点・コロンの後だけ。
    種類が混ざるときは、最初の小問が前にある並びを使う。
    """
    cues = _cues()
    candidates: dict[str, list[tuple[int, int, str]]] = {}

    def add(style: str, start: int, end: int, ordinal: int) -> None:
        if not cues.reference.match(question, end):
            candidates.setdefault(style, []).append((start, ordinal, question[start:end]))

    for index, chars in enumerate(cues.circled):
        if len(chars) < 2:
            continue
        for match in re.finditer(f"[{re.escape(chars)}]", question):
            add(f"c{index}", match.start(), match.end(), chars.index(match.group()) + 1)
    for index, (pattern, anywhere) in enumerate(cues.numbers):
        for match in pattern.finditer(question):
            start = match.start()
            before = question[start - 1] if start else ""
            if not anywhere and before and not before.isspace() and before not in cues.boundary:
                continue
            add(f"n{index}", start, match.end(), int(unicodedata.normalize("NFKC", match.group(1))))
    best: list[tuple[int, str]] = []
    for found in candidates.values():
        sequence: list[tuple[int, str]] = []
        for start, ordinal, label in sorted(found):
            if ordinal == len(sequence) + 1:
                sequence.append((start, label))
        if len(sequence) >= 2 and (not best or sequence[0][0] < best[0][0]):
            best = sequence
    return best


def _subquestion_units(question: str, markers: Sequence[tuple[int, str]]) -> list[dict[str, str]]:
    """小問ごとの単位。小問の前の共通の文は各小問に preamble として付ける (#2399)。"""
    preamble = " ".join(question[: markers[0][0]].split())
    ends = [start for start, _ in markers[1:]] + [len(question)]
    units: list[dict[str, str]] = []
    for (start, label), end in zip(markers, ends, strict=True):
        unit = {"id": f"Q{len(units) + 1}", "text": " ".join(question[start:end].split()), "subquestion": label}
        if preamble:
            unit["preamble"] = preamble
        units.append(unit)
    return units


def request_units(question: str) -> list[dict[str, str]]:
    """丸数字・(1) などで並べた小問は小問ごとに 1 単位にする。印が無ければ原文全体を 1 単位にする。"""
    markers = subquestion_markers(question)
    if markers:
        return _subquestion_units(question, markers)
    return [{"id": "Q1", "text": question}] if question.strip() else []


def query_rejection_reason(question: str, query: str, *, grounded_text: str = "") -> str:
    """原質問・明示別名にない業務 ID を含む検索文を拒否する。grounded_text は検証済み別名のみ。

    日付や一文字の版名は ID と扱わず、一般的な言い換えは保持する。
    モデル自身が生成した説明を grounded_text へ渡してはいけない。
    """
    source, value = _text(question), _text(query)
    if source == value:
        return ""
    # 日付・版ではなく業務 ID を対象にする。明示別名以外の新しい ID は推測で追加しない。
    identifiers = re.findall(r"(?<![a-z0-9_])[a-z]{2,}[a-z0-9_-]*\d[a-z0-9_-]*", value)
    support = _text(question + " " + grounded_text)
    if any(not re.search(r"(?<![a-z0-9_])" + re.escape(identifier) + r"(?![a-z0-9_])", support) for identifier in identifiers):
        return "原質問・明示別名にない業務識別子"
    return ""


def filter_queries(question: str, queries: Sequence[str], *, grounded_text: str = "") -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """採用検索文と、理由付きの不採用検索文を返す。入力は変更しない。"""
    accepted, rejected = [], []
    for query in queries:
        reason = query_rejection_reason(question, query, grounded_text=grounded_text)
        if reason:
            rejected.append({"query": query, "reason": reason})
        else:
            accepted.append(query)
    return tuple(accepted), tuple(rejected)


def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower()
