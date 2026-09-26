"""ユーザー管理・ロール管理の API 契約（#206）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pr_system_settings.users_roles import (
    AssignedRoleData,
    PasswordPolicyError,
    RoleCreateRequest,
    RoleUpdateRequest,
    UserCreateRequest,
    UserUpdateRequest,
    generate_temporary_password,
    validate_password,
)


def test_login_user_id_is_trimmed_and_validated() -> None:
    assert UserCreateRequest(login_user_id=" a.b-c_1 ", display_name="x").login_user_id == "a.b-c_1"
    with pytest.raises(ValidationError):
        UserCreateRequest(login_user_id="._-", display_name="x")
    with pytest.raises(ValidationError):
        UserCreateRequest(login_user_id="has space", display_name="x")


def test_user_status_is_normalized() -> None:
    assert UserUpdateRequest(version=1, display_name="x", status=" disabled ").status == "DISABLED"
    with pytest.raises(ValidationError):
        UserUpdateRequest(version=1, display_name="x", status="LOCKED")


def test_role_code_is_uppercased_and_role_requests_carry_no_permissions() -> None:
    created = RoleCreateRequest(role_code=" sales_viewer ", display_name="営業閲覧")
    assert created.role_code == "SALES_VIEWER"
    with pytest.raises(ValidationError):
        RoleCreateRequest(role_code="1ROLE", display_name="x")
    # 権限は製品の権限管理 API で扱う。旧 client が送っても共通契約は受け取らない。
    assert "permissions" not in RoleCreateRequest.model_fields
    assert "permissions" not in RoleUpdateRequest.model_fields


def test_unresolved_assigned_role_is_archived() -> None:
    role = AssignedRoleData.unresolved("gone")
    assert (role.role_code, role.display_name, role.archived) == ("gone", "gone", True)


def test_password_policy_reports_every_violation() -> None:
    validate_password("Str0ng!Passw0rd", login_user_id="alice", min_length=12, max_length=128)
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password("short", login_user_id="alice", min_length=12, max_length=128)
    message = str(exc.value)
    assert "12～128" in message and "英大文字" in message and "数字" in message
    with pytest.raises(PasswordPolicyError, match="推測されやすい"):
        validate_password("Alice!Pass1234", login_user_id="alice", min_length=12, max_length=128)


def test_temporary_password_satisfies_policy() -> None:
    for _ in range(20):
        password = generate_temporary_password()
        assert len(password) == 20
        validate_password(password, login_user_id="alice", min_length=12, max_length=128)
