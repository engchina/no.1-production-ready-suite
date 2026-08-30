"""認証、ユーザー、ロール、DeepSec API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response
from pr_backend_core import ApiResponse

from app.api.concurrency import run_sync_io
from app.settings import get_settings

from .deepsec import get_deepsec_service
from .dependencies import current_principal, local_debug_principal, request_context
from .domain import Principal, RoleRecord, UserRecord
from .permissions import PERMISSION_CATALOG
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
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetData,
    PasswordResetRequest,
    PermissionData,
    ProfileAccessProfileData,
    RoleArchiveRequest,
    RoleCreateRequest,
    RoleData,
    RoleDeleteData,
    RoleRestoreRequest,
    RoleUpdateRequest,
    UserCreateData,
    UserCreateRequest,
    UserData,
    UserDeleteData,
    UserUpdateRequest,
    VersionRequest,
)
from .service import get_security_service

router = APIRouter(tags=["security"])
run_in_threadpool = run_sync_io


def _set_auth_cookie(
    response: Response,
    *,
    name: str,
    value: str,
    httponly: bool,
) -> None:
    settings = get_settings()
    response.set_cookie(
        name,
        value,
        httponly=httponly,
        secure=settings.app_auth_cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.app_auth_absolute_timeout_hours * 3600,
    )


def _roles_by_id() -> dict[str, RoleRecord]:
    service = get_security_service()
    return {role.role_id: role for role in service.list_roles(include_archived=True)}


def _user_data(user: UserRecord) -> UserData:
    return UserData.from_record(user, roles_by_id=_roles_by_id())


def _expected_version(if_match: str | None) -> int:
    from .service import SecurityApiError

    if if_match is None or not if_match.strip():
        raise SecurityApiError(
            428,
            "削除には If-Match header で現在のバージョンを指定してください。"
            "表示を更新して再試行してください。",
            code="SECURITY_VERSION_REQUIRED",
        )
    normalized = if_match.strip()
    if normalized.startswith("W/"):
        raise SecurityApiError(
            400,
            "If-Match header には weak ETag ではなく現在の数値バージョンを指定してください。",
            code="SECURITY_VERSION_INVALID",
        )
    if normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    if not normalized.isdecimal() or int(normalized) < 1:
        raise SecurityApiError(
            400,
            "If-Match header には現在の数値バージョンを指定してください。",
            code="SECURITY_VERSION_INVALID",
        )
    return int(normalized)


@router.post("/auth/login", response_model=ApiResponse[CurrentUserData])
def login(
    payload: LoginRequest, request: Request, response: Response
) -> ApiResponse[CurrentUserData]:
    if get_settings().local_debug_enabled:
        return ApiResponse(
            data=CurrentUserData.from_principal(local_debug_principal(), debug_mode=True)
        )
    request_id, client_ip = request_context(request)
    principal, session_token, csrf_token = get_security_service().login(
        payload.login_user_id,
        payload.password,
        request_id=request_id,
        client_ip=client_ip,
    )
    settings = get_settings()
    _set_auth_cookie(
        response,
        name=settings.app_auth_session_cookie_name,
        value=session_token,
        httponly=True,
    )
    _set_auth_cookie(
        response,
        name=settings.app_auth_csrf_cookie_name,
        value=csrf_token,
        httponly=False,
    )
    return ApiResponse(data=CurrentUserData.from_principal(principal))


@router.get("/auth/me", response_model=ApiResponse[CurrentUserData])
def me(request: Request) -> ApiResponse[CurrentUserData]:
    settings = get_settings()
    return ApiResponse(
        data=CurrentUserData.from_principal(
            current_principal(request), debug_mode=settings.local_debug_enabled
        )
    )


@router.post("/auth/logout", response_model=ApiResponse[dict[str, bool]])
def logout(request: Request, response: Response) -> ApiResponse[dict[str, bool]]:
    if get_settings().local_debug_enabled:
        return ApiResponse(data={"logged_out": False})
    principal = current_principal(request)
    request_id, client_ip = request_context(request)
    get_security_service().logout(
        principal,
        request_id=request_id,
        client_ip=client_ip,
    )
    settings = get_settings()
    response.delete_cookie(settings.app_auth_session_cookie_name, path="/")
    response.delete_cookie(settings.app_auth_csrf_cookie_name, path="/")
    return ApiResponse(data={"logged_out": True})


@router.post("/auth/password/change", response_model=ApiResponse[dict[str, bool]])
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
) -> ApiResponse[dict[str, bool]]:
    if get_settings().local_debug_enabled:
        from .service import SecurityApiError

        raise SecurityApiError(409, "ローカル DEBUG モードではパスワードを変更できません。")
    principal = current_principal(request)
    request_id, client_ip = request_context(request)
    get_security_service().change_password(
        principal,
        payload.current_password,
        payload.new_password,
        request_id=request_id,
        client_ip=client_ip,
    )
    settings = get_settings()
    response.delete_cookie(settings.app_auth_session_cookie_name, path="/")
    response.delete_cookie(settings.app_auth_csrf_cookie_name, path="/")
    return ApiResponse(data={"changed": True})


@router.get("/security/users", response_model=ApiResponse[list[UserData]])
def list_users() -> ApiResponse[list[UserData]]:
    users = get_security_service().list_users()
    roles_by_id = _roles_by_id()
    return ApiResponse(data=[UserData.from_record(user, roles_by_id=roles_by_id) for user in users])


@router.post("/security/users", response_model=ApiResponse[UserCreateData])
def create_user(payload: UserCreateRequest, request: Request) -> ApiResponse[UserCreateData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user, password = get_security_service().create_user(
        login_user_id=payload.login_user_id,
        display_name=payload.display_name,
        role_ids=payload.role_ids,
        temporary_password=payload.temporary_password,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=UserCreateData(user=_user_data(user), temporary_password=password))


@router.get("/security/users/{user_uuid}", response_model=ApiResponse[UserData])
def get_user(user_uuid: str) -> ApiResponse[UserData]:
    user = get_security_service().store.get_user(user_uuid)
    if user is None:
        from .service import SecurityApiError

        raise SecurityApiError(404, "ユーザーが見つかりません。")
    return ApiResponse(data=_user_data(user))


@router.patch("/security/users/{user_uuid}", response_model=ApiResponse[UserData])
def update_user(
    user_uuid: str,
    payload: UserUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[UserData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user = get_security_service().update_user(
        user_uuid,
        expected_version=payload.version,
        display_name=payload.display_name,
        status=payload.status,
        role_ids=payload.role_ids,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    response.headers["ETag"] = f'"{user.version}"'
    return ApiResponse(data=_user_data(user))


@router.delete("/security/users/{user_uuid}", response_model=ApiResponse[UserDeleteData])
def delete_user(
    user_uuid: str,
    request: Request,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ApiResponse[UserDeleteData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    deleted = get_security_service().delete_user(
        user_uuid,
        expected_version=_expected_version(if_match),
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(
        data=UserDeleteData(
            user_uuid=deleted.user_uuid,
            login_user_id=deleted.login_user_id,
        )
    )


@router.post(
    "/security/users/{user_uuid}/reset-password", response_model=ApiResponse[PasswordResetData]
)
def reset_password(
    user_uuid: str,
    payload: PasswordResetRequest,
    request: Request,
) -> ApiResponse[PasswordResetData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user, password = get_security_service().reset_password(
        user_uuid,
        payload.temporary_password,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=PasswordResetData(user=_user_data(user), temporary_password=password))


@router.post("/security/users/{user_uuid}/unlock", response_model=ApiResponse[UserData])
def unlock_user(user_uuid: str, request: Request) -> ApiResponse[UserData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user = get_security_service().unlock_user(
        user_uuid,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=_user_data(user))


def _change_user_status(
    user_uuid: str,
    payload: VersionRequest,
    request: Request,
    status: str,
) -> ApiResponse[UserData]:
    service = get_security_service()
    current = service.store.get_user(user_uuid)
    if current is None:
        from .service import SecurityApiError

        raise SecurityApiError(404, "ユーザーが見つかりません。")
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    updated = service.update_user(
        user_uuid,
        expected_version=payload.version,
        display_name=current.display_name,
        status=status,
        role_ids=current.role_ids,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=_user_data(updated))


@router.post("/security/users/{user_uuid}/enable", response_model=ApiResponse[UserData])
def enable_user(user_uuid: str, payload: VersionRequest, request: Request) -> ApiResponse[UserData]:
    return _change_user_status(user_uuid, payload, request, "ACTIVE")


@router.post("/security/users/{user_uuid}/disable", response_model=ApiResponse[UserData])
def disable_user(
    user_uuid: str,
    payload: VersionRequest,
    request: Request,
) -> ApiResponse[UserData]:
    return _change_user_status(user_uuid, payload, request, "DISABLED")


@router.get("/security/roles", response_model=ApiResponse[list[RoleData]])
def list_roles(
    request: Request, include_archived: bool = Query(default=False)
) -> ApiResponse[list[RoleData]]:
    service = get_security_service()
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        roles = service.list_roles_for_actor(
            principal,
            include_archived=include_archived,
        )
    else:
        roles = service.list_roles(include_archived=include_archived)
    return ApiResponse(data=[RoleData.from_record(role) for role in roles])


@router.post("/security/roles", response_model=ApiResponse[RoleData])
def create_role(payload: RoleCreateRequest, request: Request) -> ApiResponse[RoleData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = get_security_service().create_role(
        role_code=payload.role_code,
        display_name=payload.display_name,
        description=payload.description,
        permissions=set(payload.permissions),
        entitlements=[
            (item.resource_code, item.scope_code, item.capability)
            for item in payload.data_entitlements
        ],
        allowed_profile_ids=(
            set(payload.allowed_profile_ids) if payload.allowed_profile_ids is not None else None
        ),
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=RoleData.from_record(role))


@router.get("/security/roles/{role_id}", response_model=ApiResponse[RoleData])
def get_role(role_id: str, request: Request) -> ApiResponse[RoleData]:
    service = get_security_service()
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        role = service.get_role_for_actor(role_id, principal)
    else:
        role = service.store.get_role(role_id)
    if role is None:
        from .service import SecurityApiError

        raise SecurityApiError(404, "ロールが見つかりません。")
    return ApiResponse(data=RoleData.from_record(role))


@router.patch("/security/roles/{role_id}", response_model=ApiResponse[RoleData])
def update_role(
    role_id: str,
    payload: RoleUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[RoleData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = get_security_service().update_role(
        role_id,
        expected_version=payload.version,
        display_name=payload.display_name,
        description=payload.description,
        permissions=set(payload.permissions),
        entitlements=[
            (item.resource_code, item.scope_code, item.capability)
            for item in payload.data_entitlements
        ],
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
    """ロール管理画面向けに業務 profile の利用権限カタログを返す。"""
    from app.features.nl2sql.service import nl2sql_service

    profiles = nl2sql_service.list_profiles(include_archived=include_archived)
    profile_ids = {profile.id for profile in profiles}
    roles = get_security_service().list_roles(include_archived=True)
    allowed_roles_by_profile: dict[str, list[str]] = {profile_id: [] for profile_id in profile_ids}
    for role in roles:
        if role.archived:
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


@router.post("/security/roles/{role_id}/archive", response_model=ApiResponse[RoleData])
def archive_role(
    role_id: str,
    payload: RoleArchiveRequest,
    request: Request,
) -> ApiResponse[RoleData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = get_security_service().archive_role(
        role_id,
        expected_version=payload.version,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=RoleData.from_record(role))


@router.post("/security/roles/{role_id}/restore", response_model=ApiResponse[RoleData])
def restore_role(
    role_id: str,
    payload: RoleRestoreRequest,
    request: Request,
) -> ApiResponse[RoleData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = get_security_service().restore_role(
        role_id,
        expected_version=payload.version,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=RoleData.from_record(role))


@router.delete("/security/roles/{role_id}", response_model=ApiResponse[RoleDeleteData])
def delete_role(
    role_id: str,
    request: Request,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ApiResponse[RoleDeleteData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    deleted = get_security_service().delete_role(
        role_id,
        expected_version=_expected_version(if_match),
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(
        data=RoleDeleteData(
            role_id=deleted.role_id,
            role_code=deleted.role_code,
        )
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
