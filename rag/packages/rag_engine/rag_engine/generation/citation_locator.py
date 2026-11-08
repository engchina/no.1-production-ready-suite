"""説明の文に写された内部の位置を、構造化した出典と比べて外す（#4153）。

根拠の ID と引用単位の番号は ``citations`` の欄で受け取り、利用者に見せる出典はコードが描く。モデルは同じ位置を説明の
文の括弧にも写すことがある（根拠の ID と行の番号の括弧）。その括弧は出典の欄の写しで、文の内容を持たない。

判定は括弧の字の形ではなく、中身の字種と出典の座標で行う: 括弧の中が根拠の ID・番号・行の単位の語（言語資源）だけで、
資料の本文のコードではなく、ID が無いときは番号が自分の出典の範囲に入るもの。文を含む括弧は位置の写しと決められない
ので文に残し、残った ID を ``residual`` で返す（呼び出し側が既存の印と是正に乗せる）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from rag_engine.generation.grounding_cues import grounding_cues
from rag_engine.models.llm import GroundedCitation
from rag_engine.retrieval.character_forms import fold_width

EVIDENCE_LABEL = re.compile(r"(?<![A-Za-z0-9])E[0-9]{1,3}(?![A-Za-z0-9])")
_BRACKET = re.compile(r"(\s*)([（(\[【])([^（）()\[\]【】\n]*)([）)\]】])")
_NUMBER = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class CleanedLocators:
    text: str
    # 位置の写しの括弧にあった根拠の ID（資料のコードを除く）。
    labels: tuple[str, ...] = ()
    # 文を含む箇所に残っていた根拠の ID。文から ID の字だけを除いたので、文が崩れている可能性がある。
    residual: tuple[str, ...] = ()


def _content(inner: str) -> str:
    """括弧の中の、根拠の ID と番号と区切りの記号を除いた字（語か文）。"""
    rest = _NUMBER.sub("", EVIDENCE_LABEL.sub("", inner))
    return "".join(char for char in rest if unicodedata.category(char)[0] in "LNM")


def _in_cited_range(numbers: Sequence[int], citations: Sequence[GroundedCitation]) -> bool:
    """番号がすべて、同じ出典の引用の範囲に入るか。"""
    for citation in citations:
        cited = [int(value) for value in _NUMBER.findall(citation.lines)]
        if cited and all(min(cited) <= number <= max(cited) for number in numbers):
            return True
    return False


def clean_evidence_locators(
    text: str, *, codes: Collection[str], citations: Sequence[GroundedCitation] = (), source_text: str = ""
) -> CleanedLocators:
    """説明の文から位置の写しの括弧を外す。``codes`` と ``source_text`` は資料の本文（本文にある記載は外さない）。"""
    unit_words = set(grounding_cues().words["citation_unit_words"])
    cited_ids = {citation.evidence_id for citation in citations}
    copied: list[str] = []

    def bracket(match: re.Match[str]) -> str:
        space, opening, body, closing = match.groups()
        inner = fold_width(body)
        labels = EVIDENCE_LABEL.findall(inner)
        content = _content(inner)
        numbers = [int(value) for value in _NUMBER.findall(EVIDENCE_LABEL.sub("", inner))]
        internal = [label for label in labels if label not in codes]
        if not numbers and not content and labels:
            copied.extend(internal)
            if not internal:
                return match.group()
            retained = [label for label in labels if label in codes]
            return f"{space}{opening}{'、'.join(retained)}{closing}" if retained else ""
        if not numbers or (content and content.casefold() not in unit_words):
            return match.group()
        if labels:
            if not internal and not cited_ids.intersection(labels):
                return match.group()
            copied.extend(internal)
            return ""
        if content and _in_cited_range(numbers, citations) and fold_width(f"{opening}{body}{closing}") not in fold_width(source_text):
            return ""
        return match.group()

    cleaned = _BRACKET.sub(bracket, text)
    residual = [label for label in EVIDENCE_LABEL.findall(cleaned) if label not in codes]
    cleaned = EVIDENCE_LABEL.sub(lambda match: match.group() if match.group() in codes else "", cleaned)
    return CleanedLocators(cleaned.strip(), tuple(dict.fromkeys(copied)), tuple(dict.fromkeys(residual)))


# 括弧の中が根拠の ID 1 つだけの箇所（「（E3）」）。括弧はそのままにして中身だけを出典の名前にする。
_BRACKETED_LABEL = re.compile(rf"([（(])\s*({EVIDENCE_LABEL.pattern})\s*([）)])")
# 括弧の外の ID と、前後の半角の空白（「E2 の表」）。出典の名前は「」で区切るので、空白は残さない。
_SPACED_LABEL = re.compile(rf"[ \t]?({EVIDENCE_LABEL.pattern})[ \t]?")


def replace_evidence_labels(text: str, sources: Mapping[str, str], *, codes: Collection[str]) -> str:
    """文に混ざった根拠の表示 ID を、利用者に見せる出典の名前に置き換える（#4626）。

    ``sources`` は今回モデルへ送った根拠の表示 ID から出典の名前（本文の出典の行と同じ書き方）への対応。対応の無い
    ID（送っていない根拠・存在しない番号）と、資料の本文にある同じ字のコード（``codes``）は置き換えずに残す
    （呼び出し側の内部の番号の検査がそのまま漏れとして扱う）。ID のトークンの形だけを見て、文の意味は判定しない。
    """

    def source(label: str) -> str | None:
        return None if label in codes else sources.get(label)

    def bracketed(match: re.Match[str]) -> str:
        opening, label, closing = match.groups()
        name = source(label)
        return match.group() if name is None else f"{opening}{name}{closing}"

    def bare(match: re.Match[str]) -> str:
        name = source(match.group(1))
        return match.group() if name is None else f"「{name}」"

    return _SPACED_LABEL.sub(bare, _BRACKETED_LABEL.sub(bracketed, text))
