"""生成が宣言した要求の要点と、公開できる出力との構造的な対応を調べる。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.models.llm import GroundedDraft


def check_answer_parts(
    draft: GroundedDraft, items: Mapping[int, Any], applications: Sequence[Any], request_ids: set[str]
) -> dict[str, Any]:
    """参照の実在・役割・要求IDを検証する。自然言語の意味支持は判定しない。"""
    if not draft.answer_parts:
        return {"status": "unrecorded", "parts": [], "unrecorded_request_ids": sorted(request_ids)}
    counts = Counter(part.id for part in draft.answer_parts)
    rows = []
    for part in draft.answer_parts:
        direct = [items.get(index) for index in part.item_indexes]
        derived = [applications[index] if 0 <= index < len(applications) else None for index in part.application_indexes]
        valid_id = part.request_id in request_ids and counts[part.id] == 1
        if part.result_kind == "derived":
            present = (
                bool(derived)
                and not direct
                and all(entry is not None and entry.published and entry.application.request_id == part.request_id for entry in derived)
            )
        else:
            present = (
                bool(direct)
                and not derived
                and all(
                    entry is not None
                    and entry.item.request_id == part.request_id
                    and ((entry.item.kind == "gap") if part.result_kind == "gap" else (entry.confirmed and entry.item.kind != "gap"))
                    for entry in direct
                )
            )
        rows.append({**part.model_dump(), "status": "present" if valid_id and present else "invalid" if not valid_id else "missing"})
    missing_ids = sorted(request_ids - {part.request_id for part in draft.answer_parts})
    return {
        "status": "incomplete" if missing_ids or any(row["status"] != "present" for row in rows) else "linked",
        "parts": rows,
        "unrecorded_request_ids": missing_ids,
    }
