"""RAG 評価 API。"""

from fastapi import APIRouter, Request

from app.rag.evaluation import EvaluationRunner
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.evaluation import EvaluationMetrics, EvaluationRunRequest

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
        thresholds=request.thresholds,
    )
    return ApiResponse(data=metrics)
