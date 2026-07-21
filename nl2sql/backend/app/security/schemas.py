"""認証/RBAC API schema。"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .domain import AuditRecord, DataEntitlementRecord, Principal, RoleRecord, UserRecord
from .permissions import PermissionDefinition


class LoginRequest(BaseModel):
    login_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class DataEntitlementInput(BaseModel):
    resource_code: str = Field(min_length=1, max_length=128)
    scope_code: str = Field(min_length=1, max_length=256)
    capability: str = Field(min_length=1, max_length=64)

    @field_validator("resource_code")
    @classmethod
    def normalize_resource_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,127}", normalized):
            raise ValueError("英大文字・数字・アンダースコア等で指定してください。")
        return normalized

    @field_validator("capability")
    @classmethod
    def normalize_capability(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"ROW_READ", "SENSITIVE_READ", "FULL"}:
            raise ValueError("capability は ROW_READ、SENSITIVE_READ、FULL のいずれかです。")
        return normalized

    @field_validator("scope_code")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("有効なデータ範囲を指定してください。")
        return normalized


class RoleCreateRequest(BaseModel):
    role_code: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=1000)
    permissions: list[str] = Field(default_factory=list)
    data_entitlements: list[DataEntitlementInput] = Field(default_factory=list)

    @field_validator("role_code")
    @classmethod
    def normalize_role_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", normalized):
            raise ValueError("ロールコードは英大文字・数字・アンダースコアで指定してください。")
        return normalized


class RoleUpdateRequest(BaseModel):
    version: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=1000)
    permissions: list[str] = Field(default_factory=list)
    data_entitlements: list[DataEntitlementInput] = Field(default_factory=list)


class RoleArchiveRequest(BaseModel):
    version: int = Field(ge=1)


class UserCreateRequest(BaseModel):
    login_name: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    role_ids: list[str] = Field(default_factory=list)
    temporary_password: str | None = Field(default=None, max_length=256)

    @field_validator("login_name")
    @classmethod
    def validate_login_name(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{3,64}", normalized):
            raise ValueError("ログイン名は英数字と . _ - を使い 3～64 文字で入力してください。")
        return normalized


class UserUpdateRequest(BaseModel):
    version: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=256)
    status: str
    role_ids: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"ACTIVE", "DISABLED"}:
            raise ValueError("status は ACTIVE または DISABLED です。")
        return normalized


class PasswordResetRequest(BaseModel):
    temporary_password: str | None = Field(default=None, max_length=256)


class VersionRequest(BaseModel):
    version: int = Field(ge=1)


class DeepSecApplyRequest(BaseModel):
    checksum: str = Field(min_length=64, max_length=64)


class DataEntitlementData(BaseModel):
    entitlement_id: str
    resource_code: str
    scope_code: str
    capability: str

    @classmethod
    def from_record(cls, record: DataEntitlementRecord) -> DataEntitlementData:
        return cls(
            entitlement_id=record.entitlement_id,
            resource_code=record.resource_code,
            scope_code=record.scope_code,
            capability=record.capability,
        )


class RoleData(BaseModel):
    role_id: str
    role_code: str
    display_name: str
    description: str
    is_built_in: bool
    archived: bool
    version: int
    permissions: list[str]
    data_entitlements: list[DataEntitlementData]

    @classmethod
    def from_record(cls, role: RoleRecord) -> RoleData:
        return cls(
            role_id=role.role_id,
            role_code=role.role_code,
            display_name=role.display_name,
            description=role.description,
            is_built_in=role.is_built_in,
            archived=role.archived,
            version=role.version,
            permissions=sorted(role.permissions),
            data_entitlements=[DataEntitlementData.from_record(item) for item in role.entitlements],
        )


class UserData(BaseModel):
    user_id: str
    login_name: str
    display_name: str
    status: str
    force_password_change: bool
    locked_until: datetime | None
    version: int
    role_ids: list[str]

    @classmethod
    def from_record(cls, user: UserRecord) -> UserData:
        return cls(
            user_id=user.user_id,
            login_name=user.login_name,
            display_name=user.display_name,
            status=user.status,
            force_password_change=user.force_password_change,
            locked_until=user.locked_until,
            version=user.version,
            role_ids=user.role_ids,
        )


class UserCreateData(BaseModel):
    user: UserData
    temporary_password: str


class PasswordResetData(BaseModel):
    user: UserData
    temporary_password: str


class CurrentUserData(BaseModel):
    user_id: str
    login_name: str
    display_name: str
    status: str
    force_password_change: bool
    role_codes: list[str]
    permissions: list[str]
    data_entitlements: list[DataEntitlementData]
    debug_mode: bool = False

    @classmethod
    def from_principal(
        cls, principal: Principal, *, debug_mode: bool = False
    ) -> CurrentUserData:
        return cls(
            user_id=principal.user_id,
            login_name=principal.login_name,
            display_name=principal.display_name,
            status=principal.status,
            force_password_change=principal.force_password_change,
            role_codes=principal.role_codes,
            permissions=sorted(principal.permissions),
            data_entitlements=[
                DataEntitlementData.from_record(item) for item in principal.data_entitlements
            ],
            debug_mode=debug_mode,
        )


class PermissionData(BaseModel):
    code: str
    group: str
    label: str
    description: str
    implies: list[str]

    @classmethod
    def from_definition(cls, definition: PermissionDefinition) -> PermissionData:
        return cls(
            code=definition.code,
            group=definition.group,
            label=definition.label,
            description=definition.description,
            implies=list(definition.implies),
        )


class AuditData(BaseModel):
    audit_id: int
    actor_user_id: str | None
    event_type: str
    target_type: str
    target_id: str
    outcome: str
    detail: dict[str, object]
    request_id: str
    client_ip: str
    created_at: datetime

    @classmethod
    def from_record(cls, record: AuditRecord) -> AuditData:
        return cls(
            audit_id=record.audit_id,
            actor_user_id=record.actor_user_id,
            event_type=record.event_type,
            target_type=record.target_type,
            target_id=record.target_id,
            outcome=record.outcome,
            detail=record.detail,
            request_id=record.request_id,
            client_ip=record.client_ip,
            created_at=record.created_at,
        )


class AuditPageData(BaseModel):
    items: list[AuditData]
    page: int
    page_size: int
    total: int
    total_pages: int
