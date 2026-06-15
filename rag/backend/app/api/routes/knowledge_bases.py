"""ナレッジベース API。作成・一覧・詳細・membership 管理。"""

from fastapi import APIRouter, HTTPException, Query

from app.clients.oracle import OracleClient
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


@router.get("", response_model=ApiResponse[Page[KnowledgeBaseSummary]])
async def list_knowledge_bases(
    status: KnowledgeBaseStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[KnowledgeBaseSummary]]:
    """ナレッジベース一覧を返す。"""
    oracle = OracleClient()
    items = await oracle.list_knowledge_bases(status=status, query=q, limit=limit, offset=offset)
    total = await oracle.count_knowledge_bases(status=status, query=q)
    return ApiResponse(
        data=Page(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )
    )


@router.post("", response_model=ApiResponse[KnowledgeBaseDetail])
async def create_knowledge_base(
    request: KnowledgeBaseCreateRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースを作成する。"""
    detail = await OracleClient().create_knowledge_base(
        name=request.name,
        description=request.description,
        default_search_mode=request.default_search_mode,
        retrieval_config=request.retrieval_config,
    )
    return ApiResponse(data=detail)


@router.get("/{knowledge_base_id}", response_model=ApiResponse[KnowledgeBaseDetail])
async def get_knowledge_base(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベース詳細を返す。"""
    detail = await OracleClient().get_knowledge_base(knowledge_base_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    return ApiResponse(data=detail)


@router.patch("/{knowledge_base_id}", response_model=ApiResponse[KnowledgeBaseDetail])
async def update_knowledge_base(
    knowledge_base_id: str,
    request: KnowledgeBaseUpdateRequest,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースを更新する。"""
    try:
        detail = await OracleClient().update_knowledge_base(
            knowledge_base_id,
            name=request.name,
            description=request.description,
            default_search_mode=request.default_search_mode,
            retrieval_config=request.retrieval_config,
            update_fields=set(request.model_fields_set),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    return ApiResponse(data=detail)


@router.post("/{knowledge_base_id}/archive", response_model=ApiResponse[KnowledgeBaseDetail])
async def archive_knowledge_base(
    knowledge_base_id: str,
) -> ApiResponse[KnowledgeBaseDetail]:
    """ナレッジベースをアーカイブする。"""
    try:
        detail = await OracleClient().archive_knowledge_base(knowledge_base_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from exc
    return ApiResponse(data=detail)


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
    return ApiResponse(data=detail)


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
    return ApiResponse(data=detail)
