"""Excel(.xls/.xlsx)→構造化 JSON 前処理マイクロサービスの変換実装。

engchina/No.1 系の表→JSON 相当を本プロジェクトの前処理契約(`ConvertResponse`)へ
再マップする。Excel は複数シートを持つため、シート単位で「ヘッダ列をキーにした
レコード配列」を束ねた JSON を決定論で生成し、後段 parser が表構造として安定して
扱えるようにする。

- `.xlsx` は openpyxl(read_only / data_only)で計算済み値を読む。
- `.xls` は xlrd で読む。
- 形式判定は magic bytes(ZIP=xlsx / OLE2=xls)優先、失敗時は拡張子フォールバック。

依存(openpyxl / xlrd)は本サービス image にのみ含め、他 parser / backend に非干渉。
未対応・依存欠如・解析失敗・空のときは passthrough(変換せず原本を使う)へ縮退する。
"""

from __future__ import annotations

import datetime as _dt
import json

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile

# xlsx は ZIP(PK\x03\x04)、xls は OLE2 複合ドキュメント(D0CF11E0...)。
_XLSX_MAGIC = b"PK\x03\x04"
_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def openpyxl_available() -> bool:
    try:
        import openpyxl  # noqa: F401
    except Exception:
        return False
    return True


def xlrd_available() -> bool:
    try:
        import xlrd  # noqa: F401
    except Exception:
        return False
    return True


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
) -> ConvertOutcome:
    """選択プリセットで変換する。対象外・依存欠如・失敗は passthrough へ縮退する。"""
    if preprocess_profile != "excel_to_json":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _excel_to_json(source_bytes, source_profile)


def _is_xlsx(source_bytes: bytes, source_profile: SourceProfile | None) -> bool:
    if source_bytes[:4] == _XLSX_MAGIC:
        return True
    if source_bytes[:8] == _XLS_MAGIC:
        return False
    extension = (source_profile.extension if source_profile is not None else "") or ""
    return extension.strip().lower().lstrip(".") == "xlsx"


def _excel_to_json(source_bytes: bytes, source_profile: SourceProfile | None) -> ConvertOutcome:
    if not source_bytes:
        return ConvertOutcome.passthrough(reason="excel_empty")
    if _is_xlsx(source_bytes, source_profile):
        sheets, warnings = _read_xlsx(source_bytes)
    else:
        sheets, warnings = _read_xls(source_bytes)
    if sheets is None:
        # warnings に縮退理由が入っている。
        return ConvertOutcome.passthrough(reason=warnings[0] if warnings else "excel_parse_failed")
    if not any(sheet["row_count"] for sheet in sheets):
        return ConvertOutcome.passthrough(reason="excel_no_rows")
    payload = {"sheets": sheets, "sheet_count": len(sheets)}
    derived = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return ConvertOutcome(
        converted=True,
        converter_name="excel_to_json",
        converter_version="v1",
        derived_bytes=derived,
        derived_content_type="application/json; charset=utf-8",
        warnings=tuple(warnings),
    )


def _read_xlsx(source_bytes: bytes) -> tuple[list[dict] | None, list[str]]:
    try:
        import io

        import openpyxl
    except Exception:
        return None, ["openpyxl_unavailable"]
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(source_bytes), read_only=True, data_only=True
        )
    except Exception:
        return None, ["excel_open_failed"]
    sheets: list[dict] = []
    try:
        for worksheet in workbook.worksheets:
            rows = [
                [_cell_to_str(value) for value in row]
                for row in worksheet.iter_rows(values_only=True)
            ]
            sheets.append(_sheet_payload(worksheet.title, rows))
    except Exception:
        return None, ["excel_read_failed"]
    finally:
        workbook.close()
    return sheets, []


def _read_xls(source_bytes: bytes) -> tuple[list[dict] | None, list[str]]:
    try:
        import xlrd
    except Exception:
        return None, ["xlrd_unavailable"]
    try:
        workbook = xlrd.open_workbook(file_contents=source_bytes)
    except Exception:
        return None, ["excel_open_failed"]
    sheets: list[dict] = []
    try:
        for worksheet in workbook.sheets():
            rows = [
                [_cell_to_str(worksheet.cell_value(r, c)) for c in range(worksheet.ncols)]
                for r in range(worksheet.nrows)
            ]
            sheets.append(_sheet_payload(worksheet.name, rows))
    except Exception:
        return None, ["excel_read_failed"]
    return sheets, []


def _sheet_payload(name: str, rows: list[list[str]]) -> dict:
    """1 シートの行列を {name, columns, row_count, rows[]} へ整形する。"""
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return {"name": name, "columns": [], "row_count": 0, "rows": []}
    columns = _resolve_columns([cell.strip() for cell in rows[0]])
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        record = {
            column: (row[index] if index < len(row) else "")
            for index, column in enumerate(columns)
        }
        records.append(record)
    return {"name": name, "columns": columns, "row_count": len(records), "rows": records}


def _cell_to_str(value: object) -> str:
    """セル値を決定論で文字列化する(None→空、整数 float は小数点を落とす)。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    return str(value)


def _resolve_columns(header: list[str]) -> list[str]:
    """空・重複のないユニークな列名を決定論で確定する。"""
    columns: list[str] = []
    seen: dict[str, int] = {}
    for index, name in enumerate(header):
        base = name or f"col_{index + 1}"
        if base in seen:
            seen[base] += 1
            base = f"{base}_{seen[base]}"
        else:
            seen[base] = 0
        columns.append(base)
    return columns
