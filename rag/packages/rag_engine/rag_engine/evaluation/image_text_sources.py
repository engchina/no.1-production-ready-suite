"""最終検証の原文とE-IDを保ち、画像用途を局所範囲へ投影する。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from rag_engine.generation.answer_policy import DOCUMENT_IMAGE_VALUE_POLICY
from rag_engine.generation.image_text_sources import (
    SPAN_IMAGE_SOURCE_KEY,
    slice_span_image_context,
    span_source_context,
    with_image_purpose_policy,
)

_RANGE_CONTRACT = (
    "- 最終照合の image_purposes は同じ evidence_items.text の原文の文字位置 start（含む）/end（含まない）にだけ適用する。"
    "各範囲は引用単位で、範囲外の本文や他の根拠へ用途を広げない。"
)


def has_scoped_image_ranges(item: Mapping[str, Any]) -> bool:
    """同文でも捨てられない、検査済み用途の区間があるか。"""
    context = span_source_context(item)
    return context is not None and bool(context["ranges"])


def fragment_image_context(item: Mapping[str, Any], start: int, end: int) -> dict[str, Any]:
    """元本文の局所範囲へ、検査済み位置だけを写す。原文は変えない。"""
    return slice_span_image_context(item, start, end)


def model_image_item(item: Mapping[str, Any], fold: Callable[[str], str]) -> dict[str, Any]:
    """scope/hash/画像IDを外し、原文内の用途範囲だけモデルへ渡す。"""
    result = {
        k: v
        for k, v in item.items()
        if k not in {SPAN_IMAGE_SOURCE_KEY, "answer_image_text_source_map", "vision_image_purpose", "image_purposes"}
    }
    context = span_source_context(item)
    if context is not None and context["ranges"]:
        result["image_purposes"] = [
            {"start": row["start"], "end": row["end"], "purpose": row["vision_image_purpose"]} for row in context["ranges"]
        ]
        # 用途位置は元の文字列に結ぶ。幅の正規化で位置/hashを変えない。
        result["text"] = str(item.get("text") or "")
    else:
        result["text"] = fold(str(item.get("text") or ""))
    return result


def validation_image_system(system: str, evidence: Sequence[Mapping[str, Any]]) -> str:
    """用途の有効な原文だけ、共通の用途規則へ切り替える。"""
    if not any(has_scoped_image_ranges(item) for item in evidence):
        return system
    system = with_image_purpose_policy(system.replace(DOCUMENT_IMAGE_VALUE_POLICY, ""))
    before, marker, after = system.partition("\n## Step 3:")
    return before.rstrip() + "\n" + _RANGE_CONTRACT + ("\n" + marker + after if marker else "\n")
