"""取込の job を取る順番のテスト(#2027)。

一括投入で段ごとに全件がそろって進む(幅優先)のではなく、始めた文書の続きの段を新しい文書の
最初の段より先に取る(深さ優先 / パイプライン)。続きの段の繰り上げには上限(aging)があり、
新しい文書の最初の段が飢餓にならない。SQL の形は fake の pool で、実際の順番は実 Oracle で
確かめる(未到達なら oracle_db fixture が skip し、作成行は cleanup_to_baseline で後始末する)。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.clients.oracle import (
    OracleClient,
    ingestion_dispatch_order_binds,
    ingestion_dispatch_order_sql,
)
from app.config import get_settings
from app.schemas.document import (
    INGESTION_HEAVY_PHASES,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
)
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


def test_dispatch_order_sql_uses_alias_and_window_bind() -> None:
    """順番の式は別名の表を指し、繰り上げの上限を bind で受ける(#2026 の数え方でも使う)。"""
    sql = ingestion_dispatch_order_sql("q")

    assert "start_job.document_id = q.document_id" in sql
    assert "DECODE(start_job.recipe_id, q.recipe_id, 1, 0) = 1" in sql
    assert "start_job.phase IN ('EXTRACT', 'PREPROCESS')" in sql
    assert "q.phase IN ('CHUNK', 'INDEX')" in sql
    assert ":dispatch_priority_window_seconds" in sql
    assert " j." not in sql
    settings = _oci_settings().model_copy(
        update={"ingestion_queue_pipeline_priority_window_seconds": 0}
    )
    assert ingestion_dispatch_order_binds(settings) == {"dispatch_priority_window_seconds": 0}


async def test_list_dispatchable_excludes_full_phases() -> None:
    """重い段の上限に達した worker は、重い段を除いて軽い段の job を取る。"""
    pool = FakeOraclePool(execute_results=[[]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    assert (
        await client.list_dispatchable_ingestion_jobs(
            limit=3, exclude_phases=INGESTION_HEAVY_PHASES
        )
        == []
    )

    [call] = pool.connection.calls
    assert "AND NOT (j.phase IN (:exclude_phase_0, :exclude_phase_1))" in call.statement
    assert call.parameters["exclude_phase_0"] == "EXTRACT"
    assert call.parameters["exclude_phase_1"] == "PREPROCESS"
    assert call.parameters["limit"] == 3


_BASE = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


async def _document(client: OracleClient) -> str:
    file_name = f"dispatch-order-{uuid4().hex[:12]}.txt"
    detail = await client.create_document(
        file_name=file_name,
        object_storage_path=f"oci://bucket/{file_name}",
        content_type="text/plain",
    )
    return detail.id


async def _job(
    client: OracleClient,
    document_id: str,
    phase: IngestionJobPhase,
    *,
    queued_after_seconds: int,
    status: IngestionJobStatus = IngestionJobStatus.QUEUED,
) -> str:
    queued_at = _BASE + timedelta(seconds=queued_after_seconds)
    finished = status == IngestionJobStatus.SUCCEEDED
    job = await client.create_ingestion_job(
        IngestionJob(
            id=f"job_dispatch_order_{uuid4().hex[:16]}",
            document_id=document_id,
            status=status,
            phase=phase,
            parser_profile="local_text_structure",
            queued_at=queued_at,
            started_at=queued_at if finished else None,
            finished_at=queued_at + timedelta(seconds=1) if finished else None,
            attempt_count=1 if finished else 0,
        )
    )
    return job.id


async def _dispatch_order(
    client: OracleClient,
    job_ids: Sequence[str],
    *,
    exclude_phases: Sequence[IngestionJobPhase] = (),
) -> list[str]:
    jobs = await client.list_dispatchable_ingestion_jobs(limit=50, exclude_phases=exclude_phases)
    ours = set(job_ids)
    return [job.id for job in jobs if job.id in ours]


@pytest.mark.usefixtures("oracle_db")
async def test_started_documents_continue_before_new_documents_start_in_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一括投入で、ファイル準備が終わった文書の続きの段を、後ろの文書の最初の段より先に取る。

    投入の順(FIFO)では、続きの段がキューの末尾に付き、後ろの文書のファイル準備が全部終わるまで
    最初の文書が索引まで進まなかった。
    """
    monkeypatch.setattr(get_settings(), "ingestion_queue_pipeline_priority_window_seconds", 3600)
    client = OracleClient()
    first, second, third, fourth = [await _document(client) for _ in range(4)]
    # 4 件を一括投入(1 秒ずつ)。最初の 2 件はファイル準備・抽出が終わっている。
    await _job(
        client,
        first,
        IngestionJobPhase.PREPROCESS,
        queued_after_seconds=0,
        status=IngestionJobStatus.SUCCEEDED,
    )
    await _job(
        client,
        second,
        IngestionJobPhase.PREPROCESS,
        queued_after_seconds=1,
        status=IngestionJobStatus.SUCCEEDED,
    )
    third_start = await _job(client, third, IngestionJobPhase.PREPROCESS, queued_after_seconds=2)
    fourth_start = await _job(client, fourth, IngestionJobPhase.PREPROCESS, queued_after_seconds=3)
    # 2 件目の Chunk 作成が先に終わって索引を投入し、1 件目の Chunk 作成は後から投入された。
    await _job(
        client,
        second,
        IngestionJobPhase.CHUNK,
        queued_after_seconds=60,
        status=IngestionJobStatus.SUCCEEDED,
    )
    second_index = await _job(client, second, IngestionJobPhase.INDEX, queued_after_seconds=90)
    first_chunk = await _job(client, first, IngestionJobPhase.CHUNK, queued_after_seconds=120)
    ours = [third_start, fourth_start, second_index, first_chunk]

    # 文書の取込の始まりの古い順。続きの段は、後ろの文書の最初の段より先。
    assert await _dispatch_order(client, ours) == [
        first_chunk,
        second_index,
        third_start,
        fourth_start,
    ]
    # 重い段の上限に達した worker は、軽い段だけを同じ順番で取る。
    assert await _dispatch_order(client, ours, exclude_phases=sorted(INGESTION_HEAVY_PHASES)) == [
        first_chunk,
        second_index,
    ]

    # 繰り上げの上限が 0 なら投入の順(FIFO)。
    monkeypatch.setattr(get_settings(), "ingestion_queue_pipeline_priority_window_seconds", 0)
    assert await _dispatch_order(client, ours) == [
        third_start,
        fourth_start,
        second_index,
        first_chunk,
    ]


@pytest.mark.usefixtures("oracle_db")
async def test_continuation_does_not_overtake_jobs_waiting_longer_than_window_in_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """続きの段の繰り上げは上限の秒数まで。それより前から待つ新しい文書の最初の段は追い越さない。"""
    monkeypatch.setattr(get_settings(), "ingestion_queue_pipeline_priority_window_seconds", 30)
    client = OracleClient()
    started, waiting, recent = [await _document(client) for _ in range(3)]
    await _job(
        client,
        started,
        IngestionJobPhase.PREPROCESS,
        queued_after_seconds=0,
        status=IngestionJobStatus.SUCCEEDED,
    )
    waiting_start = await _job(
        client, waiting, IngestionJobPhase.PREPROCESS, queued_after_seconds=50
    )
    recent_start = await _job(
        client, recent, IngestionJobPhase.PREPROCESS, queued_after_seconds=110
    )
    # 始まりから 130 秒後の続きの段は、130 − 30 = 100 秒の位置まで繰り上がる。
    started_chunk = await _job(client, started, IngestionJobPhase.CHUNK, queued_after_seconds=130)
    ours = [waiting_start, recent_start, started_chunk]

    assert await _dispatch_order(client, ours) == [waiting_start, started_chunk, recent_start]
