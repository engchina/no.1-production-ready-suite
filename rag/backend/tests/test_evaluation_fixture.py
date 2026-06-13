"""リポジトリ同梱 golden set テンプレートの契約テスト。"""

import json
from pathlib import Path

from app.schemas.evaluation import EvaluationRunRequest
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
    assert request.filters == {"status": "REGISTERED"}
    assert request.thresholds is not None
    assert all(case.id and case.query for case in request.cases)
    assert all(case.relevant_document_ids for case in request.cases)
