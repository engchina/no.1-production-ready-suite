"""引用した原文が構造的な区切り線・書式だけかを検査する。意味支持は判定しない。"""

import html
import re

from rag_engine.chunking.constants import _flatten_table_html
from rag_engine.parsing.structural_text import structural_only

_TABLE_TAG = re.compile(r"</?(?:table|thead|tbody|tfoot|tr|t[dh]|caption|colgroup)\b[^<>]*>", re.IGNORECASE)
_TABLE_WRAPPER = re.compile(r"</?(?:table|thead|tbody|tfoot|caption|colgroup)\b[^<>]*>|<col\b[^<>]*>", re.IGNORECASE)
_TABLE_ROW = re.compile(r"<tr\b[^<>]*>(.*?)</tr\s*>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL = re.compile(r"<t[dh]\b([^<>]*)>(.*?)</t[dh]\s*>", re.IGNORECASE | re.DOTALL)
_COL_SPAN = re.compile(r"\bcolspan\s*=\s*[\"']?(\d+)", re.IGNORECASE)
_INLINE_TAG = re.compile(r"<[^<>]{1,40}>")
# colspan は文書由来の値なので、空のセルを足す数に上限を置く。
_MAX_COL_SPAN = 20


def citation_content_unusable(quote: str) -> bool:
    """短い文字・数字・記号を一律に除かず、構造的な行だけの範囲を識別する。"""
    return structural_only(quote)


def _table_row(match: re.Match[str]) -> str:
    cells: list[str] = []
    for attributes, body in _TABLE_CELL.findall(match.group(1)):
        span = _COL_SPAN.search(attributes)
        # ponytail: rowspan は列をそろえない。行だけを引用すると結合元のセルが手元に無い。
        cells += [" ".join(html.unescape(_INLINE_TAG.sub(" ", body)).split())]
        cells += [""] * (min(int(span.group(1)), _MAX_COL_SPAN) - 1 if span else 0)
    return f"| {' | '.join(cells)} |\n" if cells else ""


def table_html_as_rows(text: str) -> str:
    """回答の本文に出す表の HTML を、タグの無い ``| A | B |`` の行にする（#4717）。

    閉じていない断片（行の境目が読めなかった表）は、行にせずタグだけを外す。
    """
    if not _TABLE_TAG.search(text):
        return text
    rows = _TABLE_WRAPPER.sub("", _TABLE_ROW.sub(_table_row, text))
    if _TABLE_TAG.search(rows):
        return _flatten_table_html(text)
    return re.sub(r"\n\s*\n", "\n", rows).strip()
