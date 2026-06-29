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
from app.rag.kb_adapter_config import (
    KnowledgeBaseAdapterConfig,
    KnowledgeBaseIngestionConfig,
    apply_adapter_config_or_global,
    resolve_effective_adapter_config,
)
from app.rag.navigation import build_navigation_tree
from app.rag.rate_limit import enforce_rate_limit
from app.rag.source_profile import build_source_profile
from app.rag.variant_keys import (
    compute_chunk_set_id,
    compute_extraction_recipe_id,
    compute_layer_ids,
)
from app.rag.variant_planner import MaterializationPlan, plan_document_materializations
from app.schemas.common import ApiResponse, Page
from app.schemas.document import (
    BatchUploadFailedItem,
    BatchUploadResult,
    DocumentApproveRequest,
    DocumentBuildConfigGroup,
    DocumentBuildConfigState,
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
    DocumentStats,
    DocumentSummary,
    DocumentTableCellTextEdit,
    DuplicateDocumentRef,
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
CHUNK_SET_PUBLISH_ERROR_MESSAGE = "索引の公開設定に失敗しました。時間をおいて再実行してください。"
DELETE_BLOCKING_INGESTION_STATUSES = frozenset({IngestionJobStatus.RUNNING})


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


@router.get("/{document_id}/chunk-sets", response_model=ApiResponse[list[DocumentChunkSet]])
async def list_document_chunk_sets(document_id: str) -> ApiResponse[list[DocumentChunkSet]]:
    """文書の chunk_set(variant)一覧を返す。KB 詳細での variant 可視化に使う。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    rows = await oracle.list_document_chunk_sets(document_id)
    plan, configs = await _materialization_plan_for_document(oracle, detail)
    effective_by_kb = _effective_ingestion_settings_by_kb(get_settings(), configs)
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


async def _materialization_plan_for_document(
    oracle: OracleClient,
    detail: DocumentDetail,
    *,
    global_settings: Settings | None = None,
) -> tuple[MaterializationPlan | None, dict[str, KnowledgeBaseAdapterConfig]]:
    """文書の所属 KB 構築設定から materialization plan を復元する。"""
    configs = dict(await oracle.list_document_knowledge_base_configs(detail.id))
    if not detail.content_sha256 or not configs:
        return None, configs
    settings = global_settings or get_settings()
    return plan_document_materializations(detail.content_sha256, settings, configs), configs


def _effective_ingestion_settings_by_kb(
    global_settings: Settings,
    configs: Mapping[str, KnowledgeBaseAdapterConfig],
) -> dict[str, Settings]:
    """KB ごとの有効な構築設定を返す(3 層モデル: レシピは文書/global で KB 共通)。

    レイヤー状態表示を単一レシピ(global)に揃え、materialization と一致させる。
    KB 別取込上書きは使わない。
    """
    return {knowledge_base_id: global_settings for knowledge_base_id in configs}


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

    # ドリフト判定は取込済みで観測値があるときのみ。文書解析 / 文書分割はいずれも
    # 取込時にしか効かないため、差分があれば現在設定での再取込対象になる。
    chunking_drift = bool(
        is_indexed
        and observed_strategy is not None
        and observed_strategy != effective_settings.rag_chunking_strategy
    )
    parser_drift = bool(
        is_indexed
        and observed_parser is not None
        and _parser_backend_drifted(
            observed_parser,
            effective_settings.rag_parser_adapter_backend,
        )
    )
    config_drift = chunking_drift or parser_drift
    build_configurations = await _document_build_configurations(oracle, detail, owning)

    return ApiResponse(
        data=DocumentIngestionConfigData(
            document_id=document_id,
            is_indexed=is_indexed,
            owning_knowledge_base=(
                KnowledgeBaseRef(id=owning.id, name=owning.name) if owning is not None else None
            ),
            effective_preprocess_profile=effective_settings.rag_preprocess_profile,
            effective_chunking_strategy=effective_settings.rag_chunking_strategy,
            effective_parser_adapter_backend=effective_settings.rag_parser_adapter_backend,
            observed_chunking_strategy=observed_strategy,
            observed_parser_backend=observed_parser,
            chunking_drift=chunking_drift,
            parser_drift=parser_drift,
            config_drift=config_drift,
            build_configurations=build_configurations,
        )
    )


_BUILD_CONFIG_INTERNAL_FIELDS = frozenset(
    {
        "parser_docling_enabled",
        "parser_marker_enabled",
        "parser_unstructured_enabled",
        "parser_unlimited_ocr_enabled",
        "parser_mineru_enabled",
        "parser_dots_ocr_enabled",
        "parser_glm_ocr_enabled",
    }
)
_ACTIVE_DOCUMENT_STATUSES = frozenset(
    {
        FileStatus.PREPROCESSING,
        FileStatus.INGESTING,
        FileStatus.CHUNKING,
        FileStatus.INDEXING,
    }
)


async def _document_build_configurations(
    oracle: OracleClient,
    detail: DocumentDetail,
    owning: KnowledgeBaseDetail | None,
) -> list[DocumentBuildConfigGroup]:
    """所属 KB の有効な構築設定をまとめ、現在の物化/配信状態を付ける。"""
    configs = await oracle.list_document_knowledge_base_configs(detail.id)
    if not detail.content_sha256 or not configs:
        return []

    global_settings = get_settings()
    refs = {knowledge_base.id: knowledge_base for knowledge_base in detail.knowledge_bases}
    grouped: dict[
        str,
        tuple[Settings, KnowledgeBaseIngestionConfig, list[KnowledgeBaseRef]],
    ] = {}
    for knowledge_base_id, config in configs:
        settings, applied = apply_adapter_config_or_global(
            global_settings,
            config,
            scope="ingestion",
        )
        display_config = config if applied or config.is_empty() else KnowledgeBaseAdapterConfig()
        effective_config = resolve_effective_adapter_config(
            global_settings,
            display_config,
        ).ingestion
        key = _build_config_group_key(effective_config)
        reference = refs.get(knowledge_base_id)
        if reference is None:
            continue
        if key in grouped:
            grouped[key][2].append(reference)
        else:
            grouped[key] = (settings, effective_config, [reference])

    rows = await oracle.list_document_chunk_sets(detail.id)
    row_by_id = {str(row["chunk_set_id"]): row for row in rows}
    chunk_set_ids = [
        compute_chunk_set_id(detail.content_sha256, settings)
        for settings, _effective, _refs in grouped.values()
    ]
    persisted_layers = await oracle.list_artifact_layers_for_chunk_sets(chunk_set_ids)

    result: list[DocumentBuildConfigGroup] = []
    extractions: dict[str, Mapping[str, object] | None] = {}
    for settings, effective_config, knowledge_bases in grouped.values():
        knowledge_bases.sort(key=lambda item: (item.name.casefold(), item.id))
        layer_ids = compute_layer_ids(detail.content_sha256, settings)
        row = row_by_id.get(layer_ids["chunk_set_id"])
        extraction_recipe_id = layer_ids["extraction_recipe_id"]
        if extraction_recipe_id not in extractions:
            extractions[extraction_recipe_id] = await oracle.get_document_extraction_artifact(
                document_id=detail.id,
                extraction_recipe_id=extraction_recipe_id,
            )
        extraction = extractions[extraction_recipe_id]
        layer_statuses = _configuration_layer_statuses(
            settings,
            layer_ids,
            persisted_layers,
        )
        state, reason = _build_config_state(
            detail,
            knowledge_bases,
            row,
            extraction,
            layer_statuses,
        )
        serving_ids = _build_config_string_set((row or {}).get("serving_knowledge_base_ids", []))
        result.append(
            DocumentBuildConfigGroup(
                knowledge_bases=knowledge_bases,
                effective_config=effective_config,
                is_review_target=bool(
                    owning and any(item.id == owning.id for item in knowledge_bases)
                ),
                extraction_recipe_id=extraction_recipe_id,
                chunk_set_id=layer_ids["chunk_set_id"],
                state=state,
                reason=reason,
                chunk_count=_metadata_int((row or {}).get("chunk_count")) or 0,
                vector_count=_metadata_int((row or {}).get("vector_count")) or 0,
                serving_knowledge_base_count=sum(
                    item.id in serving_ids for item in knowledge_bases
                ),
                layer_statuses=layer_statuses,
            )
        )
    return sorted(
        result,
        key=lambda group: (
            not group.is_review_target,
            group.knowledge_bases[0].name.casefold(),
            group.chunk_set_id,
        ),
    )


def _build_config_group_key(config: KnowledgeBaseIngestionConfig) -> str:
    """UI で編集できる有効値だけを使い、同じ設定の KB をまとめる。"""
    values = config.model_dump(mode="json", exclude=set(_BUILD_CONFIG_INTERNAL_FIELDS))
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_config_string_set(value: object) -> set[str]:
    if not isinstance(value, list | tuple | set | frozenset):
        return set()
    return {str(item) for item in value}


def _configuration_layer_statuses(
    settings: Settings,
    layer_ids: Mapping[str, str],
    persisted_layers: Mapping[str, Mapping[str, object]],
) -> DocumentChunkSetLayerStatuses:
    return DocumentChunkSetLayerStatuses(
        metadata=_configuration_layer_status(
            requested=_layer_requested("metadata", settings),
            layer_id=layer_ids["metadata_layer_id"],
            persisted_layers=persisted_layers,
            user_label="項目抽出・画像要約",
        ),
        graph=_configuration_layer_status(
            requested=_layer_requested("graph", settings),
            layer_id=layer_ids["graph_layer_id"],
            persisted_layers=persisted_layers,
            user_label="関係情報",
        ),
        navigation=_configuration_layer_status(
            requested=_layer_requested("navigation", settings),
            layer_id=layer_ids["nav_layer_id"],
            persisted_layers=persisted_layers,
            user_label="ナビゲーション",
        ),
    )


def _configuration_layer_status(
    *,
    requested: bool,
    layer_id: str,
    persisted_layers: Mapping[str, Mapping[str, object]],
    user_label: str,
) -> DocumentMaterializationLayerStatus:
    if not requested:
        return DocumentMaterializationLayerStatus(
            requested=False,
            status=DocumentLayerStatusName.NOT_REQUESTED,
            reason=f"現在の構築設定では{user_label}を使用しません。",
        )
    persisted = persisted_layers.get(layer_id)
    if persisted is None:
        return DocumentMaterializationLayerStatus(
            layer_id=layer_id,
            requested=True,
            status=DocumentLayerStatusName.PLANNED_ONLY,
            reason=f"{user_label}は構築計画に含まれていますが、まだ実体化していません。",
        )
    return DocumentMaterializationLayerStatus(
        layer_id=layer_id,
        requested=bool(persisted.get("requested", True)),
        status=DocumentLayerStatusName(
            str(persisted.get("status") or DocumentLayerStatusName.PLANNED_ONLY.value)
        ),
        reason=str(persisted["reason"]) if persisted.get("reason") is not None else None,
    )


def _build_config_state(
    detail: DocumentDetail,
    knowledge_bases: list[KnowledgeBaseRef],
    chunk_set: Mapping[str, object] | None,
    extraction: Mapping[str, object] | None,
    layer_statuses: DocumentChunkSetLayerStatuses,
) -> tuple[DocumentBuildConfigState, str]:
    layer_states = {
        layer_statuses.metadata.status,
        layer_statuses.graph.status,
        layer_statuses.navigation.status,
    }
    extraction_status = str((extraction or {}).get("status") or "")
    chunk_set_status = str((chunk_set or {}).get("status") or "")
    if (
        detail.status == FileStatus.ERROR
        or extraction_status == DocumentLayerStatusName.ERROR.value
        or chunk_set_status == "ERROR"
        or DocumentLayerStatusName.ERROR.value in layer_states
    ):
        return DocumentBuildConfigState.ERROR, "この構築設定の処理に失敗しています。"
    if detail.status in _ACTIVE_DOCUMENT_STATUSES or chunk_set_status in {"INGESTING", "CHUNKED"}:
        return DocumentBuildConfigState.BUILDING, "この構築設定の処理を進めています。"
    if (
        extraction_status == DocumentLayerStatusName.NEEDS_REINGEST.value
        or DocumentLayerStatusName.NEEDS_REINGEST.value in layer_states
    ):
        return (
            DocumentBuildConfigState.UPDATE_REQUIRED,
            "現在の構築設定で再取込が必要です。",
        )
    expected_ids = {knowledge_base.id for knowledge_base in knowledge_bases}
    serving_ids = _build_config_string_set((chunk_set or {}).get("serving_knowledge_base_ids", []))
    if chunk_set_status == "INDEXED" and expected_ids <= serving_ids:
        return DocumentBuildConfigState.SERVING, "この構築設定を検索に使用しています。"
    if detail.status == FileStatus.INDEXED:
        return (
            DocumentBuildConfigState.UPDATE_REQUIRED,
            "現在の構築設定に対応する結果が未構築または未配信です。",
        )
    return DocumentBuildConfigState.PLANNED, "取込時にこの構築設定を適用します。"


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
    # extraction 層(正本)もレビュー編集に追従させる(無ければ 0 件更新で legacy へ縮退)。
    await oracle.update_document_extractions_payload(document_id=document_id, extraction=normalized)


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
    global_settings = get_settings()
    plan, _configs = await _materialization_plan_for_document(
        oracle,
        detail,
        global_settings=global_settings,
    )
    ingest_prompt = "ドキュメントを日本語で OCR し、本文テキストを抽出してください。"
    if plan is None or not plan.chunk_sets:
        # 3 層モデル: materialization のレシピは常に global(KB からは解決しない)。
        effective_settings = get_settings()
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
        await _reconcile_document_chunk_sets(oracle, document_id, result, chunk_set_id)
        return result
    # plan 実体化: 抽出グループ(parser×preprocess)ごとに extract 1 回 → 各 chunking で index。
    result = detail
    recipe_groups = plan.chunk_sets_by_extraction_recipe()
    total_chunk_sets = sum(len(chunk_set_ids) for chunk_set_ids in recipe_groups.values())
    processed_chunk_sets = 0
    for _recipe_id, chunk_set_ids in recipe_groups.items():
        for index, chunk_set_id in enumerate(chunk_set_ids):
            # 3 層モデル: レシピは文書単位(global)。chunk_set_id も global から計算済み。
            pipeline = IngestionPipeline(oracle=oracle, settings=global_settings)
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
    await _reconcile_plan_chunk_sets(oracle, document_id, result, plan)
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
    global_settings = get_settings()
    plan, _configs = await _materialization_plan_for_document(
        oracle,
        detail,
        global_settings=global_settings,
    )
    if plan is None or not plan.chunk_sets:
        # 3 層モデル: 所属 KB / content_sha256 が無い縮退でもレシピは global を使う。
        effective_settings = get_settings()
        chunk_set_id = _document_chunk_set_id(detail, effective_settings)
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.chunk_reviewed(
            document_id, chunk_set_id=chunk_set_id, cancel_checker=cancel_checker
        )
        await _reconcile_document_chunk_sets_chunked(oracle, document_id, result, chunk_set_id)
        return result
    # 3 層モデル: plan は常に単一 extraction recipe。保存済み extraction から chunk 化する。
    result = detail
    chunk_set_ids = sorted(plan.chunk_sets)
    for index, chunk_set_id in enumerate(chunk_set_ids):
        pipeline = IngestionPipeline(oracle=oracle, settings=global_settings)
        # 成功 metric/audit は最後の chunk_set でのみ出し、1 文書 1 論理取込に集約する。
        result = await pipeline.chunk_reviewed(
            document_id,
            chunk_set_id=chunk_set_id,
            record_outcome=index == len(chunk_set_ids) - 1,
            cancel_checker=cancel_checker,
        )
    await _reconcile_plan_chunk_sets_chunked(oracle, document_id, result, plan)
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
    global_settings = get_settings()
    plan, _configs = await _materialization_plan_for_document(
        oracle,
        detail,
        global_settings=global_settings,
    )
    if plan is None or not plan.chunk_sets:
        # 3 層モデル: materialization のレシピは常に global。
        effective_settings = get_settings()
        chunk_set_id = _document_chunk_set_id(detail, effective_settings)
        if chunk_set_id is None:
            raise HTTPException(status_code=409, detail="索引対象の chunk_set がありません。")
        pipeline = IngestionPipeline(oracle=oracle, settings=effective_settings)
        result = await pipeline.index_chunked(
            document_id, chunk_set_id=chunk_set_id, cancel_checker=cancel_checker
        )
        await _reconcile_document_chunk_sets(oracle, document_id, result, chunk_set_id)
        return result
    result = detail
    chunk_set_ids = sorted(plan.chunk_sets)
    for index, chunk_set_id in enumerate(chunk_set_ids):
        # 3 層モデル: レシピは文書単位(global)。
        pipeline = IngestionPipeline(oracle=oracle, settings=global_settings)
        result = await pipeline.index_chunked(
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

    chunk は save_index で挿入時タグ付け済み。KB binding / extraction artifact まで揃って
    初めて検索可能な INDEXED とみなすため、失敗時は ERROR に戻す。
    """
    if detail.status != FileStatus.INDEXED:
        return
    try:
        for chunk_set_id, knowledge_base_ids in plan.chunk_sets.items():
            chunk_count = await oracle.count_chunk_set_chunks(chunk_set_id)
            extraction_recipe_id = plan.extraction_recipe_for_chunk_set(chunk_set_id)
            await oracle.upsert_chunk_set(
                chunk_set_id=chunk_set_id,
                document_id=document_id,
                extraction_recipe_id=extraction_recipe_id,
            )
            await oracle.mark_chunk_set_indexed(
                chunk_set_id=chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
            )
            for knowledge_base_id in knowledge_base_ids:
                await oracle.upsert_chunk_set_binding(
                    knowledge_base_id=knowledge_base_id,
                    document_id=document_id,
                    chunk_set_id=chunk_set_id,
                )
            if extraction_recipe_id is not None:
                # 3 層モデル: extraction recipe は global から計算済み。artifact 記録も global で。
                await _record_document_extraction_artifact(
                    oracle,
                    detail,
                    extraction_recipe_id=extraction_recipe_id,
                    settings=get_settings(),
                )
        # 3 層モデル: 文書の serving chunk_set を設定(単一レシピなので plan の chunk_set)。
        serving_chunk_sets = sorted(plan.chunk_sets)
        if serving_chunk_sets:
            await oracle.set_document_serving_chunk_set(
                document_id=document_id, chunk_set_id=serving_chunk_sets[0]
            )
        await _reconcile_plan_artifact_layers(oracle, document_id, detail, plan)
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
) -> None:
    """plan の各 chunk_set を CHUNKED として永続化する。KB binding は INDEX 後に作る。"""
    if detail.status != FileStatus.CHUNKED:
        return
    try:
        for chunk_set_id in plan.chunk_sets:
            chunk_count = await oracle.count_chunk_set_chunks(chunk_set_id)
            extraction_recipe_id = plan.extraction_recipe_for_chunk_set(chunk_set_id)
            await oracle.upsert_chunk_set(
                chunk_set_id=chunk_set_id,
                document_id=document_id,
                extraction_recipe_id=extraction_recipe_id,
                status="CHUNKED",
            )
            await oracle.mark_chunk_set_chunked(chunk_set_id=chunk_set_id, chunk_count=chunk_count)
            if extraction_recipe_id is not None:
                # 3 層モデル: extraction recipe は global から計算済み。artifact 記録も global で。
                await _record_document_extraction_artifact(
                    oracle,
                    detail,
                    extraction_recipe_id=extraction_recipe_id,
                    settings=get_settings(),
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
) -> None:
    """plan に含まれる派生 layer の状態を永続化する。"""
    configs = dict(await oracle.list_document_knowledge_base_configs(document_id))
    effective_by_kb = _effective_ingestion_settings_by_kb(get_settings(), configs)
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
) -> tuple[DocumentLayerStatusName, str]:
    if not detail.extraction:
        return (
            DocumentLayerStatusName.NEEDS_REINGEST,
            (
                f"{user_label}の作成に必要な抽出 artifact がありません。"
                "現在の構築設定で再取込してください。"
            ),
        )
    if layer == "metadata" and _metadata_layer_is_materialized(detail.extraction):
        return (
            DocumentLayerStatusName.MATERIALIZED,
            f"{user_label}は保存済み抽出 artifact から実体化済みです。",
        )
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


def _metadata_layer_is_materialized(extraction: Mapping[str, object]) -> bool:
    """field / asset summary 由来の metadata layer が実 payload を持つかを見る。"""
    return any(bool(extraction.get(key)) for key in ("fields", "assets", "asset_summary"))


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
    return {
        "preprocess_profile": getattr(settings, "rag_preprocess_profile", "passthrough"),
        "parser_adapter_backend": getattr(settings, "rag_parser_adapter_backend", "local"),
        "parser_docling_enabled": bool(getattr(settings, "rag_parser_docling_enabled", False)),
        "parser_marker_enabled": bool(getattr(settings, "rag_parser_marker_enabled", False)),
        "parser_unstructured_enabled": bool(
            getattr(settings, "rag_parser_unstructured_enabled", False)
        ),
        "parser_unlimited_ocr_enabled": bool(
            getattr(settings, "rag_parser_unlimited_ocr_enabled", False)
        ),
        "parser_mineru_enabled": bool(getattr(settings, "rag_parser_mineru_enabled", False)),
        "parser_dots_ocr_enabled": bool(getattr(settings, "rag_parser_dots_ocr_enabled", False)),
        "parser_glm_ocr_enabled": bool(getattr(settings, "rag_parser_glm_ocr_enabled", False)),
        "parser_asr_enabled": bool(getattr(settings, "rag_parser_asr_enabled", False)),
    }


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
) -> None:
    """取込後、materialize した chunk_set を記録し所属 KB を binding する(planner 駆動の基盤)。

    chunk は save_index で**挿入時に chunk_set_id タグ付け済み**。本関数は chunk_set 行の永続化・
    KB binding・旧 chunk_set(とその chunk、未タグ chunk)の GC を行う。公開 binding まで揃って
    初めて検索可能な INDEXED とみなすため、失敗時は ERROR に戻す。
    """
    if detail.status != FileStatus.INDEXED or chunk_set_id is None:
        return
    try:
        chunk_count = await oracle.count_document_chunks(document_id)
        # 3 層モデル: materialization のレシピは常に global。
        effective_settings = get_settings()
        extraction_recipe_id = _document_extraction_recipe_id(detail, effective_settings)
        await oracle.upsert_chunk_set(
            chunk_set_id=chunk_set_id,
            document_id=document_id,
            extraction_recipe_id=extraction_recipe_id,
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
        await oracle.set_document_serving_chunk_set(
            document_id=document_id, chunk_set_id=chunk_set_id
        )
        # 単一 materialization なので所属 KB すべてをこの chunk_set に bind する(binding は当面
        # dual-write で温存。退役は後続の increment で実施)。
        knowledge_bases = await oracle.list_document_knowledge_bases(document_id)
        for knowledge_base in knowledge_bases:
            await oracle.upsert_chunk_set_binding(
                knowledge_base_id=knowledge_base.id,
                document_id=document_id,
                chunk_set_id=chunk_set_id,
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
) -> None:
    """CHUNK 後、chunk_set 行だけを記録する。KB binding は INDEX 完了まで作らない。"""
    if detail.status != FileStatus.CHUNKED or chunk_set_id is None:
        return
    try:
        chunk_count = await oracle.count_chunk_set_chunks(chunk_set_id)
        # 3 層モデル: materialization のレシピは常に global。
        effective_settings = get_settings()
        extraction_recipe_id = _document_extraction_recipe_id(detail, effective_settings)
        await oracle.upsert_chunk_set(
            chunk_set_id=chunk_set_id,
            document_id=document_id,
            extraction_recipe_id=extraction_recipe_id,
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
) -> tuple[Settings, KnowledgeBaseDetail | None]:
    """owning KB の取込上書きを重ねた有効 Settings と owning KB detail を返す(**表示専用**)。

    取込設定スナップショット / ドリフト表示 endpoint 用。3 層モデルでは materialization は
    この関数を使わず常に global 既定で実体化する(レシピ=文書プロパティ)。owning KB の
    取込表示は Phase 4 で文書単位レシピ表示へ置き換える予定。
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
) -> IngestionJob:
    """FAILED segment checkpoint のみを対象にした再試行 job を投入する。"""
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

    async def is_cancelled() -> bool:
        current = await oracle.get_ingestion_job(job_id)
        return current is not None and current.status == IngestionJobStatus.CANCELLED

    try:
        if job.phase == IngestionJobPhase.CHUNK:
            detail = await _chunk_reviewed_document(
                job.document_id,
                cancel_checker=is_cancelled,
            )
        elif job.phase == IngestionJobPhase.INDEX:
            detail = await _index_reviewed_document(
                job.document_id,
                cancel_checker=is_cancelled,
            )
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
    except Exception as exc:
        await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=_safe_ingestion_job_error_message(exc),
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


def _safe_ingestion_job_error_message(error: Exception) -> str:
    if getattr(error, "safe_for_user", False):
        message = str(error).replace("\n", " ").strip()
        if message:
            return message[:2000]
    return "取込処理に失敗しました。"


async def _enqueue_auto_advance_job(job: IngestionJob, detail: DocumentDetail) -> None:
    """グローバル設定に従い次 stage の job を投入する(3 層モデル: レシピは global)。"""
    try:
        settings = get_settings()
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
    if variant == "prepared":
        artifact = detail.preprocess_artifact
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
