"""RAG 評価 API。"""

import hashlib
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.clients.oracle import OracleClient
from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, get_settings
from app.rag.evaluation import EvaluationProgressCallback, EvaluationRunner
from app.rag.evaluation_adapter import normalize_evaluation_suite, resolve_evaluation_suite
from app.rag.evaluation_jobs import (
    EvaluationJobExecutor,
    EvaluationJobLimitError,
    EvaluationJobNotFoundError,
    EvaluationJobOutcome,
    EvaluationJobStateError,
    get_evaluation_job_service,
)
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCompareRequest,
    EvaluationCompareResponse,
    EvaluationExperiment,
    EvaluationJob,
    EvaluationJobKind,
    EvaluationMetrics,
    EvaluationRunRequest,
)
from app.schemas.search import parse_search_id_filter

router = APIRouter()
logger = logging.getLogger(__name__)
# 同期の品質評価（golden set。`/run`・`/compare`）の全体の時間の上限（秒。#383）。評価は同期の
# HTTP で、1 ケースごとに回答生成（`rag_answer_timeout_seconds`）を行うため、ケース数 × 上限では
# Nginx の待ちを超える。保存済みの回答の評価（#304）と同じく、LLM 1 回の timeout の設定の上限
# （`OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS` = 600 秒）で打ち切り、上限に達した後のケースは実行せずに
# 失敗として返す。Nginx の待ち時間（660 秒。`init_script.sh` が生成する設定）はこれより長くし、
# 打ち切った結果を呼び出し元に届ける。
# 画面と評価 CLI は job の API（`/jobs/run`・`/jobs/compare`。#390）に移した。job の全体の上限は
# `rag_evaluation_job_timeout_seconds`。同期の API は、外部から直接呼ぶ利用のために残す。
EVALUATION_RUN_TIMEOUT_SECONDS = OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS


async def _resolve_evaluation_suite_name(
    request_suite: str | None,
    knowledge_base_ids: Sequence[str],
) -> str:
    """評価スイート名を request > グローバル既定で決める。

    KB はナレッジ構築設定だけを持つため、KB に残る legacy query 設定は評価にも反映しない。
    ``knowledge_base_ids`` は signature 互換のため受け取る。
    """
    _ = knowledge_base_ids
    if request_suite:
        return normalize_evaluation_suite(request_suite)
    return normalize_evaluation_suite(get_settings().rag_evaluation_suite)


@router.post("/run", response_model=ApiResponse[EvaluationMetrics])
async def run_evaluation(
    http_request: Request,
    request: EvaluationRunRequest,
) -> ApiResponse[EvaluationMetrics]:
    """golden set を使って RAG 評価を実行する（同期。互換のため残す）。

    評価全体を `EVALUATION_RUN_TIMEOUT_SECONDS` で打ち切る。画面・評価 CLI は job の API
    （`POST /jobs/run`。#390）を使う。
    """
    enforce_rate_limit("evaluation", http_request)
    metrics, _ = await _execute_run(request, time_budget_seconds=EVALUATION_RUN_TIMEOUT_SECONDS)
    return ApiResponse(data=metrics)


@router.post("/compare", response_model=ApiResponse[EvaluationCompareResponse])
async def compare_evaluation(
    http_request: Request,
    request: EvaluationCompareRequest,
) -> ApiResponse[EvaluationCompareResponse]:
    """複数 RAG 設定を同じ golden set で比較する（同期。互換のため残す）。

    画面・評価 CLI は job の API（`POST /jobs/compare`。#390）を使う。
    """
    enforce_rate_limit("evaluation", http_request)
    comparison, _ = await _execute_compare(
        request, time_budget_seconds=EVALUATION_RUN_TIMEOUT_SECONDS
    )
    return ApiResponse(data=comparison)


@router.post(
    "/jobs/run",
    response_model=ApiResponse[EvaluationJob],
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_run_evaluation_job(
    http_request: Request,
    request: EvaluationRunRequest,
) -> ApiResponse[EvaluationJob]:
    """golden set の評価を job として投入する（#390）。進捗は `GET /jobs/{job_id}` で取得する。"""
    enforce_rate_limit("evaluation", http_request)
    time_limit = get_settings().rag_evaluation_job_timeout_seconds

    async def execute(progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        metrics, evaluation_run_id = await _execute_run(
            request, time_budget_seconds=time_limit, progress=progress
        )
        return EvaluationJobOutcome(
            result=metrics.model_dump(mode="json"), evaluation_run_id=evaluation_run_id
        )

    job = await _submit_job(
        kind="run", total_cases=len(request.cases), time_limit=time_limit, executor=execute
    )
    return ApiResponse(data=job)


@router.post(
    "/jobs/compare",
    response_model=ApiResponse[EvaluationJob],
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_compare_evaluation_job(
    http_request: Request,
    request: EvaluationCompareRequest,
) -> ApiResponse[EvaluationJob]:
    """複数 RAG 設定の比較を job として投入する（#390）。件数は experiment × ケース。"""
    enforce_rate_limit("evaluation", http_request)
    time_limit = get_settings().rag_evaluation_job_timeout_seconds

    async def execute(progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        comparison, evaluation_run_id = await _execute_compare(
            request, time_budget_seconds=time_limit, progress=progress
        )
        return EvaluationJobOutcome(
            result=comparison.model_dump(mode="json"), evaluation_run_id=evaluation_run_id
        )

    job = await _submit_job(
        kind="compare",
        total_cases=len(request.cases) * len(request.experiments),
        time_limit=time_limit,
        executor=execute,
    )
    return ApiResponse(data=job)


@router.get("/jobs/{job_id}", response_model=ApiResponse[EvaluationJob])
async def get_evaluation_job(job_id: str) -> ApiResponse[EvaluationJob]:
    """投入した利用者の品質評価の job の状態（進捗・結果）を返す（#390）。"""
    try:
        job = await get_evaluation_job_service().get(job_id)
    except EvaluationJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(data=job)


@router.post("/jobs/{job_id}/cancel", response_model=ApiResponse[EvaluationJob])
async def cancel_evaluation_job(job_id: str) -> ApiResponse[EvaluationJob]:
    """投入した利用者の実行中の品質評価の job を取り消す（#390）。"""
    service = get_evaluation_job_service()
    try:
        job = await service.cancel(job_id)
    except EvaluationJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvaluationJobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(data=job)


async def _submit_job(
    *,
    kind: EvaluationJobKind,
    total_cases: int,
    time_limit: int,
    executor: EvaluationJobExecutor,
) -> EvaluationJob:
    try:
        return await get_evaluation_job_service().submit(
            kind=kind,
            total_cases=total_cases,
            time_limit_seconds=time_limit,
            executor=executor,
        )
    except EvaluationJobLimitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _execute_run(
    request: EvaluationRunRequest,
    *,
    time_budget_seconds: float,
    progress: EvaluationProgressCallback | None = None,
) -> tuple[EvaluationMetrics, str | None]:
    """評価を実行し、評価 artifact を保存する。artifact の id（保存できなければ None）も返す。"""
    suite_name = await _resolve_evaluation_suite_name(request.suite, request.knowledge_base_ids)
    effective_thresholds = (
        request.thresholds
        if request.thresholds is not None
        else resolve_evaluation_suite(suite_name)
    )
    metrics = await EvaluationRunner().run(
        cases=request.cases,
        top_k=request.top_k,
        filters=request.filters,
        knowledge_base_ids=request.knowledge_base_ids,
        thresholds=effective_thresholds,
        rag_overrides=request.rag_overrides,
        search_answer_profile_id=request.search_answer_profile_id,
        time_budget_seconds=time_budget_seconds,
        progress=progress,
    )
    metrics = metrics.model_copy(update={"evaluation_suite": suite_name})
    evaluation_run_id = await _save_evaluation_artifact(
        request_summary=_run_request_summary(request),
        result_summary=metrics.model_dump(mode="json"),
        knowledge_base_ids=request.knowledge_base_ids,
        best_experiment_id=None,
        passed=_metrics_passed(metrics),
    )
    return metrics, evaluation_run_id


async def _execute_compare(
    request: EvaluationCompareRequest,
    *,
    time_budget_seconds: float,
    progress: EvaluationProgressCallback | None = None,
) -> tuple[EvaluationCompareResponse, str | None]:
    """比較を実行し、評価 artifact を保存する。artifact の id（保存できなければ None）も返す。"""
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
        time_budget_seconds=time_budget_seconds,
        progress=progress,
    )
    # /run と同じく、確定した評価スイートを各 experiment の metrics に残す。
    comparison = comparison.model_copy(
        update={
            "results": [
                result.model_copy(
                    update={
                        "metrics": result.metrics.model_copy(
                            update={"evaluation_suite": suite_name}
                        )
                    }
                )
                for result in comparison.results
            ]
        }
    )
    evaluation_run_id = await _save_evaluation_artifact(
        request_summary=_compare_request_summary(request),
        result_summary=comparison.model_dump(mode="json"),
        knowledge_base_ids=_compare_knowledge_base_ids(request.experiments),
        best_experiment_id=comparison.best_experiment_id,
        passed=_comparison_passed(comparison),
    )
    return comparison, evaluation_run_id


async def _save_evaluation_artifact(
    *,
    request_summary: dict[str, Any],
    result_summary: dict[str, Any],
    knowledge_base_ids: Sequence[str],
    best_experiment_id: str | None,
    passed: bool,
) -> str | None:
    """評価 artifact を best-effort で Oracle へ保存し、id を返す（保存できなければ None）。"""
    try:
        return await OracleClient().save_evaluation_artifact(
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
        return None


def _run_request_summary(request: EvaluationRunRequest) -> dict[str, Any]:
    """EvaluationRunRequest から query 原文を除いた artifact summary を作る。"""
    return {
        "kind": "run",
        "case_count": len(request.cases),
        "cases": [_case_summary(case) for case in request.cases],
        "top_k": request.top_k,
        "filter_keys": sorted(request.filters),
        "knowledge_base_ids": request.knowledge_base_ids,
        "search_answer_profile_id": request.search_answer_profile_id,
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
        # 標準回答の本文は残さない(有無と hash だけ。#591)。
        "standard_answer_hash": (
            _hash_text(case.standard_answer) if case.standard_answer is not None else None
        ),
        # 区分・往復・条件・必要な根拠（#1284）。返答・条件の値・根拠の語句は残さない。
        "split": case.split,
        "turn_count": len(case.turns),
        "condition_ids": sorted(case.all_condition_ids()),
        "required_evidence_ids": [evidence.id for evidence in case.required_evidence],
    }


def _experiment_summary(experiment: EvaluationExperiment) -> dict[str, Any]:
    return {
        "id": experiment.id,
        "top_k": experiment.top_k,
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
