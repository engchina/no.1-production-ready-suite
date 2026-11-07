"""推導の監査の合成応答。独立の監査は判定（ApplicationVerdictAudit）と理由（AuditReason）の 2 段。

当てはめの段の監査は application の固定キーに束縛した wire（canonical が ApplicationAudit。#4393）で、
旧部品 fixture の移行（``legacy_application_audit_result``）として返す。

テストは判定を ``{"index", "valid"}`` の行で書き、要求された schema の形にして返す。
判定の行に ``basis_evidence_ids`` が無ければ、``prompt``（独立の監査の入力）でその application が引用した
evidence_id を根拠にする（引用元を含まない valid は未確認になるため。``basis_not_cited``）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from rag_engine.generation.application_reason import AuditReason
from rag_engine.models.llm import ApplicationAudit, ApplicationVerdictAudit

SYNTHETIC_REASON = "合成の判定の理由です。"
APPLICATION_SCHEMAS = (ApplicationAudit, ApplicationVerdictAudit, AuditReason)


def is_application_schema(schema: Any) -> bool:
    """推導の監査の schema（当てはめの段の束縛 wire を含む）か。"""
    return schema in APPLICATION_SCHEMAS or getattr(schema, "canonical_schema", None) is ApplicationAudit


def audit_reply(
    schema: type[Any],
    rows: Iterable[Mapping[str, Any]] = (),
    *,
    prompt: str | None = None,
    summary_supported: bool = True,
    reason: str = SYNTHETIC_REASON,
) -> Any:
    """当てはめの監査・独立の監査の判定・理由のどの schema にも、同じ判定の合成応答を返す。"""
    if schema is AuditReason:
        return AuditReason(reason=reason)
    if schema is ApplicationVerdictAudit:
        reviews = [
            {
                "index": row["index"],
                "valid": row["valid"],
                "basis_evidence_ids": list(row.get("basis_evidence_ids", cited_evidence_ids(prompt, row["index"]))),
            }
            for row in rows
        ]
    elif schema is ApplicationAudit or getattr(schema, "canonical_schema", None) is ApplicationAudit:
        reviews = [{"index": row["index"], "valid": row["valid"], "reason": row.get("reason", reason)} for row in rows]
        if schema is not ApplicationAudit:
            from audit_fixtures import legacy_application_audit_result

            return legacy_application_audit_result(
                ApplicationAudit(application_reviews=reviews, summary_supported=summary_supported), schema
            )
    else:
        raise AssertionError(f"推導の監査ではない schema: {schema}")
    return schema.model_validate({"application_reviews": reviews, "summary_supported": summary_supported})


def cited_evidence_ids(prompt: str | None, index: int) -> list[str]:
    """独立の監査の入力で、application が引用した evidence_id（items の citation_bindings）。入力が無ければ空。"""
    if not prompt:
        return []
    data = json.loads(prompt)
    application = next((row for row in data.get("applications") or () if row.get("index") == index), None)
    if application is None:
        return []
    return list(
        dict.fromkeys(
            str(binding["evidence_id"])
            for position in application.get("item_indexes") or ()
            for binding in data["items"][position].get("citation_bindings") or ()
            if binding.get("evidence_id")
        )
    )
