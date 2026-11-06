"""参照した正式レシピの欠測を、旧文書列の設定で埋めない。"""

import json
from datetime import UTC, datetime
from inspect import isawaitable
from types import SimpleNamespace
from typing import Any, Never, cast

import pytest
from fastapi import HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.api.routes import documents
from app.main import app
from app.rag.ingestion import IngestionUserError
from app.schemas.document import (
    DocumentProcessingConfig,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
)


class RecipeOracle:
    def __init__(self, row: dict[str, Any] | None, legacy: bool | None) -> None:
        self.row = row
        self.legacy = legacy
        self.calls: list[str] = []

    async def ensure_default_document_recipe(self, _document_id: str) -> dict[str, str]:
        self.calls.append("ensure")
        return {"recipe_id": "synthetic-recipe"}

    async def get_document_recipe(self, _document_id: str, recipe_id: str) -> Any:
        self.calls.append(f"recipe:{recipe_id}")
        return self.row

    async def get_document_processing_config(self, _document_id: str) -> DocumentProcessingConfig:
        self.calls.append("legacy")
        return DocumentProcessingConfig(vision_enabled=self.legacy)

    async def get_document(self, _document_id: str) -> Any:
        return SimpleNamespace(id="synthetic-doc", content_sha256="a" * 64)


@pytest.mark.parametrize("recipe_id", [None, "synthetic-recipe"])
@pytest.mark.parametrize("legacy", [None, False, True])
async def test_missing_formal_recipe_does_not_read_old_document_settings(
    recipe_id: str | None, legacy: bool | None
) -> None:
    oracle = RecipeOracle(None, legacy)
    with pytest.raises(HTTPException) as raised:
        await documents._resolve_ingestion_settings(cast(Any, oracle), "synthetic-doc", recipe_id)
    assert raised.value.status_code == 404
    assert "レシピ" in str(raised.value.detail)
    assert "legacy" not in oracle.calls


@pytest.mark.parametrize("recipe_id", [None, "synthetic-recipe", "synthetic-slot-two"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_formal_recipe_keeps_its_settings(recipe_id: str | None, enabled: bool) -> None:
    oracle = RecipeOracle({"processing_config": {"vision_enabled": enabled}}, not enabled)
    settings, config = await documents._resolve_ingestion_settings(
        cast(Any, oracle), "synthetic-doc", recipe_id
    )
    assert config.vision_enabled is enabled
    assert settings.rag_vision_enabled is enabled
    assert "legacy" not in oracle.calls
    assert ("ensure" in oracle.calls) is (recipe_id is None)


class ReachedChunkIdentity(Exception):
    """設定解決の直後で止め、成果物や状態を変更しない。"""


def _job(snapshot: dict[str, bool] | None = None) -> IngestionJob:
    return IngestionJob(
        id="synthetic-job",
        document_id="synthetic-doc",
        recipe_id="synthetic-recipe",
        status=IngestionJobStatus.RUNNING,
        phase=IngestionJobPhase.EXTRACT,
        parser_profile="synthetic",
        queued_at=datetime(2026, 1, 1, tzinfo=UTC),
        settings_overrides=None if snapshot is None else {"processing_config": snapshot},
    )


@pytest.mark.parametrize("snapshot", [None, {"vision_enabled": False}, {}])
async def test_worker_missing_recipe_and_saved_snapshot_are_distinct(
    monkeypatch: pytest.MonkeyPatch, snapshot: dict[str, bool] | None
) -> None:
    oracle = RecipeOracle(None, True)
    job = _job(snapshot)

    def stop_before_materializing(*_args: Any, **_kwargs: Any) -> Never:
        raise ReachedChunkIdentity

    monkeypatch.setattr(documents, "compute_chunk_set_id", stop_before_materializing)
    expected = IngestionUserError if snapshot is None else ReachedChunkIdentity
    with pytest.raises(expected):
        await documents._materialize_experiment_candidate(cast(Any, oracle), job)
    assert "legacy" not in oracle.calls
    assert bool(oracle.calls) is (snapshot is None)


class VisibleRecipeOracle(RecipeOracle):
    async def get_document_summary(self, _document_id: str) -> Any:
        return SimpleNamespace(id="synthetic-doc", content_sha256=None)

    async def list_document_chunk_sets(self, _document_id: str) -> list[dict[str, Any]]:
        return []

    async def list_ingestion_segments(self, _document_id: str) -> list[dict[str, Any]]:
        return []


@pytest.mark.parametrize("route", ["chunk_sets", "segments"])
async def test_missing_recipe_get_returns_safe_404(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    oracle = VisibleRecipeOracle(None, True)

    async def no_plan(*_args: Any, **_kwargs: Any) -> tuple[None, dict[str, Any]]:
        return None, {}

    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    monkeypatch.setattr(documents, "_materialization_plan_for_document", no_plan)
    with pytest.raises(HTTPException) as raised:
        if route == "chunk_sets":
            await documents.list_document_chunk_sets("synthetic-doc")
        else:
            await documents.list_document_ingestion_segments("synthetic-doc")
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/documents/synthetic-doc", "headers": []}
    )
    pending = app.exception_handlers[StarletteHTTPException](request, raised.value)
    assert isawaitable(pending)
    response = await pending
    assert response.status_code == 404
    body = bytes(response.body)
    assert raised.value.detail in json.loads(body)["error_messages"]
    assert "処理レシピ" in body.decode()
    assert "legacy" not in oracle.calls


@pytest.mark.parametrize("status", [409, 503])
async def test_worker_keeps_other_http_failures(status: int) -> None:
    error = HTTPException(status_code=status, detail="合成HTTP失敗")

    class RaisingOracle(RecipeOracle):
        async def get_document_recipe(self, _document_id: str, recipe_id: str) -> Any:
            self.calls.append(f"recipe:{recipe_id}")
            raise error

    oracle = RaisingOracle(None, True)
    with pytest.raises(HTTPException) as raised:
        await documents._materialize_experiment_candidate(cast(Any, oracle), _job())
    assert raised.value is error
    assert "legacy" not in oracle.calls


async def test_formal_empty_config_keeps_global_inheritance() -> None:
    oracle = RecipeOracle({"processing_config": {}}, True)
    _, config = await documents._resolve_ingestion_settings(
        cast(Any, oracle), "synthetic-doc", "synthetic-recipe"
    )
    assert config.vision_enabled is None
    assert "legacy" not in oracle.calls


@pytest.mark.parametrize(
    "capabilities,recipe_id",
    [((), None), ((), "synthetic-recipe"), (("ensure",), None), (("get",), None)],
)
async def test_required_recipe_reader_cannot_fall_back_to_legacy_settings(
    capabilities: tuple[str, ...], recipe_id: str | None
) -> None:
    calls: list[str] = []

    async def legacy(_document_id: str) -> DocumentProcessingConfig:
        calls.append("legacy")
        return DocumentProcessingConfig(vision_enabled=False)

    async def ensure(_document_id: str) -> dict[str, str]:
        calls.append("ensure")
        return {"recipe_id": "synthetic-recipe"}

    async def get(_document_id: str, _recipe_id: str) -> dict[str, object]:
        calls.append("get")
        return {"processing_config": {"vision_enabled": True}}

    oracle = SimpleNamespace(get_document_processing_config=legacy)
    if "ensure" in capabilities:
        oracle.ensure_default_document_recipe = ensure
    if "get" in capabilities:
        oracle.get_document_recipe = get
    with pytest.raises(AttributeError):
        await documents._resolve_ingestion_settings(cast(Any, oracle), "synthetic-doc", recipe_id)
    assert calls == (["ensure"] if capabilities == ("ensure",) else [])
    assert "legacy" not in calls
