"""要求の回答漏れと材料不足を分け、既存の補救経路を選ぶ。"""

from collections.abc import Collection, Mapping, Sequence
from typing import Any


def request_recovery_plan(
    reviews: Sequence[Mapping[str, Any]],
    gap_ids: Sequence[str],
    evidence_ids: set[str],
    *,
    blocked_request_ids: Sequence[str] = (),
    expected_request_ids: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    """実行済み監査の材料判定、または生成の明示不足だけから補救を選ぶ。"""
    by_id = {
        str(row["request_id"]): row
        for row in reviews
        if (row.get("reviewed") and row.get("status") in {"missing", "partial"}) or row.get("status") == "unknown"
    }
    result = []
    for request_id in dict.fromkeys([*by_id, *gap_ids, *blocked_request_ids]):
        row = by_id.get(request_id, {})
        status = row.get("evidence_status", "unknown")
        refs = list(dict.fromkeys(row.get("evidence_ids") or ()))
        invalid_id = expected_request_ids is not None and request_id not in expected_request_ids
        if invalid_id:
            cause, action = "invalid_request_id", "retain"
        elif request_id in blocked_request_ids or row.get("status") == "unknown":
            cause, action = "review_incomplete", "retain"
        elif status == "available":
            valid = bool(refs) and set(refs) <= evidence_ids
            cause, action = ("answer_omission", "correction") if valid else ("unverified_evidence", "retain")
        elif status == "missing" or (request_id in gap_ids and not row):
            cause, action = "evidence_missing", "retrieval"
        else:
            cause, action = "unverified_evidence", "retain"
        result.append(
            {
                "request_id": request_id,
                "status": "unknown" if invalid_id or request_id in blocked_request_ids else row.get("status", "missing"),
                "cause": cause,
                "action": action,
                "evidence_ids": refs if action == "correction" else [],
            }
        )
    return result
