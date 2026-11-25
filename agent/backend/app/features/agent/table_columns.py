"""ファイルに入れる表の、列の型・書式の名前と値の読み方（#5837）。

`xlsx_workbook`（Excel）と `docx_document`（Word）が同じ列の定義（型 `text` / `number` / `date` と
書式の名前）を使うための共通の部品。Run・DB・ツールの層に依らない。

- 書式の名前（`NUMBER_FORMATS`・`DATE_FORMATS`）は 2 つの形式で同じ。値は Excel の表示形式の
  書き方で、Excel はそのまま cell に当て、Word は同じ見え方の文字にする。
- 値の読み方のうち、形式に依らない所だけを置く（JSON の数値の書き方、ISO 8601 の日付）。形式ごとの
  制約（Excel の精度・日付の始まりの年、Word の桁の上限）は、それぞれの module が足す。
- 誤りは値を含まない場所・種類・直し方（`SpecIssue`）で返す（引数の検証の誤りと同じ書き方）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal

ColumnType = Literal["text", "number", "date"]

# 書式の名前 → Excel の表示形式。
NUMBER_FORMATS: dict[str, str] = {
    "integer": "#,##0",
    "decimal1": "#,##0.0",
    "decimal2": "#,##0.00",
    "percent": "0%",
    "percent1": "0.0%",
}
DATE_FORMATS: dict[str, str] = {
    "date": "yyyy-mm-dd",
    "date_slash": "yyyy/mm/dd",
    "datetime": "yyyy-mm-dd hh:mm",
}
TYPE_LABELS: dict[str, str] = {"text": "文字", "number": "数値", "date": "日付"}

# JSON の数値の書き方（文字で渡った数値を数値として読むときの形）。
JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
RETYPE_FIX = "列の type を text にするか、値を直してください。"
_NOT_DATE = "日付（YYYY-MM-DD か ISO 8601 の日時）として読めません。"


@dataclass(frozen=True, slots=True)
class SpecIssue:
    """内容の 1 つの誤り（値を含まない）。"""

    argument: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"argument": self.argument, "code": self.code, "message": self.message}


class ValueProblem(Exception):
    """列の型の値として読めない値（`fix` は直し方）。"""

    def __init__(self, code: str, message: str, fix: str = RETYPE_FIX) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix


def format_issue(at: str, column_type: str, format_name: str | None) -> SpecIssue | None:
    """列の型に使えない書式の名前なら、その誤り（`at` は列の場所）。"""
    allowed = {"number": NUMBER_FORMATS, "date": DATE_FORMATS}.get(column_type, {})
    if format_name is None or format_name in allowed:
        return None
    return SpecIssue(
        f"{at}.format",
        "format",
        f"{TYPE_LABELS[column_type]}の列に使える書式は"
        f"{' / '.join(allowed) if allowed else 'ありません'}（既定は null）。",
    )


def iso_date_value(value: Any) -> date | datetime | None:
    """ISO 8601 の文字を日付・日時にする（時刻が 0 時ちょうどなら日付。時間帯は外す）。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueProblem("not_date", _NOT_DATE)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueProblem("not_date", _NOT_DATE) from exc
    # ファイルの日時は時間帯を持たない。書かれた時刻のまま入れる。
    parsed = parsed.replace(tzinfo=None)
    return parsed.date() if parsed.time() == time.min else parsed


def display_width(text: str) -> int:
    """表示の幅の目安（全角は 2）。複数行は最も長い行。"""
    return max(
        (
            sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in line)
            for line in text.splitlines()
        ),
        default=0,
    )


__all__ = [
    "DATE_FORMATS",
    "JSON_NUMBER",
    "NUMBER_FORMATS",
    "RETYPE_FIX",
    "TYPE_LABELS",
    "ColumnType",
    "SpecIssue",
    "ValueProblem",
    "display_width",
    "format_issue",
    "iso_date_value",
]
