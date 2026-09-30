"""関係情報の構築ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import GraphStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def test_health_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["stage"] == "graphrag"


def test_off_builds_nothing() -> None:
    resp = client.post(
        "/run", content=GraphStageRequest(profile="off").model_dump_json(), headers=_JSON
    )
    body = resp.json()
    assert body == {"profile": "off", "build_entities": False, "build_relationships": False}


def test_entities_builds_entities_and_relationships() -> None:
    resp = client.post(
        "/run", content=GraphStageRequest(profile="entities").model_dump_json(), headers=_JSON
    )
    body = resp.json()
    assert body == {"profile": "entities", "build_entities": True, "build_relationships": True}


def test_removed_full_profile_resolves_to_off() -> None:
    """削除した full(#621)は未知の値として既定 off へ寄せる。"""
    resp = client.post(
        "/run", content=GraphStageRequest(profile="full").model_dump_json(), headers=_JSON
    )
    assert resp.json()["profile"] == "off"
