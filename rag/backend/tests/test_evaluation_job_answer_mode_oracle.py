"""品質評価の job の回答の方法の列と migration の実 Oracle のテスト（#5340）。

未到達なら oracle_db fixture が skip する（CI の実 Oracle の job は skip を失敗にする）。
内容はすべて合成の値。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.rag.evaluation_jobs import (
    EvaluationJobRecord,
    EvaluationJobViewer,
    OracleEvaluationJobStore,
)
from app.rag.oracle_schema import oracle_schema_migration_sections, split_sql_statements
from app.schemas.evaluation import EvaluationAnswerMode
from tests import _oracle_test_db

_LEASE = "pytest-worker"
# ほかのテストの行と混ざらないよう、このテストだけの tenant にする。
_TENANT = f"pytest-5340-{uuid4().hex}"[:64]
_OWNER = "o" * 64
_MIGRATION = "20261117_5340_evaluation_job_answer_mode"


@pytest.fixture
def job_ids(oracle_db: None) -> Iterator[list[str]]:
    """テストで作った job の行を消す。"""
    created: list[str] = []
    yield created
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        for job_id in created:
            cursor.execute("DELETE FROM rag_evaluation_jobs WHERE job_id = :id", {"id": job_id})
        connection.commit()


def _viewer() -> EvaluationJobViewer:
    return EvaluationJobViewer(tenant_id_hash=_TENANT, user_id_hash=_OWNER)


async def _create(
    store: OracleEvaluationJobStore, job_ids: list[str], answer_mode: EvaluationAnswerMode | None
) -> str:
    job_id = f"pytest-{uuid4().hex}"
    job_ids.append(job_id)
    await store.create(
        EvaluationJobRecord(
            job_id=job_id,
            kind="run",
            status="RUNNING",
            total_cases=1,
            time_limit_seconds=600,
            created_at=datetime.now(UTC),
            tenant_id_hash=_TENANT,
            user_id_hash=_OWNER,
            lease_owner=_LEASE,
            answer_mode=answer_mode,
        )
    )
    return job_id


def _apply_migration(cursor: Any) -> None:
    section = next(item for item in oracle_schema_migration_sections() if item.name == _MIGRATION)
    for statement in split_sql_statements(section.sql):
        cursor.execute(statement)


async def test_answer_mode_is_saved_and_listed(job_ids: list[str]) -> None:
    store = OracleEvaluationJobStore()
    chat = await _create(store, job_ids, "chat")
    search = await _create(store, job_ids, "search")
    unrecorded = await _create(store, job_ids, None)

    for job_id, expected in ((chat, "chat"), (search, "search"), (unrecorded, None)):
        record = await store.get(job_id, viewer=_viewer())
        assert record is not None
        assert record.answer_mode == expected
    rows, _ = await store.list_visible(viewer=_viewer(), offset=0, limit=25)
    assert {row.job_id: row.answer_mode for row in rows} == {
        chat: "chat",
        search: "search",
        unrecorded: None,
    }


async def test_migration_is_idempotent_and_keeps_existing_rows_null(job_ids: list[str]) -> None:
    """列と CHECK を足すだけ。流し直しても同じで、既存の行は NULL（未記録）のまま。"""
    store = OracleEvaluationJobStore()
    existing = await _create(store, job_ids, None)
    recorded = await _create(store, job_ids, "search")
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        _apply_migration(cursor)
        _apply_migration(cursor)  # 流し直しても同じ（冪等）
        connection.commit()
        cursor.execute(
            "SELECT job_id, answer_mode FROM rag_evaluation_jobs WHERE tenant_id_hash = :tenant",
            {"tenant": _TENANT},
        )
        modes = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT COUNT(*) FROM user_constraints WHERE table_name = 'RAG_EVALUATION_JOBS' "
            "AND constraint_name = 'RAG_EVALUATION_JOBS_ANSWER_MODE_CK'"
        )
        (constraints,) = cursor.fetchone()
        # CHECK は知らない方法を拒否する。
        with pytest.raises(Exception, match="ORA-02290"):
            cursor.execute(
                "UPDATE rag_evaluation_jobs SET answer_mode = 'other' WHERE job_id = :id",
                {"id": existing},
            )
        connection.rollback()
    assert modes == {existing: None, recorded: "search"}
    assert constraints == 1
