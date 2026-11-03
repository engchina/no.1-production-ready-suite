"""実行の正本・checkpoint namespaceと共通の全文軌跡保存の接続。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.trace_bodies import (
    BodyOwner,
    BodyPage,
    read_body_page,
    release_body_owner,
)
from pr_backend_core.observability.trace_body_lifecycle import captured_body_call
from pr_backend_core.observability.trace_body_persistence import TraceBodyPersistence
from pr_backend_core.observability.trace_body_reader import CallBodySlot, saved_call_body_content
from pr_backend_core.observability.trace_body_registry import mark_call_body_failures

logger = logging.getLogger(__name__)
_PERSISTENCE: TraceBodyPersistence | None = None
_LOCK = threading.Lock()


def run_body_owner(checkpoint_key: str, run_id: str) -> BodyOwner:
    # 正本の主キーはcheckpointとRunの組。別namespaceの同名Runの本文を混ぜない。
    key = json.dumps([checkpoint_key, run_id], ensure_ascii=False, separators=(",", ":"))
    return BodyOwner("agent", "run", hashlib.sha256(key.encode("utf-8")).hexdigest())


def start_trace_body_persistence(settings: TracingSettings) -> None:
    import oracledb

    from app.oracle_connection import platform_oracle_connection

    global _PERSISTENCE
    with _LOCK:
        if _PERSISTENCE is not None:
            return
        persistence = TraceBodyPersistence(
            platform_oracle_connection, clob_type=oracledb.DB_TYPE_CLOB, settings=settings
        )
        persistence.start()
        _PERSISTENCE = persistence


def stop_trace_body_persistence() -> None:
    global _PERSISTENCE
    with _LOCK:
        persistence, _PERSISTENCE = _PERSISTENCE, None
    if persistence is not None:
        persistence.stop()


def capture_trace_bodies[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    return captured_body_call(function, persistence=lambda: _PERSISTENCE)


def register_run_bodies(cursor: Any, checkpoint_key: str, run_id: str, text: str | None) -> None:
    """正本のMERGEの後、同じhead lock取引で登録する。投影の削除には使わない。"""
    owner = run_body_owner(checkpoint_key, run_id)
    if text is None:
        release_body_owner(cursor, owner)
        return
    persistence = _PERSISTENCE
    if persistence is None:
        return
    try:
        stored = json.loads(text)
        details = stored.get("span_details") or {}
        faults = persistence.register(cursor, owner, details)
        if faults:
            stored["span_details"] = mark_call_body_failures(details, faults)
            cursor.execute(
                "UPDATE AGENT_RUNTIME_ENTITIES SET ENTITY_JSON = :entity_json "
                "WHERE CHECKPOINT_KEY = :checkpoint_key AND ENTITY_KIND = 'run' "
                "AND ENTITY_ID = :entity_id",
                {
                    "entity_json": json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                    "checkpoint_key": checkpoint_key,
                    "entity_id": run_id,
                },
            )
    except Exception:
        logger.warning("trace_body_registration_snapshot_failed")


def confirm_run_bodies(rows: Mapping[tuple[str, str], str | None]) -> None:
    persistence = _PERSISTENCE
    if persistence is None:
        return
    for (kind, _run_id), text in rows.items():
        if kind != "run" or text is None:
            continue
        try:
            persistence.confirm_saved(json.loads(text).get("span_details") or {})
        except Exception:
            logger.warning("trace_body_commit_confirmation_failed")


def read_saved_run_body(
    checkpoint_key: str | None,
    run_id: str,
    span_details: Mapping[str, Any],
    *,
    span_id: str,
    slot: CallBodySlot,
    first_part: int,
    allow_restricted: bool,
) -> BodyPage | None:
    """routeでRunと現在の根拠権限を確認した後、正式なspan欄だけを読む。"""
    content = saved_call_body_content(
        span_details, span_id=span_id, slot=slot, allow_restricted=allow_restricted
    )
    if content is None or content.body_reference is None:
        return None
    if checkpoint_key is None:
        return BodyPage(content.body_reference, "incomplete", "storage_unavailable", (), None)
    from app.oracle_connection import platform_oracle_connection

    try:
        with platform_oracle_connection() as connection, connection.cursor() as cursor:
            page = read_body_page(
                cursor,
                run_body_owner(checkpoint_key, run_id),
                content.body_reference,
                first_part=first_part,
                allow_restricted=allow_restricted,
            )
        if page is not None:
            return page
    except Exception:
        logger.warning("trace_body_read_failed")
        return BodyPage(content.body_reference, "incomplete", "storage_unavailable", (), None)
    return BodyPage(
        content.body_reference, "incomplete", content.failure_code or "body_unavailable", (), None
    )
