"""説明・要約に書いた年を、質問と引いた根拠に照らす（#4042）。

監査をしない回答では、引用の位置が合っていても、説明に足した年の誤りを検知する段が無い。年は決定的に照らせるので
コードで確かめる。業務の語や特定の年は持たない（元号・単位は言語資源）。本文は書き換えず、見つけた値を返すだけ
（呼び出し側が既存の印と是正に乗せる）。

計算で新しく現れる正しい年を誤って疑わないよう、次は無い年に数えない: 質問か根拠の年から、年をまたぐ 1 年と、
質問か根拠の「N 年」の分だけ離れた年。西暦と元号の換算は持たないので、書き方の違う年は確かめない（疑わない）。

月日は確かめない。期限の計算で新しい月日が現れるのは正常で、月末・営業日などの数え方をコードでは決められない
（質問の全数字を照らす広い検査は、計算の結果を誤って疑う）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rag_parser_core.language import current_pack
from rag_parser_core.language.text import alternation, text_patterns

# 西暦とみなす 4 桁（「1000 件」のような数を年に数えない。年の単位か日付の区切りが続くものだけを取り出す）。
_GREGORIAN = r"(?<![0-9])([12][0-9]{3})"
_DATE_SEPARATED = re.compile(_GREGORIAN + r"[-/.](?:0?[1-9]|1[0-2])(?:[-/.](?:0?[1-9]|[12][0-9]|3[01]))?(?![0-9])")


@dataclass(frozen=True)
class _Years:
    """本文の年。``gregorian`` は西暦の値、``era`` は（元号, 年）。``spans`` は「N 年」の N（離れた年の計算に使う）。"""

    gregorian: frozenset[int]
    era: frozenset[tuple[str, int]]
    spans: frozenset[int]


def _read_years(text: str) -> _Years:
    pack = current_pack()
    patterns = text_patterns()
    year_unit = alternation((pack.text("labels", "year"), *pack.words("numbers", "fiscal_year_units")))
    normalized = unicodedata.normalize("NFKC", text)
    letters = pack.mapping("numbers", "era_letters")
    first_year = alternation(pack.words("numbers", "first_year_words"))
    gregorian = {int(match.group(1)) for match in re.finditer(rf"{_GREGORIAN}\s*{year_unit}", normalized)}
    gregorian |= {int(match.group(1)) for match in _DATE_SEPARATED.finditer(normalized)}
    era = {
        (letters.get(match.group(1), match.group(1)), 1 if not match.group(2).isdigit() else int(match.group(2)))
        for match in re.finditer(rf"({patterns.era}|{patterns.era_letter})\s*([0-9]+|{first_year})\s*{year_unit}", normalized)
    }
    # 元号の年は期間ではない。期間は 1〜3 桁の「N 年」だけ（4 桁は西暦）。
    without_era = re.sub(rf"(?:{patterns.era}|{patterns.era_letter})\s*[0-9]+", "", normalized)
    spans = {int(match.group(1)) for match in re.finditer(rf"(?<![0-9])([0-9]{{1,3}})\s*{year_unit}", without_era)}
    return _Years(frozenset(gregorian), frozenset(era), frozenset(spans))


def _reachable(years: Iterable[int], spans: Iterable[int]) -> set[int]:
    """質問か根拠の年と、そこから年をまたぐ・期間の分だけ離れた年。"""
    offsets = {0, 1, *spans}
    return {year + sign * offset for year in years for offset in offsets for sign in (1, -1)}


def absent_years(text: str, question: str, sources: Sequence[str]) -> list[str]:
    """``text`` の年のうち、質問と根拠（``sources``）から説明できない年（出てきた順の値。無ければ空）。"""
    claimed = _read_years(text)
    if not claimed.gregorian and not claimed.era:
        return []
    known = [_read_years(part) for part in (question, *sources)]
    gregorian = {year for row in known for year in row.gregorian}
    era = {year for row in known for year in row.era}
    spans = {span for row in known for span in row.spans}
    unit = current_pack().text("labels", "year")
    absent: list[str] = []
    # 書き方の違う年は換算できないので、質問・根拠に別の書き方の年があれば確かめない。
    if not era:
        reachable = _reachable(gregorian, spans)
        absent.extend(f"{year}{unit}" for year in sorted(claimed.gregorian - reachable))
    if not gregorian:
        names = {name for name, _year in era}
        reachable_era = {(name, year) for name in names for year in _reachable((y for n, y in era if n == name), spans)}
        absent.extend(f"{name}{year}{unit}" for name, year in sorted(claimed.era - reachable_era))
    return absent


def span_texts(span: dict) -> list[str]:
    """根拠の本文と、年の出どころになり得る文書の見出し（同じ機能・文書の本文を含む）。"""
    parts = [str(span.get("text") or ""), str(span.get("source") or "")]
    for key in ("section_path", "function", "function_texts", "adjacent_texts", "document_texts"):
        parts.extend(str(part) for part in span.get(key) or ())
    return parts
