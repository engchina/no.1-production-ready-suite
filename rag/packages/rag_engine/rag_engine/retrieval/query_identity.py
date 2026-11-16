"""検索文の表記差だけをそろえる同一性のキー。"""

import re
import unicodedata
from collections.abc import Sequence


def normalized_query_key(query: str) -> str:
    """NFKC と前後・連続空白を正規化し、語間の区切りと大小文字は保持する。"""
    return " ".join(unicodedata.normalize("NFKC", query).split())


def dedupe_query_key(query: str) -> str:
    """検索文の重複を判定するキー（NFKC・casefold・空白をすべて除く）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(query or "")).casefold())


def dedupe_queries(queries: Sequence[str]) -> tuple[str, ...]:
    """前後の空白を除いた検索文を、`dedupe_query_key` が同じものは最初の 1 つだけ残して返す。"""
    selected: list[str] = []
    seen: set[str] = set()
    for query in queries:
        text = str(query or "").strip()
        key = dedupe_query_key(text)
        if not key or key in seen:
            continue
        selected.append(text)
        seen.add(key)
    return tuple(selected)
