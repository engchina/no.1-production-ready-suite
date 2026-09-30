"""Evaluation ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import EvaluationStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def _run(suite: str) -> dict:
    resp = client.post(
        "/run", content=EvaluationStageRequest(suite=suite).model_dump_json(), headers=_JSON
    )
    assert resp.status_code == 200
    return resp.json()


def test_health_ok() -> None:
    assert client.get("/health").json()["stage"] == "evaluation"


def test_standard_thresholds() -> None:
    body = _run("standard")
    assert body["thresholds"]["context_recall"] == 0.8
    assert body["thresholds"]["refusal_accuracy"] == 0.9


def test_strict_thresholds() -> None:
    body = _run("strict")
    assert body["thresholds"]["mrr"] == 0.8
    assert body["thresholds"]["claim_support_rate"] == 1.0


def test_legacy_suite_maps_to_successor() -> None:
    assert _run("strict_ci")["suite"] == "strict"
    assert _run("ragas_like")["suite"] == "standard"


def test_unknown_suite_falls_back() -> None:
    assert _run("bogus")["suite"] == "standard"
