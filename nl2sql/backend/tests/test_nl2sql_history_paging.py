"""GET /nl2sql/history の cursor pagination(Issue: 履歴は最新 50 件しか見えない)。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import FeedbackRating, HistoryItem, Nl2SqlEngine
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.domain import SYSTEM_ADMIN_ROLE_CODE, Principal, UserIdentity, UserRecord
from app.security.service import SecurityApiError, SecurityService
from app.security.store import InMemorySecurityStore, OracleSecurityStore
from app.settings import Settings


@pytest.fixture(autouse=True)
def history_security(monkeypatch: pytest.MonkeyPatch) -> SecurityService:
    security = SecurityService(InMemorySecurityStore(), Settings())
    monkeypatch.setattr(nl2sql_router, "get_security_service", lambda: security)
    return security


def _history(index: int, actor: str) -> HistoryItem:
    return HistoryItem(
        id=f"hist-{index:03d}",
        question=f"質問 {index}",
        engine=Nl2SqlEngine.SELECT_AI,
        generated_sql=f"SELECT {index} FROM DUAL",
        created_at=f"2026-09-02T00:00:{index:02d}+00:00",
        profile_id="default",
        actor_user_uuid=actor,
    )


def _memory_service(items: list[HistoryItem]) -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._history = list(items)  # noqa: SLF001 - white-box contract test
    return service


def _incremental_service(items: list[HistoryItem]) -> Nl2SqlService:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    for item in items:
        repository.put_document(
            "history",
            item.id,
            item.model_dump(mode="json"),
            profile_id=item.profile_id,
            status=item.feedback_rating.value if item.feedback_rating else "unrated",
        )
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._incremental_repository = repository  # noqa: SLF001
    service._refresh_job_repository = repository  # noqa: SLF001
    service._persistence_ready = True  # noqa: SLF001
    service._persistence_writable = True  # noqa: SLF001
    service._cache_token_poll_seconds = 0.0  # noqa: SLF001
    return service


def _principal(*, admin: bool, user_uuid: str = "user-1") -> Principal:
    return Principal(
        user_uuid=user_uuid,
        login_user_id=user_uuid,
        display_name="利用者",
        status="ACTIVE",
        force_password_change=False,
        role_codes=[SYSTEM_ADMIN_ROLE_CODE] if admin else ["ANALYST"],
        permissions={"menu.history"},
        data_entitlements=[],
        allowed_profile_ids={"default"},
        session_id="session-1",
        csrf_token_hash="csrf",
    )


def _request(principal: Principal | None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(principal=principal))


_ITEMS = [
    _history(1, "user-1"),
    _history(2, "user-2"),
    _history(3, "user-1"),
    _history(4, "user-2"),
    _history(5, "user-1"),
]


@pytest.mark.parametrize("factory", [_memory_service, _incremental_service])
def test_list_history_pages_through_all_items(factory: object) -> None:
    service = factory(_ITEMS)  # type: ignore[operator]

    first = service.list_history(limit=2)
    assert [item.id for item in first.items] == ["hist-005", "hist-004"]
    assert first.total == 5
    assert first.next_cursor

    second = service.list_history(cursor=first.next_cursor, limit=2)
    assert [item.id for item in second.items] == ["hist-003", "hist-002"]
    assert second.next_cursor

    third = service.list_history(cursor=second.next_cursor, limit=2)
    # 旧実装は 50 件固定で、51 件目以降には到達できなかった。
    assert [item.id for item in third.items] == ["hist-001"]
    assert third.next_cursor == ""


@pytest.mark.parametrize("factory", [_memory_service, _incremental_service])
def test_list_history_keeps_actor_filter_across_pages(factory: object) -> None:
    service = factory(_ITEMS)  # type: ignore[operator]

    first = service.list_history(actor_user_uuid="user-1", limit=2)
    assert [item.id for item in first.items] == ["hist-005", "hist-003"]
    assert first.total == 3

    second = service.list_history(actor_user_uuid="user-1", cursor=first.next_cursor, limit=2)
    assert [item.id for item in second.items] == ["hist-001"]
    assert second.next_cursor == ""


@pytest.mark.parametrize("factory", [_memory_service, _incremental_service])
def test_list_history_filters_query_rating_and_safety_before_paging(factory: object) -> None:
    items = [
        _history(1, "user-1").model_copy(
            update={
                "feedback_rating": FeedbackRating.GOOD,
                "feedback_comment": "期待通り",
                "safety_is_safe": True,
            }
        ),
        _history(2, "user-1").model_copy(
            update={
                "question": "監査ログを削除",
                "generated_sql": "DELETE FROM AUDIT_LOG",
                "feedback_rating": None,
                "safety_is_safe": False,
            }
        ),
        _history(3, "user-2").model_copy(
            update={
                "question": "監査ログを確認",
                "generated_sql": "SELECT * FROM AUDIT_LOG",
                "feedback_rating": None,
                "safety_is_safe": False,
            }
        ),
    ]
    service = factory(items)  # type: ignore[operator]

    page = service.list_history(
        actor_user_uuid="user-1",
        limit=10,
        query="AUDIT_LOG",
        rating="unrated",
        safety="blocked",
    )

    assert [item.id for item in page.items] == ["hist-002"]
    assert page.total == 1
    assert page.next_cursor == ""


def test_list_history_clamps_limit() -> None:
    service = _memory_service([_history(index, "user-1") for index in range(1, 260)])

    page = service.list_history(limit=10_000)

    assert len(page.items) == 200
    assert page.next_cursor


def test_history_route_scopes_non_admin_and_passes_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _memory_service(_ITEMS)
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    own = nl2sql_router.history(_request(_principal(admin=False)), limit=2)  # type: ignore[arg-type]
    assert own.data is not None
    assert [item.id for item in own.data.items] == ["hist-005", "hist-003"]
    assert own.data.total == 3

    own_rest = nl2sql_router.history(
        _request(_principal(admin=False)), cursor=own.data.next_cursor, limit=2  # type: ignore[arg-type]
    )
    assert own_rest.data is not None
    assert [item.id for item in own_rest.data.items] == ["hist-001"]

    everyone = nl2sql_router.history(_request(_principal(admin=True)), limit=500)  # type: ignore[arg-type]
    assert everyone.data is not None
    assert everyone.data.total == 5
    assert len(everyone.data.items) == 5

    unauthenticated = nl2sql_router.history(_request(None))  # type: ignore[arg-type]
    assert unauthenticated.data is not None
    assert unauthenticated.data.total == 5


def test_history_route_accepts_server_side_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _memory_service(
        [
            _history(1, "user-1").model_copy(
                update={
                    "question": "請求金額を確認",
                    "feedback_rating": FeedbackRating.BAD,
                    "safety_is_safe": True,
                }
            ),
            _history(2, "user-1").model_copy(
                update={
                    "question": "監査ログを削除",
                    "generated_sql": "DELETE FROM AUDIT_LOG",
                    "feedback_rating": None,
                    "safety_is_safe": False,
                }
            ),
        ]
    )
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    blocked = nl2sql_router.history(
        _request(_principal(admin=False)),  # type: ignore[arg-type]
        limit=10,
        q="AUDIT_LOG",
        rating="unrated",
        safety="blocked",
    )

    assert blocked.data is not None
    assert [item.id for item in blocked.data.items] == ["hist-002"]


@pytest.mark.parametrize(
    ("rating", "safety"),
    [
        ("needs_review", "all"),
        ("all", "unknown"),
    ],
)
def test_history_route_rejects_invalid_filters(rating: str, safety: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        nl2sql_router.history(
            _request(_principal(admin=True)),  # type: ignore[arg-type]
            rating=rating,
            safety=safety,
        )
    assert exc_info.value.status_code == 422


def test_history_route_rejects_broken_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _memory_service(_ITEMS)
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    with pytest.raises(HTTPException) as broken:
        nl2sql_router.history(_request(None), cursor="%%%not-base64%%%")  # type: ignore[arg-type]
    assert broken.value.status_code == 422


@pytest.mark.parametrize("factory", [_memory_service, _incremental_service])
def test_history_executor_identity_is_admin_only_and_does_not_mutate_history(
    factory: object, monkeypatch: pytest.MonkeyPatch, history_security: SecurityService
) -> None:
    store = history_security.store
    for index in [1, 2]:
        store.create_user(
            UserRecord(
                user_uuid=f"user-{index}",
                login_user_id=f"analyst{index}",
                display_name="同じ表示名",
                password_hash="not-a-real-hash",
                status="ACTIVE" if index == 1 else "DISABLED",
                force_password_change=False,
                failed_login_count=0,
                locked_until=None,
                version=1,
            )
        )
    original = [
        _history(1, "user-1"),
        _history(2, "user-2"),
        _history(3, "deleted"),
        _history(4, ""),
    ]
    service = factory(original)  # type: ignore[operator]
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    before = service.list_history().model_dump()
    first = nl2sql_router.history(_request(_principal(admin=True)), limit=2).data  # type: ignore[arg-type]
    assert first is not None
    assert [(item.actor_user_uuid, item.actor_login_user_id) for item in first.items] == [
        ("", ""),
        ("deleted", ""),
    ]
    second = nl2sql_router.history(
        _request(_principal(admin=True)), cursor=first.next_cursor, limit=2  # type: ignore[arg-type]
    ).data
    assert second is not None
    assert [(item.actor_login_user_id, item.actor_display_name) for item in second.items] == [
        ("analyst2", "同じ表示名"),
        ("analyst1", "同じ表示名"),
    ]
    assert "password_hash" not in second.model_dump_json()
    assert service.list_history().model_dump() == before
    user = store.get_user("user-1")
    assert user is not None
    store.update_user(
        user.user_uuid,
        expected_version=user.version,
        display_name="変更後の名前",
        status=user.status,
        role_ids=user.role_ids,
    )
    renamed = nl2sql_router.history(_request(_principal(admin=True))).data  # type: ignore[arg-type]
    assert renamed is not None
    assert renamed.items[-1].actor_display_name == "変更後の名前"
    store.delete_user("user-2", expected_version=1)
    after_delete = nl2sql_router.history(_request(_principal(admin=True))).data  # type: ignore[arg-type]
    assert after_delete is not None
    deleted = next(item for item in after_delete.items if item.actor_user_uuid == "user-2")
    assert deleted.actor_login_user_id == deleted.actor_display_name == ""
    assert service.list_history().model_dump() == before

    def forbidden_lookup(_ids: list[str]) -> dict[str, UserIdentity]:
        raise AssertionError("一般ユーザーが他のユーザー情報を取得した")

    monkeypatch.setattr(store, "get_user_identities", forbidden_lookup)
    own = nl2sql_router.history(_request(_principal(admin=False))).data  # type: ignore[arg-type]
    assert own is not None
    assert [item.actor_user_uuid for item in own.items] == ["user-1"]
    assert own.items[0].actor_display_name == own.items[0].actor_login_user_id == ""
    with pytest.raises(SecurityApiError) as exc:
        history_security.history_user_identities(_principal(admin=False), ["user-2"])
    assert exc.value.status_code == 403


def test_oracle_history_user_identities_batch_only_public_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = OracleSecurityStore(Settings.model_construct())
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [("user-1", "analyst1", "利用者")]
    context = MagicMock()
    context.__enter__.return_value = connection
    connect = MagicMock(return_value=context)
    monkeypatch.setattr(store, "connection", connect)
    assert store.get_user_identities([]) == {}
    connect.assert_not_called()
    identities = store.get_user_identities(["user-1", "user-1", "missing", ""])
    assert identities == {"user-1": UserIdentity("user-1", "analyst1", "利用者")}
    cursor.execute.assert_called_once()
    sql, binds = cursor.execute.call_args.args
    assert sql.startswith("SELECT USER_UUID, LOGIN_USER_ID, DISPLAY_NAME FROM NL2SQL_APP_USERS ")
    assert set(binds.values()) == {"user-1", "missing"}
    assert "user-1" not in sql
    assert "PASSWORD" not in sql and "ROLE" not in sql
