"""保存済み回答の一覧・詳細 API。"""

import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from app.api.routes import search as search_route
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

RECORD: dict[str, Any] = {
    "trace_id": "trace-1",
    "search_answer_profile_id": "bv-1",
    "surface": "search",
    "answer_engine": "grounded",
    "question": "受注の登録方法は？",
    "rewritten_question": None,
    "created_at": datetime(2026, 9, 25, tzinfo=UTC),
}


class FakeAnswerOracle:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.evaluations: dict[str, dict[str, Any]] = {}

    async def list_answer_records(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls.append(kwargs)
        return [{**RECORD, "confidence": "high"}]

    async def count_answer_records(self, **kwargs: Any) -> int:
        self.count_calls.append(kwargs)
        return 23

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


def test_list_and_get_saved_answers(fake_oracle: FakeAnswerOracle) -> None:
    listed = client.get(
        "/api/search/answers", params={"search_answer_profile_id": "bv-1", "limit": 5}
    )

    assert listed.status_code == 200
    page = listed.json()["data"]
    assert page["items"][0]["confidence"] == "high"
    assert fake_oracle.list_calls == [
        {"search_answer_profile_id": "bv-1", "limit": 5, "offset": 0, "trace_ids": None}
    ]

    detail = client.get("/api/search/answers/trace-1")
    data = detail.json()["data"]
    assert data["answer"] == "受注番号を入力して登録します。"
    assert data["citations"][0]["chunk_id"] == "doc-1:c1"
    assert data["answer_diagnostics"]["confidence"] == "high"
    assert client.get("/api/search/answers/missing").status_code == 404


def test_list_saved_answers_returns_total_and_paging(
    fake_oracle: FakeAnswerOracle,
) -> None:
    """回答履歴は総件数とページング（limit / offset / has_next）を返す（#304）。"""
    listed = client.get(
        "/api/search/answers",
        params={"search_answer_profile_id": "bv-1", "limit": 10, "offset": 10},
    )

    assert listed.status_code == 200
    page = listed.json()["data"]
    assert page["total"] == 23
    assert page["limit"] == 10
    assert page["offset"] == 10
    assert page["has_next"] is True
    assert len(page["items"]) == 1
    assert fake_oracle.count_calls == [{"search_answer_profile_id": "bv-1", "trace_ids": None}]
    # 既定は 1 ページ 10 件（共通の Pagination の既定と同じ）。
    client.get("/api/search/answers", params={"search_answer_profile_id": "bv-1"})
    assert fake_oracle.list_calls[-1]["limit"] == 10


def test_list_saved_answers_filters_trace_ids(fake_oracle: FakeAnswerOracle) -> None:
    """チャットは会話の回答の trace_id で保存の有無を引き当てる（重複・空白は除く）。"""
    listed = client.get(
        "/api/search/answers",
        params=[
            ("search_answer_profile_id", "bv-1"),
            ("trace_id", " trace-1 "),
            ("trace_id", "trace-2"),
            ("trace_id", "trace-1"),
        ],
    )

    assert listed.status_code == 200
    assert fake_oracle.list_calls[-1]["trace_ids"] == ["trace-1", "trace-2"]
    assert fake_oracle.count_calls[-1]["trace_ids"] == ["trace-1", "trace-2"]
    blank = client.get("/api/search/answers", params=[("trace_id", "  ")])
    too_many = client.get(
        "/api/search/answers", params=[("trace_id", f"t-{index}") for index in range(101)]
    )
    assert blank.status_code == 422
    assert too_many.status_code == 422


def test_delete_saved_answer(fake_oracle: FakeAnswerOracle) -> None:
    deleted = client.delete("/api/search/answers/trace-1")

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"trace_id": "trace-1"}
    assert client.delete("/api/search/answers/missing").status_code == 404
    assert fake_oracle.deleted == ["trace-1", "missing"]


def test_evaluate_saved_answer_with_standard_answer(
    fake_oracle: FakeAnswerOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[dict[str, Any], str, list[Any]]] = []

    def fake_evaluate(
        evaluation_input: dict[str, Any], standard_answer: str, settings: Any, *, citations: Any
    ) -> Any:
        calls.append((evaluation_input, standard_answer, citations))
        return {"status": "completed", "passed": True, "metrics": [], "suite": "standard"}

    monkeypatch.setattr(search_route, "evaluate_answer_record", fake_evaluate)

    response = client.post(
        "/api/search/answers/trace-1/evaluation", json={"standard_answer": "受注番号を入力する"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["evaluation_available"] is True
    assert data["evaluation"]["suite"] == "standard"
    assert data["evaluation"]["standard_answer"] == "受注番号を入力する"
    assert [call[:2] for call in calls] == [
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


def test_evaluate_saved_answer_times_out_without_saving(
    fake_oracle: FakeAnswerOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """評価が時間の上限を超えたら 504 と理由を返し、評価を保存しない（#304）。"""

    finished = threading.Event()

    def slow_evaluate(*_args: Any, **_kwargs: Any) -> Any:
        try:
            time.sleep(0.3)  # 上限（0.05 秒）の 6 倍。
            return {"status": "completed", "passed": True}
        finally:
            finished.set()

    monkeypatch.setattr(search_route, "evaluate_answer_record", slow_evaluate)
    monkeypatch.setattr(search_route, "ANSWER_EVALUATION_TIMEOUT_SECONDS", 0.05)

    response = client.post(
        "/api/search/answers/trace-1/evaluation", json={"standard_answer": "受注番号を入力する"}
    )

    assert response.status_code == 504
    assert response.json()["error_messages"] == [search_route.ANSWER_EVALUATION_TIMEOUT_MESSAGE]
    # worker thread の評価が終わっても保存しない。テストの client（`anyio.run`）は、504 を返した後も
    # worker thread の終了を待ってから戻るため、固定の時間は待たない（#401）。
    assert finished.wait(timeout=5)
    assert fake_oracle.evaluations == {}


def test_answer_evaluation_timeout_follows_llm_timeout_limit() -> None:
    """評価全体の上限は LLM 1 回の timeout の設定の上限（画面・nginx はこれより長い）。"""
    from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, Settings

    field = Settings.model_fields["oci_enterprise_ai_timeout_seconds"]
    upper = [item.le for item in field.metadata if getattr(item, "le", None) is not None]
    assert upper == [OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS]
    assert search_route.ANSWER_EVALUATION_TIMEOUT_SECONDS == OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS


@pytest.mark.usefixtures("oracle_db")
async def test_answer_evaluation_round_trip_on_real_oracle() -> None:
    """実 Oracle AI Database で、評価の入力と結果の JSON 列を保存・読み戻しできる。"""
    from app.clients.oracle import OracleClient

    oracle = OracleClient()
    trace_id = "pytest-evaluation-round-trip"
    await oracle.save_answer_record(
        {
            "trace_id": trace_id,
            "surface": "search",
            "answer_engine": "grounded",
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
                "answer_engine": "grounded",
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


def test_oracle_rows_turn_json_decimals_into_numbers() -> None:
    """Oracle の JSON 列の Decimal を int / float に戻す(#678 / #695)。"""
    from decimal import Decimal

    from app.clients.oracle import _row_to_dict

    row = _row_to_dict(
        (
            {"evidence_items": [{"score": Decimal("0.5"), "rank": Decimal(2)}]},
            [Decimal(3)],
            Decimal("1.5"),
        ),
        [("payload_json",), ("items_json",), ("score",)],
    )
    item = row["payload_json"]["evidence_items"][0]  # type: ignore[index]
    assert item == {"score": 0.5, "rank": 2}
    assert type(item["rank"]) is int
    assert row["items_json"] == [3]
    # NUMBER 列の値はそのまま。
    assert row["score"] == Decimal("1.5")
    json.dumps([row["payload_json"], row["items_json"]])
