"""実際に保存された所属だけから、全文の非同期書込を認める。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..chat_progress import ChatProgressStepDetails
from .trace_bodies import BodyFailure, BodyReference, PreparedBody, mark_body_incomplete, write_body


def call_body_references(span_details: Mapping[str, Any]) -> tuple[BodyReference, ...]:
    """span詳細の正式なcall欄だけを読む。モデルのJSON本文や任意の入れ子は探索しない。"""
    references: dict[str, BodyReference] = {}
    for value in span_details.values():
        details = (
            value
            if isinstance(value, ChatProgressStepDetails)
            else ChatProgressStepDetails.model_validate(value)
        )
        if details.call is None:
            continue
        for content in (details.call.input, details.call.output, details.call.schema_):
            reference = content.body_reference
            if reference is None or content.status in {"withheld", "not_recorded"}:
                continue
            before = references.get(reference.body_id)
            if before is not None and before != reference:
                raise ValueError("conflicting trace body references")
            references[reference.body_id] = reference
    return tuple(sorted(references.values(), key=lambda value: value.body_id))


def call_body_failures(span_details: Mapping[str, Any]) -> dict[str, BodyFailure]:
    """捕獲時点で実際に返された障害だけを引き継ぐ。pendingから障害を推定しない。"""
    faults: dict[str, BodyFailure] = {}
    for value in span_details.values():
        details = (
            value
            if isinstance(value, ChatProgressStepDetails)
            else ChatProgressStepDetails.model_validate(value)
        )
        if details.call is None:
            continue
        for content in (details.call.input, details.call.output, details.call.schema_):
            if (
                content.status == "incomplete"
                and content.body_reference is not None
                and content.failure_code is not None
            ):
                faults[content.body_reference.body_id] = content.failure_code
    return faults


def _registered(cursor: Any, reference: BodyReference) -> bool:
    # 正本保存取引がattachしたmanifestだけをロックする。writerは箱も所属も作らない。
    cursor.execute(
        """SELECT CONTENT_SHA256, CHARACTER_COUNT, BYTE_COUNT, PART_COUNT, VISIBILITY, STATUS
        FROM PLATFORM_TRACE_BODIES WHERE BODY_ID = :body_id FOR UPDATE""",
        {"body_id": reference.body_id},
    )
    row = cursor.fetchone()
    if row is None or row[5] == "deleted":
        return False
    if tuple(row[:5]) != (
        reference.sha256,
        reference.character_count,
        reference.byte_count,
        reference.part_count,
        reference.visibility,
    ):
        raise ValueError("registered trace body reference mismatch")
    cursor.execute(
        "SELECT COUNT(*) FROM PLATFORM_TRACE_BODY_OWNERS WHERE BODY_ID = :body_id",
        {"body_id": reference.body_id},
    )
    return bool(cursor.fetchone()[0])


def write_registered_body(cursor: Any, body: PreparedBody, *, clob_type: Any) -> bool:
    """commit済み所属がなければ書かない。保存rollback・最終削除後に復活させない。"""
    if not _registered(cursor, body.reference):
        return False
    write_body(cursor, body, clob_type=clob_type)
    return True


def fail_registered_body(cursor: Any, reference: BodyReference, reason: BodyFailure) -> bool:
    """障害の遅い通知でも所属のないmanifestを新規作成しない。"""
    if not _registered(cursor, reference):
        return False
    mark_body_incomplete(cursor, reference, reason)
    return True
