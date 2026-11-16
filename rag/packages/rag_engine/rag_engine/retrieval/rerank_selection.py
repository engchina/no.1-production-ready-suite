"""採点後の候補を件数で選ぶ。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .attachment_evidence import is_attachment_evidence


def select_reranked_candidates[T](records: Sequence[T], limit: int) -> tuple[list[T], dict[str, Any]]:
    """候補の順のまま、普通候補を上限の件数まで選ぶ。明示添付は件数に数えず残す。

    検索拡張の根拠（参照先・実体・関係情報）は rerank の候補に入らず、最後の根拠の並びへ合わせる（#5162）。
    """
    if limit <= 0:
        return list(records), {"limit": 0, "applied": False}
    chosen: set[int] = set()
    ordinary_count = protected_count = skipped = 0
    for index, record in enumerate(records):
        # 明示添付は普通候補の起点数と別枠。
        if is_attachment_evidence(record):
            chosen.add(index)
            protected_count += 1
        elif ordinary_count < limit:
            chosen.add(index)
            ordinary_count += 1
        else:
            skipped += 1
    selected = [record for index, record in enumerate(records) if index in chosen]
    return selected, {
        "limit": limit,
        "applied": True,
        "input_count": len(records),
        "selected_count": len(selected),
        "ordinary_selected_count": ordinary_count,
        "attachment_related_count": protected_count,
        "excluded_count": len(records) - len(selected),
        "groups_over_budget": skipped,
        "stop_reason": "selection_limit" if skipped else "all_candidates_selected",
    }
