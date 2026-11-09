"""推導の監査の合成応答（当てはめの段の監査）。

当てはめの段の監査は application の固定キーに束縛した wire（canonical が ApplicationAudit。#4393）で、
旧部品 fixture の移行（``legacy_application_audit_result``）として返す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from rag_engine.models.llm import ApplicationAudit

SYNTHETIC_REASON = "合成の判定の理由です。"
APPLICATION_SCHEMAS = (ApplicationAudit,)


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
    """当てはめの監査の schema に、同じ判定の合成応答を返す。"""
    if not is_application_schema(schema):
        raise AssertionError(f"推導の監査ではない schema: {schema}")
    reviews = [{"index": row["index"], "valid": row["valid"], "reason": row.get("reason", reason)} for row in rows]
    if schema is not ApplicationAudit:
        from audit_fixtures import legacy_application_audit_result

        return legacy_application_audit_result(ApplicationAudit(application_reviews=reviews, summary_supported=summary_supported), schema)
    return schema.model_validate({"application_reviews": reviews, "summary_supported": summary_supported})
