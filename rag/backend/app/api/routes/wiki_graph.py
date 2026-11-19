"""KB 詳細の Wiki の図の読み取り（#5502）。案内の構築・検索の経路は変えない。"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.clients.oracle import OracleClient
from app.clients.wiki_graph_store import WikiGraphStore
from app.config import get_settings
from app.db_read_failures import read_or_raise
from app.schemas.common import ApiResponse
from app.schemas.wiki_graph import (
    WikiGraphLocation,
    WikiGraphOverview,
    WikiGraphSearchPage,
    WikiGraphTreePage,
    WikiGraphUnitDetail,
)

router = APIRouter()
# 単位 ID・章節 ID は sha256 の 16 進。
NODE_ID = Query(min_length=64, max_length=64, pattern="^[a-f0-9]+$")


async def _read(
    knowledge_base_id: str,
    log_label: str,
    read: Callable[[WikiGraphStore], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """KB の確認と読み取り。読み取りの失敗は API の失敗にし、見えない対象は 404。"""
    oracle = OracleClient()

    async def _load() -> dict[str, Any]:
        if not await oracle.knowledge_base_is_visible(knowledge_base_id):
            raise HTTPException(status_code=404, detail="ナレッジベースが見つかりません。")
        try:
            return await read(WikiGraphStore(oracle))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="対象が見つかりません。") from exc

    return await read_or_raise(
        _load, timeout_seconds=get_settings().db_read_timeout_seconds, log_label=log_label
    )


@router.get(
    "/{knowledge_base_id}/wiki/graph",
    response_model=ApiResponse[WikiGraphOverview],
)
async def get_wiki_graph_overview(knowledge_base_id: str) -> ApiResponse[WikiGraphOverview]:
    """案内のある文書と、文書ごとの単位の数・文書をまたぐ関連の数を返す。"""
    data = await _read(
        knowledge_base_id, "wiki_graph_overview", lambda store: store.overview(knowledge_base_id)
    )
    return ApiResponse(data=WikiGraphOverview(**data))


@router.get(
    "/{knowledge_base_id}/wiki/graph/documents/{document_id}/tree",
    response_model=ApiResponse[WikiGraphTreePage],
)
async def get_wiki_graph_tree(
    knowledge_base_id: str,
    document_id: str,
    parent: str | None = Query(default=None, min_length=64, max_length=64, pattern="^[a-f0-9]+$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> ApiResponse[WikiGraphTreePage]:
    """木の 1 段（文書の順）を返す。``parent`` が無ければ文書の直下、あれば章節の直下。"""
    data = await _read(
        knowledge_base_id,
        "wiki_graph_tree",
        lambda store: store.children(
            knowledge_base_id, document_id, parent_id=parent, offset=offset, limit=limit
        ),
    )
    return ApiResponse(data=WikiGraphTreePage(**data))


@router.get(
    "/{knowledge_base_id}/wiki/graph/documents/{document_id}/locate",
    response_model=ApiResponse[WikiGraphLocation],
)
async def locate_wiki_graph_node(
    knowledge_base_id: str, document_id: str, node: str = NODE_ID
) -> ApiResponse[WikiGraphLocation]:
    """節点までの段と各段の位置を返す（検索・関係から図の位置へ開く）。"""
    data = await _read(
        knowledge_base_id,
        "wiki_graph_locate",
        lambda store: store.locate(knowledge_base_id, document_id, node),
    )
    return ApiResponse(data=WikiGraphLocation(**data))


@router.get(
    "/{knowledge_base_id}/wiki/graph/search",
    response_model=ApiResponse[WikiGraphSearchPage],
)
async def search_wiki_graph(
    knowledge_base_id: str,
    q: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> ApiResponse[WikiGraphSearchPage]:
    """KB の全体から原文単位の見出し・章節・案内を検索する。"""
    data = await _read(
        knowledge_base_id,
        "wiki_graph_search",
        lambda store: store.search(knowledge_base_id, q, offset=offset, limit=limit),
    )
    return ApiResponse(data=WikiGraphSearchPage(**data))


@router.get(
    "/{knowledge_base_id}/wiki/graph/units/{unit_id}",
    response_model=ApiResponse[WikiGraphUnitDetail],
)
async def get_wiki_graph_unit(
    knowledge_base_id: str, unit_id: str
) -> ApiResponse[WikiGraphUnitDetail]:
    """単位の案内（要約・想定の質問・本文に無いこと）と原文の位置と、出る・入る関係を返す。"""
    data = await _read(
        knowledge_base_id, "wiki_graph_unit", lambda store: store.unit(knowledge_base_id, unit_id)
    )
    return ApiResponse(data=WikiGraphUnitDetail(**data))
