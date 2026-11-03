"""RAGの正本保存取引と、共通の全文軌跡保存を接続する。"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from functools import wraps
from typing import Any, Literal

from pr_backend_core.observability.trace_bodies import (
    BodyFailure,
    BodyOwner,
    BodyPage,
    read_body_page,
    release_body_owners,
)
from pr_backend_core.observability.trace_body_persistence import TraceBodyPersistence
from pr_backend_core.observability.trace_body_reader import CallBodySlot, saved_call_body_content
from pr_backend_core.observability.trace_body_registry import (
    call_body_references,
    mark_call_body_failures,
)

from app.config import Settings
from app.rag.request_context import current_audit_request_context

type RagBodyOwnerKind = Literal["message", "history", "answer_record"]

_PERSISTENCE: TraceBodyPersistence | None = None
_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


@contextmanager
def _connection() -> Iterator[Any]:
    from app.clients.oracle import OracleClient

    connection = OracleClient().acquire_connection()
    try:
        yield connection
    finally:
        with contextlib.suppress(Exception):
            connection.close()


def start_trace_body_persistence(settings: Settings) -> None:
    import oracledb

    from app.clients.oracle import OracleClient

    global _PERSISTENCE
    with _LOCK:
        if _PERSISTENCE is not None or not OracleClient(settings).is_connection_configured():
            return
        persistence = TraceBodyPersistence(
            _connection, clob_type=oracledb.DB_TYPE_CLOB, settings=settings
        )
        persistence.start()
        _PERSISTENCE = persistence


def stop_trace_body_persistence() -> None:
    global _PERSISTENCE
    with _LOCK:
        persistence, _PERSISTENCE = _PERSISTENCE, None
    if persistence is not None:
        persistence.stop()


@contextmanager
def trace_body_capture() -> Iterator[None]:
    persistence = _PERSISTENCE
    with persistence.capture() if persistence is not None else contextlib.nullcontext():
        yield


def capture_trace_bodies[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """内側のpipelineと外側の失敗記録保存を同じ本文sessionにまとめる。"""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with trace_body_capture():
            return await function(*args, **kwargs)

    return wrapped


def trace_body_details(trace: object) -> Mapping[str, Any]:
    if not isinstance(trace, Mapping):
        return {}
    details = trace.get("span_details")
    return details if isinstance(details, Mapping) else {}


def register_trace_body_owner(
    connection: Any, kind: RagBodyOwnerKind, record_id: str, trace: object
) -> dict[str, BodyFailure]:
    """正本SQL成功後、同じ取引内で所属を登録する。commitは呼出側が持つ。"""
    persistence = _PERSISTENCE
    details = trace_body_details(trace)
    if persistence is None:
        return {}
    try:
        with contextlib.closing(connection.cursor()) as cursor:
            return persistence.register(cursor, BodyOwner("rag", kind, record_id), details)
    except Exception:
        logger.warning("trace_body_owner_registration_failed")
        try:
            return {
                reference.body_id: "storage_unavailable"
                for reference in call_body_references(details)
            }
        except Exception:
            return {}


def confirm_trace_body_saved(trace: object) -> None:
    """正本取引のcommit成功後だけ、一時bufferを解放する。"""
    persistence = _PERSISTENCE
    if persistence is not None:
        persistence.confirm_saved(trace_body_details(trace))


def trace_with_body_failures(trace: object, faults: Mapping[str, BodyFailure]) -> dict[str, Any]:
    saved = dict(trace) if isinstance(trace, Mapping) else {}
    saved["span_details"] = mark_call_body_failures(trace_body_details(trace), faults)
    return saved


def release_deleted_trace_body_owners(connection: Any, kind: RagBodyOwnerKind) -> None:
    """削除SQLの後、同じ取引で本当に正本が無くなった所属だけを解放する。

    FKのcascadeやTTL削除の直前にIDを列挙する方式では、同時追加された行を取りこぼす。
    所属は正本保存と同じ取引でのみ作るため、未commitの別の保存をここで消すことはない。
    writerの起動状態には依存せず、再起動後の削除でも共有本文を正しく保持する。
    """
    table, identifier = {
        "message": ("rag_messages", "message_id"),
        "history": ("rag_run_history", "run_id"),
        "answer_record": ("rag_answer_records", "trace_id"),
    }[kind]
    with contextlib.closing(connection.cursor()) as cursor:
        cursor.execute(
            "SELECT DISTINCT o.OWNER_ID FROM PLATFORM_TRACE_BODY_OWNERS o "
            "WHERE o.PRODUCT = :product AND o.OWNER_KIND = :kind "
            f"AND NOT EXISTS (SELECT 1 FROM {table} p WHERE p.{identifier} = o.OWNER_ID) "
            "ORDER BY o.OWNER_ID",
            {"product": "rag", "kind": kind},
        )
        owners = [BodyOwner("rag", kind, str(row[0])) for row in cursor.fetchall()]
        release_body_owners(cursor, owners)


def read_saved_trace_body(
    trace: object,
    kind: RagBodyOwnerKind,
    record_id: str,
    *,
    span_id: str,
    slot: CallBodySlot,
    first_part: int,
) -> BodyPage | None:
    """routeが正本を認可して渡す。本文IDの指定や実行中bufferへのアクセスは認めない。"""
    allow_restricted = current_audit_request_context().all_users_visible
    content = saved_call_body_content(
        trace_body_details(trace), span_id=span_id, slot=slot, allow_restricted=allow_restricted
    )
    if content is None or content.body_reference is None:
        return None
    try:
        with _connection() as connection, contextlib.closing(connection.cursor()) as cursor:
            page = read_body_page(
                cursor,
                BodyOwner("rag", kind, record_id),
                content.body_reference,
                first_part=first_part,
                allow_restricted=allow_restricted,
            )
        if page is not None:
            return page
    except Exception:
        logger.warning("trace_body_read_failed")
        return BodyPage(content.body_reference, "incomplete", "storage_unavailable", (), None)
    # 正本は存在するが本文の登録が成功していない。空の全文を完了として返さない。
    return BodyPage(
        content.body_reference, "incomplete", content.failure_code or "body_unavailable", (), None
    )
