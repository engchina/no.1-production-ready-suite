"""表の値を、列の型と書式を当てた表示の文字にする（#5837）。

`docx_document`（Word）と `pptx_presentation`（PowerPoint）の共通の部品。この 2 つの形式は、表の値を
cell の書式ではなく文字として入れる（Excel は値と表示形式を cell に持つので、`xlsx_workbook` が
自分で読む）。Run・DB・ツールの層に依らない。

- 列の型（`text` / `number` / `date`）で値を読む。数値・日付として読めない値は、文字にしたり
  丸めたりせずに誤りにする（呼ぶ側が列の型を文字に直す）。
- 書式の名前（`table_columns` の `NUMBER_FORMATS`・`DATE_FORMATS`）から、Excel の表示と同じ見え方の
  文字を作る（桁区切り・小数の桁数・百分率・四捨五入）。
- 誤りは値を含まない場所・種類・直し方（`SpecIssue`）で返す。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

from app.features.agent.table_columns import (
    JSON_NUMBER,
    ColumnType,
    SpecIssue,
    ValueProblem,
    iso_date_value,
)

# 数値として表示する桁の上限（指数の大きい値を展開しない）。
MAX_NUMBER_DIGITS = 40

# 書式の名前 → 小数の桁数と、百分率か（名前は `NUMBER_FORMATS` と同じ）。
NUMBER_DISPLAY: dict[str, tuple[int, bool]] = {
    "integer": (0, False),
    "decimal1": (1, False),
    "decimal2": (2, False),
    "percent": (0, True),
    "percent1": (1, True),
}
# 書式の名前 → 年月日の区切りと、時刻を出すか（名前は `DATE_FORMATS` と同じ）。
DATE_DISPLAY: dict[str, tuple[str, bool]] = {
    "date": ("-", False),
    "date_slash": ("/", False),
    "datetime": ("-", True),
}
# Excel の表示と同じ四捨五入（桁の多い値も丸めずに扱える精度）。
_DECIMAL = Context(prec=MAX_NUMBER_DIGITS * 3, rounding=ROUND_HALF_UP)
# XML に入れられない文字（制御文字・対にならない surrogate・非文字）。
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    type: ColumnType
    # 書式の名前（数値は `NUMBER_FORMATS`、日付は `DATE_FORMATS` の key）。None は既定。
    format: str | None = None


def clean_text(value: str) -> str:
    """XML に入れられない文字を除き、改行を `\\n` にそろえる。"""
    return _XML_ILLEGAL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return clean_text(value if isinstance(value, str) else str(value)).strip() or None


def _number_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if JSON_NUMBER.fullmatch(text) is None:
            raise ValueProblem("not_number", "数値として読めません。")
        number = Decimal(text)
    elif isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueProblem("not_number", "数値として読めません。")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueProblem("not_number", "数値として読めません。")
        number = Decimal(repr(value))
    else:
        number = Decimal(value)
    if number.is_zero():
        return Decimal(0)
    if (
        len(number.as_tuple().digits) > MAX_NUMBER_DIGITS
        or abs(number.adjusted()) > MAX_NUMBER_DIGITS
    ):
        raise ValueProblem("precision", "桁が多すぎて、数値として表示できません。")
    return number


_CONVERTERS: dict[str, Callable[[Any], Any]] = {
    "text": _text_value,
    "number": _number_value,
    "date": iso_date_value,
}


def _number_text(number: Decimal, format_name: str | None) -> str:
    if format_name is None:
        # 書式なしは、値のまま（桁区切りなし。指数は展開する）。
        return format(number, "f")
    decimals, percent = NUMBER_DISPLAY[format_name]
    scaled = number.scaleb(2, context=_DECIMAL) if percent else number
    rounded = scaled.quantize(Decimal(1).scaleb(-decimals), context=_DECIMAL)
    if rounded.is_zero():
        rounded = abs(rounded)
    return f"{rounded:,.{decimals}f}" + ("%" if percent else "")


def _date_text(value: date | datetime, format_name: str) -> str:
    separator, with_time = DATE_DISPLAY[format_name]
    text = f"{value.year:04d}{separator}{value.month:02d}{separator}{value.day:02d}"
    if not with_time:
        return text
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    return f"{text} {moment.hour:02d}:{moment.minute:02d}"


def column_values(
    at: str, column: Column, cells: Sequence[Any], issues: list[SpecIssue]
) -> list[Any] | None:
    """1 つの列の値を、列の型で読む（文字・`Decimal`・日付と日時。空は None）。

    読めない値は、最初の 1 件を誤りにして None を返す（`at` は列の場所）。
    """
    convert = _CONVERTERS[column.type]
    values: list[Any] = []
    for row_index, cell in enumerate(cells):
        try:
            values.append(convert(cell))
        except ValueProblem as problem:
            issues.append(
                SpecIssue(
                    f"{at}.type",
                    problem.code,
                    f"この列の {row_index + 1} 行目の値は、{problem.message}{problem.fix}",
                )
            )
            return None
    return values


def column_texts(column: Column, values: Sequence[Any]) -> list[str]:
    """1 つの列の、型で読んだ値（`column_values`）を表示の文字にする（None は空）。"""
    if column.type == "number":
        return ["" if value is None else _number_text(value, column.format) for value in values]
    if column.type == "date":
        # 既定は内容から決める（時刻のある値が 1 つでもあれば日時）。
        has_time = any(isinstance(value, datetime) for value in values)
        name = column.format or ("datetime" if has_time else "date")
        return ["" if value is None else _date_text(value, name) for value in values]
    return ["" if value is None else str(value) for value in values]


__all__ = [
    "DATE_DISPLAY",
    "MAX_NUMBER_DIGITS",
    "NUMBER_DISPLAY",
    "Column",
    "clean_text",
    "column_texts",
    "column_values",
]
