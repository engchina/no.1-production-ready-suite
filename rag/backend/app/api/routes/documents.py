"""ドキュメント API。アップロード・一覧・取込(抽出→索引)。"""

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from html import escape
from pathlib import PurePath
from typing import Annotated
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
from app.clients.oracle import DocumentDeleteBlockedByRunningIngestionError, OracleClient
from app.config import Settings, get_settings
from app.db_degradation import load_or_degrade
from app.rag.ingestion import (
    IngestionCancelledError,
    IngestionPipeline,
    IngestionTimeoutError,
    IngestionUserError,
)
from app.rag.ingestion_worker import request_ingestion_worker_wakeup
from app.rag.kb_adapter_config import apply_adapter_config_or_global
from app.rag.navigation import build_navigation_tree
from app.rag.rate_limit import enforce_rate_limit
from app.rag.source_profile import build_source_profile
from app.rag.variant_keys import compute_chunk_set_id
from app.rag.variant_planner import MaterializationPlan, plan_document_materializations
from app.schemas.common import ApiResponse, Page
from app.schemas.document import (
    BatchUploadFailedItem,
    BatchUploadResult,
    DocumentApproveRequest,
    DocumentChunkView,
    DocumentDeleteResult,
    DocumentDetail,
    DocumentExtractionExport,
    DocumentExtractionExportFormat,
    DocumentIngestionConfigData,
    DocumentStats,
    DocumentSummary,
    DocumentTableCellTextEdit,
    FileStatus,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
    IngestionSegment,
    SourceProfile,
    UploadResult,
)
from app.schemas.extraction import (
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
    KnowledgeBaseDetail,
    KnowledgeBaseRef,
)
from app.schemas.search import normalize_search_id_list

router = APIRouter()
logger = logging.getLogger(__name__)
SOURCE_SIZE_MISMATCH_MESSAGE = "原本ファイルのサイズがアップロード時と一致しません。"
SOURCE_HASH_MISMATCH_MESSAGE = "原本ファイルの SHA-256 がアップロード時と一致しません。"
INGESTION_JOB_CANCELLED_MESSAGE = "利用者によりキャンセルされました。"
DELETE_BLOCKING_INGESTION_STATUSES = frozenset({IngestionJobStatus.RUNNING})


class UploadIngestionMode(StrEnum):
    """アップロード後の取込開始方針。"""

    MANUAL = "manual"
    AUTO = "auto"


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
    result = await _attach_ingestion_job(result, ingestion_mode)
    return ApiResponse(data=result)


@router.post("/batch-upload", response_model=ApiResponse[BatchUploadResult])
async def batch_upload_documents(
    http_request: Request,
    files: Annotated[list[UploadFile], File(...)],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
    ingestion_mode: Annotated[UploadIngestionMode, Form()] = UploadIngestionMode.MANUAL,
) -> ApiResponse[BatchUploadResult]:
    """複数ドキュメントをまとめてアップロードし、必要に応じて取込 job を作る。"""
    enforce_rate_limit("upload", http_request)
    if not files:
        raise HTTPException(status_code=400, detail="アップロード対象ファイルを選択してください。")
    items: list[UploadResult] = []
    failed_items: list[BatchUploadFailedItem] = []
    for file in files:
        try:
            result = await _store_uploaded_document(file, knowledge_base_ids)
            items.append(await _attach_ingestion_job(result, ingestion_mode))
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
    if duplicate is not None:
        await _assign_duplicate_canonical_to_knowledge_bases(
            oracle=oracle,
            canonical_document_id=duplicate.id,
            knowledge_base_ids=[knowledge_base.id for knowledge_base in detail.knowledge_bases],
        )
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


async def _assign_duplicate_canonical_to_knowledge_bases(
    *,
    oracle: OracleClient,
    canonical_document_id: str,
    knowledge_base_ids: list[str],
) -> None:
    """重複 upload 時、検索対象の canonical document を選択 KB に追加する。"""
    for knowledge_base_id in knowledge_base_ids:
        try:
            await oracle.assign_documents_to_knowledge_base(
                knowledge_base_id,
                [canonical_document_id],
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="重複元ドキュメントまたはナレッジベースが見つかりません。",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


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
        force=force,
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
        # INDEX フェーズの取消は抽出をやり直さないよう REVIEW へ戻す。
        restore_status = (
            FileStatus.REVIEW if job.phase == IngestionJobPhase.INDEX else FileStatus.UPLOADED
        )
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
) -> ApiResponse[IngestionJob]:
    """保存済みドキュメントを取込 job としてキュー投入する。"""
    enforce_rate_limit("ingest", http_request)
    job = await _enqueue_ingestion_job_for_document(
        document_id,
        force=force,
    )
    return ApiResponse(data=job)


@router.post(
    "/{document_id}/ingestion-segments/retry",
    response_model=ApiResponse[IngestionJob],
)
async def retry_failed_document_ingestion_segments(
    http_request: Request,
    document_id: str,
) -> ApiResponse[IngestionJob]:
    """FAILED checkpoint がある文書だけ、失敗 segment 再試行 job として再投入する。"""
    enforce_rate_limit("ingest", http_request)
    job = await _enqueue_failed_segment_retry_job_for_document(
        document_id,
    )
    return ApiResponse(data=job)


@router.get("/{document_id}/chunks", response_model=ApiResponse[list[DocumentChunkView]])
async def list_document_chunks(document_id: str) -> ApiResponse[list[DocumentChunkView]]:
    """文書 preview workspace 用に chunk/citation metadata を返す。"""
    oracle = OracleClient()
    if await oracle.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=await oracle.list_document_chunks(document_id))


@router.get(
    "/{document_id}/ingestion-config",
    response_model=ApiResponse[DocumentIngestionConfigData],
)
async def get_document_ingestion_config(
    document_id: str,
) -> ApiResponse[DocumentIngestionConfigData]:
    """owning KB の現行取込設定と、取込済みチャンクのドリフト状況を返す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")

    effective_settings, owning = await _resolve_ingestion_settings(oracle, document_id)
    is_indexed = detail.status == FileStatus.INDEXED

    observed_strategy: str | None = None
    observed_parser: str | None = None
    if is_indexed:
        chunks = await oracle.list_document_chunks(document_id)
        if chunks:
            first = chunks[0]
            strategy_value = first.metadata.get("chunk_strategy")
            observed_strategy = str(strategy_value) if strategy_value is not None else None
            observed_parser = first.source_parser or (
                str(first.metadata["parser_backend"])
                if "parser_backend" in first.metadata
                else None
            )

    # ドリフト判定は取込済みで観測値があるときのみ。chunking strategy 差が主シグナル。
    config_drift = bool(
        is_indexed
        and observed_strategy is not None
        and observed_strategy != effective_settings.rag_chunking_strategy
    )

    return ApiResponse(
        data=DocumentIngestionConfigData(
            document_id=document_id,
            is_indexed=is_indexed,
            owning_knowledge_base=(
                KnowledgeBaseRef(id=owning.id, name=owning.name) if owning is not None else None
            ),
            effective_chunking_strategy=effective_settings.rag_chunking_strategy,
            effective_parser_adapter_backend=effective_settings.rag_parser_adapter_backend,
            observed_chunking_strategy=observed_strategy,
            observed_parser_backend=observed_parser,
            config_drift=config_drift,
        )
    )


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
        return ApiResponse(data=persisted_segments)
    jobs = await oracle.list_document_ingestion_jobs(document_id)
    return ApiResponse(data=_document_ingestion_segments(detail, jobs))


@router.get("/{document_id}", response_model=ApiResponse[DocumentDetail])
async def get_document(document_id: str) -> ApiResponse[DocumentDetail]:
    """ドキュメント詳細（抽出本文含む）を返す。"""
    detail = await OracleClient().get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=detail)


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
    """REVIEW(プレビュー確認待ち)の文書を承認し、後段(index)を投入する。

    body に REVIEW 中のテキスト修正(raw_text / element_edits / table_cell_edits)を
    含む場合は、bbox・構造を保持したままテキストのみ差し替えて保存してから index する。
    """
    enforce_rate_limit("ingest", http_request)
    if body is not None and (
        body.element_edits or body.table_cell_edits or body.raw_text is not None
    ):
        await _apply_review_text_edits(document_id, body)
    job = await _enqueue_index_phase_job_for_document(document_id)
    return ApiResponse(data=job)


async def _apply_review_text_edits(
    document_id: str,
    edits: DocumentApproveRequest,
) -> None:
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
    extraction = StructuredExtraction.model_validate(detail.extraction)
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
    if edits.raw_text is not None:
        extraction = extraction.model_copy(update={"raw_text": edits.raw_text})
    # 正規化(raw_text/element の整合補完)を再実行してから保存する。
    normalized = StructuredExtraction.model_validate(extraction.model_dump())
    await oracle.save_extraction(document_id, normalized)


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


async def _ingest_existing_document(
    document_id: str,
    *,
    force: bool = False,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
) -> DocumentDetail:
    """保存済み原本を検証して取込パイプラインへ渡す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status in (FileStatus.INGESTING, FileStatus.INDEXING):
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
    # plan 駆動の複数 chunk_set materialization（_index_reviewed_document と整合）。
    # 先頭 chunk_set は ingest（extract+index）で抽出を保存し、残りはその抽出を再利用して
    # index_reviewed で再チャンクする（chunking 軸 variant 前提。preprocess/parser 軸の
    # 再抽出は別 follow-up）。所属 KB / content_sha256 が無ければ現行の単一(owning)挙動へ縮退。
    global_settings = get_settings()
    configs = dict(await oracle.list_document_knowledge_base_configs(document_id))
    plan = (
        plan_document_materializations(detail.content_sha256, global_settings, configs)
        if detail.content_sha256 and configs
        else None
    )
    ingest_prompt = "ドキュメントを日本語で OCR し、本文テキストを抽出してください。"
    ingest_content_type = detail.content_type or "application/octet-stream"
    if plan is None or not plan.chunk_sets:
        # owning KB(最古割当)の取込上書きをスナップショットとして適用する。
        effective_settings, _owning = await _resolve_ingestion_settings(oracle, document_id)
        chunk_set_id = _document_chunk_set_id(detail, effective_settings)
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.ingest(
            document_id=document_id,
            image_bytes=data,
            prompt=ingest_prompt,
            content_type=ingest_content_type,
            source_profile=source_profile,
            chunk_set_id=chunk_set_id,
            cancel_checker=cancel_checker,
        )
        await _reconcile_document_chunk_sets(oracle, document_id, result, chunk_set_id)
        return result
    result = detail
    chunk_set_ids = sorted(plan.chunk_sets)
    for index, chunk_set_id in enumerate(chunk_set_ids):
        representative_kb_id = sorted(plan.chunk_sets[chunk_set_id])[0]
        recipe_settings, _applied = apply_adapter_config_or_global(
            global_settings, configs[representative_kb_id], scope="ingestion"
        )
        pipeline = IngestionPipeline(oracle=oracle, settings=recipe_settings)
        # 成功 metric/audit は最後の chunk_set でのみ出し、1 文書 1 論理取込に集約する。
        record_outcome = index == len(chunk_set_ids) - 1
        if index == 0:
            result = await pipeline.ingest(
                document_id=document_id,
                image_bytes=data,
                prompt=ingest_prompt,
                content_type=ingest_content_type,
                source_profile=source_profile,
                chunk_set_id=chunk_set_id,
                record_outcome=record_outcome,
                cancel_checker=cancel_checker,
            )
        else:
            result = await pipeline.index_reviewed(
                document_id,
                chunk_set_id=chunk_set_id,
                record_outcome=record_outcome,
                cancel_checker=cancel_checker,
            )
    await _reconcile_plan_chunk_sets(oracle, document_id, result, plan)
    return result


async def _index_reviewed_document(
    document_id: str,
    *,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
) -> DocumentDetail:
    """REVIEW で承認済みの文書を後段(index)だけ実行する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status not in (FileStatus.REVIEW, FileStatus.INDEXING):
        raise HTTPException(
            status_code=409,
            detail="プレビュー確認待ちの文書のみ索引できます。",
        )
    global_settings = get_settings()
    configs = dict(await oracle.list_document_knowledge_base_configs(document_id))
    plan = (
        plan_document_materializations(detail.content_sha256, global_settings, configs)
        if detail.content_sha256 and configs
        else None
    )
    if plan is None or not plan.chunk_sets:
        # 所属 KB / content_sha256 が無い → 現行の単一(owning)挙動へ縮退する。
        effective_settings, _owning = await _resolve_ingestion_settings(oracle, document_id)
        chunk_set_id = _document_chunk_set_id(detail, effective_settings)
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.index_reviewed(
            document_id, chunk_set_id=chunk_set_id, cancel_checker=cancel_checker
        )
        await _reconcile_document_chunk_sets(oracle, document_id, result, chunk_set_id)
        return result
    # plan 駆動: 各 distinct chunk_set を recipe 設定で materialize(保存済み extraction 再利用)。
    result = detail
    chunk_set_ids = sorted(plan.chunk_sets)
    for index, chunk_set_id in enumerate(chunk_set_ids):
        representative_kb_id = sorted(plan.chunk_sets[chunk_set_id])[0]
        recipe_settings, _applied = apply_adapter_config_or_global(
            global_settings, configs[representative_kb_id], scope="ingestion"
        )
        pipeline = IngestionPipeline(oracle=oracle, settings=recipe_settings)
        # 成功 metric/audit は最後の chunk_set でのみ出し、1 文書 1 論理取込に集約する。
        result = await pipeline.index_reviewed(
            document_id,
            chunk_set_id=chunk_set_id,
            record_outcome=index == len(chunk_set_ids) - 1,
            cancel_checker=cancel_checker,
        )
    await _reconcile_plan_chunk_sets(oracle, document_id, result, plan)
    return result


async def _reconcile_plan_chunk_sets(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
    plan: MaterializationPlan,
) -> None:
    """plan の各 chunk_set を永続化し、KB グループを binding、plan に無い chunk_set を GC する。

    chunk は save_index で挿入時タグ付け済み。bookkeeping なので失敗しても取込は止めず warning。
    """
    if detail.status != FileStatus.INDEXED:
        return
    try:
        for chunk_set_id, knowledge_base_ids in plan.chunk_sets.items():
            chunk_count = await oracle.count_chunk_set_chunks(chunk_set_id)
            await oracle.upsert_chunk_set(chunk_set_id=chunk_set_id, document_id=document_id)
            await oracle.mark_chunk_set_indexed(
                chunk_set_id=chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
            )
            for knowledge_base_id in knowledge_base_ids:
                await oracle.upsert_chunk_set_binding(
                    knowledge_base_id=knowledge_base_id,
                    document_id=document_id,
                    chunk_set_id=chunk_set_id,
                )
        await oracle.delete_document_chunk_sets_except(
            document_id=document_id, keep_chunk_set_ids=list(plan.chunk_sets)
        )
    except Exception:  # noqa: BLE001 - reconcile は bookkeeping。失敗しても取込は止めない。
        logger.warning(
            "chunk_set plan reconcile に失敗しました（取込は完了済み）。document_id=%s",
            document_id,
            exc_info=True,
        )


def _document_chunk_set_id(detail: DocumentDetail, settings: Settings) -> str | None:
    """文書の content_sha256 と effective 取込設定から chunk_set_id を求める(無ければ None)。"""
    if not detail.content_sha256:
        return None
    return compute_chunk_set_id(detail.content_sha256, settings)


async def _reconcile_document_chunk_sets(
    oracle: OracleClient,
    document_id: str,
    detail: DocumentDetail,
    chunk_set_id: str | None,
) -> None:
    """取込後、materialize した chunk_set を記録し所属 KB を binding する(planner 駆動の基盤)。

    chunk は save_index で**挿入時に chunk_set_id タグ付け済み**。本関数は chunk_set 行の永続化・
    KB binding・旧 chunk_set(とその chunk、未タグ chunk)の GC を行う。bookkeeping なので失敗
    しても取込自体は止めず warning に留める(chunk は既に INDEXED 済み)。
    """
    if detail.status != FileStatus.INDEXED or chunk_set_id is None:
        return
    try:
        chunk_count = await oracle.count_document_chunks(document_id)
        await oracle.upsert_chunk_set(chunk_set_id=chunk_set_id, document_id=document_id)
        await oracle.mark_chunk_set_indexed(
            chunk_set_id=chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
        )
        # 取込設定変更で生じた旧 chunk_set とその chunk(+未タグ chunk)を削除し、keep だけ残す。
        await oracle.delete_stale_document_chunk_sets(
            document_id=document_id, keep_chunk_set_id=chunk_set_id
        )
        # 現状は単一 materialization なので、所属 KB すべてをこの chunk_set に bind する。
        # KB ごとに取込設定が分岐する複数 materialization は後続の増分で対応する。
        knowledge_bases = await oracle.list_document_knowledge_bases(document_id)
        for knowledge_base in knowledge_bases:
            await oracle.upsert_chunk_set_binding(
                knowledge_base_id=knowledge_base.id,
                document_id=document_id,
                chunk_set_id=chunk_set_id,
            )
    except Exception:  # noqa: BLE001 - reconcile は bookkeeping。失敗しても取込は止めない。
        logger.warning(
            "chunk_set reconcile に失敗しました（取込は完了済み）。document_id=%s",
            document_id,
            exc_info=True,
        )


async def _resolve_ingestion_settings(
    oracle: OracleClient,
    document_id: str,
) -> tuple[Settings, KnowledgeBaseDetail | None]:
    """owning KB の取込上書きを重ねた有効 Settings と owning KB detail を返す。

    所属が無い・設定が無い・グローバルと矛盾する場合はグローバル設定へ縮退する。
    """
    global_settings = get_settings()
    owning = await oracle.get_owning_knowledge_base(document_id)
    if owning is None:
        return global_settings, None
    effective, _applied = apply_adapter_config_or_global(
        global_settings,
        owning.adapter_config,
        scope="ingestion",
    )
    return effective, owning


def _dispatch_ingestion_job(
    job_id: str,
    *,
    force: bool = False,
) -> None:
    """QUEUED ジョブの消費をワーカーへ通知する。HTTP 内では実行しない。"""
    _ = (job_id, force)
    request_ingestion_worker_wakeup()


async def _attach_ingestion_job(
    result: UploadResult,
    ingestion_mode: UploadIngestionMode,
) -> UploadResult:
    """auto 指定時に取込 job を作り、upload result に添付する。"""
    if ingestion_mode != UploadIngestionMode.AUTO:
        return result
    job = await _create_ingestion_job(result)
    if job.status == IngestionJobStatus.QUEUED:
        _dispatch_ingestion_job(job.id)
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
) -> IngestionJob:
    """既存ドキュメントを job 化し、必要ならバックグラウンド実行へ渡す。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status in (FileStatus.INGESTING, FileStatus.INDEXING):
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
    if source_profile.unsupported_reason and not force:
        return await _create_ingestion_job_record(
            oracle=oracle,
            document_id=document_id,
            parser_profile=source_profile.parser_profile,
            quality_warnings=source_profile.quality_warnings,
            status=IngestionJobStatus.SKIPPED,
            skip_reason=source_profile.unsupported_reason,
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
    _dispatch_ingestion_job(job.id, force=force)
    return job


async def _enqueue_index_phase_job_for_document(
    document_id: str,
) -> IngestionJob:
    """REVIEW 承認済みの文書に対し INDEX フェーズ job を投入する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status != FileStatus.REVIEW:
        raise HTTPException(
            status_code=409,
            detail="プレビュー確認待ちの文書のみ承認できます。",
        )
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


async def _enqueue_failed_segment_retry_job_for_document(
    document_id: str,
) -> IngestionJob:
    """FAILED segment checkpoint のみを対象にした再試行 job を投入する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status in (FileStatus.INGESTING, FileStatus.INDEXING):
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
    phase: IngestionJobPhase = IngestionJobPhase.EXTRACT,
    skip_reason: str | None = None,
) -> IngestionJob:
    """取込 job を永続化する。"""
    queued_at = datetime.now(UTC)
    settings = get_settings()
    job = IngestionJob(
        id=uuid4().hex,
        document_id=document_id,
        status=status,
        phase=phase,
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


def _document_ingestion_segments(
    detail: DocumentDetail,
    jobs: list[IngestionJob],
) -> list[IngestionSegment]:
    """保存済み extraction/job から segment view を推定する。"""
    source_profile = _source_profile_for_detail(detail)
    page_start, page_end = _document_page_range(detail.extraction)
    latest_job = max(jobs, key=lambda job: job.queued_at, default=None)
    attempt_count = latest_job.attempt_count if latest_job is not None else 0
    status = _segment_status_from_detail(detail, latest_job)
    error_message = detail.error_message or (latest_job.error_message if latest_job else None)
    return [
        IngestionSegment(
            segment_id=f"{detail.id}:source",
            document_id=detail.id,
            status=status,
            parser_backend=_parser_backend_from_extraction(detail.extraction, source_profile),
            parser_profile=source_profile.parser_profile,
            page_start=page_start,
            page_end=page_end,
            attempt_count=attempt_count,
            artifact_path=(
                _extraction_artifact_path(detail.extraction) or detail.object_storage_path
            ),
            error_code="ingestion_error" if error_message else None,
            error_message=error_message,
        )
    ]


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
                f'  <p class="page-marker" data-page="{current_page}">' f"page {current_page}</p>"
            )
        rendered = _element_html(element, tables_by_element_id=tables_by_element_id)
        if rendered:
            lines.append(rendered)
    for asset in sorted(extraction.assets, key=_asset_sort_key):
        if asset.page_number is not None and asset.page_number != current_page:
            current_page = asset.page_number
            lines.append(
                f'  <p class="page-marker" data-page="{current_page}">' f"page {current_page}</p>"
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

    async def is_cancelled() -> bool:
        current = await oracle.get_ingestion_job(job_id)
        return current is not None and current.status == IngestionJobStatus.CANCELLED

    try:
        if job.phase == IngestionJobPhase.INDEX:
            await _index_reviewed_document(
                job.document_id,
                cancel_checker=is_cancelled,
            )
        else:
            await _ingest_existing_document(
                job.document_id,
                force=True,
                cancel_checker=is_cancelled,
            )
    except HTTPException as exc:
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
        logger.info(
            "ingestion_job_cancelled",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except IngestionTimeoutError as exc:
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
    except Exception:
        await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message="取込処理に失敗しました。",
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
