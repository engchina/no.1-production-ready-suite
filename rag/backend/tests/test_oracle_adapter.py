"""Oracle adapter 境界のテスト。"""

import json
import logging
from array import array
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.clients.oracle as oracle_module
from app.clients.oracle import (
    DocumentDeleteBlockedByRunningIngestionError,
    OracleClient,
    OracleWalletPasswordRequiredError,
    _datetime_value,
    _test_oracle_connection_sync,
    oracle_agent_memory_schema_sql,
    oracle_audit_schema_sql,
    oracle_document_schema_sql,
    oracle_evaluation_artifact_schema_sql,
    oracle_feedback_schema_sql,
    oracle_ingestion_audit_schema_sql,
    oracle_ingestion_job_schema_sql,
    oracle_ingestion_segment_schema_sql,
    oracle_knowledge_base_schema_sql,
    oracle_knowledge_graph_schema_sql,
    oracle_search_audit_schema_sql,
    oracle_text_terms,
    oracle_vector_schema_sql,
    reset_local_store,
)
from app.config import Settings
from app.rag.chunking import Chunk
from app.rag.graph_index import (
    GraphClaim,
    GraphCommunitySummary,
    GraphEntity,
    GraphEntityChunkLink,
    GraphIndex,
    GraphRelationship,
)
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.document import FileStatus, IngestionJob, IngestionJobStatus, IngestionSegment
from app.schemas.extraction import StructuredExtraction
from app.schemas.knowledge_base import KnowledgeBaseStatus
from app.schemas.search import RetrievedChunk, SearchMode

IN_MEMORY_ORACLE_REMOVED = pytest.mark.skip(reason="in-memory Oracle fallback was removed")


def setup_function() -> None:
    """テストごとにテスト補助 store を初期化する。"""
    reset_local_store()


def test_close_oracle_pool_force_closes_busy_shared_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定保存時の pool 切り替えは busy connection があっても 500 にしない。"""
    pool = ForceCloseOnlyPool()
    monkeypatch.setattr(oracle_module, "_SHARED_ORACLE_POOL", pool)

    oracle_module.close_oracle_pool()

    assert pool.close_forces == [True]
    assert oracle_module._SHARED_ORACLE_POOL is None


def test_datetime_value_attaches_utc_to_naive_database_values() -> None:
    """Oracle driver が naive datetime を返しても API JSON の基準時刻を失わない。"""
    value = _datetime_value(datetime(2026, 6, 23, 0, 34, 0))

    assert value.tzinfo is UTC
    assert value.isoformat() == "2026-06-23T00:34:00+00:00"


def test_oracle_connection_refuses_password_wallet_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """パスワード必須 Wallet は SDK の対話入力へ進む前に止める。"""
    wallet_dir = tmp_path / "wallet"
    wallet_dir.mkdir()
    (wallet_dir / "tnsnames.ora").write_text("ragdb_high = ...", encoding="utf-8")
    (wallet_dir / "sqlnet.ora").write_text("WALLET_LOCATION = ...", encoding="utf-8")
    (wallet_dir / "ewallet.p12").write_bytes(b"encrypted-wallet")
    called = False

    def fake_connect(**kwargs: object) -> object:
        nonlocal called
        called = True
        return FakeOracleConnection([[{"ok": 1}]])

    monkeypatch.setattr(
        "app.clients.oracle.importlib.import_module",
        lambda name: SimpleNamespace(connect=fake_connect),
    )
    settings = Settings.model_construct(
        oracle_user="rag_app",
        oracle_password="",
        oracle_dsn="ragdb_high",
        oracle_client_lib_dir="",
        oracle_wallet_dir=str(wallet_dir),
        oracle_wallet_password="",
    )

    with pytest.raises(OracleWalletPasswordRequiredError, match="Wallet パスワード"):
        _test_oracle_connection_sync(settings)

    assert called is False


def test_oracle_connection_uses_auto_login_wallet_without_wallet_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """cwallet.sso がある Wallet は Wallet パスワードなしで接続 kwargs を作る。"""
    wallet_dir = tmp_path / "wallet"
    wallet_dir.mkdir()
    (wallet_dir / "tnsnames.ora").write_text("ragdb_high = ...", encoding="utf-8")
    (wallet_dir / "sqlnet.ora").write_text("WALLET_LOCATION = ...", encoding="utf-8")
    (wallet_dir / "cwallet.sso").write_bytes(b"auto-login-wallet")
    captured: dict[str, object] = {}

    def fake_connect(**kwargs: object) -> object:
        captured.update(kwargs)
        return FakeOracleConnection([[{"ok": 1}]])

    monkeypatch.setattr(
        "app.clients.oracle.importlib.import_module",
        lambda name: SimpleNamespace(connect=fake_connect),
    )
    settings = Settings.model_construct(
        oracle_user="rag_app",
        oracle_password="",
        oracle_dsn="ragdb_high",
        oracle_client_lib_dir="",
        oracle_wallet_dir=str(wallet_dir),
        oracle_wallet_password="",
    )

    _test_oracle_connection_sync(settings)

    assert captured["config_dir"] == str(wallet_dir)
    assert captured["wallet_location"] == str(wallet_dir)
    assert captured["tcp_connect_timeout"] == 10.0
    assert "wallet_password" not in captured


def test_oracle_connection_initializes_instant_client_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """RAG も nl2sql と同じく ORACLE_CLIENT_LIB_DIR があれば thick client を使う。"""
    wallet_dir = tmp_path / "instantclient_23_26" / "network" / "admin"
    wallet_dir.mkdir(parents=True)
    (wallet_dir / "tnsnames.ora").write_text("ragdb_high = ...", encoding="utf-8")
    (wallet_dir / "sqlnet.ora").write_text("WALLET_LOCATION = ...", encoding="utf-8")
    (wallet_dir / "cwallet.sso").write_bytes(b"auto-login-wallet")
    calls: list[tuple[str, object]] = []

    def fake_init_oracle_client(*, lib_dir: str) -> None:
        calls.append(("init", lib_dir))

    def fake_connect(**kwargs: object) -> object:
        calls.append(("connect", kwargs["dsn"]))
        return FakeOracleConnection([[{"ok": 1}]])

    monkeypatch.setattr(oracle_module, "_ORACLE_CLIENT_INITIALIZED_LIB_DIR", None)
    monkeypatch.setattr(
        "app.clients.oracle.importlib.import_module",
        lambda name: SimpleNamespace(
            connect=fake_connect,
            init_oracle_client=fake_init_oracle_client,
        ),
    )
    settings = Settings.model_construct(
        oracle_user="rag_app",
        oracle_password="db-secret",
        oracle_dsn="ragdb_high",
        oracle_client_lib_dir=str(tmp_path / "instantclient_23_26"),
        oracle_wallet_dir="",
        oracle_wallet_password="",
    )

    _test_oracle_connection_sync(settings)

    assert calls[0] == ("init", str(tmp_path / "instantclient_23_26"))
    assert calls[1] == ("connect", "ragdb_high")


def test_oracle_pool_initializes_instant_client_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """共有 pool 作成も thin ではなく nl2sql と同じ thick client に寄せる。"""
    calls: list[tuple[str, object]] = []
    pool = FakeOraclePool()

    def fake_init_oracle_client(*, lib_dir: str) -> None:
        calls.append(("init", lib_dir))

    def fake_create_pool(**kwargs: object) -> object:
        calls.append(("create_pool", kwargs["dsn"]))
        return pool

    monkeypatch.setattr(oracle_module, "_SHARED_ORACLE_POOL", None)
    monkeypatch.setattr(oracle_module, "_ORACLE_CLIENT_INITIALIZED_LIB_DIR", None)
    monkeypatch.setattr(
        "app.clients.oracle.importlib.import_module",
        lambda name: SimpleNamespace(
            create_pool=fake_create_pool,
            init_oracle_client=fake_init_oracle_client,
        ),
    )
    settings = Settings.model_construct(
        oracle_user="rag_app",
        oracle_password="db-secret",
        oracle_dsn="ragdb_high",
        oracle_client_lib_dir=str(tmp_path / "instantclient_23_26"),
        oracle_wallet_dir="",
        oracle_wallet_password="",
    )

    client = OracleClient(settings=settings)

    assert client.connection_pool() is pool
    assert calls[0] == ("init", str(tmp_path / "instantclient_23_26"))
    assert calls[1] == ("create_pool", "ragdb_high")


def test_oracle_connection_uses_database_password_as_wallet_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Wallet パスワード未入力時は DB password を wallet_password に使う。"""
    wallet_dir = tmp_path / "wallet"
    wallet_dir.mkdir()
    (wallet_dir / "tnsnames.ora").write_text("ragdb_high = ...", encoding="utf-8")
    (wallet_dir / "sqlnet.ora").write_text("WALLET_LOCATION = ...", encoding="utf-8")
    (wallet_dir / "cwallet.sso").write_bytes(b"auto-login-wallet")
    (wallet_dir / "ewallet.pem").write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nabc\n-----END ENCRYPTED PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_connect(**kwargs: object) -> object:
        captured.update(kwargs)
        return FakeOracleConnection([[{"ok": 1}]])

    monkeypatch.setattr(
        "app.clients.oracle.importlib.import_module",
        lambda name: SimpleNamespace(connect=fake_connect),
    )
    settings = Settings.model_construct(
        oracle_user="rag_app",
        oracle_password="db-secret",
        oracle_dsn="ragdb_high",
        oracle_client_lib_dir="",
        oracle_wallet_dir=str(wallet_dir),
        oracle_wallet_password="",
    )

    _test_oracle_connection_sync(settings)

    assert captured["wallet_password"] == "db-secret"
    assert captured["tcp_connect_timeout"] == 10.0


def test_oracle_connection_test_strips_wallet_retry_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """接続テストは ADB Wallet alias の長い retry 設定を外して即時診断しやすくする。"""
    wallet_dir = tmp_path / "wallet"
    wallet_dir.mkdir()
    (wallet_dir / "tnsnames.ora").write_text(
        "ragdb_high = (description=(retry_count=20)(retry_delay=3)"
        "(address=(protocol=tcps)(port=1522)(host=adb.example.com))"
        "(connect_data=(service_name=ragdb_high.adb.oraclecloud.com)))",
        encoding="utf-8",
    )
    (wallet_dir / "sqlnet.ora").write_text("WALLET_LOCATION = ...", encoding="utf-8")
    (wallet_dir / "cwallet.sso").write_bytes(b"auto-login-wallet")
    captured: dict[str, object] = {}

    def fake_connect(**kwargs: object) -> object:
        captured.update(kwargs)
        return FakeOracleConnection([[{"ok": 1}]])

    monkeypatch.setattr(
        "app.clients.oracle.importlib.import_module",
        lambda name: SimpleNamespace(connect=fake_connect),
    )
    settings = Settings.model_construct(
        oracle_user="rag_app",
        oracle_password="db-secret",
        oracle_dsn="ragdb_high",
        oracle_client_lib_dir="",
        oracle_wallet_dir=str(wallet_dir),
        oracle_wallet_password="",
    )

    _test_oracle_connection_sync(settings)

    assert "(retry_count=" not in str(captured["dsn"]).lower()
    assert "(retry_delay=" not in str(captured["dsn"]).lower()
    assert captured["retry_count"] == 0
    assert captured["retry_delay"] == 0


async def test_oracle_client_persists_documents_through_pool() -> None:
    """document persistence は Oracle pool 経由で実行される。"""
    pool = FakeOraclePool(execute_results=[[]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.create_document(
        file_name="policy.txt",
        object_storage_path="oci://bucket/policy.txt",
        content_type="text/plain",
    )

    assert detail.file_name == "policy.txt"
    assert detail.knowledge_bases[0].name == "DEFAULT"
    assert pool.connection.commits == 1
    statements = [call.statement for call in pool.connection.calls]
    assert any("INSERT INTO rag_knowledge_bases" in statement for statement in statements)
    assert any("INSERT INTO rag_documents" in statement for statement in statements)
    assert pool.connection.many_calls
    assert "INSERT INTO rag_document_knowledge_bases" in pool.connection.many_calls[0].statement


async def test_oracle_client_get_document_restores_preprocess_artifact() -> None:
    """文書詳細は保存済みファイル準備 artifact を欠落させず返す。"""
    row = _oracle_document_row(status="PREPROCESSED")
    row["preprocess_artifact"] = json.dumps(
        {
            "derivation_id": "derivation-1",
            "profile": "pdf_to_page_images",
            "converted": True,
            "object_storage_path": "oci://namespace/bucket/artifacts/canonical/doc-1.pdf",
            "content_type": "application/pdf",
            "sha256": "b" * 64,
            "file_name": "policy.pdf",
        }
    )
    pool = FakeOraclePool(
        execute_results=[
            [row],
            [{"document_id": "doc-1", "knowledge_base_id": "kb-1", "name": "社内規程"}],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.get_document("doc-1")

    assert "preprocess_artifact" in pool.connection.calls[0].statement
    assert detail is not None
    assert detail.preprocess_artifact is not None
    assert (
        detail.preprocess_artifact.object_storage_path
        == "oci://namespace/bucket/artifacts/canonical/doc-1.pdf"
    )
    assert detail.preprocess_artifact.content_type == "application/pdf"
    assert detail.preprocess_artifact.sha256 == "b" * 64


async def test_oracle_read_retries_one_recoverable_disconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """recoverable な読取切断は新しい接続で1回だけ再試行する。"""
    disconnect = RuntimeError(SimpleNamespace(isrecoverable=True, full_code="DPY-4011"))
    pool = FakeOraclePool(
        execute_results=[[], [{"ok": 1}]],
        fetch_errors=[disconnect],
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with caplog.at_level(logging.WARNING, logger="app.clients.oracle"):
        rows = await client._fetch_all("SELECT 1 AS ok FROM DUAL")

    assert rows == [{"ok": 1}]
    assert pool.acquire_calls == 2
    record = next(item for item in caplog.records if item.message == "oracle_read_retry")
    assert record.__dict__["error_type"] == "RuntimeError"
    assert record.__dict__["oracle_error_code"] == "DPY-4011"


@pytest.mark.parametrize(("recoverable", "expected_attempts"), [(True, 2), (False, 1)])
async def test_oracle_read_does_not_retry_more_than_once_or_nonrecoverable_errors(
    recoverable: bool,
    expected_attempts: int,
) -> None:
    """再切断は2回で止め、非recoverable例外は即時に返す。"""
    errors = [
        RuntimeError(SimpleNamespace(isrecoverable=recoverable, full_code="DPY-4011"))
        for _ in range(expected_attempts)
    ]
    pool = FakeOraclePool(
        execute_results=[[] for _ in range(expected_attempts)],
        fetch_errors=errors,
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with pytest.raises(RuntimeError):
        await client._fetch_all("SELECT 1 AS ok FROM DUAL")

    assert pool.acquire_calls == expected_attempts


def test_fetch_all_preserves_fetch_error_when_cursor_close_also_fails() -> None:
    """切断後の cursor.close 失敗で元の fetch 例外を上書きしない。"""

    def fail(message: str) -> None:
        raise RuntimeError(message)

    cursor = SimpleNamespace(
        description=None,
        execute=lambda *_args: None,
        fetchall=lambda: fail("fetch failed"),
        close=lambda: fail("close failed"),
    )
    connection: Any = SimpleNamespace(cursor=lambda: cursor)

    with pytest.raises(RuntimeError, match="fetch failed"):
        oracle_module._fetch_all(connection, "SELECT 1 FROM DUAL", {})


async def test_oracle_client_assigns_uploaded_document_to_selected_knowledge_base() -> None:
    """upload 時の KB 指定は既定 KB ではなく指定先 membership を作る。"""
    pool = FakeOraclePool(execute_results=[[_oracle_knowledge_base_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.create_document(
        file_name="policy.txt",
        object_storage_path="oci://bucket/policy.txt",
        content_type="text/plain",
        knowledge_base_ids=["kb-1"],
    )

    assert len(detail.knowledge_bases) == 1
    assert detail.knowledge_bases[0].id == "kb-1"
    statements = [call.statement for call in pool.connection.calls]
    assert not any("INSERT INTO rag_knowledge_bases" in statement for statement in statements)
    assert pool.connection.many_calls[0].rows[0]["knowledge_base_id"] == "kb-1"


async def test_oracle_client_persists_ingestion_job() -> None:
    """取込 job は Oracle queue table へ保存する。"""
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    job = IngestionJob(
        id="job-1",
        document_id="doc-1",
        status=IngestionJobStatus.QUEUED,
        parser_profile="enterprise_ai_pdf_layout",
        quality_warnings=["table_structure_review"],
        queued_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    created = await client.create_ingestion_job(job)

    assert created == job
    statements = [call.statement for call in pool.connection.calls]
    assert any("INSERT INTO rag_ingestion_jobs" in statement for statement in statements)
    insert_call = next(
        call for call in pool.connection.calls if "INSERT INTO rag_ingestion_jobs" in call.statement
    )
    assert insert_call.parameters["job_id"] == "job-1"
    assert insert_call.parameters["parser_profile"] == "enterprise_ai_pdf_layout"
    assert insert_call.parameters["quality_warnings"] == '["table_structure_review"]'
    assert insert_call.parameters["attempt_count"] == 0
    assert insert_call.parameters["max_attempts"] == 3


async def test_oracle_client_persists_ingestion_job_without_max_attempts_column() -> None:
    """旧 queue table では max_attempts 列を省いて取込 job を保存する。"""
    pool = FakeOraclePool(
        execute_results=[[_oracle_document_row()]],
        missing_ingestion_job_max_attempts=True,
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    job = IngestionJob(
        id="job-1",
        document_id="doc-1",
        status=IngestionJobStatus.QUEUED,
        parser_profile="enterprise_ai_pdf_layout",
        queued_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    created = await client.create_ingestion_job(job)

    assert created == job
    insert_calls = [
        call for call in pool.connection.calls if "INSERT INTO rag_ingestion_jobs" in call.statement
    ]
    assert len(insert_calls) == 2
    assert "max_attempts" in insert_calls[0].statement
    assert "max_attempts" not in insert_calls[1].statement


async def test_oracle_client_lists_and_counts_ingestion_jobs() -> None:
    """取込 job 一覧は document access predicate 付きで取得する。"""
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_ingestion_job_row()],
            [{"count_value": 1}],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    jobs = await client.list_ingestion_jobs(
        status=IngestionJobStatus.QUEUED,
        limit=10,
        offset=0,
    )
    count = await client.count_ingestion_jobs(status=IngestionJobStatus.QUEUED)

    assert count == 1
    assert len(jobs) == 1
    assert jobs[0].id == "job-1"
    assert jobs[0].status == IngestionJobStatus.QUEUED
    assert jobs[0].quality_warnings == ["table_structure_review"]
    list_call = pool.connection.calls[0]
    assert "FROM rag_ingestion_jobs j" in list_call.statement
    assert "JOIN rag_documents d" in list_call.statement
    assert "j.status = :ingestion_job_status" in list_call.statement
    assert "FETCH NEXT :limit ROWS ONLY" in list_call.statement
    assert list_call.parameters["ingestion_job_status"] == "QUEUED"
    assert list_call.parameters["limit"] == 10
    count_call = pool.connection.calls[1]
    assert "j.status = :ingestion_job_status" in count_call.statement
    assert count_call.parameters["ingestion_job_status"] == "QUEUED"


async def test_oracle_client_lists_document_ingestion_jobs() -> None:
    """指定 document の取込 job 一覧は document_id と状態で絞り込める。"""
    pool = FakeOraclePool(execute_results=[[_oracle_ingestion_job_row(status="RUNNING")]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    jobs = await client.list_document_ingestion_jobs(
        "doc-1",
        status=IngestionJobStatus.RUNNING,
    )

    assert len(jobs) == 1
    assert jobs[0].document_id == "doc-1"
    assert jobs[0].status == IngestionJobStatus.RUNNING
    call = pool.connection.calls[0]
    assert "FROM rag_ingestion_jobs j" in call.statement
    assert "JOIN rag_documents d" in call.statement
    assert "j.document_id = :document_id" in call.statement
    assert "j.status = :ingestion_job_status" in call.statement
    assert call.parameters["document_id"] == "doc-1"
    assert call.parameters["ingestion_job_status"] == "RUNNING"


async def test_oracle_client_updates_ingestion_job_status() -> None:
    """取込 job 状態更新後に最新行を返す。"""
    started_at = datetime(2026, 1, 2, 0, 1, tzinfo=UTC)
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_ingestion_job_row(status="RUNNING", started_at=started_at)],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    updated = await client.update_ingestion_job(
        "job-1",
        status=IngestionJobStatus.RUNNING,
        attempt_count=1,
        started_at=started_at,
    )

    assert updated is not None
    assert updated.status == IngestionJobStatus.RUNNING
    assert updated.started_at == started_at
    update_call = pool.connection.calls[0]
    assert "UPDATE rag_ingestion_jobs" in update_call.statement
    assert "EXISTS (" in update_call.statement
    assert update_call.parameters["status"] == "RUNNING"
    assert update_call.parameters["attempt_count"] == 1
    assert update_call.parameters["started_at"] == started_at


async def test_oracle_client_recovers_stale_ingestion_jobs() -> None:
    """stale RUNNING job は試行回数に応じて再キューまたは失敗へ戻す。"""
    stale_at = datetime(2026, 1, 2, 1, 0, tzinfo=UTC)
    pool = FakeOraclePool(
        execute_results=[
            [
                _oracle_ingestion_job_row(
                    status="RUNNING",
                    attempt_count=1,
                    max_attempts=3,
                    started_at=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
                ),
                _oracle_ingestion_job_row(
                    job_id="job-maxed",
                    status="RUNNING",
                    attempt_count=3,
                    max_attempts=3,
                    started_at=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
                ),
            ],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    recovered = await client.recover_stale_ingestion_jobs(stale_before=stale_at, limit=10)

    assert [job.id for job in recovered] == ["job-1", "job-maxed"]
    assert pool.connection.commits == 1
    select_call = pool.connection.calls[0]
    assert "j.status = 'RUNNING'" in select_call.statement
    assert "COALESCE(j.started_at, j.queued_at) < :stale_before" in select_call.statement
    assert select_call.parameters["stale_before"] == stale_at
    assert select_call.parameters["limit"] == 10
    update_statements = [call.statement for call in pool.connection.calls[1:]]
    assert any("SET status = 'QUEUED'" in statement for statement in update_statements)
    assert any("SET status = 'FAILED'" in statement for statement in update_statements)
    # 固着防止: 再キュー/失敗時は文書状態も復旧する。
    document_updates = [
        call for call in pool.connection.calls if "UPDATE rag_documents" in call.statement
    ]
    document_statuses = {call.parameters.get("status") for call in document_updates}
    assert "UPLOADED" in document_statuses  # 再キューした EXTRACT job の文書
    assert "ERROR" in document_statuses  # 試行上限超過 job の文書
    assert not any("DELETE FROM rag_chunks" in call.statement for call in pool.connection.calls)


async def test_oracle_client_recovers_orphaned_ingesting_document() -> None:
    """active な job が無いのに INGESTING で取り残された文書を ERROR へ復旧する。"""
    stale_at = datetime(2026, 1, 2, 1, 0, tzinfo=UTC)
    pool = FakeOraclePool(
        execute_results=[
            [],  # stale RUNNING job は無い
            [{"document_id": "doc-stuck"}],  # orphan 文書
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    recovered = await client.recover_stale_ingestion_jobs(stale_before=stale_at, limit=10)

    assert recovered == []
    orphan_select = next(
        call
        for call in pool.connection.calls
        if "FROM rag_documents d" in call.statement
        and "NOT EXISTS" in call.statement
        and call.statement.strip().startswith("SELECT")
    )
    assert (
        "d.status IN ('PREPROCESSING', 'INGESTING', 'CHUNKING', 'INDEXING')"
        in orphan_select.statement
    )
    assert not any("DELETE FROM rag_chunks" in call.statement for call in pool.connection.calls)
    document_update = next(
        call
        for call in pool.connection.calls
        if "UPDATE rag_documents" in call.statement
        and call.parameters.get("document_id") == "doc-stuck"
    )
    assert document_update.parameters["status"] == "ERROR"
    assert (
        "status IN ('PREPROCESSING', 'INGESTING', 'CHUNKING', 'INDEXING')"
        in document_update.statement
    )
    assert pool.connection.commits == 1


async def test_oracle_client_recovers_stale_ingestion_jobs_without_max_attempts_column() -> None:
    """旧 queue table では既定 max_attempts を補って stale job を回復する。"""
    stale_at = datetime(2026, 1, 2, 1, 0, tzinfo=UTC)
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    key: value
                    for key, value in _oracle_ingestion_job_row(
                        status="RUNNING",
                        attempt_count=1,
                        started_at=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
                    ).items()
                    if key != "max_attempts"
                }
            ],
        ],
        missing_ingestion_job_max_attempts=True,
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    recovered = await client.recover_stale_ingestion_jobs(stale_before=stale_at, limit=10)

    assert [job.id for job in recovered] == ["job-1"]
    assert recovered[0].max_attempts == 3
    select_calls = [
        call for call in pool.connection.calls if "FROM rag_ingestion_jobs j" in call.statement
    ]
    assert "j.max_attempts" in select_calls[0].statement
    assert ":default_max_attempts AS max_attempts" in select_calls[1].statement
    assert select_calls[1].parameters["default_max_attempts"] == 3
    update_statements = [call.statement for call in pool.connection.calls]
    assert any("SET status = 'QUEUED'" in statement for statement in update_statements)


async def test_oracle_client_claims_ingestion_job_with_row_lock() -> None:
    """取込 job 実行前に QUEUED 行を row lock 付きで claim する。"""
    started_at = datetime(2026, 1, 2, 0, 2, tzinfo=UTC)
    pool = FakeOraclePool(execute_results=[[_oracle_ingestion_job_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    claimed = await client.claim_ingestion_job("job-1", started_at=started_at)

    assert claimed is not None
    assert claimed.status == IngestionJobStatus.RUNNING
    assert claimed.attempt_count == 1
    assert claimed.started_at == started_at
    select_call = pool.connection.calls[0]
    update_call = pool.connection.calls[1]
    assert "FOR UPDATE SKIP LOCKED" in select_call.statement
    assert "j.status = 'QUEUED'" in select_call.statement
    assert "SET status = 'RUNNING'" in update_call.statement
    assert update_call.parameters["attempt_count"] == 1
    assert update_call.parameters["started_at"] == started_at


async def test_oracle_client_replaces_and_lists_ingestion_segments() -> None:
    """segment checkpoint は document scope で置換・取得できる。"""
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_document_row()],
            [_oracle_ingestion_segment_row()],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    segment = IngestionSegment(
        segment_id="doc-1:p1-3",
        document_id="doc-1",
        status="QUEUED",
        parser_backend="enterprise_ai",
        parser_profile="enterprise_ai_pdf_layout",
        page_start=1,
        page_end=3,
    )

    replaced = await client.replace_ingestion_segments("doc-1", [segment])
    listed = await client.list_ingestion_segments("doc-1")

    assert replaced == [segment]
    assert listed[0].segment_id == "doc-1:p1-3"
    assert listed[0].page_start == 1
    statements = [call.statement for call in pool.connection.calls]
    assert any("DELETE FROM rag_ingestion_segments" in statement for statement in statements)
    assert pool.connection.many_calls
    assert "INSERT INTO rag_ingestion_segments" in pool.connection.many_calls[0].statement
    assert pool.connection.many_calls[0].rows[0]["segment_id"] == "doc-1:p1-3"
    list_call = pool.connection.calls[-1]
    assert "FROM rag_ingestion_segments s" in list_call.statement
    assert "JOIN rag_documents d" in list_call.statement
    assert list_call.parameters["document_id"] == "doc-1"


async def test_oracle_client_updates_ingestion_segment_status() -> None:
    """segment checkpoint 更新は document access predicate 付きで行う。"""
    pool = FakeOraclePool(execute_results=[[_oracle_ingestion_segment_row(status="RUNNING")]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    updated = await client.update_ingestion_segment(
        "doc-1:p1-3",
        status="RUNNING",
        attempt_count=2,
        artifact_path="oci://namespace/bucket/artifacts/extractions/doc-1/trace.json",
    )

    assert updated is not None
    assert updated.status == "RUNNING"
    update_call = pool.connection.calls[0]
    assert "UPDATE rag_ingestion_segments" in update_call.statement
    assert "updated_at = SYSTIMESTAMP" in update_call.statement
    assert update_call.parameters["segment_id"] == "doc-1:p1-3"
    assert update_call.parameters["status"] == "RUNNING"
    assert update_call.parameters["attempt_count"] == 2


async def test_oracle_client_persists_knowledge_bases_through_pool() -> None:
    """knowledge base persistence は Oracle pool 経由で実行される。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.create_knowledge_base(
        name="社内規程",
        description="規程文書",
        default_search_mode=SearchMode.HYBRID,
        retrieval_config={"top_k": 20},
    )

    assert detail.name == "社内規程"
    assert detail.status == KnowledgeBaseStatus.ACTIVE
    assert detail.retrieval_config == {"top_k": 20}
    assert pool.connection.commits == 1
    call = pool.connection.calls[0]
    assert "INSERT INTO rag_knowledge_bases" in call.statement
    assert call.parameters["name"] == "社内規程"
    assert call.parameters["default_search_mode"] == "hybrid"


async def test_oracle_client_lists_knowledge_bases_with_counts() -> None:
    """knowledge base 一覧は集計列を含む SQL で取得する。"""
    pool = FakeOraclePool(execute_results=[[_oracle_knowledge_base_row(document_count=3)]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    items = await client.list_knowledge_bases(status=KnowledgeBaseStatus.ACTIVE, query="規程")

    assert len(items) == 1
    assert items[0].id == "kb-1"
    assert items[0].document_count == 3
    call = pool.connection.calls[0]
    assert "FROM rag_knowledge_bases kb" in call.statement
    assert "LEFT JOIN rag_document_knowledge_bases dkb" in call.statement
    assert "CASE WHEN UPPER(kb.name) = 'DEFAULT' THEN 0 ELSE 1 END" in call.statement
    assert "kb.status = :knowledge_base_status" in call.statement
    assert call.parameters["knowledge_base_status"] == "ACTIVE"
    assert call.parameters["knowledge_base_query"] == "%規程%"


async def test_oracle_client_updates_knowledge_base_metadata() -> None:
    """knowledge base 更新は既存行を確認してから UPDATE する。"""
    pool = FakeOraclePool(execute_results=[[_oracle_knowledge_base_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    updated = await client.update_knowledge_base(
        "kb-1",
        name="更新済み",
        description=None,
        update_fields={"name", "description"},
    )

    assert updated.name == "更新済み"
    assert updated.description is None
    statements = [call.statement for call in pool.connection.calls]
    assert any(
        "SELECT" in statement and "rag_knowledge_bases" in statement for statement in statements
    )
    assert any("UPDATE rag_knowledge_bases" in statement for statement in statements)


async def test_oracle_client_protects_default_knowledge_base() -> None:
    """DEFAULT は改名もアーカイブもできない。"""
    update_pool = FakeOraclePool(execute_results=[[_oracle_knowledge_base_row(name="DEFAULT")]])
    update_client = OracleClient(
        settings=_oci_settings(), pool=update_pool, db_call_runner=_run_inline
    )

    with pytest.raises(ValueError, match="名前は変更できません"):
        await update_client.update_knowledge_base(
            "kb-1",
            name="別名",
            update_fields={"name"},
        )

    archive_pool = FakeOraclePool(execute_results=[[_oracle_knowledge_base_row(name="DEFAULT")]])
    archive_client = OracleClient(
        settings=_oci_settings(), pool=archive_pool, db_call_runner=_run_inline
    )

    with pytest.raises(ValueError, match="アーカイブできません"):
        await archive_client.archive_knowledge_base("kb-1")


async def test_oracle_client_assigns_documents_to_knowledge_base() -> None:
    """membership 追加は active knowledge base と document を確認して MERGE する。"""
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_knowledge_base_row()],
            [_oracle_document_row()],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.assign_documents_to_knowledge_base("kb-1", ["doc-1"])

    assert detail.id == "kb-1"
    assert pool.connection.many_calls
    many_call = pool.connection.many_calls[0]
    assert "MERGE INTO rag_document_knowledge_bases" in many_call.statement
    assert many_call.rows[0]["knowledge_base_id"] == "kb-1"
    assert many_call.rows[0]["document_id"] == "doc-1"


async def test_oracle_client_rejects_assignment_to_archived_knowledge_base() -> None:
    """アーカイブ済み knowledge base へ文書は追加できない。"""
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_knowledge_base_row(status="ARCHIVED")],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with pytest.raises(ValueError, match="アーカイブ済み"):
        await client.assign_documents_to_knowledge_base("kb-1", ["doc-1"])

    assert not pool.connection.many_calls


async def test_oci_vector_search_uses_ai_vector_search_sql() -> None:
    """OCI mode の vector search は Oracle AI Vector Search に bind 付きで問い合わせる。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:0",
                    "chunk_text": "社内規程 クラウド利用料",
                    "metadata_json": '{"chunk_index":0}',
                    "file_name": "policy.txt",
                    "category_name": "社内規程",
                    "score": 0.91,
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    hits = await client.vector_search(
        [1.0, 0.0, 0.0],
        top_k=3,
        filters={"document_id": "doc-1"},
    )

    assert len(hits) == 1
    assert hits[0].metadata["document_id"] == "doc-1"
    assert hits[0].metadata["chunk_id"] == "doc-1:0"
    assert hits[0].metadata["retrieval_mode"] == "vector"
    assert hits[0].metadata["vector_rank"] == 1
    assert hits[0].metadata["vector_score"] == 0.91
    call = pool.connection.calls[0]
    assert "VECTOR_DISTANCE" in call.statement
    assert "d.status = 'INDEXED'" in call.statement
    assert "FETCH APPROX FIRST 3 ROWS ONLY WITH TARGET ACCURACY 90" in call.statement
    assert call.parameters["embedding"] == array("f", [1.0, 0.0, 0.0])
    assert "top_k" not in call.parameters
    assert call.parameters["filter_document_id"] == "doc-1"


async def test_oci_vector_search_applies_chunk_metadata_filters() -> None:
    """OCI retrieval SQL は chunk metadata filter も bind 付きで適用する。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:2",
                    "chunk_text": "料金表 クラウド利用料",
                    "metadata_json": (
                        '{"chunk_index":2,"content_kind":"table",'
                        '"section_title":"料金表","section_path":"経費申請 > 料金表"}'
                    ),
                    "file_name": "policy.txt",
                    "category_name": "社内規程",
                    "score": 0.93,
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    hits = await client.vector_search(
        [1.0, 0.0, 0.0],
        top_k=3,
        filters={
            "content_kind": "TABLE",
            "section_title": "料金",
            "section_path": "経費申請",
        },
    )

    assert len(hits) == 1
    assert hits[0].metadata["content_kind"] == "table"
    call = pool.connection.calls[0]
    assert "JSON_VALUE(c.metadata_json, '$.content_kind')" in call.statement
    assert "JSON_VALUE(c.metadata_json, '$.section_title')" in call.statement
    assert "JSON_VALUE(c.metadata_json, '$.section_path')" in call.statement
    assert call.parameters["filter_content_kind"] == "table"
    assert call.parameters["filter_section_title"] == "%料金%"
    assert call.parameters["filter_section_path"] == "%経費申請%"


async def test_oracle_graph_local_search_uses_kg_chunk_links() -> None:
    """Graph local search は Oracle KG entity-chunk link から citation を作る。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:2",
                    "chunk_text": "承認条件は 120000 円以上です。",
                    "metadata_json": '{"chunk_index":2}',
                    "chunk_index": 2,
                    "file_name": "policy.txt",
                    "category_name": "社内規程",
                    "entity_id": "ent-1",
                    "canonical_name": "承認条件",
                    "entity_type": "policy_rule",
                    "entity_confidence": 0.92,
                    "entity_chunk_relevance": 0.88,
                    "score": 0.894,
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    hits = await client.graph_local_search(
        "承認条件",
        top_k=5,
        filters={"knowledge_base_id": "kb-1"},
    )

    assert len(hits) == 1
    assert hits[0].metadata["retrieval_mode"] == "graph_local"
    assert hits[0].metadata["graph_entity_id"] == "ent-1"
    assert hits[0].metadata["graph_entity_name"] == "承認条件"
    call = pool.connection.calls[0]
    assert "FROM rag_graph_entities e" in call.statement
    assert "JOIN rag_graph_entity_chunks ec" in call.statement
    assert "JOIN rag_chunks c" in call.statement
    assert call.parameters["top_k"] == 5
    assert call.parameters["filter_knowledge_base_id_0"] == "kb-1"
    assert call.parameters["graph_local_term_0"] == "%承認条件%"


async def test_oracle_graph_global_search_returns_community_summary_chunk() -> None:
    """Graph global search は community summary を合成 RetrievedChunk として返す。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "community_id": "comm-1",
                    "knowledge_base_id": "kb-1",
                    "level_no": 1,
                    "title": "承認と監査",
                    "summary_text": "承認条件と監査証跡の関係をまとめた要約です。",
                    "source_document_ids": '["doc-a","doc-b"]',
                    "score": 1.75,
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    hits = await client.graph_global_search("全体の関係", top_k=3)

    assert len(hits) == 1
    assert hits[0].document_id == "doc-a"
    assert hits[0].chunk_id == "community:comm-1"
    assert hits[0].metadata["retrieval_mode"] == "graph_global"
    assert hits[0].metadata["graph_source_document_count"] == 2
    call = pool.connection.calls[0]
    assert "FROM rag_graph_community_summaries g" in call.statement
    assert call.parameters["top_k"] == 3
    assert call.parameters["graph_title_exact"] == "%全体の関係%"


async def test_upsert_extraction_artifact_preserves_existing_payload_when_omitted() -> None:
    """後段 status 更新では大きい抽出 JSON を再 bind せず、既存 payload を保持する。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    await client.upsert_document_extraction_artifact(
        document_id="doc-1",
        extraction_recipe_id="ex-1",
        source_sha256="a" * 64,
        recipe_subset={"rag_parser_adapter_backend": "mineru"},
        extraction=None,
        status="materialized",
    )

    call = pool.connection.calls[0]
    matched_update = call.statement.split("WHEN MATCHED THEN UPDATE SET", 1)[1].split(
        "WHEN NOT MATCHED",
        1,
    )[0]
    assert "COALESCE(:extraction_json, t.extraction_json)" not in call.statement
    assert "t.extraction_json" not in matched_update
    assert call.parameters["extraction_json"] is None
    assert call.parameters["recipe_subset"] == {"rag_parser_adapter_backend": "mineru"}
    assert (
        call.input_sizes["extraction_json"]
        == oracle_module._json_input_sizes("extraction_json")["extraction_json"]
    )


async def test_upsert_extraction_artifact_updates_payload_when_provided() -> None:
    """抽出 payload を渡す経路では JSON 列を明示更新する。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    await client.upsert_document_extraction_artifact(
        document_id="doc-1",
        extraction_recipe_id="ex-1",
        source_sha256="a" * 64,
        extraction={"elements": [{"text": "本文"}]},
        status="materialized",
    )

    call = pool.connection.calls[0]
    matched_update = call.statement.split("WHEN MATCHED THEN UPDATE SET", 1)[1].split(
        "WHEN NOT MATCHED",
        1,
    )[0]
    assert "t.extraction_json = :extraction_json" in matched_update
    assert call.parameters["extraction_json"] == {"elements": [{"text": "本文"}]}
    assert (
        call.input_sizes["extraction_json"]
        == oracle_module._json_input_sizes("extraction_json")["extraction_json"]
    )


async def test_upsert_extraction_artifact_binds_large_payload_as_json() -> None:
    """大きい抽出 payload も VARCHAR2 ではなく JSON bind で渡す。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    large_text = "x" * 40000

    await client.upsert_document_extraction_artifact(
        document_id="doc-1",
        extraction_recipe_id="ex-1",
        source_sha256="a" * 64,
        extraction={"elements": [{"text": large_text}]},
        status="materialized",
    )

    call = pool.connection.calls[0]
    assert len(json.dumps(call.parameters["extraction_json"])) > 32767
    assert call.parameters["extraction_json"] == {"elements": [{"text": large_text}]}
    assert (
        call.input_sizes["extraction_json"]
        == oracle_module._json_input_sizes("extraction_json")["extraction_json"]
    )


async def test_oracle_replace_document_graph_index_replaces_document_scope() -> None:
    """GraphRAG-lite index 保存は対象 document の旧 KG rows を置換する。"""
    graph_index = GraphIndex(
        entities=[
            GraphEntity(
                entity_id="ent-doc",
                knowledge_base_id="kb-1",
                canonical_name="文書全体: 社内規程",
                entity_type="document",
                description="社内規程全体",
                confidence=0.95,
                source_document_ids=["doc-1"],
            ),
            GraphEntity(
                entity_id="ent-section",
                knowledge_base_id="kb-1",
                canonical_name="承認条件",
                entity_type="section",
                description="承認条件の章節",
                confidence=0.9,
                source_document_ids=["doc-1"],
            ),
        ],
        relationships=[
            GraphRelationship(
                relationship_id="rel-doc-section",
                knowledge_base_id="kb-1",
                source_entity_id="ent-doc",
                target_entity_id="ent-section",
                relationship_type="contains",
                description="文書全体は承認条件を含みます。",
                confidence=1.0,
                source_document_ids=["doc-1"],
            )
        ],
        claims=[
            GraphClaim(
                claim_id="claim-1",
                knowledge_base_id="kb-1",
                entity_id="ent-section",
                claim_text="12万円以上は部門長承認です。",
                confidence=0.88,
                source_document_id="doc-1",
                source_chunk_id="doc-1:0",
            )
        ],
        community_summaries=[
            GraphCommunitySummary(
                community_id="comm-1",
                knowledge_base_id="kb-1",
                level_no=0,
                title="社内規程 の全体要約",
                summary_text="承認条件の関係をまとめた要約です。",
                entity_ids=["ent-doc", "ent-section"],
                source_document_ids=["doc-1"],
            )
        ],
        entity_chunk_links=[
            GraphEntityChunkLink(
                entity_id="ent-section",
                chunk_id="doc-1:0",
                document_id="doc-1",
                relevance_score=0.8,
            )
        ],
    )
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_document_row()],
            [{"entity_id": "ent-doc"}, {"entity_id": "ent-old"}, {"entity_id": "ent-stale"}],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    await client.replace_document_graph_index("doc-1", graph_index)

    assert pool.connection.commits == 1
    statements = [call.statement for call in pool.connection.calls]
    assert any("DELETE FROM rag_graph_relationships" in statement for statement in statements)
    assert any(
        "FROM rag_graph_entities" in statement and "source_document_ids" in statement
        for statement in statements
    )
    assert any("source_entity_id IN" in statement for statement in statements)
    assert any("target_entity_id IN" in statement for statement in statements)
    assert any("DELETE FROM rag_graph_entity_chunks" in statement for statement in statements)
    assert any("DELETE FROM rag_graph_claims" in statement for statement in statements)
    assert any(
        "DELETE FROM rag_graph_community_summaries" in statement and "JSON_EXISTS" in statement
        for statement in statements
    )
    assert any("DELETE FROM rag_graph_entities" in statement for statement in statements)
    many_statements = [call.statement for call in pool.connection.many_calls]
    assert any("INSERT INTO rag_graph_entities" in statement for statement in many_statements)
    assert any("INSERT INTO rag_graph_relationships" in statement for statement in many_statements)
    assert any("INSERT INTO rag_graph_claims" in statement for statement in many_statements)
    assert any(
        "INSERT INTO rag_graph_community_summaries" in statement for statement in many_statements
    )
    assert any("INSERT INTO rag_graph_entity_chunks" in statement for statement in many_statements)
    entity_insert = next(
        call
        for call in pool.connection.many_calls
        if "INSERT INTO rag_graph_entities" in call.statement
    )
    assert json.loads(str(entity_insert.rows[0]["source_document_ids"])) == ["doc-1"]
    claim_insert = next(
        call
        for call in pool.connection.many_calls
        if "INSERT INTO rag_graph_claims" in call.statement
    )
    assert claim_insert.rows[0]["source_chunk_id"] == "doc-1:0"


async def test_oracle_save_evaluation_artifact_redacts_query_text() -> None:
    """評価 artifact は query 原文ではなく summary/hash だけを保存する。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    evaluation_run_id = await client.save_evaluation_artifact(
        {
            "evaluation_run_id": "eval-1",
            "knowledge_base_ids": ["kb-1"],
            "request_summary": {
                "kind": "run",
                "cases": [{"id": "case-1", "query_hash": "a" * 64, "query_chars": 12}],
            },
            "result_summary": {"case_count": 1, "passed": True},
            "passed": True,
        }
    )

    assert evaluation_run_id == "eval-1"
    assert pool.connection.commits == 1
    call = pool.connection.calls[0]
    assert "INSERT INTO rag_evaluation_runs" in call.statement
    assert call.parameters["evaluation_run_id"] == "eval-1"
    assert call.parameters["passed"] == 1
    assert len(str(call.parameters["result_sha256"])) == 64
    assert "query_text" not in str(call.parameters)


async def test_oci_save_chunks_replaces_existing_chunks_and_binds_vectors() -> None:
    """OCI mode の chunk 保存は既存 chunk を消して VECTOR bind を挿入する。"""
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    saved = await client.save_chunks(
        "doc-1",
        [
            Chunk(
                index=0,
                text="社内規程",
                start_offset=0,
                end_offset=3,
                metadata={"section_path": "経費申請 > 承認", "content_kind": "text"},
            )
        ],
        [[1.0, 0.0, 0.0]],
    )

    assert saved[0].chunk_id == "doc-1:0"
    assert saved[0].metadata["document_id"] == "doc-1"
    assert saved[0].metadata["chunk_id"] == "doc-1:0"
    assert saved[0].metadata["chunk_index"] == 0
    assert saved[0].metadata["section_path"] == "経費申請 > 承認"
    assert pool.connection.commits == 1
    statements = [call.statement for call in pool.connection.calls]
    assert any("DELETE FROM rag_chunks" in statement for statement in statements)
    assert pool.connection.many_calls
    inserted = pool.connection.many_calls[0].rows[0]
    assert inserted["chunk_id"] == "doc-1:0"
    assert inserted["embedding"] == array("f", [1.0, 0.0, 0.0])
    metadata = json.loads(str(inserted["metadata_json"]))
    assert metadata["document_id"] == "doc-1"
    assert metadata["chunk_id"] == "doc-1:0"
    assert metadata["chunk_index"] == 0
    assert metadata["section_path"] == "経費申請 > 承認"
    assert metadata["content_kind"] == "text"


async def test_oci_save_index_persists_extraction_and_chunks_atomically() -> None:
    """index 保存は extraction と chunk/vector を 1 transaction で置換する。"""
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    saved = await client.save_index(
        "doc-1",
        StructuredExtraction(raw_text="新しい抽出本文", confidence=0.98),
        [
            Chunk(
                index=0,
                text="新しい抽出本文",
                start_offset=0,
                end_offset=7,
                metadata={"section_path": "本文", "content_kind": "text"},
            )
        ],
        [[0.1, 0.2, 0.3]],
    )

    assert saved[0].chunk_id == "doc-1:0"
    assert saved[0].metadata["content_kind"] == "text"
    assert pool.connection.commits == 1
    statements = [call.statement for call in pool.connection.calls]
    update_index = next(
        index for index, statement in enumerate(statements) if "UPDATE rag_documents" in statement
    )
    delete_index = next(
        index for index, statement in enumerate(statements) if "DELETE FROM rag_chunks" in statement
    )
    assert update_index < delete_index
    update_call = pool.connection.calls[update_index]
    extraction_payload = json.loads(str(update_call.parameters["extraction"]))
    assert extraction_payload["raw_text"] == "新しい抽出本文"
    inserted = pool.connection.many_calls[0].rows[0]
    assert inserted["chunk_id"] == "doc-1:0"
    assert inserted["embedding"] == array("f", [0.1, 0.2, 0.3])


async def test_oci_list_chunk_metadata_adds_traceable_lineage() -> None:
    """metadata 一覧も citation 可視化に必要な chunk lineage を補完する。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:4",
                    "chunk_index": 4,
                    "metadata_json": json.dumps(
                        {
                            "content_kind": "table",
                            "element_ids": "tbl-1",
                            "page_start": 2,
                            "page_end": 3,
                            "bbox": "[0.1,0.2,0.8,0.9]",
                            "chunk_group_id": "grp-table",
                            "source_parser": "marker",
                        }
                    ),
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    metadata = await client.list_chunk_metadata()

    assert metadata == [
        {
            "document_id": "doc-1",
            "chunk_id": "doc-1:4",
            "chunk_index": 4,
            "content_kind": "table",
            "element_ids": "tbl-1",
            "page_start": 2,
            "page_end": 3,
            "bbox": "[0.1,0.2,0.8,0.9]",
            "chunk_group_id": "grp-table",
            "source_parser": "marker",
        }
    ]
    call = pool.connection.calls[0]
    assert "c.document_id" in call.statement
    assert "c.chunk_id" in call.statement
    assert "c.chunk_index" in call.statement
    assert "c.metadata_json" in call.statement


async def test_oci_list_document_chunks_accepts_json_element_ids_and_row_group_metadata() -> None:
    """chunk view は JSON array element_ids と table row-group metadata を保持する。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:7",
                    "chunk_index": 7,
                    "chunk_text": "|項目|金額|\n|---|---|\n|交通費|1000円|",
                    "metadata_json": json.dumps(
                        {
                            "content_kind": "table",
                            "element_ids": ["tbl-main", "row-group-1"],
                            "page_start": 2,
                            "page_end": 2,
                            "bbox": [10, 20, 80, 40],
                            "chunk_group_id": "grp-table",
                            "source_parser": "local_office_structure",
                            "bbox_coordinate_mode": "xywh",
                            "bbox_unit": "percent",
                            "dependency_edges": [
                                {"parent_id": "tbl-main", "child_id": "row-group-1"}
                            ],
                            "table_data_row_start": 1,
                            "table_data_row_end": 12,
                            "table_header_repeated": True,
                        }
                    ),
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    chunks = await client.list_document_chunks("doc-1")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "doc-1:7"
    assert chunk.element_ids == ["tbl-main", "row-group-1"]
    assert chunk.bbox == [10.0, 20.0, 80.0, 40.0]
    assert chunk.content_kind == "table"
    assert chunk.chunk_group_id == "grp-table"
    assert chunk.metadata["element_ids"] == ["tbl-main", "row-group-1"]
    assert chunk.metadata["bbox"] == [10, 20, 80, 40]
    assert chunk.metadata["dependency_edges"] == [
        {"parent_id": "tbl-main", "child_id": "row-group-1"}
    ]
    assert chunk.metadata["bbox_coordinate_mode"] == "xywh"
    assert chunk.metadata["bbox_unit"] == "percent"
    assert chunk.metadata["table_data_row_start"] == 1
    assert chunk.metadata["table_data_row_end"] == 12
    assert chunk.metadata["table_header_repeated"] is True


@IN_MEMORY_ORACLE_REMOVED
async def test_local_retrieval_filters_by_chunk_metadata() -> None:
    """local reference store でも chunk metadata filter を検索前に適用する。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [
            Chunk(
                index=0,
                text="クラウド利用料の申請本文",
                start_offset=0,
                end_offset=12,
                metadata={
                    "content_kind": "text",
                    "section_title": "本文",
                    "section_path": "経費申請 > 本文",
                },
            ),
            Chunk(
                index=1,
                text="クラウド利用料 料金表",
                start_offset=13,
                end_offset=25,
                metadata={
                    "content_kind": "table",
                    "section_title": "料金表",
                    "section_path": "経費申請 > 料金表",
                },
            ),
        ],
        [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)

    hits = await client.keyword_search(
        "クラウド利用料",
        top_k=10,
        filters={
            "content_kind": "TABLE",
            "section_title": "料金",
            "section_path": "経費申請",
        },
    )

    assert len(hits) == 1
    assert hits[0].metadata["chunk_index"] == 1
    assert hits[0].metadata["content_kind"] == "table"
    assert hits[0].metadata["section_title"] == "料金表"


@IN_MEMORY_ORACLE_REMOVED
async def test_local_context_neighbors_returns_searchable_adjacent_chunks() -> None:
    """local store の隣接 context は同一 document の検索可能 chunk だけを返す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [
            Chunk(index=0, text="前段: 申請条件。", start_offset=0, end_offset=8),
            Chunk(index=1, text="中心: 承認条件。", start_offset=8, end_offset=16),
            Chunk(index=2, text="後段: 証憑要件。", start_offset=16, end_offset=24),
        ],
        [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.8, 0.2, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)
    anchor = RetrievedChunk(
        document_id=document.id,
        chunk_id=f"{document.id}:1",
        text="中心: 承認条件。",
        score=0.92,
        file_name="policy.txt",
        metadata={"chunk_index": 1},
    )

    neighbors = await client.context_neighbors([anchor], window=1)

    assert [chunk.chunk_id for chunk in neighbors] == [
        f"{document.id}:0",
        f"{document.id}:2",
    ]
    assert [chunk.metadata["context_neighbor_distance"] for chunk in neighbors] == [-1, 1]
    for neighbor in neighbors:
        assert neighbor.score == 0.92
        assert neighbor.metadata["context_expanded"] is True
        assert neighbor.metadata["context_anchor_chunk_id"] == anchor.chunk_id


@IN_MEMORY_ORACLE_REMOVED
async def test_local_context_group_siblings_returns_same_group_chunks() -> None:
    """local store の同一 group context は lineage metadata で sibling を返す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="table-policy.txt",
        object_storage_path="local://uploaded/table-policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [
            Chunk(
                index=0,
                text="表ヘッダー: 項目 / 条件。",
                start_offset=0,
                end_offset=12,
                metadata={"chunk_group_id": "grp-table", "content_kind": "table"},
            ),
            Chunk(
                index=1,
                text="表行: 承認条件 / 120000 円以上。",
                start_offset=12,
                end_offset=28,
                metadata={"chunk_group_id": "grp-table", "content_kind": "table"},
            ),
            Chunk(
                index=2,
                text="表注記: 証憑添付が必要。",
                start_offset=28,
                end_offset=40,
                metadata={"chunk_group_id": "grp-table", "content_kind": "table"},
            ),
            Chunk(
                index=3,
                text="別 group の注記。",
                start_offset=40,
                end_offset=48,
                metadata={"chunk_group_id": "grp-other", "content_kind": "table"},
            ),
        ],
        [
            [1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0],
            [0.8, 0.2, 0.0],
            [0.7, 0.3, 0.0],
        ],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)
    anchor = RetrievedChunk(
        document_id=document.id,
        chunk_id=f"{document.id}:1",
        text="表行: 承認条件 / 120000 円以上。",
        score=0.92,
        file_name="table-policy.txt",
        metadata={"chunk_index": 1, "chunk_group_id": "grp-table"},
    )

    siblings = await client.context_group_siblings([anchor], max_chunks_per_group=2)

    assert [chunk.chunk_id for chunk in siblings] == [
        f"{document.id}:0",
        f"{document.id}:2",
    ]
    assert [chunk.metadata["context_group_distance"] for chunk in siblings] == [-1, 1]
    for sibling in siblings:
        assert sibling.score == 0.92
        assert sibling.metadata["context_group_expanded"] is True
        assert sibling.metadata["context_anchor_chunk_id"] == anchor.chunk_id
        assert sibling.metadata["context_group_id"] == "grp-table"


async def test_oci_context_neighbors_uses_chunk_index_window_sql() -> None:
    """OCI mode の隣接 context は chunk_index window を bind 付き SQL で取得する。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:1",
                    "chunk_text": "前段: 申請条件。",
                    "metadata_json": "{}",
                    "chunk_index": 1,
                    "file_name": "policy.txt",
                    "category_name": "社内規程",
                    "score": 0,
                },
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:3",
                    "chunk_text": "後段: 証憑要件。",
                    "metadata_json": "{}",
                    "chunk_index": 3,
                    "file_name": "policy.txt",
                    "category_name": "社内規程",
                    "score": 0,
                },
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    anchor = RetrievedChunk(
        document_id="doc-1",
        chunk_id="doc-1:2",
        text="中心: 承認条件。",
        score=0.91,
        file_name="policy.txt",
        metadata={"chunk_index": 2},
    )

    neighbors = await client.context_neighbors([anchor], window=1)

    assert [chunk.chunk_id for chunk in neighbors] == ["doc-1:1", "doc-1:3"]
    assert [chunk.metadata["chunk_index"] for chunk in neighbors] == [1, 3]
    assert [chunk.metadata["context_neighbor_distance"] for chunk in neighbors] == [-1, 1]
    assert all(chunk.score == 0.91 for chunk in neighbors)
    call = pool.connection.calls[0]
    assert "c.chunk_index BETWEEN :start_index AND :end_index" in call.statement
    assert "c.chunk_id <> :anchor_chunk_id" in call.statement
    assert "ABS(c.chunk_index - :anchor_index)" in call.statement
    assert "d.status = 'INDEXED'" in call.statement
    assert "d.document_id = :filter_document_id" in call.statement
    assert call.parameters["filter_document_id"] == "doc-1"
    assert call.parameters["anchor_index"] == 2
    assert call.parameters["start_index"] == 1
    assert call.parameters["end_index"] == 3
    assert call.parameters["anchor_chunk_id"] == "doc-1:2"


async def test_oci_context_group_siblings_uses_chunk_group_sql() -> None:
    """OCI mode の同一 group context は chunk_group_id を bind 付き SQL で取得する。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:1",
                    "chunk_text": "表ヘッダー: 項目 / 条件。",
                    "metadata_json": json.dumps({"chunk_group_id": "grp-table"}),
                    "chunk_index": 1,
                    "file_name": "policy.txt",
                    "category_name": "社内規程",
                    "score": 0,
                },
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:3",
                    "chunk_text": "表注記: 証憑添付が必要。",
                    "metadata_json": json.dumps({"chunk_group_id": "grp-table"}),
                    "chunk_index": 3,
                    "file_name": "policy.txt",
                    "category_name": "社内規程",
                    "score": 0,
                },
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    anchor = RetrievedChunk(
        document_id="doc-1",
        chunk_id="doc-1:2",
        text="表行: 承認条件 / 120000 円以上。",
        score=0.91,
        file_name="policy.txt",
        metadata={"chunk_index": 2, "chunk_group_id": "grp-table"},
    )

    siblings = await client.context_group_siblings([anchor], max_chunks_per_group=2)

    assert [chunk.chunk_id for chunk in siblings] == ["doc-1:1", "doc-1:3"]
    assert [chunk.metadata["chunk_index"] for chunk in siblings] == [1, 3]
    assert [chunk.metadata["context_group_distance"] for chunk in siblings] == [-1, 1]
    assert all(chunk.score == 0.91 for chunk in siblings)
    assert all(chunk.metadata["context_group_expanded"] is True for chunk in siblings)
    call = pool.connection.calls[0]
    assert "JSON_VALUE(c.metadata_json, '$.chunk_group_id') = :chunk_group_id" in (call.statement)
    assert "c.chunk_id <> :anchor_chunk_id" in call.statement
    assert "ABS(c.chunk_index - :anchor_index)" in call.statement
    assert "ROWNUM <= :max_chunks_per_group" in call.statement
    assert "d.status = 'INDEXED'" in call.statement
    assert "d.document_id = :filter_document_id" in call.statement
    assert call.parameters["filter_document_id"] == "doc-1"
    assert call.parameters["chunk_group_id"] == "grp-table"
    assert call.parameters["anchor_index"] == 2
    assert call.parameters["anchor_chunk_id"] == "doc-1:2"
    assert call.parameters["max_chunks_per_group"] == 2


async def test_oci_context_dependency_chunks_uses_dependency_metadata_sql() -> None:
    """OCI mode の dependency context は anchor lineage token で候補を絞り込む。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:3",
                    "chunk_text": "キャプション: 120000 円以上は部門長承認。",
                    "metadata_json": json.dumps(
                        {
                            "chunk_index": 3,
                            "element_ids": "fig-1-caption",
                            "parent_element_ids": "fig-1",
                            "dependency_edges": [
                                {"parent_id": "fig-1", "child_id": "fig-1-caption"}
                            ],
                        }
                    ),
                    "chunk_index": 3,
                    "file_name": "approval.pdf",
                    "category_name": "社内規程",
                    "score": 0,
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    anchor = RetrievedChunk(
        document_id="doc-1",
        chunk_id="doc-1:2",
        text="図: 承認フロー。",
        score=0.91,
        file_name="approval.pdf",
        metadata={"chunk_index": 2, "element_ids": "fig-1"},
    )

    chunks = await client.context_dependency_chunks([anchor], max_chunks_per_anchor=2)

    assert [chunk.chunk_id for chunk in chunks] == ["doc-1:3"]
    assert chunks[0].score == 0.91
    assert chunks[0].metadata["parent_element_ids"] == "fig-1"
    call = pool.connection.calls[0]
    assert "d.status = 'INDEXED'" in call.statement
    assert "d.document_id = :filter_document_id" in call.statement
    assert "c.chunk_id <> :anchor_chunk_id" in call.statement
    assert "JSON_SERIALIZE(c.metadata_json RETURNING VARCHAR2(32767))" in call.statement
    assert "LIKE :dependency_token_0 ESCAPE '\\'" in call.statement
    assert "ROWNUM <= :candidate_limit" in call.statement
    assert call.parameters["filter_document_id"] == "doc-1"
    assert call.parameters["anchor_index"] == 2
    assert call.parameters["anchor_chunk_id"] == "doc-1:2"
    assert call.parameters["candidate_limit"] == 16
    assert call.parameters["dependency_token_0"] == "%fig-1%"


async def test_oci_context_dependency_chunks_falls_back_without_anchor_lineage() -> None:
    """旧 metadata で anchor lineage がない場合は従来の lineage metadata 候補へ戻す。"""
    pool = FakeOraclePool(execute_results=[[]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    anchor = RetrievedChunk(
        document_id="doc-1",
        chunk_id="doc-1:2",
        text="図: 承認フロー。",
        score=0.91,
        file_name="approval.pdf",
        metadata={"chunk_index": 2},
    )

    chunks = await client.context_dependency_chunks([anchor], max_chunks_per_anchor=2)

    assert chunks == []
    call = pool.connection.calls[0]
    assert "JSON_EXISTS(c.metadata_json, '$.element_ids')" in call.statement
    assert "JSON_EXISTS(c.metadata_json, '$.parent_element_ids')" in call.statement
    assert "JSON_EXISTS(c.metadata_json, '$.dependency_edges')" in call.statement
    assert "dependency_token_0" not in call.parameters


async def test_oci_update_error_status_preserves_chunks_and_extraction() -> None:
    """ERROR への状態遷移では段階レビュー用の chunk と抽出 JSON を保持する。"""
    extraction = {"document_type": "text", "raw_text": "抽出済み"}
    errored = _oracle_document_row(status="ERROR", extraction=extraction)
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()], [errored]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.update_document_status(
        "doc-1",
        FileStatus.ERROR,
        "再分析に失敗しました。",
    )

    assert detail.status == FileStatus.ERROR
    assert detail.extraction == extraction
    statements = [call.statement for call in pool.connection.calls]
    assert not any("DELETE FROM rag_chunks" in statement for statement in statements)
    assert not any("extraction = NULL" in statement for statement in statements)
    assert pool.connection.commits == 1


async def test_oci_delete_document_removes_chunks_and_document_with_access_scope() -> None:
    """OCI mode の削除は access scope を維持して document と chunk を同一 transaction で消す。"""
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="request-1",
            allowed_document_ids=frozenset({"doc-1"}),
        )
    )
    try:
        deleted = await client.delete_document("doc-1")
    finally:
        reset_audit_request_context(token)

    assert deleted is True
    statements = [call.statement for call in pool.connection.calls]
    assert statements[0].startswith("SELECT")
    assert "document_id IN (:access_document_id_0)" in statements[0]
    assert any("FROM rag_graph_entity_chunks" in statement for statement in statements)
    assert any("DELETE FROM rag_graph_entity_chunks" in statement for statement in statements)
    assert any("DELETE FROM rag_graph_claims" in statement for statement in statements)
    assert any("DELETE FROM rag_graph_community_summaries" in statement for statement in statements)
    duplicate_clear = next(
        call
        for call in pool.connection.calls
        if call.statement.startswith("UPDATE rag_documents")
        and "duplicate_of_document_id = NULL" in call.statement
    )
    assert "duplicate_of_document_id = :document_id" in duplicate_clear.statement
    assert duplicate_clear.parameters["document_id"] == "doc-1"
    segment_delete_index = next(
        index
        for index, statement in enumerate(statements)
        if "DELETE FROM rag_ingestion_segments" in statement
    )
    job_delete_index = next(
        index
        for index, statement in enumerate(statements)
        if "DELETE FROM rag_ingestion_jobs" in statement
    )
    chunk_delete_index = next(
        index for index, statement in enumerate(statements) if "DELETE FROM rag_chunks" in statement
    )
    document_delete = next(
        call
        for call in pool.connection.calls
        if call.statement.startswith("DELETE FROM rag_documents")
    )
    document_delete_index = statements.index(document_delete.statement)
    assert segment_delete_index < document_delete_index
    assert job_delete_index < document_delete_index
    assert chunk_delete_index < document_delete_index
    assert "d.document_id IN (:access_document_id_0)" in statements[segment_delete_index]
    assert "d.document_id IN (:access_document_id_0)" in statements[job_delete_index]
    assert "document_id = :document_id" in document_delete.statement
    assert "document_id IN (:access_document_id_0)" in document_delete.statement
    assert document_delete.parameters["document_id"] == "doc-1"
    assert document_delete.parameters["access_document_id_0"] == "doc-1"
    assert pool.connection.commits == 1


async def test_oci_delete_document_rolls_back_when_running_job_appears() -> None:
    """削除 transaction 中に RUNNING job が見えたら document 物理削除へ進まない。"""
    pool = FakeOraclePool(
        execute_results=[
            [_oracle_document_row()],
            [],
            [{"job_id": "job-running"}],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with pytest.raises(DocumentDeleteBlockedByRunningIngestionError):
        await client.delete_document("doc-1")

    statements = [call.statement for call in pool.connection.calls]
    assert any("DELETE FROM rag_ingestion_jobs" in statement for statement in statements)
    assert any("j.status = 'RUNNING'" in statement for statement in statements)
    assert not any(statement.startswith("DELETE FROM rag_documents") for statement in statements)
    assert pool.connection.commits == 0
    assert pool.connection.rollbacks == 1


async def test_oci_retrieval_applies_request_access_scope_predicates() -> None:
    """OCI retrieval SQL は request context の認可済み scope を必ず含める。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="request-1",
            allowed_document_ids=frozenset({"doc-allowed"}),
            allowed_category_names=frozenset({"社内規程".casefold()}),
            allowed_knowledge_base_ids=frozenset({"kb-allowed"}),
        )
    )
    try:
        hits = await client.keyword_search("社内規程", top_k=3)
    finally:
        reset_audit_request_context(token)

    assert hits == []
    call = pool.connection.calls[0]
    assert "d.document_id IN (:access_document_id_0)" in call.statement
    assert "LOWER(d.category_name) IN (:access_category_name_0)" in call.statement
    assert "rag_document_knowledge_bases dkb" in call.statement
    assert "kb.knowledge_base_id IN (:access_knowledge_base_id_0)" in call.statement
    assert call.parameters["access_document_id_0"] == "doc-allowed"
    assert call.parameters["access_category_name_0"] == "社内規程".casefold()
    assert call.parameters["access_knowledge_base_id_0"] == "kb-allowed"


async def test_oci_retrieval_applies_multiple_knowledge_base_filters() -> None:
    """OCI retrieval SQL は複数 KB filter を bind 付き IN 条件で適用する。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    hits = await client.keyword_search(
        "社内規程",
        top_k=3,
        filters={"knowledge_base_id": "kb-1,kb-2"},
    )

    assert hits == []
    call = pool.connection.calls[0]
    assert "rag_document_knowledge_bases dkb" in call.statement
    assert (
        "dkb.knowledge_base_id IN " "(:filter_knowledge_base_id_0, :filter_knowledge_base_id_1)"
    ) in call.statement
    assert call.parameters["filter_knowledge_base_id_0"] == "kb-1"
    assert call.parameters["filter_knowledge_base_id_1"] == "kb-2"


def test_oracle_text_terms_extracts_safe_display_keywords() -> None:
    """表示用 keyword terms は自然文から安全な短い語だけを返す。"""
    terms = oracle_text_terms("社内規程の申請フローは？")

    assert terms == ["社内", "規程", "申請", "フロー"]
    assert "？" not in "".join(terms)
    assert not {"の申", "請フ", "ーは"} & set(terms)
    assert len(terms) <= 12


def test_oracle_text_terms_filters_japanese_particles_and_english_stopwords() -> None:
    """日本語助詞と英語 stopword は UI/Oracle Text query に出さない。"""
    assert set(oracle_module.ORACLE_TEXT_STOP_WORDS) == oracle_module.JAPANESE_QUERY_STOP_TERMS
    assert oracle_text_terms("私の上司の興味はなんですか") == ["上司", "興味"]
    assert oracle_text_terms("申請へ承認") == ["申請", "承認"]
    assert oracle_text_terms("what is the expense policy and who approves it") == [
        "expense",
        "policy",
        "approves",
    ]
    assert oracle_text_terms("IT policy for HR") == ["it", "policy", "hr"]


async def test_oci_keyword_search_normalizes_natural_language_query() -> None:
    """自然文 keyword query は Oracle Text 用の安全な term expression に変換する。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    raw_query = "社内規程の申請フローは？"

    hits = await client.keyword_search(raw_query, top_k=3)

    assert hits == []
    call = pool.connection.calls[0]
    text_query = call.parameters["query"]
    assert isinstance(text_query, str)
    assert text_query != raw_query
    assert "？" not in text_query
    assert "{社内規程}" not in text_query
    assert "{社内}" in text_query
    assert "{規程}" in text_query
    assert "{フロー}" in text_query
    assert " ACCUM " in text_query
    assert "CONTAINS(c.chunk_text, :query, 1) > 0" in call.statement


async def test_oci_keyword_search_escapes_oracle_text_special_syntax() -> None:
    """Oracle Text の演算子/記号は literal term 化して query syntax にしない。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    await client.keyword_search('policy - NEAR(oracle?) OR "x"', top_k=3)

    text_query = pool.connection.calls[0].parameters["query"]
    assert isinstance(text_query, str)
    assert "{policy}" in text_query
    assert "{oracle}" in text_query
    assert "{near}" not in text_query
    assert "{or}" not in text_query
    assert all(char not in text_query for char in '-?()"')


async def test_oci_keyword_search_empty_normalized_query_skips_database() -> None:
    """検索語にできない入力は DB に投げず空結果にする。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    hits = await client.keyword_search("？！--", top_k=3)

    assert hits == []
    assert pool.acquire_calls == 0
    assert pool.connection.calls == []

    hits = await client.keyword_search("の んで です what is the", top_k=3)

    assert hits == []
    assert pool.acquire_calls == 0
    assert pool.connection.calls == []


async def test_oci_hybrid_search_uses_normalized_keyword_query() -> None:
    """hybrid 内の keyword branch も同じ Oracle Text query normalization を通る。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    raw_query = "社内規程の申請フローは？"

    hits = await client.hybrid_search(
        query=raw_query,
        embedding=[1.0, 0.0, 0.0],
        top_k=3,
        mode=SearchMode.HYBRID,
    )

    assert hits == []
    keyword_call = next(call for call in pool.connection.calls if "CONTAINS" in call.statement)
    text_query = keyword_call.parameters["query"]
    assert isinstance(text_query, str)
    assert text_query != raw_query
    assert "{社内規程}" not in text_query
    assert "{社内}" in text_query
    assert "？" not in text_query


async def test_oracle_agent_memory_search_applies_hashed_scope_predicates() -> None:
    """Agent Memory search SQL は raw ID ではなく hash scope の bind だけで絞り込む。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="request-memory-sql",
            tenant_id_hash="a" * 64,
            user_id_hash="b" * 64,
            role_id_hash="c" * 64,
            agent_id_hash="d" * 64,
            thread_id_hash="e" * 64,
        )
    )
    try:
        hits = await client.agent_memory_search(
            "承認条件",
            [1.0, 0.0, 0.0],
            top_k=3,
        )
    finally:
        reset_audit_request_context(token)

    assert hits == []
    call = pool.connection.calls[0]
    assert "FROM rag_agent_memories m" in call.statement
    assert "m.tenant_id_hash = :agent_memory_tenant_id_hash" in call.statement
    assert "m.user_id_hash = :agent_memory_user_id_hash" in call.statement
    assert "m.role_id_hash = :agent_memory_role_id_hash" in call.statement
    assert "m.agent_id_hash = :agent_memory_agent_id_hash" in call.statement
    assert "m.thread_id_hash = :agent_memory_thread_id_hash" in call.statement
    assert call.parameters["agent_memory_tenant_id_hash"] == "a" * 64
    assert call.parameters["agent_memory_user_id_hash"] == "b" * 64
    assert call.parameters["agent_memory_role_id_hash"] == "c" * 64
    assert call.parameters["agent_memory_agent_id_hash"] == "d" * 64
    assert call.parameters["agent_memory_thread_id_hash"] == "e" * 64


@IN_MEMORY_ORACLE_REMOVED
async def test_local_update_missing_document_raises_key_error() -> None:
    """存在しない document の状態更新は明示的に失敗する。"""
    client = OracleClient()

    with pytest.raises(KeyError):
        await client.update_document_status("missing", FileStatus.INGESTING)


@IN_MEMORY_ORACLE_REMOVED
async def test_local_delete_document_removes_chunks_and_is_idempotent() -> None:
    """local reference store でも document 削除時に関連 chunk を消す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="社内規程", start_offset=0, end_offset=4)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)
    assert await client.count_chunks() == 1

    assert await client.delete_document(document.id) is True
    assert await client.delete_document(document.id) is False
    assert await client.get_document(document.id) is None
    assert await client.count_chunks() == 0


@IN_MEMORY_ORACLE_REMOVED
async def test_local_find_document_by_content_hash_prefers_original_document() -> None:
    """重複検索は重複行ではなく最初の原本ドキュメントを返す。"""
    client = OracleClient()
    content_hash = "a" * 64
    original = await client.create_document(
        file_name="original.txt",
        object_storage_path="local://uploaded/original.txt",
        content_type="text/plain",
        file_size_bytes=12,
        content_sha256=content_hash,
    )
    await client.create_document(
        file_name="duplicate.txt",
        object_storage_path="local://uploaded/duplicate.txt",
        content_type="text/plain",
        file_size_bytes=12,
        content_sha256=content_hash,
        duplicate_of_document_id=original.id,
    )

    found = await client.find_document_by_content_hash(content_hash)

    assert found is not None
    assert found.id == original.id
    assert found.content_sha256 == content_hash
    assert found.file_size_bytes == 12


def test_oracle_document_schema_includes_ingestion_metadata_columns() -> None:
    """Oracle document DDL 例は ingestion 監査用メタデータ列を含む。"""
    ddl = oracle_document_schema_sql()

    assert "tenant_id_hash           CHAR(64)" in ddl
    assert "content_sha256           CHAR(64)" in ddl
    assert "file_size_bytes          NUMBER(19)" in ddl
    assert "duplicate_of_document_id VARCHAR2(64)" in ddl
    assert "rag_documents_content_sha256_idx" in ddl
    assert "rag_documents_tenant_status_uploaded_idx" in ddl
    assert "'UPLOADED', 'PREPROCESSING', 'PREPROCESSED', 'INGESTING', 'REVIEW'," in ddl
    assert "'INDEXING', 'INDEXED', 'ERROR'" in ddl


def test_oracle_knowledge_base_schema_includes_membership_tables() -> None:
    """Oracle knowledge base DDL 例は KB と membership table を含む。"""
    ddl = oracle_knowledge_base_schema_sql()

    assert "CREATE TABLE rag_knowledge_bases" in ddl
    assert "CREATE TABLE rag_document_knowledge_bases" in ddl
    assert "knowledge_base_id     VARCHAR2(64) PRIMARY KEY" in ddl
    assert "default_search_mode   VARCHAR2(16) DEFAULT 'hybrid' NOT NULL" in ddl
    assert "rag_knowledge_bases_tenant_name_uidx" in ddl
    assert "FOREIGN KEY (document_id)" in ddl
    assert "REFERENCES rag_documents (document_id)" in ddl


def test_oracle_ingestion_job_schema_includes_queue_table() -> None:
    """Oracle ingestion job DDL 例は永続 queue table を含む。"""
    ddl = oracle_ingestion_job_schema_sql()

    assert "CREATE TABLE rag_ingestion_jobs" in ddl
    assert "job_id           VARCHAR2(64) PRIMARY KEY" in ddl
    assert "document_id      VARCHAR2(64) NOT NULL" in ddl
    assert "quality_warnings JSON" in ddl
    assert "attempt_count    NUMBER(5) DEFAULT 0 NOT NULL" in ddl
    assert "max_attempts     NUMBER(5) DEFAULT 3 NOT NULL" in ddl
    assert "phase            VARCHAR2(16) DEFAULT 'PREPROCESS' NOT NULL" in ddl
    assert (
        "CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'CANCELLED'))"
        in ddl
    )
    assert "CHECK (phase IN ('PREPROCESS', 'EXTRACT', 'CHUNK', 'INDEX'))" in ddl
    assert "CHECK (attempt_count >= 0 AND max_attempts >= 1)" in ddl
    assert "REFERENCES rag_documents (document_id)" in ddl
    assert "ON rag_ingestion_jobs (tenant_id_hash, status, queued_at DESC)" in ddl


def test_oracle_ingestion_segment_schema_includes_checkpoint_table() -> None:
    """segment checkpoint は document 単位で page range / artifact path を永続化する。"""
    ddl = oracle_ingestion_segment_schema_sql()

    assert "CREATE TABLE rag_ingestion_segments" in ddl
    assert "segment_id     VARCHAR2(128) PRIMARY KEY" in ddl
    assert "artifact_path  VARCHAR2(1024)" in ddl
    assert "REFERENCES rag_documents (document_id)" in ddl
    assert "rag_ingestion_segments_document_status_idx" in ddl
    assert "CHECK (page_start IS NULL OR page_end IS NULL OR page_start <= page_end)" in ddl


def test_oracle_vector_schema_includes_tenant_filter_columns() -> None:
    """chunk/vector DDL 例は tenant filter 用の列と索引を含む。"""
    ddl = oracle_vector_schema_sql()

    assert "tenant_id_hash  CHAR(64)" in ddl
    assert "embedding       VECTOR(1536, FLOAT32)," in ddl
    assert "embedding       VECTOR(1536, FLOAT32) NOT NULL" not in ddl
    assert "CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx" in ddl
    assert "ORGANIZATION INMEMORY NEIGHBOR GRAPH" in ddl
    assert "DISTANCE COSINE" in ddl
    assert "WITH TARGET ACCURACY 95" in ddl
    assert "TYPE HNSW" in ddl
    assert "NEIGHBORS 32" in ddl
    assert "EFCONSTRUCTION 500" in ddl
    assert "CTX_DDL.CREATE_PREFERENCE('RAG_TEXT_WORLD_LEXER', 'WORLD_LEXER')" in ddl
    assert "CTX_DDL.CREATE_STOPLIST('RAG_TEXT_STOPLIST', 'BASIC_STOPLIST')" in ddl
    assert "ADD_STOPWORD('RAG_TEXT_STOPLIST', p_word)" in ddl
    assert "add_stopword('の')" in ddl
    assert "add_stopword('へ')" in ddl
    assert (
        "PARAMETERS ('LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST SYNC (ON COMMIT)')"
        in ddl
    )
    assert "rag_chunks_tenant_document_idx" in ddl
    assert "ON rag_chunks (tenant_id_hash, document_id, chunk_index)" in ddl
    assert "INDEXTYPE IS CTXSYS.CONTEXT" in ddl


def test_oracle_search_audit_schema_redacts_query_body() -> None:
    """検索監査 DDL は query 原文ではなく hash と集計値だけを保存する。"""
    ddl = oracle_search_audit_schema_sql()
    normalized = ddl.lower()

    assert "create table rag_search_audit" in normalized
    assert "trace_id              varchar2(64) not null" in normalized
    assert "request_id            varchar2(128)" in normalized
    assert "tenant_id_hash        char(64)" in normalized
    assert "user_id_hash          char(64)" in normalized
    assert "search_mode           varchar2(16) not null" in normalized
    assert "query_hash            char(64) not null" in normalized
    assert "top_k                 number(10)" in normalized
    assert "rerank_top_n          number(10)" in normalized
    assert "query_variant_count   number(10) default 1 not null" in normalized
    assert "guardrail_codes       json" in normalized
    assert "reranked_count        number(10) default 0 not null" in normalized
    assert "deduplicated_count    number(10) default 0 not null" in normalized
    assert "context_diversified_count number(10) default 0 not null" in normalized
    assert "context_group_expanded_count number(10) default 0 not null" in normalized
    assert "context_expanded_count number(10) default 0 not null" in normalized
    assert "context_adaptive_expanded_count number(10) default 0 not null" in normalized
    assert "context_dependency_promoted_count number(10) default 0 not null" in normalized
    assert "context_compressed_count number(10) default 0 not null" in normalized
    assert "context_compression_saved_chars number(10) default 0 not null" in normalized
    assert "agent_memory_retrieved_count number(10) default 0 not null" in normalized
    assert "agent_memory_writeback_count number(10) default 0 not null" in normalized
    assert "agent_memory_writeback_status varchar2(32) default 'skipped' not null" in normalized
    assert "context_chars         number(10) default 0 not null" in normalized
    assert "config_fingerprint    char(64)" in normalized
    assert "document_ids          json" in normalized
    assert "knowledge_base_ids    json" in normalized
    assert "check (outcome in ('success', 'blocked', 'no_results', 'error'))" in normalized
    assert "check (search_mode in ('hybrid', 'vector', 'keyword'))" in normalized
    assert "check (agent_memory_writeback_status in ('skipped', 'saved', 'failed'))" in normalized
    assert "rag_search_audit_created_outcome_idx" in ddl
    assert "rag_search_audit_tenant_created_idx" in ddl
    assert "rag_search_audit_config_idx" in ddl
    assert "query_text" not in normalized
    assert "prompt" not in normalized
    assert " mode " not in normalized


def test_oracle_agent_memory_schema_uses_vector_and_hashed_scope() -> None:
    """Agent Memory は Oracle 26ai 内の vector table と hash scope で保持する。"""
    ddl = oracle_agent_memory_schema_sql()
    normalized = ddl.lower()

    assert "create table rag_agent_memories" in normalized
    assert "tenant_id_hash   char(64)" in normalized
    assert "user_id_hash     char(64)" in normalized
    assert "role_id_hash     char(64)" in normalized
    assert "agent_id_hash    char(64)" in normalized
    assert "thread_id_hash   char(64)" in normalized
    assert "memory_text      clob not null" in normalized
    assert "embedding        vector(1536, float32) not null" in normalized
    assert "create vector index rag_agent_memories_embedding_hnsw_idx" in normalized
    assert "organization inmemory neighbor graph" in normalized
    assert "indextype is ctxsys.context" in normalized
    assert "rag_agent_memories_scope_idx" in ddl
    assert "raw_user_id" not in normalized
    assert "thread_id " not in normalized
    assert "qdrant" not in normalized
    assert "pgvector" not in normalized


def test_oracle_ingestion_audit_schema_redacts_ocr_body() -> None:
    """取込監査 DDL は原本 hash と件数を保存し、OCR 原文列を持たない。"""
    ddl = oracle_ingestion_audit_schema_sql()
    normalized = ddl.lower()

    assert "create table rag_ingestion_audit" in normalized
    assert "request_id             varchar2(128)" in normalized
    assert "tenant_id_hash         char(64)" in normalized
    assert "user_id_hash           char(64)" in normalized
    assert "document_id            varchar2(64) not null" in normalized
    assert "source_sha256          char(64) not null" in normalized
    assert "source_bytes           number(19) not null" in normalized
    assert "parser_backend         varchar2(80)" in normalized
    assert "parser_profile         varchar2(80)" in normalized
    assert "segment_count          number(10) default 0 not null" in normalized
    assert "fallback_count         number(10) default 0 not null" in normalized
    assert "failed_segment_count   number(10) default 0 not null" in normalized
    assert "chunk_count            number(10) default 0 not null" in normalized
    assert "vector_count           number(10) default 0 not null" in normalized
    assert "check (outcome in ('success', 'error'))" in normalized
    assert "rag_ingestion_audit_tenant_created_idx" in ddl
    assert "rag_ingestion_audit_document_created_idx" in ddl
    assert "rag_ingestion_audit_parser_created_idx" in ddl
    assert "raw_text" not in normalized
    assert "ocr_text" not in normalized


async def test_oci_ingestion_audit_persists_file_processing_metrics() -> None:
    """Oracle 取込監査は parser / segment の低機密集計を保存する。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    await client.save_ingestion_audit_event(
        {
            "event_type": "rag.ingestion",
            "trace_id": "trace-ingest",
            "request_id": "request-1",
            "tenant_id_hash": "a" * 64,
            "user_id_hash": "b" * 64,
            "document_id": "doc-1",
            "outcome": "success",
            "source_sha256": "c" * 64,
            "source_bytes": 1024,
            "document_type": "PDF",
            "extraction_confidence": 0.95,
            "parser_backend": "docling",
            "parser_profile": "enterprise_ai_pdf_layout",
            "segment_count": 4,
            "fallback_count": 1,
            "failed_segment_count": 2,
            "chunk_count": 30,
            "vector_count": 30,
            "elapsed_ms": 1234.5,
        }
    )

    call = pool.connection.calls[0]
    assert "INSERT INTO rag_ingestion_audit" in call.statement
    assert "parser_backend" in call.statement
    assert "parser_profile" in call.statement
    assert "segment_count" in call.statement
    assert "fallback_count" in call.statement
    assert "failed_segment_count" in call.statement
    assert call.parameters["parser_backend"] == "docling"
    assert call.parameters["parser_profile"] == "enterprise_ai_pdf_layout"
    assert call.parameters["segment_count"] == 4
    assert call.parameters["fallback_count"] == 1
    assert call.parameters["failed_segment_count"] == 2
    assert "raw_text" not in call.parameters
    assert "ocr_text" not in call.parameters
    assert pool.connection.commits == 1


def test_oracle_audit_schema_bundle_includes_search_and_ingestion_tables() -> None:
    """監査 DDL bundle は検索・取込の両テーブルを含む。"""
    ddl = oracle_audit_schema_sql()

    assert "CREATE TABLE rag_search_audit" in ddl
    assert "CREATE TABLE rag_ingestion_audit" in ddl
    assert ddl.count("CREATE TABLE") == 2


def test_oracle_graph_feedback_and_eval_artifact_schema_use_oracle_tables() -> None:
    """GraphRAG-lite / feedback / eval artifact は Oracle table と JSON で表現する。"""
    graph_ddl = oracle_knowledge_graph_schema_sql()
    feedback_ddl = oracle_feedback_schema_sql()
    artifact_ddl = oracle_evaluation_artifact_schema_sql()

    assert "CREATE TABLE rag_graph_entities" in graph_ddl
    assert "CREATE TABLE rag_graph_relationships" in graph_ddl
    assert "CREATE TABLE rag_graph_claims" in graph_ddl
    assert "CREATE TABLE rag_graph_community_summaries" in graph_ddl
    assert "CREATE TABLE rag_graph_entity_chunks" in graph_ddl
    assert "CREATE TABLE rag_citation_feedback" in feedback_ddl
    assert "comment_hash      CHAR(64)" in feedback_ddl
    assert "comment_text" not in feedback_ddl.lower()
    assert "comment_body" not in feedback_ddl.lower()
    assert "CREATE TABLE rag_evaluation_runs" in artifact_ddl
    assert "request_json      JSON NOT NULL" in artifact_ddl
    assert "result_json       JSON NOT NULL" in artifact_ddl
    assert "result_sha256     CHAR(64) NOT NULL" in artifact_ddl
    assert "rag_evaluation_runs_result_hash_idx" in artifact_ddl
    assert "NEO4J" not in (graph_ddl + feedback_ddl + artifact_ddl).upper()


async def test_vector_search_rejects_wrong_embedding_dimension() -> None:
    """検索 embedding が Oracle VECTOR 幅と違う場合は明示的に拒否する。"""
    client = OracleClient(settings=Settings.model_construct(oci_genai_embedding_dim=3))

    with pytest.raises(ValueError, match="query embedding の次元数が不正です"):
        await client.vector_search([1.0, 0.0], top_k=1)


@IN_MEMORY_ORACLE_REMOVED
async def test_save_chunks_rejects_wrong_embedding_dimension() -> None:
    """保存 embedding が Oracle VECTOR 幅と違う場合は明示的に拒否する。"""
    client = OracleClient(settings=Settings.model_construct(oci_genai_embedding_dim=3))
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )

    with pytest.raises(ValueError, match=r"chunk embedding\[0\] の次元数が不正です"):
        await client.save_chunks(
            document.id,
            [Chunk(index=0, text="社内規程", start_offset=0, end_offset=3)],
            [[1.0, 0.0]],
        )


@IN_MEMORY_ORACLE_REMOVED
async def test_non_searchable_status_clears_existing_chunks() -> None:
    """ERROR / INGESTING 状態へ移ると古い index chunk を検索対象から外す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="社内規程 クラウド利用料", start_offset=0, end_offset=10)],
        [[1.0, 0.0, 0.0]],
    )
    assert await client.count_chunks() == 0

    await client.update_document_status(document.id, FileStatus.INDEXED)
    assert await client.count_chunks() == 1
    assert await client.vector_search([1.0, 0.0, 0.0], top_k=1)

    await client.update_document_status(document.id, FileStatus.ERROR, "再分析に失敗しました。")

    assert await client.count_chunks() == 0
    assert await client.vector_search([1.0, 0.0, 0.0], top_k=1) == []


@IN_MEMORY_ORACLE_REMOVED
async def test_count_document_chunks_returns_searchable_rows_for_one_document() -> None:
    """document 別の索引件数は検索可能状態の当該 document だけを数える。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    first = await client.create_document(
        file_name="first.txt",
        object_storage_path="local://uploaded/first.txt",
        content_type="text/plain",
    )
    second = await client.create_document(
        file_name="second.txt",
        object_storage_path="local://uploaded/second.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        first.id,
        [
            Chunk(index=0, text="社内規程 A", start_offset=0, end_offset=4),
            Chunk(index=1, text="社内規程 A 明細", start_offset=4, end_offset=10),
        ],
        [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]],
    )
    await client.save_chunks(
        second.id,
        [Chunk(index=0, text="社内規程 B", start_offset=0, end_offset=4)],
        [[0.0, 1.0, 0.0]],
    )

    assert await client.count_document_chunks(first.id) == 0
    await client.update_document_status(first.id, FileStatus.INDEXED)
    await client.update_document_status(second.id, FileStatus.INDEXED)

    assert await client.count_document_chunks(first.id) == 2
    assert await client.count_document_chunks(second.id) == 1

    await client.update_document_status(first.id, FileStatus.ERROR, "再分析に失敗しました。")

    assert await client.count_document_chunks(first.id) == 0
    assert await client.count_document_chunks(second.id) == 1


@IN_MEMORY_ORACLE_REMOVED
async def test_keyword_score_uses_unique_query_terms_and_is_bounded() -> None:
    """同じ query token の繰り返しで keyword score を 1 超にしない。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="policy", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)

    hits = await client.keyword_search("policy policy", top_k=1)

    assert len(hits) == 1
    assert hits[0].score == 1.0
    assert hits[0].metadata["retrieval_mode"] == "keyword"
    assert hits[0].metadata["keyword_rank"] == 1
    assert hits[0].metadata["keyword_score"] == 1.0


@IN_MEMORY_ORACLE_REMOVED
async def test_vector_search_exposes_retrieval_metadata() -> None:
    """vector search は rank/score を citation metadata に残す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="policy", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)

    hits = await client.vector_search([1.0, 0.0, 0.0], top_k=1)

    assert len(hits) == 1
    assert hits[0].metadata["retrieval_mode"] == "vector"
    assert hits[0].metadata["vector_rank"] == 1
    assert hits[0].metadata["vector_score"] == 1.0


@IN_MEMORY_ORACLE_REMOVED
async def test_keyword_search_tie_breaks_by_document_and_chunk() -> None:
    """同点の keyword hit は document_id / chunk_index で安定順にする。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    second = await client.create_document(
        file_name="second.txt",
        object_storage_path="local://uploaded/second.txt",
        content_type="text/plain",
    )
    first = await client.create_document(
        file_name="first.txt",
        object_storage_path="local://uploaded/first.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        second.id,
        [Chunk(index=0, text="policy", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.save_chunks(
        first.id,
        [
            Chunk(index=1, text="policy", start_offset=8, end_offset=15),
            Chunk(index=0, text="policy", start_offset=0, end_offset=7),
        ],
        [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
    )
    await client.update_document_status(first.id, FileStatus.INDEXED)
    await client.update_document_status(second.id, FileStatus.INDEXED)

    hits = await client.keyword_search("policy", top_k=3)

    expected = sorted([(first.id, 0), (first.id, 1), (second.id, 0)])
    assert [(hit.document_id, hit.metadata["chunk_index"]) for hit in hits] == expected


@IN_MEMORY_ORACLE_REMOVED
async def test_hybrid_search_tie_breaks_rrf_scores_stably() -> None:
    """RRF 同点も document_id / chunk_index で安定順にする。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    second = await client.create_document(
        file_name="second.txt",
        object_storage_path="local://uploaded/second.txt",
        content_type="text/plain",
    )
    first = await client.create_document(
        file_name="first.txt",
        object_storage_path="local://uploaded/first.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        second.id,
        [Chunk(index=0, text="policy", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.save_chunks(
        first.id,
        [Chunk(index=0, text="policy", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(first.id, FileStatus.INDEXED)
    await client.update_document_status(second.id, FileStatus.INDEXED)

    hits = await client.hybrid_search(
        query="policy",
        embedding=[1.0, 0.0, 0.0],
        top_k=2,
        mode=SearchMode.HYBRID,
    )

    assert [hit.document_id for hit in hits] == sorted([first.id, second.id])
    for hit in hits:
        assert hit.metadata["retrieval_mode"] == "hybrid"
        assert isinstance(hit.metadata["vector_rank"], int)
        assert isinstance(hit.metadata["keyword_rank"], int)
        assert hit.metadata["vector_score"] == 1.0
        assert hit.metadata["keyword_score"] == 1.0
        assert hit.metadata["rrf_k"] == 60
        assert hit.metadata["rrf_score"] == hit.score


@IN_MEMORY_ORACLE_REMOVED
async def test_hybrid_search_uses_configured_rrf_k() -> None:
    """Hybrid 検索の RRF 定数は Settings から変更できる。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
            rag_rrf_k=10,
        )
    )
    document = await client.create_document(
        file_name="rrf.txt",
        object_storage_path="local://uploaded/rrf.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="policy", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)

    hits = await client.hybrid_search(
        query="policy",
        embedding=[1.0, 0.0, 0.0],
        top_k=1,
        mode=SearchMode.HYBRID,
    )

    assert len(hits) == 1
    assert hits[0].metadata["rrf_k"] == 10
    assert hits[0].score == pytest.approx(round((1 / 11) + (1 / 11), 6))
    assert hits[0].metadata["rrf_score"] == hits[0].score


@IN_MEMORY_ORACLE_REMOVED
async def test_hybrid_search_marks_vector_only_results() -> None:
    """hybrid 検索でも片側だけの hit は検索経路を区別できる。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="no keyword match", start_offset=0, end_offset=16)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)

    hits = await client.hybrid_search(
        query="社内規程",
        embedding=[1.0, 0.0, 0.0],
        top_k=1,
        mode=SearchMode.HYBRID,
    )

    assert len(hits) == 1
    assert hits[0].metadata["retrieval_mode"] == "vector"
    assert hits[0].metadata["vector_rank"] == 1
    assert "keyword_rank" not in hits[0].metadata


@IN_MEMORY_ORACLE_REMOVED
async def test_non_searchable_status_clears_extraction() -> None:
    """ERROR / INGESTING 状態へ移ると古い抽出結果も表示対象から外す。"""
    client = OracleClient(settings=Settings.model_construct())
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_extraction(
        document.id,
        StructuredExtraction(
            raw_text="社内規程: 経費申請",
            document_type="社内規程",
            confidence=0.9,
            warnings=[],
        ),
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)
    indexed = await client.get_document(document.id)
    assert indexed is not None
    assert indexed.extraction

    errored = await client.update_document_status(
        document.id,
        FileStatus.ERROR,
        "再分析に失敗しました。",
    )

    assert errored.extraction == {}


@IN_MEMORY_ORACLE_REMOVED
async def test_analyzing_status_removes_stale_chunks_during_reindex() -> None:
    """再分析中は旧 chunk を残さず、検索対象にも数えない。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="policy.txt",
        object_storage_path="local://uploaded/policy.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="古い社内規程チャンク", start_offset=0, end_offset=9)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.INDEXED)
    assert await client.count_chunks() == 1

    await client.update_document_status(document.id, FileStatus.INGESTING)

    assert await client.count_chunks() == 0
    assert await client.vector_search([1.0, 0.0, 0.0], top_k=1) == []


@dataclass
class SqlCall:
    """Fake Oracle connection が記録する単発 SQL 呼び出し。"""

    statement: str
    parameters: dict[str, object]
    input_sizes: dict[str, object]


@dataclass
class SqlManyCall:
    """Fake Oracle connection が記録する executemany 呼び出し。"""

    statement: str
    rows: list[dict[str, object]]


class FakeOraclePool:
    """python-oracledb pool の fake。"""

    def __init__(
        self,
        execute_results: list[list[dict[str, object]]] | None = None,
        missing_ingestion_job_max_attempts: bool = False,
        fetch_errors: Sequence[Exception] | None = None,
    ) -> None:
        self.connection = FakeOracleConnection(
            execute_results or [],
            missing_ingestion_job_max_attempts=missing_ingestion_job_max_attempts,
            fetch_errors=fetch_errors,
        )
        self.acquire_calls = 0
        self.close_calls = 0

    def acquire(self) -> "FakeOracleConnection":
        self.acquire_calls += 1
        return self.connection

    def close(self, force: bool = False) -> None:
        self.close_calls += 1


class ForceCloseOnlyPool:
    """busy pool の fake。force=True でしか閉じられない。"""

    def __init__(self) -> None:
        self.close_forces: list[bool] = []

    def acquire(self) -> "FakeOracleConnection":
        raise AssertionError("close_oracle_pool は acquire しない")

    def close(self, force: bool = False) -> None:
        self.close_forces.append(force)
        if not force:
            raise RuntimeError(
                "DPY-1005: connection pool cannot be closed because connections are busy"
            )


class FakeOracleConnection:
    """python-oracledb connection の fake。"""

    def __init__(
        self,
        execute_results: list[list[dict[str, object]]],
        *,
        missing_ingestion_job_max_attempts: bool = False,
        fetch_errors: Sequence[Exception] | None = None,
    ) -> None:
        self._execute_results = execute_results
        self.missing_ingestion_job_max_attempts = missing_ingestion_job_max_attempts
        self.fetch_errors = list(fetch_errors or ())
        self.calls: list[SqlCall] = []
        self.many_calls: list[SqlManyCall] = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self) -> "FakeOracleCursor":
        return FakeOracleCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1

    def next_rows(self) -> list[dict[str, object]]:
        if not self._execute_results:
            return []
        return self._execute_results.pop(0)


class FakeOracleCursor:
    """python-oracledb cursor の fake。"""

    description: Sequence[Sequence[Any]] | None = None

    def __init__(self, connection: FakeOracleConnection) -> None:
        self._connection = connection
        self._input_sizes: dict[str, object] = {}
        self._rows: list[dict[str, object]] = []
        self.closed = False

    def setinputsizes(self, **kwargs: object) -> None:
        self._input_sizes.update(kwargs)

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        self._connection.calls.append(
            SqlCall(statement, dict(parameters or {}), dict(self._input_sizes))
        )
        if self._connection.missing_ingestion_job_max_attempts and (
            "j.max_attempts" in statement
            or ("INSERT INTO rag_ingestion_jobs" in statement and "max_attempts" in statement)
            or ("UPDATE rag_ingestion_jobs" in statement and "max_attempts" in statement)
        ):
            raise RuntimeError('ORA-00904: "J"."MAX_ATTEMPTS": invalid identifier')
        self._rows = self._connection.next_rows() if statement.startswith("SELECT") else []

    def executemany(
        self,
        statement: str,
        parameters: Sequence[Mapping[str, object]],
    ) -> None:
        self._connection.many_calls.append(
            SqlManyCall(statement, [dict(row) for row in parameters])
        )

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, object]]:
        if self._connection.fetch_errors:
            raise self._connection.fetch_errors.pop(0)
        return self._rows

    def close(self) -> None:
        self.closed = True


def _oci_settings() -> Settings:
    return Settings.model_construct(
        oci_genai_embedding_dim=3,
        rag_min_similarity=0.05,
        oracle_vector_target_accuracy=90,
        oracle_user="rag_app",
        oracle_password="oracle-password",
        oracle_dsn="adb.example.com/rag",
    )


def _oracle_document_row(
    *,
    status: str = "INDEXED",
    extraction: object = '{"raw_text":"社内規程本文","document_type":"社内規程"}',
) -> dict[str, object]:
    return {
        "document_id": "doc-1",
        "file_name": "policy.txt",
        "status": status,
        "tenant_id_hash": None,
        "category_name": "社内規程",
        "object_storage_path": "oci://namespace/bucket/policy.txt",
        "content_type": "text/plain",
        "file_size_bytes": 12,
        "content_sha256": "a" * 64,
        "duplicate_of_document_id": None,
        "extraction": extraction,
        "error_message": None,
        "uploaded_at": datetime(2026, 1, 1, tzinfo=UTC),
        "indexed_at": None,
    }


def _oracle_knowledge_base_row(
    *,
    name: str = "社内規程",
    status: str = "ACTIVE",
    document_count: int = 0,
) -> dict[str, object]:
    return {
        "knowledge_base_id": "kb-1",
        "tenant_id_hash": None,
        "name": name,
        "description": "規程文書",
        "status": status,
        "default_search_mode": "hybrid",
        "retrieval_config": '{"top_k":20}',
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        "archived_at": None,
        "document_count": document_count,
        "indexed_document_count": 1 if document_count else 0,
        "error_document_count": 0,
        "searchable_chunk_count": 5 if document_count else 0,
    }


def _oracle_ingestion_job_row(
    *,
    job_id: str = "job-1",
    status: str = "QUEUED",
    attempt_count: int = 0,
    max_attempts: int = 3,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "job_id": job_id,
        "document_id": "doc-1",
        "status": status,
        "parser_profile": "enterprise_ai_pdf_layout",
        "quality_warnings": '["table_structure_review"]',
        "skip_reason": None,
        "error_message": None,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "queued_at": datetime(2026, 1, 2, tzinfo=UTC),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _oracle_ingestion_segment_row(
    *,
    segment_id: str = "doc-1:p1-3",
    status: str = "SUCCEEDED",
) -> dict[str, object]:
    return {
        "segment_id": segment_id,
        "document_id": "doc-1",
        "status": status,
        "parser_backend": "enterprise_ai",
        "parser_profile": "enterprise_ai_pdf_layout",
        "page_start": 1,
        "page_end": 3,
        "attempt_count": 1,
        "artifact_path": "oci://namespace/bucket/artifacts/extractions/doc-1/trace.json",
        "error_code": None,
        "error_message": None,
    }


async def _run_inline(operation: Callable[[], Any]) -> Any:
    """テストでは同期 fake を同一 thread で実行する。"""
    return operation()
