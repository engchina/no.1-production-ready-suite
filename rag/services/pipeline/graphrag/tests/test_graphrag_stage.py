"""GraphRAG ステージサービスの契約テスト。"""

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
    assert body["profile"] == "off"
    assert body["build_entities"] is False
    assert body["build_community_summary"] is False


def test_entities_builds_relationships_only() -> None:
    resp = client.post(
        "/run", content=GraphStageRequest(profile="entities").model_dump_json(), headers=_JSON
    )
    body = resp.json()
    assert body["build_entities"] is True
    assert body["build_relationships"] is True
    assert body["build_claims"] is False


def test_full_builds_all() -> None:
    resp = client.post(
        "/run", content=GraphStageRequest(profile="full").model_dump_json(), headers=_JSON
    )
    body = resp.json()
    assert body["build_claims"] is True
    assert body["build_community_summary"] is True


def test_legacy_enabled_off_maps_to_full() -> None:
    resp = client.post(
        "/run",
        content=GraphStageRequest(profile="off", legacy_enabled=True).model_dump_json(),
        headers=_JSON,
    )
    assert resp.json()["profile"] == "full"
