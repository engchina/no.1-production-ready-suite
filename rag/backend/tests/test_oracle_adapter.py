"""Oracle adapter 境界のテスト。"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from app.clients.oracle import (
    OracleClient,
    oracle_audit_schema_sql,
    oracle_document_schema_sql,
    oracle_ingestion_audit_schema_sql,
    oracle_search_audit_schema_sql,
    oracle_vector_schema_sql,
    reset_local_store,
)
from app.config import Settings
from app.rag.chunking import Chunk
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import SearchMode


def setup_function() -> None:
    """テストごとにローカル store を初期化する。"""
    reset_local_store()


async def test_oci_adapter_does_not_fall_back_to_local_document_store() -> None:
    """OCI mode では document persistence が local store に落ちないこと。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="oci://bucket/invoice.txt",
        content_type="text/plain",
    )

    assert detail.file_name == "invoice.txt"
    assert pool.connection.commits == 1
    assert "INSERT INTO rag_documents" in pool.connection.calls[0].statement
    assert await OracleClient().list_documents() == []


async def test_oci_vector_search_uses_ai_vector_search_sql() -> None:
    """OCI mode の vector search は Oracle AI Vector Search に bind 付きで問い合わせる。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "document_id": "doc-1",
                    "chunk_id": "doc-1:0",
                    "chunk_text": "請求書 クラウド利用料",
                    "metadata_json": '{"chunk_index":0}',
                    "file_name": "invoice.txt",
                    "category_name": "請求書",
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
    assert "d.status IN ('ANALYZED', 'REGISTERED')" in call.statement
    assert call.parameters["embedding"] == [1.0, 0.0, 0.0]
    assert call.parameters["top_k"] == 3
    assert call.parameters["filter_document_id"] == "doc-1"


async def test_oci_save_chunks_replaces_existing_chunks_and_binds_vectors() -> None:
    """OCI mode の chunk 保存は既存 chunk を消して VECTOR bind を挿入する。"""
    pool = FakeOraclePool(execute_results=[[_oracle_document_row()]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    saved = await client.save_chunks(
        "doc-1",
        [Chunk(index=0, text="請求書", start_offset=0, end_offset=3)],
        [[1.0, 0.0, 0.0]],
    )

    assert saved[0].chunk_id == "doc-1:0"
    assert saved[0].metadata["chunk_index"] == 0
    assert pool.connection.commits == 1
    statements = [call.statement for call in pool.connection.calls]
    assert any("DELETE FROM rag_chunks" in statement for statement in statements)
    assert pool.connection.many_calls
    inserted = pool.connection.many_calls[0].rows[0]
    assert inserted["chunk_id"] == "doc-1:0"
    assert inserted["embedding"] == [1.0, 0.0, 0.0]


async def test_oci_update_error_status_clears_chunks_and_extraction() -> None:
    """ERROR への状態遷移では Oracle 側でも古い chunk と抽出 JSON を外す。"""
    errored = _oracle_document_row(status="ERROR", extracted_fields=None)
    pool = FakeOraclePool(execute_results=[[errored]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    detail = await client.update_document_status(
        "doc-1",
        FileStatus.ERROR,
        "再分析に失敗しました。",
    )

    assert detail.status == FileStatus.ERROR
    assert detail.extracted_fields == {}
    statements = [call.statement for call in pool.connection.calls]
    assert any("DELETE FROM rag_chunks" in statement for statement in statements)
    assert any("extracted_fields = NULL" in statement for statement in statements)
    assert pool.connection.commits == 1


async def test_oci_select_ai_parses_json_result_and_applies_limit() -> None:
    """Select AI の JSON result は table browser 用の行 dict に正規化する。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "result": (
                        '[{"document_id":"doc-1","status":"ANALYZED"},'
                        '{"document_id":"doc-2","status":"REGISTERED"}]'
                    )
                }
            ]
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    rows = await client.select_ai("登録済み伝票を表示", limit=1)

    assert rows == [{"document_id": "doc-1", "status": "ANALYZED"}]
    call = pool.connection.calls[0]
    assert "DBMS_CLOUD_AI.GENERATE" in call.statement
    assert call.parameters["profile_name"] == "rag_select_ai"
    assert "最大 1 行" in str(call.parameters["prompt"])


async def test_local_update_missing_document_raises_key_error() -> None:
    """存在しない document の状態更新は明示的に失敗する。"""
    client = OracleClient()

    with pytest.raises(KeyError):
        await client.update_document_status("missing", FileStatus.ANALYZING)


async def test_local_select_ai_returns_json_ready_status() -> None:
    """local Select AI 代替は enum ではなく文字列 status を返す。"""
    client = OracleClient()
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )

    rows = await client.select_ai("請求書を表示")

    assert rows == [
        {
            "document_id": document.id,
            "file_name": "invoice.txt",
            "status": "UPLOADED",
            "uploaded_at": document.uploaded_at.isoformat(),
        }
    ]


async def test_local_select_ai_applies_limit_and_newest_first() -> None:
    """local Select AI 代替も limit を適用し、新しい document から返す。"""
    client = OracleClient()
    await client.create_document(
        file_name="old.txt",
        object_storage_path="local://uploaded/old.txt",
        content_type="text/plain",
    )
    newest = await client.create_document(
        file_name="new.txt",
        object_storage_path="local://uploaded/new.txt",
        content_type="text/plain",
    )

    rows = await client.select_ai("登録済み伝票を表示", limit=1)

    assert len(rows) == 1
    assert rows[0]["document_id"] == newest.id
    assert rows[0]["file_name"] == "new.txt"


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


def test_oracle_vector_schema_includes_tenant_filter_columns() -> None:
    """chunk/vector DDL 例は tenant filter 用の列と索引を含む。"""
    ddl = oracle_vector_schema_sql()

    assert "tenant_id_hash  CHAR(64)" in ddl
    assert "rag_chunks_tenant_document_idx" in ddl
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
    assert "guardrail_codes       json" in normalized
    assert "reranked_count        number(10) default 0 not null" in normalized
    assert "context_chars         number(10) default 0 not null" in normalized
    assert "config_fingerprint    char(64)" in normalized
    assert "document_ids          json" in normalized
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


async def test_save_chunks_rejects_wrong_embedding_dimension() -> None:
    """保存 embedding が Oracle VECTOR 幅と違う場合は明示的に拒否する。"""
    client = OracleClient(settings=Settings.model_construct(oci_genai_embedding_dim=3))
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )

    with pytest.raises(ValueError, match=r"chunk embedding\[0\] の次元数が不正です"):
        await client.save_chunks(
            document.id,
            [Chunk(index=0, text="請求書", start_offset=0, end_offset=3)],
            [[1.0, 0.0]],
        )


async def test_non_searchable_status_clears_existing_chunks() -> None:
    """ERROR / ANALYZING 状態へ移ると古い index chunk を検索対象から外す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="請求書 クラウド利用料", start_offset=0, end_offset=10)],
        [[1.0, 0.0, 0.0]],
    )
    assert await client.count_chunks() == 0

    await client.update_document_status(document.id, FileStatus.ANALYZED)
    assert await client.count_chunks() == 1
    assert await client.vector_search([1.0, 0.0, 0.0], top_k=1)

    await client.update_document_status(document.id, FileStatus.ERROR, "再分析に失敗しました。")

    assert await client.count_chunks() == 0
    assert await client.vector_search([1.0, 0.0, 0.0], top_k=1) == []


async def test_count_document_chunks_returns_searchable_rows_for_one_document() -> None:
    """document 別の索引件数は検索可能状態の当該 document だけを数える。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
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
            Chunk(index=0, text="請求書 A", start_offset=0, end_offset=4),
            Chunk(index=1, text="請求書 A 明細", start_offset=4, end_offset=10),
        ],
        [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]],
    )
    await client.save_chunks(
        second.id,
        [Chunk(index=0, text="請求書 B", start_offset=0, end_offset=4)],
        [[0.0, 1.0, 0.0]],
    )

    assert await client.count_document_chunks(first.id) == 0
    await client.update_document_status(first.id, FileStatus.ANALYZED)
    await client.update_document_status(second.id, FileStatus.ANALYZED)

    assert await client.count_document_chunks(first.id) == 2
    assert await client.count_document_chunks(second.id) == 1

    await client.update_document_status(first.id, FileStatus.ERROR, "再分析に失敗しました。")

    assert await client.count_document_chunks(first.id) == 0
    assert await client.count_document_chunks(second.id) == 1


async def test_keyword_score_uses_unique_query_terms_and_is_bounded() -> None:
    """同じ query token の繰り返しで keyword score を 1 超にしない。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="invoice", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.ANALYZED)

    hits = await client.keyword_search("invoice invoice", top_k=1)

    assert len(hits) == 1
    assert hits[0].score == 1.0
    assert hits[0].metadata["retrieval_mode"] == "keyword"
    assert hits[0].metadata["keyword_rank"] == 1
    assert hits[0].metadata["keyword_score"] == 1.0


async def test_vector_search_exposes_retrieval_metadata() -> None:
    """vector search は rank/score を citation metadata に残す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="invoice", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.ANALYZED)

    hits = await client.vector_search([1.0, 0.0, 0.0], top_k=1)

    assert len(hits) == 1
    assert hits[0].metadata["retrieval_mode"] == "vector"
    assert hits[0].metadata["vector_rank"] == 1
    assert hits[0].metadata["vector_score"] == 1.0


async def test_keyword_search_tie_breaks_by_document_and_chunk() -> None:
    """同点の keyword hit は document_id / chunk_index で安定順にする。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
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
        [Chunk(index=0, text="invoice", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.save_chunks(
        first.id,
        [
            Chunk(index=1, text="invoice", start_offset=8, end_offset=15),
            Chunk(index=0, text="invoice", start_offset=0, end_offset=7),
        ],
        [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
    )
    await client.update_document_status(first.id, FileStatus.ANALYZED)
    await client.update_document_status(second.id, FileStatus.ANALYZED)

    hits = await client.keyword_search("invoice", top_k=3)

    expected = sorted([(first.id, 0), (first.id, 1), (second.id, 0)])
    assert [(hit.document_id, hit.metadata["chunk_index"]) for hit in hits] == expected


async def test_hybrid_search_tie_breaks_rrf_scores_stably() -> None:
    """RRF 同点も document_id / chunk_index で安定順にする。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
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
        [Chunk(index=0, text="invoice", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.save_chunks(
        first.id,
        [Chunk(index=0, text="invoice", start_offset=0, end_offset=7)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(first.id, FileStatus.ANALYZED)
    await client.update_document_status(second.id, FileStatus.ANALYZED)

    hits = await client.hybrid_search(
        query="invoice",
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
        assert hit.metadata["rrf_score"] == hit.score


async def test_hybrid_search_marks_vector_only_results() -> None:
    """hybrid 検索でも片側だけの hit は検索経路を区別できる。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="no keyword match", start_offset=0, end_offset=16)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.ANALYZED)

    hits = await client.hybrid_search(
        query="請求書",
        embedding=[1.0, 0.0, 0.0],
        top_k=1,
        mode=SearchMode.HYBRID,
    )

    assert len(hits) == 1
    assert hits[0].metadata["retrieval_mode"] == "vector"
    assert hits[0].metadata["vector_rank"] == 1
    assert "keyword_rank" not in hits[0].metadata


async def test_non_searchable_status_clears_extracted_fields() -> None:
    """ERROR / ANALYZING 状態へ移ると古い抽出結果も表示対象から外す。"""
    client = OracleClient(settings=Settings.model_construct(ai_service_adapter="local"))
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )
    await client.save_extraction(
        document.id,
        StructuredExtraction(
            raw_text="請求書番号: INV-001",
            document_type="請求書",
            fields={"document_number": "INV-001"},
            confidence=0.9,
            warnings=[],
        ),
    )
    await client.update_document_status(document.id, FileStatus.ANALYZED)
    analyzed = await client.get_document(document.id)
    assert analyzed is not None
    assert analyzed.extracted_fields

    errored = await client.update_document_status(
        document.id,
        FileStatus.ERROR,
        "再分析に失敗しました。",
    )

    assert errored.extracted_fields == {}


async def test_analyzing_status_removes_stale_chunks_during_reindex() -> None:
    """再分析中は旧 chunk を残さず、検索対象にも数えない。"""
    client = OracleClient(
        settings=Settings.model_construct(
            ai_service_adapter="local",
            oci_genai_embedding_dim=3,
            rag_min_similarity=0.0,
        )
    )
    document = await client.create_document(
        file_name="invoice.txt",
        object_storage_path="local://uploaded/invoice.txt",
        content_type="text/plain",
    )
    await client.save_chunks(
        document.id,
        [Chunk(index=0, text="古い請求書チャンク", start_offset=0, end_offset=9)],
        [[1.0, 0.0, 0.0]],
    )
    await client.update_document_status(document.id, FileStatus.ANALYZED)
    assert await client.count_chunks() == 1

    await client.update_document_status(document.id, FileStatus.ANALYZING)

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
    ) -> None:
        self.connection = FakeOracleConnection(execute_results or [])
        self.acquire_calls = 0
        self.close_calls = 0

    def acquire(self) -> "FakeOracleConnection":
        self.acquire_calls += 1
        return self.connection

    def close(self) -> None:
        self.close_calls += 1


class FakeOracleConnection:
    """python-oracledb connection の fake。"""

    def __init__(self, execute_results: list[list[dict[str, object]]]) -> None:
        self._execute_results = execute_results
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
        ai_service_adapter="oci",
        oci_genai_embedding_dim=3,
        rag_min_similarity=0.05,
        oracle_user="rag_app",
        oracle_password="oracle-password",
        oracle_dsn="adb.example.com/rag",
        oracle_select_ai_profile="rag_select_ai",
    )


def _oracle_document_row(
    *,
    status: str = "ANALYZED",
    extracted_fields: object = '{"fields":{"document_number":"INV-001"}}',
) -> dict[str, object]:
    return {
        "document_id": "doc-1",
        "file_name": "invoice.txt",
        "status": status,
        "tenant_id_hash": None,
        "category_name": "請求書",
        "object_storage_path": "oci://namespace/bucket/invoice.txt",
        "content_type": "text/plain",
        "file_size_bytes": 12,
        "content_sha256": "a" * 64,
        "duplicate_of_document_id": None,
        "extracted_fields": extracted_fields,
        "error_message": None,
        "uploaded_at": datetime(2026, 1, 1, tzinfo=UTC),
        "registered_at": None,
    }


async def _run_inline(operation: Callable[[], Any]) -> Any:
    """テストでは同期 fake を同一 thread で実行する。"""
    return operation()
