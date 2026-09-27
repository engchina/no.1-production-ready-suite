"""認証・ユーザー・ロール（platform の共通 router）と、Agent の権限管理 API（#215）。

- `/auth/*`・`/security/users*`・`/security/roles*`: platform の `build_auth_router`
- `GET /security/permissions`: 権限カタログ
- `GET /security/access-targets`: 権限管理で選べるエージェントと業務ビュー
- `PUT /security/roles/{role_id}/access`: ロールの Agent 権限と対象範囲の保存
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from pr_backend_core import ApiResponse
from pr_system_settings.auth.domain import Principal as PlatformPrincipal
from pr_system_settings.auth.domain import RoleRecord as PlatformRoleRecord
from pr_system_settings.auth.router import build_auth_router
from starlette.concurrency import run_in_threadpool

import app.settings as app_settings
from app.features.agent.config import runtime_config_store
from app.features.agent.router import run_business_view_id
from app.features.agent.runtime import runtime_repository
from app.features.agent.tools import (
    ExternalRagListBusinessViewsInput,
    ExternalToolError,
    ToolInvocationContext,
    list_rag_business_views,
)

from .dependencies import current_principal, local_debug_principal, request_context
from .domain import as_principal, as_role
from .permissions import PERMISSION_CATALOG
from .schemas import (
    AccessTargetsData,
    AgentTargetData,
    BusinessViewTargetData,
    CurrentUserData,
    PermissionData,
    RoleAccessUpdateRequest,
    RoleData,
)
from .service import BUSINESS_VIEW_ID_PATTERN, get_security_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["security"])
# 権限管理の候補に出す RAG の業務ビューの上限（MCP の rag_list_business_views の limit の上限）。
_RAG_BUSINESS_VIEW_LIMIT = 200


def _current_user_data(principal: PlatformPrincipal, debug_mode: bool) -> CurrentUserData:
    return CurrentUserData.from_principal(as_principal(principal), debug_mode=debug_mode)


def _role_data(role: PlatformRoleRecord) -> RoleData:
    return RoleData.from_record(as_role(role))


# 認証 API とユーザー管理・ロール管理（基本情報）は 3 製品共通（platform。#212）。
auth_router = build_auth_router(
    get_service=lambda: get_security_service(),
    get_settings=lambda: app_settings.get_settings(),
    current_principal=current_principal,
    request_context=request_context,
    local_debug_principal=local_debug_principal,
    current_user_model=CurrentUserData,
    current_user_data=_current_user_data,
    role_model=RoleData,
    role_data=_role_data,
)
router.include_router(auth_router)


def _run_business_view_ids() -> set[str]:
    """Run の metadata に現れた業務ビュー ID（Agent に業務ビューのマスタはない）。"""
    return {
        view_id
        for run in runtime_repository.list_runs()
        if (view_id := run_business_view_id(run)) is not None
    }


def _rag_business_view_names(user_uuid: str) -> tuple[dict[str, str], list[str]]:
    """RAG の業務ビュー（ID → 名前）を、画面を開いた利用者として MCP で読む（#233）。

    RAG の MCP が未設定・失敗のときは空にして、警告を返す（権限管理は今までどおり使える）。
    """
    if not runtime_config_store.get_rag().mcp_url:
        return {}, [
            "外部 RAG の MCP が設定されていないため、RAG の業務ビューは候補に含まれていません。"
        ]
    try:
        views = list_rag_business_views(
            ExternalRagListBusinessViewsInput(limit=_RAG_BUSINESS_VIEW_LIMIT),
            context=ToolInvocationContext(user_uuid=user_uuid),
        ).business_views
    except ExternalToolError as exc:
        logger.warning(
            "RAG の業務ビューを取得できませんでした",
            extra={"error_code": exc.code, "error_message": exc.message},
        )
        return {}, [f"RAG の業務ビューを取得できませんでした（{exc.message}）。"]
    # Agent の業務ビュー ID の形式に合わないものは保存できないため候補にしない。
    return {
        view.id: view.name or view.id
        for view in views
        if BUSINESS_VIEW_ID_PATTERN.fullmatch(view.id)
    }, []


@router.get("/security/permissions", response_model=ApiResponse[list[PermissionData]])
def permission_catalog() -> ApiResponse[list[PermissionData]]:
    """権限管理画面の権限カタログ（code / group / label / description / implies）。"""
    return ApiResponse(data=[PermissionData.from_definition(item) for item in PERMISSION_CATALOG])


@router.get("/security/access-targets", response_model=ApiResponse[AccessTargetsData])
async def list_access_targets(request: Request) -> ApiResponse[AccessTargetsData]:
    """権限管理画面で選べるエージェントと業務ビュー。

    - エージェント: Runtime repository の業務 Agent（無効を含む）。
    - 業務ビュー: Agent にマスタがないため、Run の metadata に現れた ID とロールに割り当て済みの
      ID と、RAG の MCP で読んだ業務ビュー（画面を開いた利用者が RAG で使えるもの。#233）の和集合。
      名前は RAG から読めたものは RAG の名前、それ以外は ID と同じ。RAG を読めなければ警告を返す。
    - 一覧は利用者の対象範囲で絞る（SYSTEM_ADMIN と `agent.admin` を持つ利用者は全件）。
    """
    principal = as_principal(current_principal(request))
    assigned_views = await run_in_threadpool(get_security_service().assigned_business_view_ids)
    rag_view_names, warnings = await run_in_threadpool(
        _rag_business_view_names, principal.user_uuid
    )
    view_ids = _run_business_view_ids() | assigned_views | set(rag_view_names)
    agents = [
        agent for agent in runtime_repository.list_agents() if principal.can_use_agent(agent.id)
    ]
    return ApiResponse(
        data=AccessTargetsData(
            agents=[
                AgentTargetData(
                    id=agent.id,
                    name=agent.name,
                    description=agent.description or None,
                    status="enabled" if agent.enabled else "disabled",
                )
                for agent in agents
            ],
            business_views=[
                BusinessViewTargetData(id=view_id, name=rag_view_names.get(view_id, view_id))
                for view_id in sorted(view_ids)
                if principal.can_use_business_view(view_id)
            ],
            business_view_warnings=warnings,
        ),
        warning_messages=warnings,
    )


@router.put("/security/roles/{role_id}/access", response_model=ApiResponse[RoleData])
async def update_role_access(
    role_id: str,
    payload: RoleAccessUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[RoleData]:
    """権限管理画面の保存。

    ロールの Agent 権限と対象範囲（エージェント・業務ビュー）だけを置き換える。
    """
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    # エージェント ID の存在確認は利用者の範囲と無関係に行う（範囲外は 403、存在しない ID は 400）。
    known_agent_ids = {agent.id for agent in runtime_repository.list_agents()}
    role = await run_in_threadpool(
        lambda: get_security_service().update_role_access(
            role_id,
            expected_version=payload.version,
            permissions=payload.permissions,
            agent_ids=payload.agent_ids,
            business_view_ids=payload.business_view_ids,
            known_agent_ids=known_agent_ids,
            actor=actor,
            request_id=request_id,
            client_ip=client_ip,
        )
    )
    response.headers["ETag"] = f'"{role.version}"'
    return ApiResponse(data=RoleData.from_record(role))
