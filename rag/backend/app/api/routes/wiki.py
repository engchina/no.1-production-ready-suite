"""Wiki の背景構築の指定。本文は返さず、KB の見え方で状態だけを返す。"""

from collections import Counter

from fastapi import APIRouter, HTTPException, status

from app.clients.oracle import OracleClient
from app.clients.wiki_store import WikiStore
from app.config import get_settings
from app.rag.request_context import system_access_context
from app.rag.wiki_jobs import wiki_jobs
from app.rag.wiki_models import WIKI_BUILD_KEY
from app.schemas.common import ApiResponse
from app.schemas.knowledge_base import KnowledgeBaseStatus
from app.schemas.wiki import (
    KnowledgeBaseWikiData,
    KnowledgeBaseWikiDocument,
    KnowledgeBaseWikiUpdate,
)

router = APIRouter()


async def _actor_unavailable(store: WikiStore, knowledge_base_id: str) -> bool:
    """worker と同じ確認で、構築を指定した利用者が今も構築できるかを見る。"""
    with system_access_context():
        owners = dict(await store.enabled_knowledge_bases())
    try:
        await wiki_jobs.actor(owners.get(knowledge_base_id), get_settings())
    except Exception:
        return True
    return False


async def _set_enabled(knowledge_base_id: str, enabled: bool) -> None:
    """KB の指定を保存し、構築を指定した利用者を操作した利用者にする。"""
    try:
        await WikiStore(OracleClient()).set_enabled(knowledge_base_id, enabled)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{knowledge_base_id}/wiki", response_model=ApiResponse[KnowledgeBaseWikiData])
async def get_wiki_settings(knowledge_base_id: str) -> ApiResponse[KnowledgeBaseWikiData]:
    oracle = OracleClient()
    kb = await oracle.get_knowledge_base_config(knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    store = WikiStore(oracle)
    documents = [
        KnowledgeBaseWikiDocument.model_validate(row)
        for row in await store.document_statuses({"knowledge_base_id": knowledge_base_id})
    ]
    enabled = (
        kb.status == KnowledgeBaseStatus.ACTIVE and kb.retrieval_config.get(WIKI_BUILD_KEY) is True
    )
    return ApiResponse(
        data=KnowledgeBaseWikiData(
            enabled=enabled,
            counts=dict(Counter(document.status for document in documents)),
            documents=documents,
            blocked_reason="actor_unavailable"
            if enabled and await _actor_unavailable(store, knowledge_base_id)
            else None,
        )
    )


@router.put("/{knowledge_base_id}/wiki", response_model=ApiResponse[KnowledgeBaseWikiData])
async def update_wiki_settings(
    knowledge_base_id: str, request: KnowledgeBaseWikiUpdate
) -> ApiResponse[KnowledgeBaseWikiData]:
    # 保存領域の読み取りを先に確かめ、保存の後で未更新の schema に気付かないようにする。
    await get_wiki_settings(knowledge_base_id)
    await _set_enabled(knowledge_base_id, request.enabled)
    if request.enabled:
        wiki_jobs.notify(knowledge_base_id)
    return await get_wiki_settings(knowledge_base_id)


@router.post(
    "/{knowledge_base_id}/wiki/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[KnowledgeBaseWikiData],
)
async def retry_wiki_build(knowledge_base_id: str) -> ApiResponse[KnowledgeBaseWikiData]:
    current = await get_wiki_settings(knowledge_base_id)
    if not current.data or not current.data.enabled:
        raise HTTPException(status_code=409, detail="Wiki の構築を有効にしてください。")
    # 権限を失った利用者の指定のまま止まらないよう、再試行した利用者で構築する（#5466）。
    await _set_enabled(knowledge_base_id, True)
    await WikiStore(OracleClient()).requeue_failed({"knowledge_base_id": knowledge_base_id})
    wiki_jobs.notify(knowledge_base_id)
    # 待機へ戻した後の状態を返し、画面が再試行の前の件数で上書きしないようにする。
    return await get_wiki_settings(knowledge_base_id)
