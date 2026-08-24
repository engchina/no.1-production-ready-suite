"""認証/RBAC API schema。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from .domain import (
    LEGACY_APP_USER_ID_SCOPE_VALUE_SOURCE,
    LOGIN_USER_ID_SCOPE_VALUE_SOURCE,
    DataEntitlementRecord,
    DataEntitlementScopeFilter,
    Principal,
    RoleRecord,
    UserRecord,
    scope_filters_scope_code,
)
from .permissions import PermissionDefinition, normalize_permission_codes

_ORACLE_IDENTIFIER_RE = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}")
_APPLY_STATUSES = {"PENDING", "RUNNING", "APPLIED", "FAILED"}
_SCOPE_FILTER_OPERATORS = {
    "EQ",
    "NE",
    "CONTAINS",
    "STARTS_WITH",
    "IN",
    "GT",
    "GTE",
    "LT",
    "LTE",
    "BETWEEN",
    "BEFORE",
    "ON_OR_BEFORE",
    "AFTER",
    "ON_OR_AFTER",
    "IS_NULL",
    "IS_NOT_NULL",
}
_SCOPE_FILTER_VALUE_TYPES = {"TEXT", "NUMBER", "TEMPORAL"}
_SCOPE_FILTER_VALUE_SOURCES = {"LITERAL", LOGIN_USER_ID_SCOPE_VALUE_SOURCE}
_POSITIVE_INTEGER_VALUE_RE = re.compile(r"[1-9]\d*")
_LOGIN_USER_ID_RE = re.compile(r"(?=.*[A-Za-z0-9])[A-Za-z0-9._-]{1,64}")
_MAX_SCOPE_FILTERS = 8
_MAX_SCOPE_FILTER_VALUES = 25


def _normalize_oracle_identifier(value: str, field_name: str) -> str:
    normalized = value.strip().strip('"').upper()
    if not _ORACLE_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} は有効な Oracle identifier で指定してください。")
    return normalized


class LoginRequest(BaseModel):
    login_user_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class DataEntitlementScopeFilterInput(BaseModel):
    column_name: str = Field(min_length=1, max_length=128)
    operator: str = Field(min_length=1, max_length=32)
    value_type: str = Field(default="TEXT", max_length=32)
    value_source: str = Field(default="LITERAL", max_length=32)
    value: str = Field(default="", max_length=512)
    value_to: str = Field(default="", max_length=512)
    values: list[str] = Field(default_factory=list, max_length=_MAX_SCOPE_FILTER_VALUES)

    @field_validator("column_name")
    @classmethod
    def normalize_column_name(cls, value: str) -> str:
        return _normalize_oracle_identifier(value, "column_name")

    @field_validator("operator")
    @classmethod
    def normalize_operator(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _SCOPE_FILTER_OPERATORS:
            raise ValueError("scope filter operator が不正です。")
        return normalized

    @field_validator("value_type")
    @classmethod
    def normalize_value_type(cls, value: str) -> str:
        normalized = value.strip().upper() or "TEXT"
        if normalized not in _SCOPE_FILTER_VALUE_TYPES:
            raise ValueError("scope filter value_type が不正です。")
        return normalized

    @field_validator("value_source")
    @classmethod
    def normalize_value_source(cls, value: str) -> str:
        normalized = value.strip().upper() or "LITERAL"
        if normalized == LEGACY_APP_USER_ID_SCOPE_VALUE_SOURCE:
            return LOGIN_USER_ID_SCOPE_VALUE_SOURCE
        if normalized not in _SCOPE_FILTER_VALUE_SOURCES:
            raise ValueError("scope filter value_source が不正です。")
        return normalized

    @field_validator("value", "value_to")
    @classmethod
    def normalize_filter_value(cls, value: str) -> str:
        normalized = value.strip()
        if any(ord(char) < 32 for char in normalized):
            raise ValueError("scope filter value に制御文字は使用できません。")
        return normalized

    @field_validator("values")
    @classmethod
    def normalize_filter_values(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            normalized_item = str(item).strip()
            if not normalized_item:
                continue
            if any(ord(char) < 32 for char in normalized_item):
                raise ValueError("scope filter values に制御文字は使用できません。")
            if normalized_item not in normalized:
                normalized.append(normalized_item)
        return normalized[:_MAX_SCOPE_FILTER_VALUES]

    @model_validator(mode="after")
    def validate_value_source_contract(self) -> DataEntitlementScopeFilterInput:
        if self.value_source == LOGIN_USER_ID_SCOPE_VALUE_SOURCE:
            if self.operator != "EQ" or self.value_type not in {"TEXT", "NUMBER"}:
                raise ValueError(
                    "ログインユーザーID は文字列列または NUMBER 列の EQ 条件でのみ指定できます。"
                )
            self.value = ""
            self.value_to = ""
            self.values = []
        elif (
            self.value_source == "LITERAL"
            and self.value_type == "NUMBER"
            and self.operator == "EQ"
            and not _POSITIVE_INTEGER_VALUE_RE.fullmatch(self.value)
        ):
            raise ValueError("NUMBER の EQ scope 値は正整数で指定してください。")
        return self

    def to_record(self) -> DataEntitlementScopeFilter:
        return DataEntitlementScopeFilter(
            column_name=self.column_name,
            operator=self.operator,
            value_type=self.value_type,
            value_source=self.value_source,
            value=self.value,
            value_to=self.value_to,
            values=list(self.values),
        )


class DataEntitlementInput(BaseModel):
    entitlement_id: str | None = Field(default=None, max_length=36)
    resource_code: str = Field(default="", max_length=261)
    scope_code: str = Field(default="*", max_length=256)
    capability: str = Field(min_length=1, max_length=64)
    target_owner: str = Field(default="", max_length=128)
    target_object: str = Field(default="", max_length=128)
    target_type: str = Field(default="TABLE", max_length=32)
    column_names: list[str] = Field(default_factory=list)
    scope_mode: str = Field(default="ALL", max_length=32)
    scope_column: str = Field(default="", max_length=128)
    scope_filters: list[DataEntitlementScopeFilterInput] = Field(
        default_factory=list,
        max_length=_MAX_SCOPE_FILTERS,
    )

    @field_validator("resource_code")
    @classmethod
    def normalize_resource_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            return ""
        if not re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,260}", normalized):
            raise ValueError("英大文字・数字・アンダースコア等で指定してください。")
        return normalized

    @field_validator("capability")
    @classmethod
    def normalize_capability(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "SELECT":
            raise ValueError("capability は SELECT を指定してください。")
        return normalized

    @field_validator("scope_code")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        normalized = value.strip() or "*"
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("有効なデータ範囲を指定してください。")
        return normalized

    @field_validator("target_owner", "target_object", "scope_column")
    @classmethod
    def normalize_optional_identifier(cls, value: str, info) -> str:
        if not value.strip():
            return ""
        return _normalize_oracle_identifier(value, info.field_name)

    @field_validator("target_type")
    @classmethod
    def normalize_target_type(cls, value: str) -> str:
        normalized = value.strip().upper() or "TABLE"
        if normalized not in {"TABLE", "VIEW", "MATERIALIZED VIEW"}:
            raise ValueError("target_type は TABLE、VIEW、MATERIALIZED VIEW のいずれかです。")
        return normalized

    @field_validator("column_names")
    @classmethod
    def normalize_column_names(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for column in value:
            if not str(column).strip():
                continue
            normalized_column = _normalize_oracle_identifier(str(column), "column_names")
            if normalized_column not in normalized:
                normalized.append(normalized_column)
        return normalized

    @field_validator("scope_mode")
    @classmethod
    def normalize_scope_mode(cls, value: str) -> str:
        normalized = value.strip().upper() or "ALL"
        if normalized not in {"ALL", "COLUMN_EQUALS", "FILTERS"}:
            raise ValueError("scope_mode は ALL、COLUMN_EQUALS、FILTERS のいずれかです。")
        return normalized

    def to_record(self, role_id: str) -> DataEntitlementRecord:
        scope_filters = [item.to_record() for item in self.scope_filters]
        scope_code = self.scope_code
        if self.scope_mode == "ALL":
            scope_code = "*"
        elif self.scope_mode == "FILTERS":
            scope_code = scope_filters_scope_code(scope_filters)
        return DataEntitlementRecord(
            entitlement_id=self.entitlement_id or "",
            role_id=role_id,
            resource_code=self.resource_code,
            scope_code=scope_code,
            capability=self.capability,
            target_owner=self.target_owner,
            target_object=self.target_object,
            target_type=self.target_type,
            column_names=list(self.column_names),
            scope_mode=self.scope_mode,
            scope_column=self.scope_column,
            scope_filters=scope_filters,
        )


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


class RoleRestoreRequest(BaseModel):
    version: int = Field(ge=1)


class UserCreateRequest(BaseModel):
    login_user_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    role_ids: list[str] = Field(default_factory=list)
    temporary_password: str | None = Field(default=None, max_length=256)

    @field_validator("login_user_id")
    @classmethod
    def validate_login_user_id(cls, value: str) -> str:
        normalized = value.strip()
        if not _LOGIN_USER_ID_RE.fullmatch(normalized):
            raise ValueError(
                "ログインユーザーIDは英数字を1文字以上含め、英数字と . _ - を使い"
                "1～64 文字で入力してください。"
            )
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
    confirmation: str = Field(default="", max_length=128)


class DeepSecResetRequest(BaseModel):
    confirmation: str = Field(default="", max_length=128)


class DeepSecConfigUpdate(BaseModel):
    data_user_password: str = Field(min_length=12, max_length=256)

    @field_validator("data_user_password")
    @classmethod
    def validate_data_user_password(cls, value: str) -> str:
        if '"' in value or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError(
                "ORACLE_DEEPSEC_DATA_USER_PASSWORD は二重引用符と制御文字を"
                "含めずに指定してください。"
            )
        return value


class DataEntitlementData(BaseModel):
    entitlement_id: str
    resource_code: str
    scope_code: str
    capability: str
    target_owner: str = ""
    target_object: str = ""
    target_type: str = "TABLE"
    column_names: list[str] = Field(default_factory=list)
    scope_mode: str = "ALL"
    scope_column: str = ""
    scope_filters: list[DataEntitlementScopeFilterInput] = Field(default_factory=list)
    data_grant_name: str = ""
    sql_checksum: str = ""
    apply_status: str = "PENDING"
    apply_error_message: str = ""
    applied_at: datetime | None = None
    sql: list[str] = Field(default_factory=list)
    checksum: str = ""

    @classmethod
    def from_record(
        cls,
        record: DataEntitlementRecord,
        *,
        sql: list[str] | None = None,
        checksum: str = "",
    ) -> DataEntitlementData:
        return cls(
            entitlement_id=record.entitlement_id,
            resource_code=record.resource_code,
            scope_code=record.scope_code,
            capability=record.capability,
            target_owner=record.target_owner,
            target_object=record.target_object,
            target_type=record.target_type,
            column_names=list(record.column_names),
            scope_mode=record.scope_mode,
            scope_column=record.scope_column,
            scope_filters=[
                DataEntitlementScopeFilterInput(
                    column_name=item.column_name,
                    operator=item.operator,
                    value_type=item.value_type,
                    value_source=item.value_source,
                    value=item.value,
                    value_to=item.value_to,
                    values=list(item.values),
                )
                for item in record.scope_filters
            ],
            data_grant_name=record.data_grant_name,
            sql_checksum=record.sql_checksum,
            apply_status=(
                record.apply_status if record.apply_status in _APPLY_STATUSES else "PENDING"
            ),
            apply_error_message=record.apply_error_message,
            applied_at=record.applied_at,
            sql=list(sql or []),
            checksum=checksum or record.sql_checksum,
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
            permissions=sorted(normalize_permission_codes(role.permissions)),
            data_entitlements=[DataEntitlementData.from_record(item) for item in role.entitlements],
        )


class DeepSecDataEntitlementUpdateRequest(BaseModel):
    version: int = Field(ge=1)
    data_entitlements: list[DataEntitlementInput] = Field(default_factory=list)


class DeepSecDataEntitlementPreviewRequest(BaseModel):
    data_entitlements: list[DataEntitlementInput] = Field(default_factory=list)


class DeepSecDataEntitlementPreviewData(BaseModel):
    role_id: str
    data_entitlements: list[DataEntitlementData]


class DeepSecDataEntitlementApplyRequest(BaseModel):
    confirmation: str = Field(default="", max_length=128)
    entitlement_ids: list[str] = Field(default_factory=list)


class DeepSecRoleEntitlementsData(BaseModel):
    role_id: str
    role_code: str
    display_name: str
    description: str
    is_built_in: bool
    archived: bool
    version: int
    data_entitlements: list[DataEntitlementData]

    @classmethod
    def from_record(cls, role: RoleRecord) -> DeepSecRoleEntitlementsData:
        return cls(
            role_id=role.role_id,
            role_code=role.role_code,
            display_name=role.display_name,
            description=role.description,
            is_built_in=role.is_built_in,
            archived=role.archived,
            version=role.version,
            data_entitlements=[DataEntitlementData.from_record(item) for item in role.entitlements],
        )


class AssignedRoleData(BaseModel):
    role_id: str
    role_code: str
    display_name: str
    is_built_in: bool
    archived: bool

    @classmethod
    def from_record(cls, role: RoleRecord) -> AssignedRoleData:
        return cls(
            role_id=role.role_id,
            role_code=role.role_code,
            display_name=role.display_name,
            is_built_in=role.is_built_in,
            archived=role.archived,
        )

    @classmethod
    def unresolved(cls, role_id: str) -> AssignedRoleData:
        return cls(
            role_id=role_id,
            role_code=role_id,
            display_name=role_id,
            is_built_in=False,
            archived=True,
        )


class UserData(BaseModel):
    user_uuid: str
    login_user_id: str
    display_name: str
    status: str
    force_password_change: bool
    locked_until: datetime | None
    version: int
    role_ids: list[str]
    assigned_roles: list[AssignedRoleData]
    is_bootstrap_admin: bool

    @classmethod
    def from_record(
        cls,
        user: UserRecord,
        *,
        roles_by_id: Mapping[str, RoleRecord] | None = None,
    ) -> UserData:
        role_lookup = roles_by_id or {}
        return cls(
            user_uuid=user.user_uuid,
            login_user_id=user.login_user_id,
            display_name=user.display_name,
            status=user.status,
            force_password_change=user.force_password_change,
            locked_until=user.locked_until,
            version=user.version,
            role_ids=user.role_ids,
            assigned_roles=[
                (
                    AssignedRoleData.from_record(role_lookup[role_id])
                    if role_id in role_lookup
                    else AssignedRoleData.unresolved(role_id)
                )
                for role_id in user.role_ids
            ],
            is_bootstrap_admin=user.is_bootstrap_admin,
        )


class UserCreateData(BaseModel):
    user: UserData
    temporary_password: str


class PasswordResetData(BaseModel):
    user: UserData
    temporary_password: str


class CurrentUserData(BaseModel):
    user_uuid: str
    login_user_id: str
    display_name: str
    status: str
    force_password_change: bool
    role_codes: list[str]
    is_system_admin: bool
    permissions: list[str]
    data_entitlements: list[DataEntitlementData]
    debug_mode: bool = False
    password_change_allowed: bool

    @classmethod
    def from_principal(cls, principal: Principal, *, debug_mode: bool = False) -> CurrentUserData:
        return cls(
            user_uuid=principal.user_uuid,
            login_user_id=principal.login_user_id,
            display_name=principal.display_name,
            status=principal.status,
            force_password_change=principal.force_password_change,
            role_codes=principal.role_codes,
            is_system_admin=principal.is_system_admin,
            permissions=sorted(principal.permissions),
            data_entitlements=[
                DataEntitlementData.from_record(item) for item in principal.data_entitlements
            ],
            debug_mode=debug_mode,
            password_change_allowed=principal.password_change_allowed and not debug_mode,
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
