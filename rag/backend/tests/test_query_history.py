"""質問履歴の記録と、よく聞かれる質問の候補(rag_poc の query_history)。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.api.routes import business_view_knowledge as knowledge_route
from app.api.routes import settings as settings_routes
from app.config import Settings, get_settings
from app.main import app
from app.rag.pipeline import RagPipeline
from app.rag.query_history import query_history_suggestions, record_query_history
from app.schemas.search import SearchRequest
from tests.support import AsgiTestClient
from tests.test_pipeline import GroundedLlm, StubGenAiClient, StubOracleClient

client = AsgiTestClient(app)
ENABLED = Settings(rag_query_history_enabled=True)


class HistoryStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.purged: list[int] = []
        self.fail = False

    async def append_query_history(self, record: Mapping[str, object]) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.records.append(
            {
                **record,
                "query_id": f"q{len(self.records)}",
                "created_at": datetime.now(UTC) - timedelta(minutes=len(self.records)),
            }
        )

    async def purge_query_history(self, retention_days: int) -> int:
        self.purged.append(retention_days)
        return 0

    async def list_query_history(
        self, business_view_id: str, *, retention_days: int, limit: int = 5000
    ) -> list[dict[str, Any]]:
        return [record for record in self.records if record["business_view_id"] == business_view_id]


async def _record(
    store: HistoryStore, question: str, settings: Settings = ENABLED, **kwargs: Any
) -> None:
    await record_query_history(
        store,
        settings,
        business_view_id=kwargs.get("business_view_id", "bv-1"),
        question=question,
        surface="search",
        filters=kwargs.get("filters", {}),
    )


async def test_record_is_disabled_by_default_and_skips_blocklist_and_missing_view() -> None:
    store = HistoryStore()

    await _record(store, "経費の承認者は？", Settings())
    await _record(store, "経費の承認者は？", business_view_id=None)
    await _record(
        store,
        "社員番号 12345 の給与は？",
        Settings(rag_query_history_enabled=True, rag_query_history_blocklist=["給与"]),
    )

    assert store.records == []


async def test_record_normalizes_question_keeps_classification_and_purges() -> None:
    store = HistoryStore()

    await _record(
        store,
        "  経費の   承認者は？ ",
        filters={"large_category": "経理", "knowledge_base_id": "kb-1"},
    )

    record = store.records[0]
    assert record["question"] == "経費の 承認者は？"
    assert record["normalized_question"] == "経費の承認者は?"
    assert record["classification_filter"] == {"large_category": "経理"}
    assert store.purged == [90]


async def test_record_failure_does_not_raise() -> None:
    store = HistoryStore()
    store.fail = True

    await _record(store, "経費の承認者は？")


async def test_suggestions_follow_rag_poc_rules() -> None:
    store = HistoryStore()
    for _ in range(3):
        await _record(store, "経費精算の締め日は？")
    for _ in range(4):
        await _record(store, "経費の承認者は？")
    for _ in range(2):  # 回数が足りない
        await _record(store, "経費の申請方法は？")
    for _ in range(3):  # 分類が違う
        await _record(store, "経費の上限は？", filters={"large_category": "人事"})
    for _ in range(3):  # 別の業務ビュー
        await _record(store, "経費の精算先は？", business_view_id="bv-2")

    suggestions = await query_history_suggestions(
        store, ENABLED, business_view_id="bv-1", question="経費", classification={}
    )
    filtered = await query_history_suggestions(
        store,
        ENABLED,
        business_view_id="bv-1",
        question="経費",
        classification={"large_category": "経理"},
    )
    disabled = await query_history_suggestions(
        store, Settings(), business_view_id="bv-1", question="経費", classification={}
    )

    assert [(item.question, item.count) for item in suggestions] == [
        ("経費の承認者は？", 4),
        ("経費の上限は？", 3),
        ("経費精算の締め日は？", 3),
    ]
    # 分類で絞ると、分類を記録していない質問は候補から外れる。
    assert [item.question for item in filtered] == []
    assert disabled == []


async def test_pipeline_records_successful_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    class RecordingOracle(StubOracleClient):
        def __init__(self) -> None:
            super().__init__()
            self.history: list[dict[str, Any]] = []

        async def append_query_history(self, record: Any) -> None:
            self.history.append(dict(record))

        async def purge_query_history(self, retention_days: int) -> int:
            return 0

    oracle = RecordingOracle()
    pipeline = RagPipeline(
        genai=StubGenAiClient(),
        oracle=oracle,
        llm=GroundedLlm(),
        settings=Settings(rag_query_history_enabled=True),
    )

    await pipeline.run(SearchRequest(query="承認条件は？", business_view_ids=["bv-1"]))
    await pipeline.run(SearchRequest(query="業務ビューなし"))

    assert [
        (item["business_view_id"], item["question"], item["surface"]) for item in oracle.history
    ] == [("bv-1", "承認条件は？", "search")]


def test_query_suggestions_api(monkeypatch: pytest.MonkeyPatch) -> None:
    store = HistoryStore()
    store.records = [
        {
            "business_view_id": "bv-1",
            "query_id": f"q{index}",
            "question": "経費の承認者は？",
            "normalized_question": "経費の承認者は?",
            "classification_filter": {},
            "created_at": datetime.now(UTC),
        }
        for index in range(3)
    ]

    class Oracle(HistoryStore):
        async def get_business_view(self, business_view_id: str) -> object | None:
            return object() if business_view_id == "bv-1" else None

    oracle = Oracle()
    oracle.records = store.records
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: oracle)
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_query_history_enabled", True)

    response = client.get("/api/business-views/bv-1/query-suggestions", params={"q": "承認"})
    missing = client.get("/api/business-views/bv-x/query-suggestions")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "business_view_id": "bv-1",
        "enabled": True,
        "suggestions": [{"question": "経費の承認者は？", "count": 3}],
    }
    assert missing.status_code == 404


def test_query_history_settings_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    written: dict[str, str] = {}
    monkeypatch.setattr(
        settings_routes,
        "_write_env_values",
        lambda path, values, **kwargs: written.update(values),
    )

    class Oracle:
        async def purge_query_history(self, retention_days: int) -> int:
            return 0

    monkeypatch.setattr(settings_routes, "OracleClient", Oracle)
    settings = get_settings()
    for key in (
        "rag_query_history_enabled",
        "rag_query_history_retention_days",
        "rag_query_history_min_count",
        "rag_query_history_suggestion_limit",
        "rag_query_history_blocklist",
    ):
        monkeypatch.setattr(settings, key, getattr(settings, key))

    initial = client.get("/api/settings/query-history").json()["data"]
    saved = client.patch(
        "/api/settings/query-history",
        json={
            "enabled": True,
            "retention_days": 30,
            "min_count": 2,
            "suggestion_limit": 3,
            "blocklist": [" 給与 ", "", "給与", "住所"],
        },
    )

    assert initial["enabled"] is False
    assert saved.status_code == 200
    assert saved.json()["data"]["blocklist"] == ["給与", "住所"]
    assert written["RAG_QUERY_HISTORY_ENABLED"] == "true"
    assert written["RAG_QUERY_HISTORY_BLOCKLIST"] == '["給与", "住所"]'
    assert settings.rag_query_history_min_count == 2


@pytest.mark.usefixtures("oracle_db")
async def test_query_history_round_trip_on_real_oracle() -> None:
    """実 Oracle 26ai で、質問履歴の追記・一覧・期限切れの削除ができる。"""
    from app.clients.oracle import OracleClient, _execute_count

    oracle = OracleClient()
    view = "pytest-query-history"

    async def cleanup() -> None:
        # テスト用 DB の後始末(cleanup_to_baseline)は文書と KB だけなので、自分で消す。
        await oracle._run_transaction(  # noqa: SLF001
            lambda connection: _execute_count(
                connection,
                "DELETE FROM rag_query_history WHERE business_view_id = :business_view_id",
                {"business_view_id": view},
            )
        )

    await cleanup()
    for _ in range(3):
        await oracle.append_query_history(
            {
                "business_view_id": view,
                "surface": "search",
                "question": "経費の承認者は？",
                "normalized_question": "経費の承認者は?",
                "classification_filter": {"large_category": "経理"},
            }
        )
    rows = await oracle.list_query_history(view, retention_days=90)
    suggestions = await query_history_suggestions(
        oracle,
        ENABLED,
        business_view_id=view,
        question="承認",
        classification={"large_category": "経理"},
    )

    assert len(rows) == 3
    assert rows[0]["classification_filter"] == {"large_category": "経理"}
    assert [(item.question, item.count) for item in suggestions] == [("経費の承認者は？", 3)]
    assert await oracle.purge_query_history(1) == 0
    await cleanup()
