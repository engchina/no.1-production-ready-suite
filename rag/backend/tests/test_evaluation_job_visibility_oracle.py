"""品質評価の過去の評価の見え方と、記録の列を足す migration の実 Oracle のテスト（#4811）。

未到達なら oracle_db fixture が skip する（CI の実 Oracle の job は skip を失敗にする）。
内容はすべて合成の値。
"""

from __future__ import annotations

import json
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
from tests import _oracle_test_db

_LEASE = "pytest-worker"
# ほかのテストの行と混ざらないよう、このテストだけの tenant にする。
_TENANT = f"pytest-4811-{uuid4().hex}"[:64]
_OWNER = "o" * 64
_OTHER = "x" * 64
_MIGRATION = "20261110_4811_evaluation_job_visibility"


@pytest.fixture
def job_ids(oracle_db: None) -> Iterator[list[str]]:
    """テストで作った job の行を消す（ケースの行は FK で一緒に消える）。"""
    created: list[str] = []
    yield created
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        for job_id in created:
            cursor.execute("DELETE FROM rag_evaluation_jobs WHERE job_id = :id", {"id": job_id})
        connection.commit()


def _viewer(
    user: str, *, profiles: set[str] | None = None, admin: bool = False
) -> EvaluationJobViewer:
    return EvaluationJobViewer(
        tenant_id_hash=_TENANT,
        user_id_hash=user,
        all_users_visible=admin,
        allowed_search_answer_profile_ids=None if profiles is None else frozenset(profiles),
    )


async def _create(
    store: OracleEvaluationJobStore,
    job_ids: list[str],
    *,
    profile: str | None,
    compare: str | None = None,
) -> str:
    job_id = f"pytest-{uuid4().hex}"
    job_ids.append(job_id)
    await store.create(
        EvaluationJobRecord(
            job_id=job_id,
            kind="run",
            status="RUNNING",
            total_cases=2,
            time_limit_seconds=3600,
            created_at=datetime.now(UTC),
            tenant_id_hash=_TENANT,
            user_id_hash=_OWNER,
            lease_owner=_LEASE,
            search_answer_profile_id=profile,
            compare_search_answer_profile_id=compare,
            user_uuid="owner-uuid",
        )
    )
    return job_id


async def test_visibility_follows_profiles_and_owner(job_ids: list[str]) -> None:
    store = OracleEvaluationJobStore()
    known = await _create(store, job_ids, profile="profile-a")
    compared = await _create(store, job_ids, profile="profile-a", compare="profile-b")
    legacy = await _create(store, job_ids, profile=None)
    assert await store.finish(
        known,
        lease_owner=_LEASE,
        status="SUCCEEDED",
        result={"passed": False, "case_count": 2},
        error_message=None,
        evaluation_run_id=None,
    )

    cases: list[tuple[EvaluationJobViewer, set[str]]] = [
        (_viewer(_OWNER, profiles={"profile-a"}), {known, legacy}),
        (_viewer(_OTHER, profiles={"profile-a"}), {known}),
        (_viewer(_OTHER, profiles={"profile-a", "profile-b"}), {known, compared}),
        (_viewer(_OTHER, profiles=set()), set()),
        (_viewer(_OTHER), {known, compared}),
        (_viewer(_OTHER, admin=True), {known, compared, legacy}),
    ]
    for viewer, expected in cases:
        rows, total = await store.list_visible(viewer=viewer, offset=0, limit=25)
        assert {row.job_id for row in rows} == expected, viewer
        assert total == len(expected)
        for job_id in (known, compared, legacy):
            assert (await store.get(job_id, viewer=viewer) is not None) is (job_id in expected)

    rows, _ = await store.list_visible(viewer=_viewer(_OTHER, admin=True), offset=0, limit=25)
    listed = {row.job_id: row for row in rows}
    # 一覧は結果の本体を読まず、全体の判定だけを読む。
    assert listed[known].passed is False
    assert listed[known].result is None
    assert listed[known].user_uuid == "owner-uuid"
    assert listed[compared].compare_search_answer_profile_id == "profile-b"
    # 新しい順。
    assert [row.job_id for row in rows] == sorted(
        listed, key=lambda job_id: (listed[job_id].created_at, job_id), reverse=True
    )


def _apply_migration(cursor: Any) -> None:
    section = next(item for item in oracle_schema_migration_sections() if item.name == _MIGRATION)
    for statement in split_sql_statements(section.sql):
        cursor.execute(statement)


async def test_migration_fills_profiles_of_existing_rows(job_ids: list[str]) -> None:
    """既存の行のプロファイルを、保存した結果の実行条件とケースごとの結果から埋める。"""
    store = OracleEvaluationJobStore()
    from_manifest = await _create(store, job_ids, profile=None)
    from_cases = await _create(store, job_ids, profile=None)
    unknown = await _create(store, job_ids, profile=None)
    kept = await _create(store, job_ids, profile="profile-kept")
    manifest = {
        "passed": True,
        "run_manifest": {
            "request": {
                "search_answer_profile_id": "profile-a",
                "compare_search_answer_profile_id": "profile-b",
            }
        },
    }
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE rag_evaluation_jobs SET result_json = JSON(:result) WHERE job_id = :id",
            {"result": json.dumps(manifest), "id": from_manifest},
        )
        for pass_index, profile in ((0, "profile-c"), (1, "profile-d")):
            cursor.execute(
                "INSERT INTO rag_evaluation_job_case_results "
                "(job_id, pass_index, case_index, case_id, search_answer_profile_id, result_json) "
                "VALUES (:id, :pass_index, 0, 'row-2', :profile, JSON('{}'))",
                {"id": from_cases, "pass_index": pass_index, "profile": profile},
            )
        connection.commit()
        _apply_migration(cursor)
        _apply_migration(cursor)  # 流し直しても同じ（冪等）
        connection.commit()
        cursor.execute(
            "SELECT job_id, search_answer_profile_id, compare_search_answer_profile_id "
            "FROM rag_evaluation_jobs WHERE tenant_id_hash = :tenant",
            {"tenant": _TENANT},
        )
        filled = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
    assert filled[from_manifest] == ("profile-a", "profile-b")
    assert filled[from_cases] == ("profile-c", "profile-d")
    assert filled[unknown] == (None, None)
    assert filled[kept] == ("profile-kept", None)
    # 埋めた行はプロファイルを使える別の利用者にも見え、分からない行は本人だけのまま。
    other = _viewer(_OTHER, profiles={"profile-a", "profile-b"})
    assert await store.get(from_manifest, viewer=other) is not None
    assert await store.get(unknown, viewer=other) is None
    assert await store.get(unknown, viewer=_viewer(_OWNER, profiles=set())) is not None
