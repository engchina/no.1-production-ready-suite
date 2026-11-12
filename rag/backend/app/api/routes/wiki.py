"""Wiki の背景構築の指定。本文は返さず、KB の見え方で状態だけを返す。"""

from fastapi import APIRouter, HTTPException, status

from app.clients.oracle import OracleClient
from app.clients.wiki_store import WikiStore
from app.rag.wiki_jobs import wiki_jobs
from app.rag.wiki_models import WIKI_BUILD_KEY
from app.schemas.common import ApiResponse
from app.schemas.knowledge_base import KnowledgeBaseStatus
from app.schemas.wiki import KnowledgeBaseWikiData, KnowledgeBaseWikiUpdate

router = APIRouter()


@router.get("/{knowledge_base_id}/wiki", response_model=ApiResponse[KnowledgeBaseWikiData])
async def get_wiki_settings(knowledge_base_id: str) -> ApiResponse[KnowledgeBaseWikiData]:
    oracle = OracleClient()
    kb = await oracle.get_knowledge_base_config(knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
    counts = await WikiStore(oracle).status_counts({"knowledge_base_id": knowledge_base_id})
    return ApiResponse(
        data=KnowledgeBaseWikiData(
            enabled=kb.status == KnowledgeBaseStatus.ACTIVE
            and kb.retrieval_config.get(WIKI_BUILD_KEY) is True,
            counts=counts,
        )
    )


@router.put("/{knowledge_base_id}/wiki", response_model=ApiResponse[KnowledgeBaseWikiData])
async def update_wiki_settings(
    knowledge_base_id: str, request: KnowledgeBaseWikiUpdate
) -> ApiResponse[KnowledgeBaseWikiData]:
    # 保存領域の読み取りを先に確かめ、保存の後で未更新の schema に気付かないようにする。
    await get_wiki_settings(knowledge_base_id)
    try:
        await WikiStore(OracleClient()).set_enabled(knowledge_base_id, request.enabled)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
    wiki_jobs.notify(knowledge_base_id, retry=True)
    return current
