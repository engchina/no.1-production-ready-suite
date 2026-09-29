"""ドキュメント API。アップロード・一覧・取込(抽出→索引)。"""

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
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
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.clients.object_storage import ObjectStorageClient
from app.clients.oci_genai import EMBEDDING_INPUT_MAX_CHARS
from app.clients.oracle import (
    DocumentDeleteBlockedByRunningIngestionError,
    OracleClient,
    is_transient_oracle_error,
    oracle_error_log_fields,
)
from app.config import (
    CHUNKING_STRATEGIES_WITH_MIN_CHARS,
    LEGACY_CHUNKING_STRATEGY_ALIASES,
    Settings,
    get_settings,
)
from app.db_degradation import load_or_degrade
from app.rag.chunking import Chunk, chunk_extraction_with_strategy
from app.rag.chunking_strategy import resolve_docrag_chunking_params
from app.rag.docrag_chunking import (
    DOCRAG_CHUNKING_STRATEGY,
    DOCRAG_FALLBACK_CHUNKING_STRATEGY,
    build_docrag_chunks,
    docrag_fallback_needed,
    mark_docrag_fallback,
)
from app.rag.document_crop import (
    DocumentSourceNotFoundError,
    crop_png,
    load_parsed_source,
    page_sizes,
    render_page_png,
)
from app.rag.extraction_field_adapter import load_field_schema
from app.rag.ingestion import (
    IngestionCancelledError,
    IngestionPipeline,
    IngestionTimeoutError,
    IngestionUserError,
    document_artifact_prefixes,
)
from app.rag.ingestion_worker import request_ingestion_worker_wakeup
from app.rag.kb_adapter_config import (
    KbAdapterConfigError,
    KnowledgeBaseAdapterConfig,
    resolve_effective_adapter_config,
    resolve_effective_settings,
)
from app.rag.navigation import build_navigation_tree
from app.rag.parser_source_guard import check_parser_source
from app.rag.rate_limit import enforce_rate_limit
from app.rag.request_context import current_audit_request_context
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
    DocumentApproveRequest,
    DocumentChunkPreviewRequest,
    DocumentChunkPreviewResponse,
    DocumentChunkPreviewStats,
    DocumentChunkSet,
    DocumentChunkSetLayerStatuses,
    DocumentChunkView,
    DocumentClassification,
    DocumentDeleteImpact,
    DocumentDeleteResult,
    DocumentDetail,
    DocumentExtractionExport,
    DocumentExtractionExportFormat,
    DocumentLayerStatusName,
    DocumentMaterializationLayerStatus,
    DocumentPreprocessArtifact,
    DocumentPreviewPage,
    DocumentPreviewPages,
    DocumentProcessingConfig,
    DocumentRecipeCreateRequest,
    DocumentRecipeDeleteResult,
    DocumentRecipeStep,
    DocumentRecipeStepStatus,
    DocumentRecipeView,
    DocumentReviewEditsRequest,
    DocumentSummary,
    DocumentTableCellTextEdit,
    DuplicateDocumentRef,
    FileStatus,
    IngestionJob,
    IngestionJobLease,
    IngestionJobPhase,
    IngestionJobStatus,
    IngestionSegment,
    ParserSourceNotice,
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
UPLOAD_STORAGE_FAILED_MESSAGE = (
    "原本を保存先に保存できませんでした。システム設定 > アップロード保存先 の設定を確認してから、"
    "もう一度アップロードしてください。"
)
# rag_documents.file_name は VARCHAR2(512)（BYTE 長）。日本語の長いファイル名が DB の INSERT で
# 失敗しないよう、表示・保存用のファイル名は文字数と UTF-8 のバイト数の両方で切り詰める（#280）。
MAX_UPLOAD_FILE_NAME_CHARS = 255
MAX_UPLOAD_FILE_NAME_BYTES = 512
_MAX_PRESERVED_SUFFIX_CHARS = 16
CHUNK_SET_PUBLISH_ERROR_MESSAGE = "索引の公開設定に失敗しました。時間をおいて再実行してください。"
DELETE_BLOCKING_INGESTION_STATUSES = frozenset({IngestionJobStatus.RUNNING})
# cancel API が CANCELLED へ遷移できる状態。
_CANCELLABLE_INGESTION_JOB_STATUSES = (IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING)
# lease を持つ実行が、自分の実行として結果を書ける・後始末できる job の状態(#359)。RUNNING は
# 実行中、CANCELLED は実行中に利用者が取り消した job(取り消しの API は lease を変えない)。
# stale の回復は job を QUEUED(lease を外す)か FAILED に戻すため、どちらも自分の実行ではない。
_LEASE_HELD_INGESTION_JOB_STATUSES = frozenset(
    {IngestionJobStatus.RUNNING, IngestionJobStatus.CANCELLED}
)
# ブラウザが開くとスクリプトを実行しうる形式。原本配信では CSP sandbox を付ける。
SCRIPTABLE_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
    }
)
# DocRAG 親子階層の分割パラメータ(文書レシピの項目名)。分割方式が docrag_small_to_big の
# ときだけ分割結果に効くので、差分(drift)の判定もそのときだけ比べる。
DOCRAG_PROCESSING_CONFIG_FIELDS: tuple[str, ...] = (
    "docrag_child_target_chars",
    "docrag_table_child_target_chars",
    "docrag_parent_target_chars",
    "docrag_parent_max_pages",
    "docrag_parent_max_children",
)
DOCUMENT_PROCESSING_OUTPUT_GROUPS: dict[str, tuple[str, ...]] = {
    "preprocess_profile": ("preprocess_profile",),
    "parser_adapter_backend": (
        "parser_adapter_backend",
        "parser_docling_enabled",
        "parser_docling_vision_enabled",
        "parser_unstructured_enabled",
        "parser_mineru_enabled",
        "parser_dots_ocr_enabled",
    ),
    "chunking_strategy": (
        "chunking_strategy",
        "chunk_size",
        "chunk_overlap",
        "chunk_min_chars",
        "chunk_context_header_enabled",
        *DOCRAG_PROCESSING_CONFIG_FIELDS,
    ),
    "graph_profile": ("graph_profile",),
    "field_extraction_enabled": ("field_extraction_enabled",),
    "asset_summary_enabled": ("asset_summary_enabled",),
    "navigation_summary_enabled": ("navigation_summary_enabled",),
}


@router.post("/upload", response_model=ApiResponse[UploadResult])
async def upload_document(
    http_request: Request,
    file: Annotated[UploadFile, File(...)],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
) -> ApiResponse[UploadResult]:
    """ドキュメントファイルをアップロードし、Object Storage へ保管する。

    原本の保存と文書行の登録までを行い、取込 job は作らない（取込は文書ごとに明示して始める）。
    """
    enforce_rate_limit("upload", http_request)
    result = await _store_uploaded_document(file, knowledge_base_ids)
    return ApiResponse(data=result)


@router.post("/batch-upload", response_model=ApiResponse[BatchUploadResult])
async def batch_upload_documents(
    http_request: Request,
    files: Annotated[list[UploadFile], File(...)],
    knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
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
    selected_knowledge_base_ids = _normalize_upload_knowledge_base_ids(knowledge_base_ids)
    if (
        not selected_knowledge_base_ids
        and current_audit_request_context().allowed_knowledge_base_ids is not None
    ):
        # 利用できる KB が制限された利用者は、KB を指定しないと DEFAULT KB に入り、
        # 自分では見えない文書になる（#214）。
        raise HTTPException(
            status_code=400,
            detail="アップロード先のナレッジベースを指定してください。",
        )
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
    try:
        object_path = await storage.put(
            key=key,
            data=data,
            content_type=content_type,
        )
    except Exception as exc:
        # 保存先の未設定・認証切れ・容量不足などは、原因の分かる 503 にする（#280）。
        logger.exception(
            "upload_storage_put_failed",
            extra={"file_name": file_name, "exception_type": type(exc).__name__},
        )
        raise HTTPException(status_code=503, detail=UPLOAD_STORAGE_FAILED_MESSAGE) from exc
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
    except Exception as exc:
        # 文書行を作れなかった原本は、どの文書からも参照されず保存先に残り続ける。
        # 範囲外・アーカイブ済みの KB や DB の失敗でも、保存した原本を消してから返す（#280）。
        await _delete_orphan_upload_object(storage, object_path)
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return UploadResult(
        id=detail.id,
        file_name=detail.file_name,
        status=detail.status,
        file_size_bytes=detail.file_size_bytes or len(data),
        content_sha256=content_sha256,
        duplicate_of_document_id=detail.duplicate_of_document_id,
        knowledge_bases=detail.knowledge_bases,
        source_profile=source_profile,
        parser_notice=await _parser_notice(settings, source_profile),
    )


async def _parser_notice(
    settings: Settings, source_profile: SourceProfile
) -> ParserSourceNotice | None:
    """既定の文書解析エンジンで扱えない形式なら、取込を始める前の案内を返す(#286)。"""
    block = await check_parser_source(settings, source_profile)
    if block is None:
        return None
    return ParserSourceNotice(
        code=block.code,
        backend=block.backend,
        file_format=block.file_format,
        suggested_backend=block.suggested_backend,
        message=block.message,
    )


async def _raise_if_parser_source_blocked(
    settings: Settings, source_profile: SourceProfile, phase: IngestionJobPhase
) -> None:
    """解析を含む工程(準備・抽出)を、選んだ解析エンジンで扱えない形式なら始めずに 409 にする。"""
    if phase not in {IngestionJobPhase.PREPROCESS, IngestionJobPhase.EXTRACT}:
        return
    block = await check_parser_source(settings, source_profile)
    if block is not None:
        raise HTTPException(status_code=409, detail=block.message)


async def _default_recipe_settings(oracle: OracleClient, document_id: str) -> Settings:
    """文書単位の取込(既定レシピ)で使う実効設定。レシピを読めなければ global 既定。"""
    config = DocumentProcessingConfig()
    ensure_recipe = getattr(oracle, "ensure_default_document_recipe", None)
    get_recipe = getattr(oracle, "get_document_recipe", None)
    if callable(ensure_recipe) and callable(get_recipe):
        recipe = await ensure_recipe(document_id)
        row = await get_recipe(document_id, str(recipe["recipe_id"]))
        if row is not None:
            config = DocumentProcessingConfig.model_validate(row.get("processing_config") or {})
    settings, _ = _merge_document_processing_config(config)
    return settings


async def _delete_orphan_upload_object(storage: ObjectStorageClient, object_path: str) -> None:
    """文書行を作れなかったアップロードの原本を best-effort で削除する。"""
    try:
        await storage.delete(object_path)
    except Exception:
        # 後始末の失敗で元のエラーを隠さない。残った原本はログから追えるようにする。
        logger.warning(
            "upload_orphan_object_cleanup_failed",
            extra={"object_storage_path": object_path},
            exc_info=True,
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


DELETE_IMPACT_MAX_DOCUMENTS = 100


@router.get("/delete-impact", response_model=ApiResponse[list[DocumentDeleteImpact]])
async def document_delete_impact(
    document_id: Annotated[
        list[str],
        Query(min_length=1, max_length=DELETE_IMPACT_MAX_DOCUMENTS),
    ],
) -> ApiResponse[list[DocumentDeleteImpact]]:
    """削除の前に、正本を参照する重複文書の件数と所属 KB を返す（#303）。

    重複文書は chunk を持たず正本の chunk を使うため、正本を消すとその KB の検索対象から
    内容が消える。自前の索引を持つ（INDEXED の）重複文書と、同時に削除する文書は数えない。
    """
    requested = list(dict.fromkeys(value.strip() for value in document_id if value.strip()))
    if not requested:
        raise HTTPException(status_code=422, detail="document_id を指定してください。")
    deleting = set(requested)
    duplicates = await OracleClient().list_duplicate_documents(requested)
    impacts: dict[str, DocumentDeleteImpact] = {
        requested_id: DocumentDeleteImpact(document_id=requested_id) for requested_id in requested
    }
    for duplicate in duplicates:
        source_id = duplicate.duplicate_of_document_id
        if source_id not in impacts or duplicate.id in deleting:
            continue
        if duplicate.status == FileStatus.INDEXED:
            continue
        impact = impacts[source_id]
        known = {knowledge_base.id for knowledge_base in impact.knowledge_bases}
        impact.duplicate_count += 1
        impact.knowledge_bases.extend(
            knowledge_base
            for knowledge_base in duplicate.knowledge_bases
            if knowledge_base.id not in known
        )
    return ApiResponse(data=list(impacts.values()))


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
    """完了済みまたは失敗済み job の対象を新しい job として再投入する。

    レシピの job(``recipe_id`` あり)は同じレシピの job として投入し、文書全体(全レシピ)の
    出力は初期化しない(#305)。``force`` は文書単位の job の重複・未対応の skip 判定にだけ使う。
    """
    enforce_rate_limit("ingest", http_request)
    job = await OracleClient().get_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="取込ジョブが見つかりません。")
    if job.status in {IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="この取込ジョブはまだ実行中です。")
    if job.recipe_id is not None:
        try:
            recipe_job = await _enqueue_ingestion_job_for_recipe(
                job.document_id, job.recipe_id, phase=job.phase
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="レシピが見つかりません。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiResponse(data=recipe_job)
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
    """待機中または実行中の取込 job をキャンセル済みにする。

    job の状態だけを QUEUED / RUNNING → CANCELLED に条件付きで変える(#305)。文書・レシピの
    status は戻さない。

    - RUNNING の job: 処理を続けている worker が cancel を検知し、文書単位の job なら文書を、
      レシピの job ならレシピ行だけを戻す(`_restore_statuses_after_cancel`)。
    - QUEUED の job: worker がまだ何も書いておらず、投入時の文書・レシピは安定した状態のため、
      戻すものはない。
    """
    enforce_rate_limit("ingest", http_request)
    oracle = OracleClient()
    job = await oracle.get_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="取込ジョブが見つかりません。")
    if job.status not in _CANCELLABLE_INGESTION_JOB_STATUSES:
        raise HTTPException(status_code=409, detail="この取込ジョブはキャンセルできません。")
    cancelled = await oracle.transition_ingestion_job(
        job_id,
        from_statuses=_CANCELLABLE_INGESTION_JOB_STATUSES,
        to_status=IngestionJobStatus.CANCELLED,
        error_message=INGESTION_JOB_CANCELLED_MESSAGE,
        finished_at=datetime.now(UTC),
    )
    if cancelled is None:
        # 確かめた後に完了・失敗した。その最終状態を CANCELLED で上書きしない。
        raise HTTPException(status_code=409, detail="この取込ジョブはキャンセルできません。")
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
    if not await oracle.document_exists(document_id):
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
                else DocumentRecipeStepStatus.FAILED
                if i == failed_index
                else _STEP_PENDING
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
    # レシピの解析エンジン(既定は Docling)で扱えない形式は、job を作る前に止める(#286)。
    await _raise_if_parser_source_blocked(effective_settings, source_profile, phase)
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
    # Docling の解析結果がない文書では、DocRAG 親子階層の代わりに構造認識で分割する(#300)。
    docrag_fallback = docrag_fallback_needed(candidate.rag_chunking_strategy, extraction)
    try:
        if candidate.rag_chunking_strategy == DOCRAG_CHUNKING_STRATEGY and not docrag_fallback:
            chunks = build_docrag_chunks(
                extraction,
                source_name=detail.file_name,
                params=resolve_docrag_chunking_params(candidate),
            )
        else:
            chunks = chunk_extraction_with_strategy(
                extraction,
                strategy=(
                    DOCRAG_FALLBACK_CHUNKING_STRATEGY
                    if docrag_fallback
                    else candidate.rag_chunking_strategy
                ),
                chunk_size=candidate.rag_chunk_size,
                overlap=candidate.rag_chunk_overlap,
                min_chars=candidate.rag_chunk_min_chars,
                delimiter=candidate.rag_chunk_delimiter,
            )
            if docrag_fallback:
                chunks = mark_docrag_fallback(chunks)
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


async def _recipe_detail_and_artifact(
    document_id: str, recipe_id: str
) -> tuple[DocumentDetail, DocumentPreprocessArtifact | None]:
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
    return detail, artifact


@router.get(
    "/{document_id}/recipes/{recipe_id}/preview-pages",
    response_model=ApiResponse[DocumentPreviewPages],
)
async def document_recipe_preview_pages(
    document_id: str,
    recipe_id: str,
    variant: Annotated[Literal["original", "prepared"], Query()] = "original",
) -> ApiResponse[DocumentPreviewPages]:
    """選択レシピの原本 / 処理後ファイルのページ一覧(ページ画像のプレビュー用)。"""
    detail, artifact = await _recipe_detail_and_artifact(document_id, recipe_id)
    return await _document_preview_pages_response(
        detail, variant=variant, preprocess_artifact=artifact
    )


@router.get("/{document_id}/recipes/{recipe_id}/preview-pages/{page_number}")
async def document_recipe_preview_page_image(
    document_id: str,
    recipe_id: str,
    page_number: Annotated[int, Path(ge=1, le=10000)],
    variant: Annotated[Literal["original", "prepared"], Query()] = "original",
    dpi: Annotated[int, Query(ge=48, le=288)] = 144,
) -> Response:
    """選択レシピの原本 / 処理後ファイルの 1 ページを PNG で返す(bbox の強調を重ねる)。"""
    detail, artifact = await _recipe_detail_and_artifact(document_id, recipe_id)
    return await _document_preview_page_image_response(
        detail, page_number=page_number, variant=variant, dpi=dpi, preprocess_artifact=artifact
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
        candidate.rag_chunking_strategy in CHUNKING_STRATEGIES_WITH_MIN_CHARS
        and candidate.rag_chunk_min_chars >= candidate.rag_chunk_size
    ):
        raise HTTPException(
            status_code=422, detail="min_chars は chunk_size より小さくしてください。"
        )
    return candidate


@router.get("/{document_id}/chunk-sets", response_model=ApiResponse[list[DocumentChunkSet]])
async def list_document_chunk_sets(document_id: str) -> ApiResponse[list[DocumentChunkSet]]:
    """文書の chunk_set(variant)一覧を返す。KB 詳細での variant 可視化に使う。"""
    oracle = OracleClient()
    # 状態・ハッシュだけを使う。抽出結果などの JSON 列は読まない(#341)。
    detail = await oracle.get_document_summary(document_id)
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


async def _materialize_experiment_candidate(
    oracle: OracleClient,
    job: IngestionJob,
    *,
    cancel_checker: Callable[[], Awaitable[bool]] | None = None,
) -> IngestionJobPhase | None:
    """文書レシピの job で、設定 snapshot から新しい chunk_set を隔離構築する。

    既存 active 出力を構築中に変更しない。成功時だけ active を原子的に差し替える。
    ``recipe_id`` の無い job（移行前の旧実験 API の job）は実行せず、
    利用者向けのエラーで止める（#486）。

    戻り値は「現在ジョブ完了後に自動投入すべき次フェーズ」。自動進行不要なら ``None``。
    現在ジョブが RUNNING のまま新ジョブを作るとレシピ行ロックのガードで弾かれるため、
    投入自体は呼び出し側(``_run_ingestion_job``)が現在ジョブ SUCCEEDED 後に行う。
    """
    recipe_id = job.recipe_id
    if recipe_id is None:
        raise IngestionUserError(
            "旧いレシピ実験の job は実行できません。文書の処理レシピから再実行してください。"
        )
    detail = await oracle.get_document(job.document_id)
    if detail is None:
        raise IngestionUserError("ドキュメントが見つかりません。")
    if not detail.content_sha256:
        raise IngestionUserError("文書のソースハッシュが未確定です。")
    raw_config = (job.settings_overrides or {}).get("processing_config")
    if isinstance(raw_config, Mapping):
        candidate_config = DocumentProcessingConfig.model_validate(raw_config)
    else:
        _base_settings, candidate_config = await _resolve_ingestion_settings(
            oracle, job.document_id
        )
    candidate_settings, effective_candidate_config = _merge_document_processing_config(
        candidate_config
    )
    base_chunk_set_id = compute_chunk_set_id(detail.content_sha256, candidate_settings)
    candidate_chunk_set_id = hashlib.sha256(
        f"{base_chunk_set_id}:{recipe_id}:{job.recipe_revision}:{job.id}".encode()
    ).hexdigest()
    await _raise_if_job_cancelled(cancel_checker)
    await oracle.update_document_recipe_status(
        recipe_id=recipe_id,
        status=_PHASE_TO_RUNNING_STATUS[job.phase],
    )
    pipeline = IngestionPipeline(
        oracle=oracle,
        settings=candidate_settings,
        recipe_id=recipe_id,
        recipe_revision=job.recipe_revision,
    )
    extraction_recipe_id = compute_extraction_recipe_id(detail.content_sha256, candidate_settings)
    if job.phase in {
        IngestionJobPhase.CHUNK,
        IngestionJobPhase.INDEX,
    }:
        recipe_row = await oracle.get_document_recipe(job.document_id, recipe_id)
        active_extraction_recipe_id = (
            recipe_row.get("active_extraction_recipe_id") if recipe_row is not None else None
        )
        if active_extraction_recipe_id is None:
            raise IngestionUserError("索引対象の抽出結果が見つかりません。")
        extraction_recipe_id = str(active_extraction_recipe_id)

    if job.phase == IngestionJobPhase.INDEX:
        pending = await oracle.get_latest_recipe_chunk_set(
            recipe_id,
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
    elif job.phase == IngestionJobPhase.CHUNK:
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
            recipe_id=recipe_id,
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
        if job.phase == IngestionJobPhase.EXTRACT:
            recipe_row = await oracle.get_document_recipe(job.document_id, recipe_id)
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
        recipe_row = await oracle.get_document_recipe(job.document_id, recipe_id)
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
        recipe_id=recipe_id,
        extraction_recipe_id=extraction_recipe_id,
        recipe_subset=_processing_recipe_snapshot(candidate_config, effective_candidate_config),
    )
    await oracle.mark_chunk_set_indexed(
        chunk_set_id=candidate_chunk_set_id, chunk_count=chunk_count, vector_count=chunk_count
    )
    # active の切り替えは検索対象を変えるため、取り消された job では行わない(#305)。
    await _raise_if_job_cancelled(cancel_checker)
    await oracle.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=candidate_chunk_set_id,
        extraction_recipe_id=extraction_recipe_id,
        materialized_revision=job.recipe_revision,
    )
    # 文書一覧の legacy 集約状態。少なくとも1レシピが検索可能なら INDEXED とする。
    await oracle.update_document_status(job.document_id, FileStatus.INDEXED)
    # INDEX まで到達した経路は自動進行の追加投入不要(CHUNK→INDEX はここで完結)。
    return None


async def _materialization_plan_for_document(
    oracle: OracleClient,
    detail: DocumentSummary,
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


def _processing_config_drift_groups(
    observed: Mapping[str, object],
    effective: Mapping[str, object],
) -> list[str]:
    """配信中レシピの snapshot と現在の有効設定を比べ、出力が変わる設定群を返す。

    DocRAG 親子階層の分割パラメータは、その方式を使うときだけ比べる。追加前の snapshot は
    値を持たないため、そのときは rag_poc の既定値(Settings の既定)で分割したものとして扱う。
    """
    docrag_in_use = effective.get("chunking_strategy") == DOCRAG_CHUNKING_STRATEGY

    def _observed_value(field: str) -> object:
        value = observed.get(field)
        if value is None and field in DOCRAG_PROCESSING_CONFIG_FIELDS:
            return Settings.model_fields[f"rag_{field}"].default
        return value

    def _compared_fields(fields: tuple[str, ...]) -> tuple[str, ...]:
        if docrag_in_use:
            return fields
        return tuple(field for field in fields if field not in DOCRAG_PROCESSING_CONFIG_FIELDS)

    return [
        group
        for group, fields in DOCUMENT_PROCESSING_OUTPUT_GROUPS.items()
        if any(_observed_value(field) != effective.get(field) for field in _compared_fields(fields))
    ]


def _snapshot_used_removed_chunking_strategy(row: Mapping[str, object] | None) -> bool:
    """chunk_set の snapshot が削除した分割方式(親子階層など)で作られたかを返す。"""
    raw = row.get("recipe_subset") if row else None
    effective = raw.get("effective_processing_config") if isinstance(raw, Mapping) else None
    strategy = effective.get("chunking_strategy") if isinstance(effective, Mapping) else None
    return (
        isinstance(strategy, str)
        and strategy.strip().casefold() in LEGACY_CHUNKING_STRATEGY_ALIASES
    )


@router.get(
    "/{document_id}/ingestion-segments",
    response_model=ApiResponse[list[IngestionSegment]],
)
async def list_document_ingestion_segments(
    document_id: str,
) -> ApiResponse[list[IngestionSegment]]:
    """文書 preview workspace 用に取込 segment/checkpoint 状態を返す。"""
    oracle = OracleClient()
    # 取込中にポーリングされるため、保存済みの segment があれば JSON 列を読まない(#341)。
    summary = await oracle.get_document_summary(document_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    try:
        persisted_segments = await oracle.list_ingestion_segments(document_id)
    except Exception:
        persisted_segments = []
    if persisted_segments:
        return ApiResponse(
            data=[
                _segment_with_progress_defaults(segment, summary) for segment in persisted_segments
            ]
        )
    # 保存済みの segment が無い旧データだけ、抽出結果のページ範囲から segment を組み立てる。
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    effective_settings, _owning = await _resolve_ingestion_settings(oracle, document_id)
    jobs = await oracle.list_document_ingestion_jobs(document_id)
    return ApiResponse(data=_document_ingestion_segments(detail, jobs, effective_settings))


def _segment_with_progress_defaults(
    segment: IngestionSegment,
    detail: DocumentSummary,
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
    # 表示用の摘要だけを使うため、重複元の JSON 列は読まない(#341)。
    duplicate = await oracle.get_document_summary(duplicate_id)
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
    # 参照が残っていない過去の取込の成果物も、文書の prefix ごと消す（#303）。
    # 失敗しても文書の削除は取り消さず、ログと warning で知らせる。
    for artifact_prefix in document_artifact_prefixes(get_settings(), document_id):
        try:
            prefix_result = await storage.delete_prefix(artifact_prefix)
        except Exception as exc:  # noqa: BLE001 - 後始末の失敗で削除の結果を変えない
            artifact_delete_failed_count += 1
            logger.warning(
                "document_artifact_prefix_delete_failed",
                extra={"document_id": document_id, "error_type": type(exc).__name__},
            )
            continue
        artifact_deleted_count += prefix_result.deleted
        if prefix_result.failed:
            artifact_delete_failed_count += prefix_result.failed
            logger.warning(
                "document_artifact_prefix_delete_partial",
                extra={"document_id": document_id, "failed_count": prefix_result.failed},
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
    if not await oracle.document_exists(document_id):
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


@router.put("/{document_id}/classification", response_model=ApiResponse[DocumentDetail])
async def save_document_classification(
    document_id: str,
    body: DocumentClassification,
) -> ApiResponse[DocumentDetail]:
    """文書の分類と有効期間を保存する。検索の分類フィルタと基準日の絞り込みに使う。"""
    try:
        detail = await OracleClient().save_document_classification(document_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。") from exc
    return ApiResponse(data=detail)


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


async def _raise_if_job_cancelled(
    cancel_checker: Callable[[], Awaitable[bool]] | None,
) -> None:
    """文書・レシピの status や出力を書き換える前に、job が取り消されていないかを確かめる。"""
    if cancel_checker is not None and await cancel_checker():
        raise IngestionCancelledError(INGESTION_JOB_CANCELLED_MESSAGE)


def _restore_status_for_cancelled_phase(phase: IngestionJobPhase) -> FileStatus:
    if phase == IngestionJobPhase.INDEX:
        return FileStatus.CHUNKED
    if phase == IngestionJobPhase.CHUNK:
        return FileStatus.REVIEW
    return FileStatus.UPLOADED


def _restore_recipe_status_for_cancelled_phase(phase: IngestionJobPhase) -> FileStatus:
    """レシピの job を取り消した後の、レシピ行の status(工程を始める前の状態)。"""
    if phase == IngestionJobPhase.EXTRACT:
        # EXTRACT はファイル準備の成果物(処理後ファイル)がある状態から始まる。
        return FileStatus.PREPROCESSED
    return _restore_status_for_cancelled_phase(phase)


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
    # 投入直後の job は worker が拾うまで QUEUED のまま文書は UPLOADED 等に見えるため、
    # 状態だけでは二重投入を止められない。出力の初期化より前に、同じ(既定)レシピの
    # 待機中・実行中 job を確かめる(#281)。
    await _raise_if_default_recipe_job_active(oracle, document_id)

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
    # 既定レシピの解析エンジン(既定は Docling)で扱えない形式は、出力を初期化する前に止める(#286)。
    await _raise_if_parser_source_blocked(
        await _default_recipe_settings(oracle, document_id), source_profile, phase
    )
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


async def _raise_if_default_recipe_job_active(oracle: OracleClient, document_id: str) -> None:
    """文書単位の投入先(既定レシピ)に待機中・実行中の job があれば 409 にする。

    Oracle の job 作成も同じレシピの QUEUED / RUNNING を拒否するが、それより前に行う出力の初期化
    (`_reset_document_outputs_for_extract`)で待機中 job の前提を壊さないよう、ここで先に止める。
    """
    default_recipe_id: str | None = None
    ensure_recipe = getattr(oracle, "ensure_default_document_recipe", None)
    if callable(ensure_recipe):
        recipe = await ensure_recipe(document_id)
        default_recipe_id = str(recipe["recipe_id"])
    jobs = await oracle.list_document_ingestion_jobs(document_id)
    if any(
        job.status in {IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING}
        and job.recipe_id in {None, default_recipe_id}
        for job in jobs
    ):
        raise HTTPException(
            status_code=409,
            detail="このドキュメントは取込待ちまたは取込中です。完了してから再実行してください。",
        )


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
    try:
        return await oracle.create_ingestion_job(job)
    except ValueError as exc:
        # 同じレシピの待機中・実行中 job がある / レシピ設定が更新された、は競合(409)として返す。
        # 以前は文書単位の投入経路で 500 になっていた(#281)。
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    # レシピ行の `preprocess_artifact`（レシピごとのファイル準備の成果物。#303）。
    try:
        recipes = await oracle.list_document_recipes(detail.id)
    except Exception:
        recipes = []
    for recipe in recipes:
        artifact = recipe.get("preprocess_artifact")
        if isinstance(artifact, Mapping):
            recipe_artifact_path = artifact.get("object_storage_path")
            if isinstance(recipe_artifact_path, str):
                paths.append(recipe_artifact_path)
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
    lease_owner: str | None = None,
) -> None:
    """キュー投入済み取込 job を実行する。

    job の完了・失敗は RUNNING のときだけ書く(`transition_ingestion_job`)。cancel を検知したら
    (pipeline の途中でも、完了・失敗を書く直前でも)、文書・レシピの status を取り消し後の
    状態へ戻し、次工程は投入しない(#305)。
    ``lease_owner`` は job を実行する取込 worker の識別子。claim で lease を取り、worker が
    heartbeat を打つ(#357)。lease を持つ実行は、完了・失敗・再キュー・取り消しの後始末を
    自分の lease のときだけ行う。heartbeat が途絶えて別の worker が claim し直した後の古い実行の
    結果は捨て、新しい実行の状態・文書・レシピを変えない。工程の途中の取り消しの確認でも lease を
    確かめ、lease を失った実行はそこで止める(#359)。
    """
    oracle = OracleClient()
    job = await oracle.claim_ingestion_job(
        job_id, started_at=datetime.now(UTC), lease_owner=lease_owner
    )
    if job is None:
        return

    async def is_cancelled() -> bool:
        if lease_owner is not None:
            lease = await oracle.get_ingestion_job_lease(job_id)
            # lease を失った実行も取り消しと同じく止め、後始末で結果を捨てる(#359)。
            return lease is None or (
                not _holds_ingestion_lease(lease, lease_owner)
                or lease.status == IngestionJobStatus.CANCELLED
            )
        current = await oracle.get_ingestion_job(job_id)
        return current is not None and current.status == IngestionJobStatus.CANCELLED

    # レシピ経路で「現在ジョブ完了後に自動投入すべき次フェーズ」を受け取る。投入は現在ジョブが
    # SUCCEEDED になった後(レシピ行ロックのガードを通過できる状態)に行う。
    next_recipe_phase: IngestionJobPhase | None = None
    # 文書単位の job の実行結果。自動進行(次工程の投入)は SUCCEEDED を書けた後に判定する。
    finished_detail: DocumentDetail | None = None
    try:
        if job.recipe_id is not None:
            await _raise_if_job_cancelled(is_cancelled)
            await oracle.update_document_recipe_status(
                recipe_id=job.recipe_id,
                status=_PHASE_TO_RUNNING_STATUS[job.phase],
            )
        if job.recipe_id is not None or job.settings_overrides is not None:
            # 正式レシピは旧 active を維持した隔離 materialize。recipe_id の無い旧実験の job は
            # 利用者向けのエラーで止める（#486）。
            next_recipe_phase = await _materialize_experiment_candidate(
                oracle, job, cancel_checker=is_cancelled
            )
        elif job.phase == IngestionJobPhase.CHUNK:
            finished_detail = await _chunk_reviewed_document(
                job.document_id,
                cancel_checker=is_cancelled,
            )
        elif job.phase == IngestionJobPhase.INDEX:
            finished_detail = await _index_reviewed_document(
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
            await _raise_if_job_cancelled(is_cancelled)
            await _reset_document_outputs_for_extract(oracle, job.document_id)
            finished_detail = await _ingest_existing_document(
                job.document_id,
                force=True,
                use_prepared_artifact=True,
                cancel_checker=is_cancelled,
            )
        else:
            await _raise_if_job_cancelled(is_cancelled)
            await _reset_document_outputs_for_extract(
                oracle,
                job.document_id,
                clear_preprocess_artifact=True,
            )
            finished_detail = await _ingest_existing_document(
                job.document_id,
                force=True,
                use_prepared_artifact=False,
                cancel_checker=is_cancelled,
            )
    except HTTPException as exc:
        await _fail_ingestion_job(oracle, job, str(exc.detail), lease_owner=lease_owner)
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
        await _restore_statuses_after_cancel(oracle, job, lease_owner=lease_owner)
        logger.info(
            "ingestion_job_cancelled",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except IngestionTimeoutError as exc:
        await _fail_ingestion_job(oracle, job, str(exc), lease_owner=lease_owner)
        logger.info(
            "ingestion_job_timeout",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except IngestionUserError as exc:
        await _fail_ingestion_job(oracle, job, str(exc), lease_owner=lease_owner)
        logger.info(
            "ingestion_job_validation_error",
            extra={"job_id": job_id, "document_id": job.document_id},
        )
        if propagate_errors:
            raise
    except Exception as exc:
        if (
            is_transient_oracle_error(exc)
            and job.attempt_count < job.max_attempts
            and await _requeue_ingestion_job_after_transient_error(
                oracle, job, exc, lease_owner=lease_owner
            )
        ):
            if propagate_errors:
                raise
            return
        safe_error = _safe_ingestion_job_error_message(exc)
        await _fail_ingestion_job(oracle, job, safe_error, lease_owner=lease_owner)
        logger.exception(
            "ingestion_job_failed",
            extra={
                "job_id": job_id,
                "document_id": job.document_id,
                "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
                **oracle_error_log_fields(exc),
            },
        )
        if propagate_errors:
            raise
    else:
        succeeded = await _finish_ingestion_job_unless_cancelled(
            oracle,
            job_id,
            status=IngestionJobStatus.SUCCEEDED,
            lease_owner=lease_owner,
        )
        if succeeded is None:
            # SUCCEEDED を書く直前に取り消された。取り消しを検知した側として status を戻し、
            # 次工程は投入しない。lease を失っていたら(別の worker が claim し直した)何も戻さない。
            await _restore_statuses_after_cancel(
                oracle,
                job,
                lease_owner=lease_owner,
                discarded_status=IngestionJobStatus.SUCCEEDED,
            )
            return
        if finished_detail is not None:
            await _enqueue_auto_advance_job(job, finished_detail)
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


async def _requeue_ingestion_job_after_transient_error(
    oracle: OracleClient,
    job: IngestionJob,
    error: Exception,
    *,
    lease_owner: str | None = None,
) -> bool:
    """一時的な DB エラー(接続断)の job を QUEUED に戻す。戻せたら True(#341)。

    試行回数(``attempt_count < max_attempts``)が残っている場合だけ呼ぶ。文書・レシピの status を
    工程を始める前の状態へ戻してから、job を RUNNING のときだけ QUEUED に戻す(条件付きの状態遷移)。
    先に job を戻すと、別の worker が claim して書いた status を後から上書きしうるため、
    この順にする。
    戻す途中でまた DB が失敗したら False を返し、呼び出し側は今までどおり FAILED にする
    (FAILED も書けなければ、stale の回復が拾う)。
    lease を持つ実行は、lease を失っていたら(別の worker が claim し直した)文書・レシピを戻さず、
    job も戻さずに True を返す(結果を捨てる。#359)。
    """
    try:
        if await _discard_if_ingestion_lease_lost(
            oracle, job, lease_owner=lease_owner, discarded_status=IngestionJobStatus.QUEUED
        ):
            return True
        await _restore_statuses_before_job_phase(oracle, job)
        requeued = await oracle.transition_ingestion_job(
            job.id,
            from_statuses=(IngestionJobStatus.RUNNING,),
            to_status=IngestionJobStatus.QUEUED,
            error_message=None,
            finished_at=None,
            lease_owner=lease_owner,
        )
    except Exception as requeue_error:
        logger.warning(
            "ingestion_job_requeue_failed",
            extra={
                "job_id": job.id,
                "document_id": job.document_id,
                **oracle_error_log_fields(requeue_error),
            },
        )
        return False
    if requeued is None:
        # 戻す前に取り消された(または stale の回復が戻した)。取り消しとして扱う。
        await _restore_statuses_after_cancel(
            oracle, job, lease_owner=lease_owner, discarded_status=IngestionJobStatus.QUEUED
        )
        return True
    logger.warning(
        "ingestion_job_requeued_transient_db_error",
        extra={
            "job_id": job.id,
            "document_id": job.document_id,
            "recipe_id": job.recipe_id,
            "phase": job.phase.value,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            **oracle_error_log_fields(error),
        },
    )
    _dispatch_ingestion_job(job.id)
    return True


async def _restore_statuses_before_job_phase(
    oracle: OracleClient,
    job: IngestionJob,
) -> None:
    """再実行の前に、文書(文書単位の job)またはレシピ行(レシピの job)を工程の前の状態へ戻す。"""
    if job.recipe_id is not None:
        await _restore_recipe_status_after_cancel(oracle, job)
        return
    await oracle.update_document_status(
        job.document_id, _restore_status_for_cancelled_phase(job.phase)
    )


async def _fail_ingestion_job(
    oracle: OracleClient,
    job: IngestionJob,
    error_message: str,
    *,
    lease_owner: str | None = None,
) -> bool:
    """RUNNING の job を FAILED にし、レシピの job はレシピ行だけを ERROR にする。

    文書単位の job の文書は pipeline(または異常終了を検知した worker)が ERROR にする。
    FAILED を書く前に取り消されていたら、取り消しとして status を戻す。FAILED を書けたら True。
    ``lease_owner`` を渡すと自分の lease のときだけ書き、lease を失っていたら何も変えない(#359)。
    """
    failed = await _finish_ingestion_job_unless_cancelled(
        oracle,
        job.id,
        status=IngestionJobStatus.FAILED,
        error_message=error_message,
        lease_owner=lease_owner,
    )
    if failed is None:
        await _restore_statuses_after_cancel(
            oracle, job, lease_owner=lease_owner, discarded_status=IngestionJobStatus.FAILED
        )
        return False
    await _mark_recipe_job_failed(oracle, job, error_message)
    return True


async def _restore_statuses_after_cancel(
    oracle: OracleClient,
    job: IngestionJob,
    *,
    lease_owner: str | None = None,
    discarded_status: IngestionJobStatus = IngestionJobStatus.CANCELLED,
) -> None:
    """取り消しを検知した側(worker)が、job の対象の status を取り消し後の状態へ戻す。

    - レシピの job(``recipe_id`` あり): レシピ行だけを戻す。文書(全レシピの集約)には触れない。
    - 文書単位の job: 文書の status を工程に応じて戻す。

    job が CANCELLED でない(stale の回復で QUEUED に戻された等)場合と、同じ文書の別の job が
    RUNNING の場合(その job が status を書いている)は戻さない。
    lease を持つ実行(``lease_owner``)は、lease を失っていたら(別の worker が claim し直した)
    戻さず、書けなかった結果(``discarded_status``)を捨てたことをログに残す(#359)。
    取り消しの後始末は、取り消された実行(lease の持ち主)が行う。
    """
    if await _discard_if_ingestion_lease_lost(
        oracle, job, lease_owner=lease_owner, discarded_status=discarded_status
    ):
        return
    current = await oracle.get_ingestion_job(job.id)
    if current is None or current.status != IngestionJobStatus.CANCELLED:
        logger.info(
            "ingestion_job_cancel_restore_skipped",
            extra={
                "job_id": job.id,
                "status": current.status.value if current is not None else None,
            },
        )
        return
    running_jobs = await oracle.list_document_ingestion_jobs(
        job.document_id, status=IngestionJobStatus.RUNNING
    )
    if any(other.id != job.id for other in running_jobs):
        logger.info(
            "ingestion_job_cancel_restore_skipped_other_running",
            extra={"job_id": job.id, "document_id": job.document_id},
        )
        return
    if job.recipe_id is not None:
        await _restore_recipe_status_after_cancel(oracle, job)
        return
    await oracle.update_document_status(
        job.document_id, _restore_status_for_cancelled_phase(job.phase)
    )


def _holds_ingestion_lease(lease: IngestionJobLease | None, lease_owner: str) -> bool:
    """job がまだ ``lease_owner`` の実行(RUNNING か、実行中に取り消された)なら True(#359)。"""
    return (
        lease is not None
        and lease.lease_owner == lease_owner
        and lease.status in _LEASE_HELD_INGESTION_JOB_STATUSES
    )


async def _discard_if_ingestion_lease_lost(
    oracle: OracleClient,
    job: IngestionJob,
    *,
    lease_owner: str | None,
    discarded_status: IngestionJobStatus,
) -> bool:
    """lease を持つ実行が lease を失っていたら、結果を捨てたことをログに残して True を返す(#359)。

    heartbeat が TTL を超えて途絶えると、別の worker の stale の回復が job を QUEUED(lease を外す)
    か FAILED に戻し、別の worker が claim し直す(lease の持ち主が変わる)。その後の古い実行の
    完了・失敗・再キュー・取り消しの後始末は、新しい実行の状態・文書・レシピを上書きしうるため
    捨てる。lease を持たない実行(``lease_owner`` なし)は従来どおり False を返す。
    """
    if lease_owner is None:
        return False
    lease = await oracle.get_ingestion_job_lease(job.id)
    if _holds_ingestion_lease(lease, lease_owner):
        return False
    logger.warning(
        "ingestion_job_stale_result_discarded",
        extra={
            "job_id": job.id,
            "document_id": job.document_id,
            "recipe_id": job.recipe_id,
            "phase": job.phase.value,
            "lease_owner": lease_owner,
            "discarded_status": discarded_status.value,
            "current_status": lease.status.value if lease is not None else None,
            "current_lease_owner": lease.lease_owner if lease is not None else None,
        },
    )
    return True


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
    """取消後は旧 active があれば検索対象、無ければ job の工程を始める前の状態へ戻す。

    工程の前の状態は stale の回復(`_restore_recipe_status_for_job_phase`)と同じ対応にする。
    以前は一律に未処理へ戻していたため、Chunk 作成の取消で抽出の確認済み(REVIEW)が失われていた。
    """
    if job.recipe_id is None:
        return
    row = await oracle.get_document_recipe(job.document_id, job.recipe_id)
    await oracle.update_document_recipe_status(
        recipe_id=job.recipe_id,
        status=(
            FileStatus.INDEXED
            if row is not None and row.get("active_chunk_set_id") is not None
            else _restore_recipe_status_for_cancelled_phase(job.phase)
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
    lease_owner: str | None = None,
) -> IngestionJob | None:
    """RUNNING の job だけを完了・失敗にする。取り消し済みなどで書けなければ None を返す。

    状態の確認と書き込みは 1 回の条件付き UPDATE で行い、確認と書き込みの間に cancel API が
    割り込んでも CANCELLED を上書きしない(#305)。``lease_owner`` を渡すと自分の lease の行だけを
    書き、別の worker が claim し直した job を上書きしない(#359)。
    """
    finished = await oracle.transition_ingestion_job(
        job_id,
        from_statuses=(IngestionJobStatus.RUNNING,),
        to_status=status,
        error_message=error_message,
        finished_at=datetime.now(UTC),
        lease_owner=lease_owner,
    )
    if finished is None:
        logger.info(
            "ingestion_job_finish_skipped_not_running",
            extra={"job_id": job_id, "final_status": status.value},
        )
    return finished


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


@router.get("/{document_id}/preview-pages", response_model=ApiResponse[DocumentPreviewPages])
async def document_preview_pages(
    document_id: str,
    variant: Annotated[Literal["original", "prepared"], Query()] = "original",
) -> ApiResponse[DocumentPreviewPages]:
    """原本 / 処理後ファイルのページ一覧(PDF をページ画像で表示し、bbox の強調を重ねる。#349)。"""
    detail = await OracleClient().get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return await _document_preview_pages_response(
        detail, variant=variant, preprocess_artifact=detail.preprocess_artifact
    )


@router.get("/{document_id}/preview-pages/{page_number}")
async def document_preview_page_image(
    document_id: str,
    page_number: Annotated[int, Path(ge=1, le=10000)],
    variant: Annotated[Literal["original", "prepared"], Query()] = "original",
    dpi: Annotated[int, Query(ge=48, le=288)] = 144,
) -> Response:
    """原本 / 処理後ファイルの 1 ページを PNG で返す(ページの /Rotate を反映した向き)。"""
    detail = await OracleClient().get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return await _document_preview_page_image_response(
        detail,
        page_number=page_number,
        variant=variant,
        dpi=dpi,
        preprocess_artifact=detail.preprocess_artifact,
    )


async def _document_preview_pages_response(
    detail: DocumentDetail,
    *,
    variant: Literal["original", "prepared"],
    preprocess_artifact: DocumentPreprocessArtifact | None,
) -> ApiResponse[DocumentPreviewPages]:
    data, _file_name, _content_type = await _load_document_content(
        detail, variant=variant, preprocess_artifact=preprocess_artifact
    )
    try:
        sizes = await asyncio.to_thread(page_sizes, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(
        data=DocumentPreviewPages(
            page_count=len(sizes),
            pages=[
                DocumentPreviewPage(page_number=index + 1, width=width, height=height)
                for index, (width, height) in enumerate(sizes)
            ],
        )
    )


async def _document_preview_page_image_response(
    detail: DocumentDetail,
    *,
    page_number: int,
    variant: Literal["original", "prepared"],
    dpi: int,
    preprocess_artifact: DocumentPreprocessArtifact | None,
) -> Response:
    data, _file_name, _content_type = await _load_document_content(
        detail, variant=variant, preprocess_artifact=preprocess_artifact
    )
    try:
        png = await asyncio.to_thread(render_page_png, data, page_number, dpi)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{document_id}/crop")
async def document_crop(
    document_id: str,
    page: Annotated[int, Query(ge=1, le=10000)],
    x0: Annotated[float, Query(ge=0)],
    y0: Annotated[float, Query(ge=0)],
    x1: Annotated[float, Query(gt=0)],
    y1: Annotated[float, Query(gt=0)],
    page_width: Annotated[float, Query(gt=0)],
    page_height: Annotated[float, Query(gt=0)],
    dpi: Annotated[int, Query(ge=36, le=300)] = 150,
) -> Response:
    """解析に使ったファイルから bbox の領域を PNG で切り出す(解析結果プレビュー用)。

    bbox は解析結果のページ画像 px 座標(page_width / page_height 基準)。ファイル準備後の
    artifact があればそれ(解析対象)を、無ければ原本を開く。画像ファイルは 1 ページとして扱う。
    """
    if x1 <= x0 or y1 <= y0 or x1 > page_width * 1.001 or y1 > page_height * 1.001:
        raise HTTPException(status_code=422, detail="切り出し範囲が不正です。")
    try:
        data = await load_parsed_source(OracleClient(), document_id)
    except DocumentSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="原本ファイルの参照パスが不正です。") from exc
    try:
        png = await asyncio.to_thread(
            crop_png, data, page, (x0, y0, x1, y1), (page_width, page_height), dpi
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


async def _document_content_response(
    detail: DocumentDetail,
    *,
    variant: Literal["original", "prepared"],
    disposition: Literal["inline", "attachment"],
    preprocess_artifact: DocumentPreprocessArtifact | None,
) -> Response:
    data, file_name, content_type = await _load_document_content(
        detail, variant=variant, preprocess_artifact=preprocess_artifact
    )
    media_type = _content_type_header(content_type, data)
    headers = {
        # 非 ASCII ファイル名は RFC 5987 でエンコードする
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file_name)}",
        # MIME sniffing による取り違えを防ぐ
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=60",
    }
    if media_type.split(";", 1)[0].strip().lower() in SCRIPTABLE_CONTENT_TYPES:
        # 利用者がアップロードした HTML / SVG を同じ origin で開いてもスクリプトを動かさない(#281)。
        # 画面のプレビューは本文をテキストとして取得するので、表示には影響しない。
        headers["Content-Security-Policy"] = "sandbox"
    return Response(content=data, media_type=media_type, headers=headers)


async def _load_document_content(
    detail: DocumentDetail,
    *,
    variant: Literal["original", "prepared"],
    preprocess_artifact: DocumentPreprocessArtifact | None,
) -> tuple[bytes, str, str]:
    """原本またはファイル準備後 artifact の (中身, ファイル名, content type) を返す。"""
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
    return data, file_name, content_type


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
    """表示・保存用のファイル名を安全な basename にする。

    長すぎる名前は拡張子を残したまま、文字数（255）と UTF-8 のバイト数（512。
    `rag_documents.file_name` の列長）の両方に収まるよう末尾側を切り詰める。
    """
    name = PurePath((file_name or "document.bin").replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name).strip(" .")
    if not name:
        return "document.bin"
    return _truncate_file_name(name)


def _truncate_file_name(name: str) -> str:
    """拡張子を残して、ファイル名を文字数とバイト数の上限に収める。"""
    if (
        len(name) <= MAX_UPLOAD_FILE_NAME_CHARS
        and len(name.encode("utf-8")) <= MAX_UPLOAD_FILE_NAME_BYTES
    ):
        return name
    suffix = PurePath(name).suffix
    if not suffix or len(suffix) > _MAX_PRESERVED_SUFFIX_CHARS:
        suffix = ""
    stem = name[: len(name) - len(suffix)] if suffix else name
    max_stem_chars = MAX_UPLOAD_FILE_NAME_CHARS - len(suffix)
    max_stem_bytes = MAX_UPLOAD_FILE_NAME_BYTES - len(suffix.encode("utf-8"))
    stem = stem[:max_stem_chars]
    while stem and len(stem.encode("utf-8")) > max_stem_bytes:
        stem = stem[:-1]
    stem = stem.rstrip(" .")
    return f"{stem}{suffix}" if stem else f"document{suffix}"


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
