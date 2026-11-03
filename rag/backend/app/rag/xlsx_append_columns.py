"""xlsx の 1 つのシートの行の右に列を足す（#4212）。

openpyxl で読み直して保存すると、図形・グラフ・式の計算済みの値などが失われる。元のファイルを
保つため、パッケージの中の対象のシートの XML と書式の XML（足すセルの書式の分）だけを書き換え、
ほかの部品はそのまま写す。既存のセルは書き換えない。足す列は、値のある最も右の列（結合の範囲を含む）の次から。
文字列はセルの中に持つ（`inlineStr`）ので、共有文字列の表を変えず、式としても評価されない。

業務の語を持たない汎用の部品（他の製品に広げるなら platform の `pr_backend_core` へ移す）。
"""

from __future__ import annotations

import copy
import posixpath
import shutil
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal

from lxml import etree  # type: ignore[import-untyped]
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE  # type: ignore[import-untyped]
from openpyxl.utils import (  # type: ignore[import-untyped]
    column_index_from_string,
    get_column_letter,
    range_boundaries,
)
from openpyxl.utils.cell import coordinate_from_string  # type: ignore[import-untyped]

EXCEL_MAX_COLUMN = 16384
# Excel の 1 つのセルの文字数の上限。
EXCEL_CELL_MAX_CHARS = 32767
_TRUNCATED_SUFFIX = "…（省略）"
# 展開した後のパッケージと、読み込む XML の大きさの上限（圧縮の爆弾を読まない）。
_MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
_MAX_PART_BYTES = 256 * 1024 * 1024
_HEADER_FILL_RGB = "FFDDEBF7"
_HEADER_WIDTH_MARGIN = 2
_PERCENT_FORMAT = "0.0%"
_FIRST_CUSTOM_NUMBER_FORMAT = 164
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

CellValue = str | float | None


class XlsxAppendError(ValueError):
    """列を足せないファイル（xlsx でない・シートが無い・列が足りない）。"""


@dataclass(frozen=True)
class AppendedColumn:
    """足す列の見出しと、数値の書式（`percent`）と幅。"""

    header: str
    kind: Literal["text", "percent"] = "text"
    width: int = 24


def append_columns(
    data: bytes,
    *,
    sheet_title: str,
    columns: Sequence[AppendedColumn],
    rows: Mapping[int, Sequence[CellValue]],
    header_row: int = 1,
) -> bytes:
    """`sheet_title` のシートの行（`rows` の key は行番号）の右に `columns` を足して返す。"""
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            if sum(item.file_size for item in package.infolist()) > _MAX_PACKAGE_BYTES:
                raise XlsxAppendError("展開した大きさが上限を超えています。")
            sheet_part, styles_part = _parts(package, sheet_title)
            sheet = _read_xml(package, sheet_part)
            styles = _read_xml(package, styles_part)
            style_ids = _add_styles(styles)
            _append_to_sheet(sheet, columns, rows, header_row, style_ids)
            return _write_package(
                package,
                {sheet_part: _serialize(sheet), styles_part: _serialize(styles)},
            )
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as exc:
        raise XlsxAppendError("xlsx のパッケージとして読めません。") from exc


def _local(element: Any) -> str:
    """要素の名前空間を除いた名前（コメントなどは空文字）。"""
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _namespace_prefix(element: Any) -> str:
    """要素と同じ名前空間の子を作るときの `{名前空間}`（無ければ空文字）。"""
    namespace = etree.QName(element).namespace
    return f"{{{namespace}}}" if namespace else ""


def _children(element: Any, name: str) -> list[Any]:
    return [child for child in element if _local(child) == name]


def _child(element: Any, name: str) -> Any | None:
    found = _children(element, name)
    return found[0] if found else None


def _read_xml(package: zipfile.ZipFile, part: str) -> Any:
    if package.getinfo(part).file_size > _MAX_PART_BYTES:
        raise XlsxAppendError("シートが大きすぎます。")
    # DOCTYPE の実体を解決せず、外部を読まない。
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    return etree.fromstring(package.read(part), parser)


def _serialize(root: Any) -> bytes:
    return bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True))


def _relationships(package: zipfile.ZipFile, part: str) -> list[tuple[str, str, str]]:
    """部品の関係の一覧（ID・種類・解決した部品の path）。"""
    folder, name = posixpath.split(part)
    root = _read_xml(package, posixpath.join(folder, "_rels", f"{name}.rels"))
    related = []
    for item in root:
        target = str(item.get("Target") or "")
        path = target[1:] if target.startswith("/") else posixpath.join(folder, target)
        related.append(
            (str(item.get("Id") or ""), str(item.get("Type") or ""), posixpath.normpath(path))
        )
    return related


def _parts(package: zipfile.ZipFile, sheet_title: str) -> tuple[str, str]:
    """対象のシートと書式の部品の path を、パッケージの関係から求める。"""
    workbook_part = next(
        (path for _, kind, path in _relationships(package, "") if kind.endswith("/officeDocument")),
        None,
    )
    if workbook_part is None:
        raise XlsxAppendError("ブックがありません。")
    workbook = _read_xml(package, workbook_part)
    sheets = _child(workbook, "sheets")
    relation_id = None
    for sheet in _children(sheets, "sheet") if sheets is not None else []:
        if sheet.get("name") == sheet_title:
            relation_id = next(
                (
                    value
                    for key, value in sheet.attrib.items()
                    if etree.QName(key).localname == "id"
                ),
                None,
            )
    if relation_id is None:
        raise XlsxAppendError("シートがありません。")
    related = _relationships(package, workbook_part)
    sheet_part = next((path for key, _, path in related if key == relation_id), None)
    styles_part = next((path for _, kind, path in related if kind.endswith("/styles")), None)
    if sheet_part is None or styles_part is None:
        raise XlsxAppendError("シートか書式の部品がありません。")
    return sheet_part, styles_part


def _set_count(element: Any) -> None:
    if element.get("count") is not None:
        element.set("count", str(len([child for child in element if _local(child)])))


def _add_styles(styles: Any) -> dict[str, int]:
    """足すセルの書式を増やし、ID を返す。既存の書式の ID は変えない。"""
    prefix = _namespace_prefix(styles)

    def qualified(name: str) -> str:
        return f"{prefix}{name}"

    fonts, fills, cell_xfs = (_child(styles, name) for name in ("fonts", "fills", "cellXfs"))
    if fonts is None or fills is None or cell_xfs is None:
        raise XlsxAppendError("書式の部品の形が想定と違います。")

    existing_fonts = _children(fonts, "font")
    bold = copy.deepcopy(existing_fonts[0]) if existing_fonts else etree.Element(qualified("font"))
    if _child(bold, "b") is None:
        bold.insert(0, etree.Element(qualified("b")))
    fonts.append(bold)
    _set_count(fonts)
    bold_id = len(_children(fonts, "font")) - 1

    fill = etree.SubElement(fills, qualified("fill"))
    pattern = etree.SubElement(fill, qualified("patternFill"), patternType="solid")
    etree.SubElement(pattern, qualified("fgColor"), rgb=_HEADER_FILL_RGB)
    etree.SubElement(pattern, qualified("bgColor"), indexed="64")
    _set_count(fills)
    fill_id = len(_children(fills, "fill")) - 1

    number_formats = _child(styles, "numFmts")
    if number_formats is None:
        number_formats = etree.Element(qualified("numFmts"), count="0")
        styles.insert(0, number_formats)
    used = [int(item.get("numFmtId") or 0) for item in _children(number_formats, "numFmt")]
    percent_id = max([*used, _FIRST_CUSTOM_NUMBER_FORMAT - 1]) + 1
    etree.SubElement(
        number_formats,
        qualified("numFmt"),
        numFmtId=str(percent_id),
        formatCode=_PERCENT_FORMAT,
    )
    _set_count(number_formats)

    def add_xf(number_format: int, font: int, fill_index: int) -> int:
        xf = etree.SubElement(
            cell_xfs,
            qualified("xf"),
            numFmtId=str(number_format),
            fontId=str(font),
            fillId=str(fill_index),
            borderId="0",
            xfId="0",
            applyNumberFormat="1",
            applyFont="1",
            applyFill="1",
            applyAlignment="1",
        )
        etree.SubElement(xf, qualified("alignment"), vertical="top", wrapText="1")
        _set_count(cell_xfs)
        return len(_children(cell_xfs, "xf")) - 1

    return {
        "header": add_xf(0, bold_id, fill_id),
        "text": add_xf(0, 0, 0),
        "percent": add_xf(percent_id, 0, 0),
    }


def _text_value(value: str) -> str:
    cleaned = str(ILLEGAL_CHARACTERS_RE.sub("", value))
    if len(cleaned) <= EXCEL_CELL_MAX_CHARS:
        return cleaned
    return cleaned[: EXCEL_CELL_MAX_CHARS - len(_TRUNCATED_SUFFIX)] + _TRUNCATED_SUFFIX


def _new_cell(sheet: Any, reference: str, value: CellValue, style: int) -> Any:
    prefix = _namespace_prefix(sheet)

    def qualified(name: str) -> str:
        return f"{prefix}{name}"

    cell = etree.Element(qualified("c"), r=reference, s=str(style))
    if isinstance(value, str):
        cell.set("t", "inlineStr")
        text = etree.SubElement(etree.SubElement(cell, qualified("is")), qualified("t"))
        text.text = _text_value(value)
        text.set(_XML_SPACE, "preserve")
    else:
        etree.SubElement(cell, qualified("v")).text = repr(float(value or 0))
    return cell


def _cell_column(cell: Any, previous: int) -> int:
    reference = cell.get("r")
    if not reference:
        return previous + 1
    return int(column_index_from_string(coordinate_from_string(reference)[0]))


def _last_used_column(sheet_data: Any, sheet: Any) -> int:
    """値のある最も右の列（結合の範囲を含む）。書式だけのセルは数えない。"""
    last = 0
    for row in _children(sheet_data, "row"):
        column = 0
        for cell in _children(row, "c"):
            column = _cell_column(cell, column)
            if any(_local(child) in ("v", "f", "is") for child in cell):
                last = max(last, column)
    merges = _child(sheet, "mergeCells")
    for merge in _children(merges, "mergeCell") if merges is not None else []:
        last = max(last, int(range_boundaries(str(merge.get("ref")))[2]))
    return last


def _row_element(sheet: Any, sheet_data: Any, number: int) -> Any:
    """行の要素。無ければ行番号の順の位置に作る。"""
    current = 0
    following = None
    for row in _children(sheet_data, "row"):
        current = int(row.get("r") or current + 1)
        if current == number:
            return row
        if current > number:
            following = row
            break
    created = etree.Element(f"{_namespace_prefix(sheet)}row", r=str(number))
    if following is not None:
        following.addprevious(created)
    else:
        sheet_data.append(created)
    return created


def _insert_cell(row: Any, new_cell: Any, column: int) -> None:
    """列の順の位置に入れる。同じ列に値の無いセルがあれば置き換える（書式だけのセル）。"""
    current = 0
    last = None
    for cell in _children(row, "c"):
        current = _cell_column(cell, current)
        if current == column:
            row.replace(cell, new_cell)
            return
        if current > column:
            cell.addprevious(new_cell)
            return
        last = cell
    if last is not None:
        last.addnext(new_cell)
    else:
        row.insert(0, new_cell)


def _set_column_widths(
    sheet: Any, sheet_data: Any, start: int, columns: Sequence[AppendedColumn]
) -> None:
    """列の幅を足す。範囲が重なる既存の定義は分ける（重なると Excel が修復を求める）。"""
    tag = _namespace_prefix(sheet)
    definitions = _child(sheet, "cols")
    if definitions is None:
        definitions = etree.Element(f"{tag}cols")
        sheet_data.addprevious(definitions)
    end = start + len(columns) - 1
    for existing in _children(definitions, "col"):
        low, high = int(existing.get("min") or 0), int(existing.get("max") or 0)
        if high < start or low > end:
            continue
        if high > end:
            tail = copy.deepcopy(existing)
            tail.set("min", str(end + 1))
            existing.addnext(tail)
        if low < start:
            existing.set("max", str(start - 1))
        else:
            definitions.remove(existing)
    following = next(
        (item for item in _children(definitions, "col") if int(item.get("min") or 0) > end), None
    )
    for offset, column in enumerate(columns):
        position = str(start + offset)
        added = etree.Element(
            f"{tag}col",
            min=position,
            max=position,
            width=str(_column_width(column)),
            customWidth="1",
        )
        if following is not None:
            following.addprevious(added)
        else:
            definitions.append(added)


def _column_width(column: AppendedColumn) -> int:
    """見出しが切れない幅（行の高さは元のままなので、見出しは折り返しに頼らない）。"""
    header = sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in column.header)
    return max(column.width, header + _HEADER_WIDTH_MARGIN)


def _update_dimension(sheet: Any, last_column: int, last_row: int) -> None:
    dimension = _child(sheet, "dimension")
    if dimension is None or not dimension.get("ref"):
        return
    low_col, low_row, high_col, high_row = range_boundaries(str(dimension.get("ref")))
    dimension.set(
        "ref",
        f"{get_column_letter(low_col or 1)}{low_row or 1}:"
        f"{get_column_letter(max(high_col or 1, last_column))}{max(high_row or 1, last_row)}",
    )


def _append_to_sheet(
    sheet: Any,
    columns: Sequence[AppendedColumn],
    rows: Mapping[int, Sequence[CellValue]],
    header_row: int,
    style_ids: Mapping[str, int],
) -> None:
    sheet_data = _child(sheet, "sheetData")
    if sheet_data is None:
        raise XlsxAppendError("シートの本体がありません。")
    start = _last_used_column(sheet_data, sheet) + 1
    if start + len(columns) - 1 > EXCEL_MAX_COLUMN:
        raise XlsxAppendError("列を足す余地がありません。")

    def put(number: int, offset: int, value: CellValue, style: int) -> None:
        row = _row_element(sheet, sheet_data, number)
        # 使う列の範囲の目安。古い値が残らないよう除く（無くても読める）。
        row.attrib.pop("spans", None)
        reference = f"{get_column_letter(start + offset)}{number}"
        _insert_cell(row, _new_cell(sheet, reference, value, style), start + offset)

    for offset, column in enumerate(columns):
        put(header_row, offset, column.header, style_ids["header"])
    for number, values in rows.items():
        for offset, (column, value) in enumerate(zip(columns, values, strict=True)):
            if value is None:
                continue
            put(number, offset, value, style_ids["percent" if column.kind == "percent" else "text"])
    _set_column_widths(sheet, sheet_data, start, columns)
    _update_dimension(sheet, start + len(columns) - 1, max([header_row, *rows]))


def _write_package(package: zipfile.ZipFile, replaced: Mapping[str, bytes]) -> bytes:
    """部品の順を保って、書き換えた部品だけを差し替える。"""
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for item in package.infolist():
            copied = zipfile.ZipInfo(item.filename, item.date_time)
            copied.compress_type = zipfile.ZIP_DEFLATED
            copied.external_attr = item.external_attr
            payload = replaced.get(item.filename)
            if payload is not None:
                target.writestr(copied, payload)
                continue
            with package.open(item) as source, target.open(copied, "w") as sink:
                shutil.copyfileobj(source, sink)
    return output.getvalue()
