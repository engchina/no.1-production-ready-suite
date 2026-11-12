"""引用した原文が構造的な区切り線・書式だけかを検査する。意味支持は判定しない。"""

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from rag_engine.chunking.constants import _flatten_table_html
from rag_engine.generation.answer_table import _cell
from rag_engine.generation.evidence_cuts import _form, _lines, _markdown_tables
from rag_engine.html_text import html_table_text
from rag_engine.retrieval.table_header_lines import span_header_lines

_TABLE_TAG = re.compile(r"</?(?:table|thead|tbody|tfoot|tr|t[dh]|caption|colgroup)\b[^<>]*>", re.IGNORECASE)
# colspan は文書由来の値なので、空のセルを足す数に上限を置く。
_MAX_COL_SPAN = 20
_HTML_HEADER_ROW = re.compile(r"\s*<tr\b[^<>]*>(?:\s*<th\b.*?</th\s*>)+\s*</tr\s*>\s*", re.IGNORECASE | re.DOTALL)
_MARKDOWN_HEADING_MARK = re.compile(r"#{1,6}\s+\S")
# JSON の文書の字句（文字列の引用符・括弧）。文書の本文の文としては出ない記号だけを見る。
_JSON_DOCUMENT_SUFFIX = ".json"
_JSON_PUNCTUATION = re.compile(r'["{}\[\]]')


def _is_section_heading(line: str, section_path: frozenset[str]) -> bool:
    """行が取込の章節の見出しそのものか、Markdown の見出しの行か（語の意味は読まない）。"""
    return _MARKDOWN_HEADING_MARK.match(line.strip()) is not None or _form(line) in section_path


def _is_table_header(line: str, span_text: str, recorded: frozenset[str]) -> bool:
    """行が表の見出しの行（または区切りの行）か。データの行は事実なので当てはめない。"""
    stripped = line.strip()
    if _HTML_HEADER_ROW.fullmatch(stripped):
        return True
    for table in _markdown_tables(span_text, _lines(span_text), recorded):
        if table.header and stripped in {header_line.strip() for header_line in table.header.splitlines()}:
            return True
    return False


def structure_only_quote(text: str, span: Mapping[str, Any]) -> bool:
    """原文の行が見出し・表の見出しの行・JSON の文書の生の断片だけか。文書の構造と字句だけで決める（#4755）。"""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    if PurePosixPath(str(span.get("source") or "")).suffix.lower() == _JSON_DOCUMENT_SUFFIX and _JSON_PUNCTUATION.search(text):
        return True
    section_path = frozenset(_form(str(heading)) for heading in span.get("section_path") or ())
    span_text = str(span.get("text") or "")
    recorded = span_header_lines(span)
    return all(_is_section_heading(line, section_path) or _is_table_header(line, span_text, recorded) for line in lines)


def table_html_as_rows(text: str) -> str:
    """回答の本文に出す表の HTML を、タグの無い ``| A | B |`` の行にする（#4717）。

    閉じていない断片（行の境目が読めなかった表）は、行にせずタグだけを外す。
    """
    if not _TABLE_TAG.search(text):
        return text
    parsed = html_table_text(text, max_colspan=_MAX_COL_SPAN)
    if not parsed.complete_rows:
        return _flatten_table_html(text)
    rows = "".join("| " + " | ".join(_cell(cell) for cell in part) + " |" if isinstance(part, tuple) else part for part in parsed.parts)
    return re.sub(r"\n\s*\n", "\n", rows).strip()
