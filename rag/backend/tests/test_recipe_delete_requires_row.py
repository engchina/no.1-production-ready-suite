"""削除は正式レシピ行だけを対象とし、旧文書から行を作らない。"""

from collections.abc import Mapping
from copy import deepcopy

import pytest

from app.clients.oracle import OracleClient
from tests.test_oracle_adapter import (
    FakeOracleConnection,
    FakeOracleCursor,
    FakeOraclePool,
    _oci_settings,
    _oracle_document_row,
    _run_inline,
)
from tests.test_oracle_db_paths import _default_recipe_id, _recipe_row


class DeleteConnection(FakeOracleConnection):
    """正式行の状態に応答し、rollback時は開始前へ戻す合成transaction。"""

    def __init__(
        self,
        recipes: list[dict[str, object]],
        *,
        visible: bool = True,
        active_status: str | None = None,
    ) -> None:
        super().__init__([])
        self.recipes = deepcopy(recipes)
        self.initial = deepcopy(recipes)
        self.visible = visible
        self.active_status = active_status

    def cursor(self) -> "DeleteCursor":
        return DeleteCursor(self)

    def rollback(self) -> None:
        super().rollback()
        self.recipes = deepcopy(self.initial)


class DeleteCursor(FakeOracleCursor):
    def __init__(self, connection: DeleteConnection) -> None:
        super().__init__(connection)
        self.state = connection

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        sql = " ".join(statement.split())
        binds = dict(parameters or {})
        if sql.startswith("MERGE INTO rag_document_recipes"):
            self.state.recipes.append(_recipe_row())
        elif sql.startswith("DELETE FROM rag_document_recipes"):
            self.state.recipes = [
                row for row in self.state.recipes if row["recipe_id"] != binds["recipe_id"]
            ]
        rows: list[dict[str, object]] = []
        if sql.startswith("SELECT"):
            if "FROM rag_ingestion_jobs" in sql:
                rows = [{"job_id": "synthetic-job"}] if self.state.active_status else []
            elif "FROM rag_document_recipes" in sql:
                matches = [row for row in self.state.recipes if row["document_id"] == "doc-1"]
                if "COUNT(*)" in sql:
                    rows = [{"cnt": len(matches)}]
                else:
                    rows = [
                        dict(row)
                        for row in matches
                        if (
                            row["recipe_id"] == binds["recipe_id"]
                            if "recipe_id" in binds
                            else row["slot_no"] == 1
                        )
                    ]
            elif "FROM rag_documents" in sql:
                rows = [_oracle_document_row(status="UPLOADED")] if self.state.visible else []
            elif "FROM rag_chunk_sets" in sql:
                rows = (
                    [{"cnt": 2}]
                    if "COUNT(*)" in sql
                    else [{"chunk_set_id": "set-a"}, {"chunk_set_id": "set-b"}]
                )
            elif "FROM rag_graph_entities" in sql:
                rows = [{"entity_id": "entity-a"}]
            else:
                raise AssertionError(f"想定外のSELECT: {sql}")
            self.state._execute_results.append(rows)
        super().execute(statement, parameters)


def client_for(
    recipes: list[dict[str, object]],
    *,
    visible: bool = True,
    active_status: str | None = None,
) -> tuple[OracleClient, DeleteConnection]:
    pool = FakeOraclePool()
    connection = DeleteConnection(recipes, visible=visible, active_status=active_status)
    pool.connection = connection
    return OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline), connection


@pytest.mark.parametrize("has_other_recipe", [False, True])
async def test_missing_default_delete_never_creates_or_deletes_recipe(
    has_other_recipe: bool,
) -> None:
    rows = [_recipe_row(recipe_id="formal-other", slot_no=2)] if has_other_recipe else []
    client, connection = client_for(rows)
    with pytest.raises(KeyError, match="レシピが見つかりません"):
        await client.delete_document_recipe("doc-1", _default_recipe_id())
    assert connection.recipes == rows
    assert connection.commits == 0 and connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    assert not any("d.processing_config" in call.statement for call in connection.calls)


@pytest.mark.parametrize("foreign_document", [False, True])
async def test_missing_or_foreign_recipe_delete_is_read_only(foreign_document: bool) -> None:
    row = _recipe_row(recipe_id="foreign-recipe", document_id="doc-other")
    client, connection = client_for([row] if foreign_document else [])
    with pytest.raises(KeyError, match="レシピが見つかりません"):
        await client.delete_document_recipe("doc-1", "foreign-recipe")
    assert connection.commits == 0 and connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)


@pytest.mark.parametrize("recipe_id", [_default_recipe_id(), "formal-recipe"])
async def test_invisible_document_stops_before_recipe_read(recipe_id: str) -> None:
    client, connection = client_for([], visible=False)
    with pytest.raises(KeyError, match="ドキュメントが見つかりません"):
        await client.delete_document_recipe("doc-1", recipe_id)
    assert len(connection.calls) == 1
    assert "FROM rag_documents" in connection.calls[0].statement
    assert "FOR UPDATE" in connection.calls[0].statement
    assert connection.commits == 0 and connection.rollbacks == 1


@pytest.mark.parametrize("recipe_id", [_default_recipe_id(), "formal-recipe"])
async def test_last_existing_recipe_stays_protected(recipe_id: str) -> None:
    row = _recipe_row(recipe_id=recipe_id)
    client, connection = client_for([row])
    with pytest.raises(ValueError, match="少なくとも1件"):
        await client.delete_document_recipe("doc-1", recipe_id)
    assert connection.recipes == [row]
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    assert connection.commits == 0 and connection.rollbacks == 1


@pytest.mark.parametrize("active_status", ["QUEUED", "RUNNING"])
async def test_active_recipe_delete_stays_protected(active_status: str) -> None:
    rows = [_recipe_row(), _recipe_row(recipe_id="formal-other", slot_no=2)]
    client, connection = client_for(rows, active_status=active_status)
    with pytest.raises(ValueError, match="処理中または待機中"):
        await client.delete_document_recipe("doc-1", _default_recipe_id())
    assert connection.recipes == rows
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    query = next(
        call.statement for call in connection.calls if "FROM rag_ingestion_jobs" in call.statement
    )
    assert "'QUEUED', 'RUNNING'" in query
    assert connection.commits == 0 and connection.rollbacks == 1


@pytest.mark.parametrize("recipe_id", [_default_recipe_id(), "formal-recipe"])
async def test_existing_recipe_delete_keeps_graph_and_chunk_cleanup(recipe_id: str) -> None:
    rows = [_recipe_row(recipe_id=recipe_id), _recipe_row(recipe_id="formal-other", slot_no=2)]
    client, connection = client_for(rows)
    removed = await client.delete_document_recipe("doc-1", recipe_id)
    assert removed == 2
    assert connection.recipes == [rows[1]]
    assert connection.commits == 1 and connection.rollbacks == 0
    assert "FOR UPDATE" in connection.calls[0].statement
    assert "FOR UPDATE OF r.recipe_id" in connection.calls[1].statement
    writes = [call for call in connection.calls if call.statement.lstrip().startswith("DELETE")]
    graph = [call for call in writes if "rag_graph_" in call.statement]
    assert len(graph) == 6
    assert {call.parameters["chunk_set_id"] for call in graph} == {"set-a", "set-b"}
    assert [" ".join(call.statement.split()).split()[2] for call in writes[-4:]] == [
        "rag_chunk_set_sections",
        "rag_chunks",
        "rag_chunk_sets",
        "rag_document_recipes",
    ]
    assert all(call.parameters["recipe_id"] == recipe_id for call in writes[-4:])
    assert writes[-1].parameters["document_id"] == "doc-1"
    assert not any("MERGE INTO" in call.statement for call in connection.calls)


@pytest.mark.parametrize(
    "mode,status", [("missing", 404), ("invisible", 404), ("last", 409), ("active", 409)]
)
async def test_delete_route_preserves_rejection_status(
    monkeypatch: pytest.MonkeyPatch, mode: str, status: int
) -> None:
    from fastapi import HTTPException

    from app.api.routes import documents

    rows = [_recipe_row()] if mode == "last" else []
    if mode == "active":
        rows = [_recipe_row(), _recipe_row(recipe_id="formal-other", slot_no=2)]
    client, connection = client_for(
        rows, visible=mode != "invisible", active_status="RUNNING" if mode == "active" else None
    )
    monkeypatch.setattr(documents, "OracleClient", lambda: client)
    with pytest.raises(HTTPException) as caught:
        await documents.delete_document_recipe("doc-1", _default_recipe_id())
    assert caught.value.status_code == status
    assert connection.commits == 0 and connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)


async def test_delete_route_success_keeps_section_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routes import documents

    rows = [_recipe_row(), _recipe_row(recipe_id="formal-other", slot_no=2)]
    client, _ = client_for(rows)
    refreshed: list[tuple[str, str]] = []

    async def refresh(document_id: str, *, oracle: OracleClient, reason: str) -> None:
        assert oracle is client
        refreshed.append((document_id, reason))

    monkeypatch.setattr(documents, "OracleClient", lambda: client)
    monkeypatch.setattr(documents, "refresh_document_section_map", refresh)
    result = await documents.delete_document_recipe("doc-1", _default_recipe_id())
    assert result.data is not None
    assert result.data.recipe_id == _default_recipe_id()
    assert result.data.removed_chunk_set_count == 2
    assert refreshed == [("doc-1", "recipe_delete")]
