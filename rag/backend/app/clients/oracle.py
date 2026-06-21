"""Oracle 26ai クライアント。

AI Vector Search によるベクトル検索（VECTOR(1536, FLOAT32)）と
Oracle Text による keyword retrieval を担う。外部ベクトル DB は使わない。
"""

import asyncio
import hashlib
import importlib
import json
import math
import re
from array import array
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast
from uuid import uuid4

from app.config import Settings, get_settings
from app.rag.business_view_config import (
    BusinessViewConfig,
    dump_business_view_config,
    parse_business_view_config,
)
from app.rag.chunking import Chunk
from app.rag.graph_index import (
    GraphClaim,
    GraphCommunitySummary,
    GraphEntity,
    GraphEntityChunkLink,
    GraphIndex,
    GraphRelationship,
)
from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig, parse_adapter_config
from app.rag.request_context import current_audit_request_context
from app.rag.source_profile import build_source_profile
from app.rag.vector_index_adapter import resolve_vector_index_adapter
from app.schemas.business_view import (
    BusinessViewDetail,
    BusinessViewStatus,
    BusinessViewSummary,
)
from app.schemas.common import JsonValue
from app.schemas.document import (
    DocumentChunkView,
    DocumentDetail,
    DocumentStats,
    DocumentSummary,
    FileStatus,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
    IngestionSegment,
)
from app.schemas.extraction import StructuredExtraction
from app.schemas.knowledge_base import (
    KnowledgeBaseDetail,
    KnowledgeBaseRef,
    KnowledgeBaseStatus,
    KnowledgeBaseSummary,
)
from app.schemas.search import RetrievedChunk, SearchMode, SelectAiAction

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[ぁ-んァ-ン一-龯々ー]+", re.IGNORECASE)
SEARCHABLE_FILE_STATUSES = {FileStatus.INDEXED}
type MetadataValue = JsonValue
type DbCallRunner = Callable[[Callable[[], Any]], Awaitable[Any]]
T = TypeVar("T")
DocumentT = TypeVar("DocumentT", bound=DocumentSummary)
SELECT_AI_UNAVAILABLE_ERROR = "Select AI は ORACLE_SELECT_AI_PROFILE の設定が必要です。"
DEFAULT_KNOWLEDGE_BASE_NAME = "既定ナレッジベース"


def _to_vector_bind(embedding: Sequence[float]) -> "array[float]":
    """embedding を Oracle VECTOR(FLOAT32) へバインド可能な float32 配列に変換する。

    python-oracledb は VECTOR 列に list を渡すと配列バインドと誤認するため、
    array('f', ...) として渡す必要がある。
    """
    return array("f", (float(value) for value in embedding))


WALLET_PASSWORD_REQUIRED_ERROR = (  # nosec B105 - パスワードではなくエラーメッセージ定数
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
    metadata: dict[str, MetadataValue] = field(default_factory=dict)


@dataclass
class StoredKnowledgeBase:
    """ナレッジベース行。"""

    id: str
    name: str
    status: KnowledgeBaseStatus
    created_at: datetime
    updated_at: datetime
    tenant_id_hash: str | None = None
    description: str | None = None
    default_search_mode: SearchMode = SearchMode.HYBRID
    retrieval_config: dict[str, object] = field(default_factory=dict)
    archived_at: datetime | None = None
    document_count: int = 0
    indexed_document_count: int = 0
    error_document_count: int = 0
    searchable_chunk_count: int = 0


@dataclass
class StoredBusinessView:
    """業務アシスタント(Business View)行。"""

    id: str
    name: str
    status: BusinessViewStatus
    created_at: datetime
    updated_at: datetime
    tenant_id_hash: str | None = None
    description: str | None = None
    view_config: dict[str, object] = field(default_factory=dict)
    archived_at: datetime | None = None


@dataclass
class StoredAgentMemory:
    """Agent Memory 行。raw user/thread id ではなく hash scope だけを保持する。"""

    memory_id: str
    tenant_id_hash: str | None
    user_id_hash: str | None
    role_id_hash: str | None
    agent_id_hash: str | None
    thread_id_hash: str | None
    trace_id: str
    memory_text: str
    embedding: list[float]
    metadata: dict[str, object] = field(default_factory=dict)
    usefulness_score: float = 0.5
    eval_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class LocalOracleStore:
    """Oracle row 変換などの単体テストで使う補助ストア。"""

    documents: dict[str, StoredDocument] = field(default_factory=dict)
    chunks: dict[str, StoredChunk] = field(default_factory=dict)
    knowledge_bases: dict[str, StoredKnowledgeBase] = field(default_factory=dict)
    document_knowledge_bases: set[tuple[str, str]] = field(default_factory=set)
    ingestion_jobs: dict[str, IngestionJob] = field(default_factory=dict)
    ingestion_segments: dict[str, IngestionSegment] = field(default_factory=dict)
    agent_memories: dict[str, StoredAgentMemory] = field(default_factory=dict)


_LOCAL_STORE = LocalOracleStore()
_SHARED_ORACLE_POOL: OraclePoolProtocol | None = None
_DB_TEST_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="oracle_db_test_")


class SelectAiUnavailableError(RuntimeError):
    """Select AI を実行できる Oracle 設定がない。"""

    safe_for_user = True


class DocumentDeleteBlockedByRunningIngestionError(RuntimeError):
    """実行中 ingestion job があるため document 削除を止めた。"""

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

    async def graph_local_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        """軽量 KG の entity/claim/chunk link から local graph 根拠を取得する。"""
        return await self._graph_local_search_with_oracle(query, top_k, filters or {})

    async def graph_global_search(
        self,
        query: str,
        top_k: int,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        """軽量 KG の community summary から横断・全体質問向け根拠を取得する。"""
        return await self._graph_global_search_with_oracle(query, top_k, filters or {})

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

    async def context_dependency_chunks(
        self,
        anchors: list[RetrievedChunk],
        *,
        max_chunks_per_anchor: int,
    ) -> list[RetrievedChunk]:
        """rerank anchor と dependency metadata を共有する候補 chunk を取得する。"""
        if max_chunks_per_anchor <= 0 or not anchors:
            return []
        return await self._context_dependency_chunks_with_oracle(
            anchors,
            max_chunks_per_anchor=max_chunks_per_anchor,
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
        knowledge_base_ids: Sequence[str] | None = None,
    ) -> DocumentDetail:
        """ドキュメント行を作成する。"""
        return await self._create_document_with_oracle(
            file_name=file_name,
            object_storage_path=object_storage_path,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            content_sha256=content_sha256,
            duplicate_of_document_id=duplicate_of_document_id,
            knowledge_base_ids=knowledge_base_ids,
        )

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str | None = None,
        default_search_mode: SearchMode = SearchMode.HYBRID,
        retrieval_config: Mapping[str, object] | None = None,
    ) -> KnowledgeBaseDetail:
        """ナレッジベースを作成する。"""
        return await self._create_knowledge_base_with_oracle(
            name=name,
            description=description,
            default_search_mode=default_search_mode,
            retrieval_config=dict(retrieval_config or {}),
        )

    async def ensure_default_knowledge_base(
        self,
        *,
        name: str = DEFAULT_KNOWLEDGE_BASE_NAME,
    ) -> KnowledgeBaseDetail:
        """tenant ごとの既定ナレッジベースを取得または作成する。"""
        existing = await self._find_knowledge_base_by_name_with_oracle(name)
        if existing is not None:
            return existing
        return await self.create_knowledge_base(name=name)

    async def list_knowledge_bases(
        self,
        *,
        status: KnowledgeBaseStatus | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[KnowledgeBaseSummary]:
        """ナレッジベース一覧を返す。"""
        return await self._list_knowledge_bases_with_oracle(
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )

    async def count_knowledge_bases(
        self,
        *,
        status: KnowledgeBaseStatus | None = None,
        query: str | None = None,
    ) -> int:
        """条件に一致するナレッジベース数を返す。"""
        return await self._count_knowledge_bases_with_oracle(status=status, query=query)

    async def get_knowledge_base(
        self,
        knowledge_base_id: str,
    ) -> KnowledgeBaseDetail | None:
        """ナレッジベース詳細を返す。"""
        return await self._get_knowledge_base_with_oracle(knowledge_base_id)

    async def update_knowledge_base(
        self,
        knowledge_base_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        default_search_mode: SearchMode | None = None,
        retrieval_config: Mapping[str, object] | None = None,
        update_fields: set[str] | None = None,
    ) -> KnowledgeBaseDetail:
        """ナレッジベースの基本情報を更新する。"""
        return await self._update_knowledge_base_with_oracle(
            knowledge_base_id=knowledge_base_id,
            name=name,
            description=description,
            default_search_mode=default_search_mode,
            retrieval_config=dict(retrieval_config) if retrieval_config is not None else None,
            update_fields=update_fields,
        )

    async def archive_knowledge_base(self, knowledge_base_id: str) -> KnowledgeBaseDetail:
        """ナレッジベースをアーカイブする。文書・chunk は削除しない。"""
        return await self._archive_knowledge_base_with_oracle(knowledge_base_id)

    async def assign_documents_to_knowledge_base(
        self,
        knowledge_base_id: str,
        document_ids: Sequence[str],
    ) -> KnowledgeBaseDetail:
        """既存文書をナレッジベースへ追加する。"""
        return await self._assign_documents_to_knowledge_base_with_oracle(
            knowledge_base_id,
            document_ids,
        )

    async def remove_document_from_knowledge_base(
        self,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeBaseDetail:
        """文書をナレッジベースから外す。文書自体は削除しない。"""
        return await self._remove_document_from_knowledge_base_with_oracle(
            knowledge_base_id,
            document_id,
        )

    async def replace_document_knowledge_bases(
        self,
        document_id: str,
        knowledge_base_ids: Sequence[str],
    ) -> list[KnowledgeBaseRef]:
        """文書の所属ナレッジベースを指定リストへ置換する。"""
        return await self._replace_document_knowledge_bases_with_oracle(
            document_id,
            knowledge_base_ids,
        )

    async def list_document_knowledge_bases(self, document_id: str) -> list[KnowledgeBaseRef]:
        """文書の所属ナレッジベース一覧を返す。"""
        return await self._list_document_knowledge_bases_with_oracle(document_id)

    async def get_owning_knowledge_base(self, document_id: str) -> KnowledgeBaseDetail | None:
        """取込設定の基準となる owning KB(最古割当)を返す。所属無しなら None。

        文書-KB は多対多だが、取込時の Parser/Chunking 上書きを決定論的にするため、
        最も早く割り当てられた KB を owning KB とする(同時刻は knowledge_base_id 昇順)。
        """
        owning_id = await self._get_owning_knowledge_base_id_with_oracle(document_id)
        if owning_id is None:
            return None
        return await self.get_knowledge_base(owning_id)

    # ------------------------------------------------------------------
    # variant materialization: chunk_set / KB binding 永続層
    # dedup/refcount/GC の計算は app.rag.variant_planner(決定論)が担い、
    # 本メソッド群はその計画を Oracle へ反映する手。refcount は binding 件数から導出する。
    # ------------------------------------------------------------------

    async def upsert_document_extraction(
        self,
        *,
        extraction_id: str,
        document_id: str,
        extraction: StructuredExtraction,
        recipe_subset: Mapping[str, object] | None = None,
        quality: Mapping[str, object] | None = None,
        status: str = "EXTRACTED",
    ) -> None:
        """抽出(extraction 層)を冪等に作成/更新する。preprocess×parser ごとに 1 行。"""
        tenant = current_audit_request_context().tenant_id_hash
        binds = {
            "extraction_id": extraction_id,
            "document_id": document_id,
            "tenant_id_hash": tenant,
            "recipe_subset": _json_dumps(recipe_subset) if recipe_subset is not None else None,
            "extraction_json": _json_dumps(extraction.to_document_payload()),
            "quality_json": _json_dumps(quality) if quality is not None else None,
            "status": status,
        }

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                MERGE INTO rag_document_extractions t
                USING (SELECT :extraction_id AS extraction_id FROM dual) s
                ON (t.extraction_id = s.extraction_id)
                WHEN MATCHED THEN UPDATE SET
                    t.extraction_json = :extraction_json,
                    t.recipe_subset = :recipe_subset,
                    t.quality_json = :quality_json,
                    t.status = :status,
                    t.updated_at = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (extraction_id, document_id, tenant_id_hash, recipe_subset,
                     extraction_json, quality_json, status)
                    VALUES (:extraction_id, :document_id, :tenant_id_hash, :recipe_subset,
                            :extraction_json, :quality_json, :status)
                """,
                binds,
            )

        await self._run_transaction(operation)

    async def get_document_extraction(self, extraction_id: str) -> dict[str, object] | None:
        """抽出 1 件(status / recipe_subset / extraction payload)を返す。無ければ None。"""
        row = await self._fetch_one(
            """
            SELECT extraction_id, document_id, status, recipe_subset, extraction_json
            FROM rag_document_extractions WHERE extraction_id = :extraction_id
            """,
            {"extraction_id": extraction_id},
        )
        if row is None:
            return None
        return {str(key).lower(): value for key, value in row.items()}

    async def list_document_extraction_ids(self, document_id: str) -> list[str]:
        """文書が持つ extraction_id 一覧(diff/GC 入力)。"""
        rows = await self._fetch_all(
            "SELECT extraction_id FROM rag_document_extractions WHERE document_id = :document_id",
            {"document_id": document_id},
        )
        return [str(next(iter(row.values()))) for row in rows]

    async def mark_document_extraction(self, *, extraction_id: str, status: str) -> None:
        """抽出の status(EXTRACTING/EXTRACTED/ERROR)を更新する。"""
        binds = {"extraction_id": extraction_id, "status": status}

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                UPDATE rag_document_extractions
                SET status = :status, updated_at = SYSTIMESTAMP
                WHERE extraction_id = :extraction_id
                """,
                binds,
            )

        await self._run_transaction(operation)

    async def delete_document_extractions_except(
        self, *, document_id: str, keep_extraction_ids: Sequence[str]
    ) -> list[str]:
        """plan に無い extraction を削除する(GC)。keep が空なら何もしない(安全側)。

        chunk_set GC を先に走らせた後に呼ぶ前提(参照されない抽出だけ残る)。削除した id を返す。
        """
        keep = list(dict.fromkeys(keep_extraction_ids))
        if not keep:
            return []
        keep_in_sql, keep_binds = _oracle_in_predicate("extraction_id", "keep_ex", keep)
        binds: dict[str, object] = {"document_id": document_id, **keep_binds}

        def operation(connection: OracleConnectionProtocol) -> list[str]:
            rows = _fetch_all(
                connection,
                _render_sql(
                    """
                    SELECT extraction_id FROM rag_document_extractions
                    WHERE document_id = :document_id AND NOT ({keep_in_sql})
                    """,
                    keep_in_sql=keep_in_sql,
                ),
                binds,
            )
            removed = [str(next(iter(row.values()))) for row in rows]
            _execute(
                connection,
                _render_sql(
                    """
                    DELETE FROM rag_document_extractions
                    WHERE document_id = :document_id AND NOT ({keep_in_sql})
                    """,
                    keep_in_sql=keep_in_sql,
                ),
                binds,
            )
            return removed

        return await self._run_transaction(operation)

    async def upsert_chunk_set(
        self,
        *,
        chunk_set_id: str,
        document_id: str,
        recipe_subset: Mapping[str, object] | None = None,
        status: str = "INGESTING",
    ) -> None:
        """chunk_set(chunk text/embedding 層)を冪等に作成する。既存なら updated_at のみ更新。"""
        tenant = current_audit_request_context().tenant_id_hash
        binds = {
            "chunk_set_id": chunk_set_id,
            "document_id": document_id,
            "tenant_id_hash": tenant,
            "recipe_subset": _json_dumps(recipe_subset) if recipe_subset is not None else None,
            "status": status,
        }

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                MERGE INTO rag_chunk_sets t
                USING (SELECT :chunk_set_id AS chunk_set_id FROM dual) s
                ON (t.chunk_set_id = s.chunk_set_id)
                WHEN MATCHED THEN UPDATE SET t.updated_at = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (chunk_set_id, document_id, tenant_id_hash, recipe_subset, status)
                    VALUES (:chunk_set_id, :document_id, :tenant_id_hash, :recipe_subset, :status)
                """,
                binds,
            )

        await self._run_transaction(operation)

    async def mark_chunk_set_indexed(
        self,
        *,
        chunk_set_id: str,
        chunk_count: int,
        vector_count: int,
        metrics: Mapping[str, object] | None = None,
    ) -> None:
        """chunk_set を INDEXED にし、件数/metrics を記録する。"""
        binds = {
            "chunk_set_id": chunk_set_id,
            "chunk_count": chunk_count,
            "vector_count": vector_count,
            "metrics_json": _json_dumps(metrics) if metrics is not None else None,
        }

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                UPDATE rag_chunk_sets
                SET status = 'INDEXED',
                    chunk_count = :chunk_count,
                    vector_count = :vector_count,
                    metrics_json = :metrics_json,
                    updated_at = SYSTIMESTAMP
                WHERE chunk_set_id = :chunk_set_id
                """,
                binds,
            )

        await self._run_transaction(operation)

    async def get_chunk_set(self, chunk_set_id: str) -> dict[str, object] | None:
        """chunk_set の状態(status/件数)を返す。キーは小文字へ正規化。"""
        row = await self._fetch_one(
            """
            SELECT chunk_set_id, document_id, status, chunk_count, vector_count
            FROM rag_chunk_sets WHERE chunk_set_id = :chunk_set_id
            """,
            {"chunk_set_id": chunk_set_id},
        )
        if row is None:
            return None
        return {str(key).lower(): value for key, value in row.items()}

    async def list_document_knowledge_base_configs(
        self, document_id: str
    ) -> list[tuple[str, KnowledgeBaseAdapterConfig]]:
        """文書の所属 KB id と各 adapter_config を返す(variant_planner の plan 入力)。"""
        rows = await self._fetch_all(
            """
            SELECT
                kb.knowledge_base_id,
                kb.retrieval_config
            FROM rag_document_knowledge_bases dkb
            JOIN rag_knowledge_bases kb
                ON kb.knowledge_base_id = dkb.knowledge_base_id
            JOIN rag_documents d
                ON d.document_id = dkb.document_id
            WHERE dkb.document_id = :document_id
              AND {document_access_sql}
              AND {knowledge_base_access_sql}
            ORDER BY kb.knowledge_base_id ASC
            """.format(
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
            ),
            _with_tenant_bind({"document_id": document_id}),
        )
        return [
            (
                str(row["knowledge_base_id"]),
                parse_adapter_config(_json_loads(row.get("retrieval_config"))),
            )
            for row in rows
        ]

    async def list_document_chunk_set_ids(self, document_id: str) -> list[str]:
        """文書が持つ chunk_set id 一覧(planner の既存状態 = diff_plan 入力)。"""
        rows = await self._fetch_all(
            "SELECT chunk_set_id FROM rag_chunk_sets WHERE document_id = :document_id",
            {"document_id": document_id},
        )
        return [str(next(iter(row.values()))) for row in rows]

    async def list_document_chunk_sets(self, document_id: str) -> list[dict[str, object]]:
        """文書の chunk_set 一覧(状態/件数/所属・配信 KB)を返す。variant 可視化に使う。

        rag_chunk_sets と rag_kb_chunk_set_bindings を join し、chunk_set ごとに集約する。
        created_at 昇順で安定。binding が無い chunk_set も(KB 未割当でも)返す。
        """
        rows = await self._fetch_all(
            """
            SELECT cs.chunk_set_id AS chunk_set_id,
                   cs.status AS status,
                   cs.chunk_count AS chunk_count,
                   cs.vector_count AS vector_count,
                   b.knowledge_base_id AS knowledge_base_id,
                   b.is_serving AS is_serving
            FROM rag_chunk_sets cs
            LEFT JOIN rag_kb_chunk_set_bindings b ON b.chunk_set_id = cs.chunk_set_id
            WHERE cs.document_id = :document_id
            ORDER BY cs.created_at, cs.chunk_set_id, b.knowledge_base_id
            """,
            {"document_id": document_id},
        )
        by_id: dict[str, dict[str, object]] = {}
        members: dict[str, list[str]] = {}
        serving: dict[str, list[str]] = {}
        order: list[str] = []
        for row in rows:
            norm = {str(key).lower(): value for key, value in row.items()}
            chunk_set_id = str(norm["chunk_set_id"])
            if chunk_set_id not in by_id:
                by_id[chunk_set_id] = {
                    "chunk_set_id": chunk_set_id,
                    "status": str(norm["status"]),
                    "chunk_count": int(str(norm["chunk_count"] or 0)),
                    "vector_count": int(str(norm["vector_count"] or 0)),
                }
                members[chunk_set_id] = []
                serving[chunk_set_id] = []
                order.append(chunk_set_id)
            kb_value = norm.get("knowledge_base_id")
            if kb_value is None:
                continue
            kb_id = str(kb_value)
            if kb_id not in members[chunk_set_id]:
                members[chunk_set_id].append(kb_id)
            if int(str(norm.get("is_serving") or 0)) == 1 and kb_id not in serving[chunk_set_id]:
                serving[chunk_set_id].append(kb_id)
        result: list[dict[str, object]] = []
        for chunk_set_id in order:
            entry = by_id[chunk_set_id]
            entry["knowledge_base_ids"] = members[chunk_set_id]
            entry["serving_knowledge_base_ids"] = serving[chunk_set_id]
            result.append(entry)
        return result

    async def upsert_chunk_set_binding(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
        chunk_set_id: str,
        is_serving: bool = True,
    ) -> None:
        """KB→chunk_set の参照(refcount の実体)を冪等に作成/更新する。"""
        tenant = current_audit_request_context().tenant_id_hash
        binds = {
            "knowledge_base_id": knowledge_base_id,
            "document_id": document_id,
            "chunk_set_id": chunk_set_id,
            "tenant_id_hash": tenant,
            "is_serving": 1 if is_serving else 0,
        }

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                MERGE INTO rag_kb_chunk_set_bindings t
                USING (
                    SELECT :knowledge_base_id AS knowledge_base_id,
                           :document_id AS document_id,
                           :chunk_set_id AS chunk_set_id
                    FROM dual
                ) s
                ON (t.knowledge_base_id = s.knowledge_base_id
                    AND t.document_id = s.document_id
                    AND t.chunk_set_id = s.chunk_set_id)
                WHEN MATCHED THEN UPDATE SET t.is_serving = :is_serving
                WHEN NOT MATCHED THEN INSERT
                    (knowledge_base_id, document_id, chunk_set_id, tenant_id_hash, is_serving)
                    VALUES
                    (:knowledge_base_id, :document_id, :chunk_set_id, :tenant_id_hash, :is_serving)
                """,
                binds,
            )

        await self._run_transaction(operation)

    async def delete_chunk_set_binding(
        self,
        *,
        knowledge_base_id: str,
        document_id: str,
        chunk_set_id: str,
    ) -> None:
        """KB→chunk_set の参照を削除する(refcount を減らす)。chunk_set 自体は消さない。"""
        binds = {
            "knowledge_base_id": knowledge_base_id,
            "document_id": document_id,
            "chunk_set_id": chunk_set_id,
        }

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                DELETE FROM rag_kb_chunk_set_bindings
                WHERE knowledge_base_id = :knowledge_base_id
                  AND document_id = :document_id
                  AND chunk_set_id = :chunk_set_id
                """,
                binds,
            )

        await self._run_transaction(operation)

    async def chunk_set_refcount(self, chunk_set_id: str) -> int:
        """chunk_set を参照する KB binding 数(= refcount)。"""
        row = await self._fetch_one(
            """
            SELECT COUNT(*) AS cnt
            FROM rag_kb_chunk_set_bindings
            WHERE chunk_set_id = :chunk_set_id
            """,
            {"chunk_set_id": chunk_set_id},
        )
        return int(str(next(iter(row.values())))) if row else 0

    async def count_chunk_set_chunks(self, chunk_set_id: str) -> int:
        """指定 chunk_set の chunk 行数(chunk_set 単位の件数記録用)。"""
        row = await self._fetch_one(
            "SELECT COUNT(*) AS cnt FROM rag_chunks WHERE chunk_set_id = :chunk_set_id",
            {"chunk_set_id": chunk_set_id},
        )
        return int(str(next(iter(row.values())))) if row else 0

    async def delete_document_chunk_sets_except(
        self, *, document_id: str, keep_chunk_set_ids: Sequence[str]
    ) -> list[str]:
        """plan に無い chunk_set(とその chunk、未タグ chunk)を削除する。keep だけ残す。

        複数 materialization の cleanup。keep が空なら何もしない(安全側・現行 chunk は保持)。
        binding は FK cascade。削除した chunk_set id を返す。
        """
        keep = list(dict.fromkeys(keep_chunk_set_ids))
        if not keep:
            return []
        keep_in_sql, keep_binds = _oracle_in_predicate("chunk_set_id", "keep_cs", keep)
        binds: dict[str, object] = {"document_id": document_id, **keep_binds}

        def operation(connection: OracleConnectionProtocol) -> list[str]:
            rows = _fetch_all(
                connection,
                _render_sql(
                    """
                    SELECT chunk_set_id FROM rag_chunk_sets
                    WHERE document_id = :document_id AND NOT ({keep_in_sql})
                    """,
                    keep_in_sql=keep_in_sql,
                ),
                binds,
            )
            removed = [str(next(iter(row.values()))) for row in rows]
            _execute(
                connection,
                _render_sql(
                    """
                    DELETE FROM rag_chunks
                    WHERE document_id = :document_id
                      AND (NOT ({keep_in_sql}) OR chunk_set_id IS NULL)
                    """,
                    keep_in_sql=keep_in_sql,
                ),
                binds,
            )
            _execute(
                connection,
                _render_sql(
                    """
                    DELETE FROM rag_chunk_sets
                    WHERE document_id = :document_id AND NOT ({keep_in_sql})
                    """,
                    keep_in_sql=keep_in_sql,
                ),
                binds,
            )
            return removed

        return await self._run_transaction(operation)

    async def collect_unreferenced_chunk_sets(self, document_id: str) -> list[str]:
        """文書の chunk_set のうち refcount 0 のものを GC する。削除した chunk_set id を返す。

        他 KB が参照中(binding 存在)の chunk_set は削除しない。削除直前に DB で refcount 0 を
        再確認(NOT EXISTS)してから chunk と chunk_set を消すため、早すぎる削除を防ぐ。
        """

        def operation(connection: OracleConnectionProtocol) -> list[str]:
            rows = _fetch_all(
                connection,
                """
                SELECT cs.chunk_set_id
                FROM rag_chunk_sets cs
                WHERE cs.document_id = :document_id
                  AND NOT EXISTS (
                      SELECT 1 FROM rag_kb_chunk_set_bindings b
                      WHERE b.chunk_set_id = cs.chunk_set_id
                  )
                """,
                {"document_id": document_id},
            )
            collected = [str(next(iter(row.values()))) for row in rows]
            for chunk_set_id in collected:
                _execute(
                    connection,
                    "DELETE FROM rag_chunks WHERE chunk_set_id = :chunk_set_id",
                    {"chunk_set_id": chunk_set_id},
                )
                _execute(
                    connection,
                    "DELETE FROM rag_chunk_sets WHERE chunk_set_id = :chunk_set_id",
                    {"chunk_set_id": chunk_set_id},
                )
            return collected

        return await self._run_transaction(operation)

    async def tag_document_chunks_with_chunk_set(
        self, *, document_id: str, chunk_set_id: str
    ) -> None:
        """文書の全 chunk を指定 chunk_set に紐付ける(取込後のタグ付け)。"""

        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                UPDATE rag_chunks SET chunk_set_id = :chunk_set_id
                WHERE document_id = :document_id
                """,
                {"chunk_set_id": chunk_set_id, "document_id": document_id},
            )

        await self._run_transaction(operation)

    async def delete_stale_document_chunk_sets(
        self, *, document_id: str, keep_chunk_set_id: str
    ) -> list[str]:
        """文書の keep 以外の chunk_set と、その chunk(+未タグ chunk)を削除する。

        取込設定変更時の旧 chunk_set GC。keep の chunk だけを残し、別 chunk_set の chunk・
        未タグ(NULL)chunk・別 chunk_set 行を削除する(binding は FK cascade)。削除した
        chunk_set id を返す。
        """
        binds = {"document_id": document_id, "keep_chunk_set_id": keep_chunk_set_id}

        def operation(connection: OracleConnectionProtocol) -> list[str]:
            rows = _fetch_all(
                connection,
                """
                SELECT chunk_set_id FROM rag_chunk_sets
                WHERE document_id = :document_id AND chunk_set_id <> :keep_chunk_set_id
                """,
                binds,
            )
            removed = [str(next(iter(row.values()))) for row in rows]
            # keep 以外の chunk と未タグ(NULL)chunk を削除する(挿入時タグの一貫性を保つ)。
            _execute(
                connection,
                """
                DELETE FROM rag_chunks
                WHERE document_id = :document_id
                  AND (chunk_set_id <> :keep_chunk_set_id OR chunk_set_id IS NULL)
                """,
                binds,
            )
            _execute(
                connection,
                """
                DELETE FROM rag_chunk_sets
                WHERE document_id = :document_id AND chunk_set_id <> :keep_chunk_set_id
                """,
                binds,
            )
            return removed

        return await self._run_transaction(operation)

    async def create_business_view(
        self,
        *,
        name: str,
        description: str | None = None,
        config: BusinessViewConfig | None = None,
    ) -> BusinessViewDetail:
        """業務アシスタントを作成する。"""
        return await self._create_business_view_with_oracle(
            name=name,
            description=description,
            config=config or BusinessViewConfig(),
        )

    async def list_business_views(
        self,
        *,
        status: BusinessViewStatus | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[BusinessViewSummary]:
        """業務アシスタント一覧を返す。"""
        return await self._list_business_views_with_oracle(
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )

    async def count_business_views(
        self,
        *,
        status: BusinessViewStatus | None = None,
        query: str | None = None,
    ) -> int:
        """条件に一致する業務アシスタント数を返す。"""
        return await self._count_business_views_with_oracle(status=status, query=query)

    async def get_business_view(
        self,
        business_view_id: str,
    ) -> BusinessViewDetail | None:
        """業務アシスタント詳細を返す。参照 KB の名前も解決して埋める。"""
        view = await self._get_business_view_with_oracle(business_view_id)
        if view is None:
            return None
        refs = await self._resolve_knowledge_base_refs(view.config.normalized_knowledge_base_ids())
        return view.model_copy(update={"knowledge_bases": refs})

    async def update_business_view(
        self,
        business_view_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        config: BusinessViewConfig | None = None,
        update_fields: set[str] | None = None,
    ) -> BusinessViewDetail:
        """業務アシスタントを更新する。"""
        return await self._update_business_view_with_oracle(
            business_view_id=business_view_id,
            name=name,
            description=description,
            config=config,
            update_fields=update_fields,
        )

    async def archive_business_view(self, business_view_id: str) -> BusinessViewDetail:
        """業務アシスタントをアーカイブする。参照 KB・文書は変更しない。"""
        return await self._archive_business_view_with_oracle(business_view_id)

    async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        """取込 job を永続化する。"""
        return await self._create_ingestion_job_with_oracle(job)

    async def get_ingestion_job(self, job_id: str) -> IngestionJob | None:
        """取込 job の現在状態を返す。"""
        return await self._get_ingestion_job_with_oracle(job_id)

    async def list_ingestion_jobs(
        self,
        *,
        status: IngestionJobStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
        oldest_first: bool = False,
    ) -> list[IngestionJob]:
        """取込 job 一覧を返す。

        既定は新しい順(UI 用)。``oldest_first=True`` でキュー消費向けに
        古い順(FIFO)で返し、ワーカーが滞留 job を starvation させないようにする。
        """
        return await self._list_ingestion_jobs_with_oracle(
            status=status,
            limit=limit,
            offset=offset,
            oldest_first=oldest_first,
        )

    async def list_document_ingestion_jobs(
        self,
        document_id: str,
        *,
        status: IngestionJobStatus | None = None,
    ) -> list[IngestionJob]:
        """指定 document の取込 job 一覧を返す。"""
        return await self._list_document_ingestion_jobs_with_oracle(
            document_id,
            status=status,
        )

    async def replace_ingestion_segments(
        self,
        document_id: str,
        segments: Sequence[IngestionSegment],
    ) -> list[IngestionSegment]:
        """指定 document の segment checkpoint を置換する。"""
        return await self._replace_ingestion_segments_with_oracle(document_id, segments)

    async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
        """指定 document の segment checkpoint 一覧を返す。"""
        return await self._list_ingestion_segments_with_oracle(document_id)

    async def update_ingestion_segment(
        self,
        segment_id: str,
        *,
        status: str | None = None,
        attempt_count: int | None = None,
        artifact_path: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> IngestionSegment | None:
        """segment checkpoint の状態を更新する。"""
        return await self._update_ingestion_segment_with_oracle(
            segment_id,
            status=status,
            attempt_count=attempt_count,
            artifact_path=artifact_path,
            error_code=error_code,
            error_message=error_message,
        )

    async def count_ingestion_jobs(self, *, status: IngestionJobStatus | None = None) -> int:
        """アクセス可能な取込 job 件数を返す。"""
        return await self._count_ingestion_jobs_with_oracle(status=status)

    async def recover_stale_ingestion_jobs(
        self,
        *,
        stale_before: datetime,
        limit: int,
    ) -> list[IngestionJob]:
        """stale RUNNING job を再キューまたは失敗へ戻し、対象 job を返す。"""
        return await self._recover_stale_ingestion_jobs_with_oracle(
            stale_before=stale_before,
            limit=limit,
        )

    async def claim_ingestion_job(
        self,
        job_id: str,
        *,
        started_at: datetime,
    ) -> IngestionJob | None:
        """QUEUED job を row lock 付きで RUNNING へ遷移し、実行権を獲得する。"""
        return await self._claim_ingestion_job_with_oracle(job_id, started_at=started_at)

    async def update_ingestion_job(
        self,
        job_id: str,
        *,
        status: IngestionJobStatus | None = None,
        error_message: str | None = None,
        attempt_count: int | None = None,
        max_attempts: int | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> IngestionJob | None:
        """取込 job の状態を更新する。"""
        return await self._update_ingestion_job_with_oracle(
            job_id,
            status=status,
            error_message=error_message,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            started_at=started_at,
            finished_at=finished_at,
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
        knowledge_base_id: str | None = None,
    ) -> list[DocumentSummary]:
        """ドキュメント一覧を返す。"""
        return await self._list_documents_with_oracle(
            status=status,
            query=query,
            limit=limit,
            offset=offset,
            knowledge_base_id=knowledge_base_id,
        )

    async def list_document_extractions(self) -> list[dict[str, object]]:
        """アクセス可能な document の extraction JSON だけを返す。"""
        return await self._list_document_extractions_with_oracle()

    async def count_documents(
        self,
        status: FileStatus | None = None,
        query: str | None = None,
        knowledge_base_id: str | None = None,
    ) -> int:
        """条件に一致するドキュメント数を返す。"""
        return await self._count_documents_with_oracle(
            status=status,
            query=query,
            knowledge_base_id=knowledge_base_id,
        )

    async def count_chunks(self) -> int:
        """検索可能なチャンク行数を返す。"""
        return await self._count_chunks_with_oracle()

    async def list_chunk_metadata(self) -> list[dict[str, MetadataValue]]:
        """検索対象 chunk の metadata JSON だけを返す。"""
        return await self._list_chunk_metadata_with_oracle()

    async def count_document_chunks(self, document_id: str) -> int:
        """指定 document の検索可能なチャンク行数を返す。"""
        return await self._count_document_chunks_with_oracle(document_id)

    async def list_document_chunks(self, document_id: str) -> list[DocumentChunkView]:
        """指定 document の chunk/citation 可視化用 metadata を返す。"""
        return await self._list_document_chunks_with_oracle(document_id)

    async def document_stats(self) -> DocumentStats:
        """ドキュメント状態別の集計を返す。"""
        return await self._document_stats_with_oracle()

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        """ドキュメント詳細を返す。"""
        return await self._get_document_with_oracle(document_id)

    async def delete_document(self, document_id: str) -> bool:
        """ドキュメントと関連 chunk/index/ingestion 行を削除する。"""
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

    async def save_index(
        self,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        chunk_set_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """構造化抽出と chunk/vector を 1 transaction で保存する。

        chunk_set_id を渡すと、その chunk_set の chunk だけを置換・タグ付けする(複数 chunk_set
        共存)。None は文書の全 chunk を置換し未タグ保存(現行挙動・後方互換)。
        """
        if len(chunks) != len(embeddings):
            raise ValueError("chunks と embeddings の件数が一致しません。")
        for index, embedding in enumerate(embeddings):
            self._validate_embedding_width(embedding, f"chunk embedding[{index}]")
        return await self._save_index_with_oracle(
            document_id,
            extraction,
            chunks,
            embeddings,
            chunk_set_id=chunk_set_id,
        )

    async def replace_document_graph_index(
        self,
        document_id: str,
        graph_index: GraphIndex,
    ) -> None:
        """指定 document の GraphRAG-lite index を置換する。"""
        await self._replace_document_graph_index_with_oracle(document_id, graph_index)

    async def save_search_audit_event(self, event: Mapping[str, object]) -> None:
        """脱機密化済み検索監査イベントを Oracle audit table へ保存する。"""
        await self._run_transaction(
            lambda connection: _execute(
                connection,
                """
                INSERT INTO rag_search_audit (
                    event_type,
                    trace_id,
                    request_id,
                    tenant_id_hash,
                    user_id_hash,
                    outcome,
                    search_mode,
                    query_hash,
                    query_chars,
                    filter_keys,
                    memory_plan_id,
                    top_k,
                    rerank_top_n,
                    query_variant_count,
                    guardrail_codes,
                    guardrail_severities,
                    retrieved_count,
                    reranked_count,
                    deduplicated_count,
                    context_diversified_count,
                    context_group_expanded_count,
                    context_expanded_count,
                    context_adaptive_expanded_count,
                    context_dependency_promoted_count,
                    context_compressed_count,
                    context_compression_saved_chars,
                    agent_memory_retrieved_count,
                    agent_memory_writeback_count,
                    agent_memory_writeback_status,
                    evidence_count,
                    support_count,
                    structure_count,
                    history_count,
                    resolver_rejected_count,
                    insufficient_context_count,
                    citation_count,
                    context_chars,
                    context_window_chars,
                    document_ids,
                    knowledge_base_ids,
                    config_fingerprint,
                    elapsed_ms,
                    error_stage,
                    error_type
                ) VALUES (
                    :event_type,
                    :trace_id,
                    :request_id,
                    :tenant_id_hash,
                    :user_id_hash,
                    :outcome,
                    :search_mode,
                    :query_hash,
                    :query_chars,
                    :filter_keys,
                    :memory_plan_id,
                    :top_k,
                    :rerank_top_n,
                    :query_variant_count,
                    :guardrail_codes,
                    :guardrail_severities,
                    :retrieved_count,
                    :reranked_count,
                    :deduplicated_count,
                    :context_diversified_count,
                    :context_group_expanded_count,
                    :context_expanded_count,
                    :context_adaptive_expanded_count,
                    :context_dependency_promoted_count,
                    :context_compressed_count,
                    :context_compression_saved_chars,
                    :agent_memory_retrieved_count,
                    :agent_memory_writeback_count,
                    :agent_memory_writeback_status,
                    :evidence_count,
                    :support_count,
                    :structure_count,
                    :history_count,
                    :resolver_rejected_count,
                    :insufficient_context_count,
                    :citation_count,
                    :context_chars,
                    :context_window_chars,
                    :document_ids,
                    :knowledge_base_ids,
                    :config_fingerprint,
                    :elapsed_ms,
                    :error_stage,
                    :error_type
                )
                """,
                _search_audit_binds(event),
            )
        )

    async def save_ingestion_audit_event(self, event: Mapping[str, object]) -> None:
        """脱機密化済み取込監査イベントを Oracle audit table へ保存する。"""
        await self._run_transaction(
            lambda connection: _execute(
                connection,
                """
                INSERT INTO rag_ingestion_audit (
                    event_type,
                    trace_id,
                    request_id,
                    tenant_id_hash,
                    user_id_hash,
                    document_id,
                    outcome,
                    source_sha256,
                    source_bytes,
                    document_type,
                    extraction_confidence,
                    parser_backend,
                    parser_profile,
                    segment_count,
                    fallback_count,
                    failed_segment_count,
                    chunk_count,
                    vector_count,
                    elapsed_ms,
                    error_type,
                    error_message
                ) VALUES (
                    :event_type,
                    :trace_id,
                    :request_id,
                    :tenant_id_hash,
                    :user_id_hash,
                    :document_id,
                    :outcome,
                    :source_sha256,
                    :source_bytes,
                    :document_type,
                    :extraction_confidence,
                    :parser_backend,
                    :parser_profile,
                    :segment_count,
                    :fallback_count,
                    :failed_segment_count,
                    :chunk_count,
                    :vector_count,
                    :elapsed_ms,
                    :error_type,
                    :error_message
                )
                """,
                _ingestion_audit_binds(event),
            )
        )

    async def save_citation_feedback(self, feedback: Mapping[str, object]) -> str:
        """引用 feedback を低機密 metadata だけで Oracle へ保存する。"""
        feedback_id = _audit_str(feedback, "feedback_id", uuid4().hex)
        binds = _citation_feedback_binds(feedback, feedback_id=feedback_id)
        await self._run_transaction(
            lambda connection: _execute(
                connection,
                """
                INSERT INTO rag_citation_feedback (
                    feedback_id,
                    trace_id,
                    document_id,
                    chunk_id,
                    tenant_id_hash,
                    user_id_hash,
                    rating,
                    reason,
                    comment_hash,
                    comment_chars
                ) VALUES (
                    :feedback_id,
                    :trace_id,
                    :document_id,
                    :chunk_id,
                    :tenant_id_hash,
                    :user_id_hash,
                    :rating,
                    :reason,
                    :comment_hash,
                    :comment_chars
                )
                """,
                binds,
            )
        )
        return feedback_id

    async def save_evaluation_artifact(self, artifact: Mapping[str, object]) -> str:
        """nightly / staging 評価 artifact を query 原文なしで Oracle へ保存する。"""
        evaluation_run_id = _audit_str(artifact, "evaluation_run_id", uuid4().hex)
        binds = _evaluation_artifact_binds(artifact, evaluation_run_id=evaluation_run_id)
        await self._run_transaction(
            lambda connection: _execute(
                connection,
                """
                INSERT INTO rag_evaluation_runs (
                    evaluation_run_id,
                    tenant_id_hash,
                    knowledge_base_ids,
                    request_json,
                    result_json,
                    result_sha256,
                    best_experiment_id,
                    passed
                ) VALUES (
                    :evaluation_run_id,
                    :tenant_id_hash,
                    :knowledge_base_ids,
                    :request_json,
                    :result_json,
                    :result_sha256,
                    :best_experiment_id,
                    :passed
                )
                """,
                binds,
            )
        )
        return evaluation_run_id

    async def agent_memory_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        """Oracle 26ai Agent Memory から scoped history context を取得する。"""
        del filters
        if top_k <= 0 or not _agent_memory_scope_available():
            return []
        self._validate_embedding_width(embedding, "agent memory query embedding")
        if _LOCAL_STORE.agent_memories and not _oracle_connection_configured(self):
            return _local_agent_memory_search(query, embedding, top_k)
        if not _oracle_connection_configured(self):
            return []
        return await self._agent_memory_search_with_oracle(query, embedding, top_k)

    async def save_agent_memory(
        self,
        memory: Mapping[str, object],
        embedding: list[float],
    ) -> str | None:
        """根拠付き回答の低機密 summary を scoped Agent Memory として保存する。"""
        if not _agent_memory_scope_available():
            return None
        if not str(memory.get("memory_text") or "").strip():
            return None
        self._validate_embedding_width(embedding, "agent memory embedding")
        memory_id = _audit_str(memory, "memory_id", uuid4().hex)
        binds = _agent_memory_binds(memory, memory_id=memory_id, embedding=embedding)
        if _LOCAL_STORE.agent_memories and not _oracle_connection_configured(self):
            _LOCAL_STORE.agent_memories[memory_id] = _stored_agent_memory_from_binds(binds)
            return memory_id
        if not _oracle_connection_configured(self):
            return None
        await self._run_transaction(
            lambda connection: _execute(
                connection,
                """
                INSERT INTO rag_agent_memories (
                    memory_id,
                    tenant_id_hash,
                    user_id_hash,
                    role_id_hash,
                    agent_id_hash,
                    thread_id_hash,
                    trace_id,
                    memory_text,
                    metadata_json,
                    embedding,
                    usefulness_score,
                    eval_count,
                    created_at,
                    updated_at
                ) VALUES (
                    :memory_id,
                    :tenant_id_hash,
                    :user_id_hash,
                    :role_id_hash,
                    :agent_id_hash,
                    :thread_id_hash,
                    :trace_id,
                    :memory_text,
                    :metadata_json,
                    :embedding,
                    :usefulness_score,
                    :eval_count,
                    :created_at,
                    :updated_at
                )
                """,
                binds,
            )
        )
        return memory_id

    async def evaluate_agent_memory(
        self,
        memory_id: str,
        *,
        useful: bool,
    ) -> None:
        """memory feedback を usefulness_score の移動平均として保存する。"""
        cleaned_memory_id = memory_id.strip()
        if not cleaned_memory_id or not _agent_memory_scope_available():
            return
        if cleaned_memory_id in _LOCAL_STORE.agent_memories and not _oracle_connection_configured(
            self
        ):
            stored = _LOCAL_STORE.agent_memories[cleaned_memory_id]
            next_count = stored.eval_count + 1
            next_score = (stored.usefulness_score * stored.eval_count) + (1.0 if useful else 0.0)
            next_score = next_score / next_count
            _LOCAL_STORE.agent_memories[cleaned_memory_id] = replace(
                stored,
                usefulness_score=round(next_score, 6),
                eval_count=next_count,
                updated_at=datetime.now(UTC),
            )
            return
        if not _oracle_connection_configured(self):
            return
        where_sql, binds = _oracle_agent_memory_where()
        binds.update(
            {
                "memory_id": cleaned_memory_id,
                "useful_score": 1.0 if useful else 0.0,
            }
        )
        await self._run_transaction(
            lambda connection: _execute(
                connection,
                _render_sql(
                    """
                UPDATE rag_agent_memories m
                SET usefulness_score =
                        ROUND(
                            ((usefulness_score * eval_count) + :useful_score)
                            / (eval_count + 1),
                            6
                        ),
                    eval_count = eval_count + 1,
                    updated_at = SYSTIMESTAMP
                WHERE m.memory_id = :memory_id
                  AND {where_sql}
                """,
                    where_sql=where_sql,
                ),
                binds,
            )
        )

    async def _agent_memory_search_with_oracle(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Oracle VECTOR + Text で scoped Agent Memory を検索する。"""
        where_sql, binds = _oracle_agent_memory_where()
        binds.update(
            {
                "embedding": _to_vector_bind(embedding),
                "min_similarity": self._settings.rag_min_similarity,
                "query": query,
            }
        )
        fetch_clause = _oracle_vector_fetch_clause(
            top_k=top_k,
            target_accuracy=resolve_vector_index_adapter(self._settings).target_accuracy,
        )
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                memory_id,
                memory_text,
                metadata_json,
                usefulness_score,
                eval_count,
                updated_at,
                (
                    1 - VECTOR_DISTANCE(embedding, :embedding, COSINE)
                ) AS vector_score,
                (
                    (1 - VECTOR_DISTANCE(embedding, :embedding, COSINE)) * 0.85
                    + LEAST(NVL(usefulness_score, 0.5), 1) * 0.15
                ) AS score
            FROM rag_agent_memories m
            WHERE {where_sql}
              AND 1 - VECTOR_DISTANCE(embedding, :embedding, COSINE) >= :min_similarity
            ORDER BY
                VECTOR_DISTANCE(embedding, :embedding, COSINE) ASC,
                usefulness_score DESC,
                updated_at DESC,
                memory_id ASC
            {fetch_clause}
            """,
                where_sql=where_sql,
                fetch_clause=fetch_clause,
            ),
            binds,
        )
        return [_agent_memory_chunk_from_row(row, rank=rank) for rank, row in enumerate(rows, 1)]

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
            target_accuracy=resolve_vector_index_adapter(self._settings).target_accuracy,
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

    async def _graph_local_search_with_oracle(
        self,
        query: str,
        top_k: int,
        filters: dict[str, str],
    ) -> list[RetrievedChunk]:
        """Oracle KG entity/claim から関連 chunk を取得する。"""
        where_sql, binds = _oracle_retrieval_where(filters)
        match_sql, match_binds = _oracle_graph_local_match_predicate(query)
        binds.update(match_binds)
        binds["top_k"] = top_k
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
                    e.entity_id,
                    e.canonical_name,
                    e.entity_type,
                    NVL(e.confidence, 1) AS entity_confidence,
                    NVL(ec.relevance_score, 1) AS entity_chunk_relevance,
                    (
                        NVL(ec.relevance_score, 1) * 0.65
                        + NVL(e.confidence, 1) * 0.35
                    ) AS score
                FROM rag_graph_entities e
                JOIN rag_graph_entity_chunks ec
                  ON ec.entity_id = e.entity_id
                JOIN rag_chunks c
                  ON c.chunk_id = ec.chunk_id
                 AND c.document_id = ec.document_id
                JOIN rag_documents d
                  ON d.document_id = c.document_id
                WHERE {where_sql}
                  AND {match_sql}
                ORDER BY
                    score DESC,
                    c.document_id ASC,
                    c.chunk_index ASC,
                    c.chunk_id ASC
            )
            WHERE ROWNUM <= :top_k
            """,
                where_sql=where_sql,
                match_sql=match_sql,
            ),
            binds,
        )
        return [
            _with_retrieval_metadata(
                _retrieved_chunk_from_row(row),
                retrieval_mode="graph_local",
                graph_rank=rank,
                graph_entity_id=_optional_str(row.get("entity_id")),
                graph_entity_name=_optional_str(row.get("canonical_name")),
                graph_entity_type=_optional_str(row.get("entity_type")),
                graph_entity_confidence=round(_float_value(row.get("entity_confidence")), 6),
                graph_entity_chunk_relevance=round(
                    _float_value(row.get("entity_chunk_relevance")),
                    6,
                ),
            )
            for rank, row in enumerate(rows, start=1)
        ]

    async def _graph_global_search_with_oracle(
        self,
        query: str,
        top_k: int,
        filters: dict[str, str],
    ) -> list[RetrievedChunk]:
        """Oracle KG community summary から横断 context を取得する。"""
        where_sql, binds = _oracle_graph_community_where(filters)
        match_sql, match_binds = _oracle_graph_global_match_predicate(query)
        binds.update(match_binds)
        binds["top_k"] = top_k
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT *
            FROM (
                SELECT
                    community_id,
                    knowledge_base_id,
                    level_no,
                    title,
                    summary_text,
                    source_document_ids,
                    (
                        CASE
                            WHEN LOWER(title) LIKE :graph_title_exact ESCAPE '\\' THEN 1
                            ELSE 0
                        END
                        + 0.75
                    ) AS score
                FROM rag_graph_community_summaries g
                WHERE {where_sql}
                  AND {match_sql}
                ORDER BY
                    score DESC,
                    level_no ASC,
                    community_id ASC
            )
            WHERE ROWNUM <= :top_k
            """,
                where_sql=where_sql,
                match_sql=match_sql,
            ),
            binds,
        )
        return [
            _graph_community_chunk_from_row(row, rank=rank)
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

    async def _context_dependency_chunks_with_oracle(
        self,
        anchors: list[RetrievedChunk],
        *,
        max_chunks_per_anchor: int,
    ) -> list[RetrievedChunk]:
        """Oracle から dependency promotion 用の同一 document 候補を取得する。"""
        dependency_chunks: list[RetrievedChunk] = []
        candidate_limit = max(max_chunks_per_anchor * 8, max_chunks_per_anchor)
        for anchor in anchors:
            anchor_index = _chunk_index_from_retrieved(anchor) or 0
            where_sql, binds = _oracle_retrieval_where({"document_id": anchor.document_id})
            dependency_match_sql, dependency_binds = _context_dependency_match_sql(anchor)
            binds.update(
                {
                    "anchor_index": anchor_index,
                    "anchor_chunk_id": anchor.chunk_id,
                    "candidate_limit": candidate_limit,
                    **dependency_binds,
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
                      AND c.chunk_id <> :anchor_chunk_id
                      AND (
                        {dependency_match_sql}
                      )
                    ORDER BY
                        ABS(c.chunk_index - :anchor_index) ASC,
                        c.chunk_index ASC,
                        c.chunk_id ASC
                )
                WHERE ROWNUM <= :candidate_limit
                """,
                    where_sql=where_sql,
                    dependency_match_sql=dependency_match_sql,
                ),
                binds,
            )
            dependency_chunks.extend(
                _retrieved_chunk_from_row(row).model_copy(update={"score": anchor.score})
                for row in rows
            )
        return dependency_chunks

    async def _create_document_with_oracle(
        self,
        file_name: str,
        object_storage_path: str,
        content_type: str | None,
        file_size_bytes: int | None,
        content_sha256: str | None,
        duplicate_of_document_id: str | None,
        knowledge_base_ids: Sequence[str] | None,
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
        requested_knowledge_base_ids = _unique_optional_sequence(knowledge_base_ids or [])

        def operation(connection: OracleConnectionProtocol) -> DocumentDetail:
            knowledge_bases = (
                [
                    _require_active_knowledge_base(connection, knowledge_base_id)
                    for knowledge_base_id in requested_knowledge_base_ids
                ]
                if requested_knowledge_base_ids
                else [_ensure_default_knowledge_base(connection, DEFAULT_KNOWLEDGE_BASE_NAME)]
            )
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
            _insert_document_knowledge_base_rows(
                connection,
                document_id=document.id,
                knowledge_base_ids=[knowledge_base.id for knowledge_base in knowledge_bases],
            )
            return _to_document_detail(document).model_copy(
                update={
                    "knowledge_bases": [
                        _to_knowledge_base_ref(knowledge_base) for knowledge_base in knowledge_bases
                    ]
                }
            )

        return await self._run_transaction(operation)

    async def _create_knowledge_base_with_oracle(
        self,
        *,
        name: str,
        description: str | None,
        default_search_mode: SearchMode,
        retrieval_config: dict[str, object],
    ) -> KnowledgeBaseDetail:
        """Oracle knowledge base table へ行を作成する。"""
        now = datetime.now(UTC)
        knowledge_base = StoredKnowledgeBase(
            id=uuid4().hex,
            tenant_id_hash=_current_tenant_id_hash(),
            name=name,
            description=description,
            status=KnowledgeBaseStatus.ACTIVE,
            default_search_mode=default_search_mode,
            retrieval_config=retrieval_config,
            created_at=now,
            updated_at=now,
        )

        def operation(connection: OracleConnectionProtocol) -> KnowledgeBaseDetail:
            _execute(
                connection,
                """
                INSERT INTO rag_knowledge_bases (
                    knowledge_base_id,
                    tenant_id_hash,
                    name,
                    description,
                    status,
                    default_search_mode,
                    retrieval_config,
                    created_at,
                    updated_at,
                    archived_at
                ) VALUES (
                    :knowledge_base_id,
                    :tenant_id_hash,
                    :name,
                    :description,
                    :status,
                    :default_search_mode,
                    :retrieval_config,
                    :created_at,
                    :updated_at,
                    :archived_at
                )
                """,
                _knowledge_base_binds(knowledge_base),
            )
            return _to_knowledge_base_detail(knowledge_base)

        return await self._run_transaction(operation)

    async def _find_knowledge_base_by_name_with_oracle(
        self,
        name: str,
    ) -> KnowledgeBaseDetail | None:
        """tenant 内のナレッジベースを名前で探す。"""
        where_sql, binds = _oracle_knowledge_base_where(query=None)
        binds["knowledge_base_name"] = name.casefold()
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                kb.knowledge_base_id,
                kb.tenant_id_hash,
                kb.name,
                kb.description,
                kb.status,
                kb.default_search_mode,
                kb.retrieval_config,
                kb.created_at,
                kb.updated_at,
                kb.archived_at,
                0 AS document_count,
                0 AS indexed_document_count,
                0 AS error_document_count,
                0 AS searchable_chunk_count
            FROM rag_knowledge_bases kb
            WHERE {where_sql}
              AND LOWER(kb.name) = :knowledge_base_name
            ORDER BY kb.created_at ASC
            FETCH FIRST 1 ROWS ONLY
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        if not rows:
            return None
        return _to_knowledge_base_detail(_stored_knowledge_base_from_row(rows[0]))

    async def _list_knowledge_bases_with_oracle(
        self,
        *,
        status: KnowledgeBaseStatus | None,
        query: str | None,
        limit: int | None,
        offset: int,
    ) -> list[KnowledgeBaseSummary]:
        """Oracle knowledge base table から一覧取得する。"""
        where_sql, binds = _oracle_knowledge_base_where(status=status, query=query)
        binds["offset"] = offset
        if limit is not None:
            binds["limit"] = limit
            paging_sql = "OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"
        else:
            paging_sql = "OFFSET :offset ROWS"
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                kb.knowledge_base_id,
                kb.tenant_id_hash,
                kb.name,
                kb.description,
                kb.status,
                kb.default_search_mode,
                kb.retrieval_config,
                kb.created_at,
                kb.updated_at,
                kb.archived_at,
                COUNT(DISTINCT dkb.document_id) AS document_count,
                COUNT(DISTINCT CASE WHEN d.status = 'INDEXED' THEN d.document_id END)
                    AS indexed_document_count,
                COUNT(DISTINCT CASE WHEN d.status = 'ERROR' THEN d.document_id END)
                    AS error_document_count,
                COUNT(c.chunk_id) AS searchable_chunk_count
            FROM rag_knowledge_bases kb
            LEFT JOIN rag_document_knowledge_bases dkb
                ON dkb.knowledge_base_id = kb.knowledge_base_id
            LEFT JOIN rag_documents d
                ON d.document_id = dkb.document_id
               AND {document_access_sql}
            LEFT JOIN rag_chunks c
                ON c.document_id = d.document_id
               AND d.status = 'INDEXED'
            WHERE {where_sql}
            GROUP BY
                kb.knowledge_base_id,
                kb.tenant_id_hash,
                kb.name,
                kb.description,
                kb.status,
                kb.default_search_mode,
                kb.retrieval_config,
                kb.created_at,
                kb.updated_at,
                kb.archived_at
            ORDER BY kb.updated_at DESC, kb.name ASC
            {paging_sql}
            """,
                where_sql=where_sql,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                paging_sql=paging_sql,
            ),
            binds,
        )
        return [_to_knowledge_base_summary(_stored_knowledge_base_from_row(row)) for row in rows]

    async def _count_knowledge_bases_with_oracle(
        self,
        *,
        status: KnowledgeBaseStatus | None,
        query: str | None,
    ) -> int:
        """Oracle knowledge base table の件数を取得する。"""
        where_sql, binds = _oracle_knowledge_base_where(status=status, query=query)
        row = await self._fetch_one(
            _render_sql(
                """
            SELECT COUNT(*) AS count_value
            FROM rag_knowledge_bases kb
            WHERE {where_sql}
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return _row_count_value(row)

    async def _get_knowledge_base_with_oracle(
        self,
        knowledge_base_id: str,
    ) -> KnowledgeBaseDetail | None:
        """Oracle knowledge base table から詳細取得する。"""
        rows = await self._fetch_all(
            """
            SELECT
                kb.knowledge_base_id,
                kb.tenant_id_hash,
                kb.name,
                kb.description,
                kb.status,
                kb.default_search_mode,
                kb.retrieval_config,
                kb.created_at,
                kb.updated_at,
                kb.archived_at,
                COUNT(DISTINCT dkb.document_id) AS document_count,
                COUNT(DISTINCT CASE WHEN d.status = 'INDEXED' THEN d.document_id END)
                    AS indexed_document_count,
                COUNT(DISTINCT CASE WHEN d.status = 'ERROR' THEN d.document_id END)
                    AS error_document_count,
                COUNT(c.chunk_id) AS searchable_chunk_count
            FROM rag_knowledge_bases kb
            LEFT JOIN rag_document_knowledge_bases dkb
                ON dkb.knowledge_base_id = kb.knowledge_base_id
            LEFT JOIN rag_documents d
                ON d.document_id = dkb.document_id
               AND {document_access_sql}
            LEFT JOIN rag_chunks c
                ON c.document_id = d.document_id
               AND d.status = 'INDEXED'
            WHERE kb.knowledge_base_id = :knowledge_base_id
              AND {knowledge_base_access_sql}
            GROUP BY
                kb.knowledge_base_id,
                kb.tenant_id_hash,
                kb.name,
                kb.description,
                kb.status,
                kb.default_search_mode,
                kb.retrieval_config,
                kb.created_at,
                kb.updated_at,
                kb.archived_at
            """.format(
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
            ),
            _with_tenant_bind({"knowledge_base_id": knowledge_base_id}),
        )
        if not rows:
            return None
        return _to_knowledge_base_detail(_stored_knowledge_base_from_row(rows[0]))

    async def _update_knowledge_base_with_oracle(
        self,
        *,
        knowledge_base_id: str,
        name: str | None,
        description: str | None,
        default_search_mode: SearchMode | None,
        retrieval_config: dict[str, object] | None,
        update_fields: set[str] | None,
    ) -> KnowledgeBaseDetail:
        """Oracle knowledge base table を更新する。"""
        fields = update_fields or {
            field_name
            for field_name, value in {
                "name": name,
                "description": description,
                "default_search_mode": default_search_mode,
                "retrieval_config": retrieval_config,
            }.items()
            if value is not None
        }

        def operation(connection: OracleConnectionProtocol) -> KnowledgeBaseDetail:
            existing = _select_knowledge_base(connection, knowledge_base_id)
            if existing is None:
                raise KeyError(f"knowledge_base_id={knowledge_base_id} は存在しません。")
            updated = existing
            now = datetime.now(UTC)
            if fields:
                updated = existing
                if "name" in fields and name is not None:
                    updated = updated_copy_knowledge_base(updated, name=name)
                if "description" in fields:
                    updated = updated_copy_knowledge_base(updated, description=description)
                if "default_search_mode" in fields and default_search_mode is not None:
                    updated = updated_copy_knowledge_base(
                        updated,
                        default_search_mode=default_search_mode,
                    )
                if "retrieval_config" in fields:
                    updated = updated_copy_knowledge_base(
                        updated,
                        retrieval_config=retrieval_config or {},
                    )
                updated = updated_copy_knowledge_base(updated, updated_at=now)
                _execute(
                    connection,
                    _render_sql(
                        """
                    UPDATE rag_knowledge_bases
                    SET
                        name = :name,
                        description = :description,
                        default_search_mode = :default_search_mode,
                        retrieval_config = :retrieval_config,
                        updated_at = :updated_at
                    WHERE knowledge_base_id = :knowledge_base_id
                      AND {knowledge_base_access_sql}
                    """,
                        knowledge_base_access_sql=(_oracle_knowledge_base_access_predicate_sql()),
                    ),
                    _knowledge_base_binds(updated),
                )
            return _to_knowledge_base_detail(updated)

        return await self._run_transaction(operation)

    async def _archive_knowledge_base_with_oracle(
        self,
        knowledge_base_id: str,
    ) -> KnowledgeBaseDetail:
        """Oracle knowledge base table の status を ARCHIVED にする。"""

        def operation(connection: OracleConnectionProtocol) -> KnowledgeBaseDetail:
            existing = _select_knowledge_base(connection, knowledge_base_id)
            if existing is None:
                raise KeyError(f"knowledge_base_id={knowledge_base_id} は存在しません。")
            now = datetime.now(UTC)
            archived = updated_copy_knowledge_base(
                existing,
                status=KnowledgeBaseStatus.ARCHIVED,
                updated_at=now,
                archived_at=now,
            )
            _execute(
                connection,
                _render_sql(
                    """
                UPDATE rag_knowledge_bases
                SET
                    status = :status,
                    updated_at = :updated_at,
                    archived_at = :archived_at
                WHERE knowledge_base_id = :knowledge_base_id
                  AND {knowledge_base_access_sql}
                """,
                    knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(),
                ),
                _knowledge_base_binds(archived),
            )
            return _to_knowledge_base_detail(archived)

        return await self._run_transaction(operation)

    async def _create_business_view_with_oracle(
        self,
        *,
        name: str,
        description: str | None,
        config: BusinessViewConfig,
    ) -> BusinessViewDetail:
        """Oracle business view table へ行を作成する。"""
        now = datetime.now(UTC)
        view = StoredBusinessView(
            id=uuid4().hex,
            tenant_id_hash=_current_tenant_id_hash(),
            name=name,
            description=description,
            status=BusinessViewStatus.ACTIVE,
            view_config=dump_business_view_config(config),
            created_at=now,
            updated_at=now,
        )

        def operation(connection: OracleConnectionProtocol) -> BusinessViewDetail:
            _execute(
                connection,
                """
                INSERT INTO rag_business_views (
                    business_view_id,
                    tenant_id_hash,
                    name,
                    description,
                    status,
                    view_config,
                    created_at,
                    updated_at,
                    archived_at
                ) VALUES (
                    :business_view_id,
                    :tenant_id_hash,
                    :name,
                    :description,
                    :status,
                    :view_config,
                    :created_at,
                    :updated_at,
                    :archived_at
                )
                """,
                _business_view_binds(view),
            )
            return _to_business_view_detail(view)

        return await self._run_transaction(operation)

    async def _list_business_views_with_oracle(
        self,
        *,
        status: BusinessViewStatus | None,
        query: str | None,
        limit: int | None,
        offset: int,
    ) -> list[BusinessViewSummary]:
        """Oracle business view table から一覧取得する。"""
        where_sql, binds = _oracle_business_view_where(status=status, query=query)
        binds["offset"] = offset
        if limit is not None:
            binds["limit"] = limit
            paging_sql = "OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"
        else:
            paging_sql = "OFFSET :offset ROWS"
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                bv.business_view_id,
                bv.tenant_id_hash,
                bv.name,
                bv.description,
                bv.status,
                bv.view_config,
                bv.created_at,
                bv.updated_at,
                bv.archived_at
            FROM rag_business_views bv
            WHERE {where_sql}
            ORDER BY bv.updated_at DESC, bv.name ASC
            {paging_sql}
            """,
                where_sql=where_sql,
                paging_sql=paging_sql,
            ),
            binds,
        )
        return [_to_business_view_summary(_stored_business_view_from_row(row)) for row in rows]

    async def _count_business_views_with_oracle(
        self,
        *,
        status: BusinessViewStatus | None,
        query: str | None,
    ) -> int:
        """Oracle business view table の件数を取得する。"""
        where_sql, binds = _oracle_business_view_where(status=status, query=query)
        row = await self._fetch_one(
            _render_sql(
                """
            SELECT COUNT(*) AS count_value
            FROM rag_business_views bv
            WHERE {where_sql}
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return _row_count_value(row)

    async def _get_business_view_with_oracle(
        self,
        business_view_id: str,
    ) -> BusinessViewDetail | None:
        """Oracle business view table から詳細取得する(KB 名は未解決)。"""
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                bv.business_view_id,
                bv.tenant_id_hash,
                bv.name,
                bv.description,
                bv.status,
                bv.view_config,
                bv.created_at,
                bv.updated_at,
                bv.archived_at
            FROM rag_business_views bv
            WHERE bv.business_view_id = :business_view_id
              AND {tenant_sql}
            """,
                tenant_sql=_oracle_tenant_predicate(alias="bv"),
            ),
            _with_tenant_bind({"business_view_id": business_view_id}),
        )
        if not rows:
            return None
        return _to_business_view_detail(_stored_business_view_from_row(rows[0]))

    async def _update_business_view_with_oracle(
        self,
        *,
        business_view_id: str,
        name: str | None,
        description: str | None,
        config: BusinessViewConfig | None,
        update_fields: set[str] | None,
    ) -> BusinessViewDetail:
        """Oracle business view table を更新する。"""
        fields = update_fields or {
            field_name
            for field_name, value in {
                "name": name,
                "description": description,
                "config": config,
            }.items()
            if value is not None
        }

        def operation(connection: OracleConnectionProtocol) -> BusinessViewDetail:
            existing = _select_business_view(connection, business_view_id)
            if existing is None:
                raise KeyError(f"business_view_id={business_view_id} は存在しません。")
            updated = existing
            now = datetime.now(UTC)
            if fields:
                if "name" in fields and name is not None:
                    updated = updated_copy_business_view(updated, name=name)
                if "description" in fields:
                    updated = updated_copy_business_view(updated, description=description)
                if "config" in fields and config is not None:
                    updated = updated_copy_business_view(
                        updated,
                        view_config=dump_business_view_config(config),
                    )
                updated = updated_copy_business_view(updated, updated_at=now)
                _execute(
                    connection,
                    _render_sql(
                        """
                    UPDATE rag_business_views
                    SET
                        name = :name,
                        description = :description,
                        view_config = :view_config,
                        updated_at = :updated_at
                    WHERE business_view_id = :business_view_id
                      AND {tenant_sql}
                    """,
                        tenant_sql=_oracle_tenant_predicate(),
                    ),
                    _business_view_binds(updated),
                )
            return _to_business_view_detail(updated)

        return await self._run_transaction(operation)

    async def _archive_business_view_with_oracle(
        self,
        business_view_id: str,
    ) -> BusinessViewDetail:
        """Oracle business view table の status を ARCHIVED にする。"""

        def operation(connection: OracleConnectionProtocol) -> BusinessViewDetail:
            existing = _select_business_view(connection, business_view_id)
            if existing is None:
                raise KeyError(f"business_view_id={business_view_id} は存在しません。")
            now = datetime.now(UTC)
            archived = updated_copy_business_view(
                existing,
                status=BusinessViewStatus.ARCHIVED,
                updated_at=now,
                archived_at=now,
            )
            _execute(
                connection,
                _render_sql(
                    """
                UPDATE rag_business_views
                SET
                    status = :status,
                    updated_at = :updated_at,
                    archived_at = :archived_at
                WHERE business_view_id = :business_view_id
                  AND {tenant_sql}
                """,
                    tenant_sql=_oracle_tenant_predicate(),
                ),
                _business_view_binds(archived),
            )
            return _to_business_view_detail(archived)

        return await self._run_transaction(operation)

    async def _resolve_knowledge_base_refs(
        self,
        knowledge_base_ids: Sequence[str],
    ) -> list[KnowledgeBaseRef]:
        """参照 KB ID 群から存在する KB の {id, name} を tenant scope で解決する。"""
        ids = _unique_optional_sequence(knowledge_base_ids)
        if not ids:
            return []
        in_sql, in_binds = _oracle_in_predicate("kb.knowledge_base_id", "ref_kb_id", ids)
        binds = _with_tenant_bind({})
        binds.update(in_binds)
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT kb.knowledge_base_id, kb.name
            FROM rag_knowledge_bases kb
            WHERE {in_sql}
              AND {tenant_sql}
            """,
                in_sql=in_sql,
                tenant_sql=_oracle_tenant_predicate(alias="kb"),
            ),
            binds,
        )
        by_id = {str(row["knowledge_base_id"]): str(row["name"]) for row in rows}
        # 入力順を保ち、存在しない KB は落とす。
        return [
            KnowledgeBaseRef(id=knowledge_base_id, name=by_id[knowledge_base_id])
            for knowledge_base_id in ids
            if knowledge_base_id in by_id
        ]

    async def _assign_documents_to_knowledge_base_with_oracle(
        self,
        knowledge_base_id: str,
        document_ids: Sequence[str],
    ) -> KnowledgeBaseDetail:
        """Oracle membership table へ文書所属を追加する。"""
        unique_document_ids = _unique_sequence(document_ids)

        def operation(connection: OracleConnectionProtocol) -> KnowledgeBaseDetail:
            knowledge_base = _require_active_knowledge_base(connection, knowledge_base_id)
            for document_id in unique_document_ids:
                if _select_document(connection, document_id) is None:
                    raise KeyError(f"document_id={document_id} は存在しません。")
            _executemany(
                connection,
                """
                MERGE INTO rag_document_knowledge_bases target
                USING (
                    SELECT
                        :knowledge_base_id AS knowledge_base_id,
                        :document_id AS document_id
                    FROM dual
                ) source
                ON (
                    target.knowledge_base_id = source.knowledge_base_id
                    AND target.document_id = source.document_id
                )
                WHEN NOT MATCHED THEN
                    INSERT (
                        knowledge_base_id,
                        document_id,
                        tenant_id_hash,
                        assigned_at,
                        assigned_by_user_id_hash
                    ) VALUES (
                        :knowledge_base_id,
                        :document_id,
                        :tenant_id_hash,
                        :assigned_at,
                        :assigned_by_user_id_hash
                    )
                """,
                [
                    _document_knowledge_base_binds(
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                    )
                    for document_id in unique_document_ids
                ],
            )
            return _to_knowledge_base_detail(knowledge_base)

        return await self._run_transaction(operation)

    async def _remove_document_from_knowledge_base_with_oracle(
        self,
        knowledge_base_id: str,
        document_id: str,
    ) -> KnowledgeBaseDetail:
        """Oracle membership table から文書所属を削除する。"""

        def operation(connection: OracleConnectionProtocol) -> KnowledgeBaseDetail:
            knowledge_base = _select_knowledge_base(connection, knowledge_base_id)
            if knowledge_base is None:
                raise KeyError(f"knowledge_base_id={knowledge_base_id} は存在しません。")
            if _select_document(connection, document_id) is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            _execute(
                connection,
                _render_sql(
                    """
                DELETE FROM rag_document_knowledge_bases
                WHERE knowledge_base_id = :knowledge_base_id
                  AND document_id = :document_id
                  AND {knowledge_base_membership_access_sql}
                """,
                    knowledge_base_membership_access_sql=(
                        _oracle_membership_access_predicate_sql()
                    ),
                ),
                _with_tenant_bind(
                    {
                        "knowledge_base_id": knowledge_base_id,
                        "document_id": document_id,
                    }
                ),
            )
            return _to_knowledge_base_detail(knowledge_base)

        return await self._run_transaction(operation)

    async def _replace_document_knowledge_bases_with_oracle(
        self,
        document_id: str,
        knowledge_base_ids: Sequence[str],
    ) -> list[KnowledgeBaseRef]:
        """Oracle membership table の文書所属を置換する。"""
        unique_knowledge_base_ids = _unique_sequence(knowledge_base_ids)

        def operation(connection: OracleConnectionProtocol) -> list[KnowledgeBaseRef]:
            if _select_document(connection, document_id) is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            knowledge_bases = [
                _require_active_knowledge_base(connection, knowledge_base_id)
                for knowledge_base_id in unique_knowledge_base_ids
            ]
            _execute(
                connection,
                _render_sql(
                    """
                DELETE FROM rag_document_knowledge_bases
                WHERE document_id = :document_id
                  AND {knowledge_base_membership_access_sql}
                """,
                    knowledge_base_membership_access_sql=(
                        _oracle_membership_access_predicate_sql()
                    ),
                ),
                _with_tenant_bind({"document_id": document_id}),
            )
            _executemany(
                connection,
                """
                INSERT INTO rag_document_knowledge_bases (
                    knowledge_base_id,
                    document_id,
                    tenant_id_hash,
                    assigned_at,
                    assigned_by_user_id_hash
                ) VALUES (
                    :knowledge_base_id,
                    :document_id,
                    :tenant_id_hash,
                    :assigned_at,
                    :assigned_by_user_id_hash
                )
                """,
                [
                    _document_knowledge_base_binds(
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                    )
                    for knowledge_base_id in unique_knowledge_base_ids
                ],
            )
            return [_to_knowledge_base_ref(knowledge_base) for knowledge_base in knowledge_bases]

        return await self._run_transaction(operation)

    async def _list_document_knowledge_bases_with_oracle(
        self,
        document_id: str,
    ) -> list[KnowledgeBaseRef]:
        """Oracle membership table から文書所属を取得する。"""
        rows = await self._fetch_all(
            """
            SELECT
                kb.knowledge_base_id,
                kb.name
            FROM rag_document_knowledge_bases dkb
            JOIN rag_knowledge_bases kb
                ON kb.knowledge_base_id = dkb.knowledge_base_id
            JOIN rag_documents d
                ON d.document_id = dkb.document_id
            WHERE dkb.document_id = :document_id
              AND {document_access_sql}
              AND {knowledge_base_access_sql}
            ORDER BY kb.name ASC, kb.knowledge_base_id ASC
            """.format(
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
            ),
            _with_tenant_bind({"document_id": document_id}),
        )
        return [
            KnowledgeBaseRef(id=str(row["knowledge_base_id"]), name=str(row["name"]))
            for row in rows
        ]

    async def _get_owning_knowledge_base_id_with_oracle(
        self,
        document_id: str,
    ) -> str | None:
        """最古割当の所属 KB の id を返す。所属が無ければ None。"""
        rows = await self._fetch_all(
            """
            SELECT
                dkb.knowledge_base_id
            FROM rag_document_knowledge_bases dkb
            JOIN rag_knowledge_bases kb
                ON kb.knowledge_base_id = dkb.knowledge_base_id
            JOIN rag_documents d
                ON d.document_id = dkb.document_id
            WHERE dkb.document_id = :document_id
              AND {document_access_sql}
              AND {knowledge_base_access_sql}
            ORDER BY dkb.assigned_at ASC, dkb.knowledge_base_id ASC
            FETCH FIRST 1 ROWS ONLY
            """.format(
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
            ),
            _with_tenant_bind({"document_id": document_id}),
        )
        if not rows:
            return None
        return str(rows[0]["knowledge_base_id"])

    async def _document_knowledge_base_refs_by_document_id_with_oracle(
        self,
        document_ids: Sequence[str],
    ) -> dict[str, list[KnowledgeBaseRef]]:
        """複数 document の所属ナレッジベースをまとめて取得する。"""
        unique_document_ids = _unique_optional_sequence(document_ids)
        if not unique_document_ids:
            return {}
        document_filter_sql, document_binds = _oracle_in_predicate(
            "dkb.document_id",
            "document_id",
            unique_document_ids,
        )
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                dkb.document_id,
                kb.knowledge_base_id,
                kb.name
            FROM rag_document_knowledge_bases dkb
            JOIN rag_knowledge_bases kb
              ON kb.knowledge_base_id = dkb.knowledge_base_id
            WHERE {document_filter_sql}
              AND {knowledge_base_access_sql}
            ORDER BY dkb.document_id ASC, kb.name ASC, kb.knowledge_base_id ASC
            """,
                document_filter_sql=document_filter_sql,
                knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
            ),
            _with_tenant_bind(document_binds),
        )
        refs_by_document_id: dict[str, list[KnowledgeBaseRef]] = {
            document_id: [] for document_id in unique_document_ids
        }
        for row in rows:
            document_id = str(row["document_id"])
            refs_by_document_id.setdefault(document_id, []).append(
                KnowledgeBaseRef(id=str(row["knowledge_base_id"]), name=str(row["name"]))
            )
        return refs_by_document_id

    async def _attach_knowledge_base_refs_to_documents(
        self,
        documents: Sequence[DocumentT],
    ) -> list[DocumentT]:
        """DocumentSummary/Detail へ所属 KB 参照を付与する。"""
        if not documents:
            return []
        refs_by_document_id = await self._document_knowledge_base_refs_by_document_id_with_oracle(
            [document.id for document in documents]
        )
        return [
            document.model_copy(
                update={"knowledge_bases": refs_by_document_id.get(document.id, [])}
            )
            for document in documents
        ]

    async def _create_ingestion_job_with_oracle(self, job: IngestionJob) -> IngestionJob:
        """Oracle ingestion job table へ job を作成する。"""

        def operation(connection: OracleConnectionProtocol) -> IngestionJob:
            if _select_document(connection, job.document_id) is None:
                raise KeyError(f"document_id={job.document_id} は存在しません。")
            _execute_ingestion_job_insert(
                connection,
                """
                INSERT INTO rag_ingestion_jobs (
                    job_id,
                    document_id,
                    tenant_id_hash,
                    status,
                    phase,
                    parser_profile,
                    quality_warnings,
                    skip_reason,
                    error_message,
                    attempt_count,
                    max_attempts,
                    queued_at,
                    started_at,
                    finished_at
                ) VALUES (
                    :job_id,
                    :document_id,
                    :tenant_id_hash,
                    :status,
                    :phase,
                    :parser_profile,
                    :quality_warnings,
                    :skip_reason,
                    :error_message,
                    :attempt_count,
                    :max_attempts,
                    :queued_at,
                    :started_at,
                    :finished_at
                )
                """,
                _ingestion_job_binds(job),
            )
            return job

        return await self._run_transaction(operation)

    async def _get_ingestion_job_with_oracle(self, job_id: str) -> IngestionJob | None:
        """Oracle ingestion job table から job を取得する。"""
        rows = await self._fetch_ingestion_job_rows(
            _render_sql(
                """
            SELECT
                j.job_id,
                j.document_id,
                j.status,
                j.phase,
                j.parser_profile,
                j.quality_warnings,
                j.skip_reason,
                j.error_message,
                j.attempt_count,
                j.max_attempts,
                j.queued_at,
                j.started_at,
                j.finished_at
            FROM rag_ingestion_jobs j
            JOIN rag_documents d
              ON d.document_id = j.document_id
            WHERE j.job_id = :job_id
              AND {document_access_sql}
            """,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
            ),
            _with_tenant_bind({"job_id": job_id}),
        )
        return None if not rows else _ingestion_job_from_row(rows[0])

    async def _list_ingestion_jobs_with_oracle(
        self,
        *,
        status: IngestionJobStatus | None,
        limit: int | None,
        offset: int,
        oldest_first: bool = False,
    ) -> list[IngestionJob]:
        """Oracle ingestion job table から job を取得する。"""
        binds: dict[str, object] = {"offset": offset}
        status_clause = ""
        if status is not None:
            binds["ingestion_job_status"] = status.value
            status_clause = "AND j.status = :ingestion_job_status"
        limit_clause = "OFFSET :offset ROWS"
        if limit is not None:
            binds["limit"] = limit
            limit_clause += " FETCH NEXT :limit ROWS ONLY"
        order_clause = (
            "ORDER BY j.queued_at ASC, j.job_id ASC"
            if oldest_first
            else "ORDER BY j.queued_at DESC, j.job_id DESC"
        )
        rows = await self._fetch_ingestion_job_rows(
            _render_sql(
                """
            SELECT
                j.job_id,
                j.document_id,
                j.status,
                j.phase,
                j.parser_profile,
                j.quality_warnings,
                j.skip_reason,
                j.error_message,
                j.attempt_count,
                j.max_attempts,
                j.queued_at,
                j.started_at,
                j.finished_at
            FROM rag_ingestion_jobs j
            JOIN rag_documents d
              ON d.document_id = j.document_id
            WHERE {document_access_sql}
              {status_clause}
            {order_clause}
            {limit_clause}
            """,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                status_clause=status_clause,
                order_clause=order_clause,
                limit_clause=limit_clause,
            ),
            _with_tenant_bind(binds),
        )
        return [_ingestion_job_from_row(row) for row in rows]

    async def _list_document_ingestion_jobs_with_oracle(
        self,
        document_id: str,
        *,
        status: IngestionJobStatus | None,
    ) -> list[IngestionJob]:
        """Oracle ingestion job table から指定 document の job を取得する。"""
        binds: dict[str, object] = {"document_id": document_id}
        status_clause = ""
        if status is not None:
            binds["ingestion_job_status"] = status.value
            status_clause = "AND j.status = :ingestion_job_status"
        rows = await self._fetch_ingestion_job_rows(
            _render_sql(
                """
            SELECT
                j.job_id,
                j.document_id,
                j.status,
                j.phase,
                j.parser_profile,
                j.quality_warnings,
                j.skip_reason,
                j.error_message,
                j.attempt_count,
                j.max_attempts,
                j.queued_at,
                j.started_at,
                j.finished_at
            FROM rag_ingestion_jobs j
            JOIN rag_documents d
              ON d.document_id = j.document_id
            WHERE j.document_id = :document_id
              AND {document_access_sql}
              {status_clause}
            ORDER BY j.queued_at DESC, j.job_id DESC
            """,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                status_clause=status_clause,
            ),
            _with_tenant_bind(binds),
        )
        return [_ingestion_job_from_row(row) for row in rows]

    async def _replace_ingestion_segments_with_oracle(
        self,
        document_id: str,
        segments: Sequence[IngestionSegment],
    ) -> list[IngestionSegment]:
        """Oracle segment checkpoint table を document scope で置換する。"""
        normalized_segments = [
            segment.model_copy(update={"document_id": document_id}) for segment in segments
        ]

        def operation(connection: OracleConnectionProtocol) -> list[IngestionSegment]:
            if _select_document(connection, document_id) is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            _execute(
                connection,
                _render_sql(
                    """
                DELETE FROM rag_ingestion_segments
                WHERE document_id = :document_id
                  AND EXISTS (
                      SELECT 1
                      FROM rag_documents d
                      WHERE d.document_id = rag_ingestion_segments.document_id
                        AND {document_access_sql}
                  )
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"document_id": document_id}),
            )
            if normalized_segments:
                _executemany(
                    connection,
                    """
                    INSERT INTO rag_ingestion_segments (
                        segment_id,
                        document_id,
                        tenant_id_hash,
                        status,
                        parser_backend,
                        parser_profile,
                        page_start,
                        page_end,
                        attempt_count,
                        artifact_path,
                        error_code,
                        error_message
                    ) VALUES (
                        :segment_id,
                        :document_id,
                        :tenant_id_hash,
                        :status,
                        :parser_backend,
                        :parser_profile,
                        :page_start,
                        :page_end,
                        :attempt_count,
                        :artifact_path,
                        :error_code,
                        :error_message
                    )
                    """,
                    [_ingestion_segment_binds(segment) for segment in normalized_segments],
                )
            return list(normalized_segments)

        return await self._run_transaction(operation)

    async def _list_ingestion_segments_with_oracle(
        self,
        document_id: str,
    ) -> list[IngestionSegment]:
        """Oracle segment checkpoint table から document scope で取得する。"""
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                s.segment_id,
                s.document_id,
                s.status,
                s.parser_backend,
                s.parser_profile,
                s.page_start,
                s.page_end,
                s.attempt_count,
                s.artifact_path,
                s.error_code,
                s.error_message
            FROM rag_ingestion_segments s
            JOIN rag_documents d
              ON d.document_id = s.document_id
            WHERE s.document_id = :document_id
              AND {document_access_sql}
            ORDER BY
                COALESCE(s.page_start, 0) ASC,
                COALESCE(s.page_end, 0) ASC,
                s.segment_id ASC
            """,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
            ),
            _with_tenant_bind({"document_id": document_id}),
        )
        return [_ingestion_segment_from_row(row) for row in rows]

    async def _update_ingestion_segment_with_oracle(
        self,
        segment_id: str,
        *,
        status: str | None,
        attempt_count: int | None,
        artifact_path: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> IngestionSegment | None:
        """Oracle segment checkpoint table の状態を更新する。"""
        updates: dict[str, object] = {}
        if status is not None:
            updates["status"] = status
        if attempt_count is not None:
            updates["attempt_count"] = attempt_count
        if artifact_path is not None:
            updates["artifact_path"] = artifact_path
        if error_code is not None:
            updates["error_code"] = error_code
        if error_message is not None:
            updates["error_message"] = error_message
        if not updates:
            rows = await self._fetch_ingestion_segment_by_id(segment_id)
            return None if not rows else _ingestion_segment_from_row(rows[0])
        set_sql = ", ".join(f"{column} = :{column}" for column in updates)
        binds = {"segment_id": segment_id, **updates}

        def operation(connection: OracleConnectionProtocol) -> IngestionSegment | None:
            _execute(
                connection,
                _render_sql(
                    """
                UPDATE rag_ingestion_segments
                SET {set_sql},
                    updated_at = SYSTIMESTAMP
                WHERE segment_id = :segment_id
                  AND EXISTS (
                      SELECT 1
                      FROM rag_documents d
                      WHERE d.document_id = rag_ingestion_segments.document_id
                        AND {document_access_sql}
                  )
                """,
                    set_sql=set_sql,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind(binds),
            )
            rows = _fetch_all(
                connection,
                _render_sql(
                    """
                SELECT
                    s.segment_id,
                    s.document_id,
                    s.status,
                    s.parser_backend,
                    s.parser_profile,
                    s.page_start,
                    s.page_end,
                    s.attempt_count,
                    s.artifact_path,
                    s.error_code,
                    s.error_message
                FROM rag_ingestion_segments s
                JOIN rag_documents d
                  ON d.document_id = s.document_id
                WHERE s.segment_id = :segment_id
                  AND {document_access_sql}
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"segment_id": segment_id}),
            )
            return None if not rows else _ingestion_segment_from_row(rows[0])

        return await self._run_transaction(operation)

    async def _fetch_ingestion_segment_by_id(
        self,
        segment_id: str,
    ) -> list[dict[str, object]]:
        return await self._fetch_all(
            _render_sql(
                """
            SELECT
                s.segment_id,
                s.document_id,
                s.status,
                s.parser_backend,
                s.parser_profile,
                s.page_start,
                s.page_end,
                s.attempt_count,
                s.artifact_path,
                s.error_code,
                s.error_message
            FROM rag_ingestion_segments s
            JOIN rag_documents d
              ON d.document_id = s.document_id
            WHERE s.segment_id = :segment_id
              AND {document_access_sql}
            """,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
            ),
            _with_tenant_bind({"segment_id": segment_id}),
        )

    async def _count_ingestion_jobs_with_oracle(
        self,
        *,
        status: IngestionJobStatus | None,
    ) -> int:
        """Oracle ingestion job table の件数を取得する。"""
        binds: dict[str, object] = {}
        status_clause = ""
        if status is not None:
            binds["ingestion_job_status"] = status.value
            status_clause = "AND j.status = :ingestion_job_status"
        row = await self._fetch_one(
            _render_sql(
                """
            SELECT COUNT(*) AS count_value
            FROM rag_ingestion_jobs j
            JOIN rag_documents d
              ON d.document_id = j.document_id
            WHERE {document_access_sql}
              {status_clause}
            """,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
                status_clause=status_clause,
            ),
            _with_tenant_bind(binds),
        )
        return _row_count_value(row)

    async def _recover_stale_ingestion_jobs_with_oracle(
        self,
        *,
        stale_before: datetime,
        limit: int,
    ) -> list[IngestionJob]:
        """stale RUNNING job を QUEUED/FAILED へ戻し、固着した文書状態も復旧する。

        サブプロセスがクラッシュした場合、job は RUNNING、文書は INGESTING/INDEXING
        のまま残る。job だけ QUEUED へ戻しても文書が INGESTING のままだと、再投入された
        job が取込中ガード(409)で必ず失敗し、永久に固着する。これを防ぐため:

        - job を再キューする際は、文書も再実行可能な状態(EXTRACT→UPLOADED /
          INDEX→REVIEW)へ戻す。
        - 試行上限超過で job を失敗させる際は、文書も ERROR へ戻す。
        - QUEUED/RUNNING の job が一つも無いのに INGESTING/INDEXING で取り残された
          文書(過去のデッドロックで FAILED になった job しか持たない等)は ERROR へ
          戻し、利用者が再試行できるようにする。
        """
        now = datetime.now(UTC)
        stale_error_message = "取込ジョブが規定回数を超えて停止しました。"
        orphan_error_message = "取込処理が中断されたため停止しました。再実行してください。"

        def operation(connection: OracleConnectionProtocol) -> list[IngestionJob]:
            rows = _fetch_ingestion_job_rows(
                connection,
                _render_sql(
                    """
                SELECT
                    j.job_id,
                    j.document_id,
                    j.status,
                    j.phase,
                    j.parser_profile,
                    j.quality_warnings,
                    j.skip_reason,
                    j.error_message,
                    j.attempt_count,
                    j.max_attempts,
                    j.queued_at,
                    j.started_at,
                    j.finished_at
                FROM rag_ingestion_jobs j
                JOIN rag_documents d
                  ON d.document_id = j.document_id
                WHERE j.status = 'RUNNING'
                  AND COALESCE(j.started_at, j.queued_at) < :stale_before
                  AND {document_access_sql}
                ORDER BY COALESCE(j.started_at, j.queued_at) ASC, j.job_id ASC
                FETCH FIRST :limit ROWS ONLY
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"stale_before": stale_before, "limit": limit}),
                default_max_attempts=self._settings.ingestion_job_max_attempts,
            )
            stale_jobs = [_ingestion_job_from_row(row) for row in rows]
            for job in stale_jobs:
                if job.attempt_count >= job.max_attempts:
                    _execute(
                        connection,
                        _render_sql(
                            """
                        UPDATE rag_ingestion_jobs
                        SET status = 'FAILED',
                            error_message = :error_message,
                            finished_at = :finished_at
                        WHERE job_id = :job_id
                          AND EXISTS (
                              SELECT 1
                              FROM rag_documents d
                              WHERE d.document_id = rag_ingestion_jobs.document_id
                                AND {document_access_sql}
                          )
                        """,
                            document_access_sql=_oracle_access_predicate_sql(alias="d"),
                        ),
                        _with_tenant_bind(
                            {
                                "job_id": job.id,
                                "error_message": stale_error_message,
                                "finished_at": now,
                            }
                        ),
                    )
                    # 試行上限超過: 文書を ERROR へ戻し固着を解消する。
                    self._reset_document_status_inline(
                        connection,
                        document_id=job.document_id,
                        status=FileStatus.ERROR,
                        error_message=stale_error_message,
                    )
                    continue
                _execute(
                    connection,
                    _render_sql(
                        """
                    UPDATE rag_ingestion_jobs
                    SET status = 'QUEUED',
                        error_message = NULL,
                        started_at = NULL,
                        finished_at = NULL
                    WHERE job_id = :job_id
                      AND EXISTS (
                          SELECT 1
                          FROM rag_documents d
                          WHERE d.document_id = rag_ingestion_jobs.document_id
                            AND {document_access_sql}
                      )
                    """,
                        document_access_sql=_oracle_access_predicate_sql(alias="d"),
                    ),
                    _with_tenant_bind({"job_id": job.id}),
                )
                # 再キュー: 文書も再実行可能な状態へ戻し、取込中ガードでの再失敗を防ぐ。
                self._reset_document_status_inline(
                    connection,
                    document_id=job.document_id,
                    status=(
                        FileStatus.REVIEW
                        if job.phase == IngestionJobPhase.INDEX
                        else FileStatus.UPLOADED
                    ),
                    error_message=None,
                )

            # QUEUED/RUNNING の job が無いのに INGESTING/INDEXING で取り残された文書を
            # ERROR へ戻す(過去のデッドロックで固着した文書の自己復旧)。
            orphan_rows = _fetch_all(
                connection,
                _render_sql(
                    """
                SELECT d.document_id
                FROM rag_documents d
                WHERE d.status IN ('INGESTING', 'INDEXING')
                  AND {document_access_sql}
                  AND NOT EXISTS (
                      SELECT 1
                      FROM rag_ingestion_jobs j
                      WHERE j.document_id = d.document_id
                        AND j.status IN ('QUEUED', 'RUNNING')
                  )
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({}),
            )
            for orphan_row in orphan_rows:
                orphan_document_id = orphan_row.get("document_id")
                if not isinstance(orphan_document_id, str):
                    continue
                self._reset_document_status_inline(
                    connection,
                    document_id=orphan_document_id,
                    status=FileStatus.ERROR,
                    error_message=orphan_error_message,
                )
            return stale_jobs

        return await self._run_transaction(operation)

    @staticmethod
    def _reset_document_status_inline(
        connection: OracleConnectionProtocol,
        *,
        document_id: str,
        status: FileStatus,
        error_message: str | None,
    ) -> None:
        """recovery トランザクション内で固着文書の状態を復旧する。

        INGESTING/INDEXING に取り残された文書だけを対象にし、ERROR へ戻す場合は
        中途半端なチャンク/抽出を破棄する。再実行可能状態(UPLOADED/REVIEW)へ戻す
        場合は再利用のため抽出を保持する。
        """
        if status == FileStatus.ERROR:
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
                SET status = :status,
                    error_message = :error_message,
                    extraction = NULL
                WHERE document_id = :document_id
                  AND status IN ('INGESTING', 'INDEXING')
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
            return
        _execute(
            connection,
            _render_sql(
                """
            UPDATE rag_documents
            SET status = :status,
                error_message = :error_message
            WHERE document_id = :document_id
              AND status IN ('INGESTING', 'INDEXING')
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

    async def _claim_ingestion_job_with_oracle(
        self,
        job_id: str,
        *,
        started_at: datetime,
    ) -> IngestionJob | None:
        """QUEUED job をロックして RUNNING へ遷移する。"""

        def operation(connection: OracleConnectionProtocol) -> IngestionJob | None:
            rows = _fetch_ingestion_job_rows(
                connection,
                _render_sql(
                    """
                SELECT
                    j.job_id,
                    j.document_id,
                    j.status,
                    j.phase,
                    j.parser_profile,
                    j.quality_warnings,
                    j.skip_reason,
                    j.error_message,
                    j.attempt_count,
                    j.max_attempts,
                    j.queued_at,
                    j.started_at,
                    j.finished_at
                FROM rag_ingestion_jobs j
                JOIN rag_documents d
                  ON d.document_id = j.document_id
                WHERE j.job_id = :job_id
                  AND j.status = 'QUEUED'
                  AND {document_access_sql}
                FOR UPDATE SKIP LOCKED
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"job_id": job_id}),
                default_max_attempts=self._settings.ingestion_job_max_attempts,
            )
            if not rows:
                return None
            job = _ingestion_job_from_row(rows[0])
            attempt_count = job.attempt_count + 1
            _execute(
                connection,
                _render_sql(
                    """
                UPDATE rag_ingestion_jobs
                SET status = 'RUNNING',
                    attempt_count = :attempt_count,
                    started_at = :started_at,
                    error_message = NULL,
                    finished_at = NULL
                WHERE job_id = :job_id
                  AND EXISTS (
                      SELECT 1
                      FROM rag_documents d
                      WHERE d.document_id = rag_ingestion_jobs.document_id
                        AND {document_access_sql}
                  )
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind(
                    {
                        "job_id": job_id,
                        "attempt_count": attempt_count,
                        "started_at": started_at,
                    }
                ),
            )
            return job.model_copy(
                update={
                    "status": IngestionJobStatus.RUNNING,
                    "attempt_count": attempt_count,
                    "started_at": started_at,
                    "error_message": None,
                    "finished_at": None,
                }
            )

        return await self._run_transaction(operation)

    async def _update_ingestion_job_with_oracle(
        self,
        job_id: str,
        *,
        status: IngestionJobStatus | None,
        error_message: str | None,
        attempt_count: int | None,
        max_attempts: int | None,
        started_at: datetime | None,
        finished_at: datetime | None,
    ) -> IngestionJob | None:
        """Oracle ingestion job table の状態を更新する。"""
        updates: dict[str, object] = {}
        if status is not None:
            updates["status"] = status.value
        if error_message is not None:
            updates["error_message"] = error_message
        if attempt_count is not None:
            updates["attempt_count"] = attempt_count
        if max_attempts is not None:
            updates["max_attempts"] = max_attempts
        if started_at is not None:
            updates["started_at"] = started_at
        if finished_at is not None:
            updates["finished_at"] = finished_at
        if not updates:
            return await self.get_ingestion_job(job_id)
        set_sql = ", ".join(f"{column} = :{column}" for column in updates)
        binds = {"job_id": job_id, **updates}

        def operation(connection: OracleConnectionProtocol) -> IngestionJob | None:
            update_sql = _render_sql(
                """
            UPDATE rag_ingestion_jobs
            SET {set_sql}
            WHERE job_id = :job_id
              AND EXISTS (
                  SELECT 1
                  FROM rag_documents d
                  WHERE d.document_id = rag_ingestion_jobs.document_id
                    AND {document_access_sql}
              )
            """,
                set_sql=set_sql,
                document_access_sql=_oracle_access_predicate_sql(alias="d"),
            )
            try:
                _execute(connection, update_sql, _with_tenant_bind(binds))
            except Exception as exc:
                is_missing_max_attempts = _is_missing_ingestion_job_max_attempts_error(exc)
                if "max_attempts" not in updates or not is_missing_max_attempts:
                    raise
                legacy_updates = {
                    column: value for column, value in updates.items() if column != "max_attempts"
                }
                if legacy_updates:
                    legacy_set_sql = ", ".join(f"{column} = :{column}" for column in legacy_updates)
                    legacy_binds = {"job_id": job_id, **legacy_updates}
                    _execute(
                        connection,
                        _render_sql(
                            """
                        UPDATE rag_ingestion_jobs
                        SET {set_sql}
                        WHERE job_id = :job_id
                          AND EXISTS (
                              SELECT 1
                              FROM rag_documents d
                              WHERE d.document_id = rag_ingestion_jobs.document_id
                                AND {document_access_sql}
                          )
                        """,
                            set_sql=legacy_set_sql,
                            document_access_sql=_oracle_access_predicate_sql(alias="d"),
                        ),
                        _with_tenant_bind(legacy_binds),
                    )
            rows = _fetch_ingestion_job_rows(
                connection,
                _render_sql(
                    """
                SELECT
                    j.job_id,
                    j.document_id,
                    j.status,
                    j.phase,
                    j.parser_profile,
                    j.quality_warnings,
                    j.skip_reason,
                    j.error_message,
                    j.attempt_count,
                    j.max_attempts,
                    j.queued_at,
                    j.started_at,
                    j.finished_at
                FROM rag_ingestion_jobs j
                JOIN rag_documents d
                  ON d.document_id = j.document_id
                WHERE j.job_id = :job_id
                  AND {document_access_sql}
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"job_id": job_id}),
                default_max_attempts=self._settings.ingestion_job_max_attempts,
            )
            return None if not rows else _ingestion_job_from_row(rows[0])

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
        summaries = await self._attach_knowledge_base_refs_to_documents(
            [_to_document_summary(_stored_document_from_row(rows[0]))]
        )
        return summaries[0]

    async def _list_documents_with_oracle(
        self,
        status: FileStatus | None,
        query: str | None,
        limit: int | None,
        offset: int,
        knowledge_base_id: str | None,
    ) -> list[DocumentSummary]:
        """Oracle document table から一覧取得する。"""
        where_sql, binds = _oracle_document_where(
            status=status,
            query=query,
            knowledge_base_id=knowledge_base_id,
        )
        binds["offset"] = offset
        limit_clause = "OFFSET :offset ROWS"
        if limit is not None:
            binds["limit"] = limit
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
        summaries = [_to_document_summary(_stored_document_from_row(row)) for row in rows]
        return await self._attach_knowledge_base_refs_to_documents(summaries)

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
        knowledge_base_id: str | None,
    ) -> int:
        """Oracle document table の件数を取得する。"""
        where_sql, binds = _oracle_document_where(
            status=status,
            query=query,
            knowledge_base_id=knowledge_base_id,
        )
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
            SELECT
                c.document_id,
                c.chunk_id,
                c.chunk_index,
                c.metadata_json
            FROM rag_chunks c
            JOIN rag_documents d ON d.document_id = c.document_id
            WHERE {where_sql}
            """,
                where_sql=where_sql,
            ),
            binds,
        )
        return [_chunk_metadata_from_row(row) for row in rows]

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

    async def _list_document_chunks_with_oracle(
        self,
        document_id: str,
    ) -> list[DocumentChunkView]:
        """Oracle chunk/vector table から embedding を除いた chunk view を返す。"""
        rows = await self._fetch_all(
            _render_sql(
                """
            SELECT
                c.document_id,
                c.chunk_id,
                c.chunk_text,
                c.metadata_json,
                c.chunk_index
            FROM rag_chunks c
            JOIN rag_documents d ON d.document_id = c.document_id
            WHERE c.document_id = :document_id
              AND {access_predicate}
            ORDER BY c.chunk_index ASC, c.chunk_id ASC
            """,
                access_predicate=_oracle_access_predicate_sql(alias="d"),
            ),
            _with_tenant_bind({"document_id": document_id}),
        )
        return [_document_chunk_view_from_row(row) for row in rows]

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
        if row is None:
            return None
        details = await self._attach_knowledge_base_refs_to_documents(
            [_to_document_detail(_stored_document_from_row(row))]
        )
        return details[0]

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
            return _to_document_detail(document).model_copy(
                update={
                    "knowledge_bases": _select_document_knowledge_base_refs(
                        connection,
                        document_id,
                    )
                }
            )

        return await self._run_transaction(operation)

    async def _delete_document_with_oracle(self, document_id: str) -> bool:
        """Oracle document table と関連 chunk/vector/ingestion 行を同一 transaction で削除する。"""

        def operation(connection: OracleConnectionProtocol) -> bool:
            existing = _select_document(connection, document_id)
            if existing is None:
                return False
            graph_entity_ids = _select_graph_entity_ids_for_document(connection, document_id)
            _delete_graph_rows_for_document(
                connection,
                document_id=document_id,
                entity_ids=graph_entity_ids,
            )
            _execute(
                connection,
                _render_sql(
                    """
                UPDATE rag_documents
                SET duplicate_of_document_id = NULL
                WHERE duplicate_of_document_id = :document_id
                  AND {access_predicate}
                """,
                    access_predicate=_oracle_access_predicate_sql(),
                ),
                _with_tenant_bind({"document_id": document_id}),
            )
            _execute(
                connection,
                _render_sql(
                    """
                DELETE FROM rag_ingestion_segments
                WHERE document_id = :document_id
                  AND EXISTS (
                      SELECT 1
                      FROM rag_documents d
                      WHERE d.document_id = rag_ingestion_segments.document_id
                        AND {access_predicate}
                  )
                """,
                    access_predicate=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"document_id": document_id}),
            )
            _execute(
                connection,
                _render_sql(
                    """
                DELETE FROM rag_ingestion_jobs
                WHERE document_id = :document_id
                  AND status <> 'RUNNING'
                  AND EXISTS (
                      SELECT 1
                      FROM rag_documents d
                      WHERE d.document_id = rag_ingestion_jobs.document_id
                        AND {access_predicate}
                  )
                """,
                    access_predicate=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"document_id": document_id}),
            )
            running_jobs = _fetch_all(
                connection,
                _render_sql(
                    """
                SELECT j.job_id
                FROM rag_ingestion_jobs j
                JOIN rag_documents d
                  ON d.document_id = j.document_id
                WHERE j.document_id = :document_id
                  AND j.status = 'RUNNING'
                  AND {document_access_sql}
                """,
                    document_access_sql=_oracle_access_predicate_sql(alias="d"),
                ),
                _with_tenant_bind({"document_id": document_id}),
            )
            if running_jobs:
                raise DocumentDeleteBlockedByRunningIngestionError(
                    "取込ジョブが実行中のため削除できません。先にキャンセルしてください。"
                )
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
            return _to_document_detail(document).model_copy(
                update={
                    "knowledge_bases": _select_document_knowledge_base_refs(
                        connection,
                        document_id,
                    )
                }
            )

        return await self._run_transaction(operation)

    @staticmethod
    def _chunk_insert_rows(
        document_id: str,
        document: StoredDocument,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        chunk_set_id: str | None = None,
    ) -> list[dict[str, object]]:
        """rag_chunks へ挿入する bind row を構築する。chunk_set_id=None は未タグ(後方互換)。

        chunk_id は chunk_set_id 指定時 ``document:chunk_set:index`` で chunk_set 間衝突を避ける。
        None のときは現行どおり ``document:index``。
        """

        def chunk_id_for(index: int) -> str:
            if chunk_set_id is not None:
                return f"{document_id}:{chunk_set_id}:{index}"
            return f"{document_id}:{index}"

        return [
            {
                "chunk_id": chunk_id_for(chunk.index),
                "document_id": document_id,
                "tenant_id_hash": document.tenant_id_hash,
                "chunk_index": chunk.index,
                "chunk_text": chunk.text,
                "metadata_json": _json_dumps(
                    {
                        "document_id": document_id,
                        "chunk_id": chunk_id_for(chunk.index),
                        "chunk_index": chunk.index,
                        "start_offset": chunk.start_offset,
                        "end_offset": chunk.end_offset,
                        **chunk.metadata,
                    }
                ),
                "embedding": _to_vector_bind(embedding),
                "chunk_set_id": chunk_set_id,
            }
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]

    @staticmethod
    def _retrieved_chunks_from_insert_rows(
        document_id: str,
        document: StoredDocument,
        rows: Sequence[Mapping[str, object]],
    ) -> list[RetrievedChunk]:
        """保存直後の chunk row を API 返却用 schema へ変換する。"""
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
            rows = self._chunk_insert_rows(document_id, document, chunks, embeddings)
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
            return self._retrieved_chunks_from_insert_rows(document_id, document, rows)

        return await self._run_transaction(operation)

    async def _save_index_with_oracle(
        self,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        chunk_set_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """抽出 payload と chunk/vector を同一 Oracle transaction で置換する。

        chunk_set_id を渡すと chunk 置換を **その chunk_set に限定**(他 chunk_set の chunk は
        残す)し、挿入 chunk をその chunk_set でタグ付けする。None は文書の全 chunk を置換し
        未タグで保存する(現行挙動・後方互換)。
        """

        def operation(connection: OracleConnectionProtocol) -> list[RetrievedChunk]:
            document = _select_document(connection, document_id)
            if document is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
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
            # chunk_set_id 指定時はその chunk_set の chunk だけ置換する(複数 chunk_set 共存可)。
            chunk_set_clause = (
                "AND chunk_set_id = :chunk_set_id" if chunk_set_id is not None else ""
            )
            delete_binds: dict[str, object] = {"document_id": document_id}
            if chunk_set_id is not None:
                delete_binds["chunk_set_id"] = chunk_set_id
            _execute(
                connection,
                _render_sql(
                    """
                DELETE FROM rag_chunks
                WHERE document_id = :document_id
                  {chunk_set_clause}
                """,
                    chunk_set_clause=chunk_set_clause,
                ),
                delete_binds,
            )
            rows = self._chunk_insert_rows(
                document_id, document, chunks, embeddings, chunk_set_id=chunk_set_id
            )
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
                        embedding,
                        chunk_set_id
                    ) VALUES (
                        :chunk_id,
                        :document_id,
                        :tenant_id_hash,
                        :chunk_index,
                        :chunk_text,
                        :metadata_json,
                        :embedding,
                        :chunk_set_id
                    )
                    """,
                    rows,
                )
            return self._retrieved_chunks_from_insert_rows(document_id, document, rows)

        return await self._run_transaction(operation)

    async def _replace_document_graph_index_with_oracle(
        self,
        document_id: str,
        graph_index: GraphIndex,
    ) -> None:
        """Oracle GraphRAG-lite tables の document scope を置換する。"""

        def operation(connection: OracleConnectionProtocol) -> None:
            if _select_document(connection, document_id) is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            existing_entity_ids = _select_graph_entity_ids_for_document(connection, document_id)
            _delete_graph_rows_for_document(
                connection,
                document_id=document_id,
                entity_ids=existing_entity_ids,
            )
            if graph_index.entities:
                _executemany(
                    connection,
                    """
                    INSERT INTO rag_graph_entities (
                        entity_id,
                        tenant_id_hash,
                        knowledge_base_id,
                        canonical_name,
                        entity_type,
                        description,
                        confidence,
                        source_document_ids
                    ) VALUES (
                        :entity_id,
                        :tenant_id_hash,
                        :knowledge_base_id,
                        :canonical_name,
                        :entity_type,
                        :description,
                        :confidence,
                        :source_document_ids
                    )
                    """,
                    [_graph_entity_binds(entity) for entity in graph_index.entities],
                )
            if graph_index.relationships:
                _executemany(
                    connection,
                    """
                    INSERT INTO rag_graph_relationships (
                        relationship_id,
                        tenant_id_hash,
                        knowledge_base_id,
                        source_entity_id,
                        target_entity_id,
                        relationship_type,
                        description,
                        confidence,
                        source_document_ids
                    ) VALUES (
                        :relationship_id,
                        :tenant_id_hash,
                        :knowledge_base_id,
                        :source_entity_id,
                        :target_entity_id,
                        :relationship_type,
                        :description,
                        :confidence,
                        :source_document_ids
                    )
                    """,
                    [
                        _graph_relationship_binds(relationship)
                        for relationship in graph_index.relationships
                    ],
                )
            if graph_index.claims:
                _executemany(
                    connection,
                    """
                    INSERT INTO rag_graph_claims (
                        claim_id,
                        tenant_id_hash,
                        knowledge_base_id,
                        entity_id,
                        claim_text,
                        confidence,
                        source_document_id,
                        source_chunk_id
                    ) VALUES (
                        :claim_id,
                        :tenant_id_hash,
                        :knowledge_base_id,
                        :entity_id,
                        :claim_text,
                        :confidence,
                        :source_document_id,
                        :source_chunk_id
                    )
                    """,
                    [_graph_claim_binds(claim) for claim in graph_index.claims],
                )
            if graph_index.community_summaries:
                _executemany(
                    connection,
                    """
                    INSERT INTO rag_graph_community_summaries (
                        community_id,
                        tenant_id_hash,
                        knowledge_base_id,
                        level_no,
                        title,
                        summary_text,
                        entity_ids,
                        source_document_ids
                    ) VALUES (
                        :community_id,
                        :tenant_id_hash,
                        :knowledge_base_id,
                        :level_no,
                        :title,
                        :summary_text,
                        :entity_ids,
                        :source_document_ids
                    )
                    """,
                    [
                        _graph_community_summary_binds(summary)
                        for summary in graph_index.community_summaries
                    ],
                )
            if graph_index.entity_chunk_links:
                _executemany(
                    connection,
                    """
                    INSERT INTO rag_graph_entity_chunks (
                        entity_id,
                        chunk_id,
                        document_id,
                        tenant_id_hash,
                        relevance_score
                    ) VALUES (
                        :entity_id,
                        :chunk_id,
                        :document_id,
                        :tenant_id_hash,
                        :relevance_score
                    )
                    """,
                    [
                        _graph_entity_chunk_link_binds(link)
                        for link in graph_index.entity_chunk_links
                    ],
                )

        await self._run_transaction(operation)

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

    async def _fetch_ingestion_job_rows(
        self, statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        """max_attempts 列が未適用の旧 queue table でも ingestion job 行を取得する。"""
        try:
            return await self._fetch_all(statement, binds)
        except Exception as exc:
            if not _is_missing_ingestion_job_max_attempts_error(exc):
                raise
            legacy_binds = _with_default_max_attempts_bind(
                binds or {},
                self._settings.ingestion_job_max_attempts,
            )
            return await self._fetch_all(
                _legacy_ingestion_job_max_attempts_select_sql(statement),
                legacy_binds,
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

    def connection_pool(self) -> OraclePoolProtocol:
        """共有 connection pool を返す(Select AI クライアント等が再利用する)。"""
        return self._pool()

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
        normalized = _normalize_sql(statement)
        cursor.execute(normalized, _binds_for_sql(normalized, binds))
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
        normalized = _normalize_sql(statement)
        cursor.execute(normalized, _binds_for_sql(normalized, binds))
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


def _select_graph_entity_ids_for_document(
    connection: OracleConnectionProtocol,
    document_id: str,
) -> list[str]:
    """指定 document に紐づく既存 graph entity id を取得する。"""
    rows = _fetch_all(
        connection,
        _render_sql(
            """
        SELECT DISTINCT entity_id
        FROM (
            SELECT entity_id
            FROM rag_graph_entity_chunks
            WHERE document_id = :document_id
              AND {graph_access_sql}
            UNION
            SELECT entity_id
            FROM rag_graph_claims
            WHERE source_document_id = :document_id
              AND {graph_access_sql}
            UNION
            SELECT entity_id
            FROM rag_graph_entities
            WHERE JSON_EXISTS(
                      source_document_ids,
                      '$[*]?(@ == $document_id)'
                      PASSING :document_id AS "document_id"
                  )
              AND {graph_access_sql}
        )
        """,
            graph_access_sql=_oracle_tenant_predicate(),
        ),
        _with_tenant_bind({"document_id": document_id}),
    )
    return [str(row["entity_id"]) for row in rows if row.get("entity_id")]


def _delete_graph_rows_for_document(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    entity_ids: Sequence[str],
) -> None:
    """指定 document の GraphRAG-lite rows を FK 順に削除する。"""
    unique_entity_ids = _unique_optional_sequence(entity_ids)
    if unique_entity_ids:
        source_sql, source_binds = _oracle_in_predicate(
            "source_entity_id",
            "graph_source_entity_id",
            unique_entity_ids,
        )
        target_sql, target_binds = _oracle_in_predicate(
            "target_entity_id",
            "graph_target_entity_id",
            unique_entity_ids,
        )
        _execute(
            connection,
            _render_sql(
                """
            DELETE FROM rag_graph_relationships
            WHERE {graph_access_sql}
              AND ({source_sql} OR {target_sql})
            """,
                graph_access_sql=_oracle_tenant_predicate(),
                source_sql=source_sql,
                target_sql=target_sql,
            ),
            _with_tenant_bind({**source_binds, **target_binds}),
        )
    _execute(
        connection,
        _render_sql(
            """
        DELETE FROM rag_graph_entity_chunks
        WHERE document_id = :document_id
          AND {graph_access_sql}
        """,
            graph_access_sql=_oracle_tenant_predicate(),
        ),
        _with_tenant_bind({"document_id": document_id}),
    )
    _execute(
        connection,
        _render_sql(
            """
        DELETE FROM rag_graph_claims
        WHERE source_document_id = :document_id
          AND {graph_access_sql}
        """,
            graph_access_sql=_oracle_tenant_predicate(),
        ),
        _with_tenant_bind({"document_id": document_id}),
    )
    _execute(
        connection,
        _render_sql(
            """
        DELETE FROM rag_graph_community_summaries
        WHERE JSON_EXISTS(
                  source_document_ids,
                  '$[*]?(@ == $document_id)'
                  PASSING :document_id AS "document_id"
              )
          AND {graph_access_sql}
        """,
            graph_access_sql=_oracle_tenant_predicate(),
        ),
        _with_tenant_bind({"document_id": document_id}),
    )
    if unique_entity_ids:
        entity_sql, entity_binds = _oracle_in_predicate(
            "entity_id",
            "graph_entity_id",
            unique_entity_ids,
        )
        _execute(
            connection,
            _render_sql(
                """
            DELETE FROM rag_graph_entities
            WHERE {entity_sql}
              AND {graph_access_sql}
            """,
                entity_sql=entity_sql,
                graph_access_sql=_oracle_tenant_predicate(),
            ),
            _with_tenant_bind(entity_binds),
        )


def _search_audit_binds(event: Mapping[str, object]) -> dict[str, object]:
    """RagSearchAuditEvent JSON を Oracle bind 値へ変換する。"""
    return {
        "event_type": _audit_str(event, "event_type", "rag.search"),
        "trace_id": _audit_str(event, "trace_id", ""),
        "request_id": _audit_optional_str(event, "request_id"),
        "tenant_id_hash": _audit_optional_str(event, "tenant_id_hash"),
        "user_id_hash": _audit_optional_str(event, "user_id_hash"),
        "outcome": _audit_str(event, "outcome", "error"),
        "search_mode": _audit_str(event, "mode", "hybrid"),
        "query_hash": _audit_str(event, "query_hash", ""),
        "query_chars": _audit_int(event, "query_chars"),
        "filter_keys": _audit_json(event.get("filter_keys", [])),
        "memory_plan_id": _audit_optional_str(event, "memory_plan_id"),
        "top_k": _audit_optional_int(event, "top_k"),
        "rerank_top_n": _audit_optional_int(event, "rerank_top_n"),
        "query_variant_count": _audit_int(event, "query_variant_count", default=1),
        "guardrail_codes": _audit_json(event.get("guardrail_codes", [])),
        "guardrail_severities": _audit_json(event.get("guardrail_severities", [])),
        "retrieved_count": _audit_int(event, "retrieved_count"),
        "reranked_count": _audit_int(event, "reranked_count"),
        "deduplicated_count": _audit_int(event, "deduplicated_count"),
        "context_diversified_count": _audit_int(event, "context_diversified_count"),
        "context_group_expanded_count": _audit_int(event, "context_group_expanded_count"),
        "context_expanded_count": _audit_int(event, "context_expanded_count"),
        "context_adaptive_expanded_count": _audit_int(
            event,
            "context_adaptive_expanded_count",
        ),
        "context_dependency_promoted_count": _audit_int(
            event,
            "context_dependency_promoted_count",
        ),
        "context_compressed_count": _audit_int(event, "context_compressed_count"),
        "context_compression_saved_chars": _audit_int(
            event,
            "context_compression_saved_chars",
        ),
        "agent_memory_retrieved_count": _audit_int(
            event,
            "agent_memory_retrieved_count",
        ),
        "agent_memory_writeback_count": _audit_int(
            event,
            "agent_memory_writeback_count",
        ),
        "agent_memory_writeback_status": _audit_str(
            event,
            "agent_memory_writeback_status",
            "skipped",
        ),
        "evidence_count": _audit_int(event, "evidence_count"),
        "support_count": _audit_int(event, "support_count"),
        "structure_count": _audit_int(event, "structure_count"),
        "history_count": _audit_int(event, "history_count"),
        "resolver_rejected_count": _audit_int(event, "resolver_rejected_count"),
        "insufficient_context_count": _audit_int(event, "insufficient_context_count"),
        "citation_count": _audit_int(event, "citation_count"),
        "context_chars": _audit_int(event, "context_chars"),
        "context_window_chars": _audit_optional_int(event, "context_window_chars"),
        "document_ids": _audit_json(event.get("document_ids", [])),
        "knowledge_base_ids": _audit_json(event.get("knowledge_base_ids", [])),
        "config_fingerprint": _audit_optional_str(event, "config_fingerprint"),
        "elapsed_ms": _audit_float(event, "elapsed_ms"),
        "error_stage": _audit_optional_str(event, "error_stage"),
        "error_type": _audit_optional_str(event, "error_type"),
    }


def _ingestion_audit_binds(event: Mapping[str, object]) -> dict[str, object]:
    """RagIngestionAuditEvent JSON を Oracle bind 値へ変換する。"""
    return {
        "event_type": _audit_str(event, "event_type", "rag.ingestion"),
        "trace_id": _audit_str(event, "trace_id", ""),
        "request_id": _audit_optional_str(event, "request_id"),
        "tenant_id_hash": _audit_optional_str(event, "tenant_id_hash"),
        "user_id_hash": _audit_optional_str(event, "user_id_hash"),
        "document_id": _audit_str(event, "document_id", ""),
        "outcome": _audit_str(event, "outcome", "error"),
        "source_sha256": _audit_str(event, "source_sha256", ""),
        "source_bytes": _audit_int(event, "source_bytes"),
        "document_type": _audit_optional_str(event, "document_type"),
        "extraction_confidence": _audit_optional_float(event, "extraction_confidence"),
        "parser_backend": _audit_optional_str(event, "parser_backend"),
        "parser_profile": _audit_optional_str(event, "parser_profile"),
        "segment_count": _audit_int(event, "segment_count"),
        "fallback_count": _audit_int(event, "fallback_count"),
        "failed_segment_count": _audit_int(event, "failed_segment_count"),
        "chunk_count": _audit_int(event, "chunk_count"),
        "vector_count": _audit_int(event, "vector_count"),
        "elapsed_ms": _audit_float(event, "elapsed_ms"),
        "error_type": _audit_optional_str(event, "error_type"),
        "error_message": _audit_optional_str(event, "error_message"),
    }


def _citation_feedback_binds(
    feedback: Mapping[str, object],
    *,
    feedback_id: str,
) -> dict[str, object]:
    """CitationFeedbackRequest を Oracle bind 値へ変換する。"""
    context = current_audit_request_context()
    return {
        "feedback_id": feedback_id,
        "trace_id": _audit_str(feedback, "trace_id", ""),
        "document_id": _audit_str(feedback, "document_id", ""),
        "chunk_id": _audit_str(feedback, "chunk_id", ""),
        "tenant_id_hash": _audit_optional_str(feedback, "tenant_id_hash") or context.tenant_id_hash,
        "user_id_hash": _audit_optional_str(feedback, "user_id_hash") or context.user_id_hash,
        "rating": _audit_str(feedback, "rating", "not_helpful"),
        "reason": _audit_optional_str(feedback, "reason"),
        "comment_hash": _audit_optional_str(feedback, "comment_hash"),
        "comment_chars": _audit_int(feedback, "comment_chars"),
    }


def _evaluation_artifact_binds(
    artifact: Mapping[str, object],
    *,
    evaluation_run_id: str,
) -> dict[str, object]:
    """評価 artifact を query/context 原文なしの Oracle bind 値へ変換する。"""
    context = current_audit_request_context()
    request_summary = artifact.get("request_summary", {})
    result_summary = artifact.get("result_summary", {})
    result_json = _audit_json(result_summary if isinstance(result_summary, Mapping) else {})
    knowledge_base_ids = artifact.get("knowledge_base_ids", [])
    return {
        "evaluation_run_id": evaluation_run_id,
        "tenant_id_hash": _audit_optional_str(artifact, "tenant_id_hash") or context.tenant_id_hash,
        "knowledge_base_ids": _audit_json(
            knowledge_base_ids
            if isinstance(knowledge_base_ids, Sequence)
            and not isinstance(knowledge_base_ids, str | bytes | bytearray)
            else []
        ),
        "request_json": _audit_json(
            request_summary if isinstance(request_summary, Mapping) else {}
        ),
        "result_json": result_json,
        "result_sha256": hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
        "best_experiment_id": _audit_optional_str(artifact, "best_experiment_id"),
        "passed": 1 if bool(artifact.get("passed")) else 0,
    }


def _agent_memory_binds(
    memory: Mapping[str, object],
    *,
    memory_id: str,
    embedding: list[float],
) -> dict[str, object]:
    """Agent Memory を Oracle bind 値へ変換する。scope は hash のみ保存する。"""
    context = current_audit_request_context()
    now = datetime.now(UTC)
    metadata = memory.get("metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}
    usefulness_score = _bounded_float(memory.get("usefulness_score"), default=0.5)
    return {
        "memory_id": memory_id,
        "tenant_id_hash": _audit_optional_str(memory, "tenant_id_hash") or context.tenant_id_hash,
        "user_id_hash": _audit_optional_str(memory, "user_id_hash") or context.user_id_hash,
        "role_id_hash": _audit_optional_str(memory, "role_id_hash") or context.role_id_hash,
        "agent_id_hash": _audit_optional_str(memory, "agent_id_hash") or context.agent_id_hash,
        "thread_id_hash": _audit_optional_str(memory, "thread_id_hash") or context.thread_id_hash,
        "trace_id": _audit_str(memory, "trace_id", ""),
        "memory_text": str(memory.get("memory_text") or "").strip(),
        "metadata_json": _audit_json(metadata),
        "embedding": _to_vector_bind(embedding),
        "embedding_list": list(embedding),
        "usefulness_score": usefulness_score,
        "eval_count": _audit_int(memory, "eval_count"),
        "created_at": now,
        "updated_at": now,
    }


def _stored_agent_memory_from_binds(binds: Mapping[str, object]) -> StoredAgentMemory:
    return StoredAgentMemory(
        memory_id=str(binds["memory_id"]),
        tenant_id_hash=_optional_str(binds.get("tenant_id_hash")),
        user_id_hash=_optional_str(binds.get("user_id_hash")),
        role_id_hash=_optional_str(binds.get("role_id_hash")),
        agent_id_hash=_optional_str(binds.get("agent_id_hash")),
        thread_id_hash=_optional_str(binds.get("thread_id_hash")),
        trace_id=str(binds.get("trace_id") or ""),
        memory_text=str(binds.get("memory_text") or ""),
        embedding=list(cast(Sequence[float], binds.get("embedding_list") or [])),
        metadata=_json_loads(binds.get("metadata_json")),
        usefulness_score=_float_value(binds.get("usefulness_score")),
        eval_count=_int_value(binds.get("eval_count")),
        created_at=_datetime_value(binds.get("created_at")),
        updated_at=_datetime_value(binds.get("updated_at")),
    )


def _graph_entity_binds(entity: GraphEntity) -> dict[str, object]:
    return {
        "entity_id": entity.entity_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "knowledge_base_id": entity.knowledge_base_id,
        "canonical_name": entity.canonical_name,
        "entity_type": entity.entity_type,
        "description": entity.description,
        "confidence": entity.confidence,
        "source_document_ids": _audit_json(entity.source_document_ids),
    }


def _graph_relationship_binds(relationship: GraphRelationship) -> dict[str, object]:
    return {
        "relationship_id": relationship.relationship_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "knowledge_base_id": relationship.knowledge_base_id,
        "source_entity_id": relationship.source_entity_id,
        "target_entity_id": relationship.target_entity_id,
        "relationship_type": relationship.relationship_type,
        "description": relationship.description,
        "confidence": relationship.confidence,
        "source_document_ids": _audit_json(relationship.source_document_ids),
    }


def _graph_claim_binds(claim: GraphClaim) -> dict[str, object]:
    return {
        "claim_id": claim.claim_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "knowledge_base_id": claim.knowledge_base_id,
        "entity_id": claim.entity_id,
        "claim_text": claim.claim_text,
        "confidence": claim.confidence,
        "source_document_id": claim.source_document_id,
        "source_chunk_id": claim.source_chunk_id,
    }


def _graph_community_summary_binds(summary: GraphCommunitySummary) -> dict[str, object]:
    return {
        "community_id": summary.community_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "knowledge_base_id": summary.knowledge_base_id,
        "level_no": summary.level_no,
        "title": summary.title,
        "summary_text": summary.summary_text,
        "entity_ids": _audit_json(summary.entity_ids),
        "source_document_ids": _audit_json(summary.source_document_ids),
    }


def _graph_entity_chunk_link_binds(link: GraphEntityChunkLink) -> dict[str, object]:
    return {
        "entity_id": link.entity_id,
        "chunk_id": link.chunk_id,
        "document_id": link.document_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "relevance_score": link.relevance_score,
    }


def _audit_json(value: object) -> str:
    """Oracle JSON 列へ入れる低機密 metadata を JSON 文字列化する。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _audit_str(event: Mapping[str, object], key: str, default: str) -> str:
    value = event.get(key)
    return value if isinstance(value, str) and value else default


def _audit_optional_str(event: Mapping[str, object], key: str) -> str | None:
    value = event.get(key)
    return value if isinstance(value, str) and value else None


def _audit_int(event: Mapping[str, object], key: str, default: int = 0) -> int:
    value = event.get(key)
    return int(value) if isinstance(value, int | float) else default


def _audit_optional_int(event: Mapping[str, object], key: str) -> int | None:
    value = event.get(key)
    return int(value) if isinstance(value, int | float) else None


def _audit_float(event: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = event.get(key)
    return float(value) if isinstance(value, int | float) else default


def _audit_optional_float(event: Mapping[str, object], key: str) -> float | None:
    value = event.get(key)
    return float(value) if isinstance(value, int | float) else None


def _bounded_float(value: object, *, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return default
    return min(1.0, max(0.0, float(value)))


def _fetch_ingestion_job_rows(
    connection: OracleConnectionProtocol,
    statement: str,
    binds: Mapping[str, object],
    *,
    default_max_attempts: int,
) -> list[dict[str, object]]:
    """max_attempts 列がない旧 ingestion job table では既定値列として読み替える。"""
    try:
        return _fetch_all(connection, statement, binds)
    except Exception as exc:
        if not _is_missing_ingestion_job_max_attempts_error(exc):
            raise
        return _fetch_all(
            connection,
            _legacy_ingestion_job_max_attempts_select_sql(statement),
            _with_default_max_attempts_bind(binds, default_max_attempts),
        )


def _execute_ingestion_job_insert(
    connection: OracleConnectionProtocol,
    statement: str,
    binds: Mapping[str, object],
) -> None:
    """max_attempts 列がない旧 ingestion job table では該当列を省いて INSERT する。"""
    try:
        _execute(connection, statement, binds)
    except Exception as exc:
        if not _is_missing_ingestion_job_max_attempts_error(exc):
            raise
        _execute(
            connection,
            _legacy_ingestion_job_max_attempts_insert_sql(statement),
            binds,
        )


def _is_missing_ingestion_job_max_attempts_error(exc: Exception) -> bool:
    message = str(exc).upper()
    return "ORA-00904" in message and "MAX_ATTEMPTS" in message


def _legacy_ingestion_job_max_attempts_select_sql(statement: str) -> str:
    return _replace_exact_sql_line(
        statement,
        "j.max_attempts,",
        ":default_max_attempts AS max_attempts,",
    )


def _legacy_ingestion_job_max_attempts_insert_sql(statement: str) -> str:
    return _remove_exact_sql_lines(statement, {"max_attempts,", ":max_attempts,"})


def _with_default_max_attempts_bind(
    binds: Mapping[str, object],
    default_max_attempts: int,
) -> dict[str, object]:
    return {**binds, "default_max_attempts": default_max_attempts}


def _replace_exact_sql_line(statement: str, old: str, new: str) -> str:
    lines = []
    for line in statement.splitlines():
        if line.strip() == old:
            indent = line[: len(line) - len(line.lstrip())]
            lines.append(f"{indent}{new}")
            continue
        lines.append(line)
    return "\n".join(lines)


def _remove_exact_sql_lines(statement: str, stripped_lines: set[str]) -> str:
    lines = [line for line in statement.splitlines() if line.strip() not in stripped_lines]
    return "\n".join(lines)


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


def _select_knowledge_base(
    connection: OracleConnectionProtocol,
    knowledge_base_id: str,
) -> StoredKnowledgeBase | None:
    rows = _fetch_all(
        connection,
        _render_sql(
            """
        SELECT
            knowledge_base_id,
            tenant_id_hash,
            name,
            description,
            status,
            default_search_mode,
            retrieval_config,
            created_at,
            updated_at,
            archived_at,
            0 AS document_count,
            0 AS indexed_document_count,
            0 AS error_document_count,
            0 AS searchable_chunk_count
        FROM rag_knowledge_bases
        WHERE knowledge_base_id = :knowledge_base_id
          AND {knowledge_base_access_sql}
        """,
            knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(),
        ),
        _with_tenant_bind({"knowledge_base_id": knowledge_base_id}),
    )
    return None if not rows else _stored_knowledge_base_from_row(rows[0])


def _select_business_view(
    connection: OracleConnectionProtocol,
    business_view_id: str,
) -> StoredBusinessView | None:
    rows = _fetch_all(
        connection,
        _render_sql(
            """
        SELECT
            business_view_id,
            tenant_id_hash,
            name,
            description,
            status,
            view_config,
            created_at,
            updated_at,
            archived_at
        FROM rag_business_views
        WHERE business_view_id = :business_view_id
          AND {tenant_sql}
        """,
            tenant_sql=_oracle_tenant_predicate(),
        ),
        _with_tenant_bind({"business_view_id": business_view_id}),
    )
    return None if not rows else _stored_business_view_from_row(rows[0])


def _select_knowledge_base_by_name(
    connection: OracleConnectionProtocol,
    name: str,
) -> StoredKnowledgeBase | None:
    rows = _fetch_all(
        connection,
        _render_sql(
            """
        SELECT
            knowledge_base_id,
            tenant_id_hash,
            name,
            description,
            status,
            default_search_mode,
            retrieval_config,
            created_at,
            updated_at,
            archived_at,
            0 AS document_count,
            0 AS indexed_document_count,
            0 AS error_document_count,
            0 AS searchable_chunk_count
        FROM rag_knowledge_bases
        WHERE LOWER(name) = :knowledge_base_name
          AND {knowledge_base_access_sql}
        """,
            knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(),
        ),
        _with_tenant_bind({"knowledge_base_name": name.casefold()}),
    )
    return None if not rows else _stored_knowledge_base_from_row(rows[0])


def _insert_knowledge_base(
    connection: OracleConnectionProtocol,
    knowledge_base: StoredKnowledgeBase,
) -> None:
    _execute(
        connection,
        """
        INSERT INTO rag_knowledge_bases (
            knowledge_base_id,
            tenant_id_hash,
            name,
            description,
            status,
            default_search_mode,
            retrieval_config,
            created_at,
            updated_at,
            archived_at
        ) VALUES (
            :knowledge_base_id,
            :tenant_id_hash,
            :name,
            :description,
            :status,
            :default_search_mode,
            :retrieval_config,
            :created_at,
            :updated_at,
            :archived_at
        )
        """,
        _knowledge_base_binds(knowledge_base),
    )


def _ensure_default_knowledge_base(
    connection: OracleConnectionProtocol,
    name: str,
) -> StoredKnowledgeBase:
    existing = _select_knowledge_base_by_name(connection, name)
    if existing is not None:
        if existing.status != KnowledgeBaseStatus.ACTIVE:
            raise ValueError("既定ナレッジベースがアーカイブ済みです。")
        return existing
    now = datetime.now(UTC)
    knowledge_base = StoredKnowledgeBase(
        id=uuid4().hex,
        tenant_id_hash=_current_tenant_id_hash(),
        name=name,
        description=None,
        status=KnowledgeBaseStatus.ACTIVE,
        default_search_mode=SearchMode.HYBRID,
        retrieval_config={},
        created_at=now,
        updated_at=now,
    )
    _insert_knowledge_base(connection, knowledge_base)
    return knowledge_base


def _require_active_knowledge_base(
    connection: OracleConnectionProtocol,
    knowledge_base_id: str,
) -> StoredKnowledgeBase:
    knowledge_base = _select_knowledge_base(connection, knowledge_base_id)
    if knowledge_base is None:
        raise KeyError(f"knowledge_base_id={knowledge_base_id} は存在しません。")
    if knowledge_base.status != KnowledgeBaseStatus.ACTIVE:
        raise ValueError("アーカイブ済みナレッジベースは変更できません。")
    return knowledge_base


def _insert_document_knowledge_base_rows(
    connection: OracleConnectionProtocol,
    *,
    document_id: str,
    knowledge_base_ids: Sequence[str],
) -> None:
    unique_knowledge_base_ids = _unique_optional_sequence(knowledge_base_ids)
    if not unique_knowledge_base_ids:
        return
    _executemany(
        connection,
        """
        INSERT INTO rag_document_knowledge_bases (
            knowledge_base_id,
            document_id,
            tenant_id_hash,
            assigned_at,
            assigned_by_user_id_hash
        ) VALUES (
            :knowledge_base_id,
            :document_id,
            :tenant_id_hash,
            :assigned_at,
            :assigned_by_user_id_hash
        )
        """,
        [
            _document_knowledge_base_binds(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
            )
            for knowledge_base_id in unique_knowledge_base_ids
        ],
    )


def _select_document_knowledge_base_refs(
    connection: OracleConnectionProtocol,
    document_id: str,
) -> list[KnowledgeBaseRef]:
    rows = _fetch_all(
        connection,
        _render_sql(
            """
        SELECT
            kb.knowledge_base_id,
            kb.name
        FROM rag_document_knowledge_bases dkb
        JOIN rag_knowledge_bases kb
          ON kb.knowledge_base_id = dkb.knowledge_base_id
        WHERE dkb.document_id = :document_id
          AND {knowledge_base_access_sql}
        ORDER BY kb.name ASC, kb.knowledge_base_id ASC
        """,
            knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
        ),
        _with_tenant_bind({"document_id": document_id}),
    )
    return [
        KnowledgeBaseRef(id=str(row["knowledge_base_id"]), name=str(row["name"])) for row in rows
    ]


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


_BIND_NAME_RE = re.compile(r":([a-zA-Z_]\w*)")


def _binds_for_sql(statement: str, binds: Mapping[str, object]) -> dict[str, object]:
    """SQL text に現れる placeholder のバインドだけを残す。

    アクセス scope のバインド(tenant / document / category / knowledge base)は
    `_with_tenant_bind` が予防的に superset で付与するため、特定クエリで使われない
    placeholder が混ざる。oracledb thin モードは未使用の bind を渡すと DPY-4008 を出す
    ので、実行直前に SQL に現れる名前だけへ絞り込む。
    """
    referenced = set(_BIND_NAME_RE.findall(statement))
    return {name: value for name, value in binds.items() if name in referenced}


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
    knowledge_base_id: str | None = None,
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
    knowledge_base_ids = _filter_id_values(knowledge_base_id)
    if knowledge_base_ids:
        knowledge_base_filter_sql, knowledge_base_binds = _oracle_in_predicate(
            "dkb.knowledge_base_id",
            "filter_knowledge_base_id",
            knowledge_base_ids,
        )
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM rag_document_knowledge_bases dkb
                JOIN rag_knowledge_bases kb
                  ON kb.knowledge_base_id = dkb.knowledge_base_id
                WHERE dkb.document_id = rag_documents.document_id
                  AND {knowledge_base_filter_sql}
                  AND kb.status = 'ACTIVE'
                  AND {knowledge_base_access_sql}
            )
            """.format(
                knowledge_base_filter_sql=knowledge_base_filter_sql,
                knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
            )
        )
        binds.update(knowledge_base_binds)
    return " AND ".join(clauses), binds


def _oracle_in_predicate(
    column: str,
    bind_prefix: str,
    values: Sequence[str],
) -> tuple[str, dict[str, object]]:
    """可変長 IN 条件を bind 付きで生成する。"""
    unique_values = _unique_sequence(values)
    binds: dict[str, object] = {
        f"{bind_prefix}_{index}": value for index, value in enumerate(unique_values)
    }
    placeholders = ", ".join(f":{key}" for key in binds)
    return f"{column} IN ({placeholders})", binds


def _oracle_knowledge_base_where(
    *,
    status: KnowledgeBaseStatus | None = None,
    query: str | None = None,
) -> tuple[str, dict[str, object]]:
    clauses = _oracle_knowledge_base_access_predicates(alias="kb")
    binds = _with_tenant_bind({})
    if status is not None:
        clauses.append("kb.status = :knowledge_base_status")
        binds["knowledge_base_status"] = status.value
    if query and query.strip():
        clauses.append(
            "(LOWER(kb.name) LIKE :knowledge_base_query ESCAPE '\\' "
            "OR LOWER(kb.description) LIKE :knowledge_base_query ESCAPE '\\')"
        )
        binds["knowledge_base_query"] = _like_pattern(query)
    return " AND ".join(clauses), binds


def _oracle_business_view_where(
    *,
    status: BusinessViewStatus | None = None,
    query: str | None = None,
) -> tuple[str, dict[str, object]]:
    clauses = [_oracle_tenant_predicate(alias="bv")]
    binds = _with_tenant_bind({})
    if status is not None:
        clauses.append("bv.status = :business_view_status")
        binds["business_view_status"] = status.value
    if query and query.strip():
        clauses.append(
            "(LOWER(bv.name) LIKE :business_view_query ESCAPE '\\' "
            "OR LOWER(bv.description) LIKE :business_view_query ESCAPE '\\')"
        )
        binds["business_view_query"] = _like_pattern(query)
    return " AND ".join(clauses), binds


def _oracle_retrieval_where(filters: dict[str, str]) -> tuple[str, dict[str, object]]:
    clauses = ["d.status = 'INDEXED'", *_oracle_access_predicates(alias="d")]
    binds = _with_tenant_bind({}, alias="d")
    serving_mode = (filters.get("serving_mode") or "single").strip().lower()
    knowledge_base_ids = _filter_id_values(filters.get("knowledge_base_id"))
    knowledge_base_filter_sql = ""
    if knowledge_base_ids:
        knowledge_base_filter_sql, knowledge_base_binds = _oracle_in_predicate(
            "dkb.knowledge_base_id",
            "filter_knowledge_base_id",
            knowledge_base_ids,
        )
        binds.update(knowledge_base_binds)
        knowledge_base_filter_sql = f"AND {knowledge_base_filter_sql}"
        # variant: 配信中(is_serving)の chunk_set だけを検索対象にする(single/routed)。
        # スコープ KB が「この文書で別 chunk_set を配信中」のときだけ、その chunk_set 以外の
        # chunk を除外する。単一 materialization では別 chunk_set が無いので no-op(回帰なし)。
        # chunk_set 未タグ(NULL)や binding 未整備の chunk は除外しない(後方互換)。
        # fused は全 serving chunk_set を横断検索するため、この制限をかけない(重複は
        # アプリ側の source-span dedup で除去する)。
        if serving_mode != "fused":
            served_in_sql, served_binds = _oracle_in_predicate(
                "b.knowledge_base_id", "filter_knowledge_base_id", knowledge_base_ids
            )
            binds.update(served_binds)
            clauses.append(f"""
            NOT EXISTS (
                SELECT 1
                FROM rag_kb_chunk_set_bindings b
                WHERE b.document_id = c.document_id
                  AND b.is_serving = 1
                  AND b.chunk_set_id <> c.chunk_set_id
                  AND {served_in_sql}
            )
            """)
    if knowledge_base_ids or current_audit_request_context().allowed_knowledge_base_ids is not None:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM rag_document_knowledge_bases dkb
                JOIN rag_knowledge_bases kb
                  ON kb.knowledge_base_id = dkb.knowledge_base_id
                WHERE dkb.document_id = d.document_id
                  AND kb.status = 'ACTIVE'
                  AND {knowledge_base_access_sql}
                  {knowledge_base_filter_sql}
            )
            """.format(
                knowledge_base_access_sql=_oracle_knowledge_base_access_predicate_sql(alias="kb"),
                knowledge_base_filter_sql=knowledge_base_filter_sql,
            )
        )
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
        elif key == "knowledge_base_id":
            continue
        elif key == "serving_mode":
            continue  # フィルタ値ではなく配信モード制御。chunk_set 制限の有無で既に処理済み。
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
        elif key == "source_acl":
            clauses.append(
                "LOWER(JSON_VALUE(c.metadata_json, '$.source_acl')) = :filter_source_acl"
            )
            binds["filter_source_acl"] = cleaned.casefold()
        elif key == "document_version":
            clauses.append(
                "LOWER(JSON_VALUE(c.metadata_json, '$.document_version')) "
                "= :filter_document_version"
            )
            binds["filter_document_version"] = cleaned.casefold()
        elif key == "page_number_min":
            clauses.append(
                "JSON_VALUE(c.metadata_json, '$.page_number' RETURNING NUMBER) "
                ">= :filter_page_number_min"
            )
            binds["filter_page_number_min"] = int(cleaned)
        elif key == "page_number_max":
            clauses.append(
                "JSON_VALUE(c.metadata_json, '$.page_number' RETURNING NUMBER) "
                "<= :filter_page_number_max"
            )
            binds["filter_page_number_max"] = int(cleaned)
        elif key == "uploaded_from":
            clauses.append("d.uploaded_at >= :filter_uploaded_from")
            binds["filter_uploaded_from"] = _parse_filter_datetime(cleaned, end_of_day=False)
        elif key == "uploaded_to":
            clauses.append("d.uploaded_at <= :filter_uploaded_to")
            binds["filter_uploaded_to"] = _parse_filter_datetime(cleaned, end_of_day=True)
        elif key == "indexed_from":
            clauses.append("d.indexed_at >= :filter_indexed_from")
            binds["filter_indexed_from"] = _parse_filter_datetime(cleaned, end_of_day=False)
        elif key == "indexed_to":
            clauses.append("d.indexed_at <= :filter_indexed_to")
            binds["filter_indexed_to"] = _parse_filter_datetime(cleaned, end_of_day=True)
        elif key == "content_kinds":
            kinds = [part.strip().casefold() for part in cleaned.split(",") if part.strip()]
            if kinds:
                predicate, kind_binds = _oracle_in_predicate(
                    "LOWER(JSON_VALUE(c.metadata_json, '$.content_kind'))",
                    "filter_content_kind_in",
                    kinds,
                )
                clauses.append(predicate)
                binds.update(kind_binds)
        else:
            raise ValueError(f"未対応の検索フィルターです: {key}")
    return " AND ".join(clauses), binds


def _parse_filter_datetime(value: str, *, end_of_day: bool) -> datetime:
    """検証済みの ISO 8601 日付/日時を tz-aware datetime へ変換する。

    date-only（YYYY-MM-DD）の `_to` 境界は当日全体を含めるため、その日の終端へ寄せる。
    naive な入力は UTC として解釈する。
    """
    raw = value.strip()
    candidate = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end_of_day and len(raw) == 10:
        parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
    return parsed


def _oracle_agent_memory_where() -> tuple[str, dict[str, object]]:
    """Agent Memory の tenant/user/thread/agent scope predicate を作る。"""
    context = current_audit_request_context()
    clauses: list[str] = []
    binds: dict[str, object] = {}
    if context.tenant_id_hash is not None:
        clauses.append("m.tenant_id_hash = :agent_memory_tenant_id_hash")
        binds["agent_memory_tenant_id_hash"] = context.tenant_id_hash
    if context.user_id_hash is not None:
        clauses.append("m.user_id_hash = :agent_memory_user_id_hash")
        binds["agent_memory_user_id_hash"] = context.user_id_hash
    if context.role_id_hash is not None:
        clauses.append("m.role_id_hash = :agent_memory_role_id_hash")
        binds["agent_memory_role_id_hash"] = context.role_id_hash
    if context.agent_id_hash is not None:
        clauses.append("m.agent_id_hash = :agent_memory_agent_id_hash")
        binds["agent_memory_agent_id_hash"] = context.agent_id_hash
    if context.thread_id_hash is not None:
        clauses.append("m.thread_id_hash = :agent_memory_thread_id_hash")
        binds["agent_memory_thread_id_hash"] = context.thread_id_hash
    if not _agent_memory_scope_available():
        clauses.append("1 = 0")
    return " AND ".join(clauses or ["1 = 1"]), binds


def _agent_memory_scope_available() -> bool:
    """ユーザー・スレッド・エージェントのいずれかで scope できる場合だけ memory を使う。"""
    context = current_audit_request_context()
    return any(
        (
            context.user_id_hash,
            context.role_id_hash,
            context.agent_id_hash,
            context.thread_id_hash,
        )
    )


def _oracle_graph_community_where(filters: dict[str, str]) -> tuple[str, dict[str, object]]:
    """community summary table 用の tenant / KB scope predicate を作る。"""
    clauses = _oracle_knowledge_base_access_predicates(alias="g")
    binds = _with_tenant_bind({}, alias="g")
    unsupported_global_filters = {
        key for key, value in filters.items() if key != "knowledge_base_id" and value.strip()
    }
    if unsupported_global_filters:
        clauses.append("1 = 0")
    knowledge_base_ids = _filter_id_values(filters.get("knowledge_base_id"))
    if knowledge_base_ids:
        knowledge_base_filter_sql, knowledge_base_binds = _oracle_in_predicate(
            "g.knowledge_base_id",
            "filter_knowledge_base_id",
            knowledge_base_ids,
        )
        clauses.append(knowledge_base_filter_sql)
        binds.update(knowledge_base_binds)
    return " AND ".join(clauses), binds


def _oracle_graph_local_match_predicate(query: str) -> tuple[str, dict[str, object]]:
    """entity local search 用の LIKE predicate を作る。"""
    return _oracle_like_any_predicate(
        query,
        columns=[
            "LOWER(e.canonical_name)",
            "LOWER(e.entity_type)",
            "LOWER(DBMS_LOB.SUBSTR(e.description, 4000, 1))",
        ],
        bind_prefix="graph_local_term",
    )


def _oracle_graph_global_match_predicate(query: str) -> tuple[str, dict[str, object]]:
    """community summary search 用の LIKE predicate を作る。"""
    match_sql, binds = _oracle_like_any_predicate(
        query,
        columns=[
            "LOWER(g.title)",
            "LOWER(DBMS_LOB.SUBSTR(g.summary_text, 4000, 1))",
        ],
        bind_prefix="graph_global_term",
    )
    binds["graph_title_exact"] = _like_pattern(query)
    return match_sql, binds


def _oracle_like_any_predicate(
    query: str,
    *,
    columns: Sequence[str],
    bind_prefix: str,
) -> tuple[str, dict[str, object]]:
    """複数列 x query term の OR predicate を bind 付きで生成する。"""
    terms = _graph_query_terms(query)
    if not terms:
        return "1 = 1", {}
    clauses: list[str] = []
    binds: dict[str, object] = {}
    for index, term in enumerate(terms):
        bind_name = f"{bind_prefix}_{index}"
        binds[bind_name] = _like_pattern(term)
        clauses.extend(f"{column} LIKE :{bind_name} ESCAPE '\\'" for column in columns)
    return "(" + " OR ".join(clauses) + ")", binds


def _graph_query_terms(query: str) -> list[str]:
    """Graph 検索用に query から短い低コスト term 集合を作る。"""
    normalized = query.casefold().strip()
    terms = [normalized] if len(normalized) >= 2 else []
    terms.extend(
        token.strip().casefold()
        for token in TOKEN_PATTERN.findall(normalized)
        if len(token.strip()) >= 2
    )
    return _unique_optional_sequence(terms)[:8]


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


def _oracle_knowledge_base_access_predicates(*, alias: str | None = None) -> list[str]:
    """tenant と認可済み knowledge base scope を SQL predicate にする。"""
    context = current_audit_request_context()
    predicates = [_oracle_tenant_predicate(alias=alias)]
    knowledge_base_column = f"{alias}.knowledge_base_id" if alias else "knowledge_base_id"
    if context.allowed_knowledge_base_ids is not None:
        if not context.allowed_knowledge_base_ids:
            predicates.append("1 = 0")
        else:
            placeholders = ", ".join(
                f":access_knowledge_base_id_{index}"
                for index, _ in enumerate(sorted(context.allowed_knowledge_base_ids))
            )
            predicates.append(f"{knowledge_base_column} IN ({placeholders})")
    return predicates


def _oracle_knowledge_base_access_predicate_sql(*, alias: str | None = None) -> str:
    """tenant と knowledge base scope の predicate を AND で結合する。"""
    return " AND ".join(_oracle_knowledge_base_access_predicates(alias=alias))


def _oracle_membership_access_predicate_sql() -> str:
    """membership table に対する tenant / knowledge base scope predicate。"""
    predicates = [_oracle_tenant_predicate()]
    context = current_audit_request_context()
    if context.allowed_knowledge_base_ids is not None:
        if not context.allowed_knowledge_base_ids:
            predicates.append("1 = 0")
        else:
            placeholders = ", ".join(
                f":access_knowledge_base_id_{index}"
                for index, _ in enumerate(sorted(context.allowed_knowledge_base_ids))
            )
            predicates.append(f"knowledge_base_id IN ({placeholders})")
    return " AND ".join(predicates)


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
    if context.allowed_knowledge_base_ids is not None:
        for index, knowledge_base_id in enumerate(sorted(context.allowed_knowledge_base_ids)):
            resolved[f"access_knowledge_base_id_{index}"] = knowledge_base_id
    return resolved


def _like_pattern(value: str) -> str:
    escaped = value.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _unique_sequence(values: Sequence[str]) -> list[str]:
    unique_values = _unique_optional_sequence(values)
    if not unique_values:
        raise ValueError("ID を 1 件以上指定してください。")
    return unique_values


def _unique_optional_sequence(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique_values.append(cleaned)
    return unique_values


def _filter_id_values(value: str | None) -> list[str]:
    if value is None:
        return []
    return _unique_optional_sequence(value.split(","))


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


def _knowledge_base_binds(knowledge_base: StoredKnowledgeBase) -> dict[str, object]:
    return {
        "knowledge_base_id": knowledge_base.id,
        "tenant_id_hash": knowledge_base.tenant_id_hash,
        "name": knowledge_base.name,
        "description": knowledge_base.description,
        "status": knowledge_base.status.value,
        "default_search_mode": knowledge_base.default_search_mode.value,
        "retrieval_config": _json_dumps(knowledge_base.retrieval_config),
        "created_at": knowledge_base.created_at,
        "updated_at": knowledge_base.updated_at,
        "archived_at": knowledge_base.archived_at,
    }


def _business_view_binds(view: StoredBusinessView) -> dict[str, object]:
    return {
        "business_view_id": view.id,
        "tenant_id_hash": view.tenant_id_hash,
        "name": view.name,
        "description": view.description,
        "status": view.status.value,
        "view_config": _json_dumps(view.view_config),
        "created_at": view.created_at,
        "updated_at": view.updated_at,
        "archived_at": view.archived_at,
    }


def _document_knowledge_base_binds(
    *,
    knowledge_base_id: str,
    document_id: str,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "knowledge_base_id": knowledge_base_id,
        "document_id": document_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "assigned_at": now,
        "assigned_by_user_id_hash": current_audit_request_context().user_id_hash,
    }


def _ingestion_job_binds(job: IngestionJob) -> dict[str, object]:
    return {
        "job_id": job.id,
        "document_id": job.document_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "status": job.status.value,
        "phase": job.phase.value,
        "parser_profile": job.parser_profile,
        "quality_warnings": _json_dumps(job.quality_warnings),
        "skip_reason": job.skip_reason,
        "error_message": job.error_message,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _ingestion_segment_binds(segment: IngestionSegment) -> dict[str, object]:
    return {
        "segment_id": segment.segment_id,
        "document_id": segment.document_id,
        "tenant_id_hash": _current_tenant_id_hash(),
        "status": segment.status,
        "parser_backend": segment.parser_backend,
        "parser_profile": segment.parser_profile,
        "page_start": segment.page_start,
        "page_end": segment.page_end,
        "attempt_count": segment.attempt_count,
        "artifact_path": segment.artifact_path,
        "error_code": segment.error_code,
        "error_message": segment.error_message,
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


def _stored_knowledge_base_from_row(row: Mapping[str, object]) -> StoredKnowledgeBase:
    return StoredKnowledgeBase(
        id=str(row["knowledge_base_id"]),
        tenant_id_hash=_optional_str(row.get("tenant_id_hash")),
        name=str(row["name"]),
        description=_optional_str(row.get("description")),
        status=_knowledge_base_status(row.get("status")),
        default_search_mode=_search_mode(row.get("default_search_mode")),
        retrieval_config=_json_loads(row.get("retrieval_config")),
        created_at=_datetime_value(row.get("created_at")),
        updated_at=_datetime_value(row.get("updated_at")),
        archived_at=_optional_datetime(row.get("archived_at")),
        document_count=_int_value(row.get("document_count")),
        indexed_document_count=_int_value(row.get("indexed_document_count")),
        error_document_count=_int_value(row.get("error_document_count")),
        searchable_chunk_count=_int_value(row.get("searchable_chunk_count")),
    )


def _stored_business_view_from_row(row: Mapping[str, object]) -> StoredBusinessView:
    return StoredBusinessView(
        id=str(row["business_view_id"]),
        tenant_id_hash=_optional_str(row.get("tenant_id_hash")),
        name=str(row["name"]),
        description=_optional_str(row.get("description")),
        status=_business_view_status(row.get("status")),
        view_config=_json_loads(row.get("view_config")),
        created_at=_datetime_value(row.get("created_at")),
        updated_at=_datetime_value(row.get("updated_at")),
        archived_at=_optional_datetime(row.get("archived_at")),
    )


def _ingestion_job_from_row(row: Mapping[str, object]) -> IngestionJob:
    return IngestionJob(
        id=str(row["job_id"]),
        document_id=str(row["document_id"]),
        status=_ingestion_job_status(row.get("status")),
        phase=_ingestion_job_phase(row.get("phase")),
        parser_profile=str(row.get("parser_profile") or "enterprise_ai_generic"),
        quality_warnings=_json_string_list(row.get("quality_warnings")),
        skip_reason=_optional_str(row.get("skip_reason")),
        error_message=_optional_str(row.get("error_message")),
        attempt_count=_int_value(row.get("attempt_count")),
        max_attempts=max(1, _int_value(row.get("max_attempts")) or 3),
        queued_at=_datetime_value(row.get("queued_at")),
        started_at=_optional_datetime(row.get("started_at")),
        finished_at=_optional_datetime(row.get("finished_at")),
    )


def _ingestion_segment_from_row(row: Mapping[str, object]) -> IngestionSegment:
    return IngestionSegment(
        segment_id=str(row["segment_id"]),
        document_id=str(row["document_id"]),
        status=str(row.get("status") or "QUEUED"),
        parser_backend=str(row.get("parser_backend") or "enterprise_ai"),
        parser_profile=str(row.get("parser_profile") or "enterprise_ai_generic"),
        page_start=_optional_int(row.get("page_start")),
        page_end=_optional_int(row.get("page_end")),
        attempt_count=_int_value(row.get("attempt_count")),
        artifact_path=_optional_str(row.get("artifact_path")),
        error_code=_optional_str(row.get("error_code")),
        error_message=_optional_str(row.get("error_message")),
    )


def _retrieved_chunk_from_row(row: Mapping[str, object]) -> RetrievedChunk:
    metadata = _metadata_from_json(row.get("metadata_json"))
    metadata.setdefault("document_id", str(row["document_id"]))
    metadata.setdefault("chunk_id", str(row["chunk_id"]))
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


def _chunk_metadata_from_row(row: Mapping[str, object]) -> dict[str, MetadataValue]:
    """chunk metadata listing に traceable citation lineage を補う。"""
    metadata = _metadata_from_json(row.get("metadata_json"))
    document_id = row.get("document_id")
    if document_id is not None:
        metadata.setdefault("document_id", str(document_id))
    chunk_id = row.get("chunk_id")
    if chunk_id is not None:
        metadata.setdefault("chunk_id", str(chunk_id))
    chunk_index = row.get("chunk_index")
    if "chunk_index" not in metadata and chunk_index is not None:
        metadata["chunk_index"] = _int_value(chunk_index)
    return metadata


def _agent_memory_chunk_from_row(
    row: Mapping[str, object],
    *,
    rank: int,
) -> RetrievedChunk:
    metadata = _metadata_from_json(row.get("metadata_json"))
    memory_id = str(row["memory_id"])
    metadata.update(
        {
            "retrieval_mode": "agent_memory",
            "context_role": "history",
            "agent_memory_id": memory_id,
            "agent_memory_rank": rank,
            "agent_memory_usefulness_score": round(
                _float_value(row.get("usefulness_score", 0.5)),
                6,
            ),
            "agent_memory_eval_count": _int_value(row.get("eval_count")),
            "agent_memory_vector_score": round(_float_value(row.get("vector_score")), 6),
        }
    )
    return RetrievedChunk(
        document_id="agent-memory",
        chunk_id=f"agent-memory:{memory_id}",
        text=str(row["memory_text"]),
        score=round(_float_value(row.get("score", 0.0)), 6),
        file_name="agent-memory",
        metadata=metadata,
    )


def _document_chunk_view_from_row(row: Mapping[str, object]) -> DocumentChunkView:
    """rag_chunks row を UI 用 chunk view へ変換する。"""
    metadata = _metadata_from_json(row.get("metadata_json"))
    metadata.setdefault("document_id", str(row["document_id"]))
    metadata.setdefault("chunk_id", str(row["chunk_id"]))
    chunk_index = _optional_metadata_int(metadata.get("chunk_index"))
    if chunk_index is None:
        chunk_index = _int_value(row.get("chunk_index"))
    return DocumentChunkView(
        document_id=str(row["document_id"]),
        chunk_id=str(row["chunk_id"]),
        chunk_index=chunk_index,
        text=str(row["chunk_text"]),
        page_start=_optional_metadata_int(metadata.get("page_start")),
        page_end=_optional_metadata_int(metadata.get("page_end")),
        bbox=_bbox_from_metadata(metadata.get("bbox")),
        section_path=_metadata_str(metadata.get("section_path")),
        content_kind=_metadata_str(metadata.get("content_kind")),
        chunk_group_id=_metadata_str(metadata.get("chunk_group_id")),
        source_parser=_metadata_str(metadata.get("source_parser")),
        element_ids=_element_ids_from_metadata(metadata.get("element_ids")),
        metadata=metadata,
    )


def _graph_community_chunk_from_row(row: Mapping[str, object], *, rank: int) -> RetrievedChunk:
    """community summary row を RetrievedChunk として LLM context へ渡す。"""
    community_id = str(row["community_id"])
    source_document_ids = _json_list(row.get("source_document_ids"))
    primary_document_id = source_document_ids[0] if source_document_ids else community_id
    title = _optional_str(row.get("title")) or "Graph community summary"
    metadata: dict[str, MetadataValue] = {
        "retrieval_mode": "graph_global",
        "graph_rank": rank,
        "graph_community_id": community_id,
        "graph_community_title": title,
        "graph_level": _int_value(row.get("level_no")),
        "graph_knowledge_base_id": _optional_str(row.get("knowledge_base_id")),
        "graph_source_document_count": len(source_document_ids),
        "graph_source_document_ids": _audit_json(source_document_ids),
    }
    return RetrievedChunk(
        document_id=primary_document_id,
        chunk_id=f"community:{community_id}",
        text=str(row["summary_text"]),
        score=round(_float_value(row.get("score", 0.0)), 6),
        file_name=title,
        metadata=metadata,
    )


def _metadata_from_json(value: object) -> dict[str, MetadataValue]:
    decoded = _json_loads(value)
    return {str(key): _coerce_metadata_value(item) for key, item in decoded.items()}


def _coerce_metadata_value(item: object) -> MetadataValue:
    """JSON 由来の値を MetadataValue へ正規化する。

    Oracle の JSON 列は数値を Decimal で返すため、int/float に戻す。
    list/dict は citation lineage として構造を保ったまま返す。
    """
    if item is None or isinstance(item, bool):
        return item
    if isinstance(item, Decimal):
        return int(item) if item == item.to_integral_value() else float(item)
    if isinstance(item, str | int | float):
        return item
    if isinstance(item, Mapping):
        return {str(key): _coerce_metadata_value(value) for key, value in item.items()}
    if isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
        return [_coerce_metadata_value(value) for value in item]
    return str(item)


def _optional_metadata_int(value: object) -> int | None:
    """metadata scalar から optional int を読む。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _metadata_str(value: object) -> str | None:
    """metadata scalar から空でない文字列を読む。"""
    if isinstance(value, str | int | float):
        cleaned = str(value).strip()
        return cleaned or None
    return None


def _element_ids_from_metadata(value: object) -> list[str]:
    """chunk metadata の element_ids を list にする。"""
    if isinstance(value, list):
        return [
            str(item).strip() for item in value if isinstance(item, str | int) and str(item).strip()
        ]
    text = _metadata_str(value)
    if not text:
        return []
    if text.startswith("["):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            return [
                str(item).strip()
                for item in decoded
                if isinstance(item, str | int) and str(item).strip()
            ]
    return [part.strip() for part in text.split(",") if part.strip()]


def _bbox_from_metadata(value: object) -> list[float] | None:
    """chunk metadata の bbox JSON 文字列 / 配列を list[float] に戻す。"""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
    else:
        decoded = value
    if not isinstance(decoded, list) or len(decoded) not in {4, 8}:
        return None
    bbox: list[float] = []
    for item in decoded:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return None
        bbox.append(float(item))
    return bbox


def _json_dumps(value: object) -> str:
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


def _json_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(decoded, Sequence) and not isinstance(decoded, str | bytes | bytearray):
        return [str(item) for item in decoded]
    return []


def _json_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(decoded, Sequence) and not isinstance(decoded, str | bytes | bytearray):
        return [str(item) for item in decoded]
    return []


def _row_count_value(row: Mapping[str, object] | None) -> int:
    if row is None:
        return 0
    value = row.get("count_value")
    return _int_value(value)


def _file_status(value: object) -> FileStatus:
    if isinstance(value, FileStatus):
        return value
    return FileStatus(str(value))


def _ingestion_job_status(value: object) -> IngestionJobStatus:
    if isinstance(value, IngestionJobStatus):
        return value
    return IngestionJobStatus(str(value or IngestionJobStatus.QUEUED.value))


def _ingestion_job_phase(value: object) -> IngestionJobPhase:
    if isinstance(value, IngestionJobPhase):
        return value
    return IngestionJobPhase(str(value or IngestionJobPhase.EXTRACT.value))


def _knowledge_base_status(value: object) -> KnowledgeBaseStatus:
    if isinstance(value, KnowledgeBaseStatus):
        return value
    return KnowledgeBaseStatus(str(value or KnowledgeBaseStatus.ACTIVE.value))


def _business_view_status(value: object) -> BusinessViewStatus:
    if isinstance(value, BusinessViewStatus):
        return value
    return BusinessViewStatus(str(value or BusinessViewStatus.ACTIVE.value))


def _search_mode(value: object) -> SearchMode:
    if isinstance(value, SearchMode):
        return value
    return SearchMode(str(value or SearchMode.HYBRID.value))


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
    _LOCAL_STORE.knowledge_bases.clear()
    _LOCAL_STORE.document_knowledge_bases.clear()
    _LOCAL_STORE.ingestion_jobs.clear()
    _LOCAL_STORE.ingestion_segments.clear()
    _LOCAL_STORE.agent_memories.clear()


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


def _oracle_connection_configured(client: OracleClient) -> bool:
    """実 DB に接続する設定または明示 pool があるかを返す。"""
    settings = client._settings
    return (
        client._pool_instance is not None
        or bool(settings.oracle_user.strip() and settings.oracle_dsn.strip())
        or _SHARED_ORACLE_POOL is not None
    )


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


def oracle_knowledge_base_schema_sql(
    knowledge_base_table: str = "rag_knowledge_bases",
    membership_table: str = "rag_document_knowledge_bases",
    document_table: str = "rag_documents",
) -> str:
    """Oracle knowledge base / document membership table の DDL 例を返す。"""
    return f"""
CREATE TABLE {knowledge_base_table} (
    knowledge_base_id     VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash        CHAR(64),
    name                  VARCHAR2(256) NOT NULL,
    description           VARCHAR2(2000),
    status                VARCHAR2(32) DEFAULT 'ACTIVE' NOT NULL,
    default_search_mode   VARCHAR2(16) DEFAULT 'hybrid' NOT NULL,
    retrieval_config      JSON,
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    archived_at           TIMESTAMP WITH TIME ZONE,
    CONSTRAINT {knowledge_base_table}_status_ck
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    CONSTRAINT {knowledge_base_table}_mode_ck
        CHECK (default_search_mode IN ('hybrid', 'vector', 'keyword'))
);

CREATE UNIQUE INDEX {knowledge_base_table}_tenant_name_uidx
    ON {knowledge_base_table} (
        NVL(tenant_id_hash, '__GLOBAL__'),
        LOWER(name)
    );

CREATE INDEX {knowledge_base_table}_tenant_status_idx
    ON {knowledge_base_table} (tenant_id_hash, status, updated_at DESC);

CREATE TABLE {membership_table} (
    knowledge_base_id        VARCHAR2(64) NOT NULL,
    document_id              VARCHAR2(64) NOT NULL,
    tenant_id_hash           CHAR(64),
    assigned_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    assigned_by_user_id_hash CHAR(64),
    PRIMARY KEY (knowledge_base_id, document_id),
    CONSTRAINT {membership_table}_kb_fk
        FOREIGN KEY (knowledge_base_id)
        REFERENCES {knowledge_base_table} (knowledge_base_id)
        ON DELETE CASCADE,
    CONSTRAINT {membership_table}_doc_fk
        FOREIGN KEY (document_id)
        REFERENCES {document_table} (document_id)
        ON DELETE CASCADE
);

CREATE INDEX {membership_table}_document_idx
    ON {membership_table} (document_id, knowledge_base_id);

CREATE INDEX {membership_table}_tenant_kb_idx
    ON {membership_table} (tenant_id_hash, knowledge_base_id, assigned_at DESC);
""".strip()


def oracle_business_view_schema_sql(
    table_name: str = "rag_business_views",
) -> str:
    """Oracle business view(業務アシスタント)table の DDL 例を返す。

    参照 KB は ``view_config`` JSON 内に ID 群として保持する(多対多。link table 不要で
    DDL を最小化する)。query 上書きと persona も同 JSON へ束ねる。
    """
    return f"""
CREATE TABLE {table_name} (
    business_view_id   VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash     CHAR(64),
    name               VARCHAR2(256) NOT NULL,
    description        VARCHAR2(2000),
    status             VARCHAR2(32) DEFAULT 'ACTIVE' NOT NULL,
    view_config        JSON,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    archived_at        TIMESTAMP WITH TIME ZONE,
    CONSTRAINT {table_name}_status_ck
        CHECK (status IN ('ACTIVE', 'ARCHIVED'))
);

CREATE UNIQUE INDEX {table_name}_tenant_name_uidx
    ON {table_name} (
        NVL(tenant_id_hash, '__GLOBAL__'),
        LOWER(name)
    );

CREATE INDEX {table_name}_tenant_status_idx
    ON {table_name} (tenant_id_hash, status, updated_at DESC);
""".strip()


def oracle_ingestion_job_schema_sql(
    table_name: str = "rag_ingestion_jobs",
    document_table: str = "rag_documents",
) -> str:
    """Oracle ingestion job table の DDL 例を返す。"""
    return f"""
CREATE TABLE {table_name} (
    job_id           VARCHAR2(64) PRIMARY KEY,
    document_id      VARCHAR2(64) NOT NULL,
    tenant_id_hash   CHAR(64),
    status           VARCHAR2(32) NOT NULL,
    phase            VARCHAR2(16) DEFAULT 'EXTRACT' NOT NULL,
    parser_profile   VARCHAR2(80) NOT NULL,
    quality_warnings JSON,
    skip_reason      VARCHAR2(256),
    error_message    VARCHAR2(2000),
    attempt_count    NUMBER(5) DEFAULT 0 NOT NULL,
    max_attempts     NUMBER(5) DEFAULT 3 NOT NULL,
    queued_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    started_at       TIMESTAMP WITH TIME ZONE,
    finished_at      TIMESTAMP WITH TIME ZONE,
    CONSTRAINT {table_name}_status_ck
        CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'CANCELLED')),
    CONSTRAINT {table_name}_phase_ck
        CHECK (phase IN ('EXTRACT', 'INDEX')),
    CONSTRAINT {table_name}_attempts_ck
        CHECK (attempt_count >= 0 AND max_attempts >= 1),
    CONSTRAINT {table_name}_document_fk
        FOREIGN KEY (document_id)
        REFERENCES {document_table} (document_id)
        ON DELETE CASCADE
);

CREATE INDEX {table_name}_tenant_queued_idx
    ON {table_name} (tenant_id_hash, status, queued_at DESC);

CREATE INDEX {table_name}_document_idx
    ON {table_name} (document_id, queued_at DESC);
""".strip()


def oracle_ingestion_segment_schema_sql(
    table_name: str = "rag_ingestion_segments",
    document_table: str = "rag_documents",
) -> str:
    """Oracle ingestion segment checkpoint table の DDL 例を返す。"""
    return f"""
CREATE TABLE {table_name} (
    segment_id     VARCHAR2(128) PRIMARY KEY,
    document_id    VARCHAR2(64) NOT NULL,
    tenant_id_hash CHAR(64),
    status         VARCHAR2(32) DEFAULT 'QUEUED' NOT NULL,
    parser_backend VARCHAR2(80) DEFAULT 'enterprise_ai' NOT NULL,
    parser_profile VARCHAR2(80) DEFAULT 'enterprise_ai_generic' NOT NULL,
    page_start     NUMBER(10),
    page_end       NUMBER(10),
    attempt_count  NUMBER(5) DEFAULT 0 NOT NULL,
    artifact_path  VARCHAR2(1024),
    error_code     VARCHAR2(128),
    error_message  VARCHAR2(2000),
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {table_name}_status_ck
        CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CONSTRAINT {table_name}_attempts_ck
        CHECK (attempt_count >= 0),
    CONSTRAINT {table_name}_page_range_ck
        CHECK (page_start IS NULL OR page_end IS NULL OR page_start <= page_end),
    CONSTRAINT {table_name}_document_fk
        FOREIGN KEY (document_id)
        REFERENCES {document_table} (document_id)
        ON DELETE CASCADE
);

CREATE INDEX {table_name}_document_status_idx
    ON {table_name} (document_id, status, page_start, page_end);

CREATE INDEX {table_name}_tenant_status_idx
    ON {table_name} (tenant_id_hash, status, updated_at DESC);
""".strip()


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
    chunk_set_id    VARCHAR2(64),
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

CREATE INDEX {table_name}_chunk_set_idx
    ON {table_name} (chunk_set_id, chunk_index);
""".strip()


def oracle_chunk_set_schema_sql() -> str:
    """variant materialization の chunk_set 層 + KB binding の DDL を返す。

    1 文書 × N レシピ(複数チャンク集合)を共有して保持するための土台。
    refcount は ``rag_kb_chunk_set_bindings`` の件数から導出する(列で持たず drift しない)。
    実際の dedup/GC 計算は :mod:`app.rag.variant_planner`(決定論)が行い、本表はその永続層。
    """
    return """
CREATE TABLE rag_chunk_sets (
    chunk_set_id    VARCHAR2(64) PRIMARY KEY,
    document_id     VARCHAR2(64) NOT NULL,
    tenant_id_hash  CHAR(64),
    recipe_subset   JSON,
    status          VARCHAR2(32) DEFAULT 'INGESTING' NOT NULL,
    chunk_count     NUMBER(10) DEFAULT 0 NOT NULL,
    vector_count    NUMBER(10) DEFAULT 0 NOT NULL,
    metrics_json    JSON,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_chunk_sets_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_chunk_sets_status_ck
        CHECK (status IN ('INGESTING', 'INDEXED', 'ERROR'))
);

CREATE INDEX rag_chunk_sets_document_idx
    ON rag_chunk_sets (document_id, status);

CREATE TABLE rag_kb_chunk_set_bindings (
    knowledge_base_id VARCHAR2(64) NOT NULL,
    document_id       VARCHAR2(64) NOT NULL,
    chunk_set_id      VARCHAR2(64) NOT NULL,
    tenant_id_hash    CHAR(64),
    is_serving        NUMBER(1) DEFAULT 1 NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_kb_chunk_set_bindings_pk
        PRIMARY KEY (knowledge_base_id, document_id, chunk_set_id),
    CONSTRAINT rag_kb_cs_bind_cs_fk
        FOREIGN KEY (chunk_set_id) REFERENCES rag_chunk_sets (chunk_set_id) ON DELETE CASCADE,
    CONSTRAINT rag_kb_cs_bind_serving_ck
        CHECK (is_serving IN (0, 1))
);

CREATE INDEX rag_kb_cs_bind_cs_idx
    ON rag_kb_chunk_set_bindings (chunk_set_id);
""".strip()


def oracle_document_extractions_schema_sql() -> str:
    """variant の extraction 層(1 文書 × N 抽出 = preprocess×parser ごと)の DDL を返す。

    chunk_set_id は preprocess/parser をキーに含むのに抽出が 1 文書 1 つだと parser 軸が潰れる
    問題を解く土台。各 chunk_set は親 extraction_id を指し、extract は parser グループごとに 1 回。
    """
    return """
CREATE TABLE rag_document_extractions (
    extraction_id   VARCHAR2(64) PRIMARY KEY,
    document_id     VARCHAR2(64) NOT NULL,
    tenant_id_hash  CHAR(64),
    recipe_subset   JSON,
    extraction_json JSON,
    status          VARCHAR2(32) DEFAULT 'EXTRACTING' NOT NULL,
    quality_json    JSON,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_document_extractions_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_document_extractions_status_ck
        CHECK (status IN ('EXTRACTING', 'EXTRACTED', 'ERROR'))
);

CREATE INDEX rag_document_extractions_document_idx
    ON rag_document_extractions (document_id, status);
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
        CHECK (status IN ('UPLOADED', 'INGESTING', 'REVIEW', 'INDEXING', 'INDEXED', 'ERROR')),
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
    search_mode           VARCHAR2(16) NOT NULL,
    query_hash            CHAR(64) NOT NULL,
    query_chars           NUMBER(10) NOT NULL,
    filter_keys           JSON,
    memory_plan_id        VARCHAR2(32),
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
    context_adaptive_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_dependency_promoted_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compressed_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compression_saved_chars NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_retrieved_count NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_writeback_count NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_writeback_status VARCHAR2(32) DEFAULT 'skipped' NOT NULL,
    evidence_count        NUMBER(10) DEFAULT 0 NOT NULL,
    support_count         NUMBER(10) DEFAULT 0 NOT NULL,
    structure_count       NUMBER(10) DEFAULT 0 NOT NULL,
    history_count         NUMBER(10) DEFAULT 0 NOT NULL,
    resolver_rejected_count NUMBER(10) DEFAULT 0 NOT NULL,
    insufficient_context_count NUMBER(10) DEFAULT 0 NOT NULL,
    citation_count        NUMBER(10) DEFAULT 0 NOT NULL,
    context_chars         NUMBER(10) DEFAULT 0 NOT NULL,
    context_window_chars  NUMBER(10),
    document_ids          JSON,
    knowledge_base_ids    JSON,
    config_fingerprint    CHAR(64),
    elapsed_ms            NUMBER(12, 3) NOT NULL,
    error_stage           VARCHAR2(64),
    error_type            VARCHAR2(128),
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {table_name}_outcome_ck
        CHECK (outcome IN ('success', 'blocked', 'no_results', 'error')),
    CONSTRAINT {table_name}_search_mode_ck
        CHECK (search_mode IN ('hybrid', 'vector', 'keyword')),
    CONSTRAINT {table_name}_agent_memory_status_ck
        CHECK (agent_memory_writeback_status IN ('skipped', 'saved', 'failed'))
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
    parser_backend         VARCHAR2(80),
    parser_profile         VARCHAR2(80),
    segment_count          NUMBER(10) DEFAULT 0 NOT NULL,
    fallback_count         NUMBER(10) DEFAULT 0 NOT NULL,
    failed_segment_count   NUMBER(10) DEFAULT 0 NOT NULL,
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

CREATE INDEX {table_name}_parser_created_idx
    ON {table_name} (parser_backend, parser_profile, created_at DESC);

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


def oracle_knowledge_graph_schema_sql() -> str:
    """GraphRAG-lite 用の軽量 KG / community summary table DDL を返す。"""
    return """
CREATE TABLE rag_graph_entities (
    entity_id          VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash     CHAR(64),
    knowledge_base_id  VARCHAR2(64),
    canonical_name     VARCHAR2(512) NOT NULL,
    entity_type        VARCHAR2(128),
    description        CLOB,
    confidence         NUMBER(6, 5),
    source_document_ids JSON,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX rag_graph_entities_tenant_name_idx
    ON rag_graph_entities (tenant_id_hash, canonical_name);

CREATE TABLE rag_graph_relationships (
    relationship_id    VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash     CHAR(64),
    knowledge_base_id  VARCHAR2(64),
    source_entity_id   VARCHAR2(64) NOT NULL,
    target_entity_id   VARCHAR2(64) NOT NULL,
    relationship_type  VARCHAR2(128) NOT NULL,
    description        CLOB,
    confidence         NUMBER(6, 5),
    source_document_ids JSON,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_graph_rel_source_fk
        FOREIGN KEY (source_entity_id) REFERENCES rag_graph_entities (entity_id),
    CONSTRAINT rag_graph_rel_target_fk
        FOREIGN KEY (target_entity_id) REFERENCES rag_graph_entities (entity_id)
);

CREATE INDEX rag_graph_rel_source_idx
    ON rag_graph_relationships (tenant_id_hash, source_entity_id);

CREATE INDEX rag_graph_rel_target_idx
    ON rag_graph_relationships (tenant_id_hash, target_entity_id);

CREATE TABLE rag_graph_claims (
    claim_id           VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash     CHAR(64),
    knowledge_base_id  VARCHAR2(64),
    entity_id          VARCHAR2(64),
    claim_text         CLOB NOT NULL,
    confidence         NUMBER(6, 5),
    source_document_id VARCHAR2(64),
    source_chunk_id    VARCHAR2(128),
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_graph_claim_entity_fk
        FOREIGN KEY (entity_id) REFERENCES rag_graph_entities (entity_id)
);

CREATE INDEX rag_graph_claim_entity_idx
    ON rag_graph_claims (tenant_id_hash, entity_id);

CREATE TABLE rag_graph_community_summaries (
    community_id       VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash     CHAR(64),
    knowledge_base_id  VARCHAR2(64),
    level_no           NUMBER(5) DEFAULT 0 NOT NULL,
    title              VARCHAR2(512),
    summary_text       CLOB NOT NULL,
    entity_ids         JSON,
    source_document_ids JSON,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX rag_graph_community_tenant_idx
    ON rag_graph_community_summaries (tenant_id_hash, knowledge_base_id, level_no);

CREATE TABLE rag_graph_entity_chunks (
    entity_id          VARCHAR2(64) NOT NULL,
    chunk_id           VARCHAR2(128) NOT NULL,
    document_id        VARCHAR2(64) NOT NULL,
    tenant_id_hash     CHAR(64),
    relevance_score    NUMBER(8, 6) DEFAULT 1 NOT NULL,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_graph_entity_chunks_pk PRIMARY KEY (entity_id, chunk_id),
    CONSTRAINT rag_graph_entity_chunks_entity_fk
        FOREIGN KEY (entity_id) REFERENCES rag_graph_entities (entity_id)
);

CREATE INDEX rag_graph_entity_chunks_chunk_idx
    ON rag_graph_entity_chunks (tenant_id_hash, chunk_id);
""".strip()


def oracle_agent_memory_schema_sql(table_name: str = "rag_agent_memories") -> str:
    """Agent Memory を Oracle 26ai VECTOR と hash scope で保存する DDL を返す。"""
    return f"""
CREATE TABLE {table_name} (
    memory_id        VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash   CHAR(64),
    user_id_hash     CHAR(64),
    role_id_hash     CHAR(64),
    agent_id_hash    CHAR(64),
    thread_id_hash   CHAR(64),
    trace_id         VARCHAR2(64) NOT NULL,
    memory_text      CLOB NOT NULL,
    metadata_json    JSON,
    embedding        VECTOR(1536, FLOAT32) NOT NULL,
    usefulness_score NUMBER(8, 6) DEFAULT 0.5 NOT NULL,
    eval_count       NUMBER(10) DEFAULT 0 NOT NULL,
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {table_name}_usefulness_ck
        CHECK (usefulness_score >= 0 AND usefulness_score <= 1),
    CONSTRAINT {table_name}_eval_count_ck
        CHECK (eval_count >= 0)
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
    ON {table_name} (memory_text)
    INDEXTYPE IS CTXSYS.CONTEXT;

CREATE INDEX {table_name}_scope_idx
    ON {table_name} (
        tenant_id_hash,
        user_id_hash,
        role_id_hash,
        agent_id_hash,
        thread_id_hash,
        updated_at DESC
    );

CREATE INDEX {table_name}_trace_idx
    ON {table_name} (trace_id);
""".strip()


def oracle_feedback_schema_sql(table_name: str = "rag_citation_feedback") -> str:
    """citation feedback の低機密保存 table DDL を返す。"""
    return f"""
CREATE TABLE {table_name} (
    feedback_id       VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    trace_id          VARCHAR2(64) NOT NULL,
    document_id       VARCHAR2(64) NOT NULL,
    chunk_id          VARCHAR2(128) NOT NULL,
    tenant_id_hash    CHAR(64),
    user_id_hash      CHAR(64),
    rating            VARCHAR2(32) NOT NULL,
    reason            VARCHAR2(64),
    comment_hash      CHAR(64),
    comment_chars     NUMBER(10) DEFAULT 0 NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {table_name}_rating_ck
        CHECK (rating IN ('helpful', 'not_helpful')),
    CONSTRAINT {table_name}_reason_ck
        CHECK (reason IS NULL OR reason IN ('missing_evidence', 'not_relevant', 'answer_untrusted'))
);

CREATE INDEX {table_name}_trace_idx
    ON {table_name} (trace_id);

CREATE INDEX {table_name}_tenant_created_idx
    ON {table_name} (tenant_id_hash, created_at DESC);
""".strip()


def oracle_evaluation_artifact_schema_sql(table_name: str = "rag_evaluation_runs") -> str:
    """nightly / staging の評価結果 artifact table DDL を返す。"""
    return f"""
CREATE TABLE {table_name} (
    evaluation_run_id VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    tenant_id_hash    CHAR(64),
    knowledge_base_ids JSON,
    request_json      JSON NOT NULL,
    result_json       JSON NOT NULL,
    result_sha256     CHAR(64) NOT NULL,
    best_experiment_id VARCHAR2(80),
    passed            NUMBER(1) DEFAULT 0 NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX {table_name}_tenant_created_idx
    ON {table_name} (tenant_id_hash, created_at DESC);

CREATE INDEX {table_name}_best_experiment_idx
    ON {table_name} (best_experiment_id);

CREATE INDEX {table_name}_result_hash_idx
    ON {table_name} (result_sha256);
""".strip()


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
    knowledge_base_ids = _filter_id_values(filters.get("knowledge_base_id"))
    if knowledge_base_ids and not any(
        (knowledge_base_id, document.id) in _LOCAL_STORE.document_knowledge_bases
        for knowledge_base_id in knowledge_base_ids
    ):
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
    if (section_path := filters.get("section_path")) and not _metadata_value_contains(
        chunk.metadata,
        "section_path",
        section_path,
    ):
        return False
    if (source_acl := filters.get("source_acl")) and not _metadata_value_equals(
        chunk.metadata,
        "source_acl",
        source_acl,
    ):
        return False
    return not (
        (document_version := filters.get("document_version"))
        and not _metadata_value_equals(
            chunk.metadata,
            "document_version",
            document_version,
        )
    )


def _local_agent_memory_search(
    query: str,
    embedding: list[float],
    top_k: int,
) -> list[RetrievedChunk]:
    """テスト用 local store の Agent Memory 検索。"""
    query_tokens = _tokens(query)
    scored: list[tuple[StoredAgentMemory, float, float]] = []
    for memory in _LOCAL_STORE.agent_memories.values():
        if not _agent_memory_matches_current_scope(memory):
            continue
        vector_score = _cosine_similarity(embedding, memory.embedding)
        keyword_score = _keyword_score(query_tokens, _tokens(memory.memory_text))
        score = (vector_score * 0.75) + (keyword_score * 0.1) + (memory.usefulness_score * 0.15)
        scored.append((memory, score, vector_score))
    ranked = sorted(
        scored,
        key=lambda item: (
            -item[1],
            -item[0].usefulness_score,
            item[0].updated_at,
            item[0].memory_id,
        ),
    )[:top_k]
    return [
        _agent_memory_chunk_from_row(
            {
                "memory_id": memory.memory_id,
                "memory_text": memory.memory_text,
                "metadata_json": _json_dumps(memory.metadata),
                "usefulness_score": memory.usefulness_score,
                "eval_count": memory.eval_count,
                "vector_score": vector_score,
                "score": score,
            },
            rank=rank,
        )
        for rank, (memory, score, vector_score) in enumerate(ranked, start=1)
    ]


def _agent_memory_matches_current_scope(memory: StoredAgentMemory) -> bool:
    context = current_audit_request_context()
    if not _agent_memory_scope_available():
        return False
    if context.tenant_id_hash is not None and memory.tenant_id_hash != context.tenant_id_hash:
        return False
    if context.user_id_hash is not None and memory.user_id_hash != context.user_id_hash:
        return False
    if context.role_id_hash is not None and memory.role_id_hash != context.role_id_hash:
        return False
    if context.agent_id_hash is not None and memory.agent_id_hash != context.agent_id_hash:
        return False
    return not (
        context.thread_id_hash is not None and memory.thread_id_hash != context.thread_id_hash
    )


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
    if context.allowed_knowledge_base_ids is not None:
        document_knowledge_base_ids = {
            knowledge_base_id
            for knowledge_base_id, document_id in _LOCAL_STORE.document_knowledge_bases
            if document_id == document.id
        }
        if not (document_knowledge_base_ids & set(context.allowed_knowledge_base_ids)):
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


def _context_dependency_match_sql(anchor: RetrievedChunk) -> tuple[str, dict[str, object]]:
    """anchor lineage に一致する dependency candidate を Oracle metadata から絞り込む。"""
    tokens = _context_dependency_anchor_tokens(anchor.metadata)
    if not tokens:
        return (
            "JSON_EXISTS(c.metadata_json, '$.element_ids')\n"
            "                        OR JSON_EXISTS(c.metadata_json, '$.parent_element_ids')\n"
            "                        OR JSON_EXISTS(c.metadata_json, '$.dependency_edges')",
            {},
        )
    clauses: list[str] = []
    binds: dict[str, object] = {}
    for index, token in enumerate(tokens[:16]):
        bind_name = f"dependency_token_{index}"
        clauses.append(
            "LOWER(JSON_SERIALIZE(c.metadata_json RETURNING VARCHAR2(32767))) "
            f"LIKE :{bind_name} ESCAPE '\\'"
        )
        binds[bind_name] = _like_pattern(token)
    return "\n                        OR ".join(clauses), binds


def _context_dependency_anchor_tokens(
    metadata: Mapping[str, MetadataValue],
) -> list[str]:
    """dependency lookup で使う element id / parent id / edge endpoint を抽出する。"""
    tokens: list[str] = []
    tokens.extend(_element_ids_from_metadata(metadata.get("element_ids")))
    tokens.extend(_element_ids_from_metadata(metadata.get("parent_element_ids")))
    tokens.extend(_dependency_edge_endpoint_ids(metadata.get("dependency_edges")))
    return _unique_dependency_tokens(tokens)


def _dependency_edge_endpoint_ids(value: object) -> list[str]:
    """dependency_edges metadata から parent/child endpoint id を取り出す。"""
    payload = value
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes | bytearray):
        return []
    endpoint_ids: list[str] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        for key in ("parent_id", "parent", "child_id", "child"):
            value = item.get(key)
            if isinstance(value, str | int):
                cleaned = str(value).strip()
                if cleaned:
                    endpoint_ids.append(cleaned)
    return endpoint_ids


def _unique_dependency_tokens(values: Sequence[str]) -> list[str]:
    """SQL metadata match に使える短い lineage token を安定順で返す。"""
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if len(cleaned) < 2:
            continue
        normalized = cleaned.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(cleaned)
    return tokens


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
        source_profile=build_source_profile(
            original_file_name=document.file_name,
            sanitized_file_name=document.file_name,
            content_type=document.content_type,
            file_size_bytes=document.file_size_bytes,
            content_sha256=document.content_sha256,
            duplicate_of_document_id=document.duplicate_of_document_id,
        ),
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
        source_profile=build_source_profile(
            original_file_name=document.file_name,
            sanitized_file_name=document.file_name,
            content_type=document.content_type,
            file_size_bytes=document.file_size_bytes,
            content_sha256=document.content_sha256,
            duplicate_of_document_id=document.duplicate_of_document_id,
        ),
    )


def _to_knowledge_base_ref(knowledge_base: StoredKnowledgeBase) -> KnowledgeBaseRef:
    return KnowledgeBaseRef(id=knowledge_base.id, name=knowledge_base.name)


def _to_knowledge_base_summary(
    knowledge_base: StoredKnowledgeBase,
) -> KnowledgeBaseSummary:
    return KnowledgeBaseSummary(
        id=knowledge_base.id,
        name=knowledge_base.name,
        description=knowledge_base.description,
        status=knowledge_base.status,
        default_search_mode=knowledge_base.default_search_mode,
        document_count=knowledge_base.document_count,
        indexed_document_count=knowledge_base.indexed_document_count,
        error_document_count=knowledge_base.error_document_count,
        searchable_chunk_count=knowledge_base.searchable_chunk_count,
        created_at=knowledge_base.created_at,
        updated_at=knowledge_base.updated_at,
        archived_at=knowledge_base.archived_at,
    )


def _to_knowledge_base_detail(knowledge_base: StoredKnowledgeBase) -> KnowledgeBaseDetail:
    return KnowledgeBaseDetail(
        **_to_knowledge_base_summary(knowledge_base).model_dump(),
        retrieval_config=knowledge_base.retrieval_config,
        adapter_config=parse_adapter_config(knowledge_base.retrieval_config),
    )


def updated_copy_knowledge_base(
    knowledge_base: StoredKnowledgeBase,
    **changes: object,
) -> StoredKnowledgeBase:
    return replace(knowledge_base, **cast(Any, changes))


def _to_business_view_summary(view: StoredBusinessView) -> BusinessViewSummary:
    config = parse_business_view_config(view.view_config)
    return BusinessViewSummary(
        id=view.id,
        name=view.name,
        description=view.description,
        status=view.status,
        knowledge_base_count=len(config.normalized_knowledge_base_ids()),
        created_at=view.created_at,
        updated_at=view.updated_at,
        archived_at=view.archived_at,
    )


def _to_business_view_detail(view: StoredBusinessView) -> BusinessViewDetail:
    config = parse_business_view_config(view.view_config)
    return BusinessViewDetail(
        **_to_business_view_summary(view).model_dump(),
        config=config,
        knowledge_bases=[],
    )


def updated_copy_business_view(
    view: StoredBusinessView,
    **changes: object,
) -> StoredBusinessView:
    return replace(view, **cast(Any, changes))


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
