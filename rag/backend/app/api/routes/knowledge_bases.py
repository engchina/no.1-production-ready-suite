"""ナレッジベース API。作成・一覧・詳細・membership 管理。"""

from fastapi import APIRouter, HTTPException, Query

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.db_degradation import load_or_degrade
from app.rag.kb_adapter_config import dump_adapter_config, resolve_effective_adapter_config
from app.schemas.common import ApiResponse, Page
from app.schemas.document import DocumentSummary, FileStatus
from app.schemas.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDetail,
    KnowledgeBaseDocumentAssignmentRequest,
    KnowledgeBaseStatus,
    KnowledgeBaseSummary,
    KnowledgeBaseUpdateRequest,
)

router = APIRouter()


def _detail_response(detail: KnowledgeBaseDetail) -> ApiResponse[KnowledgeBaseDetail]:
    """詳細に解決済み構築設定(継承値表示用)を埋めて返す。"""
    effective = resolve_effective_adapter_config(get_settings(), detail.adapter_config)
    return ApiResponse(data=detail.model_copy(update={"effective_adapter_config": effective}))


@router.get("", response_model=ApiResponse[Page[KnowledgeBaseSummary]])
async def list_knowledge_bases(
    status: KnowledgeBaseStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[KnowledgeBaseSummary]]:
    """ナレッジベース一覧を返す。DB 停止時は空一覧 + warning で縮退する。"""
    oracle = OracleClient()
    settings = get_settings()

    async def _load() -> Page[KnowledgeBaseSummary]:
        items = await oracle.list_knowledge_bases(
            status=status, query=q, limit=limit, offset=offset
        )
        total = await oracle.count_knowledge_bases(status=status, query=q)
        return Page(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )

    empty_page: Page[KnowledgeBaseSummary] = Page(
        items=[], total=0, limit=limit, offset=offset, has_next=False
    )
    page, degraded = await load_or_degrade(
        _load,
        timeout_seconds=settings.db_read_timeout_seconds,
        fallback=empty_page,
        log_label="knowledge_bases_list",
    )
    return ApiResponse(
        data=page,
        warning_messages=[degraded.message] if degraded else [],
    )


@router.post("", response_model=ApiResponse[KnowledgeBaseDetail])
async def create_knowledge_base(
    request: KnowledgeBaseCreateRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースを作成する。"""
    retrieval_config = (
        dump_adapter_config(request.adapter_config)
        if request.adapter_config is not None
        else request.retrieval_config
    )
    detail = await OracleClient().create_knowledge_base(
        name=request.name,
        description=request.description,
        default_search_mode=request.default_search_mode,
        retrieval_config=retrieval_config,
    )
    return _detail_response(detail)


@router.get("/{knowledge_base_id}", response_model=ApiResponse[KnowledgeBaseDetail])
async def get_knowledge_base(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベース詳細を返す。"""
    detail = await OracleClient().get_knowledge_base(knowledge_base_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    return _detail_response(detail)


@router.patch("/{knowledge_base_id}", response_model=ApiResponse[KnowledgeBaseDetail])
async def update_knowledge_base(
    knowledge_base_id: str,
    request: KnowledgeBaseUpdateRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースを更新する。"""
    update_fields = set(request.model_fields_set)
    retrieval_config = request.retrieval_config
    # adapter_config は既存 retrieval_config カラムへ正規化して保存する。
    if "adapter_config" in update_fields:
        retrieval_config = (
            dump_adapter_config(request.adapter_config)
            if request.adapter_config is not None
            else {}
        )
        update_fields.discard("adapter_config")
        update_fields.add("retrieval_config")
    try:
        detail = await OracleClient().update_knowledge_base(
            knowledge_base_id,
            name=request.name,
            description=request.description,
            default_search_mode=request.default_search_mode,
            retrieval_config=retrieval_config,
            update_fields=update_fields,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    return _detail_response(detail)


@router.post("/{knowledge_base_id}/archive", response_model=ApiResponse[KnowledgeBaseDetail])
async def archive_knowledge_base(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースをアーカイブする。"""
    try:
        detail = await OracleClient().archive_knowledge_base(knowledge_base_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    return _detail_response(detail)


@router.get("/{knowledge_base_id}/documents", response_model=ApiResponse[Page[DocumentSummary]])
async def list_knowledge_base_documents(
    knowledge_base_id: str,
    status: FileStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[DocumentSummary]]:
    """対象ナレッジベースの文書一覧を返す。"""
    oracle = OracleClient()
    if await oracle.get_knowledge_base(knowledge_base_id) is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    items = await oracle.list_documents(
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
    return ApiResponse(
        data=Page(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )
    )


@router.post("/{knowledge_base_id}/documents", response_model=ApiResponse[KnowledgeBaseDetail])
async def assign_documents_to_knowledge_base(
    knowledge_base_id: str,
    request: KnowledgeBaseDocumentAssignmentRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """既存文書をナレッジベースへ追加する。"""
    try:
        detail = await OracleClient().assign_documents_to_knowledge_base(
            knowledge_base_id,
            request.document_ids,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="ナレッジベースまたは文書が見つかりません。",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail_response(detail)


@router.delete(
    "/{knowledge_base_id}/documents/{document_id}",
    response_model=ApiResponse[KnowledgeBaseDetail],
)
async def remove_document_from_knowledge_base(
    knowledge_base_id: str,
    document_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """文書をナレッジベースから外す。文書自体は削除しない。"""
    try:
        detail = await OracleClient().remove_document_from_knowledge_base(
            knowledge_base_id,
            document_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="ナレッジベースまたは文書が見つかりません。",
        ) from exc
    return _detail_response(detail)
