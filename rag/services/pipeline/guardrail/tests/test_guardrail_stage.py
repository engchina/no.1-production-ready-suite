"""Guardrail ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import GuardrailStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def _run(policy: str) -> dict:
    resp = client.post(
        "/run", content=GuardrailStageRequest(policy=policy).model_dump_json(), headers=_JSON
    )
    assert resp.status_code == 200
    return resp.json()


def test_health_ok() -> None:
    assert client.get("/health").json()["stage"] == "guardrail"


def test_standard_uses_default_thresholds() -> None:
    body = _run("standard")
    assert body["grounding_min_overlap"] == 3
    assert body["audit_emphasis"] is False


def test_strict_tightens_groundedness() -> None:
    body = _run("strict")
    assert body["grounding_min_overlap"] == 5
    assert body["grounding_min_ratio"] == 0.30


def test_regulated_sets_audit_emphasis() -> None:
    assert _run("regulated")["audit_emphasis"] is True


def test_unknown_policy_falls_back() -> None:
    assert _run("bogus")["policy"] == "standard"
