"""部分失敗の記録を実job試行へ束縛する。"""

import importlib
import json
from typing import Any, cast

import pytest

from app.clients import oracle as oracle_module
from app.clients.oracle import IngestionJobFence, OracleClient
from app.schemas.document import IngestionJobProgress


def api() -> Any:
    return importlib.import_module("app.rag.ingestion_attempt_receipts")


def values() -> dict[str, Any]:
    return dict(
        version=1,
        job_id="j1",
        attempt_count=2,
        document_id="d1",
        recipe_id="r1",
        recipe_revision=3,
        accepted=dict(version=1, document_id="d1", recipe_id="r1", segments=[]),
        remaining=[],
    )


@pytest.mark.parametrize(
    "progress", [None, IngestionJobProgress(stage="parse", updated_at="2026-01-01T00:00:00Z")]
)
async def test_progress_preserves_large_receipt_under_same_row_lock(
    monkeypatch: Any, progress: Any
) -> None:
    client: Any = object.__new__(OracleClient)
    connection = object()
    stored = {"_segment_attempt_receipt": {"sentinel": "x" * 5000}}
    calls = []

    def fetch(conn: Any, sql: str, binds: Any) -> Any:
        assert conn is connection and "FOR UPDATE" in sql
        # 進捗も実行の識別子(lease の持ち主と試行の回数)の行だけに書く(#4187)。
        assert "attempt_count = :fence_attempt_count" in sql
        assert "lease_owner = :fence_lease_owner" in sql
        assert binds["fence_attempt_count"] == 2 and binds["fence_lease_owner"] == "w1"
        calls.append("lock")
        return [{"progress_json": json.dumps(stored)}]

    def execute(conn: Any, sql: str, binds: Any) -> int:
        assert conn is connection and calls == ["lock"]
        assert "attempt_count = :fence_attempt_count" in sql
        assert (
            json.loads(binds["progress_json"])["_segment_attempt_receipt"]
            == stored["_segment_attempt_receipt"]
        )
        calls.append("write")
        return 1

    async def transaction(operation: Any) -> Any:
        return operation(connection)

    monkeypatch.setattr(oracle_module, "_fetch_all", fetch)
    monkeypatch.setattr(oracle_module, "_execute_count", execute)
    client._run_transaction = transaction
    fence = IngestionJobFence(job_id="j1", attempt_count=2, lease_owner="w1")
    assert await client.update_ingestion_job_progress("j1", progress, fence=fence)
    assert calls == ["lock", "write"]


@pytest.mark.parametrize("mismatch", ["attempt", "lease", "terminal"])
async def test_receipt_write_refuses_lost_fence(monkeypatch: Any, mismatch: str) -> None:
    client: Any = object.__new__(OracleClient)
    receipt = api().AttemptReceipt.model_validate(values())

    def fetch(conn: Any, sql: str, binds: Any) -> Any:
        assert "attempt_count = :attempt_count" in sql
        assert "lease_owner = :lease_owner" in sql
        assert "status = 'RUNNING'" in sql and "FOR UPDATE" in sql
        assert binds["attempt_count"] == 2 and binds["lease_owner"] == "w1"
        return []

    async def transaction(operation: Any) -> Any:
        return operation(object())

    monkeypatch.setattr(oracle_module, "_fetch_all", fetch)
    monkeypatch.setattr(
        oracle_module, "_execute_count", lambda *args: pytest.fail("lost fence wrote")
    )
    client._run_transaction = transaction
    assert not await client.save_ingestion_attempt_receipt(
        "j1", receipt, attempt_count=2, lease_owner="w1"
    )


@pytest.mark.parametrize(
    "key", ["job_id", "attempt_count", "recipe_revision", "document_id", "recipe_id"]
)
def test_reader_rejects_other_attempt_or_scope(key: str) -> None:
    raw = values()
    expected = dict(raw)
    raw[key] = 99 if isinstance(raw[key], int) else "other"
    assert (
        api().read_attempt_receipt(
            {"_segment_attempt_receipt": raw},
            **{
                k: expected[k]
                for k in ["job_id", "attempt_count", "document_id", "recipe_id", "recipe_revision"]
            },
        )
        is None
    )


@pytest.mark.parametrize("storage_fails", [False, True])
async def test_partial_failure_records_actual_accepted_and_failed_ranges(
    storage_fails: bool, caplog: Any
) -> None:
    from types import SimpleNamespace

    from app.config import Settings
    from app.rag.ingestion import IngestionPipeline
    from app.schemas.document import IngestionSegment
    from app.schemas.extraction import StructuredExtraction

    pipeline: Any = object.__new__(IngestionPipeline)
    pipeline._settings = Settings.model_construct(rag_pdf_segment_parse_concurrency=1)
    pipeline._recipe_id = "r1"
    rows = [
        IngestionSegment(
            segment_id=f"s{i}",
            document_id="d1",
            recipe_id="r1",
            status="QUEUED",
            page_start=i,
            page_end=i,
        )
        for i in (1, 2)
    ]
    pages = [SimpleNamespace(page_start=i, page_end=i, content=str(i).encode()) for i in (1, 2)]
    extraction = StructuredExtraction(raw_text="合成の本文", elements=[])
    receipts = []
    parsed = []
    reuse = False

    async def write(payload: Any) -> None:
        if storage_fails:
            raise RuntimeError("synthetic storage failure")
        receipts.append(payload)

    async def cached(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            cached=[SimpleNamespace(segment=pages[0], extraction=extraction)] if reuse else [],
            missing_ranges=[],
        )

    async def partition(**kwargs: Any) -> Any:
        page = kwargs["attributes"]["segment_page_start"]
        parsed.append(page)
        if page == 2 and not reuse:
            raise RuntimeError("synthetic failure")
        return SimpleNamespace(
            extraction=extraction, parser_version="synthetic", template="layout", warnings=()
        )

    async def ignore(*args: Any, **kwargs: Any) -> Any:
        return None

    async def cache(**kwargs: Any) -> str:
        return "local://" + str(kwargs["segment"].segment_id)

    pipeline._load_cached_segment_extractions = cached
    pipeline._segment_parse_key = lambda content: "key-" + content.decode()
    pipeline._run_partition_with_progress = partition
    pipeline._segment_extraction_from_result = lambda result: result.extraction
    pipeline._safe_update_ingestion_segment = ignore
    pipeline._mark_segment_failed = ignore
    pipeline._mark_segment_succeeded = ignore
    pipeline._cache_segment_extraction_artifact = cache

    async def latest(items: Any) -> Any:
        return items

    pipeline._segments_with_latest_status = latest
    kwargs: dict[str, Any] = dict(
        trace_id="t",
        document_id="d1",
        parser_backend="docling",
        pdf_segments=pages,
        checkpoint_segments=rows,
        source_profile=None,
        content_type="application/pdf",
        attributes={},
        progress=None,
        cancel_checker=None,
    )
    with api().attempt_receipt_scope(write), pytest.raises(RuntimeError, match="synthetic failure"):
        await pipeline._parse_pdf_segments(**kwargs)
    if storage_fails:
        assert receipts == []
        assert "ingestion_attempt_receipt_unrecorded" in caplog.text
        return
    assert len(receipts) == 1
    assert receipts[0]["remaining"] == [{"segment_id": "s2", "status": "FAILED"}]
    accepted = receipts[0]["accepted"]["segments"]
    assert [
        (r["segment_id"], r["artifact_path"], r["parse_key"], r["attempt_count"]) for r in accepted
    ] == [("s1", "local://s1", "key-1", 1)]
    reuse = True
    rows[0] = rows[0].model_copy(
        update={"status": "SUCCEEDED", "attempt_count": 1, "artifact_path": "local://s1"}
    )
    rows[1] = rows[1].model_copy(update={"status": "FAILED", "attempt_count": 1})
    with api().attempt_receipt_scope(write):
        result, _ = await pipeline._parse_pdf_segments(**kwargs)
    assert parsed == [1, 2, 2]
    from app.rag.ingestion_segment_receipts import read_segment_receipt

    after = read_segment_receipt(result.extraction.to_document_payload(), "d1", "r1")
    assert after is not None
    assert after.segments[0].model_dump() == accepted[0]
    assert after.segments[1].attempt_count == 2
    from app.rag.file_processing_staging import _segment_artifact_reuse_passed

    before_segments = tuple(
        IngestionSegment.model_validate({**r, "status": "SUCCEEDED"}) for r in accepted
    )
    before_segments += tuple(
        IngestionSegment.model_validate(r) for r in receipts[0]["remaining_segments"]
    )
    evidence = SimpleNamespace(
        initial_retry_segments=before_segments,
        retry_segments=tuple(r.segment for r in after.segments),
        segments=(),
    )
    assert _segment_artifact_reuse_passed(cast(Any, evidence)) == (True, None)


async def test_external_task_cancel_joins_workers_and_records_accepted(monkeypatch: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    from app.config import Settings
    from app.rag.ingestion import IngestionPipeline
    from app.schemas.document import IngestionSegment
    from app.schemas.extraction import StructuredExtraction

    pipeline: Any = object.__new__(IngestionPipeline)
    pipeline._settings = Settings.model_construct(rag_pdf_segment_parse_concurrency=1)
    pipeline._recipe_id = "r1"
    rows = [
        IngestionSegment(
            segment_id=f"s{i}",
            document_id="d1",
            recipe_id="r1",
            status="QUEUED",
            page_start=i,
            page_end=i,
        )
        for i in (1, 2)
    ]
    pages = [SimpleNamespace(page_start=i, page_end=i, content=str(i).encode()) for i in (1, 2)]
    extraction = StructuredExtraction(raw_text="合成本文", elements=[])
    entered = asyncio.Event()
    stopped = asyncio.Event()
    receipts = []

    async def writer(payload: Any) -> None:
        assert stopped.is_set()
        receipts.append(payload)

    async def cached(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            cached=[SimpleNamespace(segment=pages[0], extraction=extraction)], missing_ranges=[]
        )

    async def partition(**kwargs: Any) -> Any:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def ignore(*args: Any, **kwargs: Any) -> None:
        return None

    pipeline._load_cached_segment_extractions = cached
    pipeline._segment_parse_key = lambda content: "key-" + content.decode()
    pipeline._run_partition_with_progress = partition
    pipeline._safe_update_ingestion_segment = ignore
    rows[0] = rows[0].model_copy(
        update={"status": "SUCCEEDED", "artifact_path": "local://s1", "attempt_count": 1}
    )
    with api().attempt_receipt_scope(writer):
        task = asyncio.create_task(
            pipeline._parse_pdf_segments(
                trace_id="t",
                document_id="d1",
                parser_backend="docling",
                pdf_segments=pages,
                checkpoint_segments=rows,
                source_profile=None,
                content_type="application/pdf",
                attributes={},
                progress=None,
                cancel_checker=None,
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(receipts) == 1
    assert receipts[0]["accepted"]["segments"][0]["segment_id"] == "s1"
    assert receipts[0]["remaining"] == [{"segment_id": "s2", "status": "CANCELLED"}]


@pytest.mark.parametrize("fails", [False, True])
async def test_receipt_rmw_commits_or_rolls_back_one_connection(
    monkeypatch: Any, fails: bool
) -> None:
    from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline

    pool = FakeOraclePool(execute_results=[[{"progress_json": None}]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    def execute(connection: Any, statement: str, binds: Any) -> int:
        assert connection is pool.connection
        assert "FOR UPDATE" in pool.connection.calls[0].statement
        if fails:
            raise ValueError("synthetic storage failure")
        return 1

    monkeypatch.setattr(oracle_module, "_execute_count", execute)
    receipt = api().AttemptReceipt.model_validate(values())
    if fails:
        with pytest.raises(ValueError, match="synthetic storage failure"):
            await client.save_ingestion_attempt_receipt(
                "j1", receipt, attempt_count=2, lease_owner="w1"
            )
    else:
        assert await client.save_ingestion_attempt_receipt(
            "j1", receipt, attempt_count=2, lease_owner="w1"
        )
    assert pool.connection.commits == int(not fails)
    assert pool.connection.rollbacks == int(fails)
    assert pool.acquire_calls == 1


@pytest.mark.parametrize("invalid", ["overlap", "order", "duplicate", "foreign"])
def test_reader_rejects_malformed_accepted_ranges(invalid: str) -> None:
    raw = values()
    rows = [
        dict(
            segment_id=f"s{i}",
            document_id="d1",
            recipe_id="r1",
            parser_backend="docling",
            parser_profile="layout",
            page_start=i,
            page_end=i,
            parse_key=f"key-{i}",
            artifact_path=f"local://s{i}",
        )
        for i in (1, 2)
    ]
    if invalid == "overlap":
        rows[0]["page_end"] = 2
    elif invalid == "order":
        rows.reverse()
    elif invalid == "duplicate":
        rows[1]["segment_id"] = "s1"
    else:
        rows[1]["document_id"] = "other"
    raw["accepted"]["segments"] = rows
    assert (
        api().read_attempt_receipt(
            {"_segment_attempt_receipt": raw},
            **{
                key: raw[key]
                for key in [
                    "job_id",
                    "attempt_count",
                    "document_id",
                    "recipe_id",
                    "recipe_revision",
                ]
            },
        )
        is None
    )


async def test_nested_producer_scope_restores_previous_and_clears_after_exit() -> None:
    calls = []

    async def first(payload: Any) -> None:
        calls.append("first")

    async def second(payload: Any) -> None:
        calls.append("second")

    with api().attempt_receipt_scope(first):
        await api().record_attempt_receipt({})
        with api().attempt_receipt_scope(second):
            await api().record_attempt_receipt({})
        await api().record_attempt_receipt({})
    await api().record_attempt_receipt({})
    assert calls == ["first", "second", "first"]
