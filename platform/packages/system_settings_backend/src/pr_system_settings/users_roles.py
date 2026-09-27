"""ユーザー管理・ロール管理の API 契約（3製品共通。NL2SQL の実装を基準に移設。#206）。

共有画面（`@engchina/production-ready-system-settings` のユーザー管理・ロール管理）が送受信する
request / response の形と、パスワードポリシーを持つ。永続化・認証・認可は製品が持つ。

- ロールはここでは「コード・名称・説明・アーカイブ状態」だけを扱う。ロールに付ける権限は製品ごとに
  違うため（NL2SQL の menu 権限や業務プロファイル利用権限など）、製品側で `RoleData` を拡張し、
  権限の更新 API も製品側に置く。
"""

from __future__ import annotations

import re
import secrets
import string
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

SYSTEM_ADMIN_ROLE_CODE = "SYSTEM_ADMIN"
USER_STATUSES = frozenset({"ACTIVE", "DISABLED"})

_LOGIN_USER_ID_RE = re.compile(r"(?=.*[A-Za-z0-9])[A-Za-z0-9._-]{1,64}")
_ROLE_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "password123!",
        "admin",
        "administrator",
        "letmein",
        "qwerty",
        "welcome",
        "welcome1",
        "welcome123!",
        "oracle",
        "oracle123",
        "oracle123!",
        "changeme",
        "changeme123!",
        "admin123!",
        "qwerty123!",
        "letmein123!",
        "1234567890",
    }
)
_SYMBOL_CHARACTERS = "!@#$%_-+="


# ---- request ----


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
        if normalized not in USER_STATUSES:
            raise ValueError("status は ACTIVE または DISABLED です。")
        return normalized


class PasswordResetRequest(BaseModel):
    temporary_password: str | None = Field(default=None, max_length=256)


class VersionRequest(BaseModel):
    """楽観ロックの版だけを送る操作（有効化・無効化・アーカイブ・復元）。"""

    version: int = Field(ge=1)


class RoleCreateRequest(BaseModel):
    """ロール管理画面の新規作成。権限は含めない（製品の権限管理で付ける）。"""

    role_code: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=1000)

    @field_validator("role_code")
    @classmethod
    def normalize_role_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _ROLE_CODE_RE.fullmatch(normalized):
            raise ValueError("ロールコードは英大文字・数字・アンダースコアで指定してください。")
        return normalized


class RoleUpdateRequest(BaseModel):
    """ロール管理画面の基本情報の更新。権限は変更しない。"""

    version: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=1000)


# ---- response ----


class AssignedRoleData(BaseModel):
    role_id: str
    role_code: str
    display_name: str
    is_built_in: bool
    archived: bool

    @classmethod
    def unresolved(cls, role_id: str) -> AssignedRoleData:
        """削除済み等で参照できないロール。無効として表示する。"""
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


class UserCreateData(BaseModel):
    user: UserData
    temporary_password: str


class UserDeleteData(BaseModel):
    deleted: bool = True
    user_uuid: str
    login_user_id: str


class PasswordResetData(BaseModel):
    user: UserData
    temporary_password: str


class RoleData(BaseModel):
    """ロールの共通部分。製品はこれを継承して権限等の項目を足す。"""

    role_id: str
    role_code: str
    display_name: str
    description: str
    is_built_in: bool
    archived: bool
    version: int


class RoleDeleteData(BaseModel):
    deleted: bool = True
    role_id: str
    role_code: str


# ---- password policy ----


class PasswordPolicyError(ValueError):
    """公開可能な password policy 違反。"""


def validate_password(
    password: str,
    *,
    login_user_id: str,
    min_length: int,
    max_length: int,
) -> None:
    errors: list[str] = []
    if len(password) < min_length or len(password) > max_length:
        errors.append(f"パスワードは {min_length}～{max_length} 文字で入力してください。")
    if not re.search(r"[A-Z]", password):
        errors.append("英大文字を 1 文字以上含めてください。")
    if not re.search(r"[a-z]", password):
        errors.append("英小文字を 1 文字以上含めてください。")
    if not re.search(r"[0-9]", password):
        errors.append("数字を 1 文字以上含めてください。")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("記号を 1 文字以上含めてください。")
    lowered = password.casefold()
    normalized_login_user_id = login_user_id.strip().casefold()
    if lowered in _COMMON_PASSWORDS or (
        len(normalized_login_user_id) >= 3 and normalized_login_user_id in lowered
    ):
        errors.append("推測されやすいパスワードは使用できません。")
    if errors:
        raise PasswordPolicyError(" ".join(errors))


def generate_temporary_password(length: int = 20) -> str:
    """各文字種を必ず含む一時 password を生成する。"""
    alphabet = string.ascii_letters + string.digits + _SYMBOL_CHARACTERS
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(_SYMBOL_CHARACTERS),
    ]
    required.extend(secrets.choice(alphabet) for _ in range(max(length - 4, 0)))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)
