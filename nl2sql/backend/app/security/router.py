"""認証、ユーザー、ロール、監査、DeepSec API。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from pr_backend_core import ApiResponse
from starlette.concurrency import run_in_threadpool

from app.settings import get_settings

from .deepsec import get_deepsec_service
from .dependencies import current_principal, local_debug_principal, request_context
from .permissions import PERMISSION_CATALOG
from .schemas import (
    AuditData,
    AuditPageData,
    CurrentUserData,
    DeepSecApplyRequest,
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetData,
    PasswordResetRequest,
    PermissionData,
    RoleArchiveRequest,
    RoleCreateRequest,
    RoleData,
    RoleUpdateRequest,
    UserCreateData,
    UserCreateRequest,
    UserData,
    UserUpdateRequest,
    VersionRequest,
)
from .service import get_security_service

router = APIRouter(tags=["security"])


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


@router.post("/auth/login", response_model=ApiResponse[CurrentUserData])
async def login(
    payload: LoginRequest, request: Request, response: Response
) -> ApiResponse[CurrentUserData]:
    if get_settings().local_debug_enabled:
        return ApiResponse(
            data=CurrentUserData.from_principal(
                local_debug_principal(), debug_mode=True
            )
        )
    request_id, client_ip = request_context(request)
    principal, session_token, csrf_token = await run_in_threadpool(
        get_security_service().login,
        payload.login_name,
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
async def me(request: Request) -> ApiResponse[CurrentUserData]:
    settings = get_settings()
    return ApiResponse(
        data=CurrentUserData.from_principal(
            current_principal(request), debug_mode=settings.local_debug_enabled
        )
    )


@router.post("/auth/logout", response_model=ApiResponse[dict[str, bool]])
async def logout(request: Request, response: Response) -> ApiResponse[dict[str, bool]]:
    if get_settings().local_debug_enabled:
        return ApiResponse(data={"logged_out": False})
    principal = current_principal(request)
    request_id, client_ip = request_context(request)
    await run_in_threadpool(
        get_security_service().logout,
        principal,
        request_id=request_id,
        client_ip=client_ip,
    )
    settings = get_settings()
    response.delete_cookie(settings.app_auth_session_cookie_name, path="/")
    response.delete_cookie(settings.app_auth_csrf_cookie_name, path="/")
    return ApiResponse(data={"logged_out": True})


@router.post("/auth/password/change", response_model=ApiResponse[dict[str, bool]])
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
) -> ApiResponse[dict[str, bool]]:
    if get_settings().local_debug_enabled:
        from .service import SecurityApiError

        raise SecurityApiError(409, "ローカル DEBUG モードではパスワードを変更できません。")
    principal = current_principal(request)
    request_id, client_ip = request_context(request)
    await run_in_threadpool(
        get_security_service().change_password,
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
async def list_users() -> ApiResponse[list[UserData]]:
    users = await run_in_threadpool(get_security_service().list_users)
    return ApiResponse(data=[UserData.from_record(user) for user in users])


@router.post("/security/users", response_model=ApiResponse[UserCreateData])
async def create_user(payload: UserCreateRequest, request: Request) -> ApiResponse[UserCreateData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user, password = await run_in_threadpool(
        get_security_service().create_user,
        login_name=payload.login_name,
        display_name=payload.display_name,
        role_ids=payload.role_ids,
        temporary_password=payload.temporary_password,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(
        data=UserCreateData(user=UserData.from_record(user), temporary_password=password)
    )


@router.get("/security/users/{user_id}", response_model=ApiResponse[UserData])
async def get_user(user_id: str) -> ApiResponse[UserData]:
    user = await run_in_threadpool(get_security_service().store.get_user, user_id)
    if user is None:
        from .service import SecurityApiError

        raise SecurityApiError(404, "ユーザーが見つかりません。")
    return ApiResponse(data=UserData.from_record(user))


@router.patch("/security/users/{user_id}", response_model=ApiResponse[UserData])
async def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[UserData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user = await run_in_threadpool(
        get_security_service().update_user,
        user_id,
        expected_version=payload.version,
        display_name=payload.display_name,
        status=payload.status,
        role_ids=payload.role_ids,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    response.headers["ETag"] = f'"{user.version}"'
    return ApiResponse(data=UserData.from_record(user))


@router.post(
    "/security/users/{user_id}/reset-password", response_model=ApiResponse[PasswordResetData]
)
async def reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    request: Request,
) -> ApiResponse[PasswordResetData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user, password = await run_in_threadpool(
        get_security_service().reset_password,
        user_id,
        payload.temporary_password,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(
        data=PasswordResetData(user=UserData.from_record(user), temporary_password=password)
    )


@router.post("/security/users/{user_id}/unlock", response_model=ApiResponse[UserData])
async def unlock_user(user_id: str, request: Request) -> ApiResponse[UserData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    user = await run_in_threadpool(
        get_security_service().unlock_user,
        user_id,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=UserData.from_record(user))


async def _change_user_status(
    user_id: str,
    payload: VersionRequest,
    request: Request,
    status: str,
) -> ApiResponse[UserData]:
    service = get_security_service()
    current = await run_in_threadpool(service.store.get_user, user_id)
    if current is None:
        from .service import SecurityApiError

        raise SecurityApiError(404, "ユーザーが見つかりません。")
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    updated = await run_in_threadpool(
        service.update_user,
        user_id,
        expected_version=payload.version,
        display_name=current.display_name,
        status=status,
        role_ids=current.role_ids,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=UserData.from_record(updated))


@router.post("/security/users/{user_id}/enable", response_model=ApiResponse[UserData])
async def enable_user(
    user_id: str, payload: VersionRequest, request: Request
) -> ApiResponse[UserData]:
    return await _change_user_status(user_id, payload, request, "ACTIVE")


@router.post("/security/users/{user_id}/disable", response_model=ApiResponse[UserData])
async def disable_user(
    user_id: str, payload: VersionRequest, request: Request
) -> ApiResponse[UserData]:
    return await _change_user_status(user_id, payload, request, "DISABLED")


@router.get("/security/roles", response_model=ApiResponse[list[RoleData]])
async def list_roles(include_archived: bool = Query(default=False)) -> ApiResponse[list[RoleData]]:
    roles = await run_in_threadpool(
        get_security_service().list_roles,
        include_archived=include_archived,
    )
    return ApiResponse(data=[RoleData.from_record(role) for role in roles])


@router.post("/security/roles", response_model=ApiResponse[RoleData])
async def create_role(payload: RoleCreateRequest, request: Request) -> ApiResponse[RoleData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = await run_in_threadpool(
        get_security_service().create_role,
        role_code=payload.role_code,
        display_name=payload.display_name,
        description=payload.description,
        permissions=set(payload.permissions),
        entitlements=[
            (item.resource_code, item.scope_code, item.capability)
            for item in payload.data_entitlements
        ],
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=RoleData.from_record(role))


@router.get("/security/roles/{role_id}", response_model=ApiResponse[RoleData])
async def get_role(role_id: str) -> ApiResponse[RoleData]:
    role = await run_in_threadpool(get_security_service().store.get_role, role_id)
    if role is None:
        from .service import SecurityApiError

        raise SecurityApiError(404, "ロールが見つかりません。")
    return ApiResponse(data=RoleData.from_record(role))


@router.patch("/security/roles/{role_id}", response_model=ApiResponse[RoleData])
async def update_role(
    role_id: str,
    payload: RoleUpdateRequest,
    request: Request,
    response: Response,
) -> ApiResponse[RoleData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = await run_in_threadpool(
        get_security_service().update_role,
        role_id,
        expected_version=payload.version,
        display_name=payload.display_name,
        description=payload.description,
        permissions=set(payload.permissions),
        entitlements=[
            (item.resource_code, item.scope_code, item.capability)
            for item in payload.data_entitlements
        ],
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    response.headers["ETag"] = f'"{role.version}"'
    return ApiResponse(data=RoleData.from_record(role))


@router.post("/security/roles/{role_id}/archive", response_model=ApiResponse[RoleData])
async def archive_role(
    role_id: str,
    payload: RoleArchiveRequest,
    request: Request,
) -> ApiResponse[RoleData]:
    actor = current_principal(request)
    request_id, client_ip = request_context(request)
    role = await run_in_threadpool(
        get_security_service().archive_role,
        role_id,
        expected_version=payload.version,
        actor=actor,
        request_id=request_id,
        client_ip=client_ip,
    )
    return ApiResponse(data=RoleData.from_record(role))


@router.get("/security/permissions", response_model=ApiResponse[list[PermissionData]])
async def permission_catalog() -> ApiResponse[list[PermissionData]]:
    return ApiResponse(data=[PermissionData.from_definition(item) for item in PERMISSION_CATALOG])


@router.get("/security/audit", response_model=ApiResponse[list[AuditData]])
async def audit_log(limit: int = Query(default=200, ge=1, le=500)) -> ApiResponse[list[AuditData]]:
    records = await run_in_threadpool(get_security_service().store.list_audit, limit=limit)
    return ApiResponse(data=[AuditData.from_record(record) for record in records])


@router.get("/security/audit/page", response_model=ApiResponse[AuditPageData])
async def audit_log_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> ApiResponse[AuditPageData]:
    records, total, resolved_page = await run_in_threadpool(
        get_security_service().get_audit_page,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return ApiResponse(
        data=AuditPageData(
            items=[AuditData.from_record(record) for record in records],
            page=resolved_page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
    )


@router.get("/security/audit/export.xlsx")
async def export_audit_log_xlsx() -> Response:
    filename, content = await run_in_threadpool(
        get_security_service().export_audit_log_xlsx
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/security/deepsec/status", response_model=ApiResponse[dict[str, object]])
async def deepsec_status() -> ApiResponse[dict[str, object]]:
    return ApiResponse(data=await run_in_threadpool(get_deepsec_service().status))


@router.get("/security/deepsec/plan", response_model=ApiResponse[dict[str, object]])
async def deepsec_plan() -> ApiResponse[dict[str, object]]:
    return ApiResponse(data=await run_in_threadpool(get_deepsec_service().plan))


@router.post(
    "/security/deepsec/plan/{version}/steps/{step_no}/apply",
    response_model=ApiResponse[dict[str, object]],
)
async def apply_deepsec_step(
    version: str,
    step_no: int,
    payload: DeepSecApplyRequest,
    request: Request,
) -> ApiResponse[dict[str, object]]:
    if version != "V001":
        from .service import SecurityApiError

        raise SecurityApiError(404, "DeepSec plan version が見つかりません。")
    result = await run_in_threadpool(
        get_deepsec_service().apply_step,
        step_no,
        payload.checksum,
        current_principal(request),
    )
    return ApiResponse(data=result)


@router.post("/security/deepsec/verify", response_model=ApiResponse[dict[str, object]])
async def verify_deepsec(request: Request) -> ApiResponse[dict[str, object]]:
    return ApiResponse(
        data=await run_in_threadpool(
            get_deepsec_service().verify,
            current_principal(request),
        )
    )
