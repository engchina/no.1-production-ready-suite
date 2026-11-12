"""採点後の候補を、引用の依存関係を分けずに件数で選ぶ。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .attachment_evidence import is_attachment_evidence
from .reference_follow import reference_source_uids


def select_reranked_candidates[T](records: Sequence[T], limit: int) -> tuple[list[T], dict[str, Any]]:
    """参照元 marker から組を作る。共有参照も一群とし、候補順は変更しない。"""
    if limit <= 0:
        return list(records), {"limit": 0, "applied": False}
    parents = list(range(len(records)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    positions = {str(getattr(record, "chunk_uid", "") or ""): index for index, record in enumerate(records)}
    positions.pop("", None)
    for index, record in enumerate(records):
        for source_id in reference_source_uids(record):
            source = positions.get(source_id)
            if source is not None:
                parents[root(index)] = root(source)
    groups: dict[int, list[int]] = {}
    for index in range(len(records)):
        groups.setdefault(root(index), []).append(index)
    chosen: set[int] = set()
    ordinary_count = protected_count = skipped_groups = 0
    for group in groups.values():
        # 明示添付は普通候補の起点数と別枠。添付の参照先も切り離さない。
        if any(is_attachment_evidence(records[index]) for index in group):
            chosen.update(group)
            protected_count += len(group)
        elif ordinary_count + len(group) <= limit:
            chosen.update(group)
            ordinary_count += len(group)
        else:
            skipped_groups += 1
    selected = [record for index, record in enumerate(records) if index in chosen]
    return selected, {
        "limit": limit,
        "applied": True,
        "input_count": len(records),
        "selected_count": len(selected),
        "ordinary_selected_count": ordinary_count,
        "attachment_related_count": protected_count,
        "excluded_count": len(records) - len(selected),
        "groups_over_budget": skipped_groups,
        "stop_reason": "selection_limit" if skipped_groups else "all_candidates_selected",
    }
