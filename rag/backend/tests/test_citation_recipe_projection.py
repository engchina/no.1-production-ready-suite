"""ID参照のSQLは引用した版の配方を返す（権限条件は別の回帰で確認）。"""

import sqlite3
from collections.abc import Mapping
from typing import Any

import pytest

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["readable_version_chunk", "retrievable_chunk"])
@pytest.mark.parametrize("relation", ["referenced-version", "missing-recipe-row", "legacy"])
async def test_id_read_projects_referenced_recipe(
    monkeypatch: pytest.MonkeyPatch, method: str, relation: str
) -> None:
    # SELECT/JOINを実行する局所試験。Oracle固有の認可条件は既存の実Oracle試験へ任せる。
    monkeypatch.setattr(oracle_module, "_version_read_where", lambda: ("1=1", {}))
    monkeypatch.setattr(oracle_module, "_oracle_retrieval_where", lambda *a, **k: ("1=1", {}))
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE rag_chunks(
                document_id,chunk_id,chunk_text,metadata_json,chunk_index,chunk_set_id);
            CREATE TABLE rag_documents(
                document_id,file_name,category_name,superseded_by_document_id);
            CREATE TABLE rag_chunk_sets(chunk_set_id,document_id,recipe_id);
            CREATE TABLE rag_document_recipes(recipe_id,document_id,slot_no);
            INSERT INTO rag_documents VALUES('doc-a','synthetic.docx',NULL,NULL);
            INSERT INTO rag_document_recipes VALUES('current-recipe','doc-a',1);
            INSERT INTO rag_chunk_sets VALUES('current-set','doc-a','current-recipe');
        """)
        if relation != "legacy":
            db.execute(
                "INSERT INTO rag_chunk_sets VALUES(?,?,?)", ("quoted-set", "doc-a", "quoted-recipe")
            )
        if relation == "referenced-version":
            db.execute(
                "INSERT INTO rag_document_recipes VALUES(?,?,?)", ("quoted-recipe", "doc-a", 2)
            )
        db.execute(
            "INSERT INTO rag_chunks VALUES(?,?,?,?,?,?)",
            (
                "doc-a",
                "quoted-chunk",
                "合成の本文",
                "{}",
                7,
                None if relation == "legacy" else "quoted-set",
            ),
        )

        class Client(OracleClient):
            async def _fetch_one(
                self, statement: str, binds: Mapping[str, object] | None = None
            ) -> dict[str, Any] | None:
                row = db.execute(statement, dict(binds or {})).fetchone()
                return dict(row) if row else None

        chunk = await getattr(Client(), method)("doc-a", "quoted-chunk")
        assert chunk is not None
        assert chunk.text == "合成の本文"
        if relation == "legacy":
            assert "recipe_id" not in chunk.metadata
        else:
            assert chunk.metadata["recipe_id"] == "quoted-recipe"
            assert chunk.metadata["chunk_set_id"] == "quoted-set"
            assert chunk.metadata.get("recipe_slot_no") == (
                2 if relation == "referenced-version" else None
            )
