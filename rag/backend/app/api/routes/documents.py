"""ドキュメント API。アップロード・一覧・取込(抽出→索引)。"""

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from html import escape
from pathlib import PurePath
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import uuid4

from charset_normalizer import from_bytes
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.clients.object_storage import ObjectStorageClient
from app.clients.oci_genai import EMBEDDING_INPUT_MAX_CHARS
from app.clients.oracle import DocumentDeleteBlockedByRunningIngestionError, OracleClient
from app.config import CHUNKING_STRATEGIES_WITH_MIN_CHARS, Settings, get_settings
from app.db_degradation import load_or_degrade
from app.rag.chunking import Chunk, chunk_extraction_with_strategy
from app.rag.extraction_field_adapter import load_field_schema
from app.rag.ingestion import (
    IngestionCancelledError,
    IngestionPipeline,
    IngestionTimeoutError,
    IngestionUserError,
)
from app.rag.ingestion_worker import request_ingestion_worker_wakeup
from app.rag.kb_adapter_config import (
    KbAdapterConfigError,
    KnowledgeBaseAdapterConfig,
    resolve_effective_adapter_config,
    resolve_effective_settings,
)
from app.rag.navigation import build_navigation_tree
from app.rag.rate_limit import enforce_rate_limit
from app.rag.source_profile import build_source_profile
from app.rag.variant_keys import (
    compute_chunk_set_id,
    compute_document_recipe_extraction_id,
    compute_extraction_recipe_id,
    extraction_recipe_subset,
)
from app.rag.variant_planner import MaterializationPlan, plan_document_materializations
from app.schemas.common import ApiResponse, Page
from app.schemas.document import (
    BatchUploadFailedItem,
    BatchUploadResult,
    ChunkSetExperimentRequest,
    DocumentApproveRequest,
    DocumentChunkPreviewRequest,
    DocumentChunkPreviewResponse,
    DocumentChunkPreviewStats,
    DocumentChunkSet,
    DocumentChunkSetLayerStatuses,
    DocumentChunkView,
    DocumentDeleteResult,
    DocumentDetail,
    DocumentExtractionExport,
    DocumentExtractionExportFormat,
    DocumentIngestionConfigData,
    DocumentLayerStatusName,
    DocumentMaterializationLayerStatus,
    DocumentPreprocessArtifact,
    DocumentProcessingConfig,
    DocumentRecipeCreateRequest,
    DocumentRecipeDeleteResult,
    DocumentRecipeStep,
    DocumentRecipeStepStatus,
    DocumentRecipeView,
    DocumentReviewEditsRequest,
    DocumentStats,
    DocumentSummary,
    DocumentTableCellTextEdit,
    DuplicateDocumentRef,
    FileStatus,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
    IngestionSegment,
    ParserExtractionExperimentRequest,
    SourceProfile,
    UploadResult,
)
from app.schemas.extraction import (
    MARKDOWN_HEADING,
    NUMBERED_HEADING,
    SEARCHABLE_ELEMENT_KINDS,
    DocumentElement,
    DocumentNavigationNode,
    ExtractionAsset,
    ExtractionField,
    ExtractionTable,
    ExtractionTableCell,
    StructuredExtraction,
)
from app.schemas.knowledge_base import (
    DocumentKnowledgeBaseReplaceRequest,
    KnowledgeBaseRef,
)
from app.schemas.search import normalize_search_id_list

router = APIRouter()
logger = logging.getLogger(__name__)
SOURCE_SIZE_MISMATCH_MESSAGE = "原本ファイルのサイズがアップロード時と一致しません。"
SOURCE_HASH_MISMATCH_MESSAGE = "原本ファイルの SHA-256 がアップロード時と一致しません。"
INGESTION_JOB_CANCELLED_MESSAGE = "利用者によりキャンセルされました。"
CHUNK_SET_PUBLISH_ERROR_MESSAGE = "索引の公開設定に失敗しました。時間をおいて再実行してください。"
DELETE_BLOCKING_INGESTION_STATUSES = frozenset({IngestionJobStatus.RUNNING})
DOCUMENT_PROCESSING_EDITABLE_STATUSES = frozenset(
    {FileStatus.UPLOADED, FileStatus.INDEXED, FileStatus.ERROR}
)
DOCUMENT_PROCESSING_OUTPUT_GROUPS: dict[str, tuple[str, ...]] = {
    "preprocess_profile": ("preprocess_profile",),
    "parser_adapter_backend": (
        "parser_adapter_backend",
        "parser_docling_enabled",
        "parser_marker_enabled",
        "parser_unstructured_enabled",
        "parser_unlimited_ocr_enabled",
        "parser_mineru_enabled",
        "parser_dots_ocr_enabled",
        "parser_glm_ocr_enabled",
    ),
    "chunking_strategy": (
        "chunking_strategy",
        "chunk_size",
        "chunk_overlap",
        "chunk_child_size",
        "chunk_min_chars",
        "chunk_context_header_enabled",
    ),
    "graph_profile": ("graph_profile",),
    "field_extraction_enabled": ("field_extraction_enabled",),
    "asset_summary_enabled": ("asset_summary_enabled",),
    "navigation_summary_enabled": ("navigation_summary_enabled",),
}


class UploadIngestionMode(StrEnum):
    """アップロード後の取込開始方針。"""

    MANUAL = "manual"


@router.post("/upload", response_model=ApiResponse[UploadResult])
async def upload_document(
    http_request: Request,
    file: Annotated[UploadFile, File(...)],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
    ingestion_mode: Annotated[UploadIngestionMode, Form()] = UploadIngestionMode.MANUAL,
) -> ApiResponse[UploadResult]:
    """ドキュメントファイルをアップロードし、Object Storage へ保管する。"""
    enforce_rate_limit("upload", http_request)
    result = await _store_uploaded_document(file, knowledge_base_ids)
    _ = ingestion_mode
    return ApiResponse(data=result)


@router.post("/batch-upload", response_model=ApiResponse[BatchUploadResult])
async def batch_upload_documents(
    http_request: Request,
    files: Annotated[list[UploadFile], File(...)],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
    ingestion_mode: Annotated[UploadIngestionMode, Form()] = UploadIngestionMode.MANUAL,
) -> ApiResponse[BatchUploadResult]:
    """複数ドキュメントをまとめてアップロードし、Object Storage へ保管する。"""
    enforce_rate_limit("upload", http_request)
    if not files:
        raise HTTPException(status_code=400, detail="アップロード対象ファイルを選択してください。")
    items: list[UploadResult] = []
    failed_items: list[BatchUploadFailedItem] = []
    for file in files:
        try:
            result = await _store_uploaded_document(file, knowledge_base_ids)
            _ = ingestion_mode
            items.append(result)
        except HTTPException as exc:
            source_profile = await _failed_upload_source_profile(file)
            failed_items.append(
                BatchUploadFailedItem(
                    file_name=_safe_display_filename(file.filename),
                    status_code=exc.status_code,
                    message=str(exc.detail),
                    source_profile=source_profile,
                )
            )
        except Exception:
            logger.exception(
                "batch_upload_item_failed",
                extra={"file_name": _safe_display_filename(file.filename)},
            )
            source_profile = await _failed_upload_source_profile(file)
            failed_items.append(
                BatchUploadFailedItem(
                    file_name=_safe_display_filename(file.filename),
                    status_code=500,
                    message="アップロード処理に失敗しました。",
                    source_profile=source_profile,
                )
            )
    return ApiResponse(
        data=BatchUploadResult(
            items=items,
            failed_items=failed_items,
            total_count=len(files),
            uploaded_count=len(items),
            failed_count=len(failed_items),
            queued_count=sum(
                1
                for item in items
                if item.ingestion_job is not None
                and item.ingestion_job.status == IngestionJobStatus.QUEUED
            ),
            skipped_count=sum(
                1
                for item in items
                if item.ingestion_job is not None
                and item.ingestion_job.status == IngestionJobStatus.SKIPPED
            ),
        )
    )


async def _failed_upload_source_profile(file: UploadFile) -> SourceProfile | None:
    """batch upload の失敗 item に返す source profile を best-effort で作る。"""
    settings = get_settings()
    original_file_name = file.filename or "document.bin"
    file_name = _safe_display_filename(original_file_name)
    content_type = _normalized_content_type(file.content_type)
    data: bytes | None = None
    file_size_bytes = _upload_file_size_hint(file)
    content_sha256 = ""
    try:
        if file_size_bytes is None or file_size_bytes <= settings.max_upload_bytes:
            await file.seek(0)
            data = await file.read(settings.max_upload_bytes + 1)
            await file.seek(0)
            file_size_bytes = len(data)
            if len(data) <= settings.max_upload_bytes:
                content_sha256 = _sha256_hex(data)
            else:
                data = None
    except Exception:
        data = None
    try:
        return build_source_profile(
            original_file_name=original_file_name,
            sanitized_file_name=file_name,
            content_type=content_type,
            file_size_bytes=file_size_bytes or 0,
            content_sha256=content_sha256,
            duplicate_of_document_id=None,
            data=data,
        )
    except Exception:
        return None


def _upload_file_size_hint(file: UploadFile) -> int | None:
    """Starlette UploadFile の size hint を安全に読む。"""
    size = getattr(file, "size", None)
    if isinstance(size, bool) or not isinstance(size, int):
        return None
    return max(size, 0)


def _is_allowed_upload_content_type(
    content_type: str,
    *,
    sanitized_file_name: str,
    allowed_content_types: list[str],
) -> bool:
    """MIME whitelist と拡張子 profile を組み合わせて upload 可否を判定する。"""
    normalized_allowed = {_normalized_content_type(allowed) for allowed in allowed_content_types}
    if content_type not in normalized_allowed:
        return False
    if content_type != "application/octet-stream":
        return True
    profile = build_source_profile(
        original_file_name=sanitized_file_name,
        sanitized_file_name=sanitized_file_name,
        content_type=content_type,
        file_size_bytes=0,
        content_sha256="",
        data=None,
    )
    return profile.unsupported_reason != "unknown_file_type"


async def _store_uploaded_document(
    file: UploadFile,
    knowledge_base_ids: list[str] | None,
) -> UploadResult:
    """単一 UploadFile を保存し、取込前の upload result を返す。"""
    settings = get_settings()
    content_type = _normalized_content_type(file.content_type)
    original_file_name = file.filename or "document.bin"
    file_name = _safe_display_filename(original_file_name)
    if not _is_allowed_upload_content_type(
        content_type,
        sanitized_file_name=file_name,
        allowed_content_types=settings.allowed_upload_content_types,
    ):
        raise HTTPException(status_code=415, detail="対応していないファイル形式です。")

    data = await _read_upload_file(file, settings.max_upload_bytes)
    if not data:
        raise HTTPException(status_code=400, detail="空のファイルはアップロードできません。")

    storage = ObjectStorageClient()
    oracle = OracleClient()
    content_sha256 = _sha256_hex(data)
    selected_knowledge_base_ids = _normalize_upload_knowledge_base_ids(knowledge_base_ids)
    duplicate = await oracle.find_document_by_content_hash(content_sha256)
    source_profile = build_source_profile(
        original_file_name=original_file_name,
        sanitized_file_name=file_name,
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256=content_sha256,
        duplicate_of_document_id=duplicate.id if duplicate is not None else None,
        data=data,
    )
    key = f"uploaded/{uuid4().hex}/{file_name}"
    object_path = await storage.put(
        key=key,
        data=data,
        content_type=content_type,
    )
    try:
        detail = await oracle.create_document(
            file_name=file_name,
            object_storage_path=object_path,
            content_type=content_type,
            file_size_bytes=len(data),
            content_sha256=content_sha256,
            duplicate_of_document_id=duplicate.id if duplicate is not None else None,
            knowledge_base_ids=selected_knowledge_base_ids or None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UploadResult(
        id=detail.id,
        file_name=detail.file_name,
        status=detail.status,
        file_size_bytes=detail.file_size_bytes or len(data),
        content_sha256=content_sha256,
        duplicate_of_document_id=detail.duplicate_of_document_id,
        knowledge_bases=detail.knowledge_bases,
        source_profile=source_profile,
    )


@router.get("", response_model=ApiResponse[Page[DocumentSummary]])
async def list_documents(
    status: FileStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    knowledge_base_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[DocumentSummary]]:
    """取込対象ドキュメントの一覧を返す。DB 停止時は空一覧 + warning で縮退する。"""
    oracle = OracleClient()
    settings = get_settings()

    async def _load() -> Page[DocumentSummary]:
        documents = await oracle.list_documents(
            status=status,
            query=q,
            limit=limit,
            offset=offset,
            knowledge_base_id=knowledge_base_id,
        )
        total = await oracle.count_documents(
            status=status,
            query=q,
            knowledge_base_id=knowledge_base_id,
        )
        return Page(
            items=documents,
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )

    empty_page: Page[DocumentSummary] = Page(
        items=[], total=0, limit=limit, offset=offset, has_next=False
    )
    page, degraded = await load_or_degrade(
        _load,
        timeout_seconds=settings.db_read_timeout_seconds,
        fallback=empty_page,
        log_label="documents_list",
    )
    return ApiResponse(
        data=page,
        warning_messages=[degraded.message] if degraded else [],
    )


@router.get("/stats", response_model=ApiResponse[DocumentStats])
async def document_stats() -> ApiResponse[DocumentStats]:
    """ドキュメント状態別の集計を返す。DB 停止時はゼロ集計 + warning で縮退する。"""
    settings = get_settings()
    stats, degraded = await load_or_degrade(
        OracleClient().document_stats,
        timeout_seconds=settings.db_read_timeout_seconds,
        fallback=DocumentStats(total=0, by_status={}),
        log_label="document_stats",
    )
    return ApiResponse(
        data=stats,
        warning_messages=[degraded.message] if degraded else [],
    )


@router.get("/ingestion-jobs", response_model=ApiResponse[Page[IngestionJob]])
async def list_ingestion_jobs(
    status: Annotated[IngestionJobStatus | None, Query()] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[IngestionJob]]:
    """直近の取込 job 一覧を返す。DB 停止時は空一覧 + warning で縮退する。"""
    oracle = OracleClient()
    settings = get_settings()

    async def _load() -> Page[IngestionJob]:
        page_items = await oracle.list_ingestion_jobs(status=status, limit=limit, offset=offset)
        total = await oracle.count_ingestion_jobs(status=status)
        return Page(
            items=page_items,
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )

    empty_page: Page[IngestionJob] = Page(
        items=[], total=0, limit=limit, offset=offset, has_next=False
    )
    page, degraded = await load_or_degrade(
        _load,
        timeout_seconds=settings.db_read_timeout_seconds,
        fallback=empty_page,
        log_label="ingestion_jobs_list",
    )
    return ApiResponse(
        data=page,
        warning_messages=[degraded.message] if degraded else [],
    )


@router.post("/ingestion-jobs/drain", response_model=ApiResponse[list[IngestionJob]])
async def drain_queued_ingestion_jobs(
    http_request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> ApiResponse[list[IngestionJob]]:
    """永続化済み QUEUED job をバックグラウンド実行へ戻す。"""
    enforce_rate_limit("ingest", http_request)
    jobs = await OracleClient().list_ingestion_jobs(
        status=IngestionJobStatus.QUEUED,
        limit=limit,
        offset=0,
    )
    for job in jobs:
        _dispatch_ingestion_job(job.id)
    return ApiResponse(data=jobs)


@router.post("/ingestion-jobs/{job_id}/retry", response_model=ApiResponse[IngestionJob])
async def retry_ingestion_job(
    http_request: Request,
    job_id: str,
    force: bool = Query(default=False),
) -> ApiResponse[IngestionJob]:
    """完了済みまたは失敗済み job の対象文書を新しい job として再投入する。"""
    enforce_rate_limit("ingest", http_request)
    job = await OracleClient().get_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="取込ジョブが見つかりません。")
    if job.status in {IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="この取込ジョブはまだ実行中です。")
    retry_job = await _enqueue_ingestion_job_for_document(
        job.document_id,
        force=force or job.status == IngestionJobStatus.FAILED,
        phase=job.phase,
    )
    return ApiResponse(data=retry_job)


@router.post("/ingestion-jobs/{job_id}/cancel", response_model=ApiResponse[IngestionJob])
async def cancel_ingestion_job(
    http_request: Request,
    job_id: str,
) -> ApiResponse[IngestionJob]:
    """待機中または実行中の取込 job をキャンセル済みにする。"""
    enforce_rate_limit("ingest", http_request)
    oracle = OracleClient()
    job = await oracle.get_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="取込ジョブが見つかりません。")
    if job.status not in {IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="この取込ジョブはキャンセルできません。")
    cancelled = await oracle.update_ingestion_job(
        job_id,
        status=IngestionJobStatus.CANCELLED,
        error_message=INGESTION_JOB_CANCELLED_MESSAGE,
        finished_at=datetime.now(UTC),
    )
    if cancelled is None:
        raise HTTPException(status_code=404, detail="取込ジョブが見つかりません。")
    if job.status == IngestionJobStatus.RUNNING:
        restore_status = _restore_status_for_cancelled_phase(job.phase)
        await oracle.update_document_status(job.document_id, restore_status)
    return ApiResponse(data=cancelled)


@router.get("/ingestion-jobs/{job_id}", response_model=ApiResponse[IngestionJob])
async def get_ingestion_job(job_id: str) -> ApiResponse[IngestionJob]:
    """指定した取込 job の現在状態を返す。"""
    job = await OracleClient().get_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="取込ジョブが見つかりません。")
    return ApiResponse(data=job)


@router.post("/{document_id}/ingestion-jobs", response_model=ApiResponse[IngestionJob])
async def enqueue_document_ingestion_job(
    http_request: Request,
    document_id: str,
    force: bool = Query(default=False),
    phase: Annotated[IngestionJobPhase, Query()] = IngestionJobPhase.PREPROCESS,
) -> ApiResponse[IngestionJob]:
    """保存済みドキュメントを取込 job としてキュー投入する。"""
    enforce_rate_limit("ingest", http_request)
    job = await _enqueue_ingestion_job_for_document(document_id, force=force, phase=phase)
    return ApiResponse(data=job)


@router.get("/{document_id}/ingestion-jobs", response_model=ApiResponse[list[IngestionJob]])
async def list_document_ingestion_jobs(
    document_id: str,
) -> ApiResponse[list[IngestionJob]]:
    """文書 workspace 用に、この文書の取込 job 履歴を新しい順で返す。"""
    oracle = OracleClient()
    if await oracle.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=await oracle.list_document_ingestion_jobs(document_id))


@router.post(
    "/{document_id}/ingestion-segments/retry",
    response_model=ApiResponse[IngestionJob],
)
async def retry_failed_document_ingestion_segments(
    http_request: Request,
    document_id: str,
    recipe_id: str | None = Query(default=None),
) -> ApiResponse[IngestionJob]:
    """FAILED checkpoint がある文書だけ、失敗 segment 再試行 job として再投入する。"""
    enforce_rate_limit("ingest", http_request)
    job = await _enqueue_failed_segment_retry_job_for_document(
        document_id,
        recipe_id=recipe_id,
    )
    return ApiResponse(data=job)


@router.get("/{document_id}/chunks", response_model=ApiResponse[list[DocumentChunkView]])
async def list_document_chunks(document_id: str) -> ApiResponse[list[DocumentChunkView]]:
    """文書 preview workspace 用に chunk/citation metadata を返す。"""
    oracle = OracleClient()
    if await oracle.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=await oracle.list_document_chunks(document_id))


_RECIPE_PHASES = (
    IngestionJobPhase.PREPROCESS,
    IngestionJobPhase.EXTRACT,
    IngestionJobPhase.CHUNK,
    IngestionJobPhase.INDEX,
)

_PHASE_TO_RUNNING_STATUS = {
    IngestionJobPhase.PREPROCESS: FileStatus.PREPROCESSING,
    IngestionJobPhase.EXTRACT: FileStatus.INGESTING,
    IngestionJobPhase.CHUNK: FileStatus.CHUNKING,
    IngestionJobPhase.INDEX: FileStatus.INDEXING,
}
_RUNNING_STATUS_TO_PHASE = {status: phase for phase, status in _PHASE_TO_RUNNING_STATUS.items()}

_STEP_PENDING = DocumentRecipeStepStatus.PENDING
_STEP_RUNNING = DocumentRecipeStepStatus.RUNNING
_STEP_SUCCEEDED = DocumentRecipeStepStatus.SUCCEEDED
_STEP_NEEDS_REVIEW = DocumentRecipeStepStatus.NEEDS_REVIEW

# レシピ行 status を単一状態源として 4 工程の表示状態を導出する行列。
# 1 本のジョブが複数工程を通し実行するため、ジョブ行の phase/status からは
# 「いまどの工程か」を判定できない(pipeline が工程ごとにレシピ status を更新する)。
_RECIPE_STEP_MATRIX: dict[FileStatus, tuple[DocumentRecipeStepStatus, ...]] = {
    FileStatus.UPLOADED: (_STEP_PENDING, _STEP_PENDING, _STEP_PENDING, _STEP_PENDING),
    FileStatus.PREPROCESSING: (_STEP_RUNNING, _STEP_PENDING, _STEP_PENDING, _STEP_PENDING),
    FileStatus.PREPROCESSED: (_STEP_SUCCEEDED, _STEP_PENDING, _STEP_PENDING, _STEP_PENDING),
    FileStatus.INGESTING: (_STEP_SUCCEEDED, _STEP_RUNNING, _STEP_PENDING, _STEP_PENDING),
    FileStatus.REVIEW: (_STEP_SUCCEEDED, _STEP_NEEDS_REVIEW, _STEP_PENDING, _STEP_PENDING),
    FileStatus.CHUNKING: (_STEP_SUCCEEDED, _STEP_SUCCEEDED, _STEP_RUNNING, _STEP_PENDING),
    FileStatus.CHUNKED: (_STEP_SUCCEEDED, _STEP_SUCCEEDED, _STEP_NEEDS_REVIEW, _STEP_PENDING),
    FileStatus.INDEXING: (_STEP_SUCCEEDED, _STEP_SUCCEEDED, _STEP_SUCCEEDED, _STEP_RUNNING),
    FileStatus.INDEXED: (_STEP_SUCCEEDED, _STEP_SUCCEEDED, _STEP_SUCCEEDED, _STEP_SUCCEEDED),
}


def _recipe_steps(row: Mapping[str, object], jobs: list[IngestionJob]) -> list[DocumentRecipeStep]:
    recipe_status = FileStatus(str(row.get("status") or FileStatus.UPLOADED.value))
    raw_failed_phase = row.get("failed_phase")
    failed_phase = IngestionJobPhase(str(raw_failed_phase)) if raw_failed_phase else None
    latest_by_phase: dict[IngestionJobPhase, IngestionJob] = {}
    for job in jobs:
        latest_by_phase.setdefault(job.phase, job)
    latest_failed = next((job for job in jobs if job.status == IngestionJobStatus.FAILED), None)
    if recipe_status == FileStatus.ERROR:
        failed = failed_phase or (
            latest_failed.phase if latest_failed is not None else IngestionJobPhase.PREPROCESS
        )
        failed_index = _RECIPE_PHASES.index(failed)
        statuses = tuple(
            (
                _STEP_SUCCEEDED
                if i < failed_index
                else DocumentRecipeStepStatus.FAILED if i == failed_index else _STEP_PENDING
            )
            for i in range(len(_RECIPE_PHASES))
        )
    else:
        statuses = _RECIPE_STEP_MATRIX.get(recipe_status, (_STEP_PENDING,) * len(_RECIPE_PHASES))
    newest = jobs[0] if jobs else None
    result: list[DocumentRecipeStep] = []
    for phase, status in zip(_RECIPE_PHASES, statuses, strict=True):
        latest_job = latest_by_phase.get(phase)
        if (
            newest is not None
            and newest.status == IngestionJobStatus.QUEUED
            and newest.phase == phase
        ):
            # enqueue→claim 間はレシピ status がまだ前値のため、最新ジョブでだけ補正する。
            status = DocumentRecipeStepStatus.QUEUED
        error_message: str | None = None
        if status == DocumentRecipeStepStatus.FAILED:
            # 通しジョブの失敗では失敗工程にジョブ行が無いことがあるため、
            # 最新 FAILED ジョブのメッセージへフォールバックする。
            error_message = (latest_job.error_message if latest_job is not None else None) or (
                latest_failed.error_message if latest_failed is not None else None
            )
        result.append(
            DocumentRecipeStep(
                phase=phase,
                status=status,
                started_at=latest_job.started_at if latest_job is not None else None,
                finished_at=latest_job.finished_at if latest_job is not None else None,
                error_message=error_message,
            )
        )
    return result


async def _document_recipe_view(
    oracle: OracleClient,
    row: Mapping[str, object],
    *,
    document_jobs: Sequence[IngestionJob] | None = None,
) -> DocumentRecipeView:
    config = DocumentProcessingConfig.model_validate(row.get("processing_config") or {})
    _, effective = _merge_document_processing_config(config)
    recipe_id = str(row["recipe_id"])
    all_jobs = (
        document_jobs
        if document_jobs is not None
        else await oracle.list_document_ingestion_jobs(str(row["document_id"]))
    )
    jobs = [job for job in all_jobs if job.recipe_id == recipe_id]
    active_chunk_set_id = (
        str(row["active_chunk_set_id"]) if row.get("active_chunk_set_id") is not None else None
    )
    config_revision = int(str(row.get("config_revision") or 1))
    materialized_revision = (
        int(str(row["materialized_revision"]))
        if row.get("materialized_revision") is not None
        else None
    )
    return DocumentRecipeView(
        recipe_id=recipe_id,
        document_id=str(row["document_id"]),
        slot_no=int(str(row["slot_no"])),
        status=FileStatus(str(row.get("status") or FileStatus.UPLOADED.value)),
        failed_phase=(
            IngestionJobPhase(str(row["failed_phase"]))
            if row.get("failed_phase") is not None
            else None
        ),
        processing_config=config,
        effective_processing_config=effective,
        preprocess_artifact=(
            DocumentPreprocessArtifact.model_validate(row["preprocess_artifact"])
            if row.get("preprocess_artifact")
            else None
        ),
        active_extraction_recipe_id=(
            str(row["active_extraction_recipe_id"])
            if row.get("active_extraction_recipe_id") is not None
            else None
        ),
        active_chunk_set_id=active_chunk_set_id,
        chunk_count=int(str(row.get("chunk_count") or 0)),
        vector_count=int(str(row.get("vector_count") or 0)),
        config_revision=config_revision,
        materialized_revision=materialized_revision,
        searchable=(
            active_chunk_set_id is not None and str(row.get("chunk_set_status")) == "INDEXED"
        ),
        needs_reprocessing=(
            materialized_revision is not None and config_revision != materialized_revision
        ),
        error_message=(str(row["error_message"]) if row.get("error_message") else None),
        steps=_recipe_steps(row, jobs),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


@router.get("/{document_id}/recipes", response_model=ApiResponse[list[DocumentRecipeView]])
async def list_document_recipes(document_id: str) -> ApiResponse[list[DocumentRecipeView]]:
    """文書の 1〜3 件の独立レシピを返す。"""
    oracle = OracleClient()
    try:
        rows = await oracle.list_document_recipes(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。") from exc
    jobs = await oracle.list_document_ingestion_jobs(document_id)
    return ApiResponse(
        data=[await _document_recipe_view(oracle, row, document_jobs=jobs) for row in rows]
    )


@router.post("/{document_id}/recipes", response_model=ApiResponse[DocumentRecipeView])
async def create_document_recipe(
    document_id: str, request: DocumentRecipeCreateRequest
) -> ApiResponse[DocumentRecipeView]:
    """空き slot にレシピを追加する。"""
    oracle = OracleClient()
    try:
        row = await oracle.create_document_recipe(
            document_id, copy_from_recipe_id=request.copy_from_recipe_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(data=await _document_recipe_view(oracle, row))


@router.put("/{document_id}/recipes/{recipe_id}", response_model=ApiResponse[DocumentRecipeView])
async def update_document_recipe(
    document_id: str, recipe_id: str, request: DocumentProcessingConfig
) -> ApiResponse[DocumentRecipeView]:
    """選択レシピの明示設定を保存する。"""
    try:
        _merge_document_processing_config(request)
    except KbAdapterConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    oracle = OracleClient()
    try:
        await oracle.update_document_recipe_config(document_id, recipe_id, request)
        row = await oracle.get_document_recipe(document_id, recipe_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    return ApiResponse(data=await _document_recipe_view(oracle, row))


@router.delete(
    "/{document_id}/recipes/{recipe_id}",
    response_model=ApiResponse[DocumentRecipeDeleteResult],
)
async def delete_document_recipe(
    document_id: str, recipe_id: str
) -> ApiResponse[DocumentRecipeDeleteResult]:
    """最後の1件を保護してレシピと固有索引を削除する。"""
    oracle = OracleClient()
    try:
        removed = await oracle.delete_document_recipe(document_id, recipe_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(
        data=DocumentRecipeDeleteResult(
            recipe_id=recipe_id,
            document_id=document_id,
            removed_chunk_set_count=removed,
        )
    )


@router.post(
    "/{document_id}/recipes/{recipe_id}/ingestion-jobs",
    response_model=ApiResponse[IngestionJob],
)
async def enqueue_document_recipe_job(
    document_id: str,
    recipe_id: str,
    phase: IngestionJobPhase = IngestionJobPhase.PREPROCESS,
) -> ApiResponse[IngestionJob]:
    """レシピ設定の snapshot を持つ独立 job を投入する。文書内実行は worker が直列化する。"""
    try:
        job = await _enqueue_ingestion_job_for_recipe(document_id, recipe_id, phase=phase)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(data=job)


async def _enqueue_ingestion_job_for_recipe(
    document_id: str,
    recipe_id: str,
    *,
    phase: IngestionJobPhase,
) -> IngestionJob:
    """snapshot を作り、Oracle 側の行ロック検証後に recipe job を投入する。"""
    oracle = OracleClient()
    row = await oracle.get_document_recipe(document_id, recipe_id)
    detail = await oracle.get_document(document_id)
    if row is None or detail is None:
        raise KeyError("レシピが見つかりません。")
    config = DocumentProcessingConfig.model_validate(row.get("processing_config") or {})
    effective_settings, _ = _merge_document_processing_config(config)
    source_profile = _source_profile_for_detail(detail)
    job = await _create_ingestion_job_record(
        oracle=oracle,
        document_id=document_id,
        recipe_id=recipe_id,
        recipe_revision=int(str(row.get("config_revision") or 1)),
        parser_profile=source_profile.parser_profile,
        quality_warnings=source_profile.quality_warnings,
        phase=phase,
        settings_overrides={
            "processing_config": config.model_dump(mode="json", exclude_none=True),
            "rag_preprocess_profile": effective_settings.rag_preprocess_profile,
            "rag_parser_adapter_backend": effective_settings.rag_parser_adapter_backend,
        },
    )
    _dispatch_ingestion_job(job.id)
    return job


@router.get(
    "/{document_id}/recipes/{recipe_id}/chunks",
    response_model=ApiResponse[list[DocumentChunkView]],
)
async def list_document_recipe_chunks(
    document_id: str, recipe_id: str
) -> ApiResponse[list[DocumentChunkView]]:
    oracle = OracleClient()
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    chunk_set_id = row.get("active_chunk_set_id")
    return ApiResponse(
        data=(await oracle.list_chunk_set_chunks(str(chunk_set_id)) if chunk_set_id else [])
    )


@router.post(
    "/{document_id}/recipes/{recipe_id}/chunk-preview",
    response_model=ApiResponse[DocumentChunkPreviewResponse],
)
async def preview_document_recipe_chunks(
    http_request: Request,
    document_id: str,
    recipe_id: str,
    request: DocumentChunkPreviewRequest | None = None,
) -> ApiResponse[DocumentChunkPreviewResponse]:
    """保存済み抽出を一時設定で分割し、DB・job・工程状態を変更せず返す。"""
    enforce_rate_limit("ingest", http_request)
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if detail is None or row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    status = FileStatus(str(row.get("status")))
    if status not in {FileStatus.REVIEW, FileStatus.CHUNKED}:
        raise HTTPException(
            status_code=409,
            detail="確認待ちまたは分割確認待ちのレシピのみプレビューできます。",
        )
    extraction_recipe_id = row.get("active_extraction_recipe_id")
    artifact = (
        await oracle.get_document_extraction_artifact(
            document_id=document_id,
            extraction_recipe_id=str(extraction_recipe_id),
        )
        if extraction_recipe_id
        else None
    )
    if artifact is None or not artifact.get("extraction_json"):
        raise HTTPException(status_code=409, detail="再利用できる抽出結果がありません。")

    config = DocumentProcessingConfig.model_validate(row.get("processing_config") or {})
    settings, _ = _merge_document_processing_config(config)
    candidate = _candidate_chunking_settings(
        settings,
        (request or DocumentChunkPreviewRequest()).settings_overrides(),
    )
    extraction = StructuredExtraction.model_validate(artifact["extraction_json"])
    try:
        chunks = chunk_extraction_with_strategy(
            extraction,
            strategy=candidate.rag_chunking_strategy,
            chunk_size=candidate.rag_chunk_size,
            overlap=candidate.rag_chunk_overlap,
            child_size=candidate.rag_chunk_child_size,
            min_chars=candidate.rag_chunk_min_chars,
            delimiter=candidate.rag_chunk_delimiter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    views: list[DocumentChunkView] = []
    search_lengths: list[int] = []
    for chunk in chunks:
        metadata = dict(chunk.metadata)
        section_path = str(metadata.get("section_path") or "").strip()
        header = " > ".join(part for part in (detail.file_name.strip(), section_path) if part)
        if candidate.rag_chunk_context_header_enabled and header:
            metadata["context_header"] = header
        else:
            metadata.pop("context_header", None)
        search_lengths.append(
            len(f"{header}\n{chunk.text}")
            if candidate.rag_chunk_context_header_enabled and header
            else len(chunk.text)
        )
        views.append(_preview_chunk_view(document_id, recipe_id, chunk, metadata))

    lengths = [len(chunk.text) for chunk in chunks]
    overflow_count = sum(
        1
        for chunk in chunks
        if chunk.metadata.get("chunk_size_compliance") in {"overflow", "overflow_justified"}
    )
    embedding_overflow_count = sum(
        1 for length in search_lengths if length > EMBEDDING_INPUT_MAX_CHARS
    )
    warnings: list[str] = []
    if overflow_count:
        warnings.append(f"設定サイズを超える chunk が {overflow_count} 件あります。")
    if embedding_overflow_count:
        warnings.append(
            f"embedding 入力上限を超える chunk が {embedding_overflow_count} 件あります。"
        )
    return ApiResponse(
        data=DocumentChunkPreviewResponse(
            chunks=views,
            stats=DocumentChunkPreviewStats(
                chunk_count=len(chunks),
                min_chars=min(lengths, default=0),
                average_chars=(round(sum(lengths) / len(lengths), 1) if lengths else 0),
                max_chars=max(lengths, default=0),
                overflow_count=overflow_count,
                embedding_overflow_count=embedding_overflow_count,
            ),
            warnings=warnings,
        )
    )


def _preview_chunk_view(
    document_id: str,
    recipe_id: str,
    chunk: Chunk,
    metadata: dict[str, str | int | float | bool | None],
) -> DocumentChunkView:
    """永続化前の Chunk を既存 UI view へ写す。"""

    def optional_int(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float, str)):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    bbox: list[float] | None = None
    raw_bbox = metadata.get("bbox")
    try:
        parsed_bbox = json.loads(raw_bbox) if isinstance(raw_bbox, str) else raw_bbox
        if isinstance(parsed_bbox, list) and len(parsed_bbox) == 4:
            bbox = [float(value) for value in parsed_bbox]
    except (TypeError, ValueError, json.JSONDecodeError):
        bbox = None
    element_ids = [
        value.strip()
        for value in str(metadata.get("element_ids") or "").split(",")
        if value.strip()
    ]
    page_start = optional_int(metadata.get("page_start")) or optional_int(
        metadata.get("page_number")
    )
    return DocumentChunkView(
        document_id=document_id,
        chunk_id=f"preview:{recipe_id}:{chunk.index}",
        chunk_index=chunk.index,
        text=chunk.text,
        page_start=page_start,
        page_end=optional_int(metadata.get("page_end")) or page_start,
        bbox=bbox,
        section_path=str(metadata["section_path"]) if metadata.get("section_path") else None,
        content_kind=str(metadata["content_kind"]) if metadata.get("content_kind") else None,
        chunk_group_id=(
            str(metadata["chunk_group_id"]) if metadata.get("chunk_group_id") else None
        ),
        source_parser=(str(metadata["source_parser"]) if metadata.get("source_parser") else None),
        element_ids=element_ids,
        metadata=metadata,
    )


@router.get("/{document_id}/recipes/{recipe_id}/content")
async def document_recipe_content(
    document_id: str,
    recipe_id: str,
    variant: Annotated[Literal["original", "prepared"], Query()] = "original",
    disposition: Annotated[Literal["inline", "attachment"], Query()] = "inline",
) -> Response:
    """選択レシピの原本または固有のファイル準備 artifact を返す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if detail is None or row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    artifact = (
        DocumentPreprocessArtifact.model_validate(row["preprocess_artifact"])
        if row.get("preprocess_artifact")
        else None
    )
    return await _document_content_response(
        detail,
        variant=variant,
        disposition=disposition,
        preprocess_artifact=artifact,
    )


@router.get(
    "/{document_id}/recipes/{recipe_id}/extraction-export",
    response_model=ApiResponse[DocumentExtractionExport],
)
async def export_document_recipe_extraction(
    document_id: str,
    recipe_id: str,
    format: Annotated[DocumentExtractionExportFormat, Query()] = (
        DocumentExtractionExportFormat.MARKDOWN
    ),
) -> ApiResponse[DocumentExtractionExport]:
    """選択レシピの抽出・active chunks を監査用に返す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if detail is None or row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    extraction_recipe_id = row.get("active_extraction_recipe_id")
    artifact = (
        await oracle.get_document_extraction_artifact(
            document_id=document_id,
            extraction_recipe_id=str(extraction_recipe_id),
        )
        if extraction_recipe_id
        else None
    )
    if artifact is None or not artifact.get("extraction_json"):
        raise HTTPException(status_code=404, detail="抽出結果が見つかりません。")
    extraction = StructuredExtraction.model_validate(artifact["extraction_json"])
    payload = extraction.to_document_payload()
    chunks: list[DocumentChunkView] = []
    if format == DocumentExtractionExportFormat.CHUNKS:
        chunk_set_id = row.get("active_chunk_set_id")
        chunks = await oracle.list_chunk_set_chunks(str(chunk_set_id)) if chunk_set_id else []
        payload = {"chunks": [chunk.model_dump(mode="json") for chunk in chunks]}
    content = _document_extraction_export_content(format, extraction, payload)
    return ApiResponse(
        data=DocumentExtractionExport(
            document_id=document_id,
            file_name=detail.file_name,
            format=format,
            content_type=_document_extraction_export_content_type(format),
            content=content,
            payload=(
                payload
                if format
                not in {
                    DocumentExtractionExportFormat.MARKDOWN,
                    DocumentExtractionExportFormat.HTML,
                }
                else {}
            ),
            chunks=chunks,
            parser_backend=_extraction_parser_backend(extraction),
            parser_profile=_extraction_parser_profile(extraction),
            page_count=len(extraction.pages),
            element_count=len(extraction.elements),
            table_count=len(extraction.tables),
            asset_count=len(extraction.assets),
        )
    )


@router.post(
    "/{document_id}/recipes/{recipe_id}/approve",
    response_model=ApiResponse[IngestionJob],
)
async def approve_document_recipe(
    http_request: Request,
    document_id: str,
    recipe_id: str,
    body: DocumentApproveRequest | None = None,
) -> ApiResponse[IngestionJob]:
    """選択レシピの確認待ち工程を承認して次工程を投入する。"""
    enforce_rate_limit("ingest", http_request)
    oracle = OracleClient()
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    status = FileStatus(str(row.get("status") or FileStatus.UPLOADED.value))
    if status == FileStatus.PREPROCESSED:
        phase = IngestionJobPhase.EXTRACT
    elif status == FileStatus.REVIEW:
        if body is not None and (
            body.element_edits or body.table_cell_edits or body.raw_text is not None
        ):
            await _apply_recipe_review_text_edits(document_id, recipe_id, body)
        phase = IngestionJobPhase.CHUNK
    elif status == FileStatus.CHUNKED:
        phase = IngestionJobPhase.INDEX
    else:
        raise HTTPException(status_code=409, detail="確認待ちのレシピのみ承認できます。")
    return await enqueue_document_recipe_job(document_id, recipe_id, phase)


@router.patch(
    "/{document_id}/recipes/{recipe_id}/review-edits",
    response_model=ApiResponse[DocumentRecipeView],
)
async def save_document_recipe_review_edits(
    http_request: Request,
    document_id: str,
    recipe_id: str,
    body: DocumentReviewEditsRequest,
) -> ApiResponse[DocumentRecipeView]:
    enforce_rate_limit("ingest", http_request)
    await _apply_recipe_review_text_edits(document_id, recipe_id, body)
    oracle = OracleClient()
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    return ApiResponse(data=await _document_recipe_view(oracle, row))


@router.post(
    "/{document_id}/recipes/{recipe_id}/reject",
    response_model=ApiResponse[DocumentRecipeView],
)
async def reject_document_recipe(
    http_request: Request,
    document_id: str,
    recipe_id: str,
) -> ApiResponse[DocumentRecipeView]:
    enforce_rate_limit("ingest", http_request)
    oracle = OracleClient()
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    if FileStatus(str(row.get("status"))) != FileStatus.REVIEW:
        raise HTTPException(status_code=409, detail="確認待ちのレシピのみ却下できます。")
    await oracle.update_document_recipe_status(
        recipe_id=recipe_id,
        status=FileStatus.UPLOADED,
    )
    updated = await oracle.get_document_recipe(document_id, recipe_id)
    assert updated is not None
    return ApiResponse(data=await _document_recipe_view(oracle, updated))


@router.get("/{document_id}/chunk-sets", response_model=ApiResponse[list[DocumentChunkSet]])
async def list_document_chunk_sets(document_id: str) -> ApiResponse[list[DocumentChunkSet]]:
    """文書の chunk_set(variant)一覧を返す。KB 詳細での variant 可視化に使う。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    rows = await oracle.list_document_chunk_sets(document_id)
    plan, configs = await _materialization_plan_for_document(oracle, detail)
    effective_settings, _config = await _resolve_ingestion_settings(oracle, document_id)
    effective_by_kb = _effective_ingestion_settings_by_kb(effective_settings, configs)
    persisted_layers = await oracle.list_artifact_layers_for_chunk_sets(
        [str(row.get("chunk_set_id")) for row in rows if row.get("chunk_set_id") is not None]
    )
    chunk_sets: list[DocumentChunkSet] = []
    for row in rows:
        chunk_set = DocumentChunkSet.model_validate(row)
        if chunk_set.extraction_recipe_id:
            extraction = await oracle.get_document_extraction_artifact(
                document_id=document_id,
                extraction_recipe_id=chunk_set.extraction_recipe_id,
            )
            if extraction is not None:
                chunk_set.extraction_status = DocumentLayerStatusName(
                    str(extraction.get("status") or DocumentLayerStatusName.PLANNED_ONLY.value)
                )
                chunk_set.extraction_reason = (
                    str(extraction["reason"]) if extraction.get("reason") is not None else None
                )
        if plan is not None:
            chunk_set.layer_statuses = _layer_statuses_for_chunk_set(
                chunk_set.chunk_set_id,
                plan,
                effective_by_kb,
                persisted_layers,
            )
        chunk_sets.append(chunk_set)
    return ApiResponse(data=chunk_sets)


def _candidate_chunking_settings(base: Settings, overrides: Mapping[str, object]) -> Settings:
    """global 設定に chunking 上書きを重ねた候補レシピ設定を返す(cross-field 検証込み)。

    model_copy は Settings の model_validator を再実行しないため、chunking の相互制約だけ
    ここで明示検証する(不正なら 422)。parser/前処理は変えない=既存抽出を再利用できる。
    """
    candidate = base.model_copy(update=dict(overrides))
    if candidate.rag_chunk_overlap >= candidate.rag_chunk_size:
        raise HTTPException(
            status_code=422, detail="overlap は chunk_size より小さくしてください。"
        )
    if (
        candidate.rag_chunking_strategy == "hierarchical_parent_child"
        and candidate.rag_chunk_child_size >= candidate.rag_chunk_size
    ):
        raise HTTPException(
            status_code=422, detail="child_size は chunk_size より小さくしてください。"
        )
    if (
        candidate.rag_chunking_strategy in CHUNKING_STRATEGIES_WITH_MIN_CHARS
        and candidate.rag_chunk_min_chars >= candidate.rag_chunk_size
    ):
        raise HTTPException(
            status_code=422, detail="min_chars は chunk_size より小さくしてください。"
        )
    return candidate


async def _chunk_set_experiment_view(
    oracle: OracleClient, document_id: str, chunk_set_id: str
) -> DocumentChunkSet:
    """指定 chunk_set を DocumentChunkSet ビューで返す(一覧と同じ導出を再利用)。"""
    for row in await oracle.list_document_chunk_sets(document_id):
        if str(row.get("chunk_set_id")) == chunk_set_id:
            return DocumentChunkSet.model_validate(row)
    raise HTTPException(status_code=500, detail="chunk_set の取得に失敗しました。")


@router.post("/{document_id}/chunk-set-experiments", response_model=ApiResponse[DocumentChunkSet])
async def create_chunk_set_experiment(
    document_id: str, request: ChunkSetExperimentRequest
) -> ApiResponse[DocumentChunkSet]:
    """別 chunking レシピで候補 chunk_set を materialize する(配信は切り替えない)。

    既存抽出を再利用して候補レシピで re-chunk→index し、is_serving=0 の候補として残す。
    検索精度の比較は ``chunk_set_id`` フィルタで候補/配信中をそれぞれ検索して横並びにする。
    """
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status != FileStatus.INDEXED:
        raise HTTPException(
            status_code=409, detail="索引済み(INDEXED)の文書のみ別レシピを試せます。"
        )
    if not detail.content_sha256:
        raise HTTPException(status_code=409, detail="文書のソースハッシュが未確定です。")
    serving_chunk_set_id = await oracle.get_document_serving_chunk_set_id(document_id)
    if serving_chunk_set_id is None:
        raise HTTPException(status_code=409, detail="配信中の chunk_set がありません。")
    base_settings, processing_config = await _resolve_ingestion_settings(oracle, document_id)
    candidate_settings = _candidate_chunking_settings(base_settings, request.settings_overrides())
    candidate_config = processing_config.model_copy(
        update={field: value for field, value in request.model_dump().items() if value is not None}
    )
    _, effective_candidate_config = _merge_document_processing_config(candidate_config)
    base_chunk_set_id = compute_chunk_set_id(detail.content_sha256, candidate_settings)
    if base_chunk_set_id == serving_chunk_set_id:
        raise HTTPException(
            status_code=409,
            detail="現在配信中のレシピと同じ設定です。別の chunking 設定を指定してください。",
        )
    recipes = await oracle.list_document_recipes(document_id)
    source_recipe = recipes[0] if recipes else None
    source_recipe_id = str(source_recipe["recipe_id"]) if source_recipe else None
    source_extraction_recipe_id = (
        str(source_recipe.get("active_extraction_recipe_id"))
        if source_recipe and source_recipe.get("active_extraction_recipe_id")
        else None
    )
    source_extraction = (
        await oracle.get_document_extraction_artifact(
            document_id=document_id,
            extraction_recipe_id=source_extraction_recipe_id,
        )
        if source_extraction_recipe_id is not None
        else None
    )
    if source_extraction is None or not source_extraction.get("extraction_json"):
        raise HTTPException(status_code=409, detail="再利用できる抽出結果がありません。")
    try:
        created_recipe = await oracle.create_document_recipe(
            document_id,
            copy_from_recipe_id=source_recipe_id,
        )
        created_recipe = await oracle.update_document_recipe_config(
            document_id,
            str(created_recipe["recipe_id"]),
            candidate_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    recipe_id = str(created_recipe["recipe_id"])
    recipe_revision = int(str(created_recipe.get("config_revision") or 1))
    extraction_recipe_id = compute_document_recipe_extraction_id(
        compute_extraction_recipe_id(detail.content_sha256, candidate_settings),
        recipe_id,
        recipe_revision,
    )
    raw_recipe_subset = source_extraction.get("recipe_subset")
    await oracle.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=extraction_recipe_id,
        source_sha256=detail.content_sha256,
        recipe_subset=(
            {str(key): value for key, value in raw_recipe_subset.items()}
            if isinstance(raw_recipe_subset, Mapping)
            else None
        ),
        extraction=StructuredExtraction.model_validate(
            source_extraction["extraction_json"]
        ).to_document_payload(),
        status=str(source_extraction.get("status") or "materialized"),
    )
    await oracle.update_document_recipe_status(
        recipe_id=recipe_id,
        status=FileStatus.REVIEW,
        active_extraction_recipe_id=extraction_recipe_id,
    )
    candidate_chunk_set_id = hashlib.sha256(
        f"{base_chunk_set_id}:{recipe_id}:compat".encode()
    ).hexdigest()
    pipeline = IngestionPipeline(
        oracle=oracle,
        settings=candidate_settings,
        recipe_id=recipe_id,
        recipe_revision=recipe_revision,
    )
    try:
        await pipeline.index_reviewed(
            document_id, chunk_set_id=candidate_chunk_set_id, record_outcome=False
        )
    except IngestionUserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    chunk_count = await oracle.count_chunk_set_chunks(candidate_chunk_set_id)
    await oracle.upsert_chunk_set(
        chunk_set_id=candidate_chunk_set_id,
        document_id=document_id,
        recipe_id=recipe_id,
        extraction_recipe_id=extraction_recipe_id,
        recipe_subset=_processing_recipe_snapshot(candidate_config, effective_candidate_config),
    )
    await oracle.mark_chunk_set_indexed(
        chunk_set_id=candidate_chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
    )
    await oracle.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=candidate_chunk_set_id,
        extraction_recipe_id=extraction_recipe_id,
        materialized_revision=recipe_revision,
    )
    return ApiResponse(
        data=await _chunk_set_experiment_view(oracle, document_id, candidate_chunk_set_id)
    )


@router.post(
    "/{document_id}/chunk-set-experiments/{chunk_set_id}/promote",
    response_model=ApiResponse[DocumentChunkSet],
)
async def promote_chunk_set_experiment(
    document_id: str, chunk_set_id: str
) -> ApiResponse[DocumentChunkSet]:
    """互換 API。全レシピ融合では昇格操作を行わない。"""
    _ = (document_id, chunk_set_id)
    raise HTTPException(
        status_code=409,
        detail="全レシピ融合モードでは昇格は不要です。処理レシピから管理してください。",
    )


@router.post(
    "/{document_id}/parser-extraction-experiments", response_model=ApiResponse[IngestionJob]
)
async def create_parser_extraction_experiment(
    document_id: str, request: ParserExtractionExperimentRequest
) -> ApiResponse[IngestionJob]:
    """parser/前処理を変えた候補を**再抽出**で materialize する非同期ジョブを投入する。

    分割軸(chunk-set-experiments)と違い parser/前処理は抽出結果が変わるため再抽出が必要で、
    配信中文書を乱さない candidate モードのジョブで実行する。ジョブ完了で候補 chunk_set が
    is_serving=0 として残り、横並び比較・昇格は既存の導線を使う。
    """
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status != FileStatus.INDEXED:
        raise HTTPException(
            status_code=409, detail="索引済み(INDEXED)の文書のみ別レシピを試せます。"
        )
    if not detail.content_sha256:
        raise HTTPException(status_code=409, detail="文書のソースハッシュが未確定です。")
    if await oracle.get_document_serving_chunk_set_id(document_id) is None:
        raise HTTPException(status_code=409, detail="配信中の chunk_set がありません。")
    overrides = request.settings_overrides()
    base_settings, processing_config = await _resolve_ingestion_settings(oracle, document_id)
    candidate_config = processing_config.model_copy(
        update={field: value for field, value in request.model_dump().items() if value is not None}
    )
    candidate_settings, _effective_candidate_config = _merge_document_processing_config(
        candidate_config
    )
    # parser/前処理が現状と同じなら再抽出は不要(分割だけ変えるなら chunk-set-experiments を使う)。
    if compute_extraction_recipe_id(
        detail.content_sha256, candidate_settings
    ) == compute_extraction_recipe_id(detail.content_sha256, base_settings):
        raise HTTPException(
            status_code=409,
            detail="現在配信中の前処理/解析と同じ設定です。分割だけ変える場合は別レシピ実験を使ってください。",
        )
    recipes = await oracle.list_document_recipes(document_id)
    source_recipe_id = str(recipes[0]["recipe_id"]) if recipes else None
    try:
        created_recipe = await oracle.create_document_recipe(
            document_id,
            copy_from_recipe_id=source_recipe_id,
        )
        created_recipe = await oracle.update_document_recipe_config(
            document_id,
            str(created_recipe["recipe_id"]),
            candidate_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job = await _create_ingestion_job_record(
        oracle=oracle,
        document_id=document_id,
        recipe_id=str(created_recipe["recipe_id"]),
        recipe_revision=int(str(created_recipe.get("config_revision") or 1)),
        parser_profile=_source_profile_for_detail(detail).parser_profile,
        quality_warnings=[],
        phase=IngestionJobPhase.PREPROCESS,
        settings_overrides={
            **overrides,
            "processing_config": candidate_config.model_dump(mode="json", exclude_none=True),
        },
    )
    _dispatch_ingestion_job(job.id)
    return ApiResponse(data=job)


def _experiment_candidate_settings(base: Settings, overrides: dict[str, object]) -> Settings:
    """global 設定に実験ジョブの候補レシピ上書きを重ねた Settings を返す(既知キーのみ)。"""
    allowed = {"rag_preprocess_profile", "rag_parser_adapter_backend"}
    filtered = {key: value for key, value in overrides.items() if key in allowed}
    return base.model_copy(update=filtered)


async def _materialize_experiment_candidate(
    oracle: OracleClient,
    job: IngestionJob,
    *,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
) -> IngestionJobPhase | None:
    """設定 snapshot から新しい chunk_set を隔離構築する。

    ``recipe_id`` 付き job は正式な文書レシピ実行であり、既存 active 出力を構築中に
    変更しない。成功時だけ active を原子的に差し替える。recipe_id が無い呼び出しは
    旧実験 API の互換経路として従来の serving を維持する。

    戻り値は「現在ジョブ完了後に自動投入すべき次フェーズ」。自動進行不要なら ``None``。
    現在ジョブが RUNNING のまま新ジョブを作るとレシピ行ロックのガードで弾かれるため、
    投入自体は呼び出し側(``_run_ingestion_job``)が現在ジョブ SUCCEEDED 後に行う。
    """
    detail = await oracle.get_document(job.document_id)
    if detail is None:
        raise IngestionUserError("ドキュメントが見つかりません。")
    if not detail.content_sha256:
        raise IngestionUserError("文書のソースハッシュが未確定です。")
    serving_chunk_set_id = await oracle.get_document_serving_chunk_set_id(job.document_id)
    if job.recipe_id is None:
        if detail.status != FileStatus.INDEXED:
            raise IngestionUserError("索引済み文書のみレシピ実験できます。")
        if serving_chunk_set_id is None:
            raise IngestionUserError("配信中の chunk_set がありません。")
    base_settings, current_config = await _resolve_ingestion_settings(oracle, job.document_id)
    raw_config = (job.settings_overrides or {}).get("processing_config")
    if isinstance(raw_config, Mapping):
        candidate_config = DocumentProcessingConfig.model_validate(raw_config)
        candidate_settings, effective_candidate_config = _merge_document_processing_config(
            candidate_config
        )
    else:
        candidate_config = current_config
        candidate_settings = _experiment_candidate_settings(
            base_settings, job.settings_overrides or {}
        )
        _, effective_candidate_config = _merge_document_processing_config(candidate_config)
    base_chunk_set_id = compute_chunk_set_id(detail.content_sha256, candidate_settings)
    candidate_chunk_set_id = (
        hashlib.sha256(
            f"{base_chunk_set_id}:{job.recipe_id}:{job.recipe_revision}:{job.id}".encode()
        ).hexdigest()
        if job.recipe_id is not None
        else base_chunk_set_id
    )
    if job.recipe_id is None and candidate_chunk_set_id == serving_chunk_set_id:
        raise IngestionUserError("現在配信中のレシピと同じ設定です。")
    if job.recipe_id is not None:
        await oracle.update_document_recipe_status(
            recipe_id=job.recipe_id,
            status=_PHASE_TO_RUNNING_STATUS[job.phase],
        )
    pipeline = IngestionPipeline(
        oracle=oracle,
        settings=candidate_settings,
        recipe_id=job.recipe_id,
        recipe_revision=job.recipe_revision,
    )
    extraction_recipe_id = compute_extraction_recipe_id(detail.content_sha256, candidate_settings)
    if job.recipe_id is not None and job.phase in {
        IngestionJobPhase.CHUNK,
        IngestionJobPhase.INDEX,
    }:
        recipe_row = await oracle.get_document_recipe(job.document_id, job.recipe_id)
        active_extraction_recipe_id = (
            recipe_row.get("active_extraction_recipe_id") if recipe_row is not None else None
        )
        if active_extraction_recipe_id is None:
            raise IngestionUserError("索引対象の抽出結果が見つかりません。")
        extraction_recipe_id = str(active_extraction_recipe_id)

    if job.recipe_id is not None and job.phase == IngestionJobPhase.INDEX:
        pending = await oracle.get_latest_recipe_chunk_set(
            job.recipe_id,
            status="CHUNKED",
            active=False,
        )
        if pending is None:
            raise IngestionUserError(
                "索引対象の Chunk が見つかりません。Chunk 作成から再開してください。"
            )
        candidate_chunk_set_id = str(pending["chunk_set_id"])
        await pipeline.index_chunked(
            job.document_id,
            chunk_set_id=candidate_chunk_set_id,
            record_outcome=False,
            cancel_checker=cancel_checker,
        )
    elif job.recipe_id is not None and job.phase == IngestionJobPhase.CHUNK:
        await pipeline.chunk_reviewed(
            job.document_id,
            chunk_set_id=candidate_chunk_set_id,
            record_outcome=False,
            cancel_checker=cancel_checker,
        )
        chunk_count = await oracle.count_chunk_set_chunks(candidate_chunk_set_id)
        await oracle.upsert_chunk_set(
            chunk_set_id=candidate_chunk_set_id,
            document_id=job.document_id,
            recipe_id=job.recipe_id,
            extraction_recipe_id=extraction_recipe_id,
            recipe_subset=_processing_recipe_snapshot(candidate_config, effective_candidate_config),
            status="CHUNKED",
        )
        await oracle.mark_chunk_set_chunked(
            chunk_set_id=candidate_chunk_set_id,
            chunk_count=chunk_count,
        )
        if not candidate_settings.rag_auto_index_after_chunk_enabled:
            return None
        await pipeline.index_chunked(
            job.document_id,
            chunk_set_id=candidate_chunk_set_id,
            record_outcome=False,
            cancel_checker=cancel_checker,
        )
    else:
        prepared_artifact: DocumentPreprocessArtifact | None = None
        if job.recipe_id is not None and job.phase == IngestionJobPhase.EXTRACT:
            recipe_row = await oracle.get_document_recipe(job.document_id, job.recipe_id)
            if recipe_row is None or not recipe_row.get("preprocess_artifact"):
                raise IngestionUserError(
                    "処理後ファイルが見つかりません。ファイル準備から再処理してください。"
                )
            prepared_artifact = DocumentPreprocessArtifact.model_validate(
                recipe_row["preprocess_artifact"]
            )
            if not prepared_artifact.object_storage_path:
                raise IngestionUserError(
                    "処理後ファイルが見つかりません。ファイル準備から再処理してください。"
                )
            try:
                data = await ObjectStorageClient().get(prepared_artifact.object_storage_path)
            except (FileNotFoundError, ValueError) as exc:
                raise IngestionUserError("処理後ファイルを読み込めませんでした。") from exc
            source_profile = _source_profile_for_detail(detail)
        else:
            data, source_profile = await _load_source_bytes(oracle, job.document_id, detail)
        await pipeline.ingest(
            document_id=job.document_id,
            image_bytes=data,
            prompt="ドキュメントを日本語で OCR し、本文テキストを抽出してください。",
            content_type=(
                prepared_artifact.content_type
                if prepared_artifact is not None and prepared_artifact.content_type
                else detail.content_type or "application/octet-stream"
            ),
            source_profile=source_profile,
            chunk_set_id=candidate_chunk_set_id,
            record_outcome=False,
            original_object_storage_path=detail.object_storage_path,
            prepared_artifact=prepared_artifact,
            manage_document_state=False,
            cancel_checker=cancel_checker,
        )
        if job.recipe_id is not None:
            recipe_row = await oracle.get_document_recipe(job.document_id, job.recipe_id)
            recipe_status = (
                FileStatus(str(recipe_row.get("status")))
                if recipe_row is not None
                else FileStatus.ERROR
            )
            if (
                recipe_status == FileStatus.REVIEW
                and candidate_settings.rag_auto_chunk_after_extract_enabled
            ):
                # 現在の EXTRACT ジョブがまだ RUNNING のため、ここで CHUNK ジョブを作ると
                # レシピ行ロックのガード(同一レシピの QUEUED/RUNNING 拒否)で弾かれる。
                # 投入は呼び出し側が現在ジョブ SUCCEEDED 後に行うので、決定だけ返す。
                return IngestionJobPhase.CHUNK
            if recipe_status in {FileStatus.PREPROCESSED, FileStatus.REVIEW}:
                return None
            active_extraction_recipe_id = (
                recipe_row.get("active_extraction_recipe_id") if recipe_row is not None else None
            )
            if active_extraction_recipe_id is None:
                raise IngestionUserError("索引対象の抽出結果が見つかりません。")
            extraction_recipe_id = str(active_extraction_recipe_id)

    chunk_count = await oracle.count_chunk_set_chunks(candidate_chunk_set_id)
    await oracle.upsert_chunk_set(
        chunk_set_id=candidate_chunk_set_id,
        document_id=job.document_id,
        recipe_id=job.recipe_id,
        extraction_recipe_id=extraction_recipe_id,
        recipe_subset=_processing_recipe_snapshot(candidate_config, effective_candidate_config),
    )
    await oracle.mark_chunk_set_indexed(
        chunk_set_id=candidate_chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
    )
    if job.recipe_id is not None:
        await oracle.activate_recipe_chunk_set(
            recipe_id=job.recipe_id,
            chunk_set_id=candidate_chunk_set_id,
            extraction_recipe_id=extraction_recipe_id,
            materialized_revision=job.recipe_revision,
        )
        # 文書一覧の legacy 集約状態。少なくとも1レシピが検索可能なら INDEXED とする。
        await oracle.update_document_status(job.document_id, FileStatus.INDEXED)
    elif serving_chunk_set_id is not None:
        # 旧実験 API は互換期間中だけ候補を serving に載せない。
        await oracle.set_document_serving_chunk_set(
            document_id=job.document_id, chunk_set_id=serving_chunk_set_id
        )
    # INDEX まで到達した経路は自動進行の追加投入不要(CHUNK→INDEX はここで完結)。
    return None


async def _materialization_plan_for_document(
    oracle: OracleClient,
    detail: DocumentDetail,
    *,
    global_settings: Settings | None = None,
) -> tuple[MaterializationPlan | None, dict[str, KnowledgeBaseAdapterConfig]]:
    """文書の有効レシピと所属 KB scope から materialization plan を復元する。"""
    configs = dict(await oracle.list_document_knowledge_base_configs(detail.id))
    if not detail.content_sha256 or not configs:
        return None, configs
    settings = global_settings
    if settings is None:
        settings, _config = await _resolve_ingestion_settings(oracle, detail.id)
    return plan_document_materializations(detail.content_sha256, settings, configs), configs


def _effective_ingestion_settings_by_kb(
    document_settings: Settings,
    configs: Mapping[str, KnowledgeBaseAdapterConfig],
) -> dict[str, Settings]:
    """KB ごとの有効な構築設定を返す(3 層モデル: レシピは文書で KB 共通)。

    レイヤー状態表示を文書の単一レシピに揃え、materialization と一致させる。
    KB 別取込上書きは使わない。
    """
    return {knowledge_base_id: document_settings for knowledge_base_id in configs}


def _layer_statuses_for_chunk_set(
    chunk_set_id: str,
    plan: MaterializationPlan,
    effective_by_kb: Mapping[str, Settings],
    persisted_layers: Mapping[str, Mapping[str, object]] | None = None,
) -> DocumentChunkSetLayerStatuses:
    """派生情報レイヤーの現在状態を chunk_set 単位で作る。"""
    return DocumentChunkSetLayerStatuses(
        metadata=_layer_status_for_chunk_set(
            chunk_set_id,
            plan,
            effective_by_kb,
            persisted_layers or {},
            layer="metadata",
            user_label="項目抽出",
        ),
        graph=_layer_status_for_chunk_set(
            chunk_set_id,
            plan,
            effective_by_kb,
            persisted_layers or {},
            layer="graph",
            user_label="関係情報",
        ),
        navigation=_layer_status_for_chunk_set(
            chunk_set_id,
            plan,
            effective_by_kb,
            persisted_layers or {},
            layer="navigation",
            user_label="ナビゲーション",
        ),
    )


def _layer_status_for_chunk_set(
    chunk_set_id: str,
    plan: MaterializationPlan,
    effective_by_kb: Mapping[str, Settings],
    persisted_layers: Mapping[str, Mapping[str, object]],
    *,
    layer: str,
    user_label: str,
) -> DocumentMaterializationLayerStatus:
    requested_ids = _requested_layer_ids_for_chunk_set(
        chunk_set_id,
        plan,
        effective_by_kb,
        layer=layer,
    )
    if not requested_ids:
        return DocumentMaterializationLayerStatus(
            requested=False,
            status=DocumentLayerStatusName.NOT_REQUESTED,
            reason=f"現在の構築設定では{user_label}を使用しません。",
        )
    if len(requested_ids) > 1:
        return DocumentMaterializationLayerStatus(
            requested=True,
            status=DocumentLayerStatusName.PLANNED_ONLY,
            reason=(
                f"{user_label}は複数の方針がこのチャンク構成を共有しています。"
                "現時点では計画だけを表示しています。"
            ),
        )
    persisted = persisted_layers.get(requested_ids[0])
    if persisted is not None:
        return DocumentMaterializationLayerStatus(
            layer_id=requested_ids[0],
            requested=bool(persisted.get("requested", True)),
            status=DocumentLayerStatusName(
                str(persisted.get("status") or DocumentLayerStatusName.PLANNED_ONLY.value)
            ),
            reason=str(persisted["reason"]) if persisted.get("reason") is not None else None,
        )
    return DocumentMaterializationLayerStatus(
        layer_id=requested_ids[0],
        requested=True,
        status=DocumentLayerStatusName.PLANNED_ONLY,
        reason=f"{user_label}は構築計画に含まれていますが、まだ実体化していません。",
    )


def _requested_layer_ids_for_chunk_set(
    chunk_set_id: str,
    plan: MaterializationPlan,
    effective_by_kb: Mapping[str, Settings],
    *,
    layer: str,
) -> tuple[str, ...]:
    knowledge_base_ids = plan.chunk_sets.get(chunk_set_id, frozenset())
    layer_map = {
        "metadata": plan.metadata_layers,
        "graph": plan.graph_layers,
        "navigation": plan.nav_layers,
    }.get(layer)
    if not knowledge_base_ids or layer_map is None:
        return ()
    requested: list[str] = []
    for layer_id, owners in layer_map.items():
        relevant_owners = owners & knowledge_base_ids
        if any(
            _layer_requested(layer, effective_by_kb[knowledge_base_id])
            for knowledge_base_id in relevant_owners
            if knowledge_base_id in effective_by_kb
        ):
            requested.append(layer_id)
    return tuple(sorted(requested))


def _layer_requested(layer: str, settings: Settings) -> bool:
    if layer == "metadata":
        return bool(settings.rag_field_extraction_enabled or settings.rag_asset_summary_enabled)
    if layer == "graph":
        return settings.rag_graph_profile != "off"
    if layer == "navigation":
        return bool(settings.rag_navigation_summary_enabled or settings.rag_raptor_enabled)
    return False


def _parser_backend_drifted(observed_parser: str, effective_backend: str) -> bool:
    """取込済み parser と現在の明示 parser 設定がずれているか判定する。

    ``local`` は外部 parser 未選択を表す umbrella で、PDF などは source profile の
    ``enterprise_ai_*`` 方針名を持つ。そのため local は parser drift の比較対象にしない。
    """
    effective = effective_backend.strip().casefold()
    if not effective or effective == "local":
        return False
    observed = observed_parser.strip().casefold()
    if not observed:
        return False
    aliases = {
        "docling": {"docling", "docling_adapter"},
        "marker": {"marker", "marker_adapter"},
        "unstructured": {"unstructured", "unstructured_adapter"},
        "unlimited_ocr": {"unlimited_ocr", "unlimited_ocr_adapter"},
        "mineru": {"mineru", "mineru_adapter"},
        "dots_ocr": {"dots_ocr", "dots_ocr_adapter"},
        "glm_ocr": {"glm_ocr", "glm_ocr_adapter"},
        "oci_genai_vision": {"oci_genai_vision", "enterprise_ai_vlm"},
        "enterprise_ai_vlm": {"oci_genai_vision", "enterprise_ai_vlm"},
    }
    return observed not in aliases.get(effective, {effective})


def _merge_document_processing_config(
    config: DocumentProcessingConfig,
    global_settings: Settings | None = None,
) -> tuple[Settings, DocumentProcessingConfig]:
    """global 既定へ文書の明示上書きだけを重ねる。KB 設定は参照しない。"""
    base = global_settings or get_settings()
    adapter = KnowledgeBaseAdapterConfig(ingestion=config)
    effective_settings = resolve_effective_settings(base, adapter, scope="ingestion")
    if config.chunk_context_header_enabled is not None:
        effective_settings = effective_settings.model_copy(
            update={"rag_chunk_context_header_enabled": config.chunk_context_header_enabled}
        )
    # 外部 parser 選択時に自動注入される feature flag も含め、実際の Settings を
    # スナップショットへ投影する。これにより runtime と drift 判定が同じ値を見る。
    effective = resolve_effective_adapter_config(
        effective_settings, KnowledgeBaseAdapterConfig()
    ).ingestion
    return effective_settings, DocumentProcessingConfig.model_validate(
        {
            **effective.model_dump(),
            "chunk_context_header_enabled": (effective_settings.rag_chunk_context_header_enabled),
        }
    )


def _processing_recipe_snapshot(
    config: DocumentProcessingConfig,
    effective: DocumentProcessingConfig,
) -> dict[str, object]:
    """chunk_set に刻む文書レシピ。昇格時に継承/上書き状態も復元できる。"""
    return {
        "processing_config": config.model_dump(mode="json", exclude_none=True),
        "effective_processing_config": effective.model_dump(mode="json"),
    }


def _processing_snapshot_config(
    row: Mapping[str, object] | None,
) -> DocumentProcessingConfig | None:
    if not row:
        return None
    raw = row.get("recipe_subset")
    if not isinstance(raw, Mapping):
        return None
    effective = raw.get("effective_processing_config")
    if not isinstance(effective, Mapping):
        return None
    try:
        return DocumentProcessingConfig.model_validate(dict(effective))
    except Exception:  # noqa: BLE001 - 旧/破損 snapshot は既存観測値へ縮退する
        return None


async def _document_ingestion_config_data(
    oracle: OracleClient,
    detail: DocumentDetail,
) -> DocumentIngestionConfigData:
    effective_settings, processing_config = await _resolve_ingestion_settings(oracle, detail.id)
    _, effective_config = _merge_document_processing_config(processing_config)
    is_indexed = detail.status == FileStatus.INDEXED

    observed_strategy: str | None = None
    observed_parser: str | None = None
    if is_indexed:
        chunks = await oracle.list_document_chunks(detail.id)
        if chunks:
            first = chunks[0]
            strategy_value = first.metadata.get("chunk_strategy")
            observed_strategy = str(strategy_value) if strategy_value is not None else None
            observed_parser = first.source_parser or (
                str(first.metadata["parser_backend"])
                if "parser_backend" in first.metadata
                else None
            )

    drift_fields: list[str] = []
    serving_id = await oracle.get_document_serving_chunk_set_id(detail.id) if is_indexed else None
    serving = await oracle.get_chunk_set(serving_id) if serving_id is not None else None
    observed_config = _processing_snapshot_config(serving)
    if observed_config is not None:
        observed_values = observed_config.model_dump(mode="json")
        effective_values = effective_config.model_dump(mode="json")
        drift_fields = [
            group
            for group, fields in DOCUMENT_PROCESSING_OUTPUT_GROUPS.items()
            if any(observed_values.get(field) != effective_values.get(field) for field in fields)
        ]
    elif is_indexed:
        if (
            detail.preprocess_artifact is not None
            and detail.preprocess_artifact.profile != effective_settings.rag_preprocess_profile
        ):
            drift_fields.append("preprocess_profile")
        if observed_parser and _parser_backend_drifted(
            observed_parser, effective_settings.rag_parser_adapter_backend
        ):
            drift_fields.append("parser_adapter_backend")
        if observed_strategy and observed_strategy != effective_settings.rag_chunking_strategy:
            drift_fields.append("chunking_strategy")

    return DocumentIngestionConfigData(
        document_id=detail.id,
        is_indexed=is_indexed,
        processing_config=processing_config,
        effective_processing_config=effective_config,
        effective_preprocess_profile=effective_settings.rag_preprocess_profile,
        effective_chunking_strategy=effective_settings.rag_chunking_strategy,
        effective_parser_adapter_backend=effective_settings.rag_parser_adapter_backend,
        observed_chunking_strategy=observed_strategy,
        observed_parser_backend=observed_parser,
        chunking_drift="chunking_strategy" in drift_fields,
        parser_drift="parser_adapter_backend" in drift_fields,
        config_drift=bool(drift_fields),
        drift_fields=drift_fields,
    )


@router.get(
    "/{document_id}/ingestion-config",
    response_model=ApiResponse[DocumentIngestionConfigData],
)
async def get_document_ingestion_config(
    document_id: str,
) -> ApiResponse[DocumentIngestionConfigData]:
    """文書の処理レシピ上書き・有効値・配信中レシピとの差分を返す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=await _document_ingestion_config_data(oracle, detail))


@router.put(
    "/{document_id}/ingestion-config",
    response_model=ApiResponse[DocumentIngestionConfigData],
)
async def update_document_ingestion_config(
    document_id: str,
    request: DocumentProcessingConfig,
) -> ApiResponse[DocumentIngestionConfigData]:
    """文書単位の処理レシピ上書きを保存する。既存成果物は変更しない。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status not in DOCUMENT_PROCESSING_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="処理途中のドキュメントは設定を変更できません。処理を完了するか最初から再処理してください。",
        )
    for status in (IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING):
        if await oracle.list_document_ingestion_jobs(document_id, status=status):
            raise HTTPException(
                status_code=409,
                detail="取込ジョブの実行中は設定を変更できません。完了後に再試行してください。",
            )
    try:
        _merge_document_processing_config(request)
    except KbAdapterConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await oracle.update_document_processing_config(document_id, request)
    return ApiResponse(data=await _document_ingestion_config_data(oracle, detail))


@router.get(
    "/{document_id}/extraction-export",
    response_model=ApiResponse[DocumentExtractionExport],
)
async def export_document_extraction(
    document_id: str,
    format: Annotated[DocumentExtractionExportFormat, Query()] = (
        DocumentExtractionExportFormat.MARKDOWN
    ),
) -> ApiResponse[DocumentExtractionExport]:
    """保存済み extraction を JSON / Markdown / HTML / chunks 形式で監査用に返す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    extraction = _structured_extraction_from_detail(detail)
    payload = extraction.to_document_payload()
    chunks: list[DocumentChunkView] = []
    if format == DocumentExtractionExportFormat.CHUNKS:
        chunks = await oracle.list_document_chunks(document_id)
        payload = {"chunks": [chunk.model_dump(mode="json") for chunk in chunks]}
    content = _document_extraction_export_content(format, extraction, payload)
    return ApiResponse(
        data=DocumentExtractionExport(
            document_id=document_id,
            file_name=detail.file_name,
            format=format,
            content_type=_document_extraction_export_content_type(format),
            content=content,
            payload=(
                payload
                if format
                not in {
                    DocumentExtractionExportFormat.MARKDOWN,
                    DocumentExtractionExportFormat.HTML,
                }
                else {}
            ),
            chunks=chunks,
            parser_backend=_extraction_parser_backend(extraction),
            parser_profile=_extraction_parser_profile(extraction),
            page_count=len(extraction.pages),
            element_count=len(extraction.elements),
            table_count=len(extraction.tables),
            asset_count=len(extraction.assets),
        )
    )


@router.get(
    "/{document_id}/navigation",
    response_model=ApiResponse[list[DocumentNavigationNode]],
)
async def get_document_navigation(
    document_id: str,
) -> ApiResponse[list[DocumentNavigationNode]]:
    """文書の章節 navigation tree（progressive disclosure 用）を返す。

    取込時に要約付きで永続化されていればそれを返し、未保存の旧文書では保存済み
    extraction から決定論的に tree を再構築する（要約なし）。
    """
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    extraction = _structured_extraction_from_detail(detail)
    nodes = extraction.navigation or build_navigation_tree(extraction)
    return ApiResponse(data=nodes)


@router.get(
    "/{document_id}/extracted-fields",
    response_model=ApiResponse[list[ExtractionField]],
)
async def get_document_extracted_fields(
    document_id: str,
) -> ApiResponse[list[ExtractionField]]:
    """文書から schema 駆動で抽出した named field/entity を返す(PoweRAG 由来)。

    帳票項目の編集 endpoint(`/fields`)とは別概念で、こちらは読み取り専用。
    """
    detail = await OracleClient().get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    extraction = _structured_extraction_from_detail(detail)
    return ApiResponse(data=extraction.fields)


@router.get(
    "/{document_id}/ingestion-segments",
    response_model=ApiResponse[list[IngestionSegment]],
)
async def list_document_ingestion_segments(
    document_id: str,
) -> ApiResponse[list[IngestionSegment]]:
    """文書 preview workspace 用に取込 segment/checkpoint 状態を返す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    try:
        persisted_segments = await oracle.list_ingestion_segments(document_id)
    except Exception:
        persisted_segments = []
    if persisted_segments:
        return ApiResponse(
            data=[
                _segment_with_progress_defaults(segment, detail) for segment in persisted_segments
            ]
        )
    effective_settings, _owning = await _resolve_ingestion_settings(oracle, document_id)
    jobs = await oracle.list_document_ingestion_jobs(document_id)
    return ApiResponse(data=_document_ingestion_segments(detail, jobs, effective_settings))


def _segment_with_progress_defaults(
    segment: IngestionSegment,
    detail: DocumentDetail,
) -> IngestionSegment:
    """旧 checkpoint row に progress 表示用の単位を補う。"""
    if segment.progress_unit != "source":
        return segment
    suffix = segment.segment_id.rsplit(":", 1)[-1]
    if suffix.startswith("slide"):
        unit = "slide"
    elif suffix.startswith("sheet"):
        unit = "sheet"
    elif segment.page_start is not None and segment.page_end is not None:
        content_type = (detail.content_type or "").lower()
        unit = (
            "page"
            if content_type == "application/pdf" or content_type.startswith("image/")
            else "source"
        )
    else:
        unit = "source"
    return segment.model_copy(
        update={
            "progress_unit": unit,
            "progress_start": segment.page_start if unit != "source" else None,
            "progress_end": segment.page_end if unit != "source" else None,
        }
    )


@router.get("/{document_id}", response_model=ApiResponse[DocumentDetail])
async def get_document(document_id: str) -> ApiResponse[DocumentDetail]:
    """ドキュメント詳細（抽出本文含む）を返す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    detail = await _attach_duplicate_source(detail, oracle)
    return ApiResponse(data=detail)


async def _attach_duplicate_source(
    detail: DocumentDetail,
    oracle: OracleClient,
) -> DocumentDetail:
    """重複 skip の理由を画面で説明できるよう、参照元の最小摘要を付ける。"""
    duplicate_id = detail.duplicate_of_document_id
    if duplicate_id is None:
        return detail
    duplicate = await oracle.get_document(duplicate_id)
    if duplicate is None:
        return detail
    return detail.model_copy(
        update={
            "duplicate_source": DuplicateDocumentRef(
                id=duplicate.id,
                file_name=duplicate.file_name,
                status=duplicate.status,
                uploaded_at=duplicate.uploaded_at,
                indexed_at=duplicate.indexed_at,
            )
        }
    )


@router.delete("/{document_id}", response_model=ApiResponse[DocumentDeleteResult])
async def delete_document(document_id: str) -> ApiResponse[DocumentDeleteResult]:
    """ドキュメント本体、検索 index、投入関連行、原本ファイル参照を削除する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    blocking_jobs = await _list_delete_blocking_ingestion_jobs(oracle, document_id)
    if blocking_jobs:
        raise HTTPException(
            status_code=409,
            detail="取込ジョブが実行中のため削除できません。先にキャンセルしてください。",
        )
    artifact_paths = await _document_artifact_paths(oracle, detail)

    try:
        deleted = await oracle.delete_document(document_id)
    except DocumentDeleteBlockedByRunningIngestionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")

    object_deleted = False
    artifact_deleted_count = 0
    artifact_delete_failed_count = 0
    warning_messages: list[str] = []
    storage = ObjectStorageClient()
    if detail.object_storage_path:
        try:
            object_deleted = await storage.delete(detail.object_storage_path)
            if not object_deleted:
                warning_messages.append("原本ファイルは既に存在しませんでした。")
        except FileNotFoundError:
            warning_messages.append("原本ファイルは既に存在しませんでした。")
        except ValueError:
            logger.warning(
                "document_source_delete_invalid_reference",
                extra={"document_id": document_id},
            )
            warning_messages.append("原本ファイルの参照パスが不正なため削除できませんでした。")
        except Exception:
            logger.exception(
                "document_source_delete_failed",
                extra={"document_id": document_id},
            )
            warning_messages.append(
                "文書は削除しましたが、原本ファイルの削除に失敗しました。保存先を確認してください。"
            )
    for artifact_path in artifact_paths:
        try:
            if await storage.delete(artifact_path):
                artifact_deleted_count += 1
        except Exception:
            artifact_delete_failed_count += 1
            artifact_ref_hash = _sha256_hex(artifact_path.encode())[:16]
            logger.info(
                "document_artifact_delete_failed",
                extra={"document_id": document_id, "artifact_ref_hash": artifact_ref_hash},
            )
    if artifact_delete_failed_count:
        warning_messages.append(
            "文書は削除しましたが、一部の抽出 artifact cache の削除に失敗しました。"
        )

    return ApiResponse(
        data=DocumentDeleteResult(
            id=detail.id,
            file_name=detail.file_name,
            object_storage_path=detail.object_storage_path,
            object_deleted=object_deleted,
            artifact_deleted_count=artifact_deleted_count,
            artifact_delete_failed_count=artifact_delete_failed_count,
        ),
        warning_messages=warning_messages,
    )


@router.get("/{document_id}/knowledge-bases", response_model=ApiResponse[list[KnowledgeBaseRef]])
async def list_document_knowledge_bases(
    document_id: str,
) -> ApiResponse[list[KnowledgeBaseRef]]:
    """ドキュメントの所属ナレッジベース一覧を返す。"""
    oracle = OracleClient()
    if await oracle.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=await oracle.list_document_knowledge_bases(document_id))


@router.put("/{document_id}/knowledge-bases", response_model=ApiResponse[list[KnowledgeBaseRef]])
async def replace_document_knowledge_bases(
    document_id: str,
    request: DocumentKnowledgeBaseReplaceRequest,
) -> ApiResponse[list[KnowledgeBaseRef]]:
    """ドキュメントの所属ナレッジベースを指定リストへ置換する。"""
    try:
        refs = await OracleClient().replace_document_knowledge_bases(
            document_id,
            request.knowledge_base_ids,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="ドキュメントまたはナレッジベースが見つかりません。",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(data=refs)


@router.post("/{document_id}/ingest", response_model=ApiResponse[IngestionJob])
async def ingest_document(
    http_request: Request,
    document_id: str,
    force: bool = Query(default=False),
) -> ApiResponse[IngestionJob]:
    """旧互換入口。HTTP では実行せず、取込 job をキュー投入して即時に返す。"""
    enforce_rate_limit("ingest", http_request)
    job = await _enqueue_ingestion_job_for_document(document_id, force=force)
    return ApiResponse(data=job)


@router.post("/{document_id}/approve", response_model=ApiResponse[IngestionJob])
async def approve_document(
    http_request: Request,
    document_id: str,
    body: DocumentApproveRequest | None = None,
) -> ApiResponse[IngestionJob]:
    """現在のレビュー段階を承認し、次の取込 job を投入する。

    body に REVIEW 中のテキスト修正(raw_text / element_edits / table_cell_edits)を
    含む場合は、bbox・構造を保持したままテキストのみ差し替えてから chunk する。
    """
    enforce_rate_limit("ingest", http_request)
    detail = await OracleClient().get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status == FileStatus.PREPROCESSED:
        # ファイル準備の承認: 保存済み preprocess artifact から EXTRACT ジョブを投入し、
        # parse → REVIEW へ進める。
        job = await _enqueue_ingestion_job_for_document(
            document_id, force=False, phase=IngestionJobPhase.EXTRACT
        )
    elif detail.status == FileStatus.REVIEW:
        # 3 層モデル: レシピは文書単位の単一 extraction recipe なので、保存済みプレビューから
        # 安全に後段 chunk/index できる(KB 別の解析分岐に伴う再取込ゲートは廃止)。
        if body is not None and (
            body.element_edits or body.table_cell_edits or body.raw_text is not None
        ):
            await _apply_review_text_edits(document_id, body)
        job = await _enqueue_chunk_phase_job_for_document(document_id)
    elif detail.status == FileStatus.CHUNKED:
        job = await _enqueue_index_phase_job_for_document(document_id)
    else:
        raise HTTPException(
            status_code=409,
            detail="確認待ちの文書のみ承認できます。",
        )
    return ApiResponse(data=job)


@router.patch("/{document_id}/review-edits", response_model=ApiResponse[DocumentDetail])
async def save_document_review_edits(
    http_request: Request,
    document_id: str,
    body: DocumentReviewEditsRequest,
) -> ApiResponse[DocumentDetail]:
    """REVIEW 中の構造化要素修正を保存し、文書状態は REVIEW のまま維持する。"""
    enforce_rate_limit("ingest", http_request)
    detail = await _apply_review_text_edits(document_id, body)
    return ApiResponse(data=detail)


async def _apply_review_text_edits(
    document_id: str,
    edits: DocumentReviewEditsRequest | DocumentApproveRequest,
) -> DocumentDetail:
    """REVIEW 中の人手テキスト修正を保存済み抽出へ適用する(テキストのみ)。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status != FileStatus.REVIEW:
        raise HTTPException(
            status_code=409,
            detail="プレビュー確認待ちの文書のみ修正できます。",
        )
    if not detail.extraction:
        raise HTTPException(status_code=409, detail="修正対象の抽出結果がありません。")
    extraction = _reviewed_extraction_with_edits(
        StructuredExtraction.model_validate(detail.extraction),
        edits,
    )
    return await oracle.save_review_extraction(document_id, extraction)


async def _apply_recipe_review_text_edits(
    document_id: str,
    recipe_id: str,
    edits: DocumentReviewEditsRequest | DocumentApproveRequest,
) -> None:
    """選択レシピの extraction artifact だけを構造保持で修正する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if detail is None or row is None:
        raise HTTPException(status_code=404, detail="レシピが見つかりません。")
    if FileStatus(str(row.get("status"))) != FileStatus.REVIEW:
        raise HTTPException(status_code=409, detail="確認待ちのレシピのみ修正できます。")
    extraction_recipe_id = row.get("active_extraction_recipe_id")
    if extraction_recipe_id is None:
        raise HTTPException(status_code=409, detail="修正対象の抽出結果がありません。")
    artifact = await oracle.get_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=str(extraction_recipe_id),
    )
    if artifact is None or not artifact.get("extraction_json"):
        raise HTTPException(status_code=409, detail="修正対象の抽出結果がありません。")
    extraction = _reviewed_extraction_with_edits(
        StructuredExtraction.model_validate(artifact["extraction_json"]),
        edits,
    )
    raw_recipe_subset = artifact.get("recipe_subset")
    recipe_subset = (
        {str(key): value for key, value in raw_recipe_subset.items()}
        if isinstance(raw_recipe_subset, Mapping)
        else None
    )
    if not detail.content_sha256:
        raise HTTPException(status_code=409, detail="文書のソースハッシュが未確定です。")
    config = DocumentProcessingConfig.model_validate(row.get("processing_config") or {})
    recipe_settings, _ = _merge_document_processing_config(config)
    if recipe_subset and isinstance(recipe_subset.get("rag_preprocess_profile"), str):
        recipe_settings = recipe_settings.model_copy(
            update={"rag_preprocess_profile": recipe_subset["rag_preprocess_profile"]}
        )
    base_extraction_recipe_id = compute_extraction_recipe_id(detail.content_sha256, recipe_settings)
    scoped_extraction_recipe_id = compute_document_recipe_extraction_id(
        base_extraction_recipe_id,
        recipe_id,
        int(str(row.get("config_revision") or 1)),
    )
    await oracle.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=scoped_extraction_recipe_id,
        source_sha256=detail.content_sha256,
        recipe_subset=recipe_subset,
        extraction=extraction.to_document_payload(),
        status=str(artifact.get("status") or "materialized"),
    )
    await oracle.update_document_recipe_status(
        recipe_id=recipe_id,
        status=FileStatus.REVIEW,
        active_extraction_recipe_id=scoped_extraction_recipe_id,
    )


def _reviewed_extraction_with_edits(
    extraction: StructuredExtraction,
    edits: DocumentReviewEditsRequest | DocumentApproveRequest,
) -> StructuredExtraction:
    """構造・bbox を維持し、許可されたテキストだけを差し替える。"""
    text_by_element_id = {edit.element_id: edit.text for edit in edits.element_edits}
    unknown_ids = sorted(
        text_by_element_id.keys()
        - {element.element_id for element in extraction.elements if element.element_id}
    )
    if unknown_ids:
        raise HTTPException(
            status_code=400,
            detail="存在しない要素 ID が含まれています。",
        )
    if text_by_element_id:
        updated_elements = [
            (
                element.model_copy(update={"text": text_by_element_id[element.element_id]})
                if element.element_id in text_by_element_id
                else element
            )
            for element in extraction.elements
        ]
        extraction = extraction.model_copy(update={"elements": updated_elements})
    if edits.table_cell_edits:
        extraction = _apply_table_cell_edits(extraction, edits.table_cell_edits)
    normalized = _canonicalize_reviewed_extraction(extraction)
    # 旧クライアントの approve(raw_text) は受理を継続する。新しい保存 API は構造編集のみ。
    if isinstance(edits, DocumentApproveRequest) and edits.raw_text is not None:
        normalized = StructuredExtraction.model_validate(
            normalized.model_copy(update={"raw_text": edits.raw_text}).model_dump()
        )
    return normalized


def _canonicalize_reviewed_extraction(
    extraction: StructuredExtraction,
) -> StructuredExtraction:
    """構造化要素を正本として表・章節・offset・raw_text を再同期する。"""
    table_by_key: dict[str, ExtractionTable] = {}
    for extraction_table in extraction.tables:
        table_by_key[extraction_table.table_id] = extraction_table
        if extraction_table.element_id:
            table_by_key[extraction_table.element_id] = extraction_table
    table_elements = [element for element in extraction.elements if element.kind == "table"]
    fallback_table = (
        extraction.tables[0] if len(extraction.tables) == len(table_elements) == 1 else None
    )

    matched_table_ids: set[str] = set()
    source_elements: list[DocumentElement] = []
    for element in extraction.elements:
        if element.kind != "table":
            source_elements.append(element)
            continue
        table_key = _review_table_key(element)
        table = table_by_key.get(table_key) if table_key else fallback_table
        if table is None:
            source_elements.append(element)
            continue
        matched_table_ids.add(table.table_id)
        source_elements.append(element.model_copy(update={"text": _review_table_text(table)}))

    for extraction_table in extraction.tables:
        if extraction_table.table_id in matched_table_ids:
            continue
        row_count, column_count = _review_table_shape(extraction_table)
        source_elements.append(
            DocumentElement(
                kind="table",
                text=_review_table_text(extraction_table),
                order=len(source_elements),
                element_id=extraction_table.element_id or extraction_table.table_id,
                content_kind="table",
                page_number=extraction_table.page_number,
                bbox=_review_table_bbox(extraction_table),
                metadata={
                    "table_id": extraction_table.table_id,
                    "row_count": row_count,
                    "column_count": column_count,
                },
            )
        )

    path_by_level: dict[int, str] = {}
    current_path: list[str] = []
    raw_parts: list[str] = []
    cursor = 0
    elements: list[DocumentElement] = []
    for element in sorted(source_elements, key=lambda item: item.order):
        metadata = dict(element.metadata)
        text = element.text.strip()
        if element.kind == "title":
            level, title = _review_heading(element, text)
            path_by_level = {
                existing_level: existing_title
                for existing_level, existing_title in path_by_level.items()
                if existing_level < level
            }
            path_by_level[level] = title
            current_path = [path_by_level[key] for key in sorted(path_by_level)]
            metadata["section_level"] = level
        elif not current_path and element.section_path:
            current_path = list(element.section_path)

        metadata.pop("raw_start", None)
        metadata.pop("raw_end", None)
        if element.kind in SEARCHABLE_ELEMENT_KINDS and text:
            if raw_parts:
                cursor += 1
            metadata["raw_start"] = cursor
            cursor += len(text)
            metadata["raw_end"] = cursor
            raw_parts.append(text)

        elements.append(
            element.model_copy(
                update={
                    "text": text,
                    "section_path": list(current_path),
                    "metadata": metadata,
                }
            )
        )

    element_page = {
        element.element_id: element.page_number
        for element in elements
        if element.element_id and element.page_number is not None
    }
    pages = [
        page.model_copy(
            update={
                "element_ids": [
                    *page.element_ids,
                    *[
                        element_id
                        for element_id, page_number in element_page.items()
                        if page_number == page.page_number and element_id not in page.element_ids
                    ],
                ]
            }
        )
        for page in extraction.pages
    ]
    normalized = StructuredExtraction.model_validate(
        extraction.model_copy(
            update={
                "elements": elements,
                "pages": pages,
                "raw_text": "\n".join(raw_parts),
                "navigation": [],
            }
        ).model_dump()
    )
    return normalized.model_copy(update={"navigation": build_navigation_tree(normalized)})


def _review_table_key(element: DocumentElement) -> str | None:
    value = element.metadata.get("table_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return element.element_id


def _review_table_text(table: ExtractionTable) -> str:
    if not table.cells:
        return table.caption or ""
    row_count, column_count = _review_table_shape(table)
    rows = [["" for _ in range(column_count)] for _ in range(row_count)]
    for cell in table.cells:
        rows[cell.row][cell.col] = cell.text
    markdown = "\n".join(
        "| " + " | ".join(value.replace("|", "\\|").strip() for value in row) + " |"
        for row in rows
        if any(value.strip() for value in row)
    )
    return "\n".join(part for part in (table.caption, markdown) if part).strip()


def _review_table_shape(table: ExtractionTable) -> tuple[int, int]:
    if not table.cells:
        return 0, 0
    return (
        max(cell.row + cell.row_span for cell in table.cells),
        max(cell.col + cell.col_span for cell in table.cells),
    )


def _review_table_bbox(table: ExtractionTable) -> list[float] | None:
    boxes = [cell.bbox for cell in table.cells if cell.bbox and len(cell.bbox) >= 4]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _review_heading(element: DocumentElement, text: str) -> tuple[int, str]:
    level_value = element.metadata.get("section_level")
    level = (
        int(level_value)
        if isinstance(level_value, int) and not isinstance(level_value, bool) and level_value > 0
        else max(1, len(element.section_path))
    )
    title = text
    if match := MARKDOWN_HEADING.match(text):
        level = len(match.group("marks"))
        title = match.group("title")
    elif match := NUMBERED_HEADING.match(text):
        title = match.group("title")
    return min(6, level), re.sub(r"\s+", " ", title).strip().strip("#")[:80]


def _apply_table_cell_edits(
    extraction: StructuredExtraction,
    cell_edits: list[DocumentTableCellTextEdit],
) -> StructuredExtraction:
    """表セルのテキストのみを差し替える(row/col/span・bbox・構造は保持)。"""
    text_by_cell_key = {(edit.table_id, edit.row, edit.col): edit.text for edit in cell_edits}
    valid_cell_keys = {
        (table.table_id, cell.row, cell.col) for table in extraction.tables for cell in table.cells
    }
    unknown_cells = sorted(
        f"{table_id}:{row},{col}"
        for (table_id, row, col) in text_by_cell_key.keys() - valid_cell_keys
    )
    if unknown_cells:
        raise HTTPException(
            status_code=400,
            detail="存在しない表セルが含まれています。",
        )
    updated_tables = [
        table.model_copy(
            update={
                "cells": [
                    (
                        cell.model_copy(
                            update={"text": text_by_cell_key[(table.table_id, cell.row, cell.col)]}
                        )
                        if (table.table_id, cell.row, cell.col) in text_by_cell_key
                        else cell
                    )
                    for cell in table.cells
                ]
            }
        )
        for table in extraction.tables
    ]
    return extraction.model_copy(update={"tables": updated_tables})


@router.post("/{document_id}/reject", response_model=ApiResponse[DocumentDetail])
async def reject_document(
    http_request: Request,
    document_id: str,
) -> ApiResponse[DocumentDetail]:
    """REVIEW の文書を却下し、UPLOADED へ戻す(抽出結果は保持・再取込で上書き)。"""
    enforce_rate_limit("ingest", http_request)
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status != FileStatus.REVIEW:
        raise HTTPException(
            status_code=409,
            detail="プレビュー確認待ちの文書のみ却下できます。",
        )
    updated = await oracle.update_document_status(document_id, FileStatus.UPLOADED)
    return ApiResponse(data=updated)


async def _load_source_bytes(
    oracle: OracleClient, document_id: str, detail: DocumentDetail
) -> tuple[bytes, SourceProfile]:
    """保存済み原本を取得し、整合性検証して source_profile を組む(失敗は HTTPException)。

    取込(extract)経路と、案 A の承認後 非 owning parser 再抽出で共有する。
    """
    if detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    try:
        data = await ObjectStorageClient().get(detail.object_storage_path)
    except FileNotFoundError as exc:
        await oracle.update_document_status(
            document_id, FileStatus.ERROR, "原本ファイルが見つかりません。"
        )
        raise HTTPException(status_code=409, detail="原本ファイルが見つかりません。") from exc
    except ValueError as exc:
        await oracle.update_document_status(document_id, FileStatus.ERROR, str(exc))
        raise HTTPException(status_code=400, detail="原本ファイルの参照パスが不正です。") from exc

    if integrity_error := _source_integrity_error(data, detail):
        await oracle.update_document_status(document_id, FileStatus.ERROR, integrity_error)
        raise HTTPException(status_code=409, detail=integrity_error)

    source_profile = build_source_profile(
        original_file_name=(
            detail.source_profile.original_file_name
            if detail.source_profile is not None
            else detail.file_name
        ),
        sanitized_file_name=detail.file_name,
        content_type=detail.content_type,
        file_size_bytes=detail.file_size_bytes,
        content_sha256=detail.content_sha256,
        duplicate_of_document_id=detail.duplicate_of_document_id,
        data=data,
    )
    return data, source_profile


async def _load_prepared_source_bytes(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
) -> tuple[bytes, SourceProfile]:
    """保存済みファイル準備 artifact を取得する。欠落時は原本へ戻さない。"""
    artifact = detail.preprocess_artifact
    if artifact is None or not artifact.object_storage_path:
        raise HTTPException(
            status_code=409,
            detail="処理後ファイルが見つかりません。ファイル準備から再処理してください。",
        )
    try:
        data = await ObjectStorageClient().get(artifact.object_storage_path)
    except FileNotFoundError as exc:
        await oracle.update_document_status(
            document_id,
            FileStatus.ERROR,
            "処理後ファイルが見つかりません。ファイル準備から再処理してください。",
        )
        raise HTTPException(
            status_code=409,
            detail="処理後ファイルが見つかりません。ファイル準備から再処理してください。",
        ) from exc
    except ValueError as exc:
        await oracle.update_document_status(document_id, FileStatus.ERROR, str(exc))
        raise HTTPException(status_code=400, detail="処理後ファイルの参照パスが不正です。") from exc

    if artifact.sha256 and _sha256_hex(data) != artifact.sha256:
        message = "処理後ファイルの SHA-256 がファイル準備時と一致しません。"
        await oracle.update_document_status(document_id, FileStatus.ERROR, message)
        raise HTTPException(status_code=409, detail=message)

    source_profile = _source_profile_for_detail(detail)
    return data, source_profile


async def _ingest_existing_document(
    document_id: str,
    *,
    force: bool = False,
    use_prepared_artifact: bool = False,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
) -> DocumentDetail:
    """保存済み原本を検証して取込パイプラインへ渡す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status in (
        FileStatus.PREPROCESSING,
        FileStatus.INGESTING,
        FileStatus.CHUNKING,
        FileStatus.INDEXING,
    ):
        raise HTTPException(status_code=409, detail="このドキュメントは現在取込中です。")
    if detail.status == FileStatus.INDEXED and not force:
        return detail
    if use_prepared_artifact:
        data, source_profile = await _load_prepared_source_bytes(oracle, document_id, detail)
        ingest_content_type = (
            detail.preprocess_artifact.content_type
            if detail.preprocess_artifact is not None and detail.preprocess_artifact.content_type
            else "application/octet-stream"
        )
        prepared_artifact = detail.preprocess_artifact
    else:
        data, source_profile = await _load_source_bytes(oracle, document_id, detail)
        ingest_content_type = detail.content_type or "application/octet-stream"
        prepared_artifact = None
    effective_settings, processing_config = await _resolve_ingestion_settings(oracle, document_id)
    plan, _configs = await _materialization_plan_for_document(
        oracle,
        detail,
        global_settings=effective_settings,
    )
    ingest_prompt = "ドキュメントを日本語で OCR し、本文テキストを抽出してください。"
    if plan is None or not plan.chunk_sets:
        chunk_set_id = _document_chunk_set_id(detail, effective_settings)
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.ingest(
            document_id=document_id,
            image_bytes=data,
            prompt=ingest_prompt,
            content_type=ingest_content_type,
            source_profile=source_profile,
            chunk_set_id=chunk_set_id,
            original_object_storage_path=detail.object_storage_path,
            prepared_artifact=prepared_artifact,
            cancel_checker=cancel_checker,
        )
        await _reconcile_document_chunk_sets(
            oracle,
            document_id,
            result,
            chunk_set_id,
            effective_settings,
            processing_config,
        )
        return result
    # plan 実体化: 抽出グループ(parser×preprocess)ごとに extract 1 回 → 各 chunking で index。
    result = detail
    recipe_groups = plan.chunk_sets_by_extraction_recipe()
    total_chunk_sets = sum(len(chunk_set_ids) for chunk_set_ids in recipe_groups.values())
    processed_chunk_sets = 0
    for _recipe_id, chunk_set_ids in recipe_groups.items():
        for index, chunk_set_id in enumerate(chunk_set_ids):
            pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
            processed_chunk_sets += 1
            # 成功 metric/audit は最後の chunk_set でのみ出し、1 文書 1 論理取込に集約する。
            record_outcome = processed_chunk_sets == total_chunk_sets
            if index == 0:
                result = await pipeline.ingest(
                    document_id=document_id,
                    image_bytes=data,
                    prompt=ingest_prompt,
                    content_type=ingest_content_type,
                    source_profile=source_profile,
                    chunk_set_id=chunk_set_id,
                    record_outcome=record_outcome,
                    original_object_storage_path=detail.object_storage_path,
                    prepared_artifact=prepared_artifact,
                    cancel_checker=cancel_checker,
                )
                if result.status == FileStatus.REVIEW:
                    # REVIEW ゲート ON: 抽出は REVIEW で停止。残りは承認後に CHUNK→INDEX する。
                    return result
            else:
                # 同抽出(同 parser/前処理)の chunking 変種: 抽出を再利用して re-chunk。
                result = await pipeline.index_reviewed(
                    document_id,
                    chunk_set_id=chunk_set_id,
                    record_outcome=record_outcome,
                    cancel_checker=cancel_checker,
                )
    await _reconcile_plan_chunk_sets(
        oracle, document_id, result, plan, effective_settings, processing_config
    )
    return result


async def _chunk_reviewed_document(
    document_id: str,
    *,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
) -> DocumentDetail:
    """REVIEW で承認済みの文書を CHUNK だけ実行する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status not in (FileStatus.REVIEW, FileStatus.CHUNKING):
        raise HTTPException(
            status_code=409,
            detail="プレビュー確認待ちの文書のみ Chunk 作成できます。",
        )
    effective_settings, processing_config = await _resolve_ingestion_settings(oracle, document_id)
    plan, _configs = await _materialization_plan_for_document(
        oracle,
        detail,
        global_settings=effective_settings,
    )
    if plan is None or not plan.chunk_sets:
        chunk_set_id = _document_chunk_set_id(detail, effective_settings)
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.chunk_reviewed(
            document_id, chunk_set_id=chunk_set_id, cancel_checker=cancel_checker
        )
        await _reconcile_document_chunk_sets_chunked(
            oracle,
            document_id,
            result,
            chunk_set_id,
            effective_settings,
            processing_config,
        )
        return result
    # 3 層モデル: plan は常に単一 extraction recipe。保存済み extraction から chunk 化する。
    result = detail
    chunk_set_ids = sorted(plan.chunk_sets)
    for index, chunk_set_id in enumerate(chunk_set_ids):
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        # 成功 metric/audit は最後の chunk_set でのみ出し、1 文書 1 論理取込に集約する。
        result = await pipeline.chunk_reviewed(
            document_id,
            chunk_set_id=chunk_set_id,
            record_outcome=index == len(chunk_set_ids) - 1,
            cancel_checker=cancel_checker,
        )
    await _reconcile_plan_chunk_sets_chunked(
        oracle, document_id, result, plan, effective_settings, processing_config
    )
    return result


async def _index_reviewed_document(
    document_id: str,
    *,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
) -> DocumentDetail:
    """CHUNKED の文書を後段(embedding/index)だけ実行する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status not in (FileStatus.CHUNKED, FileStatus.INDEXING):
        raise HTTPException(
            status_code=409,
            detail="Chunk 確認済みの文書のみ索引できます。",
        )
    effective_settings, processing_config = await _resolve_ingestion_settings(oracle, document_id)
    plan, _configs = await _materialization_plan_for_document(
        oracle,
        detail,
        global_settings=effective_settings,
    )
    if plan is None or not plan.chunk_sets:
        chunk_set_id = _document_chunk_set_id(detail, effective_settings)
        if chunk_set_id is None:
            raise HTTPException(status_code=409, detail="索引対象の chunk_set がありません。")
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.index_chunked(
            document_id, chunk_set_id=chunk_set_id, cancel_checker=cancel_checker
        )
        await _reconcile_document_chunk_sets(
            oracle,
            document_id,
            result,
            chunk_set_id,
            effective_settings,
            processing_config,
        )
        return result
    result = detail
    chunk_set_ids = sorted(plan.chunk_sets)
    for index, chunk_set_id in enumerate(chunk_set_ids):
        # 3 層モデル: レシピは文書単位(global)。
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.index_chunked(
            document_id,
            chunk_set_id=chunk_set_id,
            record_outcome=index == len(chunk_set_ids) - 1,
            cancel_checker=cancel_checker,
        )
    await _reconcile_plan_chunk_sets(
        oracle, document_id, result, plan, effective_settings, processing_config
    )
    return result


async def _reconcile_plan_chunk_sets(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
    plan: MaterializationPlan,
    effective_settings: Settings,
    processing_config: DocumentProcessingConfig,
) -> None:
    """plan の各 chunk_set を永続化し、文書の serving を確定、plan に無い chunk_set を GC する。

    chunk は save_index で挿入時タグ付け済み。serving 設定 / extraction artifact まで揃って
    初めて検索可能な INDEXED とみなすため、失敗時は ERROR に戻す。
    """
    if detail.status != FileStatus.INDEXED:
        return
    _, effective_config = _merge_document_processing_config(processing_config)
    recipe_snapshot = _processing_recipe_snapshot(processing_config, effective_config)
    try:
        for chunk_set_id in plan.chunk_sets:
            chunk_count = await oracle.count_chunk_set_chunks(chunk_set_id)
            extraction_recipe_id = plan.extraction_recipe_for_chunk_set(chunk_set_id)
            await oracle.upsert_chunk_set(
                chunk_set_id=chunk_set_id,
                document_id=document_id,
                extraction_recipe_id=extraction_recipe_id,
                recipe_subset=recipe_snapshot,
            )
            await oracle.mark_chunk_set_indexed(
                chunk_set_id=chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
            )
            if extraction_recipe_id is not None:
                await _record_document_extraction_artifact(
                    oracle,
                    detail,
                    extraction_recipe_id=extraction_recipe_id,
                    settings=effective_settings,
                )
        # 3 層モデル: 文書の serving chunk_set を設定(単一レシピなので plan の chunk_set)。
        serving_chunk_sets = sorted(plan.chunk_sets)
        if serving_chunk_sets:
            await oracle.set_document_serving_chunk_set(
                document_id=document_id, chunk_set_id=serving_chunk_sets[0]
            )
        await _reconcile_plan_artifact_layers(oracle, document_id, detail, plan, effective_settings)
        await oracle.delete_document_chunk_sets_except(
            document_id=document_id, keep_chunk_set_ids=list(plan.chunk_sets)
        )
        await oracle.delete_document_extractions_except(
            document_id=document_id, keep_extraction_ids=list(plan.extraction_recipes)
        )
        if plan.truncated_extractions:
            logger.warning(
                "抽出数が上限を超え %d 件を打ち切りました(document_id=%s)。",
                len(plan.truncated_extractions),
                document_id,
            )
    except Exception as exc:
        logger.warning(
            "chunk_set plan reconcile に失敗しました。document_id=%s",
            document_id,
            exc_info=True,
        )
        await oracle.update_document_status(
            document_id,
            FileStatus.ERROR,
            CHUNK_SET_PUBLISH_ERROR_MESSAGE,
        )
        raise IngestionUserError(CHUNK_SET_PUBLISH_ERROR_MESSAGE) from exc


async def _reconcile_plan_chunk_sets_chunked(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
    plan: MaterializationPlan,
    effective_settings: Settings,
    processing_config: DocumentProcessingConfig,
) -> None:
    """plan の各 chunk_set を CHUNKED として永続化する。KB binding は INDEX 後に作る。"""
    if detail.status != FileStatus.CHUNKED:
        return
    _, effective_config = _merge_document_processing_config(processing_config)
    recipe_snapshot = _processing_recipe_snapshot(processing_config, effective_config)
    try:
        for chunk_set_id in plan.chunk_sets:
            chunk_count = await oracle.count_chunk_set_chunks(chunk_set_id)
            extraction_recipe_id = plan.extraction_recipe_for_chunk_set(chunk_set_id)
            await oracle.upsert_chunk_set(
                chunk_set_id=chunk_set_id,
                document_id=document_id,
                extraction_recipe_id=extraction_recipe_id,
                recipe_subset=recipe_snapshot,
                status="CHUNKED",
            )
            await oracle.mark_chunk_set_chunked(chunk_set_id=chunk_set_id, chunk_count=chunk_count)
            if extraction_recipe_id is not None:
                await _record_document_extraction_artifact(
                    oracle,
                    detail,
                    extraction_recipe_id=extraction_recipe_id,
                    settings=effective_settings,
                )
        await oracle.delete_document_chunk_sets_except(
            document_id=document_id, keep_chunk_set_ids=list(plan.chunk_sets)
        )
        await oracle.delete_document_extractions_except(
            document_id=document_id, keep_extraction_ids=list(plan.extraction_recipes)
        )
    except Exception as exc:
        logger.warning(
            "chunk_set chunk plan reconcile に失敗しました。document_id=%s",
            document_id,
            exc_info=True,
        )
        await oracle.update_document_status(
            document_id,
            FileStatus.ERROR,
            CHUNK_SET_PUBLISH_ERROR_MESSAGE,
        )
        raise IngestionUserError(CHUNK_SET_PUBLISH_ERROR_MESSAGE) from exc


async def _reconcile_plan_artifact_layers(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
    plan: MaterializationPlan,
    effective_settings: Settings,
) -> None:
    """plan に含まれる派生 layer の状態を永続化する。"""
    configs = dict(await oracle.list_document_knowledge_base_configs(document_id))
    effective_by_kb = _effective_ingestion_settings_by_kb(effective_settings, configs)
    for chunk_set_id in plan.chunk_sets:
        for layer, user_label in (
            ("metadata", "項目抽出"),
            ("graph", "関係情報"),
            ("navigation", "ナビゲーション"),
        ):
            requested_ids = _requested_layer_ids_for_chunk_set(
                chunk_set_id,
                plan,
                effective_by_kb,
                layer=layer,
            )
            for layer_id in requested_ids:
                status, reason = _materialized_layer_state(
                    layer=layer,
                    user_label=user_label,
                    detail=detail,
                    settings=effective_settings,
                )
                await oracle.upsert_artifact_layer(
                    layer_id=layer_id,
                    layer_kind=layer,
                    parent_chunk_set_id=chunk_set_id,
                    document_id=document_id,
                    requested=True,
                    status=status.value,
                    reason=reason,
                    metrics=_layer_metrics(layer, detail.extraction),
                )


def _materialized_layer_state(
    *,
    layer: str,
    user_label: str,
    detail: DocumentDetail,
    settings: Settings,
) -> tuple[DocumentLayerStatusName, str]:
    if not detail.extraction:
        return (
            DocumentLayerStatusName.NEEDS_REINGEST,
            (
                f"{user_label}の作成に必要な抽出 artifact がありません。"
                "現在の構築設定で再取込してください。"
            ),
        )
    if layer == "metadata":
        return _metadata_layer_state(user_label, detail.extraction, settings)
    if layer == "navigation":
        node_count = _navigation_node_count(detail.extraction)
        if node_count > 0:
            return (
                DocumentLayerStatusName.MATERIALIZED,
                (
                    f"{user_label}は保存済み抽出 artifact から "
                    f"{node_count} 件の章節として実体化済みです。"
                ),
            )
        return (
            DocumentLayerStatusName.PLANNED_ONLY,
            f"{user_label}は要求されていますが、章節構造を抽出できていません。",
        )
    return (
        DocumentLayerStatusName.PLANNED_ONLY,
        f"{user_label}は構築計画に含まれていますが、まだ実体化していません。",
    )


def _metadata_layer_state(
    user_label: str,
    extraction: Mapping[str, object],
    settings: Settings,
) -> tuple[DocumentLayerStatusName, str]:
    """項目抽出と図表要約を機能別に判定し、有効な機能すべてに成果物があれば実体化とする。"""
    field_enabled = bool(getattr(settings, "rag_field_extraction_enabled", False))
    asset_enabled = bool(getattr(settings, "rag_asset_summary_enabled", False))
    reasons: list[str] = []
    if field_enabled and not _fields_materialized(extraction):
        if not load_field_schema().fields:
            reasons.append(
                "項目抽出は有効ですが、抽出する項目定義(スキーマ)が未設定のため実行されません。"
                "検索・回答設定で項目定義を登録してから再取込してください"
            )
        else:
            reasons.append("項目抽出の成果物がまだありません")
    if asset_enabled and not _asset_summaries_materialized(extraction):
        reasons.append("図表要約の成果物がまだありません")
    if reasons:
        return (DocumentLayerStatusName.PLANNED_ONLY, "。".join(reasons) + "。")
    if field_enabled or asset_enabled:
        return (
            DocumentLayerStatusName.MATERIALIZED,
            f"{user_label}は保存済み抽出 artifact から実体化済みです。",
        )
    # どちらも無効なのに layer が要求された場合は旧来の payload 有無で判定する。
    if _fields_materialized(extraction) or _asset_summaries_materialized(extraction):
        return (
            DocumentLayerStatusName.MATERIALIZED,
            f"{user_label}は保存済み抽出 artifact から実体化済みです。",
        )
    return (
        DocumentLayerStatusName.PLANNED_ONLY,
        f"{user_label}は構築計画に含まれていますが、まだ実体化していません。",
    )


def _fields_materialized(extraction: Mapping[str, object]) -> bool:
    return bool(extraction.get("fields"))


def _asset_summaries_materialized(extraction: Mapping[str, object]) -> bool:
    assets = extraction.get("assets")
    if isinstance(assets, Sequence):
        for asset in assets:
            if isinstance(asset, Mapping) and asset.get("summary"):
                return True
    return bool(extraction.get("asset_summary"))


def _layer_metrics(layer: str, extraction: Mapping[str, object] | None) -> dict[str, object]:
    if not extraction:
        return {}
    if layer == "navigation":
        return {"navigation_node_count": _navigation_node_count(extraction)}
    if layer != "metadata":
        return {}
    return {
        "field_count": _metadata_item_count(extraction.get("fields")),
        "asset_count": _metadata_item_count(extraction.get("assets")),
        "has_asset_summary": bool(extraction.get("asset_summary")),
    }


def _navigation_node_count(extraction: Mapping[str, object]) -> int:
    """保存済み extraction から決定論的 navigation node 数を数える。"""
    nodes = _navigation_nodes_from_extraction(extraction)
    return len(nodes)


def _navigation_nodes_from_extraction(
    extraction: Mapping[str, object],
) -> list[DocumentNavigationNode]:
    if not extraction:
        return []
    try:
        structured = StructuredExtraction.model_validate(dict(extraction))
    except Exception:
        return []
    return list(structured.navigation or build_navigation_tree(structured))


def _metadata_item_count(value: object) -> int:
    return len(value) if isinstance(value, list | tuple) else 0


def _document_chunk_set_id(detail: DocumentDetail, settings: Settings) -> str | None:
    """文書の content_sha256 と effective 取込設定から chunk_set_id を求める(無ければ None)。"""
    if not detail.content_sha256:
        return None
    return compute_chunk_set_id(detail.content_sha256, settings)


def _document_extraction_recipe_id(detail: DocumentDetail, settings: Settings) -> str | None:
    """文書の content_sha256 と effective 解析設定から extraction_recipe_id を求める。"""
    if not detail.content_sha256:
        return None
    return compute_extraction_recipe_id(detail.content_sha256, settings)


def _extraction_recipe_subset(settings: Settings) -> dict[str, object]:
    """extraction recipe の人が読める snapshot。正規 ID は variant_keys 側の hash を正とする。"""
    return extraction_recipe_subset(settings)


async def _record_document_extraction_artifact(
    oracle: OracleClient,
    detail: DocumentDetail,
    *,
    extraction_recipe_id: str | None,
    settings: Settings,
    status: DocumentLayerStatusName = DocumentLayerStatusName.MATERIALIZED,
    reason: str | None = None,
) -> None:
    """extraction recipe 単位の抽出 artifact 状態を保存する。"""
    if extraction_recipe_id is None:
        return
    await oracle.upsert_document_extraction_artifact(
        document_id=detail.id,
        extraction_recipe_id=extraction_recipe_id,
        source_sha256=detail.content_sha256,
        recipe_subset=_extraction_recipe_subset(settings),
        status=status.value,
        reason=reason,
        metrics=_extraction_metrics(detail.extraction),
    )


def _extraction_metrics(extraction: Mapping[str, object] | None) -> dict[str, object]:
    if not extraction:
        return {}
    return {
        "element_count": _metadata_item_count(extraction.get("elements")),
        "table_count": _metadata_item_count(extraction.get("tables")),
        "asset_count": _metadata_item_count(extraction.get("assets")),
        "field_count": _metadata_item_count(extraction.get("fields")),
    }


async def _reconcile_document_chunk_sets(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
    chunk_set_id: str | None,
    effective_settings: Settings,
    processing_config: DocumentProcessingConfig,
) -> None:
    """取込後、materialize した chunk_set を記録し文書の serving を確定する(planner 駆動の基盤)。

    chunk は save_index で**挿入時に chunk_set_id タグ付け済み**。本関数は chunk_set 行の永続化・
    serving 設定・旧 chunk_set(とその chunk、未タグ chunk)の GC を行う。serving 確定まで揃って
    初めて検索可能な INDEXED とみなすため、失敗時は ERROR に戻す。
    """
    if detail.status != FileStatus.INDEXED or chunk_set_id is None:
        return
    try:
        chunk_count = await oracle.count_document_chunks(document_id)
        extraction_recipe_id = _document_extraction_recipe_id(detail, effective_settings)
        _, effective_config = _merge_document_processing_config(processing_config)
        await oracle.upsert_chunk_set(
            chunk_set_id=chunk_set_id,
            document_id=document_id,
            extraction_recipe_id=extraction_recipe_id,
            recipe_subset=_processing_recipe_snapshot(processing_config, effective_config),
        )
        await oracle.mark_chunk_set_indexed(
            chunk_set_id=chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
        )
        await _record_document_extraction_artifact(
            oracle,
            detail,
            extraction_recipe_id=extraction_recipe_id,
            settings=effective_settings,
        )
        # 取込設定変更で生じた旧 chunk_set とその chunk(+未タグ chunk)を削除し、keep だけ残す。
        await oracle.delete_stale_document_chunk_sets(
            document_id=document_id, keep_chunk_set_id=chunk_set_id
        )
        # 3 層モデル: この単一 chunk_set を文書の serving にする(retrieval はこれを検索対象)。
        # 所属 KB は membership(rag_document_knowledge_bases)が正本で、別表 binding は持たない。
        await oracle.set_document_serving_chunk_set(
            document_id=document_id, chunk_set_id=chunk_set_id
        )
    except Exception as exc:
        logger.warning(
            "chunk_set reconcile に失敗しました。document_id=%s",
            document_id,
            exc_info=True,
        )
        await oracle.update_document_status(
            document_id,
            FileStatus.ERROR,
            CHUNK_SET_PUBLISH_ERROR_MESSAGE,
        )
        raise IngestionUserError(CHUNK_SET_PUBLISH_ERROR_MESSAGE) from exc


async def _reconcile_document_chunk_sets_chunked(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
    chunk_set_id: str | None,
    effective_settings: Settings,
    processing_config: DocumentProcessingConfig,
) -> None:
    """CHUNK 後、chunk_set 行だけを記録する。KB binding は INDEX 完了まで作らない。"""
    if detail.status != FileStatus.CHUNKED or chunk_set_id is None:
        return
    try:
        chunk_count = await oracle.count_chunk_set_chunks(chunk_set_id)
        extraction_recipe_id = _document_extraction_recipe_id(detail, effective_settings)
        _, effective_config = _merge_document_processing_config(processing_config)
        await oracle.upsert_chunk_set(
            chunk_set_id=chunk_set_id,
            document_id=document_id,
            extraction_recipe_id=extraction_recipe_id,
            recipe_subset=_processing_recipe_snapshot(processing_config, effective_config),
            status="CHUNKED",
        )
        await oracle.mark_chunk_set_chunked(chunk_set_id=chunk_set_id, chunk_count=chunk_count)
        await _record_document_extraction_artifact(
            oracle,
            detail,
            extraction_recipe_id=extraction_recipe_id,
            settings=effective_settings,
        )
        await oracle.delete_stale_document_chunk_sets(
            document_id=document_id, keep_chunk_set_id=chunk_set_id
        )
    except Exception as exc:
        logger.warning(
            "chunk_set chunk reconcile に失敗しました。document_id=%s",
            document_id,
            exc_info=True,
        )
        await oracle.update_document_status(
            document_id,
            FileStatus.ERROR,
            CHUNK_SET_PUBLISH_ERROR_MESSAGE,
        )
        raise IngestionUserError(CHUNK_SET_PUBLISH_ERROR_MESSAGE) from exc


async def _resolve_ingestion_settings(
    oracle: OracleClient,
    document_id: str,
) -> tuple[Settings, DocumentProcessingConfig]:
    """文書上書き > global 既定で有効な処理設定を解決する。KB は参照しない。"""
    config = await oracle.get_document_processing_config(document_id)
    effective, _resolved = _merge_document_processing_config(config)
    return effective, config


def _dispatch_ingestion_job(
    job_id: str,
    *,
    force: bool = False,
) -> None:
    """QUEUED ジョブの消費をワーカーへ通知する。HTTP 内では実行しない。"""
    _ = (job_id, force)
    request_ingestion_worker_wakeup()


def _restore_status_for_cancelled_phase(phase: IngestionJobPhase) -> FileStatus:
    if phase == IngestionJobPhase.INDEX:
        return FileStatus.CHUNKED
    if phase == IngestionJobPhase.CHUNK:
        return FileStatus.REVIEW
    return FileStatus.UPLOADED


async def _enqueue_ingestion_job_for_document(
    document_id: str,
    *,
    force: bool,
    phase: IngestionJobPhase = IngestionJobPhase.PREPROCESS,
) -> IngestionJob:
    """既存ドキュメントを job 化し、必要ならバックグラウンド実行へ渡す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status in (
        FileStatus.PREPROCESSING,
        FileStatus.INGESTING,
        FileStatus.CHUNKING,
        FileStatus.INDEXING,
    ):
        raise HTTPException(status_code=409, detail="このドキュメントは現在取込中です。")

    source_profile = _source_profile_for_detail(detail)
    # 重複スキップは初回取込(PREPROCESS)の入口だけに適用する。PREPROCESSED 以降の段階進行
    # (EXTRACT/承認して解析へ 等)は、既に重複を承知で取込を確定した文書の続きなので skip しない
    # (さもないと重複文書は承認しても解析中へ進めず PREPROCESSED で詰まる)。
    if (
        detail.duplicate_of_document_id is not None
        and not force
        and phase == IngestionJobPhase.PREPROCESS
    ):
        return await _create_ingestion_job_record(
            oracle=oracle,
            document_id=document_id,
            parser_profile=source_profile.parser_profile,
            quality_warnings=source_profile.quality_warnings,
            status=IngestionJobStatus.SKIPPED,
            skip_reason="duplicate_content",
            phase=phase,
        )
    if source_profile.unsupported_reason and not force:
        return await _create_ingestion_job_record(
            oracle=oracle,
            document_id=document_id,
            parser_profile=source_profile.parser_profile,
            quality_warnings=source_profile.quality_warnings,
            status=IngestionJobStatus.SKIPPED,
            skip_reason=source_profile.unsupported_reason,
            phase=phase,
        )
    if phase == IngestionJobPhase.CHUNK:
        return await _enqueue_chunk_phase_job_for_document(document_id, force=force)
    if phase == IngestionJobPhase.INDEX:
        return await _enqueue_index_phase_job_for_document(document_id, force=force)
    if detail.status == FileStatus.INDEXED and not force:
        return await _create_ingestion_job_record(
            oracle=oracle,
            document_id=document_id,
            parser_profile=source_profile.parser_profile,
            quality_warnings=source_profile.quality_warnings,
            status=IngestionJobStatus.SKIPPED,
            skip_reason="already_indexed",
            phase=phase,
        )
    if phase == IngestionJobPhase.EXTRACT and (
        detail.preprocess_artifact is None or not detail.preprocess_artifact.object_storage_path
    ):
        raise HTTPException(
            status_code=409,
            detail="処理後ファイルが見つかりません。ファイル準備から再処理してください。",
        )
    if phase == IngestionJobPhase.PREPROCESS:
        await _reset_document_outputs_for_extract(
            oracle,
            document_id,
            clear_preprocess_artifact=True,
        )
    else:
        await _reset_document_outputs_for_extract(oracle, document_id)

    job = await _create_ingestion_job_record(
        oracle=oracle,
        document_id=document_id,
        parser_profile=source_profile.parser_profile,
        quality_warnings=source_profile.quality_warnings,
        phase=phase,
    )
    _dispatch_ingestion_job(job.id, force=force)
    return job


async def _enqueue_index_phase_job_for_document(
    document_id: str,
    *,
    force: bool = False,
) -> IngestionJob:
    """CHUNKED 文書に対し INDEX フェーズ job を投入する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status == FileStatus.ERROR and force:
        pass
    elif detail.status not in (FileStatus.CHUNKED, FileStatus.INDEXED):
        raise HTTPException(
            status_code=409,
            detail="Chunk 確認済みの文書のみ索引できます。",
        )
    if detail.status == FileStatus.INDEXED and not force:
        source_profile = _source_profile_for_detail(detail)
        return await _create_ingestion_job_record(
            oracle=oracle,
            document_id=document_id,
            parser_profile=source_profile.parser_profile,
            quality_warnings=source_profile.quality_warnings,
            status=IngestionJobStatus.SKIPPED,
            skip_reason="already_indexed",
            phase=IngestionJobPhase.INDEX,
        )
    if force:
        await oracle.reset_document_index_outputs(document_id, status=FileStatus.CHUNKED)
    source_profile = _source_profile_for_detail(detail)
    job = await _create_ingestion_job_record(
        oracle=oracle,
        document_id=document_id,
        parser_profile=source_profile.parser_profile,
        quality_warnings=source_profile.quality_warnings,
        phase=IngestionJobPhase.INDEX,
    )
    _dispatch_ingestion_job(job.id)
    return job


async def _enqueue_chunk_phase_job_for_document(
    document_id: str,
    *,
    force: bool = False,
) -> IngestionJob:
    """REVIEW 文書に対し CHUNK フェーズ job を投入する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status == FileStatus.ERROR and force:
        pass
    elif detail.status not in (FileStatus.REVIEW, FileStatus.CHUNKED, FileStatus.INDEXED):
        raise HTTPException(
            status_code=409,
            detail="抽出確認済みの文書のみ Chunk 作成できます。",
        )
    if force or detail.status != FileStatus.REVIEW:
        await oracle.reset_document_chunk_outputs(document_id, status=FileStatus.REVIEW)
    source_profile = _source_profile_for_detail(detail)
    job = await _create_ingestion_job_record(
        oracle=oracle,
        document_id=document_id,
        parser_profile=source_profile.parser_profile,
        quality_warnings=source_profile.quality_warnings,
        phase=IngestionJobPhase.CHUNK,
    )
    _dispatch_ingestion_job(job.id)
    return job


async def _enqueue_failed_segment_retry_job_for_document(
    document_id: str,
    *,
    recipe_id: str | None = None,
) -> IngestionJob:
    """FAILED segment checkpoint のみを対象にした再試行 job を投入する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if recipe_id is not None:
        recipe = await oracle.get_document_recipe(document_id, recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="レシピが見つかりません。")
        raw_artifact = recipe.get("preprocess_artifact")
        artifact = DocumentPreprocessArtifact.model_validate(raw_artifact) if raw_artifact else None
        if artifact is None or not artifact.object_storage_path:
            raise HTTPException(
                status_code=409,
                detail="処理後ファイルが見つかりません。ファイル準備から再処理してください。",
            )
        segments = await oracle.list_ingestion_segments(document_id)
        if not any(
            segment.recipe_id == recipe_id and segment.status == "FAILED" for segment in segments
        ):
            raise HTTPException(
                status_code=409,
                detail="再試行対象の失敗 segment がありません。",
            )
        try:
            return await _enqueue_ingestion_job_for_recipe(
                document_id,
                recipe_id,
                phase=IngestionJobPhase.EXTRACT,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if detail.status in (
        FileStatus.PREPROCESSING,
        FileStatus.INGESTING,
        FileStatus.CHUNKING,
        FileStatus.INDEXING,
    ):
        raise HTTPException(status_code=409, detail="このドキュメントは現在取込中です。")
    segments = await oracle.list_ingestion_segments(document_id)
    if not any(segment.status == "FAILED" for segment in segments):
        raise HTTPException(
            status_code=409,
            detail="再試行対象の失敗 segment がありません。",
        )
    return await _enqueue_ingestion_job_for_document(
        document_id,
        force=True,
    )


async def _list_delete_blocking_ingestion_jobs(
    oracle: OracleClient,
    document_id: str,
) -> list[IngestionJob]:
    """削除を止めるべき実行中の取込 job を返す。"""
    jobs: list[IngestionJob] = []
    for status in DELETE_BLOCKING_INGESTION_STATUSES:
        jobs.extend(await oracle.list_document_ingestion_jobs(document_id, status=status))
    return jobs


async def _create_ingestion_job(result: UploadResult) -> IngestionJob:
    """upload 結果から取込 job を作る。重複・未対応は SKIPPED として記録する。"""
    is_duplicate = result.duplicate_of_document_id is not None
    unsupported_reason = result.source_profile.unsupported_reason
    skip_reason = "duplicate_content" if is_duplicate else unsupported_reason
    return await _create_ingestion_job_record(
        oracle=OracleClient(),
        document_id=result.id,
        parser_profile=result.source_profile.parser_profile,
        quality_warnings=result.source_profile.quality_warnings,
        status=IngestionJobStatus.SKIPPED if skip_reason else IngestionJobStatus.QUEUED,
        skip_reason=skip_reason,
    )


async def _create_ingestion_job_record(
    *,
    oracle: OracleClient,
    document_id: str,
    parser_profile: str,
    quality_warnings: list[str],
    status: IngestionJobStatus = IngestionJobStatus.QUEUED,
    phase: IngestionJobPhase = IngestionJobPhase.PREPROCESS,
    skip_reason: str | None = None,
    settings_overrides: dict[str, object] | None = None,
    recipe_id: str | None = None,
    recipe_revision: int | None = None,
) -> IngestionJob:
    """取込 job を永続化する。``settings_overrides`` 付きはレシピ実験(Phase 3b)ジョブ。"""
    if recipe_id is None:
        ensure_recipe = getattr(oracle, "ensure_default_document_recipe", None)
        if callable(ensure_recipe):
            recipe = await ensure_recipe(document_id)
            recipe_id = str(recipe["recipe_id"])
            recipe_revision = int(str(recipe.get("config_revision") or 1))
    queued_at = datetime.now(UTC)
    settings = get_settings()
    job = IngestionJob(
        id=uuid4().hex,
        document_id=document_id,
        status=status,
        phase=phase,
        parser_profile=parser_profile,
        quality_warnings=quality_warnings,
        settings_overrides=settings_overrides,
        recipe_id=recipe_id,
        recipe_revision=recipe_revision,
        skip_reason=skip_reason,
        max_attempts=settings.ingestion_job_max_attempts,
        queued_at=queued_at,
        finished_at=queued_at if status == IngestionJobStatus.SKIPPED else None,
    )
    return await oracle.create_ingestion_job(job)


async def _reset_document_outputs_for_extract(
    oracle: OracleClient,
    document_id: str,
    *,
    clear_preprocess_artifact: bool = False,
) -> None:
    """EXTRACT 再投入前に旧抽出・checkpoint・派生結果を初期化する。"""
    reset_outputs = getattr(oracle, "reset_document_ingestion_outputs", None)
    if callable(reset_outputs):
        await reset_outputs(
            document_id,
            status=FileStatus.UPLOADED,
            clear_preprocess_artifact=clear_preprocess_artifact,
        )
        return
    await oracle.update_document_status(document_id, FileStatus.UPLOADED)


def _source_profile_for_detail(detail: DocumentDetail) -> SourceProfile:
    """保存済み DocumentDetail から parser profile を復元する。"""
    if detail.source_profile is not None:
        return detail.source_profile
    return build_source_profile(
        original_file_name=detail.file_name,
        sanitized_file_name=detail.file_name,
        content_type=detail.content_type,
        file_size_bytes=detail.file_size_bytes,
        content_sha256=detail.content_sha256,
        duplicate_of_document_id=detail.duplicate_of_document_id,
        data=None,
    )


def _document_ingestion_segments(
    detail: DocumentDetail,
    jobs: list[IngestionJob],
    effective_settings: Settings | None = None,
) -> list[IngestionSegment]:
    """保存済み extraction/job から segment view を推定する。"""
    source_profile = _source_profile_for_detail(detail)
    page_start, page_end = _document_page_range(detail.extraction)
    latest_job = max(jobs, key=lambda job: job.queued_at, default=None)
    attempt_count = latest_job.attempt_count if latest_job is not None else 0
    status = _segment_status_from_detail(detail, latest_job)
    error_message = detail.error_message or (latest_job.error_message if latest_job else None)
    parser_backend = _parser_backend_from_extraction(detail.extraction, source_profile)
    parser_profile = _parser_profile_from_extraction(detail.extraction, source_profile)
    progress_unit = "page" if page_start is not None and page_end is not None else "source"
    planned_parser = _planned_parser_backend_for_unmaterialized_extract(
        detail,
        latest_job,
        effective_settings,
    )
    if planned_parser is not None:
        parser_backend = planned_parser
        parser_profile = planned_parser
    return [
        IngestionSegment(
            segment_id=f"{detail.id}:source",
            document_id=detail.id,
            status=status,
            parser_backend=parser_backend,
            parser_profile=parser_profile,
            page_start=page_start,
            page_end=page_end,
            progress_unit=progress_unit,
            progress_start=page_start,
            progress_end=page_end,
            attempt_count=attempt_count,
            artifact_path=(
                _extraction_artifact_path(detail.extraction) or detail.object_storage_path
            ),
            error_code="ingestion_error" if error_message else None,
            error_message=error_message,
        )
    ]


def _planned_parser_backend_for_unmaterialized_extract(
    detail: DocumentDetail,
    latest_job: IngestionJob | None,
    effective_settings: Settings | None,
) -> str | None:
    """未実体化の抽出では upload 時判定ではなく現在の明示 parser を表示に使う。"""
    if effective_settings is None or _extraction_has_parser_context(detail.extraction):
        return None
    planned = _selected_parser_backend(effective_settings)
    if planned is None:
        return None
    if (
        latest_job is not None
        and latest_job.phase in {IngestionJobPhase.PREPROCESS, IngestionJobPhase.EXTRACT}
        and latest_job.status
        in {
            IngestionJobStatus.QUEUED,
            IngestionJobStatus.RUNNING,
            IngestionJobStatus.FAILED,
        }
    ):
        return planned
    if detail.status in {
        FileStatus.UPLOADED,
        FileStatus.PREPROCESSING,
        FileStatus.INGESTING,
        FileStatus.ERROR,
    }:
        return planned
    return None


def _selected_parser_backend(settings: Settings) -> str | None:
    """Settings の明示 parser backend を表示用に返す。local は未選択として扱う。"""
    selected = str(getattr(settings, "rag_parser_adapter_backend", "local")).strip()
    if not selected or selected == "local":
        return None
    return selected[:80]


def _extraction_has_parser_context(extraction: Mapping[str, object]) -> bool:
    """保存済み extraction に実 parser 情報があるか判定する。"""
    for container_name in ("quality_report", "parser_artifacts"):
        container = extraction.get(container_name)
        if not isinstance(container, Mapping):
            continue
        for key in ("parser_backend", "parser_profile", "source_parser", "external_adapter"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return True
    return False


def _document_page_range(extraction: Mapping[str, object]) -> tuple[int | None, int | None]:
    """extraction payload からページ範囲を推定する。"""
    pages: set[int] = set()
    raw_pages = extraction.get("pages")
    if isinstance(raw_pages, list):
        for page in raw_pages:
            if isinstance(page, Mapping):
                page_number = page.get("page_number")
                if isinstance(page_number, int) and page_number >= 1:
                    pages.add(page_number)
    raw_elements = extraction.get("elements")
    if isinstance(raw_elements, list):
        for element in raw_elements:
            if isinstance(element, Mapping):
                page_number = element.get("page_number")
                if isinstance(page_number, int) and page_number >= 1:
                    pages.add(page_number)
    raw_tables = extraction.get("tables")
    if isinstance(raw_tables, list):
        pages.update(_page_numbers_from_mappings(raw_tables))
    raw_assets = extraction.get("assets")
    if isinstance(raw_assets, list):
        pages.update(_page_numbers_from_mappings(raw_assets))
    if not pages:
        return None, None
    return min(pages), max(pages)


def _page_numbers_from_mappings(items: list[object]) -> set[int]:
    """tables/assets の first-class metadata からページ番号を集める。"""
    pages: set[int] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        page_number = item.get("page_number")
        if isinstance(page_number, int) and not isinstance(page_number, bool) and page_number >= 1:
            pages.add(page_number)
    return pages


def _parser_backend_from_extraction(
    extraction: Mapping[str, object],
    source_profile: SourceProfile,
) -> str:
    """extraction quality/parser artifacts から parser backend を読む。"""
    for container_name in ("quality_report", "parser_artifacts"):
        container = extraction.get(container_name)
        if isinstance(container, Mapping):
            value = container.get("parser_backend")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return source_profile.parser_backend


def _parser_profile_from_extraction(
    extraction: Mapping[str, object],
    source_profile: SourceProfile,
) -> str:
    """extraction quality/parser artifacts から parser profile を読む。"""
    for container_name in ("quality_report", "parser_artifacts"):
        container = extraction.get(container_name)
        if isinstance(container, Mapping):
            value = container.get("parser_profile")
            if isinstance(value, str) and value.strip():
                return value.strip()
            source_parser = container.get("source_parser")
            if isinstance(source_parser, str) and source_parser.strip():
                return source_parser.strip()
            external_adapter = container.get("external_adapter")
            if isinstance(external_adapter, str) and external_adapter.strip():
                return external_adapter.strip()
    return source_profile.parser_profile


def _extraction_artifact_path(extraction: Mapping[str, object]) -> str | None:
    """extraction payload から artifact cache path を読む。"""
    artifacts = extraction.get("parser_artifacts")
    if isinstance(artifacts, Mapping):
        value = artifacts.get("extraction_artifact_path")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _structured_extraction_from_detail(detail: DocumentDetail) -> StructuredExtraction:
    """保存済み extraction JSON を export 用 StructuredExtraction へ正規化する。"""
    try:
        return StructuredExtraction.model_validate(detail.extraction)
    except Exception:
        raw_text = (
            detail.extraction.get("raw_text") if isinstance(detail.extraction, Mapping) else ""
        )
        document_type = (
            detail.extraction.get("document_type")
            if isinstance(detail.extraction, Mapping)
            else None
        )
        return StructuredExtraction(
            raw_text=raw_text if isinstance(raw_text, str) else "",
            document_type=document_type if isinstance(document_type, str) else "ドキュメント",
        )


def _document_extraction_export_content(
    export_format: DocumentExtractionExportFormat,
    extraction: StructuredExtraction,
    payload: Mapping[str, object],
) -> str:
    """export format に応じた文字列表現を返す。"""
    if export_format in {
        DocumentExtractionExportFormat.JSON,
        DocumentExtractionExportFormat.CHUNKS,
    }:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if export_format == DocumentExtractionExportFormat.HTML:
        return _extraction_html(extraction)
    return _extraction_markdown(extraction)


def _document_extraction_export_content_type(
    export_format: DocumentExtractionExportFormat,
) -> str:
    """export payload の media type を返す。"""
    if export_format == DocumentExtractionExportFormat.MARKDOWN:
        return "text/markdown; charset=utf-8"
    if export_format == DocumentExtractionExportFormat.HTML:
        return "text/html; charset=utf-8"
    return "application/json; charset=utf-8"


def _extraction_parser_backend(extraction: StructuredExtraction) -> str | None:
    """quality_report / parser_artifacts から parser backend を読む。"""
    if extraction.quality_report is not None and extraction.quality_report.parser_backend:
        return extraction.quality_report.parser_backend
    value = extraction.parser_artifacts.get("parser_backend")
    return value if isinstance(value, str) and value.strip() else None


def _extraction_parser_profile(extraction: StructuredExtraction) -> str | None:
    """quality_report / parser_artifacts から parser profile を読む。"""
    if extraction.quality_report is not None and extraction.quality_report.parser_profile:
        return extraction.quality_report.parser_profile
    value = extraction.parser_artifacts.get("parser_profile")
    return value if isinstance(value, str) and value.strip() else None


def _extraction_markdown(extraction: StructuredExtraction) -> str:
    """StructuredExtraction を human review しやすい Markdown へ変換する。"""
    lines: list[str] = []
    current_page: int | None = None
    if not extraction.elements and extraction.raw_text:
        lines.append(extraction.raw_text)
    for element in sorted(extraction.elements, key=lambda item: item.order):
        if element.page_number is not None and element.page_number != current_page:
            current_page = element.page_number
            if lines:
                lines.append("")
            lines.append(f"<!-- page: {current_page} -->")
        rendered = _element_markdown(element)
        if rendered:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(rendered)
    for asset in sorted(extraction.assets, key=_asset_sort_key):
        if asset.page_number is not None and asset.page_number != current_page:
            current_page = asset.page_number
            if lines:
                lines.append("")
            lines.append(f"<!-- page: {current_page} -->")
        rendered = _asset_markdown(asset)
        if rendered:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(rendered)
    return "\n".join(lines).strip() or extraction.raw_text


def _element_markdown(element: DocumentElement) -> str:
    """1 element を Markdown block へ変換する。"""
    text = element.text.strip()
    if not text:
        return ""
    if element.kind == "title":
        if text.startswith("#"):
            return text
        level = _markdown_heading_level(element)
        return f"{'#' * level} {text}"
    if element.kind == "code":
        language = _metadata_str(element.metadata.get("code_language"))
        return f"```{language}\n{text}\n```"
    if element.kind == "equation":
        return f"$$\n{text}\n$$"
    if element.kind == "figure":
        return f"> 図: {text}"
    if element.kind == "figure_caption":
        return f"> 図注: {text}"
    if element.kind == "table_caption":
        return f"> 表注: {text}"
    return text


def _asset_markdown(asset: ExtractionAsset) -> str:
    """first-class asset を Markdown 監査行として返す。"""
    labels = [f"> Asset: {asset.kind} `{asset.asset_id}`"]
    if asset.page_number is not None:
        labels.append(f"> page: {asset.page_number}")
    if asset.bbox:
        labels.append(f"> bbox: {','.join(f'{value:g}' for value in asset.bbox)}")
    if asset.alt_text:
        labels.append(f"> alt: {asset.alt_text.strip()}")
    return "\n".join(labels)


def _extraction_html(extraction: StructuredExtraction) -> str:
    """StructuredExtraction を安全に escaped HTML へ変換する。"""
    title = escape(extraction.document_type or "ドキュメント")
    tables_by_element_id = _tables_by_element_id(extraction)
    if not extraction.elements and not extraction.assets:
        body = _html_text_block(extraction.raw_text)
        return f'<article data-document-type="{title}">\n{body}\n</article>'
    lines = [f'<article data-document-type="{title}">']
    current_page: int | None = None
    if not extraction.elements and extraction.raw_text:
        lines.append(_html_text_block(extraction.raw_text))
    for element in sorted(extraction.elements, key=lambda item: item.order):
        if element.page_number is not None and element.page_number != current_page:
            current_page = element.page_number
            lines.append(
                f'  <p class="page-marker" data-page="{current_page}">page {current_page}</p>'
            )
        rendered = _element_html(element, tables_by_element_id=tables_by_element_id)
        if rendered:
            lines.append(rendered)
    for asset in sorted(extraction.assets, key=_asset_sort_key):
        if asset.page_number is not None and asset.page_number != current_page:
            current_page = asset.page_number
            lines.append(
                f'  <p class="page-marker" data-page="{current_page}">page {current_page}</p>'
            )
        lines.append(_asset_html(asset))
    lines.append("</article>")
    return "\n".join(lines)


def _asset_html(asset: ExtractionAsset) -> str:
    """first-class asset を実体埋め込みなしの安全な HTML へ変換する。"""
    attrs = _asset_html_attrs(asset)
    label = asset.alt_text.strip() if asset.alt_text else asset.kind
    return f'  <aside{attrs} class="asset-block">{_html_inline(label)}</aside>'


def _element_html(
    element: DocumentElement,
    *,
    tables_by_element_id: Mapping[str, ExtractionTable],
) -> str:
    """1 element を HTML block へ変換する。source text は必ず escape する。"""
    text = element.text.strip()
    attrs = _element_html_attrs(element)
    if element.kind == "table":
        table = tables_by_element_id.get(element.element_id or "")
        if table is not None and table.cells:
            return _structured_table_html(table, attrs)
    if not text:
        return ""
    if element.kind == "title":
        level = _markdown_heading_level(element)
        return f"  <h{level}{attrs}>{_html_inline(text)}</h{level}>"
    if element.kind == "code":
        language = _metadata_str(element.metadata.get("code_language"))
        class_attr = f' class="language-{escape(language, quote=True)}"' if language else ""
        return f"  <pre{attrs}><code{class_attr}>{escape(text)}</code></pre>"
    if element.kind == "equation":
        return f'  <div{attrs} class="equation">{escape(text)}</div>'
    if element.kind == "figure":
        return f"  <figure{attrs}><figcaption>{_html_inline(text)}</figcaption></figure>"
    if element.kind == "figure_caption":
        return f'  <p{attrs} class="figure-caption">{_html_inline(text)}</p>'
    if element.kind == "table_caption":
        return f'  <p{attrs} class="table-caption">{_html_inline(text)}</p>'
    if element.kind == "table":
        return f'  <pre{attrs} class="table-block">{escape(text)}</pre>'
    if element.kind == "list":
        return f'  <div{attrs} class="list-block">{_html_text_lines(text)}</div>'
    return f"  <p{attrs}>{_html_text_lines(text)}</p>"


def _asset_html_attrs(asset: ExtractionAsset) -> str:
    attrs: list[tuple[str, str]] = [
        ("data-asset-id", asset.asset_id),
        ("data-kind", asset.kind),
    ]
    if asset.page_number is not None:
        attrs.append(("data-page", str(asset.page_number)))
    if asset.bbox:
        attrs.append(("data-bbox", ",".join(f"{value:g}" for value in asset.bbox)))
    return "".join(f' {name}="{escape(value, quote=True)}"' for name, value in attrs)


def _asset_sort_key(asset: ExtractionAsset) -> tuple[int, str]:
    page_number = asset.page_number if asset.page_number is not None else 1_000_000
    return page_number, asset.asset_id


def _tables_by_element_id(extraction: StructuredExtraction) -> dict[str, ExtractionTable]:
    """element_id から first-class table metadata を参照できるようにする。"""
    tables: dict[str, ExtractionTable] = {}
    for table in extraction.tables:
        if table.element_id:
            tables.setdefault(table.element_id, table)
        tables.setdefault(table.table_id, table)
    return tables


def _structured_table_html(
    table: ExtractionTable,
    element_attrs: str,
) -> str:
    """ExtractionTable.cells を実 table として安全に HTML 化する。"""
    table_id_attr = escape(table.table_id, quote=True)
    table_attrs = f'{element_attrs} class="table-block" data-table-id="{table_id_attr}"'
    lines: list[str] = []
    if table.caption:
        lines.append(
            f'  <p class="table-caption" data-table-id="{table_id_attr}">'
            f"{_html_inline(table.caption)}</p>"
        )
    lines.append(f"  <table{table_attrs}>")
    lines.append("    <tbody>")
    for row_index, cells in _table_cells_by_row(table).items():
        lines.append(f'      <tr data-row="{row_index}">')
        for cell in cells:
            tag = "th" if row_index == 0 else "td"
            cell_attrs = _table_cell_html_attrs(table, cell)
            lines.append(f"        <{tag}{cell_attrs}>{_html_text_lines(cell.text)}</{tag}>")
        lines.append("      </tr>")
    lines.append("    </tbody>")
    lines.append("  </table>")
    return "\n".join(lines)


def _table_cells_by_row(table: ExtractionTable) -> dict[int, list[ExtractionTableCell]]:
    rows: dict[int, list[ExtractionTableCell]] = {}
    for cell in sorted(table.cells, key=lambda item: (item.row, item.col)):
        rows.setdefault(cell.row, []).append(cell)
    return rows


def _table_cell_html_attrs(table: ExtractionTable, cell: ExtractionTableCell) -> str:
    row = cell.row
    col = cell.col
    row_span = cell.row_span
    col_span = cell.col_span
    bbox = cell.bbox
    attrs = [
        ("data-table-id", table.table_id),
        ("data-row", str(row)),
        ("data-col", str(col)),
    ]
    if row_span != 1:
        attrs.append(("rowspan", str(row_span)))
    if col_span != 1:
        attrs.append(("colspan", str(col_span)))
    if isinstance(bbox, list) and bbox:
        attrs.append(("data-bbox", ",".join(f"{value:g}" for value in bbox)))
    if formula_ref := _table_cell_metadata_label(cell, "formula_cell_ref"):
        attrs.append(("data-formula-ref", formula_ref))
    if formula_format := _table_cell_metadata_label(cell, "equation_format"):
        attrs.append(("data-formula-format", formula_format))
    if formula := _table_cell_metadata_label(cell, "formula"):
        attrs.append(("data-formula", formula))
    if formula_value := _table_cell_metadata_label(cell, "formula_value"):
        attrs.append(("data-formula-value", formula_value))
    return "".join(f' {name}="{escape(value, quote=True)}"' for name, value in attrs)


def _table_cell_metadata_label(cell: ExtractionTableCell, key: str) -> str | None:
    value = cell.metadata.get(key)
    if isinstance(value, str | int | float):
        cleaned = str(value).strip()
        return cleaned[:1000] if cleaned else None
    return None


def _element_html_attrs(element: DocumentElement) -> str:
    """レビュー時に lineage を追える最小限の data 属性を作る。"""
    attrs: list[tuple[str, str]] = []
    if element.element_id:
        attrs.append(("data-element-id", element.element_id))
    if element.parent_id:
        attrs.append(("data-parent-id", element.parent_id))
    if element.content_kind:
        attrs.append(("data-content-kind", element.content_kind))
    elif element.kind:
        attrs.append(("data-content-kind", element.kind))
    if element.source_parser:
        attrs.append(("data-source-parser", element.source_parser))
    if element.page_number is not None:
        attrs.append(("data-page", str(element.page_number)))
    if element.bbox:
        attrs.append(("data-bbox", ",".join(f"{value:g}" for value in element.bbox)))
    if not attrs:
        return ""
    return "".join(f' {name}="{escape(value, quote=True)}"' for name, value in attrs)


def _html_text_block(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    return f"  <p>{_html_text_lines(cleaned)}</p>"


def _html_text_lines(text: str) -> str:
    return "<br>\n".join(escape(line) for line in text.splitlines())


def _html_inline(text: str) -> str:
    return escape(" ".join(line.strip() for line in text.splitlines() if line.strip()))


def _markdown_heading_level(element: DocumentElement) -> int:
    """element metadata / section_path から Markdown heading level を決める。"""
    metadata_level = _metadata_int(element.metadata.get("section_level"))
    if metadata_level is not None:
        return max(1, min(metadata_level, 6))
    if element.section_path:
        return max(1, min(len(element.section_path), 6))
    return 1


def _metadata_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _metadata_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


async def _document_artifact_paths(
    oracle: OracleClient,
    detail: DocumentDetail,
) -> list[str]:
    """削除対象 document に紐づく抽出 artifact cache path を重複排除して返す。"""
    paths: list[str] = []
    if extraction_artifact_path := _extraction_artifact_path(detail.extraction):
        paths.append(extraction_artifact_path)
    if detail.preprocess_artifact is not None and detail.preprocess_artifact.object_storage_path:
        paths.append(detail.preprocess_artifact.object_storage_path)
    try:
        segments = await oracle.list_ingestion_segments(detail.id)
    except Exception:
        segments = []
    for segment in segments:
        if segment.artifact_path:
            paths.append(segment.artifact_path)
    original_path = detail.object_storage_path
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = path.strip()
        if not normalized or normalized == original_path or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _segment_status_from_detail(
    detail: DocumentDetail,
    latest_job: IngestionJob | None,
) -> str:
    """document/job status を segment status に寄せる。"""
    if latest_job is not None and latest_job.status in {
        IngestionJobStatus.QUEUED,
        IngestionJobStatus.RUNNING,
        IngestionJobStatus.CANCELLED,
    }:
        return latest_job.status.value
    if detail.status == FileStatus.ERROR:
        return "FAILED"
    if detail.status == FileStatus.INDEXED:
        return "SUCCEEDED"
    return detail.status.value


async def _run_ingestion_job(
    job_id: str,
    *,
    propagate_errors: bool = False,
) -> None:
    """キュー投入済み取込 job を実行する。"""
    oracle = OracleClient()
    job = await oracle.claim_ingestion_job(job_id, started_at=datetime.now(UTC))
    if job is None:
        return
    if job.recipe_id is not None:
        await oracle.update_document_recipe_status(
            recipe_id=job.recipe_id,
            status=_PHASE_TO_RUNNING_STATUS[job.phase],
        )

    async def is_cancelled() -> bool:
        current = await oracle.get_ingestion_job(job_id)
        return current is not None and current.status == IngestionJobStatus.CANCELLED

    # レシピ経路で「現在ジョブ完了後に自動投入すべき次フェーズ」を受け取る。投入は現在ジョブが
    # SUCCEEDED になった後(レシピ行ロックのガードを通過できる状態)に行う。
    next_recipe_phase: IngestionJobPhase | None = None
    try:
        if job.recipe_id is not None or job.settings_overrides is not None:
            # 正式レシピは旧 active を維持した隔離 materialize。recipe_id 無しは旧実験互換。
            next_recipe_phase = await _materialize_experiment_candidate(
                oracle, job, cancel_checker=is_cancelled
            )
        elif job.phase == IngestionJobPhase.CHUNK:
            detail = await _chunk_reviewed_document(
                job.document_id,
                cancel_checker=is_cancelled,
            )
            await _enqueue_auto_advance_job(job, detail)
        elif job.phase == IngestionJobPhase.INDEX:
            detail = await _index_reviewed_document(
                job.document_id,
                cancel_checker=is_cancelled,
            )
            await _enqueue_auto_advance_job(job, detail)
        elif job.phase == IngestionJobPhase.EXTRACT:
            current_detail = await oracle.get_document(job.document_id)
            if (
                current_detail is None
                or current_detail.preprocess_artifact is None
                or not current_detail.preprocess_artifact.object_storage_path
            ):
                raise HTTPException(
                    status_code=409,
                    detail="処理後ファイルが見つかりません。ファイル準備から再処理してください。",
                )
            await _reset_document_outputs_for_extract(oracle, job.document_id)
            detail = await _ingest_existing_document(
                job.document_id,
                force=True,
                use_prepared_artifact=True,
                cancel_checker=is_cancelled,
            )
            await _enqueue_auto_advance_job(job, detail)
        else:
            await _reset_document_outputs_for_extract(
                oracle,
                job.document_id,
                clear_preprocess_artifact=True,
            )
            detail = await _ingest_existing_document(
                job.document_id,
                force=True,
                use_prepared_artifact=False,
                cancel_checker=is_cancelled,
            )
            await _enqueue_auto_advance_job(job, detail)
    except HTTPException as exc:
        await _mark_recipe_job_failed(oracle, job, str(exc.detail))
        await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=str(exc.detail),
        )
        logger.info(
            "ingestion_job_user_error",
            extra={
                "job_id": job_id,
                "document_id": job.document_id,
                "status_code": exc.status_code,
            },
        )
        if propagate_errors:
            raise
    except IngestionCancelledError:
        await _restore_recipe_status_after_cancel(oracle, job)
        logger.info(
            "ingestion_job_cancelled",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except IngestionTimeoutError as exc:
        await _mark_recipe_job_failed(oracle, job, str(exc))
        await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=str(exc),
        )
        logger.info(
            "ingestion_job_timeout",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except IngestionUserError as exc:
        await _mark_recipe_job_failed(oracle, job, str(exc))
        await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=str(exc),
        )
        logger.info(
            "ingestion_job_validation_error",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except Exception as exc:
        safe_error = _safe_ingestion_job_error_message(exc)
        await _mark_recipe_job_failed(oracle, job, safe_error)
        await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=safe_error,
        )
        logger.exception(
            "ingestion_job_failed",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    else:
        await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.SUCCEEDED,
        )
        if next_recipe_phase is not None and job.recipe_id is not None:
            # 現在ジョブは SUCCEEDED になったので、同一レシピの次フェーズ job を投入できる
            # (レシピ行ロックのガードを通過する)。抽出は既に成功しているため、投入失敗は
            # 握りつぶして warning に留め、人手の「承認して Chunk 作成」で続行可能にする。
            try:
                await _enqueue_ingestion_job_for_recipe(
                    job.document_id, job.recipe_id, phase=next_recipe_phase
                )
            except Exception:
                logger.warning(
                    "recipe_auto_advance_enqueue_failed",
                    extra={
                        "document_id": job.document_id,
                        "recipe_id": job.recipe_id,
                        "phase": next_recipe_phase.value,
                    },
                    exc_info=True,
                )


async def _mark_recipe_job_failed(
    oracle: OracleClient,
    job: IngestionJob,
    error_message: str,
) -> None:
    """レシピ失敗だけを記録する。既存 active chunk_set は変更しない。

    1 本のジョブが複数工程を通し実行するため、失敗工程は job.phase ではなく
    レシピ行の現在 status(pipeline が工程ごとに更新)から導出する。
    ジョブ開始工程より前へは戻さず、ゲート停止など非実行 status は job.phase に従う。
    """
    if job.recipe_id is None:
        return
    failed_phase = job.phase
    try:
        row = await oracle.get_document_recipe(job.document_id, job.recipe_id)
    except Exception:
        row = None
    if row is not None and row.get("status"):
        current_phase = _RUNNING_STATUS_TO_PHASE.get(FileStatus(str(row["status"])))
        if current_phase is not None and _RECIPE_PHASES.index(current_phase) > _RECIPE_PHASES.index(
            failed_phase
        ):
            failed_phase = current_phase
    await oracle.update_document_recipe_status(
        recipe_id=job.recipe_id,
        status=FileStatus.ERROR,
        failed_phase=failed_phase,
        error_message=error_message[:2000],
    )


async def _restore_recipe_status_after_cancel(
    oracle: OracleClient,
    job: IngestionJob,
) -> None:
    """取消後は旧 active があれば検索対象、無ければ未処理へ戻す。"""
    if job.recipe_id is None:
        return
    row = await oracle.get_document_recipe(job.document_id, job.recipe_id)
    await oracle.update_document_recipe_status(
        recipe_id=job.recipe_id,
        status=(
            FileStatus.INDEXED
            if row is not None and row.get("active_chunk_set_id") is not None
            else FileStatus.UPLOADED
        ),
    )


def _safe_ingestion_job_error_message(error: Exception) -> str:
    if getattr(error, "safe_for_user", False):
        message = str(error).replace("\n", " ").strip()
        if message:
            return message[:2000]
    return "取込処理に失敗しました。"


async def _enqueue_auto_advance_job(job: IngestionJob, detail: DocumentDetail) -> None:
    """文書の有効な処理レシピに従い次 stage の job を投入する。"""
    try:
        settings, _config = await _resolve_ingestion_settings(OracleClient(), job.document_id)
        if (
            job.phase in {IngestionJobPhase.PREPROCESS, IngestionJobPhase.EXTRACT}
            and detail.status == FileStatus.REVIEW
            and settings.rag_auto_chunk_after_extract_enabled
        ):
            await _enqueue_chunk_phase_job_for_document(job.document_id)
        elif (
            job.phase == IngestionJobPhase.CHUNK
            and detail.status == FileStatus.CHUNKED
            and settings.rag_auto_index_after_chunk_enabled
        ):
            await _enqueue_index_phase_job_for_document(job.document_id)
    except Exception:
        logger.warning(
            "auto advance job enqueue failed. document_id=%s phase=%s",
            job.document_id,
            job.phase,
            exc_info=True,
        )


async def _finish_ingestion_job_unless_cancelled(
    oracle: OracleClient,
    job_id: str,
    *,
    status: IngestionJobStatus,
    error_message: str | None = None,
) -> IngestionJob | None:
    """実行中に cancel された job の最終状態を上書きしない。"""
    current = await oracle.get_ingestion_job(job_id)
    if current is not None and current.status == IngestionJobStatus.CANCELLED:
        logger.info(
            "ingestion_job_finish_skipped_after_cancel",
            extra={"job_id": job_id, "final_status": status.value},
        )
        return current
    return await oracle.update_ingestion_job(
        job_id,
        status=status,
        error_message=error_message,
        finished_at=datetime.now(UTC),
    )


async def recover_and_drain_ingestion_jobs(
    *,
    limit: int,
    stale_running_seconds: float,
    concurrency: int,
) -> list[IngestionJob]:
    """起動時などに stale/queued 取込 job を回復して実行する。"""
    oracle = OracleClient()
    stale_before = datetime.now(UTC) - timedelta(seconds=stale_running_seconds)
    stale_jobs = await oracle.recover_stale_ingestion_jobs(
        stale_before=stale_before,
        limit=limit,
    )
    if stale_jobs:
        logger.info(
            "ingestion_jobs_recovered",
            extra={"job_count": len(stale_jobs)},
        )
    queued_jobs = await oracle.list_ingestion_jobs(
        status=IngestionJobStatus.QUEUED,
        limit=limit,
        offset=0,
    )
    if not queued_jobs:
        return []
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_job(job: IngestionJob) -> None:
        async with semaphore:
            await _run_ingestion_job(job.id)

    await asyncio.gather(*(run_job(job) for job in queued_jobs))
    return queued_jobs


@router.get("/{document_id}/content")
async def document_content(
    document_id: str,
    variant: Annotated[Literal["original", "prepared"], Query()] = "original",
    disposition: Annotated[Literal["inline", "attachment"], Query()] = "inline",
) -> Response:
    """原本またはファイル準備後 artifact を返す（文書プレビュー/ダウンロード用）。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return await _document_content_response(
        detail,
        variant=variant,
        disposition=disposition,
        preprocess_artifact=detail.preprocess_artifact,
    )


async def _document_content_response(
    detail: DocumentDetail,
    *,
    variant: Literal["original", "prepared"],
    disposition: Literal["inline", "attachment"],
    preprocess_artifact: DocumentPreprocessArtifact | None,
) -> Response:
    if detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if variant == "prepared":
        artifact = preprocess_artifact
        if artifact is None or not artifact.object_storage_path:
            raise HTTPException(
                status_code=404,
                detail="処理後ファイルが見つかりません。ファイル準備から再処理してください。",
            )
        path = artifact.object_storage_path
        file_name = artifact.file_name
        content_type = artifact.content_type or _document_media_type(detail)
        not_found_message = "処理後ファイルが見つかりません。"
        bad_path_message = "処理後ファイルの参照パスが不正です。"
    else:
        path = detail.object_storage_path
        file_name = detail.file_name
        content_type = _document_media_type(detail)
        not_found_message = "原本ファイルが見つかりません。"
        bad_path_message = "原本ファイルの参照パスが不正です。"
    try:
        data = await ObjectStorageClient().get(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=not_found_message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=bad_path_message) from exc

    return Response(
        content=data,
        media_type=_content_type_header(content_type, data),
        headers={
            # 非 ASCII ファイル名は RFC 5987 でエンコードする
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file_name)}",
            # MIME sniffing による取り違えを防ぐ
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
        },
    )


async def _read_upload_file(file: UploadFile, max_bytes: int) -> bytes:
    """アップロードを上限付きで読み込む。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="ファイルサイズが上限を超えています。")
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_display_filename(file_name: str | None) -> str:
    """表示・保存用のファイル名を安全な basename にする。"""
    name = PurePath((file_name or "document.bin").replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name).strip(" .")
    if not name:
        return "document.bin"
    return name[:255]


def _normalized_content_type(content_type: str | None) -> str:
    """MIME type のパラメータと大小差を正規化する。"""
    if not content_type:
        return "application/octet-stream"
    return content_type.split(";", maxsplit=1)[0].strip().lower() or "application/octet-stream"


def _sha256_hex(data: bytes) -> str:
    """アップロード原本の内容 hash を返す。"""
    return hashlib.sha256(data).hexdigest()


def _source_integrity_error(data: bytes, detail: DocumentDetail) -> str | None:
    """保存済みメタデータと取得した原本 bytes の整合性を検証する。"""
    if detail.file_size_bytes is not None and len(data) != detail.file_size_bytes:
        return SOURCE_SIZE_MISMATCH_MESSAGE
    if detail.content_sha256 is not None and _sha256_hex(data) != detail.content_sha256:
        return SOURCE_HASH_MISMATCH_MESSAGE
    return None


def _document_media_type(detail: DocumentDetail) -> str:
    """原本配信用 MIME type は保存済み metadata を優先する。"""
    if detail.content_type:
        return _normalized_content_type(detail.content_type)
    media_type, _ = mimetypes.guess_type(detail.file_name)
    return media_type or "application/octet-stream"


# プレビューでテキスト扱いする MIME type（text/* に加えて）
_TEXT_MEDIA_TYPES = {
    "application/json",
    "application/xml",
    "application/csv",
    "application/x-ndjson",
}


def _is_text_media_type(media_type: str) -> bool:
    """テキストとしてデコード/プレビューする MIME type かどうか。"""
    return media_type.startswith("text/") or media_type in _TEXT_MEDIA_TYPES


# python codec 名 → WHATWG (TextDecoder) ラベルの対応。
# ブラウザ TextDecoder は限られたラベルしか受け付けないため、検出結果を寄せる。
_WHATWG_LABELS = {
    "cp932": "shift_jis",
    "ms932": "shift_jis",
    "shift-jis": "shift_jis",
    "sjis": "shift_jis",
    "euc-jp": "euc-jp",
    "eucjp": "euc-jp",
    "euc-jis-2004": "euc-jp",
    "euc-jisx0213": "euc-jp",
    "cp936": "gbk",
    "gbk": "gbk",
    "gb2312": "gbk",
    "cp949": "euc-kr",
    "euc-kr": "euc-kr",
    "cp950": "big5",
    "big5hkscs": "big5",
}


def _detect_text_charset(data: bytes) -> str:
    """テキスト原本の文字コードを検出する（WHATWG TextDecoder 互換ラベルで返す）。"""
    if not data:
        return "utf-8"
    # UTF-8 として妥当ならそのまま採用（検出器の誤判定を避ける）
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    match = from_bytes(data).best()
    if match is None or not match.encoding:
        return "utf-8"
    # python codec 名（例: cp932 / euc_jis_2004）を WHATWG ラベルへ寄せる
    label = match.encoding.replace("_", "-")
    return _WHATWG_LABELS.get(label, label)


def _content_type_header(media_type: str, data: bytes) -> str:
    """テキスト系は文字コードを検出し charset を付与する（非 UTF-8 の文字化け対策）。"""
    if not _is_text_media_type(media_type):
        return media_type
    return f"{media_type}; charset={_detect_text_charset(data)}"


def _normalize_upload_knowledge_base_ids(values: list[str] | None) -> list[str]:
    """multipart form の KB ID 指定を API 内部のリストへ正規化する。"""
    if not values:
        return []
    expanded: list[str] = []
    for value in values:
        expanded.extend(value.split(","))
    return normalize_search_id_list(expanded)
