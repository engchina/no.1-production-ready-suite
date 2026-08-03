"""CSV と新旧 Excel 取込で共有する workbook 読取契約。"""

from __future__ import annotations

import importlib
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

OPENXML_WORKBOOK_SUFFIXES = frozenset({".xlsx", ".xlsm"})
LEGACY_WORKBOOK_SUFFIXES = frozenset({".xls"})
WORKBOOK_SUFFIXES = OPENXML_WORKBOOK_SUFFIXES | LEGACY_WORKBOOK_SUFFIXES
CORE_TABULAR_SUFFIXES = frozenset({".csv", ".xlsx", ".xls"})
XLS_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class TabularFileReadError(ValueError):
    """利用者がファイルを差し替えることで回復できる workbook 読取エラー。"""


@dataclass(frozen=True)
class TabularSheet:
    title: str
    rows: list[list[Any]]
    active: bool = False


def _workbook_error(format_name: str) -> str:
    return (
        f"{format_name} の読込に失敗しました。"
        "ファイルが破損、暗号化、または拡張子と内容が不一致でないか確認してください。"
    )


def _read_openxml_workbook(content: bytes) -> list[TabularSheet]:
    openpyxl = importlib.import_module("openpyxl")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise TabularFileReadError(_workbook_error("XLSX")) from exc
    active_title = str(workbook.active.title)
    return [
        TabularSheet(
            title=str(sheet.title),
            rows=[list(row) for row in sheet.iter_rows(values_only=True)],
            active=str(sheet.title) == active_title,
        )
        for sheet in workbook.worksheets
    ]


def _normalize_xls_cell(xlrd: Any, cell: Any, datemode: int) -> Any:
    if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
        return None
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(cell.value, datemode)
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        number = float(cell.value)
        return int(number) if number.is_integer() else number
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return str(xlrd.error_text_from_code.get(cell.value, f"Excel error {cell.value}"))
    return cell.value


def _read_legacy_workbook(content: bytes) -> list[TabularSheet]:
    if not content.startswith(XLS_OLE_SIGNATURE):
        raise TabularFileReadError(_workbook_error("XLS"))
    xlrd = importlib.import_module("xlrd")
    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
        sheets = [
            TabularSheet(
                title=str(sheet.name),
                rows=[
                    [
                        _normalize_xls_cell(
                            xlrd,
                            sheet.cell(row_index, column_index),
                            workbook.datemode,
                        )
                        for column_index in range(sheet.ncols)
                    ]
                    for row_index in range(sheet.nrows)
                ],
                active=index == 0,
            )
            for index, sheet in enumerate(workbook.sheets())
        ]
        workbook.release_resources()
        return sheets
    except TabularFileReadError:
        raise
    except Exception as exc:
        raise TabularFileReadError(_workbook_error("XLS")) from exc


def read_workbook_sheets(filename: str, content: bytes) -> list[TabularSheet]:
    """拡張子に対応する reader で workbook 全 sheet を決定論的に読み取る。"""

    suffix = Path(filename).suffix.lower()
    if suffix in OPENXML_WORKBOOK_SUFFIXES:
        sheets = _read_openxml_workbook(content)
    elif suffix in LEGACY_WORKBOOK_SUFFIXES:
        sheets = _read_legacy_workbook(content)
    else:
        raise TabularFileReadError(
            f"{suffix or '拡張子なし'} は未対応です。CSV、XLSX、XLS のいずれかを指定してください。"
        )
    if not sheets:
        raise TabularFileReadError(
            "Excel workbook に読み取り可能な Sheet がありません。"
            "Sheet を追加して再試行してください。"
        )
    return sheets


def select_workbook_sheet(
    sheets: list[TabularSheet],
    requested_name: str = "",
) -> tuple[TabularSheet, list[str]]:
    """指定 Sheet、または reader が示す active/先頭 Sheet を返す。"""

    warnings: list[str] = []
    if requested_name:
        selected = next((sheet for sheet in sheets if sheet.title == requested_name), None)
        if selected is not None:
            return selected, warnings
        warnings.append(
            f"{requested_name}: Sheet が見つからないため active または先頭 Sheet を使用しました。"
        )
    selected = next((sheet for sheet in sheets if sheet.active), sheets[0])
    return selected, warnings


def normalize_workbook_scalar(value: Any) -> str:
    """CSV reader へ再マップするときの workbook scalar 表現を統一する。"""

    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def validate_tabular_text_signature(content: bytes) -> None:
    """Office/PDF を CSV 等の text として誤読しないための軽量 signature 検査。"""

    looks_binary = (
        content.startswith(XLS_OLE_SIGNATURE)
        or content.startswith(ZIP_SIGNATURES)
        or content.startswith(b"%PDF-")
        or b"\x00" in content[:4096]
    )
    if looks_binary:
        raise TabularFileReadError(
            "CSV/TXT の内容をテキストとして確認できません。"
            "拡張子と実際の形式を一致させ、CSV、XLSX、XLS の正しいファイルを再選択してください。"
        )
