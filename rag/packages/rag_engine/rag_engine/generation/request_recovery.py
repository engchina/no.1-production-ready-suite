"""要求の回答漏れと材料不足を分け、既存の補救経路を選ぶ。"""

from collections.abc import Mapping, Sequence
from typing import Any


def request_recovery_plan(reviews: Sequence[Mapping[str, Any]], gap_ids: Sequence[str], evidence_ids: set[str]) -> list[dict[str, Any]]:
    """実行済み監査の材料判定、または生成の明示不足だけから補救を選ぶ。"""
    by_id = {str(row["request_id"]): row for row in reviews if row.get("reviewed") and row.get("status") in {"missing", "partial"}}
    result = []
    for request_id in dict.fromkeys([*by_id, *gap_ids]):
        row = by_id.get(request_id, {})
        status = row.get("evidence_status", "unknown")
        refs = list(dict.fromkeys(row.get("evidence_ids") or ()))
        if status == "available":
            valid = bool(refs) and set(refs) <= evidence_ids
            cause, action = ("answer_omission", "correction") if valid else ("unverified_evidence", "retain")
        elif status == "missing" or request_id in gap_ids:
            cause, action = "evidence_missing", "retrieval"
        else:
            cause, action = "unverified_evidence", "retain"
        result.append(
            {
                "request_id": request_id,
                "status": row.get("status", "missing"),
                "cause": cause,
                "action": action,
                "evidence_ids": refs if action == "correction" else [],
            }
        )
    return result
