"""Docling の結合下線を、同じ位置の PDF 原文で確認できる場合だけ復元する。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

_COMBINING_LOW_LINE = "\N{COMBINING LOW LINE}"


def _restore_underscores(text: str, original: str) -> str:
    """空白以外が同じで、差分が結合下線→ASCII 下線だけのときに原文へ戻す。"""
    parsed = "".join(text.split())
    native = "".join(original.split())
    if len(parsed) != len(native) or not native:
        return text
    if any(
        a != b and (a, b) != (_COMBINING_LOW_LINE, "_") for a, b in zip(parsed, native, strict=True)
    ):
        return text
    source = iter(native)
    return "".join(char if char.isspace() else next(source) for char in text)


def correct_docling_pdf_text(records: list[dict[str, Any]], source_bytes: bytes) -> set[str]:
    """位置と全文を照合し、訂正した record の id を返す。推測による文字置換はしない。

    表の HTML・画像説明はこの照合の対象外。画像だけの PDF、回転ページ、無効な位置、
    原文とそれ以外の文字も異なる record は保持する。PDF を開くのは疑わしい文字があるときだけ。
    """
    candidates = [
        record
        for record in records
        if record.get("engine") == "docling"
        and _COMBINING_LOW_LINE in str(record.get("text") or "")
        and record.get("category") not in {"Table", "Picture"}
    ]
    if not candidates or not source_bytes.startswith(b"%PDF"):
        return set()
    try:
        import fitz as pymupdf  # type: ignore[import-untyped]

        document = pymupdf.open(stream=source_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 - 読めない原本から訂正しない。
        return set()
    corrected: set[str] = set()
    pages: dict[int, Mapping[str, Any]] = {}
    try:
        for record in candidates:
            try:
                page_number = int(record.get("page") or 0)
                if not 1 <= page_number <= len(document):
                    continue
                page = document[page_number - 1]
                if page.rotation:
                    continue
                box = record.get("bbox")
                if record.get("coord_system") != "image_top_left" or not isinstance(box, list):
                    continue
                if len(box) != 4:
                    continue
                sx = page.rect.width / float(record.get("page_width") or 0)
                sy = page.rect.height / float(record.get("page_height") or 0)
                left, top, right, bottom = (
                    float(value) * scale for value, scale in zip(box, (sx, sy, sx, sy), strict=True)
                )
                if not all(math.isfinite(value) for value in (left, top, right, bottom)):
                    continue
                if left >= right or top >= bottom:
                    continue
                if page_number not in pages:
                    # 画像データを含めず、直前の 1 ページだけ保持する。
                    pages.clear()
                    pages[page_number] = page.get_text(
                        "rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
                    )
                original = _text_in_box(pages[page_number], (left, top, right, bottom))
            except Exception:  # noqa: BLE001 - 位置や文字層を読めない record は変更しない。
                original = ""
            previous = str(record.get("text") or "")
            restored = _restore_underscores(previous, original)
            if restored != previous:
                record["text"] = restored
                record["raw"] = {
                    **(record["raw"] if isinstance(record.get("raw"), Mapping) else {}),
                    "pdf_text_correction": "underscore_from_text_layer",
                }
                corrected.add(str(record.get("id") or ""))
    finally:
        document.close()
    return corrected


def _text_in_box(payload: Mapping[str, Any], box: tuple[float, ...]) -> str:
    """文字の中心が record の位置に入る文字だけを、PDF の行順に取り出す。"""
    left, top, right, bottom = box
    parts: list[str] = []
    for block in payload.get("blocks", []):
        for line in block.get("lines", []):
            direction = line.get("dir", (1.0, 0.0))
            if abs(direction[1]) > 0.1 or direction[0] <= 0:
                continue
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    x0, y0, x1, y1 = char["bbox"]
                    if left <= (x0 + x1) / 2 <= right and top <= (y0 + y1) / 2 <= bottom:
                        parts.append(char["c"])
            parts.append("\n")
    return "".join(parts)
