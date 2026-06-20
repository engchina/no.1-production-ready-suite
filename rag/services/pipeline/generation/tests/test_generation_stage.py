"""Generation ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import GenerationStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def _run(profile: str) -> dict:
    resp = client.post(
        "/run", content=GenerationStageRequest(profile=profile).model_dump_json(), headers=_JSON
    )
    assert resp.status_code == 200
    return resp.json()


def test_health_ok() -> None:
    assert client.get("/health").json()["stage"] == "generation"


def test_grounded_concise_uses_client_default_prompt() -> None:
    body = _run("grounded_concise")
    assert body["system_prompt"] is None
    assert body["structured_output"] is False


def test_structured_json_sets_flag() -> None:
    body = _run("structured_json")
    assert body["structured_output"] is True


def test_inline_cited_profile_has_sentence_attribution_prompt() -> None:
    body = _run("inline_cited")
    assert body["system_prompt"] is not None
    assert "逐句" in body["system_prompt"]


def test_unknown_profile_falls_back() -> None:
    assert _run("bogus")["profile"] == "grounded_concise"
