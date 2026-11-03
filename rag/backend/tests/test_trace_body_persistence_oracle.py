"""実OracleでRAG正本と全文を保存し、最後の所属削除まで共有本文を保持する。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import oracledb
import pytest
from pr_backend_core.observability.call_details import call_content
from pr_backend_core.observability.trace_bodies import (
    BodyOwner,
    read_body_page,
    release_body_owners,
)
from pr_backend_core.observability.trace_body_persistence import TraceBodyPersistence

from app.clients.oracle import OracleClient, StoredMessage, _execute
from app.clients.oracle_run_history import RunHistoryStore
from app.config import get_settings
from app.rag import trace_bodies


@pytest.mark.usefixtures("oracle_db")
async def test_rag_body_roundtrip_and_real_primary_lifetimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = OracleClient()
    run_id, message_id = uuid4().hex, uuid4().hex
    profile = await oracle.ensure_default_search_answer_profile()
    conversation = await oracle.create_conversation(search_answer_profile_id=profile.id)

    @contextmanager
    def connection() -> Iterator[Any]:
        native = oracle.acquire_connection()
        try:
            yield native
        finally:
            native.close()

    persistence = TraceBodyPersistence(
        connection, clob_type=oracledb.DB_TYPE_CLOB, settings=get_settings()
    )
    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", persistence)
    persistence.start()
    content = None
    text = "合成の保存対象本文。" * 10000 + "最終条件。"
    owners = [
        BodyOwner("rag", "message", message_id),
        BodyOwner("rag", "history", run_id),
        BodyOwner("rag", "answer_record", run_id),
    ]
    try:
        with persistence.capture():
            content = call_content(text)
            assert content.body_reference is not None and content.preview_only
            trace = {
                "trace_id": uuid4().hex,
                "span_details": {"a" * 16: {"call": {"input": content.model_dump(mode="json")}}},
            }
            await oracle.append_message(
                StoredMessage(
                    id=message_id,
                    conversation_id=conversation.id,
                    role="ASSISTANT",
                    content="合成の回答",
                    progress=trace,
                    created_at=datetime.now(UTC),
                )
            )
            await RunHistoryStore(oracle).save_run(
                {
                    "run_id": run_id,
                    "surface": "chat",
                    "status": "success",
                    "stages": {"trace": trace},
                }
            )
            await oracle.save_answer_record(
                {
                    "trace_id": run_id,
                    "surface": "chat",
                    "answer_engine": "grounded",
                    "question": "合成の質問",
                    "answer": "合成の回答",
                    "diagnostics": {"trace": trace},
                }
            )
        assert await asyncio.to_thread(persistence.stop)
        with connection() as native, native.cursor() as cursor:
            position, restored = 0, ""
            while True:
                page = read_body_page(
                    cursor, owners[1], content.body_reference, first_part=position
                )
                assert page is not None and page.status == "complete", page
                restored += "".join(part.text for part in page.parts)
                if page.next_part is None:
                    break
                position = page.next_part
            assert restored == text
        await oracle.delete_conversation(conversation.id)
        with connection() as native, native.cursor() as cursor:
            assert read_body_page(cursor, owners[0], content.body_reference) is None
            assert read_body_page(cursor, owners[1], content.body_reference) is not None

        def age_records(native: Any) -> None:
            for table, identifier in (
                ("rag_run_history", "run_id"),
                ("rag_answer_records", "trace_id"),
            ):
                _execute(
                    native,
                    f"UPDATE {table} SET created_at = SYSTIMESTAMP - NUMTODSINTERVAL(36501, 'DAY') "
                    f"WHERE {identifier} = :record_id",
                    {"record_id": run_id},
                )

        await oracle._run_transaction(age_records)  # noqa: SLF001
        assert await RunHistoryStore(oracle).purge_runs(36500) >= 1
        with connection() as native, native.cursor() as cursor:
            assert read_body_page(cursor, owners[1], content.body_reference) is None
            assert read_body_page(cursor, owners[2], content.body_reference) is not None
        assert await oracle.purge_answer_records(36500) >= 1
        with connection() as native, native.cursor() as cursor:
            cursor.execute(
                "SELECT STATUS FROM PLATFORM_TRACE_BODIES WHERE BODY_ID = :body_id",
                {"body_id": content.body_reference.body_id},
            )
            assert cursor.fetchone()[0] == "deleted"
            cursor.execute(
                "SELECT COUNT(*) FROM PLATFORM_TRACE_BODY_PARTS WHERE BODY_ID = :body_id",
                {"body_id": content.body_reference.body_id},
            )
            assert cursor.fetchone()[0] == 0
    finally:
        await asyncio.to_thread(persistence.stop)
        with connection() as native, native.cursor() as cursor:
            for table, identifier, key in (
                ("rag_messages", "message_id", message_id),
                ("rag_run_history", "run_id", run_id),
                ("rag_answer_records", "trace_id", run_id),
                ("rag_conversations", "conversation_id", conversation.id),
            ):
                cursor.execute(f"DELETE FROM {table} WHERE {identifier} = :key", {"key": key})
            release_body_owners(cursor, owners)
            if content is not None and content.body_reference is not None:
                cursor.execute(
                    "DELETE FROM PLATFORM_TRACE_BODIES WHERE BODY_ID = :body_id "
                    "AND NOT EXISTS (SELECT 1 FROM PLATFORM_TRACE_BODY_OWNERS o "
                    "WHERE o.BODY_ID = :body_id)",
                    {"body_id": content.body_reference.body_id},
                )
            native.commit()
