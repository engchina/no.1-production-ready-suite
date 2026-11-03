"""品質評価にアップロードした Excel のメタ情報の保存（#4212）。

ファイルの本体はアップロードの保存先に置き、この表は持ち主・内容の hash・保存先の参照だけを持つ。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.clients.oracle import OracleClient, OracleConnectionProtocol, _execute, _execute_count

_COLUMNS = """
    workbook_id, tenant_id_hash, user_id_hash, file_name, content_sha256, byte_size,
    object_storage_path, created_at
"""
_OWNER_SQL = (
    "(tenant_id_hash = :tenant_id_hash OR (tenant_id_hash IS NULL AND :tenant_id_hash IS NULL)) "
    "AND (user_id_hash = :user_id_hash OR (user_id_hash IS NULL AND :user_id_hash IS NULL))"
)


def oracle_evaluation_source_workbook_schema_sql(
    table_name: str = "rag_evaluation_source_workbooks",
) -> str:
    """品質評価にアップロードした Excel のメタ情報（#4212）の table DDL を返す。"""
    return f"""
CREATE TABLE {table_name} (
    workbook_id         VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash      VARCHAR2(64),
    user_id_hash        VARCHAR2(64),
    file_name           VARCHAR2(512 CHAR) NOT NULL,
    content_sha256      VARCHAR2(64) NOT NULL,
    byte_size           NUMBER(12) NOT NULL,
    object_storage_path VARCHAR2(1024 CHAR) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX rag_evaluation_source_workbooks_owner_idx
    ON {table_name} (tenant_id_hash, user_id_hash, content_sha256)
""".strip()


@dataclass(frozen=True)
class SourceWorkbookRow:
    """元の Excel のメタ情報の 1 行。"""

    workbook_id: str
    tenant_id_hash: str | None
    user_id_hash: str | None
    file_name: str
    content_sha256: str
    byte_size: int
    object_storage_path: str
    created_at: datetime | None = None


def _row(values: Mapping[str, object]) -> SourceWorkbookRow:
    created_at = values.get("created_at")
    return SourceWorkbookRow(
        workbook_id=str(values["workbook_id"]),
        tenant_id_hash=str(values["tenant_id_hash"]) if values.get("tenant_id_hash") else None,
        user_id_hash=str(values["user_id_hash"]) if values.get("user_id_hash") else None,
        file_name=str(values["file_name"]),
        content_sha256=str(values["content_sha256"]),
        byte_size=int(str(values["byte_size"])),
        object_storage_path=str(values["object_storage_path"]),
        created_at=(
            created_at
            if isinstance(created_at, datetime) and created_at.tzinfo
            else created_at.replace(tzinfo=UTC)
            if isinstance(created_at, datetime)
            else None
        ),
    )


class OracleEvaluationWorkbookStore:
    """`rag_evaluation_source_workbooks` に保存する（読むときは持ち主で絞る）。"""

    def __init__(self, client: OracleClient | None = None) -> None:
        self._client = client or OracleClient()

    async def find_by_content(
        self, *, tenant_id_hash: str | None, user_id_hash: str | None, content_sha256: str
    ) -> SourceWorkbookRow | None:
        row = await self._client._fetch_one(
            f"""
            SELECT {_COLUMNS}
            FROM rag_evaluation_source_workbooks
            WHERE content_sha256 = :content_sha256
              AND {_OWNER_SQL}
            ORDER BY created_at, workbook_id
            FETCH FIRST 1 ROWS ONLY
            """,
            {
                "content_sha256": content_sha256,
                "tenant_id_hash": tenant_id_hash,
                "user_id_hash": user_id_hash,
            },
        )
        return _row(row) if row else None

    async def create(self, row: SourceWorkbookRow) -> None:
        def operation(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                """
                INSERT INTO rag_evaluation_source_workbooks (
                    workbook_id, tenant_id_hash, user_id_hash, file_name, content_sha256,
                    byte_size, object_storage_path, created_at
                ) VALUES (
                    :workbook_id, :tenant_id_hash, :user_id_hash, :file_name, :content_sha256,
                    :byte_size, :object_storage_path, SYSTIMESTAMP
                )
                """,
                {
                    "workbook_id": row.workbook_id,
                    "tenant_id_hash": row.tenant_id_hash,
                    "user_id_hash": row.user_id_hash,
                    "file_name": row.file_name,
                    "content_sha256": row.content_sha256,
                    "byte_size": row.byte_size,
                    "object_storage_path": row.object_storage_path,
                },
            )

        await self._client._run_transaction(operation)

    async def get(
        self, workbook_id: str, *, tenant_id_hash: str | None, user_id_hash: str | None
    ) -> SourceWorkbookRow | None:
        row = await self._client._fetch_one(
            f"""
            SELECT {_COLUMNS}
            FROM rag_evaluation_source_workbooks
            WHERE workbook_id = :workbook_id
              AND {_OWNER_SQL}
            """,
            {
                "workbook_id": workbook_id,
                "tenant_id_hash": tenant_id_hash,
                "user_id_hash": user_id_hash,
            },
        )
        return _row(row) if row else None

    async def list_unused(
        self, *, tenant_id_hash: str | None, user_id_hash: str | None, older_than_hours: int
    ) -> list[SourceWorkbookRow]:
        rows = await self._client._fetch_all(
            f"""
            SELECT {_COLUMNS}
            FROM rag_evaluation_source_workbooks w
            WHERE {_OWNER_SQL}
              AND w.created_at <= SYSTIMESTAMP - NUMTODSINTERVAL(:older_than_hours, 'HOUR')
              AND NOT EXISTS (
                  SELECT 1 FROM rag_evaluation_jobs j
                  WHERE j.source_workbook_id = w.workbook_id
              )
            """,
            {
                "tenant_id_hash": tenant_id_hash,
                "user_id_hash": user_id_hash,
                "older_than_hours": int(older_than_hours),
            },
        )
        return [_row(row) for row in rows]

    async def delete(self, workbook_id: str) -> bool:
        def operation(connection: OracleConnectionProtocol) -> int:
            return _execute_count(
                connection,
                """
                DELETE FROM rag_evaluation_source_workbooks w
                WHERE w.workbook_id = :workbook_id
                  AND NOT EXISTS (
                      SELECT 1 FROM rag_evaluation_jobs j
                      WHERE j.source_workbook_id = w.workbook_id
                  )
                """,
                {"workbook_id": workbook_id},
            )

        return await self._client._run_transaction(operation) > 0
