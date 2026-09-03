"""認証/RBAC の内部ドメイン型。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SYSTEM_ADMIN_ROLE_CODE = "SYSTEM_ADMIN"
SYSTEM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000001"
SCOPE_FILTER_CODE_PREFIX = "FILTERS:"
LEGACY_APP_USER_ID_SCOPE_VALUE_SOURCE = "APP_USER_ID"
LOGIN_USER_ID_SCOPE_VALUE_SOURCE = "LOGIN_USER_ID"


@dataclass(slots=True)
class DataEntitlementScopeFilter:
    column_name: str
    operator: str
    value_type: str = "TEXT"
    value_source: str = "LITERAL"
    value: str = ""
    value_to: str = ""
    values: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DataEntitlementRecord:
    entitlement_id: str
    role_id: str
    resource_code: str
    scope_code: str
    capability: str
    target_owner: str = ""
    target_object: str = ""
    target_type: str = "TABLE"
    column_names: list[str] = field(default_factory=list)
    scope_mode: str = "ALL"
    scope_column: str = ""
    scope_filters: list[DataEntitlementScopeFilter] = field(default_factory=list)
    data_grant_name: str = ""
    sql_checksum: str = ""
    apply_status: str = "PENDING"
    apply_error_message: str = ""
    applied_at: datetime | None = None


@dataclass(slots=True)
class RoleRecord:
    role_id: str
    role_code: str
    display_name: str
    description: str
    is_built_in: bool
    archived: bool
    version: int
    permissions: set[str] = field(default_factory=set)
    entitlements: list[DataEntitlementRecord] = field(default_factory=list)
    allowed_profile_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class UserRecord:
    user_uuid: str
    login_user_id: str
    display_name: str
    password_hash: str
    status: str
    force_password_change: bool
    failed_login_count: int
    locked_until: datetime | None
    version: int
    role_ids: list[str] = field(default_factory=list)
    is_bootstrap_admin: bool = False


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    user_uuid: str
    token_hash: str
    csrf_token_hash: str
    idle_expires_at: datetime
    absolute_expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None = None


@dataclass(slots=True)
class Principal:
    user_uuid: str
    login_user_id: str
    display_name: str
    status: str
    force_password_change: bool
    role_codes: list[str]
    permissions: set[str]
    data_entitlements: list[DataEntitlementRecord]
    allowed_profile_ids: set[str]
    session_id: str
    csrf_token_hash: str
    password_change_allowed: bool = True

    @property
    def is_system_admin(self) -> bool:
        return SYSTEM_ADMIN_ROLE_CODE in self.role_codes

    def has_permission(self, permission: str) -> bool:
        return self.is_system_admin or permission in self.permissions

    def has_any_permission(self, permissions: set[str] | frozenset[str]) -> bool:
        return self.is_system_admin or bool(self.permissions.intersection(permissions))

    def can_use_profile(self, profile_id: str | None) -> bool:
        if self.is_system_admin or "nl2sql.profiles.manage" in self.permissions:
            return True
        normalized = str(profile_id or "").strip()
        return bool(normalized and normalized in self.allowed_profile_ids)


def scope_filter_payload(filter_item: DataEntitlementScopeFilter) -> dict[str, object]:
    value_source = filter_item.value_source.strip().upper() or "LITERAL"
    if value_source == LEGACY_APP_USER_ID_SCOPE_VALUE_SOURCE:
        value_source = LOGIN_USER_ID_SCOPE_VALUE_SOURCE
    payload: dict[str, object] = {
        "column_name": filter_item.column_name.strip().upper(),
        "operator": filter_item.operator.strip().upper(),
        "value_type": filter_item.value_type.strip().upper(),
        "value": filter_item.value.strip(),
        "value_to": filter_item.value_to.strip(),
        "values": [str(item).strip() for item in filter_item.values if str(item).strip()],
    }
    if value_source != "LITERAL":
        payload["value_source"] = value_source
    return payload


def scope_filters_canonical_json(filters: list[DataEntitlementScopeFilter]) -> str:
    return json.dumps(
        [scope_filter_payload(item) for item in filters],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def scope_filters_scope_code(filters: list[DataEntitlementScopeFilter]) -> str:
    digest = hashlib.sha256(scope_filters_canonical_json(filters).encode("utf-8")).hexdigest()
    return f"{SCOPE_FILTER_CODE_PREFIX}{digest[:32].upper()}"


def scope_filter_from_mapping(value: Mapping[str, Any]) -> DataEntitlementScopeFilter:
    raw_values = value.get("values", [])
    values = raw_values if isinstance(raw_values, list) else []
    value_source = str(value.get("value_source") or "LITERAL")
    if value_source.strip().upper() == LEGACY_APP_USER_ID_SCOPE_VALUE_SOURCE:
        value_source = LOGIN_USER_ID_SCOPE_VALUE_SOURCE
    return DataEntitlementScopeFilter(
        column_name=str(value.get("column_name") or ""),
        operator=str(value.get("operator") or ""),
        value_type=str(value.get("value_type") or "TEXT"),
        value_source=value_source,
        value=str(value.get("value") or ""),
        value_to=str(value.get("value_to") or ""),
        values=[str(item) for item in values],
    )


def scope_filters_from_json(value: object) -> list[DataEntitlementScopeFilter]:
    if value in (None, "", "[]"):
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    filters: list[DataEntitlementScopeFilter] = []
    for item in parsed:
        if isinstance(item, Mapping):
            filters.append(scope_filter_from_mapping(item))
    return filters
