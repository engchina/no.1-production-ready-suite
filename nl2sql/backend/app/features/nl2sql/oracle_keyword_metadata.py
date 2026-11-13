"""Oracle 自身の keyword 情報を、許可・否決へ変換せず修正用の事実として渡す。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal, NotRequired, TypedDict, cast

NATIVE_KEYWORD_SOURCE: Final = "SYS.V_$RESERVED_WORDS"
NATIVE_KEYWORD_COLUMNS = ("KEYWORD", "RESERVED", "RES_TYPE", "RES_ATTR", "RES_SEMI")
MAX_NATIVE_KEYWORD_ROWS = 16
_FIELD_MEANINGS = {
    "RESERVED": "Y: 識別子として使用できない。N: この列では予約されていない。",
    "RES_TYPE": "Y: 型名として使用できない。N: この列では予約されていない。",
    "RES_ATTR": "Y: 属性名として使用できない。N: この列では予約されていない。",
    "RES_SEMI": "Y: DMLなど一部の状況で識別子として使用できない。N: この列では予約されていない。",
}


class NativeKeywordRow(TypedDict):
    KEYWORD: str
    RESERVED: str
    RES_TYPE: str
    RES_ATTR: str
    RES_SEMI: str


class NativeKeywordMetadata(TypedDict):
    availability: Literal["available", "not_found", "unavailable"]
    source: Literal["SYS.V_$RESERVED_WORDS"]
    rows: list[NativeKeywordRow]
    field_meanings: dict[str, str]
    unavailable_reason: NotRequired[str]


def _metadata(
    availability: Literal["available", "not_found", "unavailable"],
) -> NativeKeywordMetadata:
    return {
        "availability": availability,
        "source": NATIVE_KEYWORD_SOURCE,
        "rows": [],
        "field_meanings": dict(_FIELD_MEANINGS),
    }


def unavailable_native_keyword_metadata(reason: str) -> NativeKeywordMetadata:
    result = _metadata("unavailable")
    result["unavailable_reason"] = reason
    return result


def native_keyword_metadata_from_rows(
    keyword: str, columns: Sequence[object], rows: Sequence[object]
) -> NativeKeywordMetadata:
    """欠損や不正な flag を N に補わず、複数行もそのまま有界で保持する。"""
    if tuple(columns) != NATIVE_KEYWORD_COLUMNS:
        return unavailable_native_keyword_metadata("invalid_columns")
    if len(rows) > MAX_NATIVE_KEYWORD_ROWS:
        return unavailable_native_keyword_metadata("row_limit_exceeded")
    if not rows:
        return _metadata("not_found")
    result = _metadata("available")
    for row in rows:
        if (
            not isinstance(row, Sequence)
            or isinstance(row, str | bytes)
            or len(row) != len(NATIVE_KEYWORD_COLUMNS)
        ):
            return unavailable_native_keyword_metadata("invalid_row_shape")
        word = row[0]
        if not isinstance(word, str) or word != keyword.upper():
            return unavailable_native_keyword_metadata("keyword_mismatch")
        flags = row[1:]
        if any(not isinstance(flag, str) or flag not in {"Y", "N"} for flag in flags):
            return unavailable_native_keyword_metadata("invalid_flags")
        # 上の検査で全 flag が str と分かっても、可変長の Sequence の型は絞れない。
        result["rows"].append(
            {
                "KEYWORD": word,
                "RESERVED": cast(str, row[1]),
                "RES_TYPE": cast(str, row[2]),
                "RES_ATTR": cast(str, row[3]),
                "RES_SEMI": cast(str, row[4]),
            }
        )
    return result
