"""文書レシピの解析版と引用の版を原本読み取りへ引き継ぐ。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes import documents
from app.main import app
from app.schemas.search import RetrievedChunk
from tests.support import AsgiTestClient
from tests.test_json_source_window import fixture


def test_recipe_source_map_pins_extraction_version_and_refuses_missing_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, payload = fixture()
    detail = SimpleNamespace(object_storage_path="synthetic.json", preprocess_artifact=None)
    oracle = SimpleNamespace(
        get_document=AsyncMock(return_value=detail),
        get_document_recipe=AsyncMock(return_value={"active_extraction_recipe_id": "ex1"}),
        get_document_extraction_artifact=AsyncMock(return_value={"extraction_json": payload}),
    )
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    load = AsyncMock(return_value=(raw, "synthetic.json", "application/json"))
    monkeypatch.setattr(documents, "load_document_content", load)
    client = AsgiTestClient(app)
    path = "/api/documents/d1/recipes/r1/source-map?element_id=e1&expected_revision=ex1"
    result = client.get(path)
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "exact"
    assert result.json()["data"]["revision"] == "ex1"
    load.reset_mock()
    assert client.get(path.replace("ex1", "ex0")).status_code == 409
    load.assert_not_awaited()
    oracle.get_document_recipe.return_value = None
    assert client.get(path).status_code == 404
    load.assert_not_awaited()


def test_citation_source_map_pins_chunk_and_does_not_replace_stale_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, payload = fixture()
    detail = SimpleNamespace(object_storage_path="synthetic.json", preprocess_artifact=None)
    chunk = RetrievedChunk(
        document_id="d1",
        chunk_id="c1",
        text="Same 😀 text",
        score=0,
        metadata={"chunk_set_id": "cs1", "element_ids": "e1"},
    )
    oracle = SimpleNamespace(
        retrievable_chunk=AsyncMock(return_value=chunk),
        accessible_chunk_exists=AsyncMock(return_value=True),
        get_document=AsyncMock(return_value=detail),
        chunk_set_extraction_recipe_ids=AsyncMock(return_value={"cs1": "ex1"}),
        get_document_extraction_artifact=AsyncMock(return_value={"extraction_json": payload}),
    )
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    load = AsyncMock(return_value=(raw, "synthetic.json", "application/json"))
    monkeypatch.setattr(documents, "load_document_content", load)
    client = AsgiTestClient(app)
    path = "/api/documents/d1/source-map?chunk_id=c1&expected_revision=cs1"
    result = client.get(path)
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "exact"
    load.reset_mock()
    assert client.get(path.replace("cs1", "cs0")).status_code == 409
    oracle.retrievable_chunk.return_value = None
    assert client.get(path).status_code == 409
    oracle.accessible_chunk_exists.return_value = False
    assert client.get(path).status_code == 404
    load.assert_not_awaited()
