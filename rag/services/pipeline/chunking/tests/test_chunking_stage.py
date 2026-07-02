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


def test_run_accepts_product_maximums_and_rejects_larger_values() -> None:
    extraction = StructuredExtraction(raw_text="短い本文です。")
    accepted = client.post(
        "/run",
        content=ChunkingStageRequest(
            extraction=extraction,
            chunk_size=32_000,
            overlap=8_000,
        ).model_dump_json(),
        headers=_JSON,
    )
    rejected = client.post(
        "/run",
        json={
            "extraction": extraction.model_dump(mode="json"),
            "chunk_size": 32_001,
            "overlap": 120,
        },
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 422


def test_run_rejects_cross_field_chunk_bounds() -> None:
    extraction = StructuredExtraction(raw_text="短い本文です。")

    overlap = client.post(
        "/run",
        json={
            "extraction": extraction.model_dump(mode="json"),
            "chunk_size": 200,
            "overlap": 200,
        },
    )
    child = client.post(
        "/run",
        json={
            "extraction": extraction.model_dump(mode="json"),
            "strategy": "hierarchical_parent_child",
            "chunk_size": 320,
            "child_size": 320,
        },
    )

    assert overlap.status_code == 422
    assert child.status_code == 422
