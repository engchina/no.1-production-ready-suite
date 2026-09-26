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
        self.deleted: list[str] = []
        self.evaluations: dict[str, dict[str, Any]] = {}

    async def list_answer_records(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls.append(kwargs)
        return [{**RECORD, "confidence": "high"}]

    async def delete_answer_record(self, trace_id: str) -> bool:
        self.deleted.append(trace_id)
        return trace_id == "trace-1"

    async def save_answer_evaluation(self, trace_id: str, evaluation: dict[str, Any]) -> bool:
        self.evaluations[trace_id] = evaluation
        return True

    async def get_answer_record(self, trace_id: str) -> dict[str, Any] | None:
        if trace_id == "legacy":
            return {
                **RECORD,
                "trace_id": "legacy",
                "answer": "回答",
                "citations_json": [],
                "diagnostics_json": {},
                "evaluation_input_json": None,
            }
        if trace_id != "trace-1":
            return None
        return {
            "evaluation_input_json": {"question": "受注の登録方法は？", "answer_text": "回答"},
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


def test_delete_saved_docrag_answer(fake_oracle: FakeAnswerOracle) -> None:
    deleted = client.delete("/api/search/answers/trace-1")

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"trace_id": "trace-1"}
    assert client.delete("/api/search/answers/missing").status_code == 404
    assert fake_oracle.deleted == ["trace-1", "missing"]


def test_evaluate_saved_answer_with_standard_answer(
    fake_oracle: FakeAnswerOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[dict[str, Any], str]] = []

    def fake_evaluate(evaluation_input: dict[str, Any], standard_answer: str, settings: Any) -> Any:
        calls.append((evaluation_input, standard_answer))
        return {"status": "completed", "total_score": 18, "max_score": 20, "passed": True}

    monkeypatch.setattr(search_route, "evaluate_answer_record", fake_evaluate)

    response = client.post(
        "/api/search/answers/trace-1/evaluation", json={"standard_answer": "受注番号を入力する"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["evaluation_available"] is True
    assert data["evaluation"]["total_score"] == 18
    assert data["evaluation"]["standard_answer"] == "受注番号を入力する"
    assert calls == [
        ({"question": "受注の登録方法は？", "answer_text": "回答"}, "受注番号を入力する")
    ]
    assert fake_oracle.evaluations["trace-1"]["passed"] is True


def test_evaluate_saved_answer_rejects_blank_legacy_and_missing(
    fake_oracle: FakeAnswerOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        search_route,
        "evaluate_answer_record",
        lambda *args: (_ for _ in ()).throw(AssertionError("評価しない")),
    )

    blank = client.post("/api/search/answers/trace-1/evaluation", json={"standard_answer": "  "})
    legacy = client.post("/api/search/answers/legacy/evaluation", json={"standard_answer": "a"})
    missing = client.post("/api/search/answers/missing/evaluation", json={"standard_answer": "a"})

    assert blank.status_code == 422
    assert legacy.status_code == 409
    assert missing.status_code == 404
    assert client.get("/api/search/answers/legacy").json()["data"]["evaluation_available"] is False


@pytest.mark.usefixtures("oracle_db")
async def test_answer_evaluation_round_trip_on_real_oracle() -> None:
    """実 Oracle 26ai で、評価の入力と結果の JSON 列を保存・読み戻しできる。"""
    from app.clients.oracle import OracleClient

    oracle = OracleClient()
    trace_id = "pytest-evaluation-round-trip"
    await oracle.save_answer_record(
        {
            "trace_id": trace_id,
            "surface": "search",
            "answer_engine": "docrag",
            "question": "受注の登録方法は？",
            "answer": "受注番号を入力します。",
            "citations": [],
            "diagnostics": {"confidence": "high"},
            "evaluation_input": {
                "question": "受注の登録方法は？",
                "evidence_items": [{"text": "本文"}],
            },
        }
    )
    try:
        assert await oracle.save_answer_evaluation(
            trace_id, {"status": "completed", "total_score": 17}
        )
        assert not await oracle.save_answer_evaluation("pytest-missing", {"status": "completed"})
        row = await oracle.get_answer_record(trace_id)
        assert row is not None
        assert row["evaluation_input_json"] == {
            "question": "受注の登録方法は？",
            "evidence_items": [{"text": "本文"}],
        }
        assert row["evaluation_json"] == {"status": "completed", "total_score": 17}
        # 同じ trace_id で回答を保存し直すと、古い評価は消える。
        await oracle.save_answer_record(
            {
                "trace_id": trace_id,
                "surface": "search",
                "answer_engine": "docrag",
                "question": "受注の登録方法は？",
                "answer": "別の回答",
                "citations": [],
                "diagnostics": {},
            }
        )
        row = await oracle.get_answer_record(trace_id)
        assert row is not None and row["evaluation_json"] is None
    finally:
        await oracle.delete_answer_record(trace_id)
