"""Retrieval ステージサービスの契約テスト。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from rag_pipeline_core.stage import RetrievalStageRequest

from app.main import app

client = TestClient(app)
_JSON = {"content-type": "application/json"}


def _run(strategy: str, expansion: bool = True) -> dict:
    req = RetrievalStageRequest(strategy=strategy, settings_query_expansion=expansion)
    resp = client.post("/run", content=req.model_dump_json(), headers=_JSON)
    assert resp.status_code == 200
    return resp.json()


def test_health_ok() -> None:
    assert client.get("/health").json()["stage"] == "retrieval"


def test_vector_forces_mode_and_disables_expansion() -> None:
    body = _run("vector", expansion=True)
    assert body["mode_override"] == "vector"
    assert body["query_expansion"] is False


def test_hybrid_uses_settings_expansion() -> None:
    assert _run("hybrid_rrf", expansion=True)["query_expansion"] is True
    assert _run("hybrid_rrf", expansion=False)["query_expansion"] is False


def test_graph_augmented_biases_strategy() -> None:
    assert _run("graph_augmented")["strategy_bias"] == "graph_global"


def test_business_context_strict_flags() -> None:
    body = _run("business_context_strict")
    assert body["gap_stop"] is True
    assert body["business_fit_weighting"] is True


def test_corrective_multi_query_flags() -> None:
    body = _run("corrective_multi_query")
    assert body["corrective_retrieval"] is True
    assert body["query_expansion"] is True


def test_unknown_strategy_falls_back() -> None:
    assert _run("bogus")["strategy"] == "hybrid_rrf"


def test_pending_strategies_resolve_without_bias_for_hybrid_degrade() -> None:
    # reasoning_tree_search / colpali_visual_retrieval は段階導入中。strategy_bias/mode_override を
    # 持たないため実行は hybrid へ安全縮退する(戦略名は選択値として保持)。
    for name in ("reasoning_tree_search", "colpali_visual_retrieval"):
        body = _run(name)
        assert body["strategy"] == name
        assert body["strategy_bias"] is None
        assert body["mode_override"] is None
