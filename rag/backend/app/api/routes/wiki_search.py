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
    WikiDocumentGraph,
    WikiGraphDocumentCount,
    WikiGraphLocation,
    WikiGraphOverview,
    WikiGraphTreePage,
    WikiOriginal,
    WikiRelationGroups,
    WikiSearchHits,
    WikiSearchScope,
    WikiUnitConnections,
)

profile_router = APIRouter()
Offset = Annotated[int, Query(ge=0)]
PageSize = Annotated[int, Query(ge=1, le=100)]
# 関係は単位ごとに 1 回で取り切る（画面でページ送り・絞り込みをする。#5916）。
RelationPageSize = Annotated[int, Query(ge=1, le=500)]
# 単位・章節の ID（sha256 の 16 進）。
NodeId = Annotated[str, Query(min_length=64, max_length=64, pattern="^[a-f0-9]+$")]
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
        return ApiResponse(
            data=WikiSearchScope(
                knowledge_bases=scope.knowledge_bases,
                selection_limit=get_settings().rag_wiki_selection_max_units,
            )
        )

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
        limit: RelationPageSize = 6,
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

    @router.get("/document-graph")
    async def document_graph(
        scope: WikiExplorer = resolve_scope,
        document: str | None = None,
        offset: Offset = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> ApiResponse[WikiDocumentGraph]:
        """参照の多い文書の見本と、その間の参照（``document`` はその文書の参照の相手）。"""
        data = await _read(
            lambda: scope.graph.document_graph(
                scope.ids, offset=offset, limit=limit, document=document
            )
        )
        return ApiResponse(data=WikiDocumentGraph(**data))

    @router.get("/connections")
    async def connections(
        ids: Annotated[str, Query(min_length=1, max_length=65 * 200)],
        scope: WikiExplorer = resolve_scope,
    ) -> ApiResponse[WikiUnitConnections]:
        """選んだ単位（カンマ区切り。200 件まで）の間の関係。"""
        unit_ids = [value for value in ids.split(",") if value]
        if len(unit_ids) > 200 or any(
            len(value) != 64 or value.strip("0123456789abcdef") for value in unit_ids
        ):
            raise HTTPException(422, "単位の指定が正しくありません。")
        data = await _read(lambda: scope.graph.connections(scope.ids, unit_ids))
        return ApiResponse(data=WikiUnitConnections(connections=data))

    @router.get("/documents/{document_id}/locate")
    async def locate(
        document_id: str, node: NodeId, scope: WikiExplorer = resolve_scope
    ) -> ApiResponse[WikiGraphLocation]:
        """文書の直下から節点までの段と各段の位置（図内検索で当たった単位へ開く）。"""
        data = await _read(lambda: scope.graph.locate(scope.ids, document_id, node))
        return ApiResponse(data=WikiGraphLocation(**data))

    return router
