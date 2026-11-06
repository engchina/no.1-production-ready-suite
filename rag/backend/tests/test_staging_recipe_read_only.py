"""staging結果の収集は正式slot1だけを読み、旧文書から行を補造しない。"""

from collections.abc import Mapping
from copy import deepcopy

import pytest

from app.rag.file_processing_staging import _collect_staging_evidence
from app.rag.staging_recipes import StagingRecipeOracle, read_staging_recipe
from app.schemas.document import FileStatus
from tests.test_oracle_adapter import (
    FakeOracleConnection,
    FakeOracleCursor,
    FakeOraclePool,
    _oci_settings,
    _oracle_document_row,
    _run_inline,
)
from tests.test_oracle_db_paths import _default_recipe_id, _recipe_row


class ReadConnection(FakeOracleConnection):
    def __init__(
        self,
        rows: list[dict[str, object]],
        *,
        old_status: str = "UPLOADED",
        visible: bool = True,
        disappear: bool = False,
    ) -> None:
        super().__init__([])
        self.rows = deepcopy(rows)
        self.old_status = old_status
        self.visible = visible
        self.disappear = disappear

    def cursor(self) -> "ReadCursor":
        return ReadCursor(self)


class ReadCursor(FakeOracleCursor):
    def __init__(self, connection: ReadConnection) -> None:
        super().__init__(connection)
        self.state = connection

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        sql = " ".join(statement.split())
        binds = dict(parameters or {})
        if sql.startswith("MERGE INTO rag_document_recipes"):
            self.state.rows.append(_recipe_row(status=self.state.old_status))
        elif not sql.startswith("SELECT"):
            raise AssertionError(f"想定外のSQL: {sql}")
        rows: list[dict[str, object]] = []
        if sql.startswith("SELECT"):
            if "FROM rag_document_recipes" in sql:
                rows = [
                    dict(row)
                    for row in self.state.rows
                    if self.state.visible
                    and row["document_id"] == binds["document_id"]
                    and (
                        row["recipe_id"] == binds["recipe_id"]
                        if "recipe_id" in binds
                        else row["slot_no"] == 1
                        if "r.slot_no = 1" in sql
                        else True
                    )
                ]
                if self.state.disappear and "ORDER BY r.slot_no" in sql:
                    self.state.rows = []
            elif "FROM rag_documents" in sql:
                rows = (
                    [_oracle_document_row(status=self.state.old_status)]
                    if self.state.visible
                    else []
                )
            else:
                raise AssertionError(f"想定外のSELECT: {sql}")
            self.state._execute_results.append(rows)
        super().execute(statement, parameters)


def client_for(
    rows: list[dict[str, object]],
    *,
    old_status: str = "UPLOADED",
    visible: bool = True,
    disappear: bool = False,
) -> tuple[StagingRecipeOracle, ReadConnection]:
    connection = ReadConnection(rows, old_status=old_status, visible=visible, disappear=disappear)
    pool = FakeOraclePool()
    pool.connection = connection
    client = StagingRecipeOracle(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    return client, connection


def assert_reads_only(connection: ReadConnection) -> None:
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    assert not any("d.processing_config" in call.statement for call in connection.calls)


@pytest.mark.parametrize("old_status", ["UPLOADED", "REVIEW", "ERROR", "INDEXED"])
async def test_actual_collector_missing_slot1_never_initializes(old_status: str) -> None:
    client, connection = client_for([], old_status=old_status)
    with pytest.raises(RuntimeError, match="処理レシピが見つかりません"):
        await _collect_staging_evidence(client, None, "doc-1")
    assert connection.rows == []
    assert_reads_only(connection)


async def test_slot2_alone_does_not_replace_or_recreate_slot1() -> None:
    row = _recipe_row(recipe_id="formal-slot-two", slot_no=2)
    client, connection = client_for([row])
    with pytest.raises(RuntimeError, match="処理レシピが見つかりません"):
        await read_staging_recipe(client, "doc-1")
    assert connection.rows == [row]
    assert_reads_only(connection)


async def test_invisible_document_uses_authoritative_read_denial() -> None:
    client, connection = client_for([], visible=False)
    with pytest.raises(KeyError):
        await read_staging_recipe(client, "doc-1")
    assert_reads_only(connection)


@pytest.mark.parametrize("recipe_id", [_default_recipe_id(), "formal-slot-one"])
@pytest.mark.parametrize("status", ["UPLOADED", "REVIEW", "ERROR"])
async def test_actual_reader_keeps_formal_slot1_and_state(recipe_id: str, status: str) -> None:
    row = _recipe_row(recipe_id=recipe_id, status=status, active_extraction_recipe_id="old-pending")
    client, connection = client_for([row])
    state = await client.read_staging_recipe("doc-1")
    assert state.recipe_id == recipe_id and state.status == FileStatus(status)
    assert state.extraction == {} and state.chunks == () and state.segments == ()
    assert state.chunk_set_id is None
    assert_reads_only(connection)
    selected = [call for call in connection.calls if "recipe_id" in call.parameters]
    assert selected[-1].parameters["recipe_id"] == recipe_id


async def test_recipe_disappearance_is_missing_without_repair() -> None:
    client, connection = client_for([_recipe_row(recipe_id="formal-slot-one")], disappear=True)
    with pytest.raises(RuntimeError, match="処理レシピが見つかりません"):
        await read_staging_recipe(client, "doc-1")
    assert_reads_only(connection)


@pytest.mark.parametrize("change", ["none", "document", "recipe", "status", "extraction"])
async def test_served_version_scope_and_extraction_remain_authoritative(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    row = _recipe_row(
        recipe_id="formal-slot-one",
        status="INDEXED",
        active_chunk_set_id="served-set",
        active_extraction_recipe_id="wrong-current",
    )
    client, connection = client_for([row])
    seen: list[str] = []

    async def version(chunk_set_id: str) -> dict[str, object]:
        assert chunk_set_id == "served-set"
        result: dict[str, object] = {
            "document_id": "doc-1",
            "recipe_id": "formal-slot-one",
            "status": "INDEXED",
            "extraction_recipe_id": "served-extraction",
        }
        key = {
            "document": "document_id",
            "recipe": "recipe_id",
            "status": "status",
            "extraction": "extraction_recipe_id",
        }.get(change)
        if key:
            result[key] = None if change == "extraction" else "wrong"
        return result

    async def chunks(chunk_set_id: str) -> list[object]:
        assert chunk_set_id == "served-set"
        return ["synthetic-served-chunk"]

    async def artifact(*, document_id: str, extraction_recipe_id: str) -> dict[str, object]:
        assert document_id == "doc-1" and extraction_recipe_id == "served-extraction"
        seen.append(extraction_recipe_id)
        return {"extraction_json": {"raw_text": "合成の正式版本文"}}

    monkeypatch.setattr(client, "get_chunk_set", version)
    monkeypatch.setattr(client, "list_chunk_set_chunks", chunks)
    monkeypatch.setattr(client, "get_document_extraction_artifact", artifact)
    if change != "none":
        with pytest.raises(RuntimeError):
            await read_staging_recipe(client, "doc-1")
        assert seen == []
    else:
        state = await read_staging_recipe(client, "doc-1")
        assert state.extraction == {"raw_text": "合成の正式版本文"}
        assert state.chunks == ("synthetic-served-chunk",)
        assert seen == ["served-extraction"]
    assert_reads_only(connection)
