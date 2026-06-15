"""Oracle adapter 境界のテスト。"""

import json
from array import array
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.clients.oracle import (
    OracleClient,
    OracleWalletPasswordRequiredError,
    SelectAiUnavailableError,
    _test_oracle_connection_sync,
    oracle_audit_schema_sql,
    oracle_document_schema_sql,
    oracle_ingestion_audit_schema_sql,
    oracle_ingestion_job_schema_sql,
    oracle_knowledge_base_schema_sql,
    oracle_search_audit_schema_sql,
    oracle_vector_schema_sql,
    reset_local_store,
)
from app.config import Settings
from app.rag.chunking import Chunk
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.document import FileStatus, IngestionJob, IngestionJobStatus
from app.schemas.extraction import StructuredExtraction
from app.schemas.knowledge_base import KnowledgeBaseStatus
from app.schemas.search import RetrievedChunk, SearchMode, SelectAiAction

IN_MEMORY_ORACLE_REMOVED = pytest.mark.skip(reason="in-memory Oracle fallback was removed")


def setup_function() -> None:
    """テストごとにテスト補助 store を初期化する。"""
    reset_local_store()


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
    assert detail.knowledge_bases[0].name == "既定ナレッジベース"
    assert pool.connection.commits == 1
    statements = [call.statement for call in pool.connection.calls]
    assert any("INSERT INTO rag_knowledge_bases" in statement for statement in statements)
    assert any("INSERT INTO rag_documents" in statement for statement in statements)
    assert pool.connection.many_calls
    assert "INSERT INTO rag_document_knowledge_bases" in pool.connection.many_calls[0].statement


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
    assert metadata["chunk_index"] == 0
    assert metadata["section_path"] == "経費申請 > 承認"
    assert metadata["content_kind"] == "text"


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


async def test_oci_update_error_status_clears_chunks_and_extraction() -> None:
    """ERROR への状態遷移では Oracle 側でも古い chunk と抽出 JSON を外す。"""
    errored = _oracle_document_row(status="ERROR", extraction=None)
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()], [errored]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.update_document_status(
        "doc-1",
        FileStatus.ERROR,
        "再分析に失敗しました。",
    )

    assert detail.status == FileStatus.ERROR
    assert detail.extraction == {}
    statements = [call.statement for call in pool.connection.calls]
    assert any("DELETE FROM rag_chunks" in statement for statement in statements)
    assert any("extraction = NULL" in statement for statement in statements)
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
    assert any("DELETE FROM rag_chunks" in statement for statement in statements)
    document_delete = next(
        call
        for call in pool.connection.calls
        if call.statement.startswith("DELETE FROM rag_documents")
    )
    assert "document_id = :document_id" in document_delete.statement
    assert "document_id IN (:access_document_id_0)" in document_delete.statement
    assert document_delete.parameters["document_id"] == "doc-1"
    assert document_delete.parameters["access_document_id_0"] == "doc-1"
    assert pool.connection.commits == 1


async def test_oci_select_ai_uses_dbms_cloud_ai_generate_with_binds() -> None:
    """Select AI は DBMS_CLOUD_AI.GENERATE を bind 付きで呼び出す。"""
    pool = FakeOraclePool(execute_results=[[{"result_text": "SELECT COUNT(*) FROM rag_documents"}]])
    settings = _oci_settings()
    settings.oracle_select_ai_profile = "rag_select_ai"
    client = OracleClient(settings=settings, pool=pool, db_call_runner=_run_inline)

    result = await client.select_ai(
        "索引済み文書数を教えて",
        action=SelectAiAction.SHOWSQL,
    )

    assert result == "SELECT COUNT(*) FROM rag_documents"
    call = pool.connection.calls[0]
    assert "DBMS_CLOUD_AI.GENERATE" in call.statement
    assert ":prompt" in call.statement
    assert call.parameters == {
        "prompt": "索引済み文書数を教えて",
        "profile_name": "rag_select_ai",
        "action": "showsql",
    }
    assert "索引済み文書数" not in call.statement
    assert pool.connection.commits == 0


async def test_select_ai_without_profile_is_unavailable() -> None:
    """profile 未設定では Select AI を実行しない。"""
    with pytest.raises(SelectAiUnavailableError):
        await OracleClient().select_ai("索引済み文書数を教えて")


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
        "dkb.knowledge_base_id IN "
        "(:filter_knowledge_base_id_0, :filter_knowledge_base_id_1)"
    ) in call.statement
    assert call.parameters["filter_knowledge_base_id_0"] == "kb-1"
    assert call.parameters["filter_knowledge_base_id_1"] == "kb-2"


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
    assert "CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED'))" in ddl
    assert "CHECK (attempt_count >= 0 AND max_attempts >= 1)" in ddl
    assert "REFERENCES rag_documents (document_id)" in ddl
    assert "ON rag_ingestion_jobs (tenant_id_hash, status, queued_at DESC)" in ddl


def test_oracle_vector_schema_includes_tenant_filter_columns() -> None:
    """chunk/vector DDL 例は tenant filter 用の列と索引を含む。"""
    ddl = oracle_vector_schema_sql()

    assert "tenant_id_hash  CHAR(64)" in ddl
    assert "CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx" in ddl
    assert "ORGANIZATION INMEMORY NEIGHBOR GRAPH" in ddl
    assert "DISTANCE COSINE" in ddl
    assert "WITH TARGET ACCURACY 95" in ddl
    assert "TYPE HNSW" in ddl
    assert "NEIGHBORS 32" in ddl
    assert "EFCONSTRUCTION 500" in ddl
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
    assert "context_compressed_count number(10) default 0 not null" in normalized
    assert "context_compression_saved_chars number(10) default 0 not null" in normalized
    assert "context_chars         number(10) default 0 not null" in normalized
    assert "config_fingerprint    char(64)" in normalized
    assert "document_ids          json" in normalized
    assert "knowledge_base_ids    json" in normalized
    assert "check (outcome in ('success', 'blocked', 'no_results', 'error'))" in normalized
    assert "rag_search_audit_created_outcome_idx" in ddl
    assert "rag_search_audit_tenant_created_idx" in ddl
    assert "rag_search_audit_config_idx" in ddl
    assert "query_text" not in normalized
    assert "prompt" not in normalized


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
    assert "chunk_count            number(10) default 0 not null" in normalized
    assert "vector_count           number(10) default 0 not null" in normalized
    assert "check (outcome in ('success', 'error'))" in normalized
    assert "rag_ingestion_audit_tenant_created_idx" in ddl
    assert "rag_ingestion_audit_document_created_idx" in ddl
    assert "raw_text" not in normalized
    assert "ocr_text" not in normalized


def test_oracle_audit_schema_bundle_includes_search_and_ingestion_tables() -> None:
    """監査 DDL bundle は検索・取込の両テーブルを含む。"""
    ddl = oracle_audit_schema_sql()

    assert "CREATE TABLE rag_search_audit" in ddl
    assert "CREATE TABLE rag_ingestion_audit" in ddl
    assert ddl.count("CREATE TABLE") == 2


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
    ) -> None:
        self.connection = FakeOracleConnection(
            execute_results or [],
            missing_ingestion_job_max_attempts=missing_ingestion_job_max_attempts,
        )
        self.acquire_calls = 0
        self.close_calls = 0

    def acquire(self) -> "FakeOracleConnection":
        self.acquire_calls += 1
        return self.connection

    def close(self) -> None:
        self.close_calls += 1


class FakeOracleConnection:
    """python-oracledb connection の fake。"""

    def __init__(
        self,
        execute_results: list[list[dict[str, object]]],
        *,
        missing_ingestion_job_max_attempts: bool = False,
    ) -> None:
        self._execute_results = execute_results
        self.missing_ingestion_job_max_attempts = missing_ingestion_job_max_attempts
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
        self._rows: list[dict[str, object]] = []
        self.closed = False

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        self._connection.calls.append(SqlCall(statement, dict(parameters or {})))
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
    status: str = "ACTIVE",
    document_count: int = 0,
) -> dict[str, object]:
    return {
        "knowledge_base_id": "kb-1",
        "tenant_id_hash": None,
        "name": "社内規程",
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


async def _run_inline(operation: Callable[[], Any]) -> Any:
    """テストでは同期 fake を同一 thread で実行する。"""
    return operation()
