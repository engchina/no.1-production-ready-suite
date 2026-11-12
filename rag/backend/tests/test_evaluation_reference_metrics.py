"""品質評価の参考の指標（#4714）。合否と閾値には使わない。"""

from __future__ import annotations

from typing import Any

import pytest
from rag_engine.retrieval.text_search_tokenizer import surface_tokens

from app.rag.evaluation import _case_result
from app.rag.evaluation_results_excel import METRICS
from app.schemas.evaluation import (
    EVALUATION_METRIC_NAMES,
    EVALUATION_REPORT_ONLY_METRIC_NAMES,
    EvaluationCase,
)
from app.schemas.search import SearchDiagnostics, SearchResponse

REFERENCE_NAMES = {
    "precision_at_10",
    "ndcg_at_3",
    "ndcg_at_10",
    "map",
    "bleu_1",
    "bleu_2",
    "bleu_4",
    "rouge_1",
    "rouge_2",
    "rouge_l",
}


def _chunk(document_id: str) -> dict[str, Any]:
    return {
        "chunk_id": f"{document_id}:1",
        "document_id": document_id,
        "file_name": f"{document_id}.pdf",
        "text": "合成の根拠です。",
        "score": 1.0,
    }


def _response(answer: str, documents: list[str]) -> SearchResponse:
    return SearchResponse.model_validate(
        {
            "answer": answer,
            "citations": [_chunk(document) for document in documents],
            "trace_id": "trace-1",
            "elapsed_ms": 1.0,
            "diagnostics": SearchDiagnostics().model_dump(),
        }
    )


def _case(**extra: Any) -> EvaluationCase:
    return EvaluationCase.model_validate({"id": "case-1", "query": "合成の質問です。", **extra})


def test_sudachi_tokens_keep_the_negative_form() -> None:
    assert surface_tokens("行きます") != surface_tokens("行きません")
    assert "ませ" in surface_tokens("行きません") or "ません" in surface_tokens("行きません")
    assert surface_tokens("、。 ") == []


def test_ranking_metrics_keep_misses_in_the_cited_document_list() -> None:
    case = _case(relevant_document_ids=["a", "b", "c"], standard_answer="行きます。")
    result = _case_result(case, _response("行きます。", ["a", "x", "b", "y"]), None)
    assert result.precision_at_10 == pytest.approx(0.5)
    assert result.ndcg_at_3 is not None and 0 < result.ndcg_at_3 < 1
    assert result.map == pytest.approx((1 + 2 / 3) / 3)


def test_overlap_metrics_separate_affirmative_and_negative_answers() -> None:
    case = _case(standard_answer="行きます。")
    same = _case_result(case, _response("行きます。", []), None)
    negative = _case_result(case, _response("行きません。", []), None)
    assert same.bleu_1 == pytest.approx(1.0)
    assert same.rouge_1 == pytest.approx(1.0)
    assert negative.bleu_1 is not None and negative.bleu_1 < 1.0
    assert negative.rouge_1 is not None and negative.rouge_1 < 1.0
    assert negative.rouge_2 == pytest.approx(0.0)


def test_metrics_are_not_measured_without_their_inputs() -> None:
    result = _case_result(_case(), _response("行きます。", ["a"]), None)
    for name in REFERENCE_NAMES:
        assert getattr(result, name) is None, name


def test_reference_metrics_never_join_the_six_metrics() -> None:
    assert set(EVALUATION_REPORT_ONLY_METRIC_NAMES) >= REFERENCE_NAMES
    assert not REFERENCE_NAMES & set(EVALUATION_METRIC_NAMES)


def test_results_workbook_has_a_column_for_each_reference_metric() -> None:
    assert {key for key, _field, _label in METRICS} >= REFERENCE_NAMES
