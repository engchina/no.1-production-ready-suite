"""推導の監査の合成応答。独立の監査は判定（ApplicationVerdictAudit）と理由（AuditReason）の 2 段。

テストは判定を ``{"index", "valid"}`` の行で書き、要求された schema の形にして返す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from rag_engine.generation.application_reason import AuditReason
from rag_engine.models.llm import ApplicationAudit, ApplicationVerdictAudit

SYNTHETIC_REASON = "合成の判定の理由です。"
APPLICATION_SCHEMAS = (ApplicationAudit, ApplicationVerdictAudit, AuditReason)


def audit_reply(
    schema: type[Any],
    rows: Iterable[Mapping[str, Any]] = (),
    *,
    summary_supported: bool = True,
    reason: str = SYNTHETIC_REASON,
) -> Any:
    """当てはめの監査・独立の監査の判定・理由のどの schema にも、同じ判定の合成応答を返す。"""
    if schema is AuditReason:
        return AuditReason(reason=reason)
    if schema is ApplicationVerdictAudit:
        reviews = [
            {"index": row["index"], "valid": row["valid"], "basis_evidence_ids": list(row.get("basis_evidence_ids", ()))} for row in rows
        ]
    elif schema is ApplicationAudit:
        reviews = [{"index": row["index"], "valid": row["valid"], "reason": row.get("reason", reason)} for row in rows]
    else:
        raise AssertionError(f"推導の監査ではない schema: {schema}")
    return schema.model_validate({"application_reviews": reviews, "summary_supported": summary_supported})
