"""「AI 活用」の下の機能のデータの見え方の共通の判定（#1889）。"""

from __future__ import annotations

from pr_system_settings.auth.domain import (
    CONFIGURED_SYSTEM_ADMIN_USER_UUID,
    LOCAL_DEBUG_USER_UUID,
    SYSTEM_ADMIN_ROLE_CODE,
    Principal,
)
from pr_system_settings.auth.visibility import (
    OWNER_NOT_FOUND_STATUS,
    can_access_user_data,
    can_view_all_users_data,
    owner_filter_user_uuid,
)


def _principal(user_uuid: str, *role_codes: str, permissions: set[str] | None = None) -> Principal:
    return Principal(
        user_uuid=user_uuid,
        login_user_id=user_uuid,
        display_name=user_uuid,
        status="ACTIVE",
        force_password_change=False,
        role_codes=list(role_codes),
        permissions=permissions or set(),
        session_id="s",
        csrf_token_hash="",  # nosec B106 - テスト用
    )


ADMIN = _principal(CONFIGURED_SYSTEM_ADMIN_USER_UUID, SYSTEM_ADMIN_ROLE_CODE)
LOCAL = _principal(LOCAL_DEBUG_USER_UUID, SYSTEM_ADMIN_ROLE_CODE)
ALICE = _principal("alice", "OPERATOR", permissions={"agent.admin", "menu.runs"})
BOB = _principal("bob", "OPERATOR")


def test_system_admin_sees_everyone() -> None:
    for principal in (ADMIN, LOCAL):
        assert can_view_all_users_data(principal)
        assert owner_filter_user_uuid(principal) is None
        assert can_access_user_data(principal, "alice")
        assert can_access_user_data(principal, None)


def test_other_users_see_only_their_own_data() -> None:
    # 製品の管理権限（agent.admin など）は全員を見る理由にしない。
    assert not can_view_all_users_data(ALICE)
    assert owner_filter_user_uuid(ALICE) == "alice"
    assert owner_filter_user_uuid(BOB) == "bob"
    assert can_access_user_data(ALICE, "alice")
    assert not can_access_user_data(ALICE, "bob")
    assert not can_access_user_data(BOB, "alice")


def test_unknown_owner_and_missing_principal_are_denied() -> None:
    assert not can_view_all_users_data(None)
    assert not can_access_user_data(None, "alice")
    assert not can_access_user_data(ALICE, None)
    assert not can_access_user_data(ALICE, "  ")
    assert OWNER_NOT_FOUND_STATUS == 404
