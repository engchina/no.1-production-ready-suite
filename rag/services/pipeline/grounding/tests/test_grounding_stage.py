"""Grounding ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import GroundingStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def _run(pipeline: str) -> dict:
    resp = client.post(
        "/run", content=GroundingStageRequest(pipeline=pipeline).model_dump_json(), headers=_JSON
    )
    assert resp.status_code == 200
    return resp.json()


def test_health_ok() -> None:
    assert client.get("/health").json()["stage"] == "grounding"


def test_verified_context_diversity_and_corrective() -> None:
    body = _run("verified_context")
    assert body["diversity"] is True
    assert body["corrective"] is True


def test_full_governed_enables_all() -> None:
    body = _run("full_governed")
    assert body["dependency_promotion"] is True
    assert body["compression"] is True
    assert body["expansion_mode"] == "adaptive"


def test_lean_is_minimal() -> None:
    body = _run("lean")
    assert body["diversity"] is False
    assert body["compression"] is False


def test_unknown_pipeline_falls_back_to_custom() -> None:
    assert _run("bogus")["pipeline"] == "custom"
