"""Agentic ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import AgenticStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def _run(profile: str) -> dict:
    resp = client.post(
        "/run", content=AgenticStageRequest(profile=profile).model_dump_json(), headers=_JSON
    )
    assert resp.status_code == 200
    return resp.json()


def test_health_ok() -> None:
    assert client.get("/health").json()["stage"] == "agentic"


def test_off_disables_planning() -> None:
    body = _run("off")
    assert body["enabled"] is False
    assert body["smart_routing"] is False


def test_smart_routing_enables_rewrite_path() -> None:
    body = _run("smart_routing")
    assert body["enabled"] is True
    assert body["rewrite"] is True
    assert body["smart_routing"] is True


def test_multi_hop_sets_flags() -> None:
    body = _run("multi_hop")
    assert body["decompose"] is True
    assert body["multi_hop"] is True


def test_unknown_profile_falls_back() -> None:
    assert _run("bogus")["profile"] == "off"
