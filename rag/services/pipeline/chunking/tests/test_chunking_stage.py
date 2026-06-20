"""Chunking ステージサービスの契約テスト(FastAPI TestClient)。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_parser_core.extraction import StructuredExtraction
from rag_pipeline_core.stage import ChunkingStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["stage"] == "chunking"


def test_run_chunks_extraction() -> None:
    extraction = StructuredExtraction(
        raw_text="第1章 概要\n" + "これはテスト本文です。" * 30,
        document_type="ドキュメント",
    )
    request = ChunkingStageRequest(
        extraction=extraction, strategy="structure_aware", chunk_size=200
    )
    resp = client.post("/run", content=request.model_dump_json(), headers=_JSON)
    assert resp.status_code == 200
    chunks = resp.json()["chunks"]
    assert len(chunks) >= 1
    assert all("text" in c and "index" in c for c in chunks)
    # chunk_strategy metadata が刻まれる(backend と同一ロジック)。
    assert any(c["metadata"].get("chunk_strategy") == "structure_aware" for c in chunks)


def test_run_empty_extraction_returns_no_chunks() -> None:
    request = ChunkingStageRequest(extraction=StructuredExtraction(raw_text=""))
    resp = client.post("/run", content=request.model_dump_json(), headers=_JSON)
    assert resp.status_code == 200
    assert resp.json()["chunks"] == []
