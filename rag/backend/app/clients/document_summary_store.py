"""文書全体の要約の保存と、要約の chunk の索引への入れ替え（#4793）。

- ``rag_document_summaries``: 文書ごとに 1 行。要約の文・話題の語・状態（``PENDING`` /
  ``READY`` / ``FAILED``）と、作っている途中の印（``generating_since``）を持つ。要約は文書の
  属性で、処理レシピ・chunk_set をまたいで 1 つ（文書を消すと一緒に消える）。
- 要約の chunk: 有効な chunk_set に 1 つだけ入れる ``content_kind=document_summary`` の chunk。
  同じ chunk_set の前の要約の chunk を消してから入れるので、何度実行しても 1 つのまま。

要約の文は取込の成果物で、利用者の見え方の条件（tenant・ナレッジベース）は文書の行で確かめる。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from rag_parser_core.extraction import DOCUMENT_SUMMARY_CONTENT_KIND

from app.clients.oracle import (
    OracleClient,
    OracleConnectionProtocol,
    _execute,
    _fetch_one,
    _int_value,
    _json_bind,
    _json_input_sizes,
    _optional_datetime,
    _optional_int,
    _optional_str,
    _select_document_state,
)
from app.rag.chunking import Chunk

DOCUMENT_SUMMARY_TABLE = "rag_document_summaries"
# 要約の chunk の印（metadata）。content_kind と一緒に付ける。
DOCUMENT_SUMMARY_KEY = "document_summary"

STATUS_PENDING = "PENDING"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"

# 要約の chunk だけを選ぶ SQL の条件（``c`` = rag_chunks）。
SUMMARY_CHUNK_PREDICATE_SQL = (
    f"JSON_VALUE(c.metadata_json, '$.content_kind') = '{DOCUMENT_SUMMARY_CONTENT_KIND}'"
)


def oracle_document_summary_schema_sql() -> str:
    return f"""
CREATE TABLE {DOCUMENT_SUMMARY_TABLE} (
    document_id      VARCHAR2(64) NOT NULL,
    status           VARCHAR2(16) NOT NULL,
    summary_text     CLOB,
    topics_json      JSON,
    input_hash       VARCHAR2(64),
    input_truncated  NUMBER(1) DEFAULT 0 NOT NULL,
    input_chars      NUMBER(10),
    source_chars     NUMBER(10),
    chunk_set_id     VARCHAR2(64),
    failure_code     VARCHAR2(40),
    generating_since TIMESTAMP WITH TIME ZONE,
    generated_at     TIMESTAMP WITH TIME ZONE,
    updated_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT {DOCUMENT_SUMMARY_TABLE}_pk PRIMARY KEY (document_id),
    CONSTRAINT {DOCUMENT_SUMMARY_TABLE}_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT {DOCUMENT_SUMMARY_TABLE}_status_ck
        CHECK (status IN ('{STATUS_PENDING}', '{STATUS_READY}', '{STATUS_FAILED}')),
    CONSTRAINT {DOCUMENT_SUMMARY_TABLE}_truncated_ck CHECK (input_truncated IN (0, 1))
);
""".strip()


@dataclass(frozen=True)
class DocumentSummaryRow:
    """保存した文書の要約の 1 行。"""

    document_id: str
    status: str
    summary_text: str | None
    topics: tuple[str, ...]
    input_hash: str | None
    input_truncated: bool
    input_chars: int | None
    source_chars: int | None
    chunk_set_id: str | None
    failure_code: str | None
    generating_since: datetime | None
    generated_at: datetime | None


@dataclass(frozen=True)
class SummaryTarget:
    """要約を作る対象（文書の有効な chunk_set と、その元の抽出結果）。"""

    chunk_set_id: str
    recipe_id: str
    extraction_recipe_id: str


def _topics(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _row(row: dict[str, object]) -> DocumentSummaryRow:
    text = row.get("summary_text")
    return DocumentSummaryRow(
        document_id=str(row["document_id"]),
        status=str(row["status"]),
        summary_text=None if text is None else str(text),
        topics=_topics(row.get("topics_json")),
        input_hash=_optional_str(row.get("input_hash")),
        input_truncated=_int_value(row.get("input_truncated") or 0) == 1,
        input_chars=_optional_int(row.get("input_chars")),
        source_chars=_optional_int(row.get("source_chars")),
        chunk_set_id=_optional_str(row.get("chunk_set_id")),
        failure_code=_optional_str(row.get("failure_code")),
        generating_since=_optional_datetime(row.get("generating_since")),
        generated_at=_optional_datetime(row.get("generated_at")),
    )


class DocumentSummaryStore:
    """文書の要約の読み書き（``OracleClient`` の接続と transaction を使う）。"""

    def __init__(self, oracle: OracleClient | None = None) -> None:
        self._oracle = oracle or OracleClient()

    async def get(self, document_id: str) -> DocumentSummaryRow | None:
        row = await self._oracle._fetch_one(
            f"""
            SELECT document_id, status, summary_text, topics_json, input_hash, input_truncated,
                   input_chars, source_chars, chunk_set_id, failure_code, generating_since,
                   generated_at
            FROM {DOCUMENT_SUMMARY_TABLE}
            WHERE document_id = :document_id
            """,
            {"document_id": document_id},
        )
        return None if row is None else _row(row)

    async def targets(self, document_id: str) -> list[SummaryTarget]:
        """文書の有効な chunk_set（処理レシピの順）。要約の chunk を入れる先。"""
        rows = await self._oracle._fetch_all(
            """
            SELECT cs.chunk_set_id, r.recipe_id,
                   NVL(cs.extraction_recipe_id, r.active_extraction_recipe_id)
                       AS extraction_recipe_id
            FROM rag_document_recipes r
            JOIN rag_chunk_sets cs ON cs.recipe_id = r.recipe_id
            WHERE r.document_id = :document_id
              AND cs.document_id = :document_id
              AND cs.is_active = 1
              AND cs.status = 'INDEXED'
            ORDER BY r.slot_no, r.recipe_id
            """,
            {"document_id": document_id},
        )
        return [
            SummaryTarget(
                chunk_set_id=str(row["chunk_set_id"]),
                recipe_id=str(row["recipe_id"]),
                extraction_recipe_id=str(row["extraction_recipe_id"]),
            )
            for row in rows
            if row.get("extraction_recipe_id")
        ]

    async def mark_generating(self, document_id: str) -> None:
        """作り始めの印を付ける（行が無ければ ``PENDING`` で作る。前の要約は残す）。"""
        await self._oracle._run_transaction(
            lambda connection: _execute(
                connection,
                f"""
                MERGE INTO {DOCUMENT_SUMMARY_TABLE} t
                USING (SELECT :document_id AS document_id FROM dual) s
                ON (t.document_id = s.document_id)
                WHEN MATCHED THEN UPDATE SET
                    t.generating_since = SYSTIMESTAMP, t.updated_at = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT (document_id, status, generating_since)
                    VALUES (:document_id, '{STATUS_PENDING}', SYSTIMESTAMP)
                """,
                {"document_id": document_id},
            )
        )

    async def save_failed(self, document_id: str, *, chunk_set_id: str | None, code: str) -> None:
        """失敗を記録する。同じ版の索引に入っている要約（READY）は消さない。

        取込の後の失敗では、前の版の要約の文を残さない（新しい版の内容と合わないため）。
        作り直しの失敗では、今の版の要約と要約の chunk をそのまま残す。
        """
        await self._oracle._run_transaction(
            lambda connection: _execute(
                connection,
                f"""
                MERGE INTO {DOCUMENT_SUMMARY_TABLE} t
                USING (SELECT :document_id AS document_id FROM dual) s
                ON (t.document_id = s.document_id)
                WHEN MATCHED THEN UPDATE SET
                    t.status = CASE
                        WHEN t.status = '{STATUS_READY}' AND t.chunk_set_id = :chunk_set_id
                        THEN t.status ELSE '{STATUS_FAILED}' END,
                    t.summary_text = CASE
                        WHEN t.status = '{STATUS_READY}' AND t.chunk_set_id = :chunk_set_id
                        THEN t.summary_text ELSE NULL END,
                    t.topics_json = CASE
                        WHEN t.status = '{STATUS_READY}' AND t.chunk_set_id = :chunk_set_id
                        THEN t.topics_json ELSE NULL END,
                    t.input_hash = CASE
                        WHEN t.status = '{STATUS_READY}' AND t.chunk_set_id = :chunk_set_id
                        THEN t.input_hash ELSE NULL END,
                    t.failure_code = :failure_code,
                    t.generating_since = NULL,
                    t.updated_at = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT (document_id, status, chunk_set_id, failure_code)
                    VALUES (:document_id, '{STATUS_FAILED}', :chunk_set_id, :failure_code)
                """,
                {
                    "document_id": document_id,
                    "chunk_set_id": chunk_set_id,
                    "failure_code": code[:40],
                },
            )
        )

    async def save_ready(
        self,
        document_id: str,
        *,
        chunk_set_ids: Sequence[str],
        summary_text: str,
        topics: Sequence[str],
        input_hash: str,
        input_truncated: bool,
        input_chars: int,
        source_chars: int,
        chunk: Chunk,
        embedding: Sequence[float],
    ) -> int:
        """要約を保存し、要約の chunk を渡した chunk_set に 1 つずつ入れる（1 transaction）。

        chunk を入れた chunk_set の数を返す（切替で有効でなくなった chunk_set には入れない）。
        """
        self._oracle._validate_embedding_width(list(embedding), "document summary embedding")

        def operation(connection: OracleConnectionProtocol) -> int:
            document = _select_document_state(connection, document_id)
            if document is None:
                raise KeyError(f"document_id={document_id} は存在しません。")
            inserted = 0
            for chunk_set_id in chunk_set_ids:
                target = _fetch_one(
                    connection,
                    """
                    SELECT chunk_set_id FROM rag_chunk_sets
                    WHERE chunk_set_id = :chunk_set_id AND document_id = :document_id
                      AND is_active = 1 AND status = 'INDEXED'
                    FOR UPDATE
                    """,
                    {"chunk_set_id": chunk_set_id, "document_id": document_id},
                )
                if target is None:
                    continue
                _execute(
                    connection,
                    f"""
                    DELETE FROM rag_chunks c
                    WHERE c.chunk_set_id = :chunk_set_id AND {SUMMARY_CHUNK_PREDICATE_SQL}
                    """,
                    {"chunk_set_id": chunk_set_id},
                )
                last = _fetch_one(
                    connection,
                    "SELECT NVL(MAX(chunk_index), -1) AS last_index FROM rag_chunks "
                    "WHERE chunk_set_id = :chunk_set_id",
                    {"chunk_set_id": chunk_set_id},
                )
                index = _int_value((last or {}).get("last_index", -1)) + 1
                placed = Chunk(
                    text=chunk.text,
                    index=index,
                    start_offset=0,
                    end_offset=len(chunk.text),
                    metadata=dict(chunk.metadata),
                )
                row = self._oracle._chunk_insert_rows(
                    document_id, document, [placed], [embedding], chunk_set_id=chunk_set_id
                )[0]
                _execute(
                    connection,
                    """
                    INSERT INTO rag_chunks (
                        chunk_id, document_id, tenant_id_hash, chunk_index, chunk_text,
                        search_text, metadata_json, embedding, chunk_set_id
                    ) VALUES (
                        :chunk_id, :document_id, :tenant_id_hash, :chunk_index, :chunk_text,
                        :search_text, :metadata_json, :embedding, :chunk_set_id
                    )
                    """,
                    row,
                    input_sizes=_json_input_sizes("metadata_json"),
                )
                # chunk の数の記録を、要約の chunk を入れた後の行の数に合わせる。
                _execute(
                    connection,
                    """
                    UPDATE rag_chunk_sets SET
                        chunk_count = (
                            SELECT COUNT(*) FROM rag_chunks WHERE chunk_set_id = :chunk_set_id
                        ),
                        vector_count = (
                            SELECT COUNT(*) FROM rag_chunks WHERE chunk_set_id = :chunk_set_id
                        )
                    WHERE chunk_set_id = :chunk_set_id
                    """,
                    {"chunk_set_id": chunk_set_id},
                )
                inserted += 1
            _execute(
                connection,
                f"""
                MERGE INTO {DOCUMENT_SUMMARY_TABLE} t
                USING (SELECT :document_id AS document_id FROM dual) s
                ON (t.document_id = s.document_id)
                WHEN MATCHED THEN UPDATE SET
                    t.status = '{STATUS_READY}',
                    t.summary_text = :summary_text,
                    t.topics_json = :topics_json,
                    t.input_hash = :input_hash,
                    t.input_truncated = :input_truncated,
                    t.input_chars = :input_chars,
                    t.source_chars = :source_chars,
                    t.chunk_set_id = :chunk_set_id,
                    t.failure_code = NULL,
                    t.generating_since = NULL,
                    t.generated_at = SYSTIMESTAMP,
                    t.updated_at = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT (
                    document_id, status, summary_text, topics_json, input_hash, input_truncated,
                    input_chars, source_chars, chunk_set_id, generated_at
                ) VALUES (
                    :document_id, '{STATUS_READY}', :summary_text, :topics_json, :input_hash,
                    :input_truncated, :input_chars, :source_chars, :chunk_set_id, SYSTIMESTAMP
                )
                """,
                {
                    "document_id": document_id,
                    "summary_text": summary_text,
                    "topics_json": _json_bind(list(topics)),
                    "input_hash": input_hash,
                    "input_truncated": 1 if input_truncated else 0,
                    "input_chars": input_chars,
                    "source_chars": source_chars,
                    "chunk_set_id": chunk_set_ids[0] if chunk_set_ids else None,
                },
                input_sizes=_json_input_sizes("topics_json"),
            )
            return inserted

        return int(await self._oracle._run_transaction(operation))
