"""抽出の修訂の版どうしの比較（#2644）。DB と入出力を持たない。

比べるのは要素の文だけ。表のセルの修正は要素の文に同期される（``_canonicalize_reviewed_extraction``）
ので、要素の文の差分に現れる。構造（bbox・順序・見出しの階層）は修正の対象にならない。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

ExtractionChangeKind = Literal["added", "removed", "changed"]


@dataclass(frozen=True)
class ExtractionElementChange:
    """版の間で文が変わった要素 1 件。"""

    element_id: str
    change: ExtractionChangeKind
    page_number: int | None
    before: str | None
    after: str | None


def _elements_by_key(extraction: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    elements = extraction.get("elements")
    if not isinstance(elements, list):
        return {}
    keyed: dict[str, Mapping[str, object]] = {}
    for position, element in enumerate(elements):
        if isinstance(element, Mapping):
            row = cast("Mapping[str, object]", element)
            keyed[str(row.get("element_id") or f"#{position}")] = row
    return keyed


def _text(element: Mapping[str, object]) -> str:
    text = element.get("text")
    return text if isinstance(text, str) else ""


def _page_number(element: Mapping[str, object]) -> int | None:
    page_number = element.get("page_number")
    return page_number if isinstance(page_number, int) else None


def extraction_element_changes(
    before: Mapping[str, object], after: Mapping[str, object]
) -> list[ExtractionElementChange]:
    """``before`` から ``after`` への要素の文の差分（``after`` の順の後に、消えた要素）。"""
    before_elements = _elements_by_key(before)
    after_elements = _elements_by_key(after)
    changes: list[ExtractionElementChange] = []
    for key, element in after_elements.items():
        previous = before_elements.get(key)
        if previous is None:
            changes.append(
                ExtractionElementChange(key, "added", _page_number(element), None, _text(element))
            )
        elif _text(previous) != _text(element):
            changes.append(
                ExtractionElementChange(
                    key, "changed", _page_number(element), _text(previous), _text(element)
                )
            )
    for key, element in before_elements.items():
        if key not in after_elements:
            changes.append(
                ExtractionElementChange(key, "removed", _page_number(element), _text(element), None)
            )
    return changes


def extraction_content_changed(before: Mapping[str, object], after: Mapping[str, object]) -> bool:
    """利用者に見える内容（要素の文か全文）が変わったか。変わらない保存は版を作らない。"""
    return before.get("raw_text") != after.get("raw_text") or bool(
        extraction_element_changes(before, after)
    )
