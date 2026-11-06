"""設定保存は既存の正式レシピだけを更新し、旧文書から補完しない。"""

from collections.abc import Mapping

import pytest

from app.clients.oracle import OracleClient
from app.schemas.document import DocumentProcessingConfig
from tests.test_oracle_adapter import (
    FakeOracleConnection,
    FakeOracleCursor,
    FakeOraclePool,
    _oci_settings,
    _oracle_document_row,
    _run_inline,
)
from tests.test_oracle_db_paths import _default_recipe_id, _recipe_row


class RecipeConnection(FakeOracleConnection):
    """SQL の順序ではなく正式行の有無に応答する合成 transaction。"""

    def __init__(self, recipe: dict[str, object] | None, active: bool) -> None:
        super().__init__([])
        self.recipe = recipe
        self.active = active

    def cursor(self) -> "RecipeCursor":
        return RecipeCursor(self)


class RecipeCursor(FakeOracleCursor):
    def __init__(self, connection: RecipeConnection) -> None:
        super().__init__(connection)
        self.state = connection

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        sql = " ".join(statement.split())
        if sql.startswith("MERGE INTO rag_document_recipes"):
            self.state.recipe = _recipe_row()
        elif sql.startswith("UPDATE rag_document_recipes"):
            assert self.state.recipe is not None
            revision = self.state.recipe["config_revision"]
            assert isinstance(revision, int)
            self.state.recipe["config_revision"] = revision + 1
            self.state.recipe["processing_config"] = {"vision_enabled": True}
        rows: list[dict[str, object]] = []
        if sql.startswith("SELECT"):
            if "FROM rag_ingestion_jobs" in sql:
                rows = [{"job_id": "synthetic-job"}] if self.state.active else []
            elif "FROM rag_document_recipes" in sql:
                rows = [dict(self.state.recipe)] if self.state.recipe is not None else []
            elif "FROM rag_documents" in sql:
                rows = [_oracle_document_row(status="UPLOADED")]
            else:
                raise AssertionError(f"想定外の SELECT: {sql}")
            self.state._execute_results.append(rows)
        super().execute(statement, parameters)


def client_for(
    recipe: dict[str, object] | None, *, active: bool = False
) -> tuple[OracleClient, RecipeConnection]:
    pool = FakeOraclePool()
    connection = RecipeConnection(recipe, active)
    pool.connection = connection
    return OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline), connection


@pytest.mark.parametrize("recipe_id", [_default_recipe_id(), "missing-formal-recipe"])
async def test_missing_recipe_put_does_not_seed_legacy_document(recipe_id: str) -> None:
    client, connection = client_for(None)
    with pytest.raises(KeyError, match="レシピが見つかりません"):
        await client.update_document_recipe_config(
            "doc-1", recipe_id, DocumentProcessingConfig(vision_enabled=True)
        )
    assert connection.recipe is None
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    assert not any("d.processing_config" in call.statement for call in connection.calls)


@pytest.mark.parametrize(
    "recipe_id,slot", [(_default_recipe_id(), 1), ("formal-slot-one", 1), ("formal-slot-two", 2)]
)
@pytest.mark.parametrize("active", [False, True])
async def test_existing_recipe_put_preserves_revision_and_active_job_guard(
    recipe_id: str, slot: int, active: bool
) -> None:
    client, connection = client_for(
        _recipe_row(recipe_id=recipe_id, slot_no=slot, config_revision=7), active=active
    )
    config = DocumentProcessingConfig(vision_enabled=True)
    if active:
        with pytest.raises(ValueError, match="処理中または待機中"):
            await client.update_document_recipe_config("doc-1", recipe_id, config)
        assert connection.commits == 0
        assert connection.rollbacks == 1
        assert not any(call.statement.lstrip().startswith("UPDATE") for call in connection.calls)
        return
    result = await client.update_document_recipe_config("doc-1", recipe_id, config)
    assert result["recipe_id"] == recipe_id
    assert result["slot_no"] == slot
    assert result["config_revision"] == 8
    writes = [call for call in connection.calls if not call.statement.lstrip().startswith("SELECT")]
    assert len(writes) == 1
    assert writes[0].statement.lstrip().startswith("UPDATE rag_document_recipes")
    assert writes[0].parameters["recipe_id"] == recipe_id
    assert writes[0].parameters["document_id"] == "doc-1"
    assert "config_revision = config_revision + 1" in writes[0].statement
    assert any("FOR UPDATE" in call.statement for call in connection.calls)
    assert connection.commits == 1


@pytest.mark.parametrize("active,status", [(False, 404), (True, 409)])
async def test_put_route_preserves_missing_and_busy_status(
    monkeypatch: pytest.MonkeyPatch, active: bool, status: int
) -> None:
    """正式保存経路の例外を既存の HTTP 契約へ写す。"""
    from fastapi import HTTPException

    from app.api.routes import documents
    from app.schemas.document import DocumentProcessingConfigInput

    recipe = _recipe_row() if active else None
    client, connection = client_for(recipe, active=active)
    monkeypatch.setattr(documents, "OracleClient", lambda: client)
    monkeypatch.setattr(documents, "_merge_document_processing_config", lambda config: None)
    monkeypatch.setattr(
        documents, "_raise_if_recipe_selects_mineru_without_license", lambda config: None
    )
    with pytest.raises(HTTPException) as caught:
        await documents.update_document_recipe(
            "doc-1", _default_recipe_id(), DocumentProcessingConfigInput(vision_enabled=True)
        )
    assert caught.value.status_code == status
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
