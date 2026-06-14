"""Oracle 26ai クライアント。

AI Vector Search によるベクトル検索（VECTOR(1536, FLOAT32)）と
Oracle Text による keyword retrieval を担う。外部ベクトル DB は使わない。
"""

import asyncio
import importlib
import json
import math
import re
from array import array
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast
from uuid import uuid4

from app.config import Settings, get_settings
from app.rag.chunking import Chunk
from app.rag.request_context import current_audit_request_context
from app.schemas.document import DocumentDetail, DocumentStats, DocumentSummary, FileStatus
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import RetrievedChunk, SearchMode, SelectAiAction

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[ぁ-んァ-ン一-龯々ー]+", re.IGNORECASE)
SEARCHABLE_FILE_STATUSES = {FileStatus.INDEXED}
type MetadataValue = str | int | float | bool | None
type DbCallRunner = Callable[[Callable[[], Any]], Awaitable[Any]]
T = TypeVar("T")
SELECT_AI_UNAVAILABLE_ERROR = "Select AI は ORACLE_SELECT_AI_PROFILE の設定が必要です。"


def _to_vector_bind(embedding: Sequence[float]) -> "array[float]":
    """embedding を Oracle VECTOR(FLOAT32) へバインド可能な float32 配列に変換する。

    python-oracledb は VECTOR 列に list を渡すと配列バインドと誤認するため、
    array('f', ...) として渡す必要がある。
    """
    return array("f", (float(value) for value in embedding))
WALLET_PASSWORD_REQUIRED_ERROR = (
    "Oracle Wallet に自動ログイン用の cwallet.sso がないため、Wallet パスワードが必要です。"
    " Wallet パスワードを入力するか、cwallet.sso を含む Wallet ZIP をアップロードしてください。"
)


class OracleCursorProtocol(Protocol):
    """python-oracledb cursor の最小インターフェース。"""

    description: Sequence[Sequence[Any]] | None

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> Any:
        """SQL を実行する。"""

    def executemany(self, statement: str, parameters: Sequence[Mapping[str, object]]) -> Any:
        """同一 SQL を複数 bind で実行する。"""

    def fetchone(self) -> Any:
        """1 行取得する。"""

    def fetchall(self) -> Sequence[Any]:
        """全行取得する。"""

    def close(self) -> Any:
        """cursor を閉じる。"""


class OracleConnectionProtocol(Protocol):
    """python-oracledb connection の最小インターフェース。"""

    def cursor(self) -> OracleCursorProtocol:
        """cursor を返す。"""

    def commit(self) -> Any:
        """transaction を commit する。"""

    def rollback(self) -> Any:
        """transaction を rollback する。"""

    def close(self) -> Any:
        """connection を閉じる。"""


class OraclePoolProtocol(Protocol):
    """python-oracledb pool の最小インターフェース。"""

    def acquire(self) -> OracleConnectionProtocol:
        """connection を取得する。"""

    def close(self) -> Any:
        """pool を閉じる。"""


@dataclass
class StoredDocument:
    """テスト補助で使うドキュメント行。"""

    id: str
    file_name: str
    status: FileStatus
    uploaded_at: datetime
    object_storage_path: str | None = None
    content_type: str | None = None
    file_size_bytes: int | None = None
    content_sha256: str | None = None
    duplicate_of_document_id: str | None = None
    tenant_id_hash: str | None = None
    category_name: str | None = None
    indexed_at: datetime | None = None
    extraction: dict[str, object] = field(default_factory=dict)
    error_message: str | None = None


@dataclass
class StoredChunk:
    """テスト補助で使うチャンク行。"""

    id: str
    document_id: str
    tenant_id_hash: str | None
    chunk_index: int
    text: str
    embedding: list[float]
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@dataclass
class LocalOracleStore:
    """Oracle row 変換などの単体テストで使う補助ストア。"""

    documents: dict[str, StoredDocument] = field(default_factory=dict)
    chunks: dict[str, StoredChunk] = field(default_factory=dict)


_LOCAL_STORE = LocalOracleStore()
_SHARED_ORACLE_POOL: OraclePoolProtocol | None = None
_DB_TEST_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="oracle_db_test_")


class SelectAiUnavailableError(RuntimeError):
    """Select AI を実行できる Oracle 設定がない。"""

    safe_for_user = True


class OracleWalletPasswordRequiredError(RuntimeError):
    """パスワード必須 Wallet を対話プロンプトなしで止める。"""

    safe_for_user = True


class OracleConnectionTimeoutError(TimeoutError):
    """Oracle 接続テストが所定時間内に終わらないときのユーザー向けエラー。"""

    safe_for_user = True


class OracleClient:
    """Oracle 26ai 接続・ベクトル検索クライアント。"""

    def __init__(
        self,
        settings: Settings | None = None,
        pool: OraclePoolProtocol | None = None,
        db_call_runner: DbCallRunner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pool_instance = pool
        self._db_call_runner = db_call_runner or _run_db_call_in_thread

    async def vector_search(
        self,
        embedding: list[float],
        top_k: int,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        """AI Vector Search で近傍チャンクを取得する。

        例: SELECT ... ORDER BY VECTOR_DISTANCE(embedding, :v, COSINE) FETCH FIRST :k ROWS ONLY
        """
        self._validate_embedding_width(embedding, "query embedding")
        return await self._vector_search_with_oracle(embedding, top_k, filters or {})

    async def keyword_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        """Oracle Text 相当のキーワード検索を行う。"""
        return await self._keyword_search_with_oracle(query, top_k, filters or {})

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        """ベクトル検索とキーワード検索を Reciprocal Rank Fusion で統合する。"""
        if mode == SearchMode.VECTOR:
            return await self.vector_search(embedding, top_k, filters)
        if mode == SearchMode.KEYWORD:
            return await self.keyword_search(query, top_k, filters)

        vector_hits = await self.vector_search(embedding, top_k, filters)
        keyword_hits = await self.keyword_search(query, top_k, filters)
        fused: dict[str, RetrievedChunk] = {}
        scores: dict[str, float] = {}
        retrieval_metadata: dict[str, dict[str, MetadataValue]] = {}
        for rank, hit in enumerate(vector_hits, start=1):
            fused[hit.chunk_id] = hit
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + _rrf(
                rank, self._settings.rag_rrf_k
            )
            retrieval_metadata.setdefault(hit.chunk_id, {})["vector_rank"] = rank
            retrieval_metadata[hit.chunk_id]["vector_score"] = hit.score
        for rank, hit in enumerate(keyword_hits, start=1):
            fused[hit.chunk_id] = hit
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + _rrf(
                rank, self._settings.rag_rrf_k
            )
            retrieval_metadata.setdefault(hit.chunk_id, {})["keyword_rank"] = rank
            retrieval_metadata[hit.chunk_id]["keyword_score"] = hit.score
        ranked_ids = sorted(
            scores,
            key=lambda chunk_id: _retrieved_chunk_score_sort_key(
                fused[chunk_id],
                scores[chunk_id],
            ),
        )[:top_k]
        return [
            _with_retrieval_metadata(
                fused[chunk_id].model_copy(update={"score": round(scores[chunk_id], 6)}),
                retrieval_mode=_hybrid_retrieval_mode(retrieval_metadata[chunk_id]),
                rrf_k=self._settings.rag_rrf_k,
                rrf_score=round(scores[chunk_id], 6),
                **retrieval_metadata[chunk_id],
            )
            for chunk_id in ranked_ids
        ]

    async def context_neighbors(
        self,
        anchors: list[RetrievedChunk],
        *,
        window: int,
    ) -> list[RetrievedChunk]:
        """rerank 済み anchor chunk の前後を LLM context 補完用に取得する。"""
        if window <= 0 or not anchors:
            return []
        return await self._context_neighbors_with_oracle(anchors, window=window)

    async def context_group_siblings(
        self,
        anchors: list[RetrievedChunk],
        *,
        max_chunks_per_group: int,
    ) -> list[RetrievedChunk]:
        """rerank 済み anchor と同じ親 chunk group の sibling を取得する。"""
        if max_chunks_per_group <= 0 or not anchors:
            return []
        return await self._context_group_siblings_with_oracle(
            anchors,
            max_chunks_per_group=max_chunks_per_group,
        )

    async def select_ai(
        self,
        query: str,
        *,
        action: SelectAiAction = SelectAiAction.SHOWSQL,
        profile_name: str | None = None,
        max_result_chars: int | None = None,
    ) -> str:
        """Oracle Select AI profile で自然言語 query を SQL/結果へ変換する。"""
        resolved_profile = (profile_name or self._settings.oracle_select_ai_profile).strip()
        if not resolved_profile:
            raise SelectAiUnavailableError(SELECT_AI_UNAVAILABLE_ERROR)
        result_limit = max_result_chars or self._settings.oracle_select_ai_max_result_chars
        row = await self._fetch_one(
            """
            SELECT DBMS_CLOUD_AI.GENERATE(
                prompt       => :prompt,
                profile_name => :profile_name,
                action       => :action
            ) AS result_text
            FROM dual
            """,
            {
                "prompt": query,
                "profile_name": resolved_profile,
                "action": action.value,
            },
        )
        if row is None:
            return ""
        result = row.get("result_text")
        return str(result or "")[:result_limit]

    async def create_document(
        self,
        file_name: str,
        object_storage_path: str,
        content_type: str | None,
        file_size_bytes: int | None = None,
        content_sha256: str | None = None,
        duplicate_of_document_id: str | None = None,
    ) -> DocumentDetail:
        """ドキュメント行を作成する。"""
        return await self._create_document_with_oracle(
            file_name=file_name,
            object_storage_path=object_storage_path,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            content_sha256=content_sha256,
            duplicate_of_document_id=duplicate_of_document_id,
        )

    async def find_document_by_content_hash(self, content_sha256: str) -> DocumentSummary | None:
        """同一 content hash の既存ドキュメントを返す。"""
        return await self._find_document_by_content_hash_with_oracle(content_sha256)

    async def list_documents(
        self,
        status: FileStatus | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[DocumentSummary]:
        """ドキュメント一覧を返す。"""
        return await self._list_documents_with_oracle(
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )

    async def list_document_extractions(self) -> list[dict[str, object]]:
        """アクセス可能な document の extraction JSON だけを返す。"""
        return await self._list_document_extractions_with_oracle()

    async def count_documents(
        self,
        status: FileStatus | None = None,
        query: str | None = None,
    ) -> int:
        """条件に一致するドキュメント数を返す。"""
        return await self._count_documents_with_oracle(status=status, query=query)

    async def count_chunks(self) -> int:
        """検索可能なチャンク行数を返す。"""
        return await self._count_chunks_with_oracle()

    async def list_chunk_metadata(self) -> list[dict[str, MetadataValue]]:
        """検索対象 chunk の metadata JSON だけを返す。"""
        return await self._list_chunk_metadata_with_oracle()

    async def count_document_chunks(self, document_id: str) -> int:
        """指定 document の検索可能なチャンク行数を返す。"""
        return await self._count_document_chunks_with_oracle(document_id)

    async def document_stats(self) -> DocumentStats:
        """ドキュメント状態別の集計を返す。"""
        return await self._document_stats_with_oracle()

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        """ドキュメント詳細を返す。"""
        return await self._get_document_with_oracle(document_id)

    async def delete_document(self, document_id: str) -> bool:
        """ドキュメントと関連 chunk/index 行を削除する。"""
        return await self._delete_document_with_oracle(document_id)

    async def update_document_status(
        self,
        document_id: str,
        status: FileStatus,
        error_message: str | None = None,
    ) -> DocumentDetail:
        """ドキュメント状態を更新する。"""
        return await self._update_document_status_with_oracle(
            document_id=document_id,
            status=status,
            error_message=error_message,
        )

    async def save_extraction(
        self, document_id: str, extraction: StructuredExtraction
    ) -> DocumentDetail:
        """VLM/LLM の抽出本文を保存する。"""
        return await self._save_extraction_with_oracle(document_id, extraction)

    async def save_chunks(
        self,
        document_id: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> list[RetrievedChunk]:
        """チャンクとベクトルを保存する。"""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks と embeddings の件数が一致しません。")
        for index, embedding in enumerate(embeddings):
            self._validate_embedding_width(embedding, f"chunk embedding[{index}]")
        return await self._save_chunks_with_oracle(document_id, chunks, embeddings)

    async def _vector_search_with_oracle(
        self, embedding: list[float], top_k: int, filters: dict[str, str]
    ) -> list[RetrievedChunk]:
        """Oracle 26ai AI Vector Search で近傍 chunk を取得する。"""
        where_sql, binds = _oracle_retrieval_where(filters)
        binds.update(
            {
                "embedding": _to_vector_bind(embedding),
                "min_similarity": self._settings.rag_min_similarity,
            }
        )
        fetch_clause = _oracle_vector_fetch_clause(
            top_k=top_k,
            target_accuracy=self._settings.oracle_vector_target_accuracy,
        )
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                c.document_id,
                c.chunk_id,
                c.chunk_text,
                c.metadata_json,
                c.chunk_index,
                d.file_name,
                d.category_name,
                1 - VECTOR_DISTANCE(c.embedding, :embedding, COSINE) AS score
            FROM rag_chunks c
            JOIN rag_documents d ON d.document_id = c.document_id
            WHERE {where_sql}
              AND 1 - VECTOR_DISTANCE(c.embedding, :embedding, COSINE) >= :min_similarity
            ORDER BY
                VECTOR_DISTANCE(c.embedding, :embedding, COSINE) ASC,
                c.document_id ASC,
                c.chunk_index ASC,
                c.chunk_id ASC
            {fetch_clause}
            """,
                where_sql=where_sql,
                fetch_clause=fetch_clause,
            ),
            binds,
        )
        return [
            _with_retrieval_metadata(
                _retrieved_chunk_from_row(row),
                retrieval_mode="vector",
                vector_rank=rank,
                vector_score=round(_float_value(row.get("score", 0.0)), 6),
            )
            for rank, row in enumerate(rows, start=1)
        ]

    async def _keyword_search_with_oracle(
        self, query: str, top_k: int, filters: dict[str, str]
    ) -> list[RetrievedChunk]:
        """Oracle Text で keyword chunk を取得する。"""
        where_sql, binds = _oracle_retrieval_where(filters)
        binds.update({"query": query, "top_k": top_k})
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT *
            FROM (
                SELECT
                    c.document_id,
                    c.chunk_id,
                    c.chunk_text,
                    c.metadata_json,
                    c.chunk_index,
                    d.file_name,
                    d.category_name,
                    SCORE(1) / 100 AS score
                FROM rag_chunks c
                JOIN rag_documents d ON d.document_id = c.document_id
                WHERE {where_sql}
                  AND CONTAINS(c.chunk_text, :query, 1) > 0
                ORDER BY
                    SCORE(1) DESC,
                    c.document_id ASC,
                    c.chunk_index ASC,
                    c.chunk_id ASC
            )
            WHERE ROWNUM <= :top_k
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return [
            _with_retrieval_metadata(
                _retrieved_chunk_from_row(row),
                retrieval_mode="keyword",
                keyword_rank=rank,
                keyword_score=round(_float_value(row.get("score", 0.0)), 6),
            )
            for rank, row in enumerate(rows, start=1)
        ]

    async def _context_neighbors_with_oracle(
        self,
        anchors: list[RetrievedChunk],
        *,
        window: int,
    ) -> list[RetrievedChunk]:
        """Oracle から同一 document の隣接 chunk を取得する。"""
        neighbors: list[RetrievedChunk] = []
        for anchor in anchors:
            anchor_index = _chunk_index_from_retrieved(anchor)
            if anchor_index is None:
                continue
            where_sql, binds = _oracle_retrieval_where({"document_id": anchor.document_id})
            binds.update(
                {
                    "anchor_index": anchor_index,
                    "anchor_chunk_id": anchor.chunk_id,
                    "start_index": anchor_index - window,
                    "end_index": anchor_index + window,
                }
            )
            rows = await self._fetch_all(
                _render_sql(
                    """
                SELECT
                    c.document_id,
                    c.chunk_id,
                    c.chunk_text,
                    c.metadata_json,
                    c.chunk_index,
                    d.file_name,
                    d.category_name,
                    0 AS score
                FROM rag_chunks c
                JOIN rag_documents d ON d.document_id = c.document_id
                WHERE {where_sql}
                  AND c.chunk_index BETWEEN :start_index AND :end_index
                  AND c.chunk_id <> :anchor_chunk_id
                ORDER BY
                    ABS(c.chunk_index - :anchor_index) ASC,
                    c.chunk_index ASC,
                    c.chunk_id ASC
                """,
                    where_sql=where_sql,
                ),
                binds,
            )
            for row in rows:
                neighbor = _retrieved_chunk_from_row(row).model_copy(update={"score": anchor.score})
                neighbor_index = _chunk_index_from_retrieved(neighbor)
                if neighbor_index is None:
                    continue
                neighbors.append(
                    _with_context_neighbor_metadata(
                        neighbor,
                        anchor=anchor,
                        distance=neighbor_index - anchor_index,
                    )
                )
        return neighbors

    async def _context_group_siblings_with_oracle(
        self,
        anchors: list[RetrievedChunk],
        *,
        max_chunks_per_group: int,
    ) -> list[RetrievedChunk]:
        """Oracle から同一 parent chunk group の sibling を取得する。"""
        siblings: list[RetrievedChunk] = []
        for anchor in anchors:
            group_id = _chunk_group_id_from_retrieved(anchor)
            anchor_index = _chunk_index_from_retrieved(anchor)
            if group_id is None or anchor_index is None:
                continue
            where_sql, binds = _oracle_retrieval_where({"document_id": anchor.document_id})
            binds.update(
                {
                    "chunk_group_id": group_id,
                    "anchor_index": anchor_index,
                    "anchor_chunk_id": anchor.chunk_id,
                    "max_chunks_per_group": max_chunks_per_group,
                }
            )
            rows = await self._fetch_all(
                _render_sql(
                    """
                SELECT *
                FROM (
                    SELECT
                        c.document_id,
                        c.chunk_id,
                        c.chunk_text,
                        c.metadata_json,
                        c.chunk_index,
                        d.file_name,
                        d.category_name,
                        0 AS score
                    FROM rag_chunks c
                    JOIN rag_documents d ON d.document_id = c.document_id
                    WHERE {where_sql}
                      AND JSON_VALUE(c.metadata_json, '$.chunk_group_id') = :chunk_group_id
                      AND c.chunk_id <> :anchor_chunk_id
                    ORDER BY
                        ABS(c.chunk_index - :anchor_index) ASC,
                        c.chunk_index ASC,
                        c.chunk_id ASC
                )
                WHERE ROWNUM <= :max_chunks_per_group
                """,
                    where_sql=where_sql,
                ),
                binds,
            )
            for row in rows:
                sibling = _retrieved_chunk_from_row(row).model_copy(update={"score": anchor.score})
                sibling_index = _chunk_index_from_retrieved(sibling)
                if sibling_index is None:
                    continue
                siblings.append(
                    _with_context_group_metadata(
                        sibling,
                        anchor=anchor,
                        group_id=group_id,
                        distance=sibling_index - anchor_index,
                    )
                )
        return siblings

    async def _create_document_with_oracle(
        self,
        file_name: str,
        object_storage_path: str,
        content_type: str | None,
        file_size_bytes: int | None,
        content_sha256: str | None,
        duplicate_of_document_id: str | None,
    ) -> DocumentDetail:
        """Oracle document table へ文書行を作成する。"""
        document_id = uuid4().hex
        uploaded_at = datetime.now(UTC)
        document = StoredDocument(
            id=document_id,
            file_name=file_name,
            status=FileStatus.UPLOADED,
            uploaded_at=uploaded_at,
            object_storage_path=object_storage_path,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            content_sha256=content_sha256,
            duplicate_of_document_id=duplicate_of_document_id,
            tenant_id_hash=_current_tenant_id_hash(),
        )

        def operation(connection: OracleConnectionProtocol) -> DocumentDetail:
            _execute(
                connection,
                """
                INSERT INTO rag_documents (
                    document_id,
                    file_name,
                    status,
                    tenant_id_hash,
                    object_storage_path,
                    content_type,
                    file_size_bytes,
                    content_sha256,
                    duplicate_of_document_id,
                    uploaded_at
                ) VALUES (
                    :document_id,
                    :file_name,
                    :status,
                    :tenant_id_hash,
                    :object_storage_path,
                    :content_type,
                    :file_size_bytes,
                    :content_sha256,
                    :duplicate_of_document_id,
                    :uploaded_at
                )
                """,
                _document_binds(document),
            )
            return _to_document_detail(document)

        return await self._run_transaction(operation)

    async def _find_document_by_content_hash_with_oracle(
        self, content_sha256: str
    ) -> DocumentSummary | None:
        """Oracle document table から content hash で既存文書を取得する。"""
        where_sql, binds = _oracle_document_where()
        binds["content_sha256"] = content_sha256
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT *
            FROM (
                SELECT
                    document_id,
                    file_name,
                    status,
                    tenant_id_hash,
                    category_name,
                    object_storage_path,
                    content_type,
                    file_size_bytes,
                    content_sha256,
                    duplicate_of_document_id,
                    extraction,
                    error_message,
                    uploaded_at,
                    indexed_at
                FROM rag_documents
                WHERE {where_sql}
                  AND content_sha256 = :content_sha256
                ORDER BY
                    CASE WHEN duplicate_of_document_id IS NULL THEN 0 ELSE 1 END,
                    uploaded_at ASC
            )
            WHERE ROWNUM <= 1
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        if not rows:
            return None
        return _to_document_summary(_stored_document_from_row(rows[0]))

    async def _list_documents_with_oracle(
        self,
        status: FileStatus | None,
        query: str | None,
        limit: int | None,
        offset: int,
    ) -> list[DocumentSummary]:
        """Oracle document table から一覧取得する。"""
        where_sql, binds = _oracle_document_where(status=status, query=query)
        binds.update({"offset": offset, "limit": limit})
        limit_clause = "OFFSET :offset ROWS"
        if limit is not None:
            limit_clause += " FETCH NEXT :limit ROWS ONLY"
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                document_id,
                file_name,
                status,
                tenant_id_hash,
                category_name,
                object_storage_path,
                content_type,
                file_size_bytes,
                content_sha256,
                duplicate_of_document_id,
                extraction,
                error_message,
                uploaded_at,
                indexed_at
            FROM rag_documents
            WHERE {where_sql}
            ORDER BY uploaded_at DESC
            {limit_clause}
            """,
                where_sql=where_sql,
                limit_clause=limit_clause,
            ),
            binds,
        )
        return [_to_document_summary(_stored_document_from_row(row)) for row in rows]

    async def _list_document_extractions_with_oracle(self) -> list[dict[str, object]]:
        """Oracle document table から extraction JSON だけを取得する。"""
        where_sql, binds = _oracle_document_where()
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT extraction
            FROM rag_documents
            WHERE {where_sql}
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return [_json_loads(row.get("extraction")) for row in rows]

    async def _count_documents_with_oracle(
        self,
        status: FileStatus | None,
        query: str | None,
    ) -> int:
        """Oracle document table の件数を取得する。"""
        where_sql, binds = _oracle_document_where(status=status, query=query)
        row = await self._fetch_one(
            _render_sql(
                "SELECT COUNT(*) AS count_value FROM rag_documents WHERE {where_sql}",
                where_sql=where_sql,
            ),
            binds,
        )
        return _row_count_value(row)

    async def _count_chunks_with_oracle(self) -> int:
        """Oracle chunk/vector table の検索可能件数を取得する。"""
        where_sql, binds = _oracle_retrieval_where({})
        row = await self._fetch_one(
            _render_sql(
                """
            SELECT COUNT(*) AS count_value
            FROM rag_chunks c
            JOIN rag_documents d ON d.document_id = c.document_id
            WHERE {where_sql}
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return _row_count_value(row)

    async def _list_chunk_metadata_with_oracle(self) -> list[dict[str, MetadataValue]]:
        """Oracle chunk table から検索対象 chunk の metadata JSON だけを取得する。"""
        where_sql, binds = _oracle_retrieval_where({})
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT c.metadata_json
            FROM rag_chunks c
            JOIN rag_documents d ON d.document_id = c.document_id
            WHERE {where_sql}
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return [_metadata_from_json(row.get("metadata_json")) for row in rows]

    async def _count_document_chunks_with_oracle(self, document_id: str) -> int:
        """Oracle chunk/vector table の document 別検索可能件数を取得する。"""
        where_sql, binds = _oracle_retrieval_where({"document_id": document_id})
        row = await self._fetch_one(
            _render_sql(
                """
            SELECT COUNT(*) AS count_value
            FROM rag_chunks c
            JOIN rag_documents d ON d.document_id = c.document_id
            WHERE {where_sql}
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return _row_count_value(row)

    async def _document_stats_with_oracle(self) -> DocumentStats:
        """Oracle document table の状態別集計を取得する。"""
        where_sql, binds = _oracle_document_where()
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT status, COUNT(*) AS count_value
            FROM rag_documents
            WHERE {where_sql}
            GROUP BY status
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        counts = {status: 0 for status in FileStatus}
        for row in rows:
            status_value = row.get("status")
            if isinstance(status_value, FileStatus):
                status = status_value
            elif isinstance(status_value, str):
                try:
                    status = FileStatus(status_value)
                except ValueError:
                    continue
            else:
                continue
            counts[status] = _int_value(row.get("count_value", 0))
        return DocumentStats(total=sum(counts.values()), by_status=counts)

    async def _get_document_with_oracle(self, document_id: str) -> DocumentDetail | None:
        """Oracle document table から詳細取得する。"""
        row = await self._fetch_one(
            _render_sql(
                """
            SELECT
                document_id,
                file_name,
                status,
                tenant_id_hash,
                category_name,
                object_storage_path,
                content_type,
                file_size_bytes,
                content_sha256,
                duplicate_of_document_id,
                extraction,
                error_message,
                uploaded_at,
                indexed_at
            FROM rag_documents
            WHERE document_id = :document_id
              AND {access_predicate}
            """,
                access_predicate=_oracle_access_predicate_sql(),
            ),
            _with_tenant_bind({"document_id": document_id}),
        )
        return None if row is None else _to_document_detail(_stored_document_from_row(row))

    async def _update_document_status_with_oracle(
        self,
        document_id: str,
        status: FileStatus,
        error_message: str | None,
    ) -> DocumentDetail:
        """Oracle document table の状態を更新する。"""

        def operation(connection: OracleConnectionProtocol) -> DocumentDetail:
            existing = _select_document(connection, document_id)
            if existing is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            if status in (FileStatus.INGESTING, FileStatus.ERROR):
                _execute(
                    connection,
                    """
                    DELETE FROM rag_chunks
                    WHERE document_id = :document_id
                    """,
                    {"document_id": document_id},
                )
                _execute(
                    connection,
                    _render_sql(
                        """
                    UPDATE rag_documents
                    SET
                        status = :status,
                        error_message = :error_message,
                        extraction = NULL
                    WHERE document_id = :document_id
                      AND {access_predicate}
                    """,
                        access_predicate=_oracle_access_predicate_sql(),
                    ),
                    _with_tenant_bind(
                        {
                            "document_id": document_id,
                            "status": status.value,
                            "error_message": error_message,
                        }
                    ),
                )
            elif status == FileStatus.INDEXED:
                _execute(
                    connection,
                    _render_sql(
                        """
                    UPDATE rag_documents
                    SET
                        status = :status,
                        error_message = :error_message,
                        indexed_at = COALESCE(indexed_at, SYSTIMESTAMP)
                    WHERE document_id = :document_id
                      AND {access_predicate}
                    """,
                        access_predicate=_oracle_access_predicate_sql(),
                    ),
                    _with_tenant_bind(
                        {
                            "document_id": document_id,
                            "status": status.value,
                            "error_message": error_message,
                        }
                    ),
                )
            else:
                _execute(
                    connection,
                    _render_sql(
                        """
                    UPDATE rag_documents
                    SET
                        status = :status,
                        error_message = :error_message
                    WHERE document_id = :document_id
                      AND {access_predicate}
                    """,
                        access_predicate=_oracle_access_predicate_sql(),
                    ),
                    _with_tenant_bind(
                        {
                            "document_id": document_id,
                            "status": status.value,
                            "error_message": error_message,
                        }
                    ),
                )
            document = _select_document(connection, document_id)
            if document is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            return _to_document_detail(document)

        return await self._run_transaction(operation)

    async def _delete_document_with_oracle(self, document_id: str) -> bool:
        """Oracle document table と chunk/vector table から指定 document を削除する。"""

        def operation(connection: OracleConnectionProtocol) -> bool:
            existing = _select_document(connection, document_id)
            if existing is None:
                return False
            _execute(
                connection,
                """
                DELETE FROM rag_chunks
                WHERE document_id = :document_id
                """,
                {"document_id": document_id},
            )
            _execute(
                connection,
                _render_sql(
                    """
                DELETE FROM rag_documents
                WHERE document_id = :document_id
                  AND {access_predicate}
                """,
                    access_predicate=_oracle_access_predicate_sql(),
                ),
                _with_tenant_bind({"document_id": document_id}),
            )
            return True

        return await self._run_transaction(operation)

    async def _save_extraction_with_oracle(
        self,
        document_id: str,
        extraction: StructuredExtraction,
    ) -> DocumentDetail:
        """Oracle document table へ構造化抽出を保存する。"""

        def operation(connection: OracleConnectionProtocol) -> DocumentDetail:
            _execute(
                connection,
                _render_sql(
                    """
                UPDATE rag_documents
                SET extraction = :extraction
                WHERE document_id = :document_id
                  AND {access_predicate}
                """,
                    access_predicate=_oracle_access_predicate_sql(),
                ),
                _with_tenant_bind(
                    {
                        "document_id": document_id,
                        "extraction": _json_dumps(extraction.to_document_payload()),
                    }
                ),
            )
            document = _select_document(connection, document_id)
            if document is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            return _to_document_detail(document)

        return await self._run_transaction(operation)

    async def _save_chunks_with_oracle(
        self,
        document_id: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> list[RetrievedChunk]:
        """Oracle chunk/vector table へ chunk と embedding を保存する。"""

        def operation(connection: OracleConnectionProtocol) -> list[RetrievedChunk]:
            document = _select_document(connection, document_id)
            if document is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            _execute(
                connection,
                """
                DELETE FROM rag_chunks
                WHERE document_id = :document_id
                """,
                {"document_id": document_id},
            )
            rows = [
                {
                    "chunk_id": f"{document_id}:{chunk.index}",
                    "document_id": document_id,
                    "tenant_id_hash": document.tenant_id_hash,
                    "chunk_index": chunk.index,
                    "chunk_text": chunk.text,
                    "metadata_json": _json_dumps(
                        {
                            "chunk_index": chunk.index,
                            "start_offset": chunk.start_offset,
                            "end_offset": chunk.end_offset,
                            **chunk.metadata,
                        }
                    ),
                    "embedding": _to_vector_bind(embedding),
                }
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ]
            if rows:
                _executemany(
                    connection,
                    """
                    INSERT INTO rag_chunks (
                        chunk_id,
                        document_id,
                        tenant_id_hash,
                        chunk_index,
                        chunk_text,
                        metadata_json,
                        embedding
                    ) VALUES (
                        :chunk_id,
                        :document_id,
                        :tenant_id_hash,
                        :chunk_index,
                        :chunk_text,
                        :metadata_json,
                        :embedding
                    )
                    """,
                    rows,
                )
            return [
                RetrievedChunk(
                    document_id=document_id,
                    chunk_id=str(row["chunk_id"]),
                    text=str(row["chunk_text"]),
                    score=1.0,
                    file_name=document.file_name,
                    category_name=document.category_name,
                    metadata=_json_loads(row["metadata_json"]),
                )
                for row in rows
            ]

        return await self._run_transaction(operation)

    async def _fetch_one(
        self, statement: str, binds: Mapping[str, object] | None = None
    ) -> dict[str, object] | None:
        """Oracle から 1 行を取得する。"""
        rows = await self._fetch_all(statement, binds)
        return rows[0] if rows else None

    async def _fetch_all(
        self, statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        """Oracle から行を dict として取得する。"""
        return cast(
            list[dict[str, object]],
            await self._db_call_runner(
                lambda: self._run_with_connection(
                    lambda connection: _fetch_all(connection, statement, binds or {})
                )
            ),
        )

    async def _run_transaction(self, operation: Callable[[OracleConnectionProtocol], T]) -> T:
        """Oracle transaction を同期 SDK thread で実行する。"""
        return cast(T, await self._db_call_runner(lambda: self._run_transaction_sync(operation)))

    def _run_transaction_sync(self, operation: Callable[[OracleConnectionProtocol], T]) -> T:
        connection = self._acquire_connection()
        try:
            result = operation(connection)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _run_with_connection(self, operation: Callable[[OracleConnectionProtocol], Any]) -> Any:
        connection = self._acquire_connection()
        try:
            return operation(connection)
        finally:
            connection.close()

    def _acquire_connection(self) -> OracleConnectionProtocol:
        """pool から connection を取得する。"""
        return self._pool().acquire()

    def _pool(self) -> OraclePoolProtocol:
        """python-oracledb connection pool を遅延初期化する。"""
        if self._pool_instance is not None:
            return self._pool_instance
        global _SHARED_ORACLE_POOL
        if _SHARED_ORACLE_POOL is not None:
            return _SHARED_ORACLE_POOL

        oracledb = importlib.import_module("oracledb")
        pool_kwargs = _oracle_connect_kwargs(
            self._settings,
            extra={
                "min": 1,
                "max": 4,
                "increment": 1,
            },
        )
        _SHARED_ORACLE_POOL = oracledb.create_pool(**pool_kwargs)
        return _SHARED_ORACLE_POOL

    def _to_retrieved_chunk(
        self,
        chunk: StoredChunk,
        score: float,
        document: StoredDocument | None = None,
    ) -> RetrievedChunk:
        """StoredChunk を API スキーマへ変換する。"""
        source = document or _LOCAL_STORE.documents.get(chunk.document_id)
        return RetrievedChunk(
            document_id=chunk.document_id,
            chunk_id=chunk.id,
            text=chunk.text,
            score=round(score, 6),
            file_name=source.file_name if source else None,
            category_name=source.category_name if source else None,
            metadata=chunk.metadata,
        )

    def _validate_embedding_width(self, embedding: list[float], label: str) -> None:
        """Oracle VECTOR(1536, FLOAT32) に保存/検索できる幅か検証する。"""
        expected_dim = self._settings.oci_genai_embedding_dim
        if len(embedding) != expected_dim:
            raise ValueError(
                f"{label} の次元数が不正です。expected={expected_dim}, actual={len(embedding)}"
            )


async def _run_db_call_in_thread(operation: Callable[[], Any]) -> Any:
    """同期 python-oracledb 呼び出しを event loop 外で実行する。"""
    return await asyncio.to_thread(operation)


async def _run_db_test_call_in_thread(operation: Callable[[], Any]) -> Any:
    """接続テスト専用の小さい thread pool で DB 呼び出しを実行する。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_DB_TEST_EXECUTOR, operation)


def _fetch_all(
    connection: OracleConnectionProtocol,
    statement: str,
    binds: Mapping[str, object],
) -> list[dict[str, object]]:
    cursor = connection.cursor()
    try:
        cursor.execute(_normalize_sql(statement), dict(binds))
        rows = cursor.fetchall()
        return [_row_to_dict(row, cursor.description) for row in rows]
    finally:
        cursor.close()


def _execute(
    connection: OracleConnectionProtocol,
    statement: str,
    binds: Mapping[str, object],
) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(_normalize_sql(statement), dict(binds))
    finally:
        cursor.close()


def _executemany(
    connection: OracleConnectionProtocol,
    statement: str,
    rows: Sequence[Mapping[str, object]],
) -> None:
    cursor = connection.cursor()
    try:
        cursor.executemany(_normalize_sql(statement), rows)
    finally:
        cursor.close()


def _select_document(
    connection: OracleConnectionProtocol,
    document_id: str,
) -> StoredDocument | None:
    rows = _fetch_all(
        connection,
        _render_sql(
            """
        SELECT
            document_id,
            file_name,
            status,
            tenant_id_hash,
            category_name,
            object_storage_path,
            content_type,
            file_size_bytes,
            content_sha256,
            duplicate_of_document_id,
            extraction,
            error_message,
            uploaded_at,
            indexed_at
        FROM rag_documents
        WHERE document_id = :document_id
          AND {access_predicate}
        """,
            access_predicate=_oracle_access_predicate_sql(),
        ),
        _with_tenant_bind({"document_id": document_id}),
    )
    return None if not rows else _stored_document_from_row(rows[0])


def _row_to_dict(row: object, description: Sequence[Sequence[Any]] | None) -> dict[str, object]:
    if isinstance(row, Mapping):
        return {str(key).lower(): _read_db_value(value) for key, value in row.items()}
    if not isinstance(row, Sequence) or isinstance(row, str | bytes | bytearray):
        raise ValueError("Oracle row の形式が不正です。")
    columns = _description_columns(description)
    return {column: _read_db_value(value) for column, value in zip(columns, row, strict=False)}


def _description_columns(description: Sequence[Sequence[Any]] | None) -> list[str]:
    if description is None:
        return []
    columns: list[str] = []
    for item in description:
        if not item:
            continue
        columns.append(str(item[0]).lower())
    return columns


def _read_db_value(value: object) -> object:
    read = getattr(value, "read", None)
    if callable(read):
        return read()
    return value


def _normalize_sql(statement: str) -> str:
    return re.sub(r"\s+", " ", statement).strip()


def _render_sql(template: str, **parts: str) -> str:
    """内部生成した SQL 断片だけを template へ埋め込む。bind 値はここに渡さない。"""
    rendered = template
    for name, value in parts.items():
        rendered = rendered.replace(f"{{{name}}}", value)
    return rendered


def _oracle_document_where(
    *,
    status: FileStatus | None = None,
    query: str | None = None,
) -> tuple[str, dict[str, object]]:
    clauses = _oracle_access_predicates()
    binds = _with_tenant_bind({})
    if status is not None:
        clauses.append("status = :status")
        binds["status"] = status.value
    if query and query.strip():
        clauses.append(
            "(LOWER(file_name) LIKE :query ESCAPE '\\' "
            "OR LOWER(category_name) LIKE :query ESCAPE '\\')"
        )
        binds["query"] = _like_pattern(query)
    return " AND ".join(clauses), binds


def _oracle_retrieval_where(filters: dict[str, str]) -> tuple[str, dict[str, object]]:
    clauses = ["d.status = 'INDEXED'", *_oracle_access_predicates(alias="d")]
    binds = _with_tenant_bind({}, alias="d")
    for key, value in filters.items():
        cleaned = value.strip()
        if not cleaned:
            continue
        if key == "document_id":
            clauses.append("d.document_id = :filter_document_id")
            binds["filter_document_id"] = cleaned
        elif key == "status":
            clauses.append("d.status = :filter_status")
            binds["filter_status"] = cleaned
        elif key == "file_name":
            clauses.append("LOWER(d.file_name) LIKE :filter_file_name ESCAPE '\\'")
            binds["filter_file_name"] = _like_pattern(cleaned)
        elif key == "category_name":
            clauses.append("LOWER(d.category_name) LIKE :filter_category_name ESCAPE '\\'")
            binds["filter_category_name"] = _like_pattern(cleaned)
        elif key == "content_kind":
            clauses.append(
                "LOWER(JSON_VALUE(c.metadata_json, '$.content_kind')) = :filter_content_kind"
            )
            binds["filter_content_kind"] = cleaned.casefold()
        elif key == "section_title":
            clauses.append(
                "LOWER(JSON_VALUE(c.metadata_json, '$.section_title')) "
                "LIKE :filter_section_title ESCAPE '\\'"
            )
            binds["filter_section_title"] = _like_pattern(cleaned)
        elif key == "section_path":
            clauses.append(
                "LOWER(JSON_VALUE(c.metadata_json, '$.section_path')) "
                "LIKE :filter_section_path ESCAPE '\\'"
            )
            binds["filter_section_path"] = _like_pattern(cleaned)
        else:
            raise ValueError(f"未対応の検索フィルターです: {key}")
    return " AND ".join(clauses), binds


def _oracle_vector_fetch_clause(*, top_k: int, target_accuracy: int) -> str:
    """Oracle AI Vector Search の approximate top-k 句を安全な整数 literal で返す。"""
    top_k_literal = _bounded_int_literal(top_k, name="top_k", minimum=1, maximum=1000)
    target_accuracy_literal = _bounded_int_literal(
        target_accuracy,
        name="oracle_vector_target_accuracy",
        minimum=1,
        maximum=100,
    )
    return (
        f"FETCH APPROX FIRST {top_k_literal} ROWS ONLY "
        f"WITH TARGET ACCURACY {target_accuracy_literal}"
    )


def _oracle_tenant_predicate(*, alias: str | None = None) -> str:
    tenant_id_hash = _current_tenant_id_hash()
    if tenant_id_hash is None:
        return "1 = 1"
    column = f"{alias}.tenant_id_hash" if alias else "tenant_id_hash"
    return f"{column} = :tenant_id_hash"


def _oracle_access_predicates(*, alias: str | None = None) -> list[str]:
    """tenant と認可済み document/category scope を SQL predicate にする。"""
    context = current_audit_request_context()
    predicates = [_oracle_tenant_predicate(alias=alias)]
    document_column = f"{alias}.document_id" if alias else "document_id"
    if context.allowed_document_ids is not None:
        if not context.allowed_document_ids:
            predicates.append("1 = 0")
        else:
            placeholders = ", ".join(
                f":access_document_id_{index}"
                for index, _ in enumerate(sorted(context.allowed_document_ids))
            )
            predicates.append(f"{document_column} IN ({placeholders})")
    category_column = f"{alias}.category_name" if alias else "category_name"
    if context.allowed_category_names is not None:
        if not context.allowed_category_names:
            predicates.append("1 = 0")
        else:
            placeholders = ", ".join(
                f":access_category_name_{index}"
                for index, _ in enumerate(sorted(context.allowed_category_names))
            )
            predicates.append(f"LOWER({category_column}) IN ({placeholders})")
    return predicates


def _oracle_access_predicate_sql(*, alias: str | None = None) -> str:
    """tenant と認可 scope の predicate を AND で結合する。"""
    return " AND ".join(_oracle_access_predicates(alias=alias))


def _with_tenant_bind(
    binds: Mapping[str, object],
    *,
    alias: str | None = None,
) -> dict[str, object]:
    del alias
    resolved = dict(binds)
    context = current_audit_request_context()
    if context.tenant_id_hash is not None:
        resolved["tenant_id_hash"] = context.tenant_id_hash
    if context.allowed_document_ids is not None:
        for index, document_id in enumerate(sorted(context.allowed_document_ids)):
            resolved[f"access_document_id_{index}"] = document_id
    if context.allowed_category_names is not None:
        for index, category_name in enumerate(sorted(context.allowed_category_names)):
            resolved[f"access_category_name_{index}"] = category_name
    return resolved


def _like_pattern(value: str) -> str:
    escaped = value.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _document_binds(document: StoredDocument) -> dict[str, object]:
    return {
        "document_id": document.id,
        "file_name": document.file_name,
        "status": document.status.value,
        "tenant_id_hash": document.tenant_id_hash,
        "object_storage_path": document.object_storage_path,
        "content_type": document.content_type,
        "file_size_bytes": document.file_size_bytes,
        "content_sha256": document.content_sha256,
        "duplicate_of_document_id": document.duplicate_of_document_id,
        "uploaded_at": document.uploaded_at,
    }


def _stored_document_from_row(row: Mapping[str, object]) -> StoredDocument:
    return StoredDocument(
        id=str(row["document_id"]),
        file_name=str(row["file_name"]),
        status=_file_status(row["status"]),
        uploaded_at=_datetime_value(row.get("uploaded_at")),
        object_storage_path=_optional_str(row.get("object_storage_path")),
        content_type=_optional_str(row.get("content_type")),
        file_size_bytes=_optional_int(row.get("file_size_bytes")),
        content_sha256=_optional_str(row.get("content_sha256")),
        duplicate_of_document_id=_optional_str(row.get("duplicate_of_document_id")),
        tenant_id_hash=_optional_str(row.get("tenant_id_hash")),
        category_name=_optional_str(row.get("category_name")),
        indexed_at=_optional_datetime(row.get("indexed_at")),
        extraction=_json_loads(row.get("extraction")),
        error_message=_optional_str(row.get("error_message")),
    )


def _retrieved_chunk_from_row(row: Mapping[str, object]) -> RetrievedChunk:
    metadata = _metadata_from_json(row.get("metadata_json"))
    chunk_index = row.get("chunk_index")
    if "chunk_index" not in metadata and chunk_index is not None:
        metadata["chunk_index"] = _int_value(chunk_index)
    return RetrievedChunk(
        document_id=str(row["document_id"]),
        chunk_id=str(row["chunk_id"]),
        text=str(row["chunk_text"]),
        score=round(_float_value(row.get("score", 0.0)), 6),
        file_name=_optional_str(row.get("file_name")),
        category_name=_optional_str(row.get("category_name")),
        metadata=metadata,
    )


def _metadata_from_json(value: object) -> dict[str, MetadataValue]:
    decoded = _json_loads(value)
    return {str(key): _coerce_metadata_value(item) for key, item in decoded.items()}


def _coerce_metadata_value(item: object) -> MetadataValue:
    """JSON 由来の値を MetadataValue へ正規化する。

    Oracle の JSON 列は数値を Decimal で返すため、int/float に戻す。
    """
    if item is None or isinstance(item, bool):
        return item
    if isinstance(item, Decimal):
        return int(item) if item == item.to_integral_value() else float(item)
    if isinstance(item, str | int | float):
        return item
    return str(item)


def _json_dumps(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    text = str(value).strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(decoded, Mapping):
        return {str(key): item for key, item in decoded.items()}
    return {}


def _row_count_value(row: Mapping[str, object] | None) -> int:
    if row is None:
        return 0
    value = row.get("count_value")
    return _int_value(value)


def _file_status(value: object) -> FileStatus:
    if isinstance(value, FileStatus):
        return value
    return FileStatus(str(value))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _int_value(value)


def _datetime_value(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return datetime.now(UTC)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _datetime_value(value)


def _float_value(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, int | float | Decimal):
        return float(value)
    if isinstance(value, str | bytes | bytearray):
        return float(value)
    raise ValueError(f"数値に変換できない値です: {value!r}")


def _int_value(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | Decimal):
        return int(value)
    if isinstance(value, str | bytes | bytearray):
        return int(value)
    raise ValueError(f"整数に変換できない値です: {value!r}")


def _bounded_int_literal(value: int, *, name: str, minimum: int, maximum: int) -> str:
    """SQL grammar position に使う整数 literal を範囲検証して返す。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} は整数で指定してください。")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} は {minimum} から {maximum} の範囲で指定してください。")
    return str(value)


def reset_local_store() -> None:
    """テスト用にローカルストアを初期化する。"""
    _LOCAL_STORE.documents.clear()
    _LOCAL_STORE.chunks.clear()


def close_oracle_pool() -> None:
    """共有 Oracle pool を閉じる。アプリ終了時に呼び出す。"""
    global _SHARED_ORACLE_POOL
    if _SHARED_ORACLE_POOL is None:
        return
    _SHARED_ORACLE_POOL.close()
    _SHARED_ORACLE_POOL = None


async def test_oracle_connection(
    settings: Settings | None = None,
    db_call_runner: DbCallRunner | None = None,
) -> None:
    """Oracle へ 1 回だけ接続し、最小クエリで疎通を確認する。"""
    effective_settings = settings or get_settings()
    runner = db_call_runner or _run_db_test_call_in_thread
    timeout_seconds = float(getattr(effective_settings, "oracle_db_test_timeout_seconds", 15.0))
    try:
        await asyncio.wait_for(
            runner(lambda: _test_oracle_connection_sync(effective_settings)),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise OracleConnectionTimeoutError(
            f"Oracle 26ai 接続テストが {timeout_seconds:g} 秒でタイムアウトしました。"
            "データベースの起動状態、Wallet サービス名、ネットワーク到達性を確認してください。"
        ) from exc


def _test_oracle_connection_sync(settings: Settings) -> None:
    """同期 SDK で Oracle 接続を検証する。"""
    oracledb = importlib.import_module("oracledb")
    connect_kwargs = _oracle_connect_kwargs(
        settings,
        extra={
            "dsn": _oracle_connection_test_dsn(settings),
            "retry_count": 0,
            "retry_delay": 0,
        },
    )

    connection = oracledb.connect(**connect_kwargs)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1 FROM DUAL")
            cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()


def _oracle_connect_kwargs(
    settings: Settings,
    *,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """python-oracledb connect/create_pool に渡す共通 kwargs を作る。"""
    kwargs: dict[str, object] = {
        "user": settings.oracle_user,
        "dsn": settings.oracle_dsn,
    }
    if extra:
        kwargs.update(extra)
    tcp_connect_timeout = float(getattr(settings, "oracle_tcp_connect_timeout_seconds", 10.0))
    if tcp_connect_timeout > 0:
        kwargs["tcp_connect_timeout"] = tcp_connect_timeout
    if settings.oracle_password.strip():
        kwargs["password"] = settings.oracle_password
    _add_wallet_kwargs(settings, kwargs)
    return kwargs


def _oracle_connection_test_dsn(settings: Settings) -> str:
    """接続テストでは Wallet alias の長い retry 設定を外した descriptor を使う。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return settings.oracle_dsn
    descriptor = _tns_alias_descriptor(Path(wallet_dir).expanduser(), settings.oracle_dsn)
    if not descriptor:
        return settings.oracle_dsn
    return _strip_tns_retry_settings(descriptor)


def _tns_alias_descriptor(wallet_path: Path, alias: str) -> str | None:
    """tnsnames.ora から指定 alias の connect descriptor を抜き出す。"""
    tnsnames = wallet_path / "tnsnames.ora"
    if not alias.strip() or not tnsnames.is_file():
        return None
    try:
        content = tnsnames.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for match in re.finditer(r"(?im)^\s*([A-Za-z0-9_.-]+)\s*=\s*", content):
        if match.group(1).lower() != alias.lower():
            continue
        descriptor_start = content.find("(", match.end())
        if descriptor_start < 0:
            return None
        return _balanced_parenthesized_text(content, descriptor_start)
    return None


def _balanced_parenthesized_text(content: str, start: int) -> str | None:
    """start 位置から始まる括弧式を top-level まで読み取る。"""
    depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
        if depth < 0:
            return None
    return None


def _strip_tns_retry_settings(descriptor: str) -> str:
    """ADB Wallet の長い retry 設定を接続テスト用に取り除く。"""
    without_retry_count = re.sub(r"\(\s*retry_count\s*=\s*\d+\s*\)", "", descriptor, flags=re.I)
    return re.sub(r"\(\s*retry_delay\s*=\s*\d+\s*\)", "", without_retry_count, flags=re.I)


def _add_wallet_kwargs(settings: Settings, kwargs: dict[str, object]) -> None:
    """Wallet 設定を kwargs に追加する。パスワード要求プロンプトは事前に防ぐ。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return

    wallet_path = Path(wallet_dir).expanduser()
    if not wallet_path.is_dir():
        return

    wallet_password = _oracle_wallet_password(settings)
    if not wallet_password and _wallet_requires_password(wallet_path):
        raise OracleWalletPasswordRequiredError(WALLET_PASSWORD_REQUIRED_ERROR)

    resolved_wallet_path = str(wallet_path)
    kwargs["config_dir"] = resolved_wallet_path
    kwargs["wallet_location"] = resolved_wallet_path
    if wallet_password:
        kwargs["wallet_password"] = wallet_password


def _oracle_wallet_password(settings: Settings) -> str:
    """Wallet password は専用値がなければ DB password を使う。"""
    return settings.oracle_wallet_password.strip() or settings.oracle_password.strip()


def _wallet_requires_password(wallet_path: Path) -> bool:
    """自動ログイン Wallet がなく、秘密鍵が暗号化されていればパスワード必須。"""
    try:
        files = [path for path in wallet_path.iterdir() if path.is_file()]
    except OSError:
        return False
    names = {path.name.lower() for path in files}
    if "ewallet.p12" in names:
        return True
    encrypted_pem_exists = any(
        path.suffix.lower() == ".pem" and _pem_file_is_encrypted(path) for path in files
    )
    if encrypted_pem_exists:
        return True
    return "cwallet.sso" not in names


def _pem_file_is_encrypted(path: Path) -> bool:
    """暗号化 PEM の代表的な marker だけを少量読み取って判定する。"""
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    text = head.decode("utf-8", errors="ignore").upper()
    return "BEGIN ENCRYPTED PRIVATE KEY" in text or "PROC-TYPE: 4,ENCRYPTED" in text


def oracle_vector_schema_sql(table_name: str = "rag_chunks") -> str:
    """Oracle 26ai VECTOR(1536, FLOAT32) + HNSW index の DDL 例を返す。"""
    return f"""
CREATE TABLE {table_name} (
    chunk_id        VARCHAR2(128) PRIMARY KEY,
    document_id     VARCHAR2(64) NOT NULL,
    tenant_id_hash  CHAR(64),
    chunk_index     NUMBER NOT NULL,
    chunk_text      CLOB NOT NULL,
    metadata_json   JSON,
    embedding       VECTOR(1536, FLOAT32),
    created_at      TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE VECTOR INDEX {table_name}_embedding_hnsw_idx
    ON {table_name} (embedding)
    ORGANIZATION INMEMORY NEIGHBOR GRAPH
    DISTANCE COSINE
    WITH TARGET ACCURACY 95
    PARAMETERS (
        TYPE HNSW,
        NEIGHBORS 32,
        EFCONSTRUCTION 500
    );

CREATE INDEX {table_name}_text_idx
    ON {table_name} (chunk_text)
    INDEXTYPE IS CTXSYS.CONTEXT;

CREATE INDEX {table_name}_tenant_document_idx
    ON {table_name} (tenant_id_hash, document_id, chunk_index);
""".strip()


def oracle_document_schema_sql(table_name: str = "rag_documents") -> str:
    """Oracle document table の DDL 例を返す。"""
    return f"""
CREATE TABLE {table_name} (
    document_id              VARCHAR2(64) PRIMARY KEY,
    file_name                VARCHAR2(512) NOT NULL,
    status                   VARCHAR2(32) NOT NULL,
    tenant_id_hash           CHAR(64),
    category_name            VARCHAR2(256),
    object_storage_path      VARCHAR2(1024),
    content_type             VARCHAR2(255),
    file_size_bytes          NUMBER(19),
    content_sha256           CHAR(64),
    duplicate_of_document_id VARCHAR2(64),
    extraction               JSON,
    error_message            VARCHAR2(2000),
    uploaded_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    indexed_at               TIMESTAMP WITH TIME ZONE,
    CONSTRAINT {table_name}_status_ck
        CHECK (status IN ('UPLOADED', 'INGESTING', 'INDEXED', 'ERROR')),
    CONSTRAINT {table_name}_duplicate_fk
        FOREIGN KEY (duplicate_of_document_id) REFERENCES {table_name} (document_id)
);

CREATE INDEX {table_name}_content_sha256_idx
    ON {table_name} (content_sha256);

CREATE INDEX {table_name}_status_uploaded_idx
    ON {table_name} (status, uploaded_at DESC);

CREATE INDEX {table_name}_tenant_status_uploaded_idx
    ON {table_name} (tenant_id_hash, status, uploaded_at DESC);
""".strip()


def oracle_search_audit_schema_sql(table_name: str = "rag_search_audit") -> str:
    """RAG 検索監査 table の DDL 例を返す。"""
    return f"""
CREATE TABLE {table_name} (
    audit_id              VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    event_type            VARCHAR2(32) DEFAULT 'rag.search' NOT NULL,
    trace_id              VARCHAR2(64) NOT NULL,
    request_id            VARCHAR2(128),
    tenant_id_hash        CHAR(64),
    user_id_hash          CHAR(64),
    outcome               VARCHAR2(32) NOT NULL,
    mode                  VARCHAR2(16) NOT NULL,
    query_hash            CHAR(64) NOT NULL,
    query_chars           NUMBER(10) NOT NULL,
    filter_keys           JSON,
    top_k                 NUMBER(10),
    rerank_top_n          NUMBER(10),
    query_variant_count   NUMBER(10) DEFAULT 1 NOT NULL,
    guardrail_codes       JSON,
    guardrail_severities  JSON,
    retrieved_count       NUMBER(10) DEFAULT 0 NOT NULL,
    reranked_count        NUMBER(10) DEFAULT 0 NOT NULL,
    deduplicated_count    NUMBER(10) DEFAULT 0 NOT NULL,
    context_diversified_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_group_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compressed_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compression_saved_chars NUMBER(10) DEFAULT 0 NOT NULL,
    citation_count        NUMBER(10) DEFAULT 0 NOT NULL,
    context_chars         NUMBER(10) DEFAULT 0 NOT NULL,
    context_window_chars  NUMBER(10),
    document_ids          JSON,
    config_fingerprint    CHAR(64),
    elapsed_ms            NUMBER(12, 3) NOT NULL,
    error_stage           VARCHAR2(64),
    error_type            VARCHAR2(128),
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {table_name}_outcome_ck
        CHECK (outcome IN ('success', 'blocked', 'no_results', 'error')),
    CONSTRAINT {table_name}_mode_ck
        CHECK (mode IN ('hybrid', 'vector', 'keyword'))
);

CREATE INDEX {table_name}_trace_idx
    ON {table_name} (trace_id);

CREATE INDEX {table_name}_tenant_created_idx
    ON {table_name} (tenant_id_hash, created_at DESC);

CREATE INDEX {table_name}_created_outcome_idx
    ON {table_name} (created_at DESC, outcome);

CREATE INDEX {table_name}_query_hash_idx
    ON {table_name} (query_hash);

CREATE INDEX {table_name}_config_idx
    ON {table_name} (config_fingerprint);
""".strip()


def oracle_ingestion_audit_schema_sql(table_name: str = "rag_ingestion_audit") -> str:
    """RAG 取込監査 table の DDL 例を返す。"""
    return f"""
CREATE TABLE {table_name} (
    audit_id               VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    event_type             VARCHAR2(32) DEFAULT 'rag.ingestion' NOT NULL,
    trace_id               VARCHAR2(64) NOT NULL,
    request_id             VARCHAR2(128),
    tenant_id_hash         CHAR(64),
    user_id_hash           CHAR(64),
    document_id            VARCHAR2(64) NOT NULL,
    outcome                VARCHAR2(32) NOT NULL,
    source_sha256          CHAR(64) NOT NULL,
    source_bytes           NUMBER(19) NOT NULL,
    document_type          VARCHAR2(128),
    extraction_confidence  NUMBER(6, 5),
    chunk_count            NUMBER(10) DEFAULT 0 NOT NULL,
    vector_count           NUMBER(10) DEFAULT 0 NOT NULL,
    elapsed_ms             NUMBER(12, 3) NOT NULL,
    error_type             VARCHAR2(128),
    error_message          VARCHAR2(2000),
    created_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {table_name}_outcome_ck
        CHECK (outcome IN ('success', 'error'))
);

CREATE INDEX {table_name}_trace_idx
    ON {table_name} (trace_id);

CREATE INDEX {table_name}_tenant_created_idx
    ON {table_name} (tenant_id_hash, created_at DESC);

CREATE INDEX {table_name}_document_created_idx
    ON {table_name} (document_id, created_at DESC);

CREATE INDEX {table_name}_source_sha256_idx
    ON {table_name} (source_sha256);
""".strip()


def oracle_audit_schema_sql() -> str:
    """検索・取込監査 table の DDL 例をまとめて返す。"""
    return "\n\n".join(
        [
            oracle_search_audit_schema_sql(),
            oracle_ingestion_audit_schema_sql(),
        ]
    )


def _require_document(document_id: str) -> StoredDocument:
    document = _LOCAL_STORE.documents.get(document_id)
    if document is None or not _document_matches_current_tenant(document):
        raise KeyError(f"document_id={document_id} は存在しません。")
    return document


def _delete_document_chunks(document_id: str) -> None:
    """指定 document の chunk/index 行を削除する。"""
    for chunk_id in [
        chunk_id
        for chunk_id, stored in _LOCAL_STORE.chunks.items()
        if stored.document_id == document_id
    ]:
        del _LOCAL_STORE.chunks[chunk_id]


def _filtered_documents(
    status: FileStatus | None = None,
    query: str | None = None,
) -> list[StoredDocument]:
    """ローカル store から一覧条件に合う document を返す。"""
    normalized_query = query.casefold().strip() if query else None
    documents = sorted(
        (
            document
            for document in _LOCAL_STORE.documents.values()
            if _document_matches_current_tenant(document)
        ),
        key=lambda document: document.uploaded_at,
        reverse=True,
    )
    if status is not None:
        documents = [document for document in documents if document.status == status]
    if normalized_query:
        documents = [
            document
            for document in documents
            if normalized_query in document.file_name.casefold()
            or (
                document.category_name is not None
                and normalized_query in document.category_name.casefold()
            )
        ]
    return documents


def _chunk_matches_filters(chunk: StoredChunk, filters: dict[str, str] | None) -> bool:
    """検索 filter が chunk/document に一致するか判定する。"""
    document = _LOCAL_STORE.documents.get(chunk.document_id)
    if document is None:
        return False
    if not _document_matches_current_tenant(document):
        return False
    if document.status not in SEARCHABLE_FILE_STATUSES:
        return False
    if not filters:
        return True
    if (document_id := filters.get("document_id")) and document.id != document_id:
        return False
    if (status := filters.get("status")) and document.status.value != status:
        return False
    if (file_name := filters.get("file_name")) and (
        file_name.casefold() not in document.file_name.casefold()
    ):
        return False
    if category_name := filters.get("category_name"):
        if document.category_name is None:
            return False
        if category_name.casefold() not in document.category_name.casefold():
            return False
    if (content_kind := filters.get("content_kind")) and not _metadata_value_equals(
        chunk.metadata,
        "content_kind",
        content_kind,
    ):
        return False
    if (section_title := filters.get("section_title")) and not _metadata_value_contains(
        chunk.metadata,
        "section_title",
        section_title,
    ):
        return False
    if section_path := filters.get("section_path"):
        return _metadata_value_contains(chunk.metadata, "section_path", section_path)
    return True


def _metadata_value_equals(
    metadata: Mapping[str, MetadataValue],
    key: str,
    expected: str,
) -> bool:
    """chunk metadata の文字列値を case-insensitive に完全一致で見る。"""
    value = metadata.get(key)
    return isinstance(value, str) and value.casefold() == expected.casefold()


def _metadata_value_contains(
    metadata: Mapping[str, MetadataValue],
    key: str,
    expected: str,
) -> bool:
    """chunk metadata の文字列値を case-insensitive に部分一致で見る。"""
    value = metadata.get(key)
    return isinstance(value, str) and expected.casefold() in value.casefold()


def _current_tenant_id_hash() -> str | None:
    """現在の request context にある tenant hash を返す。"""
    return current_audit_request_context().tenant_id_hash


def _document_matches_current_tenant(document: StoredDocument) -> bool:
    """tenant と認可済み access scope に一致する document だけ許可する。"""
    context = current_audit_request_context()
    if context.tenant_id_hash is not None and document.tenant_id_hash != context.tenant_id_hash:
        return False
    if context.allowed_document_ids is not None and document.id not in context.allowed_document_ids:
        return False
    if context.allowed_category_names is not None:
        category_name = document.category_name.casefold() if document.category_name else ""
        if category_name not in context.allowed_category_names:
            return False
    return True


def _stored_chunk_score_sort_key(item: tuple[StoredChunk, float]) -> tuple[float, str, int, str]:
    """score 降順、document/chunk 昇順の安定した検索順を返す。"""
    chunk, score = item
    return (-score, chunk.document_id, chunk.chunk_index, chunk.id)


def _retrieved_chunk_score_sort_key(
    chunk: RetrievedChunk,
    score: float,
) -> tuple[float, str, int, str]:
    """RRF score 降順、document/chunk 昇順の安定した検索順を返す。"""
    chunk_index = chunk.metadata.get("chunk_index")
    stable_index = chunk_index if isinstance(chunk_index, int) else 0
    return (-score, chunk.document_id, stable_index, chunk.chunk_id)


def _with_retrieval_metadata(
    chunk: RetrievedChunk,
    **metadata: MetadataValue,
) -> RetrievedChunk:
    """検索スコアの説明用 metadata を既存 metadata に追加する。"""
    return chunk.model_copy(update={"metadata": {**chunk.metadata, **metadata}})


def _chunk_index_from_retrieved(chunk: RetrievedChunk) -> int | None:
    """RetrievedChunk metadata から安全に chunk_index を読む。"""
    value = chunk.metadata.get("chunk_index")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned and cleaned.lstrip("-").isdigit():
            return int(cleaned)
    return None


def _chunk_group_id_from_retrieved(chunk: RetrievedChunk) -> str | None:
    """RetrievedChunk metadata から chunk_group_id を読む。"""
    return _chunk_group_id_from_metadata(chunk.metadata)


def _chunk_group_id_from_metadata(metadata: Mapping[str, MetadataValue]) -> str | None:
    """metadata の chunk_group_id を空白除去済み文字列として読む。"""
    value = metadata.get("chunk_group_id")
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _context_neighbor_offsets(window: int) -> list[int]:
    """近い順に前後 offset を返す。"""
    return sorted(
        (offset for offset in range(-window, window + 1) if offset != 0),
        key=lambda offset: (abs(offset), offset),
    )


def _with_context_neighbor_metadata(
    chunk: RetrievedChunk,
    *,
    anchor: RetrievedChunk,
    distance: int,
) -> RetrievedChunk:
    """隣接 context と anchor の対応を citation metadata に残す。"""
    return chunk.model_copy(
        update={
            "metadata": {
                **chunk.metadata,
                "context_expanded": True,
                "context_anchor_chunk_id": anchor.chunk_id,
                "context_neighbor_distance": distance,
            }
        }
    )


def _with_context_group_metadata(
    chunk: RetrievedChunk,
    *,
    anchor: RetrievedChunk,
    group_id: str,
    distance: int,
) -> RetrievedChunk:
    """同一 group context と anchor の対応を citation metadata に残す。"""
    return chunk.model_copy(
        update={
            "metadata": {
                **chunk.metadata,
                "context_group_expanded": True,
                "context_anchor_chunk_id": anchor.chunk_id,
                "context_group_id": group_id,
                "context_group_distance": distance,
            }
        }
    )


def _hybrid_retrieval_mode(metadata: dict[str, MetadataValue]) -> str:
    """hybrid 検索結果がどの検索経路から来たかを返す。"""
    has_vector = "vector_rank" in metadata
    has_keyword = "keyword_rank" in metadata
    if has_vector and has_keyword:
        return "hybrid"
    if has_vector:
        return "vector"
    return "keyword"


def _to_document_summary(document: StoredDocument) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        file_name=document.file_name,
        status=document.status,
        category_name=document.category_name,
        content_type=document.content_type,
        file_size_bytes=document.file_size_bytes,
        content_sha256=document.content_sha256,
        duplicate_of_document_id=document.duplicate_of_document_id,
        uploaded_at=document.uploaded_at,
        indexed_at=document.indexed_at,
    )


def _to_document_detail(document: StoredDocument) -> DocumentDetail:
    return DocumentDetail(
        id=document.id,
        file_name=document.file_name,
        status=document.status,
        category_name=document.category_name,
        content_type=document.content_type,
        file_size_bytes=document.file_size_bytes,
        content_sha256=document.content_sha256,
        duplicate_of_document_id=document.duplicate_of_document_id,
        uploaded_at=document.uploaded_at,
        indexed_at=document.indexed_at,
        object_storage_path=document.object_storage_path,
        extraction=document.extraction,
        error_message=document.error_message,
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, dot / (left_norm * right_norm))


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def _keyword_score(query_tokens: list[str], document_tokens: list[str]) -> float:
    if not query_tokens or not document_tokens:
        return 0.0
    query_set = set(query_tokens)
    document_set = set(document_tokens)
    matches = len(query_set & document_set)
    return matches / len(query_set)


def _rrf(rank: int, constant: int = 60) -> float:
    return 1.0 / (constant + rank)
