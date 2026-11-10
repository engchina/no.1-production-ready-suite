"""HTML 表の表示用テキスト。原文や引用の座標は変更しない。"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class TableText:
    plain: str
    parts: tuple[str | tuple[str, ...], ...]
    complete_rows: bool


class _TableTextParser(HTMLParser):
    def __init__(self, max_colspan: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_colspan = max_colspan
        self.plain: list[str] = []
        self.parts: list[str | tuple[str, ...]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.colspan = 1
        self.depth = 0
        self.complete = True

    def _space(self, text: str) -> None:
        if self.cell is not None:
            self.cell.append(text)
        else:
            self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.plain.append(" ")
        if tag == "table":
            self.depth += 1
            if self.depth > 1:
                self.complete = False
            self._space("\n")
        elif tag == "tr":
            if self.row is not None:
                self.complete = False
            self.parts.append("\n")
            self.row = []
        elif tag in {"td", "th"}:
            if self.row is None or self.cell is not None:
                self.complete = False
            self.cell = []
            self.colspan = self._span(dict(attrs).get("colspan"))
        else:
            self._space("\n" if tag == "caption" else " ")

    def _span(self, raw: str | None) -> int:
        value = (raw or "1").strip()
        if not value.isascii() or not value.isdecimal():
            return 1
        value = value.lstrip("0") or "0"
        # 巨大な属性値でも int の変換やセルの展開を入力に比例させない。
        if len(value) > len(str(self.max_colspan)):
            return self.max_colspan
        return max(1, min(int(value), self.max_colspan))

    def handle_endtag(self, tag: str) -> None:
        self.plain.append(" | " if tag in {"td", "th"} else " / " if tag == "tr" else " ")
        if tag in {"td", "th"}:
            if self.cell is None or self.row is None:
                self.complete = False
            else:
                self.row.append(" ".join("".join(self.cell).split()))
                self.row.extend([""] * (self.colspan - 1))
            self.cell = None
        elif tag == "tr":
            if self.cell is not None or self.row is None:
                self.complete = False
            elif self.row:
                self.parts.extend((tuple(self.row), "\n"))
            self.row = None
        elif tag == "table":
            self.depth = max(0, self.depth - 1)
            self._space("\n")
        else:
            self._space("\n" if tag == "caption" else " ")

    def handle_data(self, data: str) -> None:
        self.plain.append(data)
        self._space(data)


def html_table_text(text: str, *, max_colspan: int = 20) -> TableText:
    """完全な行はセル配列、途中で切れた行は平文で表示できる形に読む。"""
    parser = _TableTextParser(max_colspan)
    parser.feed(text)
    parser.close()
    return TableText(
        plain="".join(parser.plain),
        parts=tuple(parser.parts),
        complete_rows=parser.complete and parser.row is None and parser.cell is None,
    )
