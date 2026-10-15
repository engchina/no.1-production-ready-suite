"""取込の job の順番待ち(前にある job の数・同時に処理できる数)のテスト(#2026)。

worker が claim する順(claim できる QUEUED の job を ``queued_at``・``job_id`` の昇順。
``list_dispatchable_ingestion_jobs``)と同じ順で、前にある job を数える。SQL の形は fake の pool で、
実際の値は実 Oracle で確かめる(未到達なら oracle_db fixture が skip し、作成行は
cleanup_to_baseline で後始末する)。一覧の API が付ける ``queue_wait`` は fake の client で確かめる。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api.routes import documents as documents_route
from app.clients.oracle import OracleClient
from app.config import Settings
from app.schemas.document import (
    DocumentSummary,
    FileStatus,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
)
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


def _selects(pool: FakeOraclePool) -> list[str]:
    return [
        call.statement
        for call in pool.connection.calls
        if call.statement.lstrip().upper().startswith("SELECT")
    ]


async def test_queue_positions_skip_query_without_targets() -> None:
    """対象の job も文書も無ければ問い合わせない。"""
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    assert await client.list_ingestion_queue_positions() == []
    assert pool.connection.calls == []


async def test_queue_positions_count_like_worker_claim_order() -> None:
    """前にある job は、worker が claim できる QUEUED の job を FIFO の順で数える。"""
    pool = FakeOraclePool(
        execute_results=[
            [{"job_id": "job-b", "document_id": "doc-b", "jobs_ahead": 3}],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    rows = await client.list_ingestion_queue_positions(
        job_ids=["job-b"], document_ids=["doc-b", "doc-c"]
    )

    assert rows == [{"job_id": "job-b", "document_id": "doc-b", "jobs_ahead": 3}]
    [statement] = _selects(pool)
    # 対象は QUEUED の job で、job か文書で選ぶ。
    assert "WHERE t.status = 'QUEUED'" in statement
    assert "t.job_id IN (:queue_job_id_0)" in statement
    assert "t.document_id IN (:queue_document_id_0, :queue_document_id_1)" in statement
    # 前にある job: claim できる(同じ文書の job が RUNNING でない)QUEUED の job を、
    # worker の ORDER BY queued_at, job_id と同じ順で数える。
    assert "ahead.status = 'QUEUED'" in statement
    assert "ahead.queued_at < t.queued_at" in statement
    assert "ahead.queued_at = t.queued_at AND ahead.job_id < t.job_id" in statement
    assert "active_job.document_id = ahead.document_id" in statement
    assert "active_job.status = 'RUNNING'" in statement
    # 一覧の状態はレシピ1の状態のため、job のレシピの slot と文書の最初の slot を返す。
    assert "r.slot_no AS recipe_slot_no" in statement
    assert "AS first_slot_no" in statement
    binds = pool.connection.calls[0].parameters
    assert binds["queue_job_id_0"] == "job-b"
    assert binds["queue_document_id_1"] == "doc-c"


class _QueueOracle:
    """一覧の API の `_attach_queue_waits` 用の fake。"""

    def __init__(self, rows: list[dict[str, object]] | Exception) -> None:
        self._rows = rows
        self.document_ids: list[str] = []

    async def list_ingestion_queue_positions(
        self, *, job_ids: Sequence[str] = (), document_ids: Sequence[str] = ()
    ) -> list[dict[str, object]]:
        del job_ids
        self.document_ids = list(document_ids)
        if isinstance(self._rows, Exception):
            raise self._rows
        return self._rows


def _summary(document_id: str, status: FileStatus = FileStatus.REVIEW) -> DocumentSummary:
    return DocumentSummary(
        id=document_id,
        file_name=f"{document_id}.pdf",
        status=status,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


async def test_list_attaches_queue_wait_of_first_recipe_only() -> None:
    """一覧はレシピ1の QUEUED の job だけに順番待ちを付ける(状態列がレシピ1の状態のため)。"""
    queued_at = datetime(2026, 1, 2, tzinfo=UTC)
    oracle = _QueueOracle(
        [
            {
                "job_id": "job-a",
                "document_id": "doc-a",
                "phase": "CHUNK",
                "queued_at": queued_at,
                "recipe_slot_no": 1,
                "first_slot_no": 1,
                "jobs_ahead": 4,
            },
            # レシピ2の job は一覧の状態(レシピ1)に関係しない。
            {
                "job_id": "job-b",
                "document_id": "doc-b",
                "phase": "EXTRACT",
                "queued_at": queued_at,
                "recipe_slot_no": 2,
                "first_slot_no": 1,
                "jobs_ahead": 5,
            },
            # レシピの行が無い文書(migration 前)の job は文書の job。
            {
                "job_id": "job-c",
                "document_id": "doc-c",
                "phase": "PREPROCESS",
                "queued_at": queued_at,
                "recipe_slot_no": None,
                "first_slot_no": None,
                "jobs_ahead": 0,
            },
        ]
    )
    settings = Settings.model_construct(ingestion_queue_worker_concurrency=4)
    documents = [_summary("doc-a"), _summary("doc-b"), _summary("doc-c", FileStatus.UPLOADED)]

    result = await documents_route._attach_queue_waits(oracle, documents, settings)  # type: ignore[arg-type]

    assert oracle.document_ids == ["doc-a", "doc-b", "doc-c"]
    waits = {document.id: document.queue_wait for document in result}
    assert waits["doc-b"] is None
    wait_a = waits["doc-a"]
    assert wait_a is not None
    assert wait_a.phase == IngestionJobPhase.CHUNK
    assert wait_a.job_id == "job-a"
    assert wait_a.queued_at == queued_at
    assert wait_a.jobs_ahead == 4
    assert wait_a.concurrency == 4
    wait_c = waits["doc-c"]
    assert wait_c is not None
    assert wait_c.phase == IngestionJobPhase.PREPROCESS
    assert wait_c.jobs_ahead == 0
    # 状態そのものは変えない(画面が順番待ちを状態の上に重ねる)。
    assert [document.status for document in result] == [
        FileStatus.REVIEW,
        FileStatus.REVIEW,
        FileStatus.UPLOADED,
    ]


async def test_list_keeps_documents_when_queue_positions_fail() -> None:
    """順番を読めなくても一覧は返す(表示の補助のため)。"""
    oracle = _QueueOracle(RuntimeError("DPY-6005"))
    documents = [_summary("doc-a")]
    settings = Settings.model_construct(ingestion_queue_worker_concurrency=2)

    result = await documents_route._attach_queue_waits(oracle, documents, settings)  # type: ignore[arg-type]

    assert [document.queue_wait for document in result] == [None]


async def test_detail_queue_positions_fall_back_to_empty_on_error() -> None:
    """詳細の工程も、順番を読めなければ前の件数を出さずに返す。"""
    oracle = _QueueOracle(RuntimeError("DPY-6005"))
    job = IngestionJob(
        id="job-a",
        document_id="doc-a",
        recipe_id="recipe-a",
        status=IngestionJobStatus.QUEUED,
        phase=IngestionJobPhase.CHUNK,
        parser_profile="docling",
        queued_at=datetime.now(UTC),
    )

    assert await documents_route._ingestion_queue_positions(oracle, [job]) == {}  # type: ignore[arg-type]


def _job(
    *,
    document_id: str,
    recipe_id: str,
    status: IngestionJobStatus,
    queued_at: datetime,
    phase: IngestionJobPhase = IngestionJobPhase.CHUNK,
) -> IngestionJob:
    return IngestionJob(
        id=f"job_queue_pos_{uuid4().hex[:16]}",
        document_id=document_id,
        recipe_id=recipe_id,
        recipe_revision=1,
        status=status,
        phase=phase,
        parser_profile="local_text_structure",
        queued_at=queued_at,
    )


async def _document_with_recipe(client: OracleClient) -> tuple[str, dict[str, object]]:
    file_name = f"queue-pos-{uuid4().hex[:12]}.txt"
    detail = await client.create_document(
        file_name=file_name,
        object_storage_path=f"oci://bucket/{file_name}",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(detail.id)
    return detail.id, recipe


@pytest.mark.usefixtures("oracle_db")
async def test_queue_positions_follow_worker_order_in_oracle() -> None:
    """前の件数は FIFO の順で、claim できない(同じ文書の job が RUNNING)job を数えない。"""
    client = OracleClient()
    # 他のテストの job と順番が重ならないよう、過去の時刻に寄せる。
    base = datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=random.randint(0, 10_000_000))
    doc_a, recipe_a = await _document_with_recipe(client)
    doc_b, recipe_b1 = await _document_with_recipe(client)
    recipe_b2 = await client.create_document_recipe(doc_b)
    doc_c, recipe_c = await _document_with_recipe(client)

    job_a = await client.create_ingestion_job(
        _job(
            document_id=doc_a,
            recipe_id=str(recipe_a["recipe_id"]),
            status=IngestionJobStatus.QUEUED,
            queued_at=base,
        )
    )
    # 文書 B はレシピ1の job が RUNNING のため、レシピ2の QUEUED の job は claim できない。
    await client.create_ingestion_job(
        _job(
            document_id=doc_b,
            recipe_id=str(recipe_b1["recipe_id"]),
            status=IngestionJobStatus.RUNNING,
            queued_at=base - timedelta(hours=1),
        )
    )
    job_x = await client.create_ingestion_job(
        _job(
            document_id=doc_b,
            recipe_id=str(recipe_b2["recipe_id"]),
            status=IngestionJobStatus.QUEUED,
            queued_at=base + timedelta(seconds=1),
            phase=IngestionJobPhase.EXTRACT,
        )
    )
    job_c = await client.create_ingestion_job(
        _job(
            document_id=doc_c,
            recipe_id=str(recipe_c["recipe_id"]),
            status=IngestionJobStatus.QUEUED,
            queued_at=base + timedelta(seconds=2),
        )
    )

    rows = await client.list_ingestion_queue_positions(job_ids=[job_a.id, job_x.id, job_c.id])
    ahead = {str(row["job_id"]): int(str(row["jobs_ahead"])) for row in rows}
    assert set(ahead) == {job_a.id, job_x.id, job_c.id}
    # A は数え、claim できない X は数えない。
    assert ahead[job_x.id] == ahead[job_a.id] + 1
    assert ahead[job_c.id] == ahead[job_a.id] + 1

    # worker が claim する順(list_dispatchable_ingestion_jobs)で、C の前にちょうど ahead[C] 件ある。
    dispatchable = await client.list_dispatchable_ingestion_jobs(limit=ahead[job_c.id] + 1)
    assert dispatchable[-1].id == job_c.id
    assert job_x.id not in {job.id for job in dispatchable}

    # 文書で選ぶと、job のレシピの slot と文書の最初の slot を返す(一覧はレシピ1だけを見る)。
    [row_b] = await client.list_ingestion_queue_positions(document_ids=[doc_b])
    assert row_b["job_id"] == job_x.id
    assert int(str(row_b["recipe_slot_no"])) == 2
    assert int(str(row_b["first_slot_no"])) == 1
