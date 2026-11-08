"""長い根拠の原文を分けて読むときの切れ目の候補（#4667）。

字数は「入るかどうか」の確認だけに使い（``batched_evidence``）、切る位置は原文の構造から強い順に決める。
章節の区切り（根拠の本文の中で章節が分かるとき）か見出しの区切り → 段落の区切り → 文の区切り。表は行の途中で切らない。
章節は根拠が持つ子の範囲（``child_ranges``）と、子の今の章節（章節ナビゲーションが正本）で決める。
今の章節が分からない根拠は、子の取込の章節（``source_context.children``）で決める。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from rag_parser_core.parsed_table import html_table_layout

from rag_engine.chunking.headings import _heading_level, _heading_patterns
from rag_engine.retrieval.table_header_lines import _SEPARATOR_CELL, _TABLE_LINE, _cells, span_header_lines

# 表の続きの組で、読取の入力に添える表頭（LLM が読むためだけ）。本文に入れないので引用単位と座標は元の原文の行のまま。
CONTINUED_TABLE_HEADER_KEY = "continued_table_header"
_MARKDOWN_HEADING = re.compile(r"#{1,6}\s+\S")
_HTML_TABLE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)


def _form(text: str) -> str:
    """見出しの行と章節の名前を比べる形（全角・半角と空白、Markdown の見出しの印の違いを除く）。"""
    return "".join(unicodedata.normalize("NFKC", text).split()).lstrip("#")


def _is_heading(line: str) -> bool:
    """番号の形か Markdown の記法の見出しの行か（文として終わる番号の行は本文）。"""
    stripped = line.strip()
    if _MARKDOWN_HEADING.match(stripped):
        return True
    return _heading_level(stripped) is not None and _heading_patterns().sentence_heading.search(stripped) is None


def _lines(text: str) -> list[tuple[int, int]]:
    """物理行の開始と終了の位置。"""
    lines: list[tuple[int, int]] = []
    position = 0
    for line in text.split("\n"):
        lines.append((position, position + len(line)))
        position += len(line) + 1
    return lines


@dataclass(frozen=True)
class _Table:
    start: int
    # 表頭の後ろ（最初のデータ行の先頭）。表頭が無ければ ``start`` と同じ。
    body_start: int
    end: int
    rows: tuple[tuple[int, int], ...]
    header: str


@dataclass(frozen=True)
class EvidenceCuts:
    """切れ目の候補（本文の中の位置）。``levels`` は強い順で、各段は昇順。"""

    levels: tuple[tuple[int, ...], ...]
    tables: tuple[_Table, ...]

    def table_header_at(self, position: int) -> str:
        """``position`` が表のデータ行の中なら、その表の表頭（無ければ空）。"""
        return next((table.header for table in self.tables if table.body_start <= position < table.end), "")


def _markdown_table(text: str, run: Sequence[tuple[int, int]], recorded: Collection[str]) -> _Table:
    has_rule = all(_SEPARATOR_CELL.fullmatch(cell) for cell in _cells(text[run[1][0] : run[1][1]]))
    # 区切りの行が無い表は、取込が記録した見出しの行だけを表頭にする（列名を推測しない）。
    header_lines = 2 if has_rule else 1 if text[run[0][0] : run[0][1]].strip() in recorded else 0
    if header_lines == 0:
        return _Table(run[0][0], run[0][0], run[-1][1], tuple(run), "")
    body_start = run[header_lines][0] if len(run) > header_lines else run[-1][1]
    return _Table(run[0][0], body_start, run[-1][1], tuple(run), text[run[0][0] : run[header_lines - 1][1]])


def _markdown_tables(text: str, lines: Sequence[tuple[int, int]], recorded: Collection[str]) -> list[_Table]:
    """連続する表の行の並び。列の数が変わったら別の表とする。"""
    tables: list[_Table] = []
    run: list[tuple[int, int]] = []
    columns = 0

    def close() -> None:
        if len(run) >= 2:
            tables.append(_markdown_table(text, run, recorded))
        run.clear()

    for start, end in lines:
        line = text[start:end]
        if not _TABLE_LINE.match(line):
            close()
            continue
        cells = len(_cells(line))
        if run and cells != columns:
            close()
        columns = cells
        run.append((start, end))
    close()
    return tables


def _html_tables(text: str) -> list[_Table]:
    tables: list[_Table] = []
    for match in _HTML_TABLE.finditer(text):
        layout = html_table_layout(match.group())
        if layout is None:
            continue
        base = match.start()
        rows = tuple((base + start, base + end) for start, end in layout.row_ranges)
        header = f"{layout.table_open}{layout.caption}{''.join(layout.rows[: layout.header_rows])}" if layout.header_rows else ""
        tables.append(_Table(base + layout.table_start, rows[layout.header_rows][0], base + layout.table_end, rows, header))
    return tables


def _ingested_child_paths(value: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """根拠の子の取込の章節（子の ID → 章節の名前の列）。"""
    context = value.get("source_context")
    if not isinstance(context, Mapping):
        return {}
    return {
        str(child["source_id"]): tuple(str(part) for part in child.get("section_path") or ())
        for child in context.get("children") or ()
        if isinstance(child, Mapping) and child.get("source_id")
    }


def section_child_ids(values: Iterable[Mapping[str, Any]]) -> list[str]:
    """根拠の子の ID（今の章節を読む対象。重複なし）。"""
    return list(dict.fromkeys(child_id for value in values for child_id in _ingested_child_paths(value)))


def _section_cuts(
    value: Mapping[str, Any], text: str, lines: Sequence[tuple[int, int]], current_paths: Mapping[str, Sequence[str]]
) -> tuple[list[int], set[str]]:
    """子の章節が変わる行の先頭と、本文で見出しになる章節の名前（比べる形）。

    章節の最初の子が文脈に無いことがあるので、章節の名前の行が子の手前にあればその行で切る。
    ``current_paths`` は子の今の章節（子の ID → 章節の名前の列。どの章節にも入らない子は空）。
    """
    context = value.get("source_context")
    if not isinstance(context, Mapping):
        return [], set()
    paths = _ingested_child_paths(value)
    previous = tuple(str(part) for part in context.get("section_path") or ())
    # 取込の見出しの行は今の章節に無くても見出しなので、直後で切らないように名前は両方を持つ。
    titles = {_form(part) for path in (previous, *paths.values()) for part in path}
    rows = sorted(value.get("child_ranges") or (), key=lambda item: (item["start"], item["end"]))
    current = {child_id: tuple(str(part) for part in current_paths[child_id]) for child_id in paths if child_id in current_paths}
    # 今の章節が 1 つでも分かる根拠は、取込の章節と混ぜない（章節の木が違うので、混ぜると偽の境目になる）。
    if any(current.values()):
        paths = current
        previous = next((current[str(row["id"])] for row in rows if str(row["id"]) in current), ())
        titles |= {_form(part) for path in current.values() for part in path}
    base = int(value.get("start", 0))
    cuts: list[int] = []
    known_end = 0
    for row in rows:
        path = paths.get(str(row["id"]))
        if not path:
            continue
        start, end = row["start"] - base, row["end"] - base
        if path != previous and 0 < start < len(text):
            changed = next(
                (index for index, (a, b) in enumerate(zip(path, previous, strict=False)) if a != b), min(len(path), len(previous))
            )
            wanted = {_form(title) for title in path[changed:]}
            cut = next((a for a, b in lines if known_end <= a < start and _form(text[a:b]) in wanted), None)
            if cut is None:
                line_start = text.rfind("\n", 0, start) + 1
                # 行の途中から始まる子は章節の先頭ではない。
                cut = line_start if not text[line_start:start].strip() else None
            if cut:
                cuts.append(cut)
        previous = path
        known_end = max(known_end, end)
    return cuts, titles


def evidence_cuts(value: Mapping[str, Any], current_paths: Mapping[str, Sequence[str]]) -> EvidenceCuts:
    """根拠の本文の切れ目の候補。章節の区切りが分かる本文は見出しを推定しない。"""
    text = str(value["text"])
    lines = _lines(text)
    sections, titles = _section_cuts(value, text, lines, current_paths)
    html_tables = _html_tables(text)
    tables = [*_markdown_tables(text, lines, span_header_lines(value)), *html_tables]
    blocked = {position for table in tables for position in range(table.start + 1, table.body_start + 1)}
    blocked.update(position for table in tables for start, end in table.rows for position in range(start + 1, end))
    sentence_ends = {match.start() for match in _heading_patterns().sentence_boundary.finditer(text)}
    # Markdown の行の区切りは改行で、文の区切りに入っている。1 行に書かれた HTML の表は行の終わりを足す。
    row_ends = {end for table in html_tables for _start, end in table.rows[:-1]} | {table.end for table in html_tables}

    headings: list[int] = []
    paragraphs: list[int] = []
    # 見出しの行の直後では切らない（見出しだけが前の組に残る）。
    after_heading: set[int] = set()
    previous: tuple[int, int] | None = None
    previous_is_heading = False
    for start, end in lines:
        if text[start:end].strip():
            if previous_is_heading:
                after_heading.add(start)
            heading = _is_heading(text[start:end])
            previous_is_heading = heading or _form(text[start:end]) in titles
            if heading:
                headings.append(start)
            if previous is not None and (previous[1] in sentence_ends or not text[previous[0] : previous[1]].strip()):
                paragraphs.append(start)
        previous = (start, end)

    def allowed(positions: Iterable[int], *, keep_heading: bool = False) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    position
                    for position in positions
                    if 0 < position < len(text) and position not in blocked and not (keep_heading and position in after_heading)
                }
            )
        )

    largest = allowed(sections, keep_heading=True) or allowed(headings, keep_heading=True)
    levels = (
        largest,
        allowed(paragraphs, keep_heading=True),
        allowed({*sentence_ends, *row_ends}),
        # 行が予算より大きい表だけ、最後の手段として行の中の文の区切りで切る。
        tuple(sorted(position for position in sentence_ends if 0 < position < len(text))),
    )
    return EvidenceCuts(levels, tuple(tables))
