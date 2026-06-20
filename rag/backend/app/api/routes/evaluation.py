"""RAG 評価 API。"""

import hashlib
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Request

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.evaluation import EvaluationRunner
from app.rag.evaluation_adapter import normalize_evaluation_suite, resolve_evaluation_suite
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationExperiment,
    EvaluationMetrics,
    EvaluationRunRequest,
)
from app.schemas.search import parse_search_id_filter

router = APIRouter()
logger = logging.getLogger(__name__)


async def _resolve_evaluation_suite_name(
    request_suite: str | None,
    knowledge_base_ids: Sequence[str],
) -> str:
    """評価スイート名を解決順 request > KB 設定 > グローバル既定で決める。

    検索系と異なり評価は ``rag_overrides`` で RAG 構成を明示制御するため、KB の query
    上書きのうち評価固有の ``evaluation_suite`` だけを反映する。単一 KB 指定時のみ有効。
    """
    if request_suite:
        return normalize_evaluation_suite(request_suite)
    if len(knowledge_base_ids) == 1:
        knowledge_base = await OracleClient().get_knowledge_base(knowledge_base_ids[0])
        if knowledge_base is not None:
            kb_suite = knowledge_base.adapter_config.query.evaluation_suite
            if kb_suite is not None:
                return normalize_evaluation_suite(kb_suite)
    return normalize_evaluation_suite(get_settings().rag_evaluation_suite)


@router.post("/run", response_model=ApiResponse[EvaluationMetrics])
async def run_evaluation(
    http_request: Request,
    request: EvaluationRunRequest,
) -> ApiResponse[EvaluationMetrics]:
    """golden set を使って RAG 評価を実行する。"""
    enforce_rate_limit("evaluation", http_request)
    suite_name = await _resolve_evaluation_suite_name(request.suite, request.knowledge_base_ids)
    effective_thresholds = (
        request.thresholds
        if request.thresholds is not None
        else resolve_evaluation_suite(suite_name)
    )
    metrics = await EvaluationRunner().run(
        cases=request.cases,
        top_k=request.top_k,
        rerank_top_n=request.rerank_top_n,
        mode=request.mode,
        filters=request.filters,
        knowledge_base_ids=request.knowledge_base_ids,
        thresholds=effective_thresholds,
        rag_overrides=request.rag_overrides,
    )
    metrics = metrics.model_copy(update={"evaluation_suite": suite_name})
    await _save_evaluation_artifact(
        request_summary=_run_request_summary(request),
        result_summary=metrics.model_dump(mode="json"),
        knowledge_base_ids=request.knowledge_base_ids,
        best_experiment_id=None,
        passed=_metrics_passed(metrics),
    )
    return ApiResponse(data=metrics)


@router.post("/compare", response_model=ApiResponse[EvaluationCompareResponse])
async def compare_evaluation(
    http_request: Request,
    request: EvaluationCompareRequest,
) -> ApiResponse[EvaluationCompareResponse]:
    """複数 RAG 設定を同じ golden set で比較する。"""
    enforce_rate_limit("evaluation", http_request)
    suite_name = await _resolve_evaluation_suite_name(
        request.suite, _compare_knowledge_base_ids(request.experiments)
    )
    effective_thresholds = (
        request.thresholds
        if request.thresholds is not None
        else resolve_evaluation_suite(suite_name)
    )
    comparison = await EvaluationRunner().compare(
        cases=request.cases,
        experiments=request.experiments,
        ranking_metric=request.ranking_metric,
        thresholds=effective_thresholds,
    )
    await _save_evaluation_artifact(
        request_summary=_compare_request_summary(request),
        result_summary=comparison.model_dump(mode="json"),
        knowledge_base_ids=_compare_knowledge_base_ids(request.experiments),
        best_experiment_id=comparison.best_experiment_id,
        passed=_comparison_passed(comparison),
    )
    return ApiResponse(data=comparison)


async def _save_evaluation_artifact(
    *,
    request_summary: dict[str, Any],
    result_summary: dict[str, Any],
    knowledge_base_ids: Sequence[str],
    best_experiment_id: str | None,
    passed: bool,
) -> None:
    """評価 artifact を best-effort で Oracle へ保存する。"""
    try:
        await OracleClient().save_evaluation_artifact(
            {
                "request_summary": request_summary,
                "result_summary": result_summary,
                "knowledge_base_ids": list(knowledge_base_ids),
                "best_experiment_id": best_experiment_id,
                "passed": passed,
            }
        )
    except Exception as exc:
        logger.info(
            "evaluation_artifact_persistence_skipped",
            extra={"error_type": type(exc).__name__},
        )


def _run_request_summary(request: EvaluationRunRequest) -> dict[str, Any]:
    """EvaluationRunRequest から query 原文を除いた artifact summary を作る。"""
    return {
        "kind": "run",
        "case_count": len(request.cases),
        "cases": [_case_summary(case) for case in request.cases],
        "top_k": request.top_k,
        "rerank_top_n": request.rerank_top_n,
        "mode": request.mode.value,
        "filter_keys": sorted(request.filters),
        "knowledge_base_ids": request.knowledge_base_ids,
        "thresholds": (
            request.thresholds.model_dump(mode="json", exclude_none=True)
            if request.thresholds is not None
            else {}
        ),
        "rag_overrides": (
            request.rag_overrides.model_dump(mode="json", exclude_none=True)
            if request.rag_overrides is not None
            else {}
        ),
    }


def _compare_request_summary(request: EvaluationCompareRequest) -> dict[str, Any]:
    """EvaluationCompareRequest から query 原文を除いた artifact summary を作る。"""
    return {
        "kind": "compare",
        "case_count": len(request.cases),
        "cases": [_case_summary(case) for case in request.cases],
        "experiment_count": len(request.experiments),
        "experiments": [_experiment_summary(experiment) for experiment in request.experiments],
        "ranking_metric": request.ranking_metric,
        "thresholds": (
            request.thresholds.model_dump(mode="json", exclude_none=True)
            if request.thresholds is not None
            else {}
        ),
    }


def _case_summary(case: EvaluationCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "query_hash": _hash_text(case.query),
        "query_chars": len(case.query),
        "relevant_document_ids": case.relevant_document_ids,
        "expected_answer_keyword_hashes": [
            _hash_text(keyword) for keyword in case.expected_answer_keywords
        ],
        "expected_answer_keyword_count": len(case.expected_answer_keywords),
    }


def _experiment_summary(experiment: EvaluationExperiment) -> dict[str, Any]:
    return {
        "id": experiment.id,
        "top_k": experiment.top_k,
        "rerank_top_n": experiment.rerank_top_n,
        "mode": experiment.mode.value,
        "filter_keys": sorted(experiment.filters),
        "knowledge_base_ids": experiment.knowledge_base_ids,
        "rag_overrides": (
            experiment.rag_overrides.model_dump(mode="json", exclude_none=True)
            if experiment.rag_overrides is not None
            else {}
        ),
    }


def _compare_knowledge_base_ids(experiments: Sequence[EvaluationExperiment]) -> list[str]:
    values: list[str] = []
    for experiment in experiments:
        values.extend(experiment.knowledge_base_ids)
        values.extend(parse_search_id_filter(experiment.filters.get("knowledge_base_id")))
    return sorted(set(values))


def _metrics_passed(metrics: EvaluationMetrics) -> bool:
    return metrics.passed and metrics.error_count == 0 and not metrics.threshold_failures


def _comparison_passed(comparison: EvaluationCompareResponse) -> bool:
    if not comparison.results:
        return False
    best = sorted(comparison.results, key=lambda result: result.rank)[0]
    return _metrics_passed(best.metrics)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
