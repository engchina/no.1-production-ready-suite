"""図形・テキストボックスの文字の読み取り（#2526）。

openpyxl はシートの描画（drawing）の図形の文字を読まない。帳票は説明・押印欄・注意事項を
テキストボックスに置くことが多いので、`.xlsx` の zip から次の順にたどって直接読む。

- `xl/workbook.xml` のシート名 → `xl/_rels/workbook.xml.rels` のシートの XML
  → `xl/worksheets/_rels/sheetN.xml.rels` の drawing → `xl/drawings/drawingN.xml`。
- アンカー（`twoCellAnchor` の from / to、`oneCellAnchor` の from）の行・列を、文字を置くセルの
  範囲にする。to の位置がセルの端（オフセット 0）なら、そのセルは範囲に入れない。
  `oneCellAnchor` は大きさをセルに換算せず、from の 1 つのセルにする。
- 文字は図形（グループの中の図形も）の `txBody` の段落を改行でつなぐ。`mc:AlternateContent` は
  `mc:Choice` だけを読む（`mc:Fallback` は同じ図形の代わりの表現）。
- `absoluteAnchor`（セルに結び付かない位置）は置き場所が決まらないので、数を診断に残す。

`.xls` の図形は読まない。xlrd が図形を読まないので、シートの記録（BIFF）のテキストオブジェクト
（TXO）の数からセルのメモ（NOTE）の数を引き、図形の文字があるときに診断に残す。
"""

from __future__ import annotations

import io
import posixpath
import struct
import zipfile
from dataclasses import dataclass
from typing import Any

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_DRAWING_REL_TYPE = "/drawing"
# 読む XML の大きさの上限（展開後。極端に大きい drawing で時間・メモリを使わない）。
_MAX_XML_BYTES = 16 * 1024 * 1024
# .xls の BIFF の記録の種類（BOF・EOF・BOUNDSHEET・TXO・NOTE）。
_XLS_BOF = 0x0809
_XLS_EOF = 0x000A
_XLS_BOUNDSHEET = 0x0085
_XLS_TXO = 0x01B6
_XLS_NOTE = 0x001C


@dataclass(frozen=True)
class ShapeText:
    """図形の文字と、置くセルの範囲（1 始まりの上・左・下・右）。"""

    top: int
    left: int
    bottom: int
    right: int
    text: str


@dataclass
class SheetShapes:
    """1 シートの図形の文字と、置き場所の決まらない図形の数。"""

    shapes: list[ShapeText]
    absolute: int = 0


def xlsx_sheet_shapes(source_bytes: bytes) -> dict[str, SheetShapes]:
    """シート名 → 図形の文字（読めないときは空。セルの読み取りは止めない）。"""
    try:
        package = zipfile.ZipFile(io.BytesIO(source_bytes))
    except (zipfile.BadZipFile, OSError):
        return {}
    with package:
        try:
            return _sheet_shapes(package)
        except (KeyError, ValueError, SyntaxError, OSError, zipfile.BadZipFile):
            return {}


def _read_xml(package: zipfile.ZipFile, name: str) -> Any | None:
    try:
        info = package.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_XML_BYTES:
        return None
    # openpyxl の XML の読み方（defusedxml があれば使う）にそろえる。使う側が openpyxl を入れる。
    from openpyxl.xml.functions import fromstring

    return fromstring(package.read(info))


def _relationships(package: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """部品の関係（Id → 種類・部品の名前）。"""
    folder, name = posixpath.split(part)
    root = _read_xml(package, posixpath.join(folder, "_rels", f"{name}.rels"))
    if root is None:
        return {}
    result: dict[str, tuple[str, str]] = {}
    for item in root.iter(f"{_PKG_REL}Relationship"):
        if item.get("TargetMode") == "External":
            continue
        target = item.get("Target") or ""
        path = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
        result[item.get("Id") or ""] = (item.get("Type") or "", posixpath.normpath(path))
    return result


def _sheet_shapes(package: zipfile.ZipFile) -> dict[str, SheetShapes]:
    workbook = _read_xml(package, "xl/workbook.xml")
    if workbook is None:
        return {}
    workbook_rels = _relationships(package, "xl/workbook.xml")
    result: dict[str, SheetShapes] = {}
    for sheet in workbook.iter(f"{_MAIN}sheet"):
        name = sheet.get("name")
        part = workbook_rels.get(sheet.get(f"{_REL}id") or "")
        if not name or part is None:
            continue
        shapes = SheetShapes(shapes=[])
        for kind, drawing in _relationships(package, part[1]).values():
            if not kind.endswith(_DRAWING_REL_TYPE):
                continue
            root = _read_xml(package, drawing)
            if root is not None:
                _read_drawing(root, shapes)
        if shapes.shapes or shapes.absolute:
            result[name] = shapes
    return result


def _read_drawing(root: Any, shapes: SheetShapes) -> None:
    for anchor in root:
        tag = anchor.tag
        if tag not in {f"{_XDR}twoCellAnchor", f"{_XDR}oneCellAnchor", f"{_XDR}absoluteAnchor"}:
            continue
        text = "\n".join(_texts(anchor)).strip()
        if not text:
            continue
        if tag == f"{_XDR}absoluteAnchor":
            shapes.absolute += 1
            continue
        start = _marker(anchor.find(f"{_XDR}from"))
        if start is None:
            continue
        top, left = start[0] + 1, start[1] + 1
        bottom, right = top, left
        end = _marker(anchor.find(f"{_XDR}to")) if tag == f"{_XDR}twoCellAnchor" else None
        if end is not None:
            # to のオフセットが 0 なら、図形はそのセルの上端・左端で終わる（そのセルは入れない）。
            bottom = max(top, end[0] + 1 if end[2] > 0 else end[0])
            right = max(left, end[1] + 1 if end[3] > 0 else end[1])
        shapes.shapes.append(ShapeText(top, left, bottom, right, text))


def _marker(element: Any | None) -> tuple[int, int, int, int] | None:
    """アンカーの位置（0 始まりの行・列と、行・列の中のオフセット）。"""
    if element is None:
        return None
    try:
        return (
            int(element.findtext(f"{_XDR}row") or 0),
            int(element.findtext(f"{_XDR}col") or 0),
            int(element.findtext(f"{_XDR}rowOff") or 0),
            int(element.findtext(f"{_XDR}colOff") or 0),
        )
    except ValueError:
        return None


def _texts(element: Any) -> list[str]:
    """図形（グループの中も）の文字の段落（``mc:Fallback`` は読まない）。"""
    if element.tag == f"{_MC}Fallback":
        return []
    if element.tag == f"{_XDR}txBody":
        return [text for paragraph in element.iter(f"{_A}p") if (text := _paragraph(paragraph))]
    if element.tag == f"{_MC}AlternateContent":
        choice = element.find(f"{_MC}Choice")
        return _texts(choice) if choice is not None else []
    return [text for child in element for text in _texts(child)]


def _paragraph(paragraph: Any) -> str:
    parts: list[str] = []
    for item in paragraph.iter():
        if item.tag == f"{_A}t" and item.text:
            parts.append(item.text)
        elif item.tag == f"{_A}br":
            parts.append("\n")
    return "".join(parts).strip()


def xls_shape_text_counts(source_bytes: bytes) -> list[int] | None:
    """`.xls` のシートの順の、図形の文字（セルのメモを除くテキストオブジェクト）の数。

    読めないとき（暗号化・壊れた記録・BIFF 8 でない）は None。
    """
    try:
        from xlrd import compdoc

        document = compdoc.CompDoc(source_bytes)
        stream = None
        for name in ("Workbook", "Book"):
            stream = document.get_named_stream(name)
            if stream:
                break
        if not stream:
            return None
        # xlrd と同じく、ワークシート（シートの種類 0）だけを順に数える。
        positions = [
            struct.unpack("<I", data[:4])[0]
            for kind, data in _records(stream, 0)
            if kind == _XLS_BOUNDSHEET and len(data) >= 6 and data[5] == 0
        ]
        return [_sheet_shape_count(stream, position) for position in positions]
    except Exception:
        return None


def _records(stream: bytes, position: int) -> Any:
    """BIFF の記録（種類・中身）を ``position`` から EOF まで（埋め込みの BOF の中も）。"""
    depth = 0
    while position + 4 <= len(stream):
        kind, length = struct.unpack("<HH", stream[position : position + 4])
        data = stream[position + 4 : position + 4 + length]
        position += 4 + length
        if kind == _XLS_BOF:
            depth += 1
        yield kind, data
        if kind == _XLS_EOF:
            depth -= 1
            if depth <= 0:
                return


def _sheet_shape_count(stream: bytes, position: int) -> int:
    texts = notes = 0
    for kind, _ in _records(stream, position):
        if kind == _XLS_TXO:
            texts += 1
        elif kind == _XLS_NOTE:
            notes += 1
    return max(texts - notes, 0)
