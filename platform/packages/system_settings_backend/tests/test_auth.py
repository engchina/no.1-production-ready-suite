"""共通認証基盤（#212）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pr_system_settings.auth import service as auth_service
from pr_system_settings.auth.domain import (
    CONFIGURED_SYSTEM_ADMIN_USER_UUID,
    SYSTEM_ADMIN_ROLE_ID,
    Principal,
    RoleRecord,
)
from pr_system_settings.auth.errors import SecurityApiError
from pr_system_settings.auth.migrations import (
    PLATFORM_AUTH_DDL,
    PLATFORM_AUTH_DDL_IGNORED_ERRORS,
    apply_platform_auth_schema,
)
from pr_system_settings.auth.service import AuthService
from pr_system_settings.auth.store import (
    PRODUCT_ROLE_PERMISSION_TABLES,
    InMemoryAuthStore,
    OracleAuthStore,
)

ADMIN_PASSWORD = "BootstrapPass!123"
CONFIGURED_PASSWORD = "Configured123A"  # nosec B105


@dataclass
class _Settings:
    service_name: str = "platform-test"
    app_admin_login_user_id: str = "system_admin"
    app_admin_login_user_password: str = CONFIGURED_PASSWORD
    app_auth_absolute_timeout_hours: int = 12
    app_auth_idle_timeout_minutes: int = 60
    app_auth_failed_login_limit: int = 3
    app_auth_lockout_minutes: int = 15
    app_auth_password_min_length: int = 12
    app_auth_password_max_length: int = 128
    # テストを速くするため最小のパラメータにする。
    app_auth_argon2_time_cost: int = 1
    app_auth_argon2_memory_kib: int = 8
    app_auth_argon2_parallelism: int = 1


class _ProductService(AuthService):
    """権限を InMemory の product_role_permissions から読む最小の製品（RAG を想定）。"""

    product_key = "rag"
    role_catalog_permissions = frozenset({"menu.security_roles"})

    def all_permissions(self) -> set[str]:
        return {"menu.security_users", "menu.security_roles", "menu.search"}

    def _role_permissions(self, roles: object) -> set[str]:
        store = self.store
        assert isinstance(store, InMemoryAuthStore)
        table = PRODUCT_ROLE_PERMISSION_TABLES["rag"]
        return {
            code
            for role in roles  # type: ignore[attr-defined]
            for code in store.product_role_permissions.get(table, {}).get(role.role_id, set())
        }

    def _role_within_actor(self, actor: Principal, role: RoleRecord) -> bool:
        return self._role_permissions([role]) <= actor.permissions


def _service() -> tuple[_ProductService, InMemoryAuthStore]:
    store = InMemoryAuthStore()
    service = _ProductService(store, _Settings())
    store.bootstrap(
        login_user_id="ADMIN",
        display_name="初期管理者",
        password_hash=service._hash_password(ADMIN_PASSWORD),
    )
    admin = store.get_user_by_login_user_id("admin")
    assert admin is not None
    store.set_password(admin.user_uuid, service._hash_password(ADMIN_PASSWORD), force_change=False)
    return service, store


def _grant(store: InMemoryAuthStore, product: str, role_id: str, *codes: str) -> None:
    table = PRODUCT_ROLE_PERMISSION_TABLES[product]
    store.product_role_permissions.setdefault(table, {})[role_id] = set(codes)


def _admin(service: _ProductService) -> Principal:
    principal, _, _ = service.login("ADMIN", ADMIN_PASSWORD)
    return principal


def test_login_creates_session_and_authenticates_with_role_permissions() -> None:
    service, _ = _service()
    principal, token, csrf = service.login("admin", ADMIN_PASSWORD)
    assert principal.is_system_admin
    assert principal.role_ids == [SYSTEM_ADMIN_ROLE_ID]
    again = service.authenticate_session(token)
    assert again.user_uuid == principal.user_uuid
    service.verify_csrf(again, csrf, csrf)
    with pytest.raises(SecurityApiError) as exc:
        service.verify_csrf(again, csrf, "other")
    assert exc.value.status_code == 403


def test_login_locks_after_failed_attempts() -> None:
    service, store = _service()
    for _ in range(3):
        with pytest.raises(SecurityApiError) as exc:
            service.login("ADMIN", "WrongPassword!123")
        assert exc.value.status_code == 401
    user = store.get_user_by_login_user_id("admin")
    assert user is not None and user.locked_until is not None
    # ロック中は正しいパスワードでも拒否する。
    with pytest.raises(SecurityApiError):
        service.login("ADMIN", ADMIN_PASSWORD)


def test_session_expires_on_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = _service()
    _, token, _ = service.login("ADMIN", ADMIN_PASSWORD)
    later = datetime.now(UTC) + timedelta(minutes=61)
    monkeypatch.setattr(auth_service, "_now", lambda: later)
    with pytest.raises(SecurityApiError) as exc:
        service.authenticate_session(token)
    assert "有効期限" in exc.value.public_message


def test_configured_system_admin_uses_signed_token_without_tables() -> None:
    service, _ = _service()
    principal, token, _ = service.login("system_admin", CONFIGURED_PASSWORD)
    assert principal.user_uuid == CONFIGURED_SYSTEM_ADMIN_USER_UUID
    assert principal.permissions == service.all_permissions()
    assert token.startswith(service.configured_admin_token_prefix + ".")
    assert service.authenticate_session(token).user_uuid == CONFIGURED_SYSTEM_ADMIN_USER_UUID
    # 大小文字だけ違う login ID は DB ユーザーへもフォールバックしない。
    with pytest.raises(SecurityApiError):
        service.login("SYSTEM_ADMIN", CONFIGURED_PASSWORD)
    # 別製品の接頭辞の token は受け付けない。
    other = token.replace(service.configured_admin_token_prefix, "other-prefix", 1)
    with pytest.raises(SecurityApiError):
        service.authenticate_session(other)


def test_configured_system_admin_reads_and_writes_platform_env_file(tmp_path: Path) -> None:
    """構成管理者は共通 .env の PLATFORM_ADMIN_* を毎回読み、パスワード変更も書き戻す（#211）。"""
    env_file = tmp_path / "platform.env"
    env_file.write_text(
        "PLATFORM_ADMIN_LOGIN_USER_ID=system_admin\n"
        f"PLATFORM_ADMIN_LOGIN_USER_PASSWORD={CONFIGURED_PASSWORD}\n",
        encoding="utf-8",
    )
    store = InMemoryAuthStore()
    service = _ProductService(store, _Settings(app_admin_login_user_password="Unused1234AB"))
    service._platform_env_file = lambda: env_file  # type: ignore[method-assign]

    principal, _, _ = service.login("system_admin", CONFIGURED_PASSWORD)
    service.change_password(principal, CONFIGURED_PASSWORD, "Changed1234AB")

    content = env_file.read_text(encoding="utf-8")
    assert "PLATFORM_ADMIN_LOGIN_USER_PASSWORD=Changed1234AB" in content
    assert CONFIGURED_PASSWORD not in content
    service.login("system_admin", "Changed1234AB")


def test_last_system_admin_cannot_be_disabled() -> None:
    service, store = _service()
    admin = _admin(service)
    user = store.get_user_by_login_user_id("admin")
    assert user is not None
    with pytest.raises(SecurityApiError) as exc:
        service.update_user(
            user.user_uuid,
            expected_version=user.version,
            display_name=user.display_name,
            status="DISABLED",
            role_ids=user.role_ids,
            actor=admin,
        )
    assert "最後のシステム管理者" in exc.value.public_message


def test_cross_product_permissions_block_role_assignment() -> None:
    """RAG のユーザー管理者は、NL2SQL の権限を持つロールを割り当てられない（#212）。"""
    service, store = _service()
    admin = _admin(service)
    manager_role = service.create_role(
        role_code="RAG_USER_MANAGER", display_name="RAG 管理", description="", actor=admin
    )
    _grant(store, "rag", manager_role.role_id, "menu.security_users", "menu.search")
    search_role = service.create_role(
        role_code="SEARCH_ONLY", display_name="検索", description="", actor=admin
    )
    _grant(store, "rag", search_role.role_id, "menu.search")
    nl2sql_admin_role = service.create_role(
        role_code="NL2SQL_ADMIN_SQL", display_name="NL2SQL 管理 SQL", description="", actor=admin
    )
    _grant(store, "rag", nl2sql_admin_role.role_id, "menu.search")
    _grant(store, "nl2sql", nl2sql_admin_role.role_id, "menu.admin_sql")

    manager, _ = service.create_user(
        login_user_id="rag.manager",
        display_name="RAG 管理者",
        role_ids=[manager_role.role_id],
        temporary_password="ManagerPass!12345",
        actor=admin,
    )
    store.set_password(
        manager.user_uuid, service._hash_password("ManagerPass!12345"), force_change=False
    )
    actor, _, _ = service.login("rag.manager", "ManagerPass!12345")
    assert actor.permissions == {"menu.security_users", "menu.search"}

    # RAG の権限だけのロールは割り当てられる。
    created, _ = service.create_user(
        login_user_id="search.user",
        display_name="検索ユーザー",
        role_ids=[search_role.role_id],
        temporary_password="SearchUser!12345",
        actor=actor,
    )
    assert created.role_ids == [search_role.role_id]
    # NL2SQL の権限を含むロールは、RAG の権限が収まっていても拒否する。
    with pytest.raises(SecurityApiError) as exc:
        service.create_user(
            login_user_id="blocked.user",
            display_name="拒否",
            role_ids=[nl2sql_admin_role.role_id],
            temporary_password="BlockedUser!12345",
            actor=actor,
        )
    assert exc.value.status_code == 403
    # 一覧でも割り当て候補に出さない。
    visible = {role.role_code for role in service.list_roles_for_actor(actor)}
    assert visible == {"RAG_USER_MANAGER", "SEARCH_ONLY"}
    # 復元も同じ判定を通る。
    archived = service.archive_role(
        nl2sql_admin_role.role_id, expected_version=nl2sql_admin_role.version, actor=admin
    )
    with pytest.raises(SecurityApiError) as restore_exc:
        service.restore_role(archived.role_id, expected_version=archived.version, actor=actor)
    assert restore_exc.value.status_code == 403


def test_role_basic_update_keeps_role_and_rejects_reserved_code() -> None:
    service, _ = _service()
    admin = _admin(service)
    with pytest.raises(SecurityApiError) as exc:
        service.create_role(
            role_code="system_admin", display_name="予約", description="", actor=admin
        )
    assert exc.value.code == "SECURITY_ROLE_CODE_RESERVED"
    role = service.create_role(role_code="viewer", display_name="閲覧", description="", actor=admin)
    assert role.role_code == "VIEWER"
    updated = service.update_role(
        role.role_id, expected_version=role.version, display_name="閲覧者", actor=admin
    )
    assert (updated.display_name, updated.description, updated.version) == ("閲覧者", "", 2)


class _Cursor:
    def __init__(self, failures: dict[int, str]) -> None:
        self.failures = failures
        self.executed: list[str] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, *_: object) -> None:
        self.executed.append(statement)
        index = len(self.executed)
        if index in self.failures:
            raise RuntimeError(self.failures[index])


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


def test_platform_auth_ddl_is_idempotent_and_uses_platform_names() -> None:
    joined = "\n".join(PLATFORM_AUTH_DDL)
    for table in (
        "PLATFORM_USERS",
        "PLATFORM_ROLES",
        "PLATFORM_USER_ROLES",
        "PLATFORM_AUTH_SESSIONS",
    ):
        assert f"CREATE TABLE {table} (" in joined
    assert "NL2SQL_" not in joined
    cursor = _Cursor({1: "ORA-00955: name is already used by an existing object"})
    connection = _Connection(cursor)
    results = apply_platform_auth_schema(connection)
    assert results[0] == {"index": "1", "status": "skipped", "code": "ORA-00955"}
    assert connection.committed
    assert "ORA-00955" in PLATFORM_AUTH_DDL_IGNORED_ERRORS
    failing = _Connection(_Cursor({2: "ORA-01031: insufficient privileges"}))
    with pytest.raises(RuntimeError):
        apply_platform_auth_schema(failing)


def test_role_permission_codes_rejects_unregistered_tables_and_skips_missing() -> None:
    cursor = _Cursor({1: "ORA-00942: table or view does not exist"})
    cursor.fetchall = lambda: []  # type: ignore[attr-defined]

    class _Factory:
        def __enter__(self) -> _Connection:
            return _Connection(cursor)

        def __exit__(self, *_: object) -> None:
            return None

    store = OracleAuthStore(lambda: _Factory())
    result = store.role_permission_codes(["r1"], tables=["RAG_ROLE_PERMISSIONS"])
    assert result == {"RAG_ROLE_PERMISSIONS": {}}
    with pytest.raises(Exception, match="未登録"):
        store.role_permission_codes(["r1"], tables=["PLATFORM_USERS"])
