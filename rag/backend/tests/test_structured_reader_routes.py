"""構造化readerの版固定と権限境界を合成APIで確かめる。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes import documents
from app.main import app
from app.schemas.search import RetrievedChunk
from tests.support import AsgiTestClient
from tests.test_structured_source_reader import fixture


def test_recipe_reader_pins_saved_extraction_before_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, payload = fixture()
    oracle = SimpleNamespace(
        get_document_recipe=AsyncMock(return_value={"active_extraction_recipe_id": "ex1"}),
        get_document_extraction_artifact=AsyncMock(return_value={"extraction_json": payload}),
    )
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    monkeypatch.setattr(
        documents, "_recipe_detail_and_artifact", AsyncMock(return_value=(None, None))
    )
    load = AsyncMock(return_value=data)
    monkeypatch.setattr(documents, "_reader_structured_data", load)
    client = AsgiTestClient(app)
    path = "/api/documents/d1/recipes/r1/reader?element_id=r2&expected_revision=ex1"
    response = client.get(path)
    assert response.status_code == 200
    selected = next(b for b in response.json()["data"]["blocks"] if b["cited"])
    assert selected["record"]["location"]["row_start"] == 3
    load.reset_mock()
    assert client.get(path.replace("ex1", "ex0")).status_code == 409
    load.assert_not_awaited()
    unknown = client.get(path.replace("element_id=r2", "element_id=unknown"))
    assert unknown.json()["data"]["anchor_status"] == "unresolved"
    assert "record" not in unknown.json()["data"]["blocks"][0]


def test_citation_reader_uses_record_from_cited_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    data, payload = fixture()
    chunk = RetrievedChunk(
        document_id="d1",
        chunk_id="c1",
        text="",
        score=0,
        metadata={"chunk_set_id": "cs1", "element_ids": "r2"},
    )
    oracle = SimpleNamespace(
        readable_version_chunk=AsyncMock(return_value=chunk),
        chunk_set_extraction_recipe_ids=AsyncMock(return_value={"cs1": "ex1"}),
        get_document_extraction_artifact=AsyncMock(return_value={"extraction_json": payload}),
        accessible_document_exists=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(documents, "OracleClient", lambda: oracle)
    monkeypatch.setattr(documents, "_reader_structured_data", AsyncMock(return_value=data))
    client = AsgiTestClient(app)
    response = client.get("/api/documents/d1/reader?chunk_id=c1")
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == "cs1"
    assert next(b for b in response.json()["data"]["blocks"] if b["cited"])["id"] == "r2"
    oracle.readable_version_chunk.return_value = None
    assert client.get("/api/documents/d1/reader?chunk_id=c1").status_code == 404
