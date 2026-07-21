"""認証/RBAC の内部ドメイン型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SYSTEM_ADMIN_ROLE_CODE = "SYSTEM_ADMIN"
SYSTEM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000001"


@dataclass(slots=True)
class DataEntitlementRecord:
    entitlement_id: str
    role_id: str
    resource_code: str
    scope_code: str
    capability: str


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


@dataclass(slots=True)
class UserRecord:
    user_id: str
    login_name: str
    display_name: str
    password_hash: str
    status: str
    force_password_change: bool
    failed_login_count: int
    locked_until: datetime | None
    version: int
    role_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    user_id: str
    token_hash: str
    csrf_token_hash: str
    idle_expires_at: datetime
    absolute_expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None = None


@dataclass(slots=True)
class Principal:
    user_id: str
    login_name: str
    display_name: str
    status: str
    force_password_change: bool
    role_codes: list[str]
    permissions: set[str]
    data_entitlements: list[DataEntitlementRecord]
    session_id: str
    csrf_token_hash: str

    @property
    def is_system_admin(self) -> bool:
        return SYSTEM_ADMIN_ROLE_CODE in self.role_codes

    def has_permission(self, permission: str) -> bool:
        return self.is_system_admin or permission in self.permissions


@dataclass(slots=True)
class AuditRecord:
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
