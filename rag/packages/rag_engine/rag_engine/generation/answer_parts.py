"""生成が宣言した要求の要点と、公開できる出力との構造的な対応を調べる。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.generation.citation_content import structure_only_quote
from rag_engine.models.llm import GroundedDraft


def with_summary_fallback(
    draft: GroundedDraft, items: Mapping[int, Any], applications: Sequence[Any], diagnostic: Mapping[str, Any]
) -> tuple[GroundedDraft, str | None]:
    """摘要が空なら、構造の検査を通った同じ結論を使う。表は既存の題と表を出す。"""
    if draft.summary.strip():
        return draft, None
    present = {row["id"] for row in diagnostic.get("parts", ()) if row.get("status") == "present"}
    candidates: list[tuple[int, str, str, list[int], list[int]]] = []
    for part in draft.answer_parts:
        if part.id not in present:
            continue
        if part.result_kind == "derived":
            derived = [applications[index] for index in part.application_indexes if 0 <= index < len(applications)]
            if not derived or any(not entry.published or entry.application.table is not None for entry in derived):
                continue
            text = "\n".join(entry.application.result.strip() for entry in derived)
            candidates.append((0, part.id, text, [], list(part.application_indexes)))
        elif part.result_kind == "direct":
            direct = [items[index] for index in part.item_indexes if index in items]
            if not direct or any(not entry.confirmed for entry in direct):
                continue
            if all(structure_only_quote(entry.item.text, entry.span or {}) for entry in direct):
                continue
            text = "\n".join(entry.item.text for entry in direct)
            candidates.append((1, part.id, text, list(part.item_indexes), []))
    candidates = [row for row in candidates if row[2].strip()]
    if not candidates:
        return draft, None
    _, part_id, text, item_indexes, application_indexes = min(candidates, key=lambda row: row[0])
    return draft.model_copy(
        update={"summary": text, "summary_item_indexes": item_indexes, "summary_application_indexes": application_indexes}
    ), part_id


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
            # 導出の item_indexes は、結論の隣に示す basis の原文の引用。結論の公開は導出の検証だけが決める。
            present = (
                bool(derived)
                and all(entry is not None and entry.published and entry.application.request_id == part.request_id for entry in derived)
                and all(entry is not None and entry.item.request_id == part.request_id and entry.item.kind != "gap" for entry in direct)
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
