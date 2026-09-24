"""保存済み DocRAG 回答の一覧・詳細 API。"""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.api.routes import search as search_route
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

RECORD: dict[str, Any] = {
    "trace_id": "trace-1",
    "business_view_id": "bv-1",
    "surface": "search",
    "answer_engine": "docrag",
    "question": "受注の登録方法は？",
    "rewritten_question": None,
    "created_at": datetime(2026, 9, 25, tzinfo=UTC),
}


class FakeAnswerOracle:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, Any]] = []

    async def list_answer_records(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls.append(kwargs)
        return [{**RECORD, "confidence": "high"}]

    async def get_answer_record(self, trace_id: str) -> dict[str, Any] | None:
        if trace_id != "trace-1":
            return None
        return {
            **RECORD,
            "answer": "受注番号を入力して登録します。",
            "citations_json": [
                {"document_id": "doc-1", "chunk_id": "doc-1:c1", "text": "受注番号", "score": 0.9}
            ],
            "diagnostics_json": {"confidence": "high", "evidence_tree": []},
        }


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeAnswerOracle:
    fake = FakeAnswerOracle()
    monkeypatch.setattr(search_route, "OracleClient", lambda *args, **kwargs: fake)
    return fake


def test_list_and_get_saved_docrag_answers(fake_oracle: FakeAnswerOracle) -> None:
    listed = client.get("/api/search/answers", params={"business_view_id": "bv-1", "limit": 5})

    assert listed.status_code == 200
    assert listed.json()["data"][0]["confidence"] == "high"
    assert fake_oracle.list_calls == [{"business_view_id": "bv-1", "limit": 5, "offset": 0}]

    detail = client.get("/api/search/answers/trace-1")
    data = detail.json()["data"]
    assert data["answer"] == "受注番号を入力して登録します。"
    assert data["citations"][0]["chunk_id"] == "doc-1:c1"
    assert data["docrag"]["confidence"] == "high"
    assert client.get("/api/search/answers/missing").status_code == 404
