"""RAG 評価 API。"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import date
from pathlib import PurePath
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pr_backend_core.bounded_calls import Attempt, BoundedCallAborted
from rag_engine.evaluation.answer_eval import standard_scope_cache
from rag_parser_core.language import current_pack
from rag_parser_core.oci_enterprise_ai import sdk_retries_disabled

from app.api.routes.search import (
    SEARCH_ANSWER_PROFILE_NOT_FOUND_PREFIX,
    ensure_search_answer_profile_has_knowledge_bases,
    ensure_search_answer_profile_knowledge_bases_permitted,
    ensure_search_answer_profile_not_archived,
    permitted_knowledge_base_ids,
)
from app.clients.ai_calls import make_ai_caller
from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oracle import OracleClient, oracle_error_log_fields
from app.config import (
    OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,
    Settings,
    fixed_today,
    get_settings,
    settings_today,
)
from app.rag.answer_engine import AS_OF_FILTER_KEY
from app.rag.evaluation import (
    EvaluationCaseResultCallback,
    EvaluationProgressCallback,
    EvaluationRunner,
)
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
    evaluation_case_id,
    normalize_file_name,
    parse_evaluation_workbook,
    template_xlsx,
)
from app.rag.evaluation_jobs import (
    EVALUATION_COMPARE_PASS_INDEX,
    EvaluationJobExecutor,
    EvaluationJobLimitError,
    EvaluationJobNotFoundError,
    EvaluationJobOutcome,
    EvaluationJobStateError,
    evaluation_answer_runs,
    evaluation_job_time_limit_seconds,
    get_evaluation_job_service,
)
from app.rag.evaluation_manifest import build_run_manifest, with_run_check
from app.rag.evaluation_paired import compare_paired
from app.rag.evaluation_process import guide_process_row, process_draft
from app.rag.evaluation_result_storage import without_answer_diagnostics
from app.rag.evaluation_results_excel import (
    evaluation_results_xlsx,
    evaluation_workbook_with_results,
)
from app.rag.evaluation_source_workbooks import (
    EvaluationSourceWorkbookNotFoundError,
    get_evaluation_source_workbook_service,
)
from app.rag.rate_limit import enforce_rate_limit
from app.rag.support_guide_process_import import (
    GuideDraftLlm,
    draft_guides,
    list_knowledge_documents,
    process_headings,
)
from app.schemas.common import ApiResponse
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationImportIssue,
    EvaluationImportResult,
    EvaluationImportRow,
    EvaluationJob,
    EvaluationJobKind,
    EvaluationMetrics,
    EvaluationProcessDraftData,
    EvaluationProcessDraftRequest,
    EvaluationResultExportRequest,
    EvaluationRunRequest,
)
from app.schemas.search import normalize_search_id_list
from app.schemas.support_guide import SupportGuideProcessRow

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
    source_workbook_id = await _usable_source_workbook_id(request)
    # 比べる検索・回答プロファイルがあれば、同じケースを 2 回流す（#2402）。
    passes = 2 if request.compare_search_answer_profile_id else 1
    time_limit = evaluation_job_time_limit_seconds(
        get_settings(), answer_runs=evaluation_answer_runs(request.cases) * passes
    )

    job_id = uuid.uuid4().hex

    def case_saver(pass_index: int, profile_id: str | None) -> EvaluationCaseResultCallback:
        async def save(*, case_index: int, result: EvaluationCaseResult) -> None:
            await get_evaluation_job_service().save_case_result(
                job_id,
                pass_index=pass_index,
                case_index=case_index,
                search_answer_profile_id=profile_id,
                result=result,
            )

        return save

    async def execute(progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        metrics, evaluation_run_id = await _execute_run(
            request, time_budget_seconds=time_limit, progress=progress, case_saver=case_saver
        )
        return EvaluationJobOutcome(
            result=metrics.model_dump(mode="json"), evaluation_run_id=evaluation_run_id
        )

    job = await _submit_job(
        kind="run",
        total_cases=len(request.cases) * passes,
        time_limit=time_limit,
        executor=execute,
        job_id=job_id,
        source_workbook_id=source_workbook_id,
    )
    return ApiResponse(data=job)


async def _usable_source_workbook_id(request: EvaluationRunRequest) -> str | None:
    """投入で指定した元の Excel を確かめて ID を返す（#4212）。

    持ち主の無いファイルは 404、行に戻せないケースは 422。保存先の障害で読めないときは、評価を
    止めずに結びつけない（結果だけのファイルになる）。
    """
    workbook_id = request.source_workbook_id
    if not workbook_id:
        return None
    try:
        workbook = await get_evaluation_source_workbook_service().load(workbook_id)
    except EvaluationSourceWorkbookNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 評価は止めない
        logger.warning("evaluation_source_workbook_read_failed", extra=oracle_error_log_fields(exc))
        return None
    parsed = await asyncio.to_thread(parse_evaluation_workbook, workbook.content)
    rows = {evaluation_case_id(row.row) for row in parsed.rows}
    if any(case.id not in rows for case in request.cases):
        raise HTTPException(
            status_code=422,
            detail="ケースがアップロードした Excel の行と合いません。もう一度検証してください。",
        )
    return workbook_id


async def _evaluation_profile_knowledge_bases(profile_id: str) -> list[str]:
    """Excel の照合範囲を検索・回答と同じ参照 KB・権限から解決する。"""
    view = await OracleClient().get_search_answer_profile(profile_id)
    if view is None:
        raise HTTPException(
            status_code=404, detail=f"{SEARCH_ANSWER_PROFILE_NOT_FOUND_PREFIX}{profile_id}"
        )
    ensure_search_answer_profile_not_archived(view, profile_id)
    ids = view.config.normalized_knowledge_base_ids()
    ensure_search_answer_profile_has_knowledge_bases(ids)
    ensure_search_answer_profile_knowledge_bases_permitted(ids)
    permitted = permitted_knowledge_base_ids(ids)
    return ids if permitted is None else permitted


@router.get("/cases/template")
async def download_evaluation_template(
    knowledge_base_ids: Annotated[list[str] | None, Query()] = None,
    search_answer_profile_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> Response:
    """評価リクエストの Excel のテンプレート（#2065。見出し・記入の説明・記入例・文書一覧）。

    `search_answer_profile_id` があれば参照 KB を解決する。
    無ければ `knowledge_base_ids` の文書を使う。
    ファイル名・文書 ID・形式・ページ数 / シート名・アップロード日を
    「文書一覧」のシートに入れる（#2086）。
    """
    kb_ids = (
        await _evaluation_profile_knowledge_bases(search_answer_profile_id)
        if search_answer_profile_id
        else normalize_search_id_list(knowledge_base_ids or [])
    )
    documents: list[EvaluationDocumentRef] = []
    if kb_ids:
        documents = await _with_document_locations(await _knowledge_base_documents(kb_ids))
    content = await asyncio.to_thread(template_xlsx, documents)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILE_NAME}"'},
    )


@router.post("/cases/import", response_model=ApiResponse[EvaluationImportResult])
async def import_evaluation_cases(
    file: Annotated[UploadFile, File()],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
    search_answer_profile_id: Annotated[str | None, Form(min_length=1, max_length=128)] = None,
) -> ApiResponse[EvaluationImportResult]:
    """評価リクエストの Excel を検証し、評価のケースにする（#2065）。

    誤りの無いファイルは、結果を足してダウンロードするために持ち主の範囲で保存する（#4212）。

    構造（見出し・必須・長さ・行数・根拠の書き方）を検証し、根拠のファイル名を選んだプロファイルの
    参照 KB（プロファイルの指定が無ければ明示の KB）の文書の ID にする。
    誤りは行・列・理由で返し（HTTP は 200）、1 つでもあればケースを返さない。
    評価の実行は、返したケースで `POST /jobs/run` を呼ぶ。
    """
    kb_ids = (
        await _evaluation_profile_knowledge_bases(search_answer_profile_id)
        if search_answer_profile_id
        else normalize_search_id_list(knowledge_base_ids or [])
    )
    if not kb_ids:
        raise HTTPException(status_code=422, detail="ナレッジベースを選んでください。")
    file_name = PurePath(file.filename or "").name or "evaluation.xlsx"
    content = await file.read(EVALUATION_EXCEL_MAX_BYTES + 1)
    result = EvaluationImportResult(
        file_name=file_name,
        row_count=0,
        search_answer_profile_id=search_answer_profile_id,
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
    warnings: list[EvaluationImportIssue] = []
    if not any(issue.row is None for issue in issues):
        if any(row.evidence for row in parsed.rows):
            documents = await _knowledge_base_documents(kb_ids)
        built = build_evaluation_cases(parsed.rows, documents)
        cases = built.cases
        issues.extend(built.issues)
        warnings = built.warnings
    issues.sort(key=lambda issue: (issue.row is not None, issue.row or 0))
    result.rows = [_preview_row(row) for row in parsed.rows]
    result.warnings = warnings[:EVALUATION_IMPORT_MAX_ISSUES]
    result.warning_count = len(warnings)
    if issues:
        return ApiResponse(data=_with_issues(result, issues))
    used = {doc_id for case in cases for doc_id in case.relevant_document_ids}
    result.cases = cases
    # 結果を足してダウンロードするため、誤りの無いファイルだけを保存する（#4212）。
    result.source_workbook_id = await get_evaluation_source_workbook_service().save(
        file_name, content
    )
    result.document_names = {
        document.document_id: document.file_name
        for document in documents
        if document.document_id in used
    }
    return ApiResponse(data=result)


def evaluation_process_llm(settings: Settings) -> GuideDraftLlm:
    """回答の過程を構造化する LLM（業務ガイドの取り込みと同じ呼び出し。テストで差し替える）。"""
    return OciEnterpriseAiClient(settings=settings)


@router.post("/cases/process-drafts", response_model=ApiResponse[EvaluationProcessDraftData])
async def draft_evaluation_process_expectations(
    http_request: Request,
    body: EvaluationProcessDraftRequest,
) -> ApiResponse[EvaluationProcessDraftData]:
    """評価のケースの回答の過程から、期待（手順・条件・根拠・対応）の下書きを作る（#2402。保存しない）。

    構造化は業務ガイドの取り込み（#2397）と同じ部品で、1 行で OCI Enterprise AI を 1 回呼ぶ（上限
    つき並行。``RAG_SUPPORT_GUIDE_IMPORT_CONCURRENCY``）。見出しの語は言語資源
    （``guide_import_cues.process_headings``）、資料の名前は選んだナレッジベースの文書で解決する。
    画面は行を ``EVALUATION_PROCESS_MAX_REQUEST_ROWS`` 行ずつに分けて送る。
    """
    enforce_rate_limit("evaluation", http_request)
    settings = get_settings()
    documents = await list_knowledge_documents(OracleClient(), body.knowledge_base_ids)
    llm = evaluation_process_llm(settings)
    caller = make_ai_caller(settings, "guide_import")
    rows = [guide_process_row(row) for row in body.rows]

    async def run(
        targets: Sequence[SupportGuideProcessRow],
        fn: Callable[[SupportGuideProcessRow, Attempt], Awaitable[str]],
    ) -> list[tuple[str | None, str | None]]:
        async def call(row: SupportGuideProcessRow, attempt: Attempt) -> str:
            with sdk_retries_disabled():
                return await fn(row, attempt)

        outcomes = await caller.map(targets, call, labels=[f"row-{row.row}" for row in targets])
        return [
            (outcome.value, None)
            if outcome.ok
            else (None, f"下書きを作れませんでした（{outcome.error}）。")
            for outcome in outcomes
        ]

    try:
        items = await draft_guides(
            rows,
            documents,
            llm=llm,
            headings=process_headings(current_pack()),
            run=run,
        )
    except BoundedCallAborted as exc:
        raise HTTPException(
            status_code=503, detail=f"回答の過程から期待の下書きを作れませんでした。{exc}"
        ) from exc
    return ApiResponse(
        data=EvaluationProcessDraftData(
            drafts=[process_draft(row, item) for row, item in zip(body.rows, items, strict=True)]
        )
    )


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
        expected_response_decision=row.response_decision,
        required_answer_points=list(row.screen_lines),
        answer_process=row.answer_process,
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
                content_type=summary.content_type,
                uploaded_at=summary.uploaded_at,
            )
    return list(documents.values())


async def _with_document_locations(
    documents: Sequence[EvaluationDocumentRef],
) -> list[EvaluationDocumentRef]:
    """「文書一覧」に出すページ数・シート名を、文書の chunk から足す（#2086）。

    取れなければ足さない。

    新しい版に置き換えた文書（旧版）は、根拠に書けないため一覧に出さない。
    """
    current = [document for document in documents if not document.superseded]
    try:
        locations = await OracleClient().evaluation_document_locations(
            [document.document_id for document in current]
        )
    except Exception as exc:  # noqa: BLE001 - 一覧の補足。テンプレートは返す
        logger.info(
            "evaluation_template_locations_skipped", extra={"error_type": type(exc).__name__}
        )
        locations = {}
    return [
        replace(
            document,
            page_count=locations[document.document_id][0],
            sheet_names=locations[document.document_id][1],
        )
        if document.document_id in locations
        else document
        for document in sorted(current, key=lambda item: normalize_file_name(item.file_name))
    ]


@router.get("/jobs/{job_id}", response_model=ApiResponse[EvaluationJob])
async def get_evaluation_job(job_id: str) -> ApiResponse[EvaluationJob]:
    """投入した利用者の品質評価の job の状態（進捗・結果）を返す（#390）。"""
    try:
        job = await get_evaluation_job_service().get(job_id)
    except EvaluationJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApiResponse(data=job)


@router.post("/jobs/{job_id}/results.xlsx")
async def export_evaluation_results(job_id: str, body: EvaluationResultExportRequest) -> Response:
    """投入した利用者の結果を出力する。

    アップロードした Excel がある job は、そのファイルに結果の列を足す（#4212）。無い job・読めない
    ときは、画面の実行時の入力と合わせた結果だけのファイルにする（`X-Evaluation-Results-Source` で
    区別する）。失敗・取り消しの job は、終わったケースまでを出す（#4208）。実行中と結果の無い
    job は 409。
    """
    job = (await get_evaluation_job(job_id)).data
    metrics = (job.run_result or job.partial_result) if job is not None else None
    if job is None or metrics is None:
        raise HTTPException(
            status_code=409, detail="結果のある品質評価（終わったもの）だけを書き出せます。"
        )
    uploaded = await _results_on_source_workbook(job, metrics)
    if uploaded is not None:
        content, file_name = uploaded
        return Response(
            content,
            media_type=XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": (
                    'attachment; filename="evaluation_results.xlsx"; '
                    f"filename*=UTF-8''{quote(file_name)}"
                ),
                "X-Evaluation-Results-Source": "uploaded",
            },
        )
    content = await asyncio.to_thread(
        evaluation_results_xlsx,
        metrics,
        body,
        partial_of_total=job.total_cases if job.run_result is None else None,
    )
    return Response(
        content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="evaluation_results.xlsx"',
            "X-Evaluation-Results-Source": "generated",
        },
    )


async def _results_on_source_workbook(
    job: EvaluationJob, metrics: EvaluationMetrics
) -> tuple[bytes, str] | None:
    """元の Excel に結果の列を足す。元の Excel が無い・足せないときは None（ログだけ）。"""
    if not job.source_workbook_id:
        return None
    try:
        workbook = await get_evaluation_source_workbook_service().load(job.source_workbook_id)
        answers = await _case_answers(metrics)
        content = await asyncio.to_thread(
            evaluation_workbook_with_results, workbook.content, metrics, answers
        )
    except Exception as exc:  # noqa: BLE001 - 結果だけのファイルを返す
        logger.warning(
            "evaluation_results_source_workbook_unavailable",
            extra={"job_id": job.job_id, **oracle_error_log_fields(exc)},
        )
        return None
    stem = PurePath(workbook.file_name).stem or "evaluation"
    return content, f"{stem}_results.xlsx"


async def _case_answers(metrics: EvaluationMetrics) -> dict[str, str]:
    """結果のケースの回答の本文。読めなければ空（回答の列だけが空になる）。"""
    results = [*metrics.case_results]
    if metrics.comparison:
        results.extend(metrics.comparison.metrics.case_results)
    try:
        return await OracleClient().evaluation_answers_by_trace_ids(
            [result.trace_id for result in results]
        )
    except Exception as exc:  # noqa: BLE001 - 指標は出す
        logger.warning("evaluation_results_answers_unavailable", extra=oracle_error_log_fields(exc))
        return {}


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
    job_id: str | None = None,
    source_workbook_id: str | None = None,
) -> EvaluationJob:
    try:
        return await get_evaluation_job_service().submit(
            kind=kind,
            total_cases=total_cases,
            time_limit_seconds=time_limit,
            executor=executor,
            job_id=job_id,
            source_workbook_id=source_workbook_id,
        )
    except EvaluationJobLimitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _execute_run(
    request: EvaluationRunRequest,
    *,
    time_budget_seconds: float,
    progress: EvaluationProgressCallback | None = None,
    case_saver: Callable[[int, str | None], EvaluationCaseResultCallback] | None = None,
) -> tuple[EvaluationMetrics, str | None]:
    """評価を実行し、評価 artifact を保存する。artifact の id（保存できなければ None）も返す。

    `case_saver` は pass の番号とプロファイルの ID から、ケースごとの結果の保存先を作る（#4208）。
    """
    suite_name = await _resolve_evaluation_suite_name(request.suite, request.knowledge_base_ids)
    effective_thresholds = (
        request.thresholds
        if request.thresholds is not None
        else resolve_evaluation_suite(suite_name)
    )
    # 評価の実行条件（#59）。基準日は明示が無ければ開始時の今日にし、評価の間は固定する
    # （日付を跨いでも全ケースが同じ日で検索・業務ガイドの期間を判定する。通常の質問は変えない）。
    explicit_as_of = (request.filters.get(AS_OF_FILTER_KEY) or "").strip()
    as_of = date.fromisoformat(explicit_as_of) if explicit_as_of else settings_today()
    settings = get_settings()

    async def manifest() -> dict[str, Any]:
        return await build_run_manifest(
            request,
            settings=settings,
            oracle=OracleClient(),
            as_of=as_of,
            as_of_source="request" if explicit_as_of else "run_start",
        )

    started_manifest = await manifest()
    # 標準回答の比較範囲は、job の 2 つの pass で同じものを使う（#3570）。
    with standard_scope_cache(), fixed_today(as_of):
        started = time.monotonic()
        metrics = await EvaluationRunner().run(
            cases=request.cases,
            top_k=request.top_k,
            filters=request.filters,
            knowledge_base_ids=request.knowledge_base_ids,
            thresholds=effective_thresholds,
            rag_overrides=request.rag_overrides,
            search_answer_profile_id=request.search_answer_profile_id,
            time_budget_seconds=time_budget_seconds,
            progress=_offset_progress(progress, offset=0, final=False)
            if request.compare_search_answer_profile_id
            else progress,
            on_case_result=case_saver(0, request.search_answer_profile_id) if case_saver else None,
        )
        metrics = metrics.model_copy(update={"evaluation_suite": suite_name})
        if request.compare_search_answer_profile_id:
            # 同じケースを検索・回答プロファイルの範囲（参照 KB・業務ガイド・ルール）でも流す
            # （#2402）。範囲はプロファイルが決めるため、ナレッジベースの指定は渡さない。
            compared = await EvaluationRunner().run(
                cases=request.cases,
                top_k=request.top_k,
                filters={
                    key: value
                    for key, value in request.filters.items()
                    if key != "knowledge_base_id"
                },
                knowledge_base_ids=[],
                thresholds=effective_thresholds,
                rag_overrides=request.rag_overrides,
                search_answer_profile_id=request.compare_search_answer_profile_id,
                time_budget_seconds=max(0.0, time_budget_seconds - (time.monotonic() - started)),
                progress=_offset_progress(progress, offset=len(request.cases), final=True),
                on_case_result=case_saver(
                    EVALUATION_COMPARE_PASS_INDEX, request.compare_search_answer_profile_id
                )
                if case_saver
                else None,
            )
            metrics = metrics.model_copy(
                update={
                    "comparison": EvaluationComparison(
                        search_answer_profile_id=request.compare_search_answer_profile_id,
                        metrics=compared.model_copy(update={"evaluation_suite": suite_name}),
                        # 同じケースの対応のある差と区間（#2576。全体の既定 → プロファイル）。
                        paired=compare_paired(metrics, compared),
                    )
                }
            )
    # 実行中に変わった条件（プロファイル・chunk_set・権限・削除など）があれば比較不能の印を付ける。
    metrics = metrics.model_copy(
        update={"run_manifest": with_run_check(started_manifest, await manifest())}
    )
    evaluation_run_id = await _save_evaluation_artifact(
        request_summary=_run_request_summary(request),
        result_summary=metrics.model_dump(mode="json"),
        knowledge_base_ids=request.knowledge_base_ids,
        passed=_metrics_passed(metrics),
    )
    return metrics, evaluation_run_id


def _offset_progress(
    progress: EvaluationProgressCallback | None, *, offset: int, final: bool
) -> EvaluationProgressCallback | None:
    """2 回流す評価（#2402）の進捗を、job 全体（ケース × 2）の数にする。

    1 回目の最後の通知（今のケース無し）は、2 回目が始まる前なので job へ渡さない（`final=False`）。
    """
    if progress is None:
        return None

    async def report(*, completed_cases: int, current_case_id: str | None) -> None:
        if current_case_id is None and not final:
            return
        await progress(completed_cases=offset + completed_cases, current_case_id=current_case_id)

    return report


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
                "result_summary": without_answer_diagnostics(result_summary),
                "knowledge_base_ids": list(knowledge_base_ids),
                "passed": passed,
            }
        )
    except Exception as exc:
        logger.warning(
            "evaluation_artifact_persistence_skipped", extra=oracle_error_log_fields(exc)
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
        "compare_search_answer_profile_id": request.compare_search_answer_profile_id,
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
        "required_answer_point_count": len(case.required_answer_points),
        "expected_response_decision": case.expected_response_decision,
        # 標準回答の本文は残さない(有無と hash だけ。#591)。
        "standard_answer_hash": (
            _hash_text(case.standard_answer) if case.standard_answer is not None else None
        ),
        # 区分・往復・条件・必要な根拠（#1284）。返答・条件の値・根拠の語句は残さない。
        "split": case.split,
        "turn_count": len(case.turns),
        "condition_ids": sorted(case.all_condition_ids()),
        "required_evidence_ids": [evidence.id for evidence in case.required_evidence],
        # 案内すべき画面の数（#2402。画面の名前は残さない）。
    }


def _metrics_passed(metrics: EvaluationMetrics) -> bool:
    return metrics.passed is True and metrics.error_count == 0 and not metrics.threshold_failures


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
