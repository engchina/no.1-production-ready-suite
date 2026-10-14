"""実行履歴（チャット・RAG 検索の実行の一覧と詳細。#1890）。

- 記録: チャットと RAG 検索の画面の実行だけを 1 行で残す（安全チェックで止めた質問は保存しない、
  根拠の検索だけ（`retrieval_only`）は残さない、記録の失敗で検索・回答を止めない）。
- 見え方（#1889）: system_admin は全員の実行、それ以外は自分の実行だけ。一覧・件数・詳細は Oracle の
  SQL の条件（`r.user_id_hash = …`）で絞り、利用者の名前は system_admin にだけ返す。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from pytest import MonkeyPatch

from app.api.routes import history as history_route
from app.clients.oracle import OracleClient
from app.clients.oracle_run_history import RunHistoryStore, run_history_list_where
from app.config import Settings, get_settings
from app.main import app
from app.rag import run_history as run_history_module
from app.rag.guardrails import GuardrailResult
from app.rag.pipeline import RagPipeline
from app.rag.request_context import (
    AuditRequestContext,
    _header_hash,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.run_history import RunRecorder, answer_verification
from app.schemas.search import SearchRequest
from tests.security_support import enable_production_auth, login, login_configured_admin
from tests.support import AsgiTestClient
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm
from tests.test_oracle_adapter import FakeOraclePool, _run_inline

client = AsgiTestClient(app)

OWNER_PREDICATE = "r.user_id_hash = :run_owner_user_id_hash"


class RecordingStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[dict[str, Any]] = []
        self.purged: list[int] = []
        self.fail = fail

    async def save_run(self, record: Mapping[str, object]) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.records.append(dict(record))

    async def purge_runs(self, retention_days: int) -> int:
        self.purged.append(retention_days)
        return 0


@pytest.fixture(autouse=True)
def _reset_purge_clock(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(run_history_module, "_last_purge_at", None)


@contextmanager
def _scope(
    *,
    user_id_hash: str | None = None,
    all_users_visible: bool = False,
    search_answer_profile_ids: set[str] | None = None,
) -> Iterator[None]:
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="run-history-test",
            user_id_hash=user_id_hash,
            all_users_visible=all_users_visible,
            allowed_search_answer_profile_ids=(
                None if search_answer_profile_ids is None else frozenset(search_answer_profile_ids)
            ),
        )
    )
    try:
        yield
    finally:
        reset_audit_request_context(token)


# ---- 回答の検証 ----


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["addressed", "addressed"], "verified"),
        (["addressed", "partial"], "partial"),
        (["addressed", "missing"], "partial"),
        (["addressed", "unknown"], "partial"),
        (["unknown"], "unverified"),
        ([], "unverified"),
    ],
    ids=["all-addressed", "partial", "missing", "unknown-mixed", "all-unknown", "no-requests"],
)
def test_answer_verification_reads_the_audited_requests(statuses: list[str], expected: str) -> None:
    diagnostics = {
        "envelope": {"requests": [{"id": str(i), "status": s} for i, s in enumerate(statuses)]}
    }
    assert answer_verification(diagnostics) == expected


def test_answer_verification_without_diagnostics() -> None:
    assert answer_verification(None) is None
    # 回答の構造が無い回答（承認済み FAQ・古い記録）は確かめていない。
    assert answer_verification({"outcome": "answered"}) == "unverified"


# ---- 記録 ----


async def test_recorder_saves_summary_stages_and_purges_once() -> None:
    store = RecordingStore()
    recorder = RunRecorder(
        surface="chat",
        settings=Settings(rag_answer_record_retention_days=30),
        store=store,
        conversation_id="conv-1",
        message_id="msg-1",
        model_id="model-a",
    )
    callback = recorder.observe(None)

    class Progress:
        def __init__(self, stage: str, outcome: str, elapsed_ms: float) -> None:
            self.stage, self.outcome, self.elapsed_ms = stage, outcome, elapsed_ms

    await callback(Progress("answer", "started", 0.0))
    await callback(Progress("answer", "success", 1200.0))
    await recorder.record(
        run_id="trace-1",
        question="受注の登録方法は？",
        search_answer_profile_id="bv-1",
        answer_generated=True,
        status="success",
        elapsed_ms=1500.0,
        answer_diagnostics={
            "outcome": "answered",
            "confidence": "high",
            "needs_human_review": False,
            "envelope": {"requests": [{"status": "addressed"}]},
            "execution_steps": [
                {"name": "文書検索", "status": "ok", "elapsed_seconds": 0.4, "llm_calls": 0}
            ],
        },
    )
    await recorder.record(
        run_id="trace-2",
        question=None,
        search_answer_profile_id="bv-1",
        answer_generated=True,
        status="blocked",
        elapsed_ms=10.0,
    )

    first, blocked = store.records
    assert first["surface"] == "chat"
    assert first["conversation_id"] == "conv-1"
    assert first["message_id"] == "msg-1"
    assert first["model_id"] == "model-a"
    assert first["answer_outcome"] == "answered"
    assert first["verification"] == "verified"
    assert first["confidence"] == "high"
    # 回答を作った実行の根拠は回答の記録にあるので、ここには持たない。
    assert first["citations"] is None
    assert first["stages"] == {
        "stages": [{"stage": "answer", "outcome": "success", "elapsed_ms": 1200.0}],
        "steps": [{"name": "文書検索", "status": "ok", "elapsed_seconds": 0.4, "llm_calls": 0}],
    }
    assert blocked["status"] == "blocked"
    assert blocked["question"] is None
    assert blocked["verification"] is None
    # 期限切れの削除はプロセスごとに間隔を空けて 1 回。
    assert store.purged == [30]


async def test_recorder_failure_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    recorder = RunRecorder(surface="search", settings=Settings(), store=RecordingStore(fail=True))
    await recorder.record(
        run_id="trace-1",
        question="q",
        search_answer_profile_id=None,
        answer_generated=False,
        status="success",
        elapsed_ms=1.0,
    )
    assert "run history record failed" in caplog.text


async def test_pipeline_records_search_answer_and_blocked_runs(monkeypatch: MonkeyPatch) -> None:
    """RAG 検索（回答なし・回答あり）と、安全チェックで止めた質問を記録する。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)

    class AnswerOracle(FakeOracle):
        async def save_answer_record(self, record: Any) -> None:
            return None

        async def purge_answer_records(self, retention_days: int) -> int:
            return 0

    store = RecordingStore()

    def pipeline() -> RagPipeline:
        return RagPipeline(
            genai=FakeGenAi(),  # type: ignore[arg-type]
            oracle=AnswerOracle(),  # type: ignore[arg-type]
            settings=Settings(),
            run_recorder=RunRecorder(surface="search", settings=Settings(), store=store),
        )

    await pipeline().run(
        SearchRequest(
            query="受注の登録方法は？", search_answer_profile_id="bv-1", generate_answer=False
        )
    )
    await pipeline().run(SearchRequest(query="受注の登録方法は？", search_answer_profile_id="bv-1"))
    await pipeline().run(
        SearchRequest(query="止める質問", search_answer_profile_id="bv-1"),
        query_guardrail_result=GuardrailResult(
            allowed=False, sanitized_text="止める質問", findings=[]
        ),
    )
    # 根拠の検索だけ（ナレッジベースの検索テスト・レシピの検索比較）は残さない。
    await pipeline().run(
        SearchRequest(query="受注", search_answer_profile_id="bv-1", retrieval_only=True)
    )

    search_only, answered, blocked = store.records
    assert search_only["status"] == "success"
    assert search_only["answer_generated"] is False
    assert search_only["question"] == "受注の登録方法は？"
    assert search_only["verification"] is None
    assert search_only["citation_count"] == len(search_only["citations"]) > 0
    assert [stage["stage"] for stage in search_only["stages"]["stages"]] == ["retrieval"]
    assert answered["answer_generated"] is True
    assert answered["citations"] is None
    assert answered["verification"] in {"verified", "partial", "unverified"}
    assert "answer" in [stage["stage"] for stage in answered["stages"]["stages"]]
    assert blocked["status"] == "blocked"
    assert blocked["question"] is None


# ---- 見え方（#1889） ----


def test_list_where_limits_regular_users_to_their_own_runs() -> None:
    with _scope(user_id_hash="u" * 64, search_answer_profile_ids={"bv-1"}):
        where, binds = run_history_list_where(query="受注_%", surface="chat", status="error")
    assert OWNER_PREDICATE in where
    assert binds["run_owner_user_id_hash"] == "u" * 64
    assert "r.search_answer_profile_id IN (:access_search_answer_profile_id_0)" in where
    assert binds["run_query"] == "%受注\\_\\%%"
    assert binds["run_surface"] == "chat"
    assert binds["run_status"] == "error"


def test_list_where_does_not_limit_system_admin_and_denies_unknown_users() -> None:
    with _scope(user_id_hash="u" * 64, all_users_visible=True):
        where, binds = run_history_list_where(query=None, surface=None, status=None)
    assert OWNER_PREDICATE not in where
    assert "run_owner_user_id_hash" not in binds
    with _scope(user_id_hash=None):
        where, _ = run_history_list_where(query=None, surface=None, status=None)
    assert "1 = 0" in where


def _row(run_id: str = "run-1", **overrides: Any) -> dict[str, object]:
    return {
        "run_id": run_id,
        "surface": "search",
        "search_answer_profile_id": "bv-1",
        "search_answer_profile_name": "受注",
        "user_id_hash": "x" * 64,
        "user_uuid": "user-uuid-1",
        "question": "受注の登録方法は？",
        "answer_generated": 0,
        "status": "success",
        "answer_outcome": None,
        "verification": None,
        "confidence": None,
        "needs_human_review": None,
        "citation_count": 1,
        "elapsed_ms": 1234.5,
        "error_stage": None,
        "model_id": None,
        "conversation_id": None,
        "message_id": None,
        "created_at": datetime(2026, 10, 15, tzinfo=UTC),
        **overrides,
    }


def _capture(monkeypatch: MonkeyPatch, execute_results: list[list[dict[str, object]]]) -> Any:
    pool = FakeOraclePool(execute_results=execute_results)
    monkeypatch.setattr(
        history_route,
        "OracleClient",
        lambda *_a, **_k: OracleClient(
            settings=Settings.model_construct(), pool=pool, db_call_runner=_run_inline
        ),
    )
    return pool


def test_list_api_limits_non_admin_users_and_names_users_for_system_admin(
    monkeypatch: MonkeyPatch,
) -> None:
    auth = enable_production_auth(monkeypatch)
    viewer = auth.user_with_permissions("viewer", ["menu.history"])
    other = auth.user_with_permissions("other", ["menu.history"])
    admin_role = auth.create_role(["menu.search"])
    auth.create_user("admin", [admin_role], system_admin=True)
    settings = get_settings()
    viewer_row = _row("run-viewer", user_id_hash=_header_hash(viewer.user_uuid, settings))
    viewer_row["user_uuid"] = viewer.user_uuid
    other_row = _row("run-other", user_uuid=other.user_uuid)
    # 1 回の一覧で 2 つの SQL（一覧・件数）。
    pool = _capture(
        monkeypatch,
        [[viewer_row], [{"count_value": 1}], [viewer_row, other_row], [{"count_value": 2}]],
    )

    mine = client.get("/api/history/runs?q=受注&status=success", headers=login(client, "viewer"))
    admin = client.get("/api/history/runs", headers=login(client, "admin"))

    assert mine.status_code == 200, mine.text
    assert admin.status_code == 200, admin.text
    viewer_calls, admin_calls = pool.connection.calls[0:2], pool.connection.calls[2:4]
    for call in viewer_calls:
        assert OWNER_PREDICATE in call.statement
        assert call.parameters["run_owner_user_id_hash"] == _header_hash(viewer.user_uuid, settings)
    for call in admin_calls:
        assert OWNER_PREDICATE not in call.statement
    item = mine.json()["data"]["items"][0]
    assert item["own"] is True
    # 自分の実行だけの利用者には、利用者の名前を返さない。
    assert item["user_display_name"] is None
    assert "user_uuid" not in item and "user_id_hash" not in item
    assert mine.json()["data"]["total"] == 1
    admin_items = admin.json()["data"]["items"]
    assert [entry["user_display_name"] for entry in admin_items] == ["viewer", "other"]
    assert [entry["own"] for entry in admin_items] == [False, False]


def test_list_api_requires_the_history_menu(monkeypatch: MonkeyPatch) -> None:
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("searcher", ["menu.search"])
    _capture(monkeypatch, [])
    response = client.get("/api/history/runs", headers=login(client, "searcher"))
    assert response.status_code == 403


def test_detail_api_returns_404_outside_the_scope_and_joins_the_answer_record(
    monkeypatch: MonkeyPatch,
) -> None:
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("viewer", ["menu.history"])
    viewer = login(client, "viewer")

    # 他人の実行は見え方の条件で 0 行 → 404。
    pool = _capture(monkeypatch, [[]])
    missing = client.get("/api/history/runs/run-other", headers=viewer)
    assert missing.status_code == 404
    assert OWNER_PREDICATE in pool.connection.calls[0].statement

    # 回答を作った自分の実行は、回答の記録から回答・根拠・診断を読む。
    stages = {
        "stages": [{"stage": "answer", "outcome": "success", "elapsed_ms": 900.0}],
        "steps": [{"name": "文書検索", "status": "ok", "elapsed_seconds": 0.3, "llm_calls": 0}],
    }
    answer_record: dict[str, object] = {
        "trace_id": "run-1",
        "search_answer_profile_id": "bv-1",
        "surface": "chat",
        "answer_engine": "grounded",
        "question": "受注の登録方法は？",
        "rewritten_question": None,
        "answer": "受注番号を入力して登録します。",
        "citations_json": [
            {"document_id": "doc-1", "chunk_id": "doc-1:c1", "text": "受注番号", "score": 0.9}
        ],
        "diagnostics_json": {"outcome": "answered", "execution_steps": []},
        "evaluation_input_json": None,
        "evaluation_json": None,
        "created_at": datetime(2026, 10, 15, tzinfo=UTC),
    }
    _capture(
        monkeypatch,
        [
            [
                {
                    **_row(
                        "run-1",
                        surface="chat",
                        answer_generated=1,
                        conversation_id="conv-1",
                        verification="verified",
                        answer_outcome="answered",
                    ),
                    "question_full": "受注の登録方法は？（全文）",
                    "error_type": None,
                    "stages_json": stages,
                    "citations_json": None,
                }
            ],
            [answer_record],
            [
                {
                    "feedback_id": "fb-1",
                    "target_type": "answer",
                    "rating": "helpful",
                    "reason": None,
                    "document_id": None,
                    "created_at": datetime(2026, 10, 15, tzinfo=UTC),
                }
            ],
        ],
    )
    detail = client.get("/api/history/runs/run-1", headers=viewer)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["question"] == "受注の登録方法は？（全文）"
    assert data["answer"] == "受注番号を入力して登録します。"
    assert data["answer_available"] is True
    assert data["citations"][0]["chunk_id"] == "doc-1:c1"
    assert data["answer_diagnostics"]["outcome"] == "answered"
    assert data["stages"][0]["stage"] == "answer"
    assert data["steps"][0]["name"] == "文書検索"
    assert data["feedback"][0]["feedback_id"] == "fb-1"
    assert data["verification"] == "verified"


def test_configured_admin_sees_every_run(monkeypatch: MonkeyPatch) -> None:
    enable_production_auth(monkeypatch)
    pool = _capture(monkeypatch, [[], [{"count_value": 0}]])
    response = client.get("/api/history/runs", headers=login_configured_admin(client))
    assert response.status_code == 200, response.text
    for call in pool.connection.calls:
        assert OWNER_PREDICATE not in call.statement


async def test_save_run_takes_the_owner_from_the_context() -> None:
    pool = FakeOraclePool(execute_results=[])
    store = RunHistoryStore(
        OracleClient(settings=Settings.model_construct(), pool=pool, db_call_runner=_run_inline)
    )
    token = set_audit_request_context(
        AuditRequestContext(request_id="r", user_id_hash="h" * 64, user_uuid="user-1")
    )
    try:
        await store.save_run(
            {
                "run_id": "run-1",
                "surface": "search",
                "status": "success",
                "question": "q",
                "stages": {"stages": [], "steps": []},
                "citations": [],
                # 呼び出し側の値は使わない。
                "user_uuid": "spoofed",
            }
        )
    finally:
        reset_audit_request_context(token)
    call = pool.connection.calls[0]
    assert "MERGE INTO rag_run_history" in call.statement
    assert call.parameters["user_uuid"] == "user-1"
    assert call.parameters["user_id_hash"] == "h" * 64
