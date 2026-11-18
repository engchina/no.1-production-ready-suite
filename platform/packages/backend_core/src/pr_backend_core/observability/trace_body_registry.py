"""実際に保存された所属だけから、全文の非同期書込を認める。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..chat_progress import ChatProgressStepDetails
from .trace_bodies import (
    BodyDeleted,
    BodyFailure,
    BodyReference,
    PreparedBody,
    lock_body_manifest,
    mark_body_incomplete,
    write_body,
)


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


def mark_call_body_failures(
    span_details: Mapping[str, Any], faults: Mapping[str, BodyFailure]
) -> dict[str, dict[str, Any]]:
    """実際の登録障害を保存snapshotへ反映する。入力値や非公開欄は変更しない。"""
    result: dict[str, dict[str, Any]] = {}
    for span_id, value in span_details.items():
        details = (
            value
            if isinstance(value, ChatProgressStepDetails)
            else ChatProgressStepDetails.model_validate(value)
        )
        call = details.call
        if call is not None:
            changes = {}
            for name in ("input", "output", "schema_"):
                content = getattr(call, name)
                reference = content.body_reference
                reason = faults.get(reference.body_id) if reference is not None else None
                if reason is not None and content.status not in {"withheld", "not_recorded"}:
                    changes[name] = content.model_copy(
                        update={"status": "incomplete", "failure_code": reason}
                    )
            if changes:
                details = details.model_copy(update={"call": call.model_copy(update=changes)})
        result[span_id] = details.model_dump(mode="json", exclude_none=True)
    return result


def _registered(cursor: Any, reference: BodyReference) -> bool:
    """正本保存取引がattachしたmanifestだけをロックする。writerは箱も所属も作らない。

    登録の取引は manifest の作成と所属の attach を commit 前に writer へ渡す。commit 前の行は
    別の接続から見えないので、SELECT だけでは「まだ commit していない」と「rollback した」を
    区別できない（#5394）。登録と同じ MERGE で主キーを競合させ、Oracle にその取引の終了を
    待たせる: commit 済みなら ORA-00001 を経て行をロックし、rollback 済みなら作った行を
    savepoint まで戻して書かない。
    """
    cursor.execute("SAVEPOINT trace_body_writer")
    try:
        lock_body_manifest(cursor, reference)
    except BodyDeleted:
        return False
    cursor.execute(
        "SELECT COUNT(*) FROM PLATFORM_TRACE_BODY_OWNERS WHERE BODY_ID = :body_id",
        {"body_id": reference.body_id},
    )
    if cursor.fetchone()[0]:
        return True
    cursor.execute("ROLLBACK TO SAVEPOINT trace_body_writer")
    return False


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
