"""送達した原文範囲の画像用途を、本文を変えず表示へ投影する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from rag_engine.generation.citation_unit_ids import BoundUnitRange
from rag_engine.generation.image_text_sources import (
    IMAGE_CITATION_UNIT_CONTRACT,
    IMAGE_READING_UNIT_CONTRACT,
    image_purpose_sidecar,
    span_source_context,
)

ImagePurpose = Literal["record", "example", "unknown"]


@dataclass(frozen=True)
class ImageSourceDisplayPart:
    text: str
    purposes: tuple[ImagePurpose, ...] = ()


def image_source_display_parts(
    binding: BoundUnitRange | None, text: str, span: Mapping[str, Any] | None
) -> tuple[ImageSourceDisplayPart, ...]:
    """空の用途集合は注記対象でない原文であり、第四の画像用途ではない。"""
    plain = (ImageSourceDisplayPart(text),)
    if binding is None or span is None:
        return plain
    image_contract = binding.snapshot.contract in {IMAGE_CITATION_UNIT_CONTRACT, IMAGE_READING_UNIT_CONTRACT}
    if not image_contract and "image_text_source_context" not in span:
        return plain
    original = binding.snapshot.span()
    if any(original.get(key) != span.get(key) for key in ("text", "image_text_source_context", "source_id", "label")):
        return (ImageSourceDisplayPart(text, ("unknown",)),) if image_contract else plain
    context = span_source_context(original)
    if context is None:
        return (ImageSourceDisplayPart(text, ("unknown",)),) if image_contract else plain
    try:
        sidecar = image_purpose_sidecar(binding.snapshot)
    except ValueError:
        # 古い契約や境界不一致からrecord/exampleを復元しない。
        sidecar = []
    by_unit: dict[str, ImagePurpose] = {uid: row["purpose"] for row in sidecar for uid in row["unit_ids"]}
    bounds = binding.snapshot.units
    start, stop = bounds[binding.first][0], bounds[binding.last][1]
    parts: list[ImageSourceDisplayPart] = []
    cursor = start
    for index in range(binding.first, binding.last + 1):
        first, last = bounds[index]
        uid = binding.snapshot.unit_ids[index]
        purpose = by_unit.get(uid)
        if purpose is None and any(row["start"] < last and first < row["end"] for row in context["ranges"]):
            purpose = "unknown"
        # 単位間のseparatorも逐字保持する。注記のために原文を再探索しない。
        value = str(original["text"])[cursor:last]
        flags = (purpose,) if purpose is not None else ()
        if parts and parts[-1].purposes == flags:
            parts[-1] = ImageSourceDisplayPart(parts[-1].text + value, flags)
        else:
            parts.append(ImageSourceDisplayPart(value, flags))
        cursor = last
    assert cursor == stop and "".join(part.text for part in parts) == binding.text
    if text != binding.text:
        # 引用の括弧・空白等を再探索せず、選択済み範囲の用途を集約して添える。
        purposes = tuple(dict.fromkeys(purpose for part in parts for purpose in part.purposes))
        return (ImageSourceDisplayPart(text, purposes),)
    return tuple(parts)
