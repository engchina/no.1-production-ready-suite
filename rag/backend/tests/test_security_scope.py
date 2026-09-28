"""RAG の対象範囲（業務ビュー・ナレッジベース）の絞り込みテスト（#214）。

- Oracle の SQL に利用者の範囲の条件と bind が入ること（業務ビュー・回答履歴・文書・feedback）
- DEFAULT の確認は範囲に関係なく行い、重複 INSERT しないこと
- 検索の KB は利用者の範囲との積集合になること
- 共通認証のテーブルをシステムテーブルの全再作成で消さないこと
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from pr_system_settings.auth.errors import SecurityApiError
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.clients.oracle import OracleClient, _feedback_dashboard_filters
from app.config import Settings
from app.main import app
from app.rag.business_view_config import BusinessViewConfig, dump_business_view_config
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.system_schema import (
    MANAGED_TABLES,
    PRESERVED_TABLES,
    RECREATE_CONFIRMATION,
    SystemSchemaManager,
)
from app.schemas.business_view import BusinessViewDetail
from app.schemas.search import SearchRequest
from app.security.permissions import SCOPE_FORBIDDEN_CODE
from tests.security_support import enable_production_auth, login
from tests.support import AsgiTestClient
from tests.test_oracle_adapter import FakeOraclePool, _oracle_knowledge_base_row, _run_inline
from tests.test_search_business_view import FakeViewOracle, RecordingPipeline
from tests.test_system_schema_manager import _FakeDatabase

client = AsgiTestClient(app)


@contextmanager
def _scope(
    *,
    business_view_ids: set[str] | None = None,
    knowledge_base_ids: set[str] | None = None,
    user_id_hash: str | None = None,
    answer_records_unrestricted: bool = False,
) -> Iterator[None]:
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="scope-test",
            user_id_hash=user_id_hash,
            answer_records_unrestricted=answer_records_unrestricted,
            allowed_business_view_ids=(
                None if business_view_ids is None else frozenset(business_view_ids)
            ),
            allowed_knowledge_base_ids=(
                None if knowledge_base_ids is None else frozenset(knowledge_base_ids)
            ),
        )
    )
    try:
        yield
    finally:
        reset_audit_request_context(token)


def _client(pool: FakeOraclePool) -> OracleClient:
    return OracleClient(settings=Settings.model_construct(), pool=pool, db_call_runner=_run_inline)


# ---------------------------------------------------------------------------
# 業務ビュー
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_business_view_list_count_and_get_are_scoped() -> None:
    pool = FakeOraclePool(execute_results=[[], [{"count_value": 0}], []])
    oracle = _client(pool)
    with _scope(business_view_ids={"bv-2", "bv-1"}):
        assert await oracle.list_business_views() == []
        assert await oracle.count_business_views() == 0
        assert await oracle.get_business_view("bv-3") is None
    calls = pool.connection.calls
    assert len(calls) == 3
    for call in calls:
        assert (
            "bv.business_view_id IN (:access_business_view_id_0, :access_business_view_id_1)"
            in call.statement
        )
        assert call.parameters["access_business_view_id_0"] == "bv-1"
        assert call.parameters["access_business_view_id_1"] == "bv-2"


@pytest.mark.anyio
async def test_business_view_scope_empty_denies_all_and_none_is_unrestricted() -> None:
    pool = FakeOraclePool(execute_results=[[], []])
    oracle = _client(pool)
    with _scope(business_view_ids=set()):
        await oracle.list_business_views()
    with _scope():
        await oracle.list_business_views()
    restricted, unrestricted = pool.connection.calls
    assert "1 = 0" in restricted.statement
    assert "access_business_view_id" not in unrestricted.statement
    assert "1 = 0" not in unrestricted.statement


@pytest.mark.anyio
async def test_business_view_update_and_archive_treat_out_of_scope_as_missing() -> None:
    pool = FakeOraclePool(execute_results=[[], []])
    oracle = _client(pool)
    with _scope(business_view_ids={"bv-1"}):
        with pytest.raises(KeyError):
            await oracle.update_business_view("bv-9", name="x", update_fields={"name"})
        with pytest.raises(KeyError):
            await oracle.archive_business_view("bv-9")
    for call in pool.connection.calls:
        assert "business_view_id IN (:access_business_view_id_0)" in call.statement
    assert not any(call.statement.startswith("UPDATE") for call in pool.connection.calls)


# ---------------------------------------------------------------------------
# 回答履歴・feedback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_answer_records_are_scoped_to_allowed_business_views() -> None:
    pool = FakeOraclePool(execute_results=[[], [], [], []])
    oracle = _client(pool)
    with _scope(business_view_ids={"bv-1"}):
        await oracle.list_answer_records(business_view_id=None, limit=10)
        await oracle.list_answer_records(business_view_id="bv-9", limit=10)
        assert await oracle.get_answer_record("trace-1") is None
        assert await oracle.save_answer_evaluation("trace-1", {"score": 1}) is False
        assert await oracle.delete_answer_record("trace-1") is False
    statements = [call.statement for call in pool.connection.calls]
    assert len(statements) == 5
    for call in pool.connection.calls:
        assert "business_view_id IN (:access_business_view_id_0)" in call.statement
        assert call.parameters["access_business_view_id_0"] == "bv-1"
    # 明示した業務ビューも範囲の条件と AND で組み合わせる（範囲外は 0 件）。
    assert "business_view_id = :business_view_id" in statements[1]


@pytest.mark.anyio
async def test_answer_records_are_unrestricted_without_scope() -> None:
    pool = FakeOraclePool(execute_results=[[]])
    with _scope():
        await _client(pool).list_answer_records(business_view_id=None, limit=10)
    assert "access_business_view_id" not in pool.connection.calls[0].statement


@pytest.mark.anyio
async def test_answer_records_are_scoped_to_owner() -> None:
    """保存済みの回答は持ち主の回答だけを一覧・件数・詳細・評価・削除の対象にする（#304）。"""
    pool = FakeOraclePool(execute_results=[[], [{"count_value": 0}], [], [], []])
    oracle = _client(pool)
    with _scope(user_id_hash="owner-hash"):
        await oracle.list_answer_records(business_view_id="bv-1", limit=10)
        assert await oracle.count_answer_records(business_view_id="bv-1") == 0
        assert await oracle.get_answer_record("trace-1") is None
        assert await oracle.save_answer_evaluation("trace-1", {"score": 1}) is False
        assert await oracle.delete_answer_record("trace-1") is False
    calls = pool.connection.calls
    assert len(calls) == 5
    for call in calls:
        assert "user_id_hash = :answer_owner_user_id_hash" in call.statement
        assert call.parameters["answer_owner_user_id_hash"] == "owner-hash"
    assert not any(call.statement.lstrip().startswith("UPDATE") for call in calls)


@pytest.mark.anyio
async def test_answer_records_are_unrestricted_for_managers() -> None:
    """SYSTEM_ADMIN と rag.feedback.manage（answer_records_unrestricted）は持ち主で絞らない。"""
    pool = FakeOraclePool(execute_results=[[], [{"count_value": 3}]])
    oracle = _client(pool)
    with _scope(
        business_view_ids={"bv-1"}, user_id_hash="manager-hash", answer_records_unrestricted=True
    ):
        await oracle.list_answer_records(business_view_id=None, limit=10)
        assert await oracle.count_answer_records(business_view_id=None) == 3
    for call in pool.connection.calls:
        assert "answer_owner_user_id_hash" not in call.statement
        # 業務ビューの範囲の制限は持ち主と別に残る。
        assert "business_view_id IN (:access_business_view_id_0)" in call.statement


@pytest.mark.anyio
async def test_answer_records_filter_trace_ids() -> None:
    pool = FakeOraclePool(execute_results=[[], []])
    oracle = _client(pool)
    with _scope(user_id_hash="owner-hash"):
        await oracle.list_answer_records(
            business_view_id="bv-1", limit=10, trace_ids=["trace-1", "trace-2"]
        )
        await oracle.list_answer_records(business_view_id="bv-1", limit=10, trace_ids=[])
    listed, empty = pool.connection.calls
    assert "trace_id IN (:trace_id_0, :trace_id_1)" in listed.statement
    assert listed.parameters["trace_id_1"] == "trace-2"
    assert "1 = 0" in empty.statement


@pytest.mark.anyio
async def test_answer_record_is_saved_with_owner() -> None:
    """回答を保存するときに持ち主（利用者の hash）を記録する（#304）。"""
    pool = FakeOraclePool(execute_results=[[]])
    with _scope(user_id_hash="owner-hash"):
        await _client(pool).save_answer_record(
            {
                "trace_id": "trace-1",
                "surface": "search",
                "answer_engine": "docrag",
                "question": "質問",
                "answer": "回答",
            }
        )
    (call,) = pool.connection.calls
    assert ":user_id_hash" in call.statement
    assert call.parameters["user_id_hash"] == "owner-hash"


def _capture_answer_list_sql(monkeypatch: MonkeyPatch) -> FakeOraclePool:
    pool = FakeOraclePool(execute_results=[[], [{"count_value": 0}]] * 3)
    monkeypatch.setattr(search_route, "OracleClient", lambda *_a, **_k: _client(pool))
    return pool


def test_answer_history_api_limits_regular_users_to_own_answers(
    monkeypatch: MonkeyPatch,
) -> None:
    """一般の利用者は自分の回答だけ、rag.feedback.manage と SYSTEM_ADMIN は全件（#304）。"""
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("searcher", ["menu.search"])
    auth.user_with_permissions("feedback-manager", ["menu.search", "rag.feedback.manage"])
    admin_role = auth.create_role(["menu.search"])
    auth.create_user("admin", [admin_role], system_admin=True)
    pool = _capture_answer_list_sql(monkeypatch)

    for login_user_id in ("searcher", "feedback-manager", "admin"):
        response = client.get("/api/search/answers", headers=login(client, login_user_id))
        assert response.status_code == 200, response.text
    searcher_list, _count, manager_list, _count2, admin_list, _count3 = pool.connection.calls
    assert "user_id_hash = :answer_owner_user_id_hash" in searcher_list.statement
    assert len(str(searcher_list.parameters["answer_owner_user_id_hash"])) == 64
    assert "answer_owner_user_id_hash" not in manager_list.statement
    assert "answer_owner_user_id_hash" not in admin_list.statement


def test_feedback_filters_are_scoped_to_allowed_business_views() -> None:
    with _scope(business_view_ids={"bv-1", "bv-2"}):
        where_sql, binds = _feedback_dashboard_filters(
            business_view_id=None,
            target_type=None,
            rating=None,
            reason=None,
            period_days=None,
            search_query=None,
        )
    assert "f.business_view_id IN (:access_business_view_id_0, :access_business_view_id_1)" in (
        where_sql
    )
    assert binds == {"access_business_view_id_0": "bv-1", "access_business_view_id_1": "bv-2"}


@pytest.mark.anyio
async def test_feedback_detail_is_scoped_to_allowed_business_views() -> None:
    pool = FakeOraclePool(execute_results=[[]])
    with _scope(business_view_ids={"bv-1"}):
        assert await _client(pool).get_feedback_detail("feedback-1") is None
    call = pool.connection.calls[0]
    assert "f.business_view_id IN (:access_business_view_id_0)" in call.statement
    assert call.parameters["access_business_view_id_0"] == "bv-1"


# ---------------------------------------------------------------------------
# 文書
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_document_list_and_get_are_scoped_to_allowed_knowledge_bases() -> None:
    pool = FakeOraclePool(execute_results=[[], [{"count_value": 0}], []])
    oracle = _client(pool)
    with _scope(knowledge_base_ids={"kb-1"}):
        assert await oracle.list_documents() == []
        assert await oracle.count_documents() == 0
        assert await oracle.get_document("doc-9") is None
    for call in pool.connection.calls:
        assert "FROM rag_document_knowledge_bases scope_dkb" in call.statement
        assert "scope_dkb.document_id = rag_documents.document_id" in call.statement
        assert "scope_dkb.knowledge_base_id IN (:access_knowledge_base_id_0)" in call.statement
        # 複製が許可 KB に属する正本も見える（検索と同じ扱い）。
        assert "scope_duplicate.duplicate_of_document_id = rag_documents.document_id" in (
            call.statement
        )
        assert call.parameters["access_knowledge_base_id_0"] == "kb-1"


@pytest.mark.anyio
async def test_document_queries_are_unrestricted_without_knowledge_base_scope() -> None:
    pool = FakeOraclePool(execute_results=[[]])
    with _scope():
        await _client(pool).list_documents()
    assert "scope_dkb" not in pool.connection.calls[0].statement


@pytest.mark.anyio
async def test_ingestion_jobs_are_scoped_through_documents() -> None:
    pool = FakeOraclePool(execute_results=[[]])
    with _scope(knowledge_base_ids={"kb-1"}):
        await _client(pool).list_ingestion_jobs()
    statement = pool.connection.calls[0].statement
    assert "scope_dkb.document_id = d.document_id" in statement


def test_upload_without_knowledge_base_is_rejected_for_restricted_user(
    monkeypatch: MonkeyPatch,
) -> None:
    """KB が制限された利用者は、KB を指定しないアップロード（DEFAULT 行き）をできない。"""
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("uploader", ["menu.upload"], knowledge_base_ids=["kb-1"])
    headers = login(client, "uploader")
    response = client.post(
        "/api/documents/upload",
        files={"file": ("memo.txt", b"hello", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_messages"] == [
        "アップロード先のナレッジベースを指定してください。"
    ]


# ---------------------------------------------------------------------------
# DEFAULT の重複防止
# ---------------------------------------------------------------------------


def _default_business_view_row(knowledge_base_id: str) -> dict[str, object]:
    return {
        "business_view_id": "bv-default",
        "tenant_id_hash": None,
        "name": "DEFAULT",
        "description": None,
        "status": "ACTIVE",
        "view_config": dump_business_view_config(
            BusinessViewConfig(knowledge_base_ids=[knowledge_base_id])
        ),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "archived_at": None,
    }


@pytest.mark.anyio
async def test_default_business_view_is_checked_without_user_scope() -> None:
    """DEFAULT KB / 業務ビューが利用者の範囲外でも「ない」と判断して重複作成しない。"""
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_knowledge_base_row(name="DEFAULT")],
            [_default_business_view_row("kb-1")],
        ]
    )
    with _scope(business_view_ids={"bv-1"}, knowledge_base_ids={"kb-other"}):
        view = await _client(pool).ensure_default_business_view()
    assert view.name == "DEFAULT"
    statements = [call.statement for call in pool.connection.calls]
    assert len(statements) == 2
    assert all("access_" not in statement for statement in statements)
    assert not any(statement.startswith("INSERT") for statement in statements)
    assert pool.connection.many_calls == []


@pytest.mark.anyio
async def test_default_knowledge_base_lookup_ignores_user_scope() -> None:
    pool = FakeOraclePool(execute_results=[[_oracle_knowledge_base_row(name="DEFAULT")]])
    with _scope(knowledge_base_ids={"kb-other"}):
        knowledge_base = await _client(pool).ensure_default_knowledge_base()
    assert knowledge_base.name == "DEFAULT"
    assert "access_knowledge_base_id" not in pool.connection.calls[0].statement
    assert len(pool.connection.calls) == 1


# ---------------------------------------------------------------------------
# 検索の KB は範囲との積集合
# ---------------------------------------------------------------------------


class ScopedViewOracle(FakeViewOracle):
    """範囲外の業務ビューを「存在しない」として返す fake（Oracle の SQL と同じ扱い）。"""

    async def get_business_view(self, business_view_id: str) -> BusinessViewDetail | None:
        from app.rag.request_context import current_audit_request_context

        allowed = current_audit_request_context().allowed_business_view_ids
        if allowed is not None and business_view_id not in allowed:
            return None
        return await super().get_business_view(business_view_id)


def _install_search(monkeypatch: MonkeyPatch) -> None:
    views = {
        "bv-1": BusinessViewConfig(knowledge_base_ids=["kb-1", "kb-2"]),
        "bv-2": BusinessViewConfig(knowledge_base_ids=["kb-3"]),
        "bv-3": BusinessViewConfig(knowledge_base_ids=["kb-8", "kb-9"]),
    }
    RecordingPipeline.captured_request = None
    monkeypatch.setattr(search_route, "RagPipeline", RecordingPipeline)

    async def keep(settings: Settings, **_kwargs: object) -> Settings:
        return settings

    monkeypatch.setattr(search_route, "resolve_oracle_generation_settings", keep)
    monkeypatch.setattr(search_route, "OracleClient", lambda *_a, **_k: ScopedViewOracle(views))


def _captured_knowledge_base_ids() -> list[str]:
    captured = RecordingPipeline.captured_request
    assert captured is not None
    return list(captured.knowledge_base_ids)


def test_search_intersects_business_view_kbs_with_allowed_kbs(monkeypatch: MonkeyPatch) -> None:
    _install_search(monkeypatch)
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions(
        "searcher", ["menu.search"], business_view_ids=["bv-1"], knowledge_base_ids=["kb-1"]
    )
    auth.user_with_permissions(
        "kb-manager", ["menu.search", "rag.knowledge_bases.manage"], business_view_ids=["bv-1"]
    )
    headers = login(client, "searcher")

    response = client.post(
        "/api/search", json={"query": "規程", "business_view_ids": ["bv-1"]}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert _captured_knowledge_base_ids() == ["kb-1"]

    # request が業務ビューを上書きして範囲外の KB を指定しても読めない。
    RecordingPipeline.captured_request = None
    override = client.post(
        "/api/search",
        json={"query": "規程", "business_view_ids": ["bv-1"], "knowledge_base_ids": ["kb-3"]},
        headers=headers,
    )
    assert override.status_code == 403
    assert override.json()["error_messages"] == [
        search_route.REQUEST_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE
    ]
    assert RecordingPipeline.captured_request is None
    mixed = client.post(
        "/api/search",
        json={"query": "規程", "knowledge_base_ids": ["kb-1", "kb-3"]},
        headers=headers,
    )
    assert mixed.status_code == 200
    assert _captured_knowledge_base_ids() == ["kb-1"]

    # 範囲外の業務ビューは存在しないものとして 404。
    missing = client.post(
        "/api/search", json={"query": "規程", "business_view_ids": ["bv-2"]}, headers=headers
    )
    assert missing.status_code == 404

    # KB の範囲が無制限（rag.knowledge_bases.manage）なら業務ビューの KB をそのまま使う。
    manager = login(client, "kb-manager")
    response = client.post(
        "/api/search", json={"query": "規程", "business_view_ids": ["bv-1"]}, headers=manager
    )
    assert response.status_code == 200
    assert _captured_knowledge_base_ids() == ["kb-1", "kb-2"]


def test_business_view_without_permitted_kbs_is_forbidden_not_empty(
    monkeypatch: MonkeyPatch,
) -> None:
    """業務ビューの KB が 1 つも許可されていない検索は、黙って 0 件にせず 403 にする。"""
    _install_search(monkeypatch)
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions(
        "searcher",
        ["menu.search"],
        business_view_ids=["bv-1", "bv-3"],
        knowledge_base_ids=["kb-1"],
    )
    headers = login(client, "searcher")
    message = [search_route.BUSINESS_VIEW_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE]
    for path in ("/api/search", "/api/search/stream"):
        denied = client.post(
            path, json={"query": "規程", "business_view_ids": ["bv-3"]}, headers=headers
        )
        assert denied.status_code == 403, path
        assert denied.json()["error_messages"] == message
        # 範囲外は経路の権限拒否と区別できる error_code で返す（#224）。
        assert denied.json()["error_code"] == SCOPE_FORBIDDEN_CODE, path
    assert RecordingPipeline.captured_request is None

    # 複数の業務ビューで一部だけ許可されていれば、その積集合で検索を続ける。
    partial = client.post(
        "/api/search",
        json={"query": "規程", "business_view_ids": ["bv-3", "bv-1"]},
        headers=headers,
    )
    assert partial.status_code == 200, partial.text
    assert _captured_knowledge_base_ids() == ["kb-1"]
    RecordingPipeline.captured_request = None
    stream = client.post(
        "/api/search/stream", json={"query": "規程", "business_view_ids": ["bv-1"]}, headers=headers
    )
    assert stream.status_code == 200
    assert "event: done" in stream.text
    assert _captured_knowledge_base_ids() == ["kb-1"]


def test_chat_stream_without_permitted_kbs_is_forbidden(monkeypatch: MonkeyPatch) -> None:
    """チャットも、業務ビューの KB が 1 つも許可されていなければ stream の前に 403。"""
    from app.api.routes import chat as chat_route
    from app.clients.oracle import StoredConversation

    views = {
        "bv-1": BusinessViewConfig(knowledge_base_ids=["kb-1", "kb-2"]),
        "bv-3": BusinessViewConfig(knowledge_base_ids=["kb-9"]),
    }

    class FakeChatOracle(ScopedViewOracle):
        async def get_conversation(self, conversation_id: str) -> StoredConversation | None:
            return StoredConversation(
                id=conversation_id,
                business_view_id="bv-3",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

    monkeypatch.setattr(chat_route, "OracleClient", lambda *_a, **_k: FakeChatOracle(views))

    def fail_stream(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("stream は始めない")

    monkeypatch.setattr(chat_route, "_stream_chat_events", fail_stream)
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions(
        "chatter", ["menu.chat"], business_view_ids=["bv-1", "bv-3"], knowledge_base_ids=["kb-1"]
    )
    response = client.post(
        "/api/chat/conversations/conversation-1/messages/stream",
        json={"content": "規程は？"},
        headers=login(client, "chatter"),
    )
    assert response.status_code == 403
    assert response.json()["error_messages"] == [
        search_route.BUSINESS_VIEW_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE
    ]


def test_ensure_business_view_knowledge_bases_permitted() -> None:
    with _scope():
        search_route.ensure_business_view_knowledge_bases_permitted(["kb-9"])
    with _scope(knowledge_base_ids={"kb-1"}):
        search_route.ensure_business_view_knowledge_bases_permitted(["kb-1", "kb-9"])
        # 参照 KB のない業務ビューは Oracle の条件が範囲へ絞る（ここでは拒否しない）。
        search_route.ensure_business_view_knowledge_bases_permitted([])
        with pytest.raises(SecurityApiError) as denied:
            search_route.ensure_business_view_knowledge_bases_permitted(["kb-9"])
    assert (denied.value.status_code, denied.value.code) == (403, SCOPE_FORBIDDEN_CODE)


def test_scope_request_knowledge_bases_keeps_request_when_unrestricted() -> None:
    request = SearchRequest(query="q", knowledge_base_ids=["kb-1", "kb-2"])
    with _scope():
        assert search_route._scope_request_knowledge_bases(request) is request
    with _scope(knowledge_base_ids={"kb-2"}):
        scoped = search_route._scope_request_knowledge_bases(request)
        assert scoped.knowledge_base_ids == ["kb-2"]
        assert scoped.filters["knowledge_base_id"] == "kb-2"
        # KB を指定しない検索は Oracle の条件が範囲へ絞る（ここでは変えない）。
        plain = SearchRequest(query="q")
        assert search_route._scope_request_knowledge_bases(plain) is plain
    with _scope(knowledge_base_ids=set()), pytest.raises(SecurityApiError) as denied:
        search_route._scope_request_knowledge_bases(request)
    assert (denied.value.status_code, denied.value.code) == (403, SCOPE_FORBIDDEN_CODE)


# ---------------------------------------------------------------------------
# システムテーブル: 共通認証のテーブルは管理対象外
# ---------------------------------------------------------------------------


def test_preserved_platform_tables_are_not_managed() -> None:
    assert set(PRESERVED_TABLES) == {
        "PLATFORM_USERS",
        "PLATFORM_ROLES",
        "PLATFORM_USER_ROLES",
        "PLATFORM_AUTH_SESSIONS",
    }
    assert set(PRESERVED_TABLES).isdisjoint(MANAGED_TABLES)
    assert {"RAG_ROLE_PERMISSIONS", "RAG_ROLE_BUSINESS_VIEWS", "RAG_ROLE_KNOWLEDGE_BASES"} <= set(
        MANAGED_TABLES
    )


def test_initialize_creates_platform_auth_tables_first_and_recreate_keeps_them() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)

    initialized = manager.initialize()
    assert initialized["status"] == "ready"
    created = [name for name, object_type in database.objects if object_type == "TABLE"]
    for table in PRESERVED_TABLES:
        assert table in created
    # RAG_ROLE_* は PLATFORM_ROLES を参照するため、共通認証の表を先に作る。
    assert created.index("PLATFORM_ROLES") < created.index("RAG_ROLE_PERMISSIONS")

    recreated = manager.initialize(recreate=True, confirmation=RECREATE_CONFIRMATION)
    assert recreated["operation"] == "recreated"
    for table in PRESERVED_TABLES:
        assert (table, "TABLE") in database.objects


@pytest.mark.anyio
async def test_conversations_are_scoped_to_allowed_business_views() -> None:
    pool = FakeOraclePool(execute_results=[[], []])
    oracle = _client(pool)
    with _scope(business_view_ids={"bv-1"}):
        assert await oracle.get_conversation("conversation-1") is None
        assert await oracle.list_conversations() == []
    for call in pool.connection.calls:
        assert "business_view_id IN (:access_business_view_id_0)" in call.statement
        assert call.parameters["access_business_view_id_0"] == "bv-1"


@pytest.mark.anyio
async def test_document_list_orders_by_unique_key_for_stable_paging() -> None:
    """#281: 同じ uploaded_at の文書が OFFSET ページングで重複・欠落しないよう一意キーで並べる。"""
    pool = FakeOraclePool(execute_results=[[]])
    with _scope():
        await _client(pool).list_documents(limit=20, offset=20)
    assert "ORDER BY uploaded_at DESC, document_id DESC" in pool.connection.calls[0].statement
