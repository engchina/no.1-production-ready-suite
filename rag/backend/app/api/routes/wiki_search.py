"""プロファイルと KB の Wiki から同じ公開済みの索引・原文を読む。"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.db_read_failures import read_or_raise
from app.rag.wiki_explorer import Method, WikiExplorer
from app.rag.wiki_models import WikiRelation
from app.schemas.common import ApiResponse
from app.schemas.knowledge_base import KnowledgeBaseRef
from app.schemas.wiki_graph import (
    WikiGraphDocumentCount,
    WikiGraphOverview,
    WikiGraphTreePage,
    WikiOriginal,
    WikiRelationGroups,
    WikiSearchHits,
    WikiSearchScope,
)

profile_router = APIRouter()
Offset = Annotated[int, Query(ge=0)]
PageSize = Annotated[int, Query(ge=1, le=100)]
ScopeDependency = Callable[..., Awaitable[WikiExplorer]]


async def _read[T](load: Callable[[], Awaitable[T]]) -> T:
    async def read_visible() -> T:
        try:
            return await load()
        except KeyError as exc:
            raise HTTPException(404, "対象が見つかりません。") from exc

    return await read_or_raise(
        read_visible,
        timeout_seconds=get_settings().db_read_timeout_seconds,
        log_label="wiki_explorer",
    )


@profile_router.get("/profiles-for-kb/{kb_id}")
async def chat_profiles(kb_id: str) -> ApiResponse[list[KnowledgeBaseRef]]:
    rows = await _read(OracleClient().search_answer_profile_targets)
    return ApiResponse(
        data=[
            KnowledgeBaseRef(id=row.id, name=row.name)
            for row in rows
            if any(k.id == kb_id for k in row.knowledge_bases)
        ]
    )


async def profile_scope(scope_id: str, kb: str | None = None) -> WikiExplorer:
    return await _read(lambda: WikiExplorer.for_profile(OracleClient(), scope_id, kb))


async def kb_scope(scope_id: str) -> WikiExplorer:
    oracle = OracleClient()
    kb = await _read(lambda: oracle.knowledge_base_ref(scope_id))
    if kb is None:
        raise HTTPException(404, "ナレッジベースが見つかりません。")
    return WikiExplorer(oracle, [kb], get_settings())


def explorer_router(scope_dependency: ScopeDependency) -> APIRouter:
    router = APIRouter()
    resolve_scope = Depends(scope_dependency)

    @router.get("/scope")
    async def scope_info(
        scope: WikiExplorer = resolve_scope,
    ) -> ApiResponse[WikiSearchScope]:
        return ApiResponse(data=WikiSearchScope(knowledge_bases=scope.knowledge_bases))

    @router.get("/search")
    async def search(
        q: Annotated[str, Query(min_length=1, max_length=8000)],
        scope: WikiExplorer = resolve_scope,
        method: Method = "hybrid",
        offset: Offset = 0,
        limit: PageSize = 25,
    ) -> ApiResponse[WikiSearchHits]:
        if not q.strip():
            raise HTTPException(422, "質問を入力してください。")
        return ApiResponse(data=await scope.search(q.strip(), method, offset, limit))

    @router.get("/documents")
    async def documents(
        scope: WikiExplorer = resolve_scope,
        offset: Offset = 0,
        limit: Annotated[int, Query(ge=1, le=25)] = 25,
    ) -> ApiResponse[WikiGraphOverview]:
        data = await _read(lambda: scope.graph.overview(scope.ids, offset=offset, limit=limit))
        return ApiResponse(data=WikiGraphOverview(**data))

    @router.get("/document-count")
    async def document_count(
        scope: WikiExplorer = resolve_scope,
    ) -> ApiResponse[WikiGraphDocumentCount]:
        return ApiResponse(
            data=WikiGraphDocumentCount(
                **await _read(lambda: scope.graph.document_count(scope.ids))
            )
        )

    @router.get("/documents/{document_id}/tree")
    async def tree(
        document_id: str,
        scope: WikiExplorer = resolve_scope,
        parent: str | None = None,
        offset: Offset = 0,
        limit: PageSize = 25,
    ) -> ApiResponse[WikiGraphTreePage]:
        data = await _read(
            lambda: scope.graph.children(
                scope.ids, document_id, parent_id=parent, offset=offset, limit=limit
            )
        )
        return ApiResponse(data=WikiGraphTreePage(**data))

    @router.get("/units/{unit_id}")
    async def original(
        unit_id: str, scope: WikiExplorer = resolve_scope
    ) -> ApiResponse[WikiOriginal]:
        return ApiResponse(data=await _read(lambda: scope.original(unit_id)))

    @router.get("/units/{unit_id}/relations")
    async def relations(
        unit_id: str,
        scope: WikiExplorer = resolve_scope,
        relation: WikiRelation | None = None,
        direction: Literal["outgoing", "incoming"] | None = None,
        offset: Offset = 0,
        limit: PageSize = 6,
    ) -> ApiResponse[WikiRelationGroups]:
        data = await _read(
            lambda: scope.graph.relations(
                scope.ids,
                unit_id,
                offset,
                limit,
                relation=relation,
                direction=direction,
                grouped=True,
            )
        )
        return ApiResponse(data=WikiRelationGroups(**data))

    return router
