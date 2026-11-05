"""保存済みの処理設定が現行契約に合わない場合の読み取り境界。"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import documents


class RecipeOracle:
    def __init__(self, config: object, *, missing: bool = False) -> None:
        self.config = config
        self.missing = missing
        self.job_calls = 0

    async def list_document_recipes(self, document_id: str) -> list[dict[str, object]]:
        if self.missing:
            raise KeyError(document_id)
        now = datetime.now(UTC)
        return [
            {
                "document_id": document_id,
                "recipe_id": f"recipe-{index}",
                "slot_no": index,
                "status": "UPLOADED",
                "processing_config": config,
                "config_revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            for index, config in enumerate([self.config, {}], start=1)
        ]

    async def list_document_ingestion_jobs(self, document_id: str) -> list[Any]:
        self.job_calls += 1
        return []


@pytest.mark.parametrize(
    "config",
    [
        {"preprocess_profile": "removed_synthetic_profile"},
        {"preprocess_profile": "unknown_synthetic_profile"},
        {"chunk_size": -1},
        {"synthetic_extra_setting": "private-input-never-in-response"},
        [],
        "",
        0,
        False,
    ],
)
async def test_invalid_saved_recipe_config_is_actionable_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    config: object,
) -> None:
    original = deepcopy(config)
    oracle = RecipeOracle(config)
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    with pytest.raises(HTTPException) as caught:
        await documents.list_document_recipes("document-a")
    assert caught.value.status_code == 422
    assert getattr(caught.value, "code", None) == "DOCUMENT_RECIPE_CONFIG_INVALID"
    detail = str(caught.value.detail)
    assert "保存済み" in detail and "設定" in detail
    if isinstance(config, dict):
        assert all(str(value) not in detail for value in config.values())
    assert oracle.config == original
    if not isinstance(config, dict):
        assert "項目:" not in detail
    elif "synthetic_extra_setting" in config:
        assert "synthetic_extra_setting" not in detail
    else:
        assert next(iter(config)) in detail


async def test_valid_recipes_are_not_dropped_and_zero_values_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = RecipeOracle({"chunk_overlap": 0, "auto_index_after_chunk_enabled": False})
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    result = await documents.list_document_recipes("document-a")
    assert result.data is not None and len(result.data) == 2
    assert result.data[0].processing_config.chunk_overlap == 0
    assert result.data[0].processing_config.auto_index_after_chunk_enabled is False
    assert oracle.job_calls == 1


async def test_missing_or_inaccessible_document_keeps_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "OracleClient", lambda: RecipeOracle({}, missing=True))
    with pytest.raises(HTTPException) as caught:
        await documents.list_document_recipes("document-a")
    assert caught.value.status_code == 404


async def test_recipe_config_error_uses_safe_api_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.main import create_app

    oracle = RecipeOracle({"preprocess_profile": "unknown_synthetic_profile"})
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/api/documents/document-a/recipes")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == "DOCUMENT_RECIPE_CONFIG_INVALID"
    assert payload["data"] is None
    assert response.headers["x-request-id"]
    assert "preprocess_profile" in response.text
    assert "unknown_synthetic_profile" not in response.text


@pytest.mark.parametrize("config", [None, {}])
async def test_absent_or_empty_object_config_retains_defaults(
    monkeypatch: pytest.MonkeyPatch,
    config: object,
) -> None:
    oracle = RecipeOracle(config)
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    result = await documents.list_document_recipes("document-a")
    assert result.data is not None and len(result.data) == 2
    assert result.data[0].processing_config == result.data[1].processing_config
    assert oracle.config == config
