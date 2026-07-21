"""SQL feedback から classifier training data へ連携する回帰テスト。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI

from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    ClassifierFeedbackImportRequest,
    ClassifierFeedbackSelection,
    ClassifierTrainingExampleUpdateRequest,
    ClassifierTrainRequest,
    FeedbackRating,
    HistoryItem,
    Nl2SqlEngine,
    Nl2SqlProfile,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


class _DisabledEmbeddingClient:
    def is_configured(self) -> bool:
        return False

    def embed_texts(self, _texts: list[str]) -> list[list[float]]:
        raise AssertionError("deterministic fallback should be used")


def _service() -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._embedding_client = _DisabledEmbeddingClient()  # type: ignore[assignment]  # noqa: SLF001
    return service


def _history(
    history_id: str,
    question: str,
    *,
    profile_id: str = "default",
    profile_name: str = "標準プロファイル",
) -> HistoryItem:
    return HistoryItem(
        id=history_id,
        question=question,
        engine=Nl2SqlEngine.AUTO,
        generated_sql="SELECT 1 FROM DUAL",
        created_at="2026-07-19T00:00:00+00:00",
        profile_id=profile_id,
        profile_name=profile_name,
    )


def _append_history(service: Nl2SqlService, item: HistoryItem) -> None:
    service._history.append(item)  # noqa: SLF001 - aggregate setup
    service._persist_entities(  # noqa: SLF001 - persistence contract setup
        [("history", item.id, item.model_dump(mode="json"))]
    )


def test_good_feedback_is_reviewed_and_added_idempotently() -> None:
    service = _service()
    item = _history("history-good", "請求金額を確認したい")
    _append_history(service, item)
    service.save_feedback(item.id, FeedbackRating.GOOD, "期待どおり")

    candidates = service.classifier_training_candidates(
        cursor=None, limit=20, status="all", profile_id="", query=""
    )
    assert candidates.pending_count == 1
    assert candidates.items[0].status == "pending"
    assert candidates.items[0].eligible is True

    request = ClassifierFeedbackImportRequest(
        items=[ClassifierFeedbackSelection(history_id=item.id)]
    )
    imported = service.import_classifier_feedback_examples(request)
    repeated = service.import_classifier_feedback_examples(request)

    assert imported.imported_count == 1
    assert repeated.imported_count == 0
    assert repeated.results[0].status == "added"
    example = service.classifier_training_data().examples[0]
    assert example.profile_id == "default"
    assert example.text == item.question
    assert example.source_type == "feedback"
    assert example.source_history_id == item.id

    service.clear_feedback(item.id)
    changed = service.classifier_training_candidates(
        cursor=None, limit=20, status="source_changed", profile_id="", query=""
    )
    assert changed.items[0].training_example_id == example.id


def test_bad_feedback_is_not_a_training_candidate() -> None:
    service = _service()
    item = _history("history-bad", "誤った SQL の質問")
    _append_history(service, item)
    service.save_feedback(item.id, FeedbackRating.BAD, "SQL が違う")

    candidates = service.classifier_training_candidates(
        cursor=None, limit=20, status="all", profile_id="", query=""
    )
    imported = service.import_classifier_feedback_examples(
        ClassifierFeedbackImportRequest(items=[ClassifierFeedbackSelection(history_id=item.id)])
    )

    assert candidates.items == []
    assert imported.imported_count == 0
    assert imported.results[0].status == "source_changed"


def test_concurrent_feedback_confirmation_keeps_one_deterministic_example() -> None:
    service = _service()
    item = _history("history-concurrent", "  請求\u3000金額を確認したい  ")
    _append_history(service, item)
    service.save_feedback(item.id, FeedbackRating.GOOD)
    request = ClassifierFeedbackImportRequest(
        items=[ClassifierFeedbackSelection(history_id=item.id)]
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: service.import_classifier_feedback_examples(request), range(2)
            )
        )

    examples = service.classifier_training_data().examples
    assert len(examples) == 1
    assert examples[0].id == results[0].results[0].training_example_id
    assert examples[0].id == results[1].results[0].training_example_id
    assert sum(result.imported_count for result in results) == 1


def test_conflict_edit_delete_and_stale_model_lifecycle() -> None:
    service = _service()
    service.create_profile(Nl2SqlProfile(id="sales", name="販売", category="sales"))
    service.import_classifier_training_data(
        filename="base.csv",
        content=(
            "PROFILE_ID,TEXT\n"
            "default,標準の請求を確認したい\n"
            "default,標準の売上を確認したい\n"
            "sales,販売の請求を確認したい\n"
            "sales,販売の売上を確認したい\n"
        ).encode(),
        replace=True,
    )
    trained = service.train_classifier(ClassifierTrainRequest())
    assert trained.ready is True
    assert trained.stale is False

    conflict_history = _history(
        "history-conflict",
        "標準の請求を確認したい",
        profile_id="sales",
        profile_name="販売",
    )
    _append_history(service, conflict_history)
    service.save_feedback(conflict_history.id, FeedbackRating.GOOD)
    conflict = service.classifier_training_candidates(
        cursor=None, limit=20, status="conflict", profile_id="", query=""
    )
    assert conflict.items[0].conflict_profile_ids == ["default"]

    fresh = _history(
        "history-fresh",
        "販売地域別の件数を確認したい",
        profile_id="sales",
        profile_name="販売",
    )
    _append_history(service, fresh)
    service.save_feedback(fresh.id, FeedbackRating.GOOD)
    imported = service.import_classifier_feedback_examples(
        ClassifierFeedbackImportRequest(items=[ClassifierFeedbackSelection(history_id=fresh.id)])
    )
    status = service.classifier_status()
    assert status.ready is True
    assert status.stale is True

    example_id = imported.results[0].training_example_id
    updated = service.update_classifier_training_example(
        example_id,
        ClassifierTrainingExampleUpdateRequest(
            text="販売地域別の合計を確認したい", profile_id="sales"
        ),
    )
    assert updated.text == "販売地域別の合計を確認したい"
    assert service.train_classifier(ClassifierTrainRequest()).stale is False
    assert service.delete_classifier_training_example(example_id).total_examples == 4
    assert service.classifier_status().stale is True


def test_incremental_restart_can_update_feedback_and_restore_training_examples() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=True)
    first = _service()
    first._incremental_repository = repository  # noqa: SLF001
    first._persistence_ready = True  # noqa: SLF001
    first._persistence_writable = True  # noqa: SLF001
    item = _history("history-restart", "再起動後も利用する質問")
    repository.put_document(
        "history",
        item.id,
        item.model_dump(mode="json"),
        profile_id=item.profile_id,
        status="unrated",
    )

    first.save_feedback(item.id, FeedbackRating.GOOD, "永続化")
    first.import_classifier_feedback_examples(
        ClassifierFeedbackImportRequest(items=[ClassifierFeedbackSelection(history_id=item.id)])
    )

    restarted = _service()
    restarted._incremental_repository = repository  # noqa: SLF001
    restarted._persistence_ready = True  # noqa: SLF001
    restarted._persistence_writable = True  # noqa: SLF001

    feedback = restarted.list_feedback(
        cursor=None,
        limit=20,
        rating="good",
        profile_id="",
        query="",
    )
    assert feedback.items[0].feedback_comment == "永続化"
    assert feedback.items[0].training_status == "added"
    assert restarted.classifier_training_data().examples[0].source_history_id == item.id


def test_incremental_feedback_pagination_is_not_limited_to_recent_fifty() -> None:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=True)
    for index in range(75):
        item = _history(f"history-{index:03d}", f"ページング確認 {index:03d}")
        payload = item.model_copy(
            update={
                "feedback_rating": FeedbackRating.GOOD if index % 2 == 0 else FeedbackRating.BAD,
                "feedback_comment": "候補" if index % 2 == 0 else "除外",
            }
        ).model_dump(mode="json")
        repository.put_document(
            "history",
            item.id,
            payload,
            profile_id="default",
            status="good" if index % 2 == 0 else "bad",
        )
    service = _service()
    service._incremental_repository = repository  # noqa: SLF001

    first_page = service.list_feedback(
        cursor=None, limit=20, rating="good", profile_id="default", query="ページング"
    )
    second_page = service.list_feedback(
        cursor=first_page.next_cursor,
        limit=20,
        rating="good",
        profile_id="default",
        query="ページング",
    )
    candidates = service.classifier_training_candidates(
        cursor=None, limit=100, status="pending", profile_id="default", query=""
    )

    assert first_page.total == 38
    assert len(first_page.items) == 20
    assert len(second_page.items) == 18
    assert second_page.next_cursor == ""
    assert candidates.total == 38


def test_failed_retraining_preserves_the_active_model(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    service.create_profile(Nl2SqlProfile(id="sales", name="販売"))
    service.import_classifier_training_data(
        filename="base.csv",
        content=(
            "PROFILE_ID,TEXT\n"
            "default,請求金額を確認したい\n"
            "default,請求件数を確認したい\n"
            "sales,販売金額を確認したい\n"
            "sales,販売件数を確認したい\n"
        ).encode(),
        replace=True,
    )
    trained = service.train_classifier(ClassifierTrainRequest())
    service.import_classifier_training_data(
        filename="change.csv",
        content="PROFILE_ID,TEXT\ndefault,請求明細を確認したい\n".encode(),
    )

    def fail_vectors(_texts: list[str]) -> tuple[list[list[float]], list[str], str]:
        raise RuntimeError("embedding failure")

    monkeypatch.setattr(service, "_classifier_vectors", fail_vectors)
    failed = service.train_classifier(ClassifierTrainRequest())

    assert failed.ready is True
    assert failed.stale is True
    assert failed.classifier_version == trained.classifier_version
    assert "embedding failure" in " ".join(failed.warnings)


@pytest.mark.asyncio
async def test_feedback_and_training_candidate_api_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import router as nl2sql_router

    service = _service()
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    app = FastAPI()
    app.include_router(nl2sql_router.router, prefix="/api")
    item = _history("history-api", "API から請求金額を確認したい")
    _append_history(service, item)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.post(
            "/api/nl2sql/feedback",
            json={"history_id": "missing", "rating": "good", "comment": ""},
        )
        saved = await client.post(
            "/api/nl2sql/feedback",
            json={"history_id": item.id, "rating": "good", "comment": "確認済み"},
        )
        feedback = await client.get(
            "/api/nl2sql/feedback",
            params={"rating": "good", "profile_id": "default", "q": "請求"},
        )
        candidates = await client.get("/api/nl2sql/classifier/training-candidates")
        imported = await client.post(
            "/api/nl2sql/classifier/training-data/from-feedback",
            json={"items": [{"history_id": item.id, "profile_id": "default"}]},
        )
        example_id = imported.json()["data"]["results"][0]["training_example_id"]
        updated = await client.patch(
            f"/api/nl2sql/classifier/training-data/{example_id}",
            json={"text": "API から請求合計を確認したい", "profile_id": "default"},
        )
        deleted = await client.delete(f"/api/nl2sql/classifier/training-data/{example_id}")
        delete_missing = await client.delete(f"/api/nl2sql/classifier/training-data/{example_id}")
        cleared = await client.delete(f"/api/nl2sql/feedback/{item.id}")
        clear_missing = await client.delete("/api/nl2sql/feedback/missing")

    assert missing.status_code == 404
    assert saved.status_code == 200
    assert feedback.json()["data"]["items"][0]["training_status"] == "pending"
    assert candidates.json()["data"]["items"][0]["status"] == "pending"
    assert imported.json()["data"]["results"][0]["profile_id"] == "default"
    assert updated.json()["data"]["text"] == "API から請求合計を確認したい"
    assert deleted.json()["data"]["total_examples"] == 0
    assert delete_missing.status_code == 404
    assert cleared.json()["data"] == {"history_id": item.id, "cleared": True}
    assert clear_missing.status_code == 404
