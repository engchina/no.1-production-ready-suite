"""検索の絞り込み（文書の有効期間の基準日）を扱う。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class RetrievalFilter:
    """ADB/FAQ 検索で使う絞り込み。今は文書の有効期間の基準日だけを持つ。

    as_of は YYYY-MM-DD または空（検索時に今日として扱う）。
    """

    as_of: str = ""

    def to_request_filters(self) -> dict[str, str]:
        """HTTP API / SDK の `filters` dict へ変換します。"""
        return {"as_of": self.as_of} if self.as_of else {}


# HTTP API / SDK の `filters` で受け付けるキー。retrieval_filter_from_values の引数名と一致させる。
REQUEST_FILTER_KEYS = frozenset({"as_of"})


def as_of_value(value: Any) -> str:
    """基準日入力を YYYY-MM-DD に正規化する。空は空のまま返し、形式不正は ValueError。"""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("基準日は YYYY-MM-DD で指定してください。")
    date.fromisoformat(text)
    return text


def retrieval_filter_from_values(*, as_of: Any = "") -> RetrievalFilter:
    """UI / API の基準日入力を検索用 RetrievalFilter に変換します。"""
    return RetrievalFilter(as_of=as_of_value(as_of))
