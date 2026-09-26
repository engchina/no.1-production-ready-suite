"""共通認証のドメイン型（3製品共通。NL2SQL の実装を基準に移設。#212）。

ユーザー・ロール・セッションは `PLATFORM_*` テーブルで 3 製品が共有する。ロールに付ける権限や
対象範囲は製品ごとに違うため、製品は `RoleRecord` / `Principal` を継承して項目を足す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SYSTEM_ADMIN_ROLE_CODE = "SYSTEM_ADMIN"
SYSTEM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000001"
CONFIGURED_SYSTEM_ADMIN_USER_UUID = "00000000-0000-0000-0000-000000000002"
LOCAL_DEBUG_USER_UUID = "00000000-0000-0000-0000-000000000000"
FIXED_ADMIN_LOGIN_USER_ID = "system_admin"


@dataclass(slots=True)
class RoleRecord:
    """ロールの共通部分。製品は継承して権限・対象範囲を足す。"""

    role_id: str
    role_code: str
    display_name: str
    description: str
    is_built_in: bool
    archived: bool
    version: int


@dataclass(slots=True)
class UserIdentity:
    user_uuid: str
    login_user_id: str
    display_name: str


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
    """認証済みの利用者。`permissions` は製品が展開した実効権限。製品は継承して項目を足す。"""

    user_uuid: str
    login_user_id: str
    display_name: str
    status: str
    force_password_change: bool
    role_codes: list[str]
    permissions: set[str]
    session_id: str
    csrf_token_hash: str
    password_change_allowed: bool = True
    # 有効なロールの ID。製品をまたぐ権限昇格の判定に使う。
    role_ids: list[str] = field(default_factory=list)

    @property
    def is_system_admin(self) -> bool:
        return SYSTEM_ADMIN_ROLE_CODE in self.role_codes

    def has_permission(self, permission: str) -> bool:
        return self.is_system_admin or permission in self.permissions

    def has_any_permission(self, permissions: set[str] | frozenset[str]) -> bool:
        return self.is_system_admin or bool(self.permissions.intersection(permissions))
