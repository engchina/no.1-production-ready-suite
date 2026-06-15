"""RAG 評価 API。"""

from fastapi import APIRouter, Request

from app.rag.evaluation import EvaluationRunner
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.evaluation import (
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationMetrics,
    EvaluationRunRequest,
)

router = APIRouter()


@router.post("/run", response_model=ApiResponse[EvaluationMetrics])
async def run_evaluation(
    http_request: Request,
    request: EvaluationRunRequest,
) -> ApiResponse[EvaluationMetrics]:
    """golden set を使って RAG 評価を実行する。"""
    enforce_rate_limit("evaluation", http_request)
    metrics = await EvaluationRunner().run(
        cases=request.cases,
        top_k=request.top_k,
        rerank_top_n=request.rerank_top_n,
        mode=request.mode,
        filters=request.filters,
        knowledge_base_ids=request.knowledge_base_ids,
        thresholds=request.thresholds,
        rag_overrides=request.rag_overrides,
    )
    return ApiResponse(data=metrics)


@router.post("/compare", response_model=ApiResponse[EvaluationCompareResponse])
async def compare_evaluation(
    http_request: Request,
    request: EvaluationCompareRequest,
) -> ApiResponse[EvaluationCompareResponse]:
    """複数 RAG 設定を同じ golden set で比較する。"""
    enforce_rate_limit("evaluation", http_request)
    comparison = await EvaluationRunner().compare(
        cases=request.cases,
        experiments=request.experiments,
        ranking_metric=request.ranking_metric,
        thresholds=request.thresholds,
    )
    return ApiResponse(data=comparison)
