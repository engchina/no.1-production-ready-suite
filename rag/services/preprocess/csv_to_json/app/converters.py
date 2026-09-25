"""CSV→構造化 JSON 前処理マイクロサービスの変換実装。

engchina/No.1 系の csv2json 相当を本プロジェクトの前処理契約(`ConvertResponse`)へ
再マップする。CSV を決定論で「ヘッダ列をキーにしたレコード配列の JSON」へ変換し、
後段 parser が表構造として安定して扱えるようにする。純 Python(`csv` + `json`)のみで
完結し、重い変換依存(LibreOffice / PyMuPDF)を持たない軽量サービス。

未対応・空・復号失敗・行数 0 のときは passthrough(変換せず原本を使う)へ縮退する。
"""

from __future__ import annotations

import csv
import io
import json

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile

# CSV として扱える上限デリミタ候補(Sniffer 失敗時の決定論フォールバックはカンマ)。
_DELIMITER_CANDIDATES = ",\t;|"


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
) -> ConvertOutcome:
    """選択プリセットで変換する。csv_to_json 以外・失敗・空は passthrough へ縮退する。"""
    if preprocess_profile != "csv_to_json":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _csv_to_json(source_bytes)


def _decode_csv(source_bytes: bytes) -> tuple[str | None, list[str]]:
    """CSV バイト列を文字列へ復号する(charset-normalizer フォールバック付き)。"""
    warnings: list[str] = []
    try:
        return source_bytes.decode("utf-8-sig"), warnings
    except UnicodeDecodeError:
        try:
            from charset_normalizer import from_bytes

            best = from_bytes(source_bytes).best()
            if best is not None:
                warnings.append("csv_charset_fallback")
                return str(best), warnings
        except Exception:  # noqa: BLE001 - 判定失敗は utf-8(置換)へ縮退する
            pass
        warnings.append("csv_charset_fallback")
        return source_bytes.decode("utf-8", errors="replace"), warnings


def _sniff_delimiter(sample: str) -> str:
    """先頭サンプルからデリミタを推定する(失敗時はカンマで決定論固定)。"""
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=_DELIMITER_CANDIDATES)
        return str(dialect.delimiter)
    except csv.Error:
        return ","


def _csv_to_json(source_bytes: bytes) -> ConvertOutcome:
    if not source_bytes:
        return ConvertOutcome.passthrough(reason="csv_empty")
    text, warnings = _decode_csv(source_bytes)
    if text is None or not text.strip():
        return ConvertOutcome.passthrough(reason="csv_empty")
    delimiter = _sniff_delimiter(text[:8192])
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error:
        return ConvertOutcome.passthrough(reason="csv_parse_failed")
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ConvertOutcome.passthrough(reason="csv_no_rows")

    header = [cell.strip() for cell in rows[0]]
    # ヘッダが空/重複のときは決定論で列名を補完(col_1, col_2, ...)。
    columns = _resolve_columns(header)
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        record: dict[str, str] = {}
        for index, column in enumerate(columns):
            record[column] = row[index] if index < len(row) else ""
        records.append(record)

    payload = {"columns": columns, "row_count": len(records), "rows": records}
    derived = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return ConvertOutcome(
        converted=True,
        converter_name="csv_to_json",
        converter_version="v1",
        derived_bytes=derived,
        derived_content_type="application/json; charset=utf-8",
        warnings=tuple(warnings),
    )


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
