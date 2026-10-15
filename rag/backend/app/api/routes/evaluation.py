"""RAG 評価 API。"""

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from app.clients.oracle import OracleClient
from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, get_settings
from app.rag.evaluation import EvaluationProgressCallback, EvaluationRunner
from app.rag.evaluation_adapter import normalize_evaluation_suite, resolve_evaluation_suite
from app.rag.evaluation_excel import (
    EVALUATION_EXCEL_MAX_BYTES,
    EVALUATION_EXCEL_MAX_ROWS,
    EVALUATION_IMPORT_MAX_ISSUES,
    TEMPLATE_FILE_NAME,
    XLSX_MEDIA_TYPE,
    EvaluationDocumentRef,
    EvaluationExcelRow,
    build_evaluation_cases,
    parse_evaluation_workbook,
    template_xlsx,
)
from app.rag.evaluation_jobs import (
    EvaluationJobExecutor,
    EvaluationJobLimitError,
    EvaluationJobNotFoundError,
    EvaluationJobOutcome,
    EvaluationJobStateError,
    evaluation_answer_runs,
    evaluation_job_time_limit_seconds,
    get_evaluation_job_service,
)
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationImportIssue,
    EvaluationImportResult,
    EvaluationImportRow,
    EvaluationJob,
    EvaluationJobKind,
    EvaluationMetrics,
    EvaluationRunRequest,
)
from app.schemas.search import normalize_search_id_list

router = APIRouter()
logger = logging.getLogger(__name__)
# 同期の品質評価（golden set。`/run`）の全体の時間の上限（秒。#383）。評価は同期の
# HTTP で、1 ケースごとに回答生成（`rag_answer_timeout_seconds`）を行うため、ケース数 × 上限では
# Nginx の待ちを超える。保存済みの回答の評価（#304）と同じく、LLM 1 回の timeout の設定の上限
# （`OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS` = 600 秒）で打ち切り、上限に達した後のケースは実行せずに
# 失敗として返す。Nginx の待ち時間（660 秒。`init_script.sh` が生成する設定）はこれより長くし、
# 打ち切った結果を呼び出し元に届ける。
# 画面と評価 CLI は job の API（`/jobs/run`。#390）に移した。job の全体の上限は
# 回答の回数に比例させる（`evaluation_job_time_limit_seconds`。#1430）。同期の API は、外部から
# 直接呼ぶ利用のために残す。
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
    time_limit = evaluation_job_time_limit_seconds(
        get_settings(), answer_runs=evaluation_answer_runs(request.cases)
    )

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


@router.get("/cases/template")
async def download_evaluation_template() -> Response:
    """評価リクエストの Excel のテンプレート（#2065。見出しと「記入の説明」のシート）。"""
    content = await asyncio.to_thread(template_xlsx)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILE_NAME}"'},
    )


@router.post("/cases/import", response_model=ApiResponse[EvaluationImportResult])
async def import_evaluation_cases(
    file: Annotated[UploadFile, File()],
    knowledge_base_ids: Annotated[list[str], Form()],
) -> ApiResponse[EvaluationImportResult]:
    """評価リクエストの Excel を検証し、評価のケースにする（#2065。保存しない）。

    構造（見出し・必須・長さ・行数・根拠の書き方）を検証し、根拠のファイル名を選んだナレッジベースの
    文書の ID にする。誤りは行・列・理由で返し（HTTP は 200）、1 つでもあればケースを返さない。
    評価の実行は、返したケースで `POST /jobs/run` を呼ぶ。
    """
    kb_ids = normalize_search_id_list(knowledge_base_ids)
    if not kb_ids:
        raise HTTPException(status_code=422, detail="ナレッジベースを選んでください。")
    file_name = PurePath(file.filename or "").name or "evaluation.xlsx"
    content = await file.read(EVALUATION_EXCEL_MAX_BYTES + 1)
    result = EvaluationImportResult(
        file_name=file_name,
        row_count=0,
        knowledge_base_ids=kb_ids,
        max_rows=EVALUATION_EXCEL_MAX_ROWS,
        max_bytes=EVALUATION_EXCEL_MAX_BYTES,
    )
    if not file_name.lower().endswith(".xlsx"):
        return ApiResponse(
            data=_with_issues(
                result,
                [
                    EvaluationImportIssue(
                        message="Excel（.xlsx）のファイルを選んでください。テンプレートを使えます。"
                    )
                ],
            )
        )
    parsed = await asyncio.to_thread(parse_evaluation_workbook, content)
    result.row_count = len(parsed.rows)
    issues = list(parsed.issues)
    cases: list[EvaluationCase] = []
    documents: list[EvaluationDocumentRef] = []
    if not any(issue.row is None for issue in issues):
        if any(row.evidence for row in parsed.rows):
            documents = await _knowledge_base_documents(kb_ids)
        cases, resolve_issues = build_evaluation_cases(parsed.rows, documents)
        issues.extend(resolve_issues)
    issues.sort(key=lambda issue: (issue.row is not None, issue.row or 0))
    result.rows = [_preview_row(row) for row in parsed.rows]
    if issues:
        return ApiResponse(data=_with_issues(result, issues))
    used = {doc_id for case in cases for doc_id in case.relevant_document_ids}
    result.cases = cases
    result.document_names = {
        document.document_id: document.file_name
        for document in documents
        if document.document_id in used
    }
    return ApiResponse(data=result)


def _with_issues(
    result: EvaluationImportResult, issues: Sequence[EvaluationImportIssue]
) -> EvaluationImportResult:
    return result.model_copy(
        update={
            "cases": [],
            "errors": list(issues[:EVALUATION_IMPORT_MAX_ISSUES]),
            "error_count": len(issues),
        }
    )


# 標準回答のプレビューの長さ（文字）。
_STANDARD_ANSWER_PREVIEW_CHARS = 200


def _preview_row(row: EvaluationExcelRow) -> EvaluationImportRow:
    answer = row.standard_answer
    return EvaluationImportRow(
        row=row.row,
        case_id=row.case_id,
        question=row.question,
        evidence=[spec.line for spec in row.evidence],
        standard_answer_preview=(
            answer[:_STANDARD_ANSWER_PREVIEW_CHARS]
            + ("…" if len(answer) > _STANDARD_ANSWER_PREVIEW_CHARS else "")
            if answer
            else None
        ),
        has_standard_answer=answer is not None,
        expects_answer=bool(row.evidence or answer),
    )


async def _knowledge_base_documents(
    knowledge_base_ids: Sequence[str],
) -> list[EvaluationDocumentRef]:
    """選んだナレッジベースの文書（利用者が見られるもの）。"""
    oracle = OracleClient()
    documents: dict[str, EvaluationDocumentRef] = {}
    for knowledge_base_id in knowledge_base_ids:
        for summary in await oracle.list_documents(knowledge_base_id=knowledge_base_id):
            documents[summary.id] = EvaluationDocumentRef(
                document_id=summary.id,
                file_name=summary.file_name,
                superseded=summary.is_superseded,
                superseded_by_file_name=summary.superseded_by_file_name,
            )
    return list(documents.values())


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
        passed=_metrics_passed(metrics),
    )
    return metrics, evaluation_run_id


async def _save_evaluation_artifact(
    *,
    request_summary: dict[str, Any],
    result_summary: dict[str, Any],
    knowledge_base_ids: Sequence[str],
    passed: bool,
) -> str | None:
    """評価 artifact を best-effort で Oracle へ保存し、id を返す（保存できなければ None）。"""
    try:
        return await OracleClient().save_evaluation_artifact(
            {
                "request_summary": request_summary,
                "result_summary": result_summary,
                "knowledge_base_ids": list(knowledge_base_ids),
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


def _metrics_passed(metrics: EvaluationMetrics) -> bool:
    return metrics.passed and metrics.error_count == 0 and not metrics.threshold_failures


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
