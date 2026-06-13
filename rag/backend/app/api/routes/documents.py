"""ドキュメント（伝票）API。アップロード・一覧・分析・登録。"""

import hashlib
import mimetypes
import re
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile

from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.ingestion import IngestionPipeline, IngestionUserError
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse, Page
from app.schemas.document import (
    DocumentDetail,
    DocumentStats,
    DocumentSummary,
    ExtractedFieldsUpdate,
    FileStatus,
    UploadResult,
)
from app.schemas.extraction import StructuredExtraction

router = APIRouter()
SOURCE_SIZE_MISMATCH_MESSAGE = "原本ファイルのサイズがアップロード時と一致しません。"
SOURCE_HASH_MISMATCH_MESSAGE = "原本ファイルの SHA-256 がアップロード時と一致しません。"
MISSING_INDEX_CHUNKS_MESSAGE = "索引済みチャンクがないため本登録できません。再分析してください。"


@router.post("/upload", response_model=ApiResponse[UploadResult])
async def upload_document(
    http_request: Request,
    file: Annotated[UploadFile, File(...)],
) -> ApiResponse[UploadResult]:
    """伝票ファイルをアップロードし、Object Storage へ保管する。"""
    enforce_rate_limit("upload", http_request)
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
    file_name = _safe_display_filename(file.filename)
    content_sha256 = _sha256_hex(data)
    duplicate = await oracle.find_document_by_content_hash(content_sha256)
    key = f"uploaded/{uuid4().hex}/{file_name}"
    object_path = await storage.put(
        key=key,
        data=data,
        content_type=content_type,
    )
    detail = await oracle.create_document(
        file_name=file_name,
        object_storage_path=object_path,
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256=content_sha256,
        duplicate_of_document_id=duplicate.id if duplicate is not None else None,
    )
    return ApiResponse(
        data=UploadResult(
            id=detail.id,
            file_name=detail.file_name,
            status=detail.status,
            file_size_bytes=detail.file_size_bytes or len(data),
            content_sha256=content_sha256,
            duplicate_of_document_id=detail.duplicate_of_document_id,
        )
    )


@router.get("", response_model=ApiResponse[Page[DocumentSummary]])
async def list_documents(
    status: FileStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[DocumentSummary]]:
    """登録対象ドキュメントの一覧を返す。"""
    oracle = OracleClient()
    documents = await oracle.list_documents(status=status, query=q, limit=limit, offset=offset)
    total = await oracle.count_documents(status=status, query=q)
    return ApiResponse(
        data=Page(
            items=documents,
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )
    )


@router.get("/stats", response_model=ApiResponse[DocumentStats])
async def document_stats() -> ApiResponse[DocumentStats]:
    """ドキュメント状態別の集計を返す。"""
    return ApiResponse(data=await OracleClient().document_stats())


@router.get("/{document_id}", response_model=ApiResponse[DocumentDetail])
async def get_document(document_id: str) -> ApiResponse[DocumentDetail]:
    """ドキュメント詳細（VLM 抽出結果含む）を返す。"""
    detail = await OracleClient().get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    return ApiResponse(data=detail)


@router.post("/{document_id}/analyze", response_model=ApiResponse[DocumentDetail])
async def analyze_document(
    http_request: Request,
    document_id: str,
    force: bool = Query(default=False),
) -> ApiResponse[DocumentDetail]:
    """OCI Enterprise AI の VLM で OCR・構造化抽出を行う。"""
    enforce_rate_limit("analyze", http_request)
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None or detail.object_storage_path is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status == FileStatus.ANALYZING:
        raise HTTPException(status_code=409, detail="このドキュメントは現在分析中です。")
    if detail.status == FileStatus.REGISTERED and force:
        raise HTTPException(status_code=409, detail="本登録済みドキュメントは再分析できません。")
    if detail.status in (FileStatus.ANALYZED, FileStatus.REGISTERED) and not force:
        return ApiResponse(data=detail)
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

    pipeline = IngestionPipeline(oracle=oracle)
    try:
        analyzed = await pipeline.ingest(
            document_id=document_id,
            image_bytes=data,
            prompt="請求書・伝票の OCR と構造化抽出を日本語で行ってください。",
        )
    except IngestionUserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=analyzed)


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

    media_type, _ = mimetypes.guess_type(detail.file_name)
    return Response(
        content=data,
        media_type=media_type or "application/octet-stream",
        headers={
            # 非 ASCII ファイル名は RFC 5987 でエンコードして inline 表示する
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(detail.file_name)}",
            # MIME sniffing による取り違えを防ぐ
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.patch("/{document_id}/fields", response_model=ApiResponse[DocumentDetail])
async def update_document_fields(
    document_id: str,
    payload: ExtractedFieldsUpdate,
) -> ApiResponse[DocumentDetail]:
    """分析済みドキュメントの抽出フィールドを修正して保存する。

    検索索引（chunk/vector）は更新しない。再索引が必要なら再分析を行う。
    """
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status != FileStatus.ANALYZED:
        raise HTTPException(status_code=409, detail="分析済みのドキュメントのみ編集できます。")

    base = (
        StructuredExtraction.model_validate(detail.extracted_fields)
        if detail.extracted_fields
        else StructuredExtraction()
    )
    update: dict[str, object] = {"fields": payload.fields}
    if payload.raw_text is not None:
        update["raw_text"] = payload.raw_text
    extraction = base.model_copy(update=update)
    updated = await oracle.save_extraction(document_id, extraction)
    return ApiResponse(data=updated)


@router.post("/{document_id}/register", response_model=ApiResponse[DocumentDetail])
async def register_document(document_id: str) -> ApiResponse[DocumentDetail]:
    """抽出結果を確定し本登録する。"""
    oracle = OracleClient()
    detail = await oracle.get_document(document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ドキュメントが見つかりません。")
    if detail.status not in (FileStatus.ANALYZED, FileStatus.REGISTERED):
        raise HTTPException(status_code=409, detail="分析済みのドキュメントのみ登録できます。")
    if await oracle.count_document_chunks(document_id) == 0:
        raise HTTPException(status_code=409, detail=MISSING_INDEX_CHUNKS_MESSAGE)
    registered = await oracle.update_document_status(document_id, FileStatus.REGISTERED)
    return ApiResponse(data=registered)


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
