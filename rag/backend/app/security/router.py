"""認証・ユーザー・ロール（platform の共通 router）と、RAG の権限管理 API（#214）。

- `/auth/*`・`/security/users*`・`/security/roles*`: platform の `build_auth_router`
- `GET /security/permissions`: 権限カタログ
- `GET /security/access-targets`: 権限管理で選べる業務ビューとナレッジベース
- `PUT /security/roles/{role_id}/access`: ロールの RAG 権限と対象範囲の保存
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pr_backend_core import ApiResponse
from pr_system_settings.auth.domain import Principal as PlatformPrincipal
from pr_system_settings.auth.domain import RoleRecord as PlatformRoleRecord
from pr_system_settings.auth.router import build_auth_router
from starlette.concurrency import run_in_threadpool

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.request_context import unrestricted_access_scope

from .dependencies import current_principal, local_debug_principal, request_context
from .domain import as_principal, as_role
from .permissions import PERMISSION_CATALOG
from .schemas import (
    AccessTargetData,
    AccessTargetsData,
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


@router.get("/security/access-targets", response_model=ApiResponse[AccessTargetsData])
async def list_access_targets() -> ApiResponse[AccessTargetsData]:
    """権限管理画面で選べる業務ビューとナレッジベース（アーカイブ済みを含む）。

    一覧は利用者の対象範囲で絞る（SYSTEM_ADMIN と `rag.*.manage` を持つ利用者は全件、
    それ以外は自分の範囲内だけ）。範囲外の対象は見せない。
    """
    oracle = OracleClient()
    views = await oracle.list_business_views(limit=None)
    bases = await oracle.list_knowledge_bases(limit=None)
    return ApiResponse(
        data=AccessTargetsData(
            business_views=[
                AccessTargetData(
                    id=view.id,
                    name=view.name,
                    status=str(getattr(view.status, "value", view.status)),
                    description=view.description,
                )
                for view in views
            ],
            knowledge_bases=[
                AccessTargetData(
                    id=base.id,
                    name=base.name,
                    status=str(getattr(base.status, "value", base.status)),
                    description=base.description,
                )
                for base in bases
            ],
        )
    )


@router.put("/security/roles/{role_id}/access", response_model=ApiResponse[RoleData])
async def update_role_access(
    role_id: str,
    payload: RoleAccessUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[RoleData]:
    """権限管理画面の保存。ロールの RAG 権限と対象範囲（業務ビュー・KB）だけを置き換える。"""
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    # 指定 ID の存在確認は利用者の範囲と無関係に行う（範囲外は 403、存在しない ID は 400）。
    with unrestricted_access_scope():
        known_business_view_ids, known_knowledge_base_ids = (
            await OracleClient().list_access_target_ids()
        )
    role = await run_in_threadpool(
        lambda: get_security_service().update_role_access(
            role_id,
            expected_version=payload.version,
            permissions=payload.permissions,
            business_view_ids=payload.business_view_ids,
            knowledge_base_ids=payload.knowledge_base_ids,
            known_business_view_ids=known_business_view_ids,
            known_knowledge_base_ids=known_knowledge_base_ids,
            actor=actor,
            request_id=request_id,
            client_ip=client_ip,
        )
    )
    response.headers["ETag"] = f'"{role.version}"'
    return ApiResponse(data=RoleData.from_record(role))
