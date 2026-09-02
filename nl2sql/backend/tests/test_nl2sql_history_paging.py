"""GET /nl2sql/history の cursor pagination(Issue: 履歴は最新 50 件しか見えない)。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import HistoryItem, Nl2SqlEngine
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.domain import SYSTEM_ADMIN_ROLE_CODE, Principal


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
            "history", item.id, item.model_dump(mode="json"), profile_id=item.profile_id
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


def test_history_route_rejects_broken_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _memory_service(_ITEMS)
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)

    with pytest.raises(HTTPException) as broken:
        nl2sql_router.history(_request(None), cursor="%%%not-base64%%%")  # type: ignore[arg-type]
    assert broken.value.status_code == 422
