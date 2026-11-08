"""別々の文書の有効化が並行しても ORA-00060 にならないことを、実 Oracle で確かめる（#4628）。

子の列を先頭に持つ索引の無い FK は、親の行の DELETE のたびに子の表全体の lock を取る。
2 つの transaction が子の表を変更中（SX）のまま、それぞれ別の親の行を消すと deadlock になる
（#2865 と同じ）。データは合成の文書・chunk set だけを使う。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

import pytest
from pr_backend_core.oracle_errors import oracle_error_codes

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient, OracleConnectionProtocol, _execute, _fetch_all
from app.rag.chunking import Chunk
from app.schemas.extraction import StructuredExtraction

# 子の列の索引を持たなくてよい FK（親の行を消さず、主キーも変えない表だけ）。
# 言語資源の版は追記だけで、行の削除・主キーの更新をしない。
_FOREIGN_KEYS_WITHOUT_DELETED_PARENTS = {
    ("RAG_CHUNK_SETS", "LANGUAGE_PACK_RELEASE_ID"),
    ("RAG_INGESTION_JOBS", "LANGUAGE_PACK_RELEASE_ID"),
}


async def _document_with_chunk_set(client: OracleClient, name: str) -> tuple[str, str, str]:
    document = await client.create_document(
        file_name=f"synthetic-{name}.txt",
        object_storage_path=f"synthetic-{name}-{uuid4().hex[:8]}.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    recipe_id = str(recipe["recipe_id"])
    chunk_set_id = await _indexed_chunk_set(client, document.id, recipe_id, name)
    return document.id, recipe_id, chunk_set_id


async def _indexed_chunk_set(
    client: OracleClient, document_id: str, recipe_id: str, name: str
) -> str:
    chunk_set_id = f"cs_{name}_{uuid4().hex[:12]}"
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id, document_id=document_id, recipe_id=recipe_id
    )
    text = f"合成の本文 {chunk_set_id}"
    await client.save_chunk_preview(
        document_id,
        StructuredExtraction(raw_text=text),
        [Chunk(index=0, text=text, start_offset=0, end_offset=len(text))],
        chunk_set_id=chunk_set_id,
    )
    await client.mark_chunk_set_indexed(chunk_set_id=chunk_set_id, chunk_count=1, vector_count=0)
    return chunk_set_id


@pytest.mark.usefixtures("oracle_db")
async def test_every_foreign_key_has_an_index_led_by_its_columns() -> None:
    """親の行を消す FK は、子の列を先頭に持つ索引を持つ（無いと親の DELETE が子の表を lock）。"""
    client = OracleClient()

    def read(connection: OracleConnectionProtocol) -> list[tuple[str, str, bool]]:
        rows = _fetch_all(
            connection,
            r"""
            SELECT c.table_name, c.constraint_name,
                LISTAGG(cc.column_name, ',') WITHIN GROUP (ORDER BY cc.position) AS fk_columns
            FROM user_constraints c
            JOIN user_cons_columns cc ON cc.constraint_name = c.constraint_name
            WHERE c.constraint_type = 'R'
              AND c.table_name LIKE 'RAG\_%' ESCAPE '\'
              AND c.table_name NOT LIKE 'RAG\_TEST\_%' ESCAPE '\'
            GROUP BY c.table_name, c.constraint_name
            """,
            {},
        )
        indexes = _fetch_all(
            connection,
            r"""
            SELECT table_name, index_name,
                LISTAGG(column_name, ',') WITHIN GROUP (ORDER BY column_position) AS index_columns
            FROM user_ind_columns
            WHERE table_name LIKE 'RAG\_%' ESCAPE '\'
            GROUP BY table_name, index_name
            """,
            {},
        )
        leading: dict[str, list[list[str]]] = {}
        for index in indexes:
            leading.setdefault(str(index["table_name"]), []).append(
                str(index["index_columns"]).split(",")
            )
        result = []
        for row in rows:
            columns = str(row["fk_columns"]).split(",")
            covered = any(
                index_columns[: len(columns)] == columns
                for index_columns in leading.get(str(row["table_name"]), [])
            )
            result.append((str(row["table_name"]), str(row["fk_columns"]), covered))
        return result

    foreign_keys = await client._run_transaction(read)

    assert foreign_keys
    unindexed = {
        (table, columns)
        for table, columns, covered in foreign_keys
        if not covered and (table, columns) not in _FOREIGN_KEYS_WITHOUT_DELETED_PARENTS
    }
    assert unindexed == set()


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize(
    ("child_table", "parent_kind"),
    [
        ("rag_graph_references", "chunk_set"),
        ("rag_graph_relationships", "graph_entity"),
        ("rag_structure_index_builds", "document"),
    ],
    ids=["graph-references", "graph-relationships", "structure-builds"],
)
async def test_parent_deletes_of_two_documents_do_not_deadlock_on_child_table(
    child_table: str, parent_kind: str
) -> None:
    """子の表を変更中の 2 つの transaction が、それぞれ別の親の行を消しても両方通る。"""
    client = OracleClient()
    documents = [await _document_with_chunk_set(client, f"fk-{n}") for n in ("a", "b")]
    entity_ids = [f"ent_{uuid4().hex[:16]}" for _ in documents]
    if parent_kind == "chunk_set":
        delete_sql = "DELETE FROM rag_chunk_sets WHERE chunk_set_id = :id"
        targets = [chunk_set_id for _, _, chunk_set_id in documents]
    elif parent_kind == "graph_entity":
        delete_sql = "DELETE FROM rag_graph_entities WHERE entity_id = :id"
        targets = entity_ids

        def insert_entities(connection: OracleConnectionProtocol) -> None:
            for entity_id in entity_ids:
                _execute(
                    connection,
                    "INSERT INTO rag_graph_entities (entity_id, canonical_name) "
                    "VALUES (:id, :name)",
                    {"id": entity_id, "name": f"合成の見出し {entity_id}"},
                )

        await client._run_transaction(insert_entities)
    else:
        delete_sql = "DELETE FROM rag_documents WHERE document_id = :id"
        targets = [document_id for document_id, _, _ in documents]

    barrier = threading.Barrier(2)

    def remove(target: str) -> str:
        def operation(connection: OracleConnectionProtocol) -> str:
            # 子の行を変える transaction が持つのと同じ lock（SX）を、commit まで持つ。
            _execute(connection, f"LOCK TABLE {child_table} IN ROW EXCLUSIVE MODE", {})
            barrier.wait(timeout=30)
            _execute(connection, delete_sql, {"id": target})
            return "deleted"

        try:
            return client._run_transaction_sync(operation)
        except Exception as exc:
            if "ORA-00060" in oracle_error_codes(exc):
                return "deadlock"
            raise

    try:
        results = await asyncio.gather(*(asyncio.to_thread(remove, target) for target in targets))
        assert results == ["deleted", "deleted"]
    finally:

        def cleanup(connection: OracleConnectionProtocol) -> None:
            for entity_id in entity_ids:
                _execute(
                    connection,
                    "DELETE FROM rag_graph_entities WHERE entity_id = :id",
                    {"id": entity_id},
                )

        await client._run_transaction(cleanup)


@pytest.mark.usefixtures("oracle_db")
async def test_parallel_activations_of_two_documents_both_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別々の文書の有効化が、出さなかった候補の削除に同時に入っても両方とも成功する。"""
    client = OracleClient()
    documents = []
    for name in ("act-a", "act-b"):
        document_id, recipe_id, candidate = await _document_with_chunk_set(client, name)
        target = await _indexed_chunk_set(client, document_id, recipe_id, f"{name}-new")
        documents.append((document_id, recipe_id, candidate, target))

    original = oracle_module._delete_chunk_set_outputs
    barrier = threading.Barrier(2)
    waits = iter(range(2))
    lock = threading.Lock()

    def delete_together(
        connection: OracleConnectionProtocol,
        *,
        document_id: str,
        chunk_sets: Sequence[Mapping[str, object]],
    ) -> Any:
        # 最初の 2 回だけ、2 つの transaction が同時に削除へ入るようそろえる。
        with lock:
            first_round = next(waits, None) is not None
        if first_round:
            barrier.wait(timeout=30)
        return original(connection, document_id=document_id, chunk_sets=chunk_sets)

    monkeypatch.setattr(oracle_module, "_delete_chunk_set_outputs", delete_together)

    await asyncio.gather(
        *(
            client.activate_recipe_chunk_set(
                recipe_id=recipe_id, chunk_set_id=target, expected_base_chunk_set_id=None
            )
            for _, recipe_id, _, target in documents
        )
    )

    for document_id, recipe_id, candidate, target in documents:
        recipe = await client.get_document_recipe(document_id, recipe_id)
        assert recipe is not None
        assert recipe["status"] == "INDEXED"
        assert recipe["active_chunk_set_id"] == target

        def remaining(connection: OracleConnectionProtocol, chunk_set_id: str = candidate) -> int:
            rows = _fetch_all(
                connection,
                "SELECT COUNT(*) AS n FROM rag_chunk_sets WHERE chunk_set_id = :id",
                {"id": chunk_set_id},
            )
            return int(str(rows[0]["n"]))

        assert await client._run_transaction(remaining) == 0
