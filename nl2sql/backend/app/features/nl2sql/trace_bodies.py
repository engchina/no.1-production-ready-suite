"""正本の保存・削除と共通の全文軌跡を同一取引で結ぶ。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pr_backend_core.observability.trace_bodies import BodyOwner, BodyPage, release_body_owners
from pr_backend_core.observability.trace_body_lifecycle import captured_sync_body_call
from pr_backend_core.observability.trace_body_persistence import TraceBodyPersistence
from pr_backend_core.observability.trace_body_reader import CallBodySlot, fetch_saved_call_body_page
from pr_backend_core.observability.trace_body_registry import mark_call_body_failures
from pr_backend_core.observability.trace_store import ConnectionFactory

from app.settings import get_settings

logger = logging.getLogger(__name__)
_PERSISTENCE: TraceBodyPersistence | None = None
_LOCK = threading.Lock()
type BodyKind = Literal["jobs", "history", "db_admin_turn"]


def start_trace_body_persistence(connection_factory: ConnectionFactory) -> None:
    import oracledb

    global _PERSISTENCE
    with _LOCK:
        if _PERSISTENCE is not None:
            return
        persistence = TraceBodyPersistence(
            connection_factory,
            clob_type=oracledb.DB_TYPE_CLOB,
            settings=get_settings(),
        )
        persistence.start()
        _PERSISTENCE = persistence


def stop_trace_body_persistence() -> None:
    global _PERSISTENCE
    with _LOCK:
        persistence, _PERSISTENCE = _PERSISTENCE, None
    if persistence is not None:
        persistence.stop()


def capture_trace_bodies[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    return captured_sync_body_call(function, persistence=lambda: _PERSISTENCE)


def register_saved_body(
    cursor: Any, kind: BodyKind, record_id: str, payload: Mapping[str, Any]
) -> dict[str, Any] | None:
    """正本のSQLの後に登録し、障害があれば保存用snapshotだけに反映する。"""
    persistence = _PERSISTENCE
    if persistence is None:
        return None
    try:
        details = payload.get("span_details") or {}
        faults = persistence.register(cursor, BodyOwner("nl2sql", kind, record_id), details)
        if faults:
            return {**payload, "span_details": mark_call_body_failures(details, faults)}
    except Exception:
        logger.warning("trace_body_registration_snapshot_failed")
    return None


def register_state_document(
    cursor: Any, collection: str, entity_id: str, payload: Mapping[str, Any]
) -> None:
    # SQLチャットの一度限りの結果行など、他collectionは本文の所属先にしない。
    if collection not in {"jobs", "history"}:
        return
    kind: BodyKind = "jobs" if collection == "jobs" else "history"
    updated = register_saved_body(cursor, kind, entity_id, payload)
    if updated is None:
        return
    text = json.dumps(updated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        cursor.execute(
            "UPDATE NL2SQL_STATE_DOCUMENTS SET PAYLOAD_JSON = :payload, ETAG = :etag "
            "WHERE COLLECTION = :collection AND ENTITY_ID = :entity_id",
            {
                "payload": text,
                "etag": hashlib.sha256(text.encode()).hexdigest(),
                "collection": collection,
                "entity_id": entity_id,
            },
        )
    except Exception:
        logger.warning("trace_body_registration_snapshot_failed")


def confirm_saved_body(collection: str, payload: Mapping[str, Any]) -> None:
    persistence = _PERSISTENCE
    if persistence is not None and collection in {"jobs", "history", "db_admin_turn"}:
        persistence.confirm_saved(payload.get("span_details") or {})


def register_turn_body(cursor: Any, conversation_id: str, turn_id: str, text: str) -> None:
    updated = register_saved_body(cursor, "db_admin_turn", turn_id, json.loads(text))
    if updated is not None:
        try:
            cursor.execute(
                "UPDATE NL2SQL_DB_ADMIN_CHAT_TURNS SET TURN_JSON = :turn_json "
                "WHERE CONVERSATION_ID = :conversation_id AND TURN_ID = :turn_id",
                {
                    "turn_json": json.dumps(updated, ensure_ascii=False),
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                },
            )
        except Exception:
            logger.warning("trace_body_registration_snapshot_failed")


def release_deleted_bodies(cursor: Any, kind: BodyKind) -> None:
    """正本DELETEの後、実際に消えた所属だけを同一取引で外す。"""
    if kind == "db_admin_turn":
        predicate = (
            "NOT EXISTS (SELECT 1 FROM NL2SQL_DB_ADMIN_CHAT_TURNS t WHERE t.TURN_ID = o.OWNER_ID)"
        )
    else:
        predicate = (
            "NOT EXISTS (SELECT 1 FROM NL2SQL_STATE_DOCUMENTS t "
            "WHERE t.COLLECTION = o.OWNER_KIND AND t.ENTITY_ID = o.OWNER_ID)"
        )
    cursor.execute(
        "SELECT DISTINCT o.OWNER_ID FROM PLATFORM_TRACE_BODY_OWNERS o "  # nosec B608
        "WHERE o.PRODUCT = 'nl2sql' AND o.OWNER_KIND = :kind AND " + predicate,
        {"kind": kind},
    )
    owners = [BodyOwner("nl2sql", kind, str(row[0])) for row in cursor.fetchall()]
    release_body_owners(cursor, owners)


def read_saved_body(
    kind: BodyKind,
    record_id: str,
    span_details: Mapping[str, Any],
    *,
    span_id: str,
    slot: CallBodySlot,
    first_part: int,
    allow_restricted: bool,
) -> BodyPage | None:
    """呼出元が正本の認可を通した後、正式な参照と現在の権限を再確認する。"""
    from .trace_spans import current_trace_span_store

    store = current_trace_span_store()
    return fetch_saved_call_body_page(
        store.connection_factory if store is not None else None,
        BodyOwner("nl2sql", kind, record_id),
        span_details,
        span_id=span_id,
        slot=slot,
        first_part=first_part,
        allow_restricted=allow_restricted,
    )
