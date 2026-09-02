"""類似履歴 / few-shot の母集団取得が全履歴を読まないことの回帰テスト。

呼び出しは質問入力のデバウンスごと・job ごとに発生するため、管理者 GOOD の履歴だけを
DB 側で絞り、上限件数で止める(Issue: 類似履歴が毎回全履歴を DB からロードする)。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.nl2sql import service as service_module
from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    FeedbackRating,
    HistoryItem,
    Nl2SqlEngine,
    SimilarHistoryRequest,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


def _history(index: int, *, admin: FeedbackRating | None) -> HistoryItem:
    return HistoryItem(
        id=f"hist-{index:03d}",
        question="請求金額を確認したい",
        engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        generated_sql="SELECT TOTAL_AMOUNT FROM APP.INVOICES",
        created_at=f"2026-09-02T00:00:{index:02d}+00:00",
        profile_id="default",
        profile_name="標準プロファイル",
        safety_is_safe=True,
        admin_feedback_rating=admin,
        admin_feedback_updated_at=f"2026-09-02T01:00:{index:02d}+00:00" if admin else "",
    )


def _incremental_service(repository: MemoryIncrementalNl2SqlRepository) -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._incremental_repository = repository  # noqa: SLF001 - white-box contract test
    service._refresh_job_repository = repository  # noqa: SLF001
    service._persistence_ready = True  # noqa: SLF001
    service._persistence_writable = True  # noqa: SLF001
    service._cache_token_poll_seconds = 0.0  # noqa: SLF001
    return service


def _seed(repository: MemoryIncrementalNl2SqlRepository, items: list[HistoryItem]) -> None:
    for item in items:
        repository.put_document(
            "history",
            item.id,
            item.model_dump(mode="json"),
            profile_id=item.profile_id,
            status=item.feedback_rating.value if item.feedback_rating else "unrated",
        )


def test_pool_is_filtered_to_admin_good_on_the_repository_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    _seed(
        repository,
        [
            _history(1, admin=FeedbackRating.GOOD),
            _history(2, admin=None),
            _history(3, admin=FeedbackRating.BAD),
            _history(4, admin=FeedbackRating.GOOD),
        ],
    )
    service = _incremental_service(repository)
    seen_filters: list[dict[str, str] | None] = []
    original = repository.list_documents_page

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen_filters.append(dict(kwargs.get("payload_filters") or {}) or None)
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "list_documents_page", spy)
    monkeypatch.setattr(
        service,
        "_history_snapshot",
        lambda **_kwargs: pytest.fail("全履歴 snapshot を読んではならない"),
    )

    pool = service._similar_history_pool()  # noqa: SLF001

    assert sorted(item.id for item in pool) == ["hist-001", "hist-004"]
    assert seen_filters and all(
        filters == {"admin_feedback_rating": "good"} for filters in seen_filters
    )


def test_pool_stops_at_the_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    _seed(repository, [_history(index, admin=FeedbackRating.GOOD) for index in range(1, 8)])
    service = _incremental_service(repository)
    monkeypatch.setattr(service_module, "_SIMILAR_HISTORY_POOL_LIMIT", 3)

    pool = service._similar_history_pool()  # noqa: SLF001

    assert len(pool) == 3


def test_similar_history_only_surfaces_admin_good_items() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    _seed(
        repository,
        [_history(1, admin=FeedbackRating.GOOD), _history(2, admin=None)],
    )
    service = _incremental_service(repository)

    data = service.similar_history(
        SimilarHistoryRequest(question="請求金額を確認したい", profile_id=None, limit=5)
    )

    assert [entry.item.id for entry in data.items] == ["hist-001"]


def test_memory_pool_matches_repository_semantics() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._history = [  # noqa: SLF001
        _history(1, admin=FeedbackRating.GOOD),
        _history(2, admin=None),
    ]

    pool = service._similar_history_pool()  # noqa: SLF001

    assert [item.id for item in pool] == ["hist-001"]


def test_history_page_payload_filters_apply_in_memory_mode() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._history = [  # noqa: SLF001
        _history(1, admin=FeedbackRating.GOOD),
        _history(2, admin=FeedbackRating.BAD),
    ]

    items, _cursor, total = service._history_page(  # noqa: SLF001
        cursor=None,
        limit=10,
        payload_filters={"admin_feedback_rating": "bad"},
    )

    assert total == 1
    assert [item.id for item in items] == ["hist-002"]
