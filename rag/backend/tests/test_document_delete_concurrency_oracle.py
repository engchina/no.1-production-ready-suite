"""合成の表と文書で FK のロック競合と削除の並行性を確かめる。"""

from __future__ import annotations

import asyncio
import threading
from uuid import uuid4

import pytest
from pr_backend_core.oracle_errors import oracle_error_codes

from app.clients.document_cleanup_store import DocumentCleanupStore, DocumentCleanupTargets
from app.clients.oracle import OracleClient, OracleConnectionProtocol, _execute


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("indexed", [False, True])
async def test_foreign_key_lock_contention_with_and_without_index(indexed: bool) -> None:
    client = OracleClient()
    token = uuid4().hex[:10].upper()
    parent = f"RAG_TEST_DL_P_{token}"
    child = f"RAG_TEST_DL_C_{token}"
    created: list[str] = []

    def setup(connection: OracleConnectionProtocol) -> None:
        _execute(connection, f"CREATE TABLE {parent} (id NUMBER PRIMARY KEY)", {})
        created.append(parent)
        _execute(
            connection,
            f"CREATE TABLE {child} (id NUMBER PRIMARY KEY, "
            f"parent_id NUMBER REFERENCES {parent}(id) ON DELETE CASCADE, marker NUMBER)",
            {},
        )
        created.append(child)
        if indexed:
            _execute(connection, f"CREATE INDEX RAG_TEST_DL_I_{token} ON {child}(parent_id)", {})
        for row_id in (1, 2):
            _execute(connection, f"INSERT INTO {parent}(id) VALUES (:id)", {"id": row_id})
            _execute(
                connection,
                f"INSERT INTO {child}(id, parent_id, marker) VALUES (:id, :id, 0)",
                {"id": row_id},
            )

    barrier = threading.Barrier(2)

    def remove(row_id: int) -> str:
        def operation(connection: OracleConnectionProtocol) -> str:
            _execute(connection, f"UPDATE {child} SET marker = 1 WHERE id = :id", {"id": row_id})
            barrier.wait(timeout=15)
            _execute(connection, f"DELETE FROM {parent} WHERE id = :id", {"id": row_id})
            return "deleted"

        try:
            return client._run_transaction_sync(operation)
        except Exception as exc:
            if "ORA-00060" in oracle_error_codes(exc):
                return "deadlock"
            raise

    try:
        await client._run_transaction(setup)
        results = await asyncio.gather(asyncio.to_thread(remove, 1), asyncio.to_thread(remove, 2))
        assert sorted(results) == (["deleted", "deleted"] if indexed else ["deadlock", "deleted"])
    finally:

        def cleanup(connection: OracleConnectionProtocol) -> None:
            for table in reversed(created):
                _execute(connection, f"DROP TABLE {table} PURGE", {})

        await client._run_transaction(cleanup)


@pytest.mark.usefixtures("oracle_db")
async def test_parallel_document_delete_creates_one_cleanup_record_per_document() -> None:
    client = OracleClient()
    store = DocumentCleanupStore(client)
    token = uuid4().hex
    documents = [
        await client.create_document(
            file_name=f"parallel-{token}-{i}.txt",
            object_storage_path=f"local://uploaded/{token}/{i}.txt",
            content_type="text/plain",
        )
        for i in range(3)
    ]
    targets = DocumentCleanupTargets(prefixes=(f"artifacts/{token}/",))
    tasks = []
    try:
        tasks.extend(
            await asyncio.gather(
                *(
                    store.delete_document_with_cleanup(doc.id, doc.file_name, targets)
                    for doc in documents[:2]
                )
            )
        )
        duplicate = await asyncio.gather(
            *(
                store.delete_document_with_cleanup(documents[2].id, documents[2].file_name, targets)
                for _ in range(2)
            )
        )
        tasks.extend(duplicate)
        assert all(task is not None for task in tasks[:2])
        assert sum(task is not None for task in duplicate) == 1
        for doc in documents:
            assert await client.get_document(doc.id) is None
            assert await store.find_for_document(doc.id) is not None
    finally:
        for task in tasks:
            if task is not None:
                await store.save_progress(
                    task.cleanup_id, DocumentCleanupTargets(), error_type=None
                )
