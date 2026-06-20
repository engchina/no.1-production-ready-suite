"""Vector Index ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import VectorIndexStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["stage"] == "vector_index"


def test_run_balanced_uses_settings_accuracy() -> None:
    req = VectorIndexStageRequest(profile="balanced", settings_target_accuracy=95)
    resp = client.post("/run", content=req.model_dump_json(), headers=_JSON)
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"] == "balanced"
    assert body["target_accuracy"] == 95
    assert body["requires_reprovision"] is False


def test_run_accurate_requires_reprovision() -> None:
    req = VectorIndexStageRequest(profile="accurate", settings_target_accuracy=95)
    resp = client.post("/run", content=req.model_dump_json(), headers=_JSON)
    body = resp.json()
    assert body["target_accuracy"] == 98
    assert body["neighbors"] == 48
    assert body["requires_reprovision"] is True


def test_run_unknown_profile_falls_back_to_balanced() -> None:
    req = VectorIndexStageRequest(profile="bogus", settings_target_accuracy=90)
    resp = client.post("/run", content=req.model_dump_json(), headers=_JSON)
    assert resp.json()["profile"] == "balanced"
