"""認証・ユーザー・ロール（platform の共通 router）と、NL2SQL の権限・DeepSec API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pr_backend_core import ApiResponse
from pr_system_settings.auth.domain import Principal as PlatformPrincipal
from pr_system_settings.auth.domain import RoleRecord as PlatformRoleRecord
from pr_system_settings.auth.router import build_auth_router

from app.api.concurrency import run_sync_io
from app.settings import get_settings

from .deepsec import get_deepsec_service
from .dependencies import current_principal, local_debug_principal, request_context
from .domain import SYSTEM_ADMIN_ROLE_CODE, as_principal, as_role
from .permissions import PERMISSION_CATALOG, grants_all_profile_access
from .schemas import (
    CurrentUserData,
    DeepSecApplyRequest,
    DeepSecConfigUpdate,
    DeepSecDataEntitlementApplyData,
    DeepSecDataEntitlementApplyRequest,
    DeepSecDataEntitlementPreviewData,
    DeepSecDataEntitlementPreviewRequest,
    DeepSecDataEntitlementUpdateRequest,
    DeepSecResetRequest,
    DeepSecRoleEntitlementsData,
    DeepSecTargetObjectDetailData,
    DeepSecTargetObjectPageData,
    PermissionData,
    ProfileAccessProfileData,
    RoleData,
    RolePermissionsUpdateRequest,
)
from .service import get_security_service

router = APIRouter(tags=["security"])
run_in_threadpool = run_sync_io


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


@router.put("/security/roles/{role_id}/permissions", response_model=ApiResponse[RoleData])
def update_role_permissions(
    role_id: str,
    payload: RolePermissionsUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[RoleData]:
    """権限管理画面の保存。menu 権限と業務プロファイル利用権限だけを更新する（#206）。"""
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = get_security_service().update_role(
        role_id,
        expected_version=payload.version,
        permissions=set(payload.permissions),
        allowed_profile_ids=(
            set(payload.allowed_profile_ids) if payload.allowed_profile_ids is not None else None
        ),
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    response.headers["ETag"] = f'"{role.version}"'
    return ApiResponse(data=RoleData.from_record(role))


@router.get(
    "/security/profile-access/profiles",
    response_model=ApiResponse[list[ProfileAccessProfileData]],
)
def list_profile_access_profiles(
    include_archived: bool = Query(default=False),
) -> ApiResponse[list[ProfileAccessProfileData]]:
    """権限管理画面向けに業務 profile の利用権限カタログを返す。"""
    from app.features.nl2sql.service import nl2sql_service

    profiles = nl2sql_service.list_profiles(include_archived=include_archived)
    profile_ids = {profile.id for profile in profiles}
    roles = get_security_service().list_roles(include_archived=True)
    allowed_roles_by_profile: dict[str, list[str]] = {profile_id: [] for profile_id in profile_ids}
    for role in roles:
        if role.archived:
            continue
        if role.role_code == SYSTEM_ADMIN_ROLE_CODE or grants_all_profile_access(role.permissions):
            for profile_id in profile_ids:
                allowed_roles_by_profile[profile_id].append(role.role_id)
            continue
        for profile_id in role.allowed_profile_ids:
            if profile_id in allowed_roles_by_profile:
                allowed_roles_by_profile[profile_id].append(role.role_id)
    return ApiResponse(
        data=[
            ProfileAccessProfileData(
                id=profile.id,
                name=profile.name,
                category=profile.category,
                description=profile.description,
                archived=profile.archived,
                allowed_role_ids=sorted(allowed_roles_by_profile.get(profile.id, [])),
            )
            for profile in profiles
        ]
    )


@router.get("/security/permissions", response_model=ApiResponse[list[PermissionData]])
def permission_catalog() -> ApiResponse[list[PermissionData]]:
    return ApiResponse(data=[PermissionData.from_definition(item) for item in PERMISSION_CATALOG])


@router.get(
    "/security/deepsec/data-entitlements",
    response_model=ApiResponse[list[DeepSecRoleEntitlementsData]],
)
def list_deepsec_data_entitlements() -> ApiResponse[list[DeepSecRoleEntitlementsData]]:
    return ApiResponse(data=get_deepsec_service().data_entitlements())


@router.get("/security/deepsec/scope-profiles")
def list_deepsec_scope_profiles() -> ApiResponse[list[dict[str, object]]]:
    from .scope_relations import scope_profiles

    return ApiResponse(data=scope_profiles())


@router.get("/security/deepsec/relations")
def list_deepsec_relations(
    profile_id: str, owner: str, object_name: str
) -> ApiResponse[dict[str, object]]:
    from .deepsec import _qualified
    from .scope_relations import relation_catalog

    return ApiResponse(
        data=relation_catalog(get_deepsec_service(), profile_id, _qualified(owner, object_name))
    )


@router.get(
    "/security/deepsec/target-objects",
    response_model=ApiResponse[DeepSecTargetObjectPageData],
)
def list_deepsec_target_objects(
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    q: Annotated[str, Query(max_length=128)] = "",
    owner_prefix: Annotated[str, Query(max_length=128)] = "",
    include_counts: bool = False,
    profile_id: str | None = None,
) -> ApiResponse[DeepSecTargetObjectPageData]:
    return ApiResponse(
        data=get_deepsec_service().target_objects(
            cursor=cursor,
            limit=limit,
            q=q,
            owner_prefix=owner_prefix,
            include_counts=include_counts,
            **({"profile_id": profile_id} if profile_id else {}),
        )
    )


@router.get(
    # object_name は canonical token（引用名は "..."）。"/" を含む表名も受けるため path 変換。
    "/security/deepsec/target-objects/{owner}/{object_name:path}",
    response_model=ApiResponse[DeepSecTargetObjectDetailData],
)
def get_deepsec_target_object(
    owner: str,
    object_name: str,
    object_type: Annotated[str, Query(max_length=32)] = "",
) -> ApiResponse[DeepSecTargetObjectDetailData]:
    return ApiResponse(
        data=get_deepsec_service().target_object_detail(
            owner=owner,
            object_name=object_name,
            object_type=object_type,
        )
    )


@router.patch(
    "/security/deepsec/data-entitlements/{role_id}",
    response_model=ApiResponse[DeepSecRoleEntitlementsData],
)
def update_deepsec_data_entitlements(
    role_id: str,
    payload: DeepSecDataEntitlementUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[DeepSecRoleEntitlementsData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = get_security_service().update_role_data_entitlements(
        role_id,
        expected_version=payload.version,
        entitlements=[item.to_record(role_id) for item in payload.data_entitlements],
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    response.headers["ETag"] = f'"{role.version}"'
    return ApiResponse(data=get_deepsec_service().role_entitlements(role))


@router.post(
    "/security/deepsec/data-entitlements/{role_id}/preview",
    response_model=ApiResponse[DeepSecDataEntitlementPreviewData],
)
def preview_deepsec_data_entitlements(
    role_id: str,
    payload: DeepSecDataEntitlementPreviewRequest,
    request: Request,
) -> ApiResponse[DeepSecDataEntitlementPreviewData]:
    return ApiResponse(
        data=get_deepsec_service().preview_data_entitlements(
            role_id,
            expected_version=payload.version,
            entitlements=[item.to_record(role_id) for item in payload.data_entitlements],
            actor=current_principal(request),
        )
    )


@router.post(
    "/security/deepsec/data-entitlements/{role_id}/apply",
    response_model=ApiResponse[DeepSecDataEntitlementApplyData],
)
def apply_deepsec_data_entitlements(
    role_id: str,
    payload: DeepSecDataEntitlementApplyRequest,
    request: Request,
) -> ApiResponse[dict[str, object]]:
    return ApiResponse(
        data=get_deepsec_service().apply_data_entitlements(
            role_id,
            expected_version=payload.version,
            confirmation=payload.confirmation,
            entitlements=[item.to_record(role_id) for item in payload.data_entitlements],
            actor=current_principal(request),
        )
    )


@router.get("/security/deepsec/status", response_model=ApiResponse[dict[str, object]])
def deepsec_status() -> ApiResponse[dict[str, object]]:
    return ApiResponse(data=get_deepsec_service().status())


@router.get("/security/deepsec/plan", response_model=ApiResponse[dict[str, object]])
def deepsec_plan() -> ApiResponse[dict[str, object]]:
    return ApiResponse(data=get_deepsec_service().plan())


@router.patch("/security/deepsec/config", response_model=ApiResponse[dict[str, object]])
def update_deepsec_config(
    payload: DeepSecConfigUpdate,
    request: Request,
) -> ApiResponse[dict[str, object]]:
    current_principal(request)
    return ApiResponse(data=get_deepsec_service().update_config(payload.data_user_password))


@router.post(
    "/security/deepsec/config/sync-password",
    response_model=ApiResponse[dict[str, object]],
)
def sync_deepsec_config_password(request: Request) -> ApiResponse[dict[str, object]]:
    current_principal(request)
    return ApiResponse(data=get_deepsec_service().sync_saved_data_user_password())


@router.post(
    "/security/deepsec/plan/{version}/steps/{step_no}/apply",
    response_model=ApiResponse[dict[str, object]],
)
def apply_deepsec_step(
    version: str,
    step_no: int,
    payload: DeepSecApplyRequest,
    request: Request,
) -> ApiResponse[dict[str, object]]:
    if version != "V001":
        from .service import SecurityApiError

        raise SecurityApiError(404, "DeepSec plan version が見つかりません。")
    result = get_deepsec_service().apply_step(
        step_no,
        payload.checksum,
        payload.confirmation,
        current_principal(request),
    )
    return ApiResponse(data=result)


@router.post(
    "/security/deepsec/plan/{version}/reset",
    response_model=ApiResponse[dict[str, object]],
)
def reset_deepsec_plan(
    version: str,
    payload: DeepSecResetRequest,
    request: Request,
) -> ApiResponse[dict[str, object]]:
    return ApiResponse(
        data=get_deepsec_service().reset(
            version,
            payload.confirmation,
            current_principal(request),
        )
    )


@router.post("/security/deepsec/verify", response_model=ApiResponse[dict[str, object]])
def verify_deepsec(request: Request) -> ApiResponse[dict[str, object]]:
    return ApiResponse(
        data=get_deepsec_service().verify(
            current_principal(request),
        )
    )
