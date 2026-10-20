"""表・一覧の行の対応に反して、別の行の値を述べた文を見つける（#2659）。

根拠の表（HTML の表・Markdown の表・「名前：値」の行の並び）は、値をその行の区分・名前に対応させる。文が
ある行の区分・名前と、別の行にしか無い値（コード・番号など英数字だけの値）を同じ所で結び付けていれば、語と値が
根拠のどこかにあっても、根拠の対応とは違う。語の一致・話題の一致では見分けられないので、表の行で確かめる。

決定的な照合で、意味は見ない。誤って正しい文を外さないように、次のときだけ「対応しない」とする。

- 値のある行のどれにも、文が同じ所で述べた行の手がかり（その表の 1 行にだけあるセル。同じ行のコードなどの値も含む）
  が無く、
- 別の行の区分・名前（英数字だけの値でないセル）を、文が同じ所で述べている。別の行の値を同じ所で述べただけ
  （複数の行の値の列挙・範囲「254・253・300」「A101〜A103」）では疑わない。
- 見出しの行のセルと、表の複数の行にあるセルは、行を見分ける手がかりにしない。

「同じ所」は文の読点・句点で区切った区間で、値の無い区間（「区分Ⅱの場合、」）は続く区間と合わせて見る。
最終の検証（`evaluation.answer_validation`）と回答の生成の検査（`generation.grounded`）が同じ判定を使う。
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rag_engine.retrieval.character_forms import fold_width

# 値とみなすセル（英数字と区切りの「-」「_」「.」だけ。NFKC でローマ数字・全角も英数字にそろえる）。
_VALUE_CELL = re.compile(r"[0-9a-z]+(?:[-_.][0-9a-z]+)*")
_MAX_VALUE_CHARS = 16
# 区分・名前とみなすセルの長さ（短すぎると別の語の一部に当たり、長い説明の文は文が丸ごと写すことが少ない）。
_MIN_LABEL_CHARS = 2
_MAX_LABEL_CHARS = 40
# セルの照合に使う頭の部分（「01（通常）」「区分Ⅱ (旧)」の括弧・空白の前）。
_CELL_KEY_END = re.compile(r"\s*[（(［\[〔【「『<＜]|\s{2,}|\s*[、。,，;；]")
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t[dh]\s*>", re.IGNORECASE | re.DOTALL)
_SPAN = re.compile(r"""(row|col)span\s*=\s*["']?(\d+)""", re.IGNORECASE)
_TAG = re.compile(r"<[^<>]*>")
_TABLE_BLOCK = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
# Markdown の表の区切りの行（| --- | :---: |）。
_PIPE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
# 「名前：値」の行の区切り（全角のコロン・縦棒・タブ。半角のコロンは時刻「9:00」と区別する）。
_PAIR_SEPARATOR = re.compile(r"[：｜|\t]|(?<![0-9]):(?![0-9])")
# 文の区間の区切り（読点・句点・改行・セミコロン）。
_SENTENCE_END = re.compile(r"[。！？!?\n]+")
_CLAUSE_END = re.compile(r"[、，,；;]")
# 文の中で値の照合から除く所（根拠の ID・出典の括弧・頁の参照・強調の印）。
_CITATION = re.compile(r"【[^【】]*】|\[[^\[\]]*(?:ID|id)[^\[\]]*\]")
_CHUNK_ID = re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{6,}(?:[0-9A-Fa-f:…‥\-]|\.{2,})*:[0-9]+(?![0-9A-Za-z])")
_PAGE_REF = re.compile(r"(?<![0-9A-Za-z])[pP]{1,2}\.\s*[0-9]+(?:\s*[-–〜~]\s*[0-9]+)?")
_EMPHASIS = re.compile(r"\*\*|__|`")
# 行の先頭の箇条書きの番号（「1. 」「(2)」「③」）。手順の番号を値として照合しない。
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[0-9]{1,3}[.)．、]|[（(][0-9]{1,3}[）)]|[①-⑳])\s*")
# 漢字（「手順1」「第1」「1件」のように 1 文字の数字に続く・先立つ語）。
_IDEOGRAPH = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _fold(text: str) -> str:
    """照合の形（NFKC・小文字・空白をまとめる）。ローマ数字「Ⅱ」は「ii」、全角の英数字は半角になる。"""
    return " ".join(unicodedata.normalize("NFKC", fold_width(text)).lower().split())


def _cell_key(cell: str) -> str:
    """セルの照合に使う頭の部分（括弧の補足・続く説明を除く）。"""
    folded = _fold(cell)
    match = _CELL_KEY_END.search(folded)
    return (folded[: match.start()] if match else folded).strip(" ・:：")


def _is_value(key: str) -> bool:
    return 0 < len(key) <= _MAX_VALUE_CHARS and _VALUE_CELL.fullmatch(key) is not None


def _is_label(key: str) -> bool:
    return _MIN_LABEL_CHARS <= len(key) <= _MAX_LABEL_CHARS and not _is_value(key)


@dataclass(frozen=True)
class Table:
    """表の本文の行（見出しの行を除く）。各行はセルの照合の形の集合。``cells`` は照合の形 → 原文のセル。"""

    rows: tuple[frozenset[str], ...]
    cells: dict[str, str]


def _take_carried(carried: dict[int, tuple[int, str]], column: int, cells: list[str]) -> int:
    """上の行の rowspan のセルを、その列から続く分だけ行に写し、次の列の位置を返す。"""
    while column in carried:
        remaining, text = carried[column]
        cells.append(text)
        if remaining <= 1:
            del carried[column]
        else:
            carried[column] = (remaining - 1, text)
        column += 1
    return column


def _html_rows(block: str) -> list[tuple[bool, list[str]]]:
    """HTML の表の行（見出しの行か、セルの本文）。rowspan / colspan は覆う行・列にセルを写す。"""
    carried: dict[int, tuple[int, str]] = {}  # 列 → (残りの行の数, 本文)
    rows: list[tuple[bool, list[str]]] = []
    for row_match in _ROW.finditer(block):
        cells: list[str] = []
        kinds: list[str] = []
        column = 0
        for kind, attributes, body in _CELL.findall(row_match.group(1)):
            column = _take_carried(carried, column, cells)
            text = html.unescape(_TAG.sub(" ", body)).strip()
            spans = {name.lower(): int(value) for name, value in _SPAN.findall(attributes)}
            for _ in range(max(1, min(spans.get("col", 1), 50))):
                cells.append(text)
                kinds.append(kind.lower())
                if spans.get("row", 1) > 1:
                    carried[column] = (min(spans["row"], 500) - 1, text)
                column += 1
        _take_carried(carried, column, cells)
        if cells:
            rows.append((bool(kinds) and all(kind == "th" for kind in kinds), cells))
    return rows


def _pipe_rows(lines: Sequence[str]) -> list[tuple[bool, list[str]]]:
    """Markdown の表の行（区切りの行の直前の行は見出し）。"""
    rows: list[tuple[bool, list[str]]] = []
    for index, line in enumerate(lines):
        if _PIPE_RULE.match(line) or line.count("|") < 2:
            continue
        header = index + 1 < len(lines) and _PIPE_RULE.match(lines[index + 1]) is not None
        rows.append((header, [cell.strip() for cell in line.strip().strip("|").split("|")]))
    return rows


def _pair_runs(lines: Sequence[str]) -> list[list[tuple[bool, list[str]]]]:
    """「名前：値」の行（区切りで 2 つ以上のセルに分かれる行）の、続いて並ぶ行のまとまり。見出しの行は持たない。"""
    runs: list[list[tuple[bool, list[str]]]] = [[]]
    for line in lines:
        cells = [cell.strip() for cell in _PAIR_SEPARATOR.split(line) if cell.strip()] if line.count("|") < 2 else []
        if len(cells) >= 2:
            runs[-1].append((False, cells))
        elif runs[-1]:
            runs.append([])
    return [run for run in runs if run]


def _table(rows: Sequence[tuple[bool, list[str]]]) -> Table | None:
    body = [cells for header, cells in rows if not header]
    # 見出しの印（<th>・区切りの行）が無い HTML の表は、値のセルが無い最初の行を見出しとみなす。
    if body and len(body) == len(rows) and len(body) >= 3 and not any(_is_value(_cell_key(cell)) for cell in body[0]):
        body = body[1:]
    keyed = tuple(frozenset(key for cell in cells if (key := _cell_key(cell))) for cells in body)
    originals: dict[str, str] = {}
    for cells in body:
        for cell in cells:
            if key := _cell_key(cell):
                originals.setdefault(key, " ".join(cell.split())[:_MAX_LABEL_CHARS])
    return Table(keyed, originals) if len(keyed) >= 2 else None


def evidence_tables(text: str) -> list[Table]:
    """根拠の本文の表（HTML の表・Markdown の表・「名前：値」の行の並び）。行が 2 つ以上のものだけ。"""
    source = str(text or "")
    tables: list[Table] = []
    for block in _TABLE_BLOCK.findall(source):
        table = _table(_html_rows(block))
        if table is not None:
            tables.append(table)
    rest = _TABLE_BLOCK.sub("\n", source)
    lines = [line for line in rest.split("\n") if line.strip()]
    for rows in (_pipe_rows(lines), *_pair_runs(lines)):
        table = _table(rows)
        if table is not None:
            tables.append(table)
    return tables


def _clean_claim(text: str) -> str:
    """文から、根拠の ID・出典の括弧・頁の参照・強調の印を除く（値の照合に当てない）。"""
    text = _LIST_MARKER.sub(" ", str(text or ""))
    text = _CITATION.sub(" ", text)
    text = _CHUNK_ID.sub(" ", text)
    text = _PAGE_REF.sub(" ", text)
    return _fold(_EMPHASIS.sub("", text))


def _mentions(key: str, window: str) -> bool:
    """セルの照合の形が区間に現れるか。

    英数字で終わる・始まるセルは、前後が英数字の続きなら別の語。1 文字の値（「1」）は、前後が漢字なら手順の番号・
    数量（「手順1」「1件」）とみなして数えない。
    """
    start = window.find(key)
    while start >= 0:
        end = start + len(key)
        before = window[start - 1] if start > 0 else ""
        after = window[end] if end < len(window) else ""
        joined = (key[0].isalnum() and key[0].isascii() and before.isascii() and before.isalnum()) or (
            key[-1].isalnum() and key[-1].isascii() and after.isascii() and after.isalnum()
        )
        counted = len(key) == 1 and (_IDEOGRAPH.fullmatch(before or " ") or _IDEOGRAPH.fullmatch(after or " "))
        if not joined and not counted:
            return True
        start = window.find(key, start + 1)
    return False


def _windows(claim: str, values: set[str]) -> Iterable[str]:
    """文の区間（読点・句点で区切る）。表の値（``values``）の無い区間は続く区間と合わせる。"""
    for sentence in _SENTENCE_END.split(claim):
        pending = ""
        for clause in _CLAUSE_END.split(sentence):
            pending = f"{pending} {clause}" if pending else clause
            if any(_mentions(value, clause) for value in values):
                yield pending
                pending = ""
        if pending.strip():
            yield pending


def _unbound_in_table(window: str, table: Table) -> tuple[list[tuple[str, str]], set[str]]:
    """区間の値のうち、この表で別の行の区分・名前と結び付けたもの（値, 別の行のセル）と、この表で行に結び付いた値。

    行に結び付いたかは、値の行にあって区間に現れる手がかり（1 行にだけあるセル。区分・名前とコードなどの値）で見る。
    疑うのは、別の行の区分・名前（値でないセル）を述べたときだけ。別の行の値どうしの共起は、複数の行の値の列挙・
    範囲で、区分と値の結び付けではない。別の行は、その行にも値のセルがある行（同じ並びの別の項目）だけを数える。
    注記・見出しのような値の無い行の語を述べただけでは疑わない。
    """
    counts: dict[str, int] = {}
    for row in table.rows:
        for key in row:
            counts[key] = counts.get(key, 0) + 1
    mentioned = {key for key in counts if (_is_value(key) or _is_label(key)) and _mentions(key, window)}
    # 行を見分ける手がかり: その表の 1 行にだけあり、区間に現れるセル。
    distinct = {key for key in mentioned if counts[key] == 1}
    unbound: list[tuple[str, str]] = []
    bound: set[str] = set()
    for value in sorted(key for key in mentioned if _is_value(key)):
        rows = [row for row in table.rows if value in row]
        others = distinct - {value}
        if any(row & others for row in rows):
            bound.add(table.cells.get(value, value))
            continue
        elsewhere = sorted(
            key
            for key in others
            if _is_label(key) and any(key in row and any(_is_value(cell) for cell in row - {key}) for row in table.rows if row not in rows)
        )
        if elsewhere:
            unbound.append((table.cells.get(value, value), table.cells.get(elsewhere[0], elsewhere[0])))
    return unbound, bound


def unbound_table_values(claim: str, evidence_texts: Sequence[str]) -> list[tuple[str, str]]:
    """文が、根拠の表の別の行の区分・名前と結び付けた値の組（値, 別の行のセル。根拠の原文の表記）。

    同じ値をどれかの表で同じ行の区分・名前と結び付けていれば、その値は外さない（表をまたいで疑わない）。
    """
    tables = [table for text in evidence_texts for table in evidence_tables(text)]
    if not tables:
        return []
    values = {key for table in tables for row in table.rows for key in row if _is_value(key)}
    found: dict[str, str] = {}
    for window in _windows(_clean_claim(claim), values):
        bound: set[str] = set()
        candidates: dict[str, str] = {}
        for table in tables:
            unbound, bound_here = _unbound_in_table(window, table)
            bound |= bound_here
            for value, other in unbound:
                candidates.setdefault(value, other)
        for value, other in candidates.items():
            if value not in bound:
                found.setdefault(value, other)
    return sorted(found.items())


__all__ = ["Table", "evidence_tables", "unbound_table_values"]
