"""ドキュメント API。アップロード・一覧・取込(抽出→索引)。"""

import asyncio
import hashlib
import logging
import mimetypes
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from charset_normalizer import from_bytes
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.db_degradation import load_or_degrade
from app.rag.ingestion import IngestionPipeline, IngestionTimeoutError, IngestionUserError
from app.rag.rate_limit import enforce_rate_limit
from app.rag.source_profile import build_source_profile
from app.schemas.common import ApiResponse, Page
from app.schemas.document import (
    BatchUploadResult,
    DocumentDetail,
    DocumentStats,
    DocumentSummary,
    FileStatus,
    IngestionJob,
    IngestionJobStatus,
    SourceProfile,
    UploadResult,
)
from app.schemas.knowledge_base import DocumentKnowledgeBaseReplaceRequest, KnowledgeBaseRef
from app.schemas.search import normalize_search_id_list

router = APIRouter()
logger = logging.getLogger(__name__)
SOURCE_SIZE_MISMATCH_MESSAGE = "原本ファイルのサイズがアップロード時と一致しません。"
SOURCE_HASH_MISMATCH_MESSAGE = "原本ファイルの SHA-256 がアップロード時と一致しません。"


class UploadIngestionMode(StrEnum):
    """アップロード後の取込開始方針。"""

    MANUAL = "manual"
    AUTO = "auto"


@router.post("/upload", response_model=ApiResponse[UploadResult])
async def upload_document(
    http_request: Request,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(...)],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
    ingestion_mode: Annotated[UploadIngestionMode, Form()] = UploadIngestionMode.MANUAL,
) -> ApiResponse[UploadResult]:
    """ドキュメントファイルをアップロードし、Object Storage へ保管する。"""
    enforce_rate_limit("upload", http_request)
    result = await _store_uploaded_document(file, knowledge_base_ids)
    result = await _attach_ingestion_job(result, ingestion_mode, background_tasks)
    return ApiResponse(data=result)


@router.post("/batch-upload", response_model=ApiResponse[BatchUploadResult])
async def batch_upload_documents(
    http_request: Request,
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File(...)],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
    ingestion_mode: Annotated[UploadIngestionMode, Form()] = UploadIngestionMode.MANUAL,
) -> ApiResponse[BatchUploadResult]:
    """複数ドキュメントをまとめてアップロードし、必要に応じて取込 job を作る。"""
    enforce_rate_limit("upload", http_request)
    if not files:
        raise HTTPException(status_code=400, detail="アップロード対象ファイルを選択してください。")
    items: list[UploadResult] = []
    for file in files:
        result = await _store_uploaded_document(file, knowledge_base_ids)
        items.append(await _attach_ingestion_job(result, ingestion_mode, background_tasks))
    return ApiResponse(
        data=BatchUploadResult(
            items=items,
            total_count=len(items),
            uploaded_count=len(items),
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


async def _store_uploaded_document(
    file: UploadFile,
    knowledge_base_ids: list[str] | None,
) -> UploadResult:
    """単一 UploadFile を保存し、取込前の upload result を返す。"""
    settings = get_settings()
    content_type = _normalized_content_type(file.content_type)
    allowed_content_types = {
        _normalized_content_type(allowed) for allowed in settings.allowed_upload_content_types
    }
    if content_type not in allowed_content_types:
        raise HTTPException(status_code=415, detail="対応していないファイル形式です。")

    data = await _read_upload_file(file, settings.max_upload_bytes)
    if not data:
        raise HTTPException(status_code=400, detail="空のファイルはアップロードできません。")

    storage = ObjectStorageClient()
    oracle = OracleClient()
    original_file_name = file.filename or "document.bin"
    file_name = _safe_display_filename(original_file_name)
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
    background_tasks: BackgroundTasks,
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
        background_tasks.add_task(_run_ingestion_job, job.id)
    return ApiResponse(data=jobs)


@router.post("/ingestion-jobs/{job_id}/retry", response_model=ApiResponse[IngestionJob])
async def retry_ingestion_job(
    http_request: Request,
    background_tasks: BackgroundTasks,
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
        force=force,
        background_tasks=background_tasks,
    )
    return ApiResponse(data=retry_job)


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
    background_tasks: BackgroundTasks,
    document_id: str,
    force: bool = Query(default=False),
) -> ApiResponse[IngestionJob]:
    """保存済みドキュメントを取込 job としてキュー投入する。"""
    enforce_rate_limit("ingest", http_request)
    job = await _enqueue_ingestion_job_for_document(
        document_id,
        force=force,
        background_tasks=background_tasks,
    )
    return ApiResponse(data=job)


@router.get("/{document_id}", response_model=ApiResponse[DocumentDetail])
async def get_document(document_id: str) -> ApiResponse[DocumentDetail]:
    """ドキュメント詳細（抽出本文含む）を返す。"""
    detail = await OracleClient().get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=detail)


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


@router.post("/{document_id}/ingest", response_model=ApiResponse[DocumentDetail])
async def ingest_document(
    http_request: Request,
    document_id: str,
    force: bool = Query(default=False),
) -> ApiResponse[DocumentDetail]:
    """OCI Enterprise AI の VLM で OCR・本文抽出し、チャンク→埋め込み→索引まで行う。"""
    enforce_rate_limit("ingest", http_request)
    try:
        job = await _enqueue_ingestion_job_for_document(
            document_id,
            force=force,
            background_tasks=None,
        )
        if job.status == IngestionJobStatus.QUEUED:
            await _run_ingestion_job(job.id, force=force, propagate_errors=True)
        detail = await OracleClient().get_document(document_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
        return ApiResponse(data=detail)
    except IngestionTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except IngestionUserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _ingest_existing_document(document_id: str, *, force: bool = False) -> DocumentDetail:
    """保存済み原本を検証して取込パイプラインへ渡す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status == FileStatus.INGESTING:
        raise HTTPException(status_code=409, detail="このドキュメントは現在取込中です。")
    if detail.status == FileStatus.INDEXED and not force:
        return detail
    try:
        data = await ObjectStorageClient().get(detail.object_storage_path)
    except FileNotFoundError as exc:
        await oracle.update_document_status(
            document_id,
            FileStatus.ERROR,
            "原本ファイルが見つかりません。",
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
    pipeline = IngestionPipeline(oracle=oracle)
    return await pipeline.ingest(
        document_id=document_id,
        image_bytes=data,
        prompt="ドキュメントを日本語で OCR し、本文テキストを抽出してください。",
        content_type=detail.content_type or "application/octet-stream",
        source_profile=source_profile,
    )


async def _attach_ingestion_job(
    result: UploadResult,
    ingestion_mode: UploadIngestionMode,
    background_tasks: BackgroundTasks,
) -> UploadResult:
    """auto 指定時に取込 job を作り、upload result に添付する。"""
    if ingestion_mode != UploadIngestionMode.AUTO:
        return result
    job = await _create_ingestion_job(result)
    if job.status == IngestionJobStatus.QUEUED:
        background_tasks.add_task(_run_ingestion_job, job.id)
    return result.model_copy(
        update={
            "ingestion_started": job.status == IngestionJobStatus.QUEUED,
            "ingestion_job": job,
        }
    )


async def _enqueue_ingestion_job_for_document(
    document_id: str,
    *,
    force: bool,
    background_tasks: BackgroundTasks | None,
) -> IngestionJob:
    """既存ドキュメントを job 化し、必要ならバックグラウンド実行へ渡す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status == FileStatus.INGESTING:
        raise HTTPException(status_code=409, detail="このドキュメントは現在取込中です。")

    source_profile = _source_profile_for_detail(detail)
    if detail.duplicate_of_document_id is not None and not force:
        return await _create_ingestion_job_record(
            oracle=oracle,
            document_id=document_id,
            parser_profile=source_profile.parser_profile,
            quality_warnings=source_profile.quality_warnings,
            status=IngestionJobStatus.SKIPPED,
            skip_reason="duplicate_content",
        )
    if detail.status == FileStatus.INDEXED and not force:
        return await _create_ingestion_job_record(
            oracle=oracle,
            document_id=document_id,
            parser_profile=source_profile.parser_profile,
            quality_warnings=source_profile.quality_warnings,
            status=IngestionJobStatus.SKIPPED,
            skip_reason="already_indexed",
        )

    job = await _create_ingestion_job_record(
        oracle=oracle,
        document_id=document_id,
        parser_profile=source_profile.parser_profile,
        quality_warnings=source_profile.quality_warnings,
    )
    if background_tasks is not None:
        background_tasks.add_task(_run_ingestion_job, job.id, force=force)
    return job


async def _create_ingestion_job(result: UploadResult) -> IngestionJob:
    """upload 結果から取込 job を作る。重複は SKIPPED として記録する。"""
    is_duplicate = result.duplicate_of_document_id is not None
    return await _create_ingestion_job_record(
        oracle=OracleClient(),
        document_id=result.id,
        parser_profile=result.source_profile.parser_profile,
        quality_warnings=result.source_profile.quality_warnings,
        status=IngestionJobStatus.SKIPPED if is_duplicate else IngestionJobStatus.QUEUED,
        skip_reason="duplicate_content" if is_duplicate else None,
    )


async def _create_ingestion_job_record(
    *,
    oracle: OracleClient,
    document_id: str,
    parser_profile: str,
    quality_warnings: list[str],
    status: IngestionJobStatus = IngestionJobStatus.QUEUED,
    skip_reason: str | None = None,
) -> IngestionJob:
    """取込 job を永続化する。"""
    queued_at = datetime.now(UTC)
    settings = get_settings()
    job = IngestionJob(
        id=uuid4().hex,
        document_id=document_id,
        status=status,
        parser_profile=parser_profile,
        quality_warnings=quality_warnings,
        skip_reason=skip_reason,
        max_attempts=settings.ingestion_job_max_attempts,
        queued_at=queued_at,
        finished_at=queued_at if status == IngestionJobStatus.SKIPPED else None,
    )
    return await oracle.create_ingestion_job(job)


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


async def _run_ingestion_job(
    job_id: str,
    *,
    force: bool = False,
    propagate_errors: bool = False,
) -> None:
    """キュー投入済み取込 job を実行する。"""
    oracle = OracleClient()
    job = await oracle.claim_ingestion_job(job_id, started_at=datetime.now(UTC))
    if job is None:
        return
    try:
        await _ingest_existing_document(job.document_id, force=force)
    except HTTPException as exc:
        await oracle.update_ingestion_job(
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=str(exc.detail),
            finished_at=datetime.now(UTC),
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
    except IngestionTimeoutError as exc:
        await oracle.update_ingestion_job(
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=str(exc),
            finished_at=datetime.now(UTC),
        )
        logger.info(
            "ingestion_job_timeout",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except IngestionUserError as exc:
        await oracle.update_ingestion_job(
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=str(exc),
            finished_at=datetime.now(UTC),
        )
        logger.info(
            "ingestion_job_validation_error",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except Exception:
        await oracle.update_ingestion_job(
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message="取込処理に失敗しました。",
            finished_at=datetime.now(UTC),
        )
        logger.exception(
            "ingestion_job_failed",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    else:
        await oracle.update_ingestion_job(
            job_id,
            status=IngestionJobStatus.SUCCEEDED,
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
async def document_content(document_id: str) -> Response:
    """原本ファイルを返す（文書プレビュー用）。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    try:
        data = await ObjectStorageClient().get(detail.object_storage_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="原本ファイルが見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="原本ファイルの参照パスが不正です。") from exc

    return Response(
        content=data,
        media_type=_content_type_header(_document_media_type(detail), data),
        headers={
            # 非 ASCII ファイル名は RFC 5987 でエンコードして inline 表示する
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(detail.file_name)}",
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
