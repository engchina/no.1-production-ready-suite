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


def test_request_only_has_no_thresholds() -> None:
    body = _run("request_only")
    assert body["thresholds"] is None


def test_strict_ci_thresholds() -> None:
    body = _run("strict_ci")
    assert body["thresholds"]["precision_at_k"] == 0.7
    assert body["thresholds"]["citation_traceability_coverage"] == 0.9


def test_ragas_like_thresholds() -> None:
    body = _run("ragas_like")
    assert body["thresholds"]["faithfulness"] == 0.8
    assert body["thresholds"]["context_recall"] == 0.8


def test_unknown_suite_falls_back() -> None:
    assert _run("bogus")["suite"] == "request_only"
