"""リポジトリ同梱 golden set テンプレートの契約テスト。"""

import json
from pathlib import Path

from app.rag.search_load_cli import SearchLoadScenario
from app.schemas.evaluation import EvaluationCompareRequest, EvaluationRunRequest
from app.schemas.search import SearchMode


def test_golden_set_example_matches_evaluation_run_schema() -> None:
    """example golden set は評価 API の request schema と一致する。"""
    repo_root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (repo_root / "evaluation/golden-set.example.json").read_text(encoding="utf-8")
    )

    request = EvaluationRunRequest.model_validate(payload)

    assert request.cases
    assert request.mode == SearchMode.HYBRID
    assert request.rerank_top_n <= request.top_k
    assert request.filters == {"status": "INDEXED"}
    assert request.rag_overrides is not None
    assert request.rag_overrides.rrf_k == 60
    assert request.thresholds is not None
    assert all(case.id and case.query for case in request.cases)
    assert all(case.relevant_document_ids for case in request.cases)


def test_compare_example_matches_evaluation_compare_schema() -> None:
    """compare example は複数 experiment 評価 API の request schema と一致する。"""
    repo_root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (repo_root / "evaluation/compare.example.json").read_text(encoding="utf-8")
    )

    request = EvaluationCompareRequest.model_validate(payload)

    assert request.cases
    assert request.experiments
    assert request.ranking_metric == "recall_at_k"
    assert request.thresholds is not None
    experiment_ids = [experiment.id for experiment in request.experiments]
    assert len(experiment_ids) == len(set(experiment_ids))
    assert any(experiment.mode == SearchMode.HYBRID for experiment in request.experiments)
    assert any(experiment.rag_overrides is not None for experiment in request.experiments)
    assert all(experiment.rerank_top_n <= experiment.top_k for experiment in request.experiments)


def test_search_load_example_matches_load_schema() -> None:
    """search load example は p95 gate CLI の scenario schema と一致する。"""
    repo_root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (repo_root / "evaluation/search-load.example.json").read_text(encoding="utf-8")
    )

    scenario = SearchLoadScenario.model_validate(payload)

    assert scenario.cases
    assert scenario.repeat > 0
    assert scenario.concurrency > 0
    assert scenario.thresholds.server_p95_ms is not None
    assert all(case.rerank_top_n <= case.top_k for case in scenario.cases)
