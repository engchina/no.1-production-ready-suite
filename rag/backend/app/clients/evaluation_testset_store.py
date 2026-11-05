"""本人のテストセット下書きを Oracle に永続化する。既存の校正データとは別に持つ。"""

from typing import Protocol

from app.clients.oracle import (
    OracleClient,
    _execute,
    _execute_count_with_input_sizes,
    _json_bind,
    _json_input_sizes,
    _json_loads,
)
from app.rag.request_context import current_audit_request_context
from app.schemas.evaluation_testsets import TestSetDraft, TestSetSummary, draft_summary

TABLE = "rag_evaluation_testsets"
OWNER = (
    "(tenant_id_hash = :tenant_id_hash OR (tenant_id_hash IS NULL AND :tenant_id_hash IS NULL)) "
    "AND user_id_hash = :user_id_hash"
)


def oracle_evaluation_testset_schema_sql() -> str:
    return f"""CREATE TABLE {TABLE} (
    draft_id VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash VARCHAR2(64),
    user_id_hash VARCHAR2(64) NOT NULL,
    name VARCHAR2(200 CHAR) NOT NULL,
    revision NUMBER(10) NOT NULL,
    item_count NUMBER(10) NOT NULL,
    confirmed_count NUMBER(10) NOT NULL,
    draft_json JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);
CREATE INDEX rag_evaluation_testsets_owner_idx
    ON {TABLE} (tenant_id_hash, user_id_hash, updated_at DESC)"""


def owner_binds() -> dict[str, object]:
    context = current_audit_request_context()
    return dict(tenant_id_hash=context.tenant_id_hash, user_id_hash=context.user_id_hash)


class TestSetStore(Protocol):
    async def list(self) -> list[TestSetSummary]: ...
    async def get(self, draft_id: str) -> TestSetDraft | None: ...
    async def create(self, draft: TestSetDraft) -> None: ...
    async def save(self, draft: TestSetDraft, previous_revision: int) -> bool: ...


class MemoryTestSetStore:
    """DB 未設定時の開発・決定論テスト専用。読み書きとも本人と tenant で分ける。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str | None, str | None, str], TestSetDraft] = {}

    def key(self, draft_id: str) -> tuple[str | None, str | None, str]:
        context = current_audit_request_context()
        return context.tenant_id_hash, context.user_id_hash, draft_id

    async def list(self) -> list[TestSetSummary]:
        return sorted(
            [draft_summary(row) for key, row in self.rows.items() if key == self.key(key[2])],
            key=lambda row: row.updated_at,
            reverse=True,
        )

    async def get(self, draft_id: str) -> TestSetDraft | None:
        row = self.rows.get(self.key(draft_id))
        return row.model_copy(deep=True) if row else None

    async def create(self, draft: TestSetDraft) -> None:
        self.rows[self.key(draft.draft_id)] = draft.model_copy(deep=True)

    async def save(self, draft: TestSetDraft, previous_revision: int) -> bool:
        row = self.rows.get(self.key(draft.draft_id))
        if row is None or row.revision != previous_revision:
            return False
        self.rows[self.key(draft.draft_id)] = draft.model_copy(deep=True)
        return True


class OracleTestSetStore:
    def __init__(self, oracle: OracleClient) -> None:
        self.oracle = oracle

    async def list(self) -> list[TestSetSummary]:
        rows = await self.oracle._fetch_all(
            "SELECT draft_id, name, revision, updated_at, item_count, confirmed_count "
            f"FROM {TABLE} WHERE {OWNER} ORDER BY updated_at DESC, draft_id "
            "FETCH FIRST 200 ROWS ONLY",
            owner_binds(),
        )
        return [TestSetSummary.model_validate(row) for row in rows]

    async def get(self, draft_id: str) -> TestSetDraft | None:
        row = await self.oracle._fetch_one(
            f"SELECT draft_json FROM {TABLE} WHERE draft_id = :draft_id AND {OWNER}",
            dict(owner_binds(), draft_id=draft_id),
        )
        return TestSetDraft.model_validate(_json_loads(row["draft_json"])) if row else None

    async def create(self, draft: TestSetDraft) -> None:
        await self.oracle._run_transaction(
            lambda connection: _execute(
                connection,
                f"INSERT INTO {TABLE} (draft_id, tenant_id_hash, user_id_hash, name, revision, "
                "item_count, confirmed_count, "
                "draft_json) VALUES (:draft_id, :tenant_id_hash, :user_id_hash, :name, :revision, "
                ":item_count, :confirmed_count, "
                ":draft_json)",
                dict(
                    owner_binds(),
                    draft_id=draft.draft_id,
                    name=draft.name,
                    revision=draft.revision,
                    item_count=len(draft.items),
                    confirmed_count=sum(item.confirmed for item in draft.items),
                    draft_json=_json_bind(draft.model_dump(mode="json")),
                ),
                input_sizes=_json_input_sizes("draft_json"),
            )
        )

    async def save(self, draft: TestSetDraft, previous_revision: int) -> bool:
        count = await self.oracle._run_transaction(
            lambda connection: _execute_count_with_input_sizes(
                connection,
                f"UPDATE {TABLE} SET name = :name, revision = :revision, draft_json = :draft_json, "
                "item_count = :item_count, confirmed_count = :confirmed_count, "
                "updated_at = SYSTIMESTAMP "
                f"WHERE draft_id = :draft_id AND revision = :previous_revision AND {OWNER}",
                dict(
                    owner_binds(),
                    draft_id=draft.draft_id,
                    name=draft.name,
                    revision=draft.revision,
                    item_count=len(draft.items),
                    confirmed_count=sum(item.confirmed for item in draft.items),
                    previous_revision=previous_revision,
                    draft_json=_json_bind(draft.model_dump(mode="json")),
                ),
                input_sizes=_json_input_sizes("draft_json"),
            )
        )
        return count == 1
