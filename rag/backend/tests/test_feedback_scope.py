"""利用者フィードバックの見える範囲のテスト（#408。NL2SQL の実行履歴にならう）。

- SYSTEM_ADMIN（構成管理者・local の利用者を含む）は、すべての利用者のフィードバックを見る。
- ほかのロールは、自分が送ったフィードバックだけを見る（`rag.feedback.manage` を持っていても同じ）。
- 一覧・件数・集計（今期と前期）・詳細は、Oracle の SQL の条件（`f.user_id_hash = …`）で絞る。
- 他人のフィードバックの ID を直接指定したら 403、存在しない ID は 404。
- 送信者が記録されていない古い行（`user_id_hash IS NULL`）は、SYSTEM_ADMIN だけが見る。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from pytest import MonkeyPatch

from app.api.routes import feedback as feedback_route
from app.clients.oracle import OracleClient, _feedback_dashboard_filters
from app.config import Settings, get_settings
from app.main import app
from app.rag.request_context import (
    AuditRequestContext,
    _header_hash,
    reset_audit_request_context,
    set_audit_request_context,
)
from tests.security_support import enable_production_auth, login, login_configured_admin
from tests.support import AsgiTestClient
from tests.test_oracle_adapter import FakeOraclePool, _run_inline

client = AsgiTestClient(app)

OWNER_PREDICATE = "f.user_id_hash = :feedback_owner_user_id_hash"


@contextmanager
def _scope(
    *,
    user_id_hash: str | None = None,
    feedback_all_users: bool = False,
    business_view_ids: set[str] | None = None,
) -> Iterator[None]:
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="feedback-scope-test",
            user_id_hash=user_id_hash,
            feedback_all_users=feedback_all_users,
            allowed_business_view_ids=(
                None if business_view_ids is None else frozenset(business_view_ids)
            ),
        )
    )
    try:
        yield
    finally:
        reset_audit_request_context(token)


def _client(pool: FakeOraclePool) -> OracleClient:
    return OracleClient(settings=Settings.model_construct(), pool=pool, db_call_runner=_run_inline)


def _filters() -> tuple[str, dict[str, object]]:
    return _feedback_dashboard_filters(
        business_view_id=None,
        target_type=None,
        rating=None,
        reason=None,
        period_days=None,
        search_query=None,
    )


def _detail_row(feedback_id: str = "feedback-1") -> dict[str, object]:
    return {
        "feedback_id": feedback_id,
        "trace_id": "trace-1",
        "business_view_id": "bv-1",
        "business_view_name": "経理",
        "target_type": "answer",
        "source_surface": "search",
        "document_id": None,
        "chunk_id": None,
        "message_id": None,
        "rating": "not_helpful",
        "reason": "incorrect",
        "comment": "古いです。",
        "created_at": datetime(2026, 7, 1, tzinfo=UTC),
        "conversation_id": None,
        "conversation_title": None,
        "model": None,
        "file_name": None,
        "question_preview": "最新規程は？",
        "comment_preview": "古いです。",
        "has_comment": 1,
        "content_source": "search_snapshot",
        "question": "最新規程は？",
        "answer": "2024年版です。",
        "citations_json": "[]",
        "guardrail_codes": "[]",
    }


# ---------------------------------------------------------------------------
# Oracle の SQL の条件
# ---------------------------------------------------------------------------


def test_filters_limit_regular_users_to_their_own_feedback() -> None:
    with _scope(user_id_hash="owner-hash"):
        where_sql, binds = _filters()
    assert OWNER_PREDICATE in where_sql
    assert binds["feedback_owner_user_id_hash"] == "owner-hash"
    # 送信者のない古い行（NULL）を含める条件は足さない（等号で NULL は落ちる）。
    assert "user_id_hash IS NULL" not in where_sql


def test_filters_do_not_limit_system_admin() -> None:
    with _scope(user_id_hash="admin-hash", feedback_all_users=True):
        where_sql, binds = _filters()
    assert "user_id_hash" not in where_sql
    assert "feedback_owner_user_id_hash" not in binds


def test_filters_deny_all_when_the_user_is_unknown() -> None:
    """利用者のない context（認証を通らない経路）は、全件ではなく 0 件にする（fail-closed）。"""
    with _scope():
        where_sql, binds = _filters()
    assert "1 = 0" in where_sql
    assert "feedback_owner_user_id_hash" not in binds


def test_filters_keep_business_view_scope_with_owner_scope() -> None:
    with _scope(user_id_hash="owner-hash", business_view_ids={"bv-1"}):
        where_sql, binds = _filters()
    assert "f.business_view_id IN (:access_business_view_id_0)" in where_sql
    assert OWNER_PREDICATE in where_sql
    assert binds["access_business_view_id_0"] == "bv-1"


@pytest.mark.anyio
async def test_dashboard_list_count_and_summaries_share_the_owner_scope() -> None:
    """一覧・件数・今期の集計・前期の集計の 4 つの SQL がすべて同じ範囲で絞られる。"""
    pool = FakeOraclePool(execute_results=[[], [{"total": 0}], [], []])
    with _scope(user_id_hash="owner-hash"):
        await _client(pool).list_feedback_dashboard_rows(
            business_view_id=None,
            target_type=None,
            rating=None,
            reason=None,
            period_days=30,
            search_query=None,
            sort_order="newest",
            limit=50,
            offset=0,
        )
    calls = pool.connection.calls
    assert len(calls) == 4
    for call in calls:
        assert OWNER_PREDICATE in call.statement
        assert call.parameters["feedback_owner_user_id_hash"] == "owner-hash"


@pytest.mark.anyio
async def test_detail_is_limited_to_the_owner_and_exists_check_is_not() -> None:
    pool = FakeOraclePool(execute_results=[[], [{"found": 1}]])
    with _scope(user_id_hash="owner-hash", business_view_ids={"bv-1"}):
        oracle = _client(pool)
        assert await oracle.get_feedback_detail("feedback-other") is None
        assert await oracle.feedback_exists("feedback-other") is True
    detail_call, exists_call = pool.connection.calls
    assert OWNER_PREDICATE in detail_call.statement
    assert detail_call.parameters["feedback_owner_user_id_hash"] == "owner-hash"
    # 存在の確認は送信者で絞らない（403 と 404 を分けるため）が、業務ビューの範囲は守る。
    assert "user_id_hash" not in exists_call.statement
    assert "f.business_view_id IN (:access_business_view_id_0)" in exists_call.statement


@pytest.mark.anyio
async def test_detail_is_not_limited_for_system_admin() -> None:
    pool = FakeOraclePool(execute_results=[[_detail_row()]])
    with _scope(user_id_hash="admin-hash", feedback_all_users=True):
        detail = await _client(pool).get_feedback_detail("feedback-1")
    assert detail is not None
    assert "feedback_owner_user_id_hash" not in pool.connection.calls[0].statement


# ---------------------------------------------------------------------------
# API（production の共通認証）
# ---------------------------------------------------------------------------


def _capture(
    monkeypatch: MonkeyPatch, execute_results: list[list[dict[str, object]]]
) -> FakeOraclePool:
    pool = FakeOraclePool(execute_results=execute_results)
    monkeypatch.setattr(feedback_route, "OracleClient", lambda *_a, **_k: _client(pool))
    return pool


def test_list_api_limits_non_admin_roles_to_their_own_feedback(monkeypatch: MonkeyPatch) -> None:
    """ほかのロールは自分の分だけ（rag.feedback.manage でも同じ）、SYSTEM_ADMIN は全件。"""
    auth = enable_production_auth(monkeypatch)
    viewer = auth.user_with_permissions("viewer", ["menu.feedback"])
    manager = auth.user_with_permissions("feedback-manager", ["rag.feedback.manage"])
    admin_role = auth.create_role(["menu.search"])
    auth.create_user("admin", [admin_role], system_admin=True)
    # 1 回の一覧で 4 つの SQL（一覧・件数・今期の集計・前期の集計）。
    pool = _capture(monkeypatch, [[], [{"total": 0}], [], []] * 4)

    for headers in (
        login(client, "viewer"),
        login(client, "feedback-manager"),
        login(client, "admin"),
        login_configured_admin(client),
    ):
        response = client.get("/api/feedback", headers=headers)
        assert response.status_code == 200, response.text

    calls = pool.connection.calls
    assert len(calls) == 16
    viewer_calls, manager_calls, admin_calls, configured_calls = (
        calls[0:4],
        calls[4:8],
        calls[8:12],
        calls[12:16],
    )
    settings = get_settings()
    for user, user_calls in ((viewer, viewer_calls), (manager, manager_calls)):
        expected_hash = _header_hash(user.user_uuid, settings)
        for call in user_calls:
            assert OWNER_PREDICATE in call.statement
            assert call.parameters["feedback_owner_user_id_hash"] == expected_hash
    assert (
        viewer_calls[0].parameters["feedback_owner_user_id_hash"]
        != (manager_calls[0].parameters["feedback_owner_user_id_hash"])
    )
    for call in (*admin_calls, *configured_calls):
        assert "feedback_owner_user_id_hash" not in call.statement


def test_detail_api_rejects_other_users_feedback_with_403(monkeypatch: MonkeyPatch) -> None:
    """他人の ID（送信者のない古い行を含む）は 403、ない ID は 404（NL2SQL のジョブと同じ）。"""
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("viewer", ["menu.feedback"])
    auth.user_with_permissions("feedback-manager", ["rag.feedback.manage"])
    viewer = login(client, "viewer")
    manager = login(client, "feedback-manager")

    # 詳細の SQL は 0 行（自分のものではない）、存在の確認は 1 行 → 403。
    _capture(monkeypatch, [[], [{"found": 1}]])
    response = client.get("/api/feedback/feedback-other", headers=viewer)
    assert response.status_code == 403
    assert response.json()["error_messages"] == [
        "他の利用者のフィードバックを参照する権限がありません。"
    ]

    # 評価ケースの作成・承認済み FAQ への反映も、他人の ID は 403。
    _capture(monkeypatch, [[], [{"found": 1}]])
    evaluation_case = client.get("/api/feedback/feedback-other/evaluation-case", headers=viewer)
    assert evaluation_case.status_code == 403
    _capture(monkeypatch, [[], [{"found": 1}]])
    assert (
        client.post("/api/feedback/feedback-other/approved-faq", headers=manager).status_code == 403
    )

    # 存在しない ID は 404。
    _capture(monkeypatch, [[], []])
    assert client.get("/api/feedback/missing", headers=viewer).status_code == 404

    # 自分のフィードバックは見られる。
    _capture(monkeypatch, [[_detail_row()]])
    own = client.get("/api/feedback/feedback-1", headers=viewer)
    assert own.status_code == 200, own.text
    assert own.json()["data"]["answer"] == "2024年版です。"


def test_detail_api_lets_system_admin_open_any_feedback(monkeypatch: MonkeyPatch) -> None:
    auth = enable_production_auth(monkeypatch)
    admin_role = auth.create_role(["menu.search"])
    auth.create_user("admin", [admin_role], system_admin=True)
    admin = login(client, "admin")

    pool = _capture(monkeypatch, [[_detail_row("feedback-other")]])
    response = client.get("/api/feedback/feedback-other", headers=admin)

    assert response.status_code == 200, response.text
    assert "feedback_owner_user_id_hash" not in pool.connection.calls[0].statement


def test_local_user_sees_all_feedback(monkeypatch: MonkeyPatch) -> None:
    """local（全権限の SYSTEM_ADMIN）は X-User-ID にかかわらず全件。"""
    pool = _capture(monkeypatch, [[], [{"total": 0}], [], []])

    response = client.get("/api/feedback", headers={"X-User-ID": "someone"})

    assert response.status_code == 200, response.text
    for call in pool.connection.calls:
        assert "feedback_owner_user_id_hash" not in call.statement
