"""認証・ユーザー・ロール（platform の共通 router）と、RAG の権限管理 API（#214）。

- `/auth/*`・`/security/users*`・`/security/roles*`: platform の `build_auth_router`
- `GET /security/permissions`: 権限カタログ
- `GET /security/access-targets/{search-answ
er-profiles,knowledge-bases}`: 権限管理で選べる検索・回答
プロファイルと
  ナレッジベース（検索とページング。#608）
- `PUT /security/roles/{role_id}/access`: ロールの RAG 権限と対象範囲の保存
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pr_backend_core import ApiResponse, Page
from pr_system_settings.auth.domain import Principal as PlatformPrincipal
from pr_system_settings.auth.domain import RoleRecord as PlatformRoleRecord
from pr_system_settings.auth.router import build_auth_router
from starlette.concurrency import run_in_threadpool

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.request_context import unrestricted_access_scope
from app.schemas.knowledge_base import KnowledgeBaseSummary
from app.schemas.search_answer_profile import SearchAnswerProfileSummary

from .dependencies import current_principal, local_debug_principal, request_context
from .domain import as_principal, as_role
from .permissions import PERMISSION_CATALOG
from .schemas import (
    AccessTargetData,
    CurrentUserData,
    PermissionData,
    RoleAccessUpdateRequest,
    RoleData,
)
from .service import get_security_service

router = APIRouter(tags=["security"])


def _current_user_data(principal: PlatformPrincipal, debug_mode: bool) -> CurrentUserData:
    return CurrentUserData.from_principal(as_principal(principal), debug_mode=debug_mode)


def _role_data(role: PlatformRoleRecord) -> RoleData:
    return RoleData.from_record(as_role(role))


# 認証 API とユーザー管理・ロール管理（基本情報）は 3 製品共通（platform。#212）。
auth_router = build_auth_router(
    get_service=lambda: get_security_service(),
    get_settings=lambda: get_settings(),
    current_principal=current_principal,
    request_context=request_context,
    local_debug_principal=local_debug_principal,
    current_user_model=CurrentUserData,
    current_user_data=_current_user_data,
    role_model=RoleData,
    role_data=_role_data,
)
router.include_router(auth_router)


@router.get("/security/permissions", response_model=ApiResponse[list[PermissionData]])
def permission_catalog() -> ApiResponse[list[PermissionData]]:
    """権限管理画面の権限カタログ（code / group / label / description / implies）。"""
    return ApiResponse(data=[PermissionData.from_definition(item) for item in PERMISSION_CATALOG])


# 権限管理の「利用できる対象」の候補の 1 ページの上限（#608）。
# 画面は 50 件ずつ読み、選択済みの名前は `ids` で読む。
ACCESS_TARGET_PAGE_LIMIT_MAX = 100


def _access_target_page(
    items: list[AccessTargetData], *, total: int, limit: int, offset: int
) -> Page[AccessTargetData]:
    return Page(
        items=items, total=total, limit=limit, offset=offset, has_next=offset + limit < total
    )


def _access_target_data(
    item: SearchAnswerProfileSummary | KnowledgeBaseSummary,
) -> AccessTargetData:
    return AccessTargetData(
        id=item.id,
        name=item.name,
        status=str(getattr(item.status, "value", item.status)),
        description=item.description,
    )


@router.get(
    "/security/access-targets/search-answer-profiles",
    response_model=ApiResponse[Page[AccessTargetData]],
)
async def list_search_answer_profile_access_targets(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=ACCESS_TARGET_PAGE_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    ids: Annotated[list[str] | None, Query(max_length=ACCESS_TARGET_PAGE_LIMIT_MAX)] = None,
) -> ApiResponse[Page[AccessTargetData]]:
    """権限管理画面で選べる検索・回答プロファイル（アーカイブ済みを含む）を、検索とページングで返す（#608）。

    - `q`: 名前・説明の部分一致。`ids`: その ID だけ（ロールに選択済みの対象の名前の解決に使う）。
    - 一覧は利用者の対象範囲で絞る（SYSTEM_ADMIN と `rag.se
    arch_answer_profiles.manage` を持つ利用者は全件、

      それ以外は自分の範囲内だけ）。範囲外の対象は見せない。
    """
    oracle = OracleClient()
    query = (q or "").strip() or None
    views = await oracle.list_search_answer_profiles(
        query=query, limit=limit, offset=offset, search_answer_profile_ids=ids
    )
    total = await oracle.count_search_answer_profiles(query=query, search_answer_profile_ids=ids)
    return ApiResponse(
        data=_access_target_page(
            [_access_target_data(view) for view in views], total=total, limit=limit, offset=offset
        )
    )


@router.get(
    "/security/access-targets/knowledge-bases",
    response_model=ApiResponse[Page[AccessTargetData]],
)
async def list_knowledge_base_access_targets(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=ACCESS_TARGET_PAGE_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    ids: Annotated[list[str] | None, Query(max_length=ACCESS_TARGET_PAGE_LIMIT_MAX)] = None,
) -> ApiResponse[Page[AccessTargetData]]:
    """権限管理画面で選べるナレッジベース（アーカイブ済みを含む）を、検索とページングで返す（#608）。

    絞り込み・対象範囲は検索・回答プロファイルと同じ（`rag.kn
    owledge_bases.manage` を持つ利用者は全件）。

    """
    oracle = OracleClient()
    query = (q or "").strip() or None
    bases = await oracle.list_knowledge_bases(
        query=query, limit=limit, offset=offset, knowledge_base_ids=ids
    )
    total = await oracle.count_knowledge_bases(query=query, knowledge_base_ids=ids)
    return ApiResponse(
        data=_access_target_page(
            [_access_target_data(base) for base in bases], total=total, limit=limit, offset=offset
        )
    )


@router.put("/security/roles/{role_id}/access", response_model=ApiResponse[RoleData])
async def update_role_access(
    role_id: str,
    payload: RoleAccessUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[RoleData]:
    """権限管理画面の保存。ロールの RAG 権限と対象範"
    "囲（検索・回答プロファイル・KB）だけを置き換える。"""
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    # 指定 ID の存在確認は利用者の範囲と無関係に行う（範囲外は 403、存在しない ID は 400）。
    with unrestricted_access_scope():
        (
            known_search_answer_profile_ids,
            known_knowledge_base_ids,
        ) = await OracleClient().list_access_target_ids()
    role = await run_in_threadpool(
        lambda: get_security_service().update_role_access(
            role_id,
            expected_version=payload.version,
            permissions=payload.permissions,
            search_answer_profile_ids=payload.search_answer_profile_ids,
            knowledge_base_ids=payload.knowledge_base_ids,
            known_search_answer_profile_ids=known_search_answer_profile_ids,
            known_knowledge_base_ids=known_knowledge_base_ids,
            actor=actor,
            request_id=request_id,
            client_ip=client_ip,
        )
    )
    response.headers["ETag"] = f'"{role.version}"'
    return ApiResponse(data=RoleData.from_record(role))
