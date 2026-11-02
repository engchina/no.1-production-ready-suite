"""品質評価の job（非同期の実行・進捗・取り消し。#390）のテスト。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from app.clients.oracle import OracleClient, OraclePoolProtocol
from app.config import RAG_EVALUATION_JOB_TIMEOUT_MAX_SECONDS, Settings, get_settings
from app.main import app
from app.rag import evaluation_jobs
from app.rag.evaluation import EvaluationProgressCallback, EvaluationRunner
from app.rag.evaluation_jobs import (
    EVALUATION_JOB_CANCELLED_MESSAGE,
    EVALUATION_JOB_SHUTDOWN_MESSAGE,
    EVALUATION_JOB_STALE_MESSAGE,
    EvaluationJobLimitError,
    EvaluationJobNotFoundError,
    EvaluationJobOutcome,
    EvaluationJobRecord,
    EvaluationJobService,
    EvaluationJobStateError,
    MemoryEvaluationJobStore,
    OracleEvaluationJobStore,
    evaluation_answer_runs,
    evaluation_job_time_limit_seconds,
    set_evaluation_job_service,
)
from app.rag.pipeline import SearchStageProgress, SearchStageProgressCallback
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.evaluation import EvaluationCase
from app.schemas.search import SearchDiagnostics, SearchRequest, SearchResponse
from tests.support import TEST_REQUEST_HEADERS


@pytest.fixture
def service() -> Iterator[EvaluationJobService]:
    """memory の保存先の service を差し込む（heartbeat は短くする）。"""
    installed = set_evaluation_job_service(
        EvaluationJobService(
            MemoryEvaluationJobStore(),
            heartbeat_seconds=0.01,
            worker_id="test-worker",
        )
    )
    yield installed
    set_evaluation_job_service(None)


@pytest.fixture
def owner() -> Iterator[AuditRequestContext]:
    context = AuditRequestContext(tenant_id_hash="t" * 64, user_id_hash="u" * 64)
    token = set_audit_request_context(context)
    yield context
    reset_audit_request_context(token)


def _metrics_json(case_count: int = 1) -> dict[str, Any]:
    return {
        "case_count": case_count,
        "required_evidence_recall": 1.0,
        "mrr": 1.0,
        "answer_correctness": 1.0,
        "response_decision_accuracy": 1.0,
    }


class ControlledExecutor:
    """ケースの進み具合をテストから操作する executor。"""

    def __init__(self, case_ids: list[str]) -> None:
        self.case_ids = case_ids
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.cancelled = False

    async def __call__(self, progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        try:
            for index, case_id in enumerate(self.case_ids):
                await progress(completed_cases=index, current_case_id=case_id)
                self.started.set()
                await self.release.wait()
                self.release.clear()
            await progress(completed_cases=len(self.case_ids), current_case_id=None)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return EvaluationJobOutcome(
            result=_metrics_json(len(self.case_ids)), evaluation_run_id="run-1"
        )


async def _eventually(predicate: Any, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("条件が時間内に満たされませんでした。")
        await asyncio.sleep(0.005)


async def test_job_reports_progress_and_result(
    service: EvaluationJobService, owner: AuditRequestContext
) -> None:
    """投入 → 進捗（終わったケースの数・今のケース）→ 完了（結果）の順に見える。"""
    executor = ControlledExecutor(["c1", "c2"])
    submitted = await service.submit(
        kind="run", total_cases=2, time_limit_seconds=3600, executor=executor
    )
    assert submitted.status == "RUNNING"
    assert submitted.total_cases == 2
    assert submitted.time_limit_seconds == 3600
    assert submitted.started_at is not None

    await executor.started.wait()
    running = await service.get(submitted.job_id)
    assert running.status == "RUNNING"
    assert running.completed_cases == 0
    assert running.current_case_id == "c1"
    assert running.current_case_started_at is not None
    assert running.run_result is None

    executor.started.clear()
    executor.release.set()
    await executor.started.wait()
    second = await service.get(submitted.job_id)
    assert (second.completed_cases, second.current_case_id) == (1, "c2")

    executor.release.set()
    await service.wait_for(submitted.job_id)
    done = await service.get(submitted.job_id)
    assert done.status == "SUCCEEDED"
    assert done.completed_cases == 2
    assert done.current_case_id is None
    assert done.finished_at is not None
    assert done.run_result is not None
    assert done.run_result.case_count == 2


async def test_job_cancel_stops_running_evaluation(
    service: EvaluationJobService, owner: AuditRequestContext
) -> None:
    """取り消すと評価の task を止め、CANCELLED のまま結果を書かない。"""
    executor = ControlledExecutor(["c1", "c2"])
    submitted = await service.submit(
        kind="run", total_cases=2, time_limit_seconds=3600, executor=executor
    )
    await executor.started.wait()

    cancelled = await service.cancel(submitted.job_id)
    assert cancelled.status == "CANCELLED"
    assert cancelled.error_message == EVALUATION_JOB_CANCELLED_MESSAGE
    await service.wait_for(submitted.job_id)
    assert executor.cancelled is True

    latest = await service.get(submitted.job_id)
    assert latest.status == "CANCELLED"
    assert latest.run_result is None
    # 取り消し済みの job の取り消しは、そのまま返す。
    assert (await service.cancel(submitted.job_id)).status == "CANCELLED"


async def test_job_cancel_from_another_process_stops_by_heartbeat(
    owner: AuditRequestContext,
) -> None:
    """別のプロセスが行を CANCELLED にしても、heartbeat で気づいて評価を止める。"""
    store = MemoryEvaluationJobStore()
    running_service = EvaluationJobService(store, heartbeat_seconds=0.01, worker_id="worker-a")
    other_service = EvaluationJobService(store, heartbeat_seconds=0.01, worker_id="worker-b")
    executor = ControlledExecutor(["c1"])
    submitted = await running_service.submit(
        kind="run", total_cases=1, time_limit_seconds=3600, executor=executor
    )
    await executor.started.wait()

    await other_service.cancel(submitted.job_id)
    await _eventually(lambda: executor.cancelled)
    await running_service.wait_for(submitted.job_id)
    assert (await running_service.get(submitted.job_id)).status == "CANCELLED"


async def test_finished_job_cannot_be_cancelled(
    service: EvaluationJobService, owner: AuditRequestContext
) -> None:
    executor = ControlledExecutor([])
    submitted = await service.submit(
        kind="run", total_cases=0, time_limit_seconds=3600, executor=executor
    )
    await service.wait_for(submitted.job_id)
    with pytest.raises(EvaluationJobStateError):
        await service.cancel(submitted.job_id)


async def test_finished_jobs_are_kept_without_retention(owner: AuditRequestContext) -> None:
    """終わった品質評価の job は既定で無期限に残す（自動では消さない。#3235）。"""
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    store = MemoryEvaluationJobStore(clock=lambda: now[0])
    service = EvaluationJobService(store, heartbeat_seconds=0.01)
    first = await service.submit(
        kind="run", total_cases=0, time_limit_seconds=3600, executor=ControlledExecutor([])
    )
    await service.wait_for(first.job_id)
    now[0] += timedelta(days=3650)

    second = await service.submit(
        kind="run", total_cases=0, time_limit_seconds=3600, executor=ControlledExecutor([])
    )
    await service.wait_for(second.job_id)

    assert evaluation_jobs.EVALUATION_JOB_RETENTION_DAYS == 0
    assert (await service.get(first.job_id)).status == "SUCCEEDED"
    assert await store.purge(retention_days=0) == 0
    # 保持日数を決めたときは、終わった job を期限で消す。
    assert await store.purge(retention_days=1) == 1


async def test_job_failure_hides_exception_text(
    service: EvaluationJobService, owner: AuditRequestContext
) -> None:
    """評価が例外で止まったら FAILED。例外の本文（query などを含みうる）は出さない。"""

    async def failing(progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        raise RuntimeError("機密の質問本文")

    submitted = await service.submit(
        kind="run", total_cases=1, time_limit_seconds=3600, executor=failing
    )
    await service.wait_for(submitted.job_id)
    failed = await service.get(submitted.job_id)
    assert failed.status == "FAILED"
    assert failed.error_message is not None
    # 例外のクラス名は 1 文目に入れず、末尾の「エラー種別: …」に分ける（画面は「詳細」に畳む）。
    assert failed.error_message == (
        "品質評価の実行中に予期しないエラーが発生したため、評価を中断しました。"
        "サーバーのログを確認して、もう一度実行してください。 エラー種別: RuntimeError"
    )
    assert "機密" not in failed.error_message


async def test_job_is_visible_only_to_submitter(
    service: EvaluationJobService, owner: AuditRequestContext
) -> None:
    executor = ControlledExecutor([])
    submitted = await service.submit(
        kind="run", total_cases=0, time_limit_seconds=3600, executor=executor
    )
    await service.wait_for(submitted.job_id)

    token = set_audit_request_context(
        AuditRequestContext(tenant_id_hash="t" * 64, user_id_hash="x" * 64)
    )
    try:
        with pytest.raises(EvaluationJobNotFoundError):
            await service.get(submitted.job_id)
        with pytest.raises(EvaluationJobNotFoundError):
            await service.cancel(submitted.job_id)
    finally:
        reset_audit_request_context(token)


async def test_running_job_limit(owner: AuditRequestContext) -> None:
    service = EvaluationJobService(
        MemoryEvaluationJobStore(), heartbeat_seconds=0.01, max_running=1
    )
    executor = ControlledExecutor(["c1"])
    first = await service.submit(
        kind="run", total_cases=1, time_limit_seconds=3600, executor=executor
    )
    with pytest.raises(EvaluationJobLimitError, match="上限の 1 件"):
        await service.submit(
            kind="run", total_cases=1, time_limit_seconds=3600, executor=ControlledExecutor([])
        )
    await service.cancel(first.job_id)
    await service.wait_for(first.job_id)


async def test_stale_job_of_stopped_process_is_failed(owner: AuditRequestContext) -> None:
    """heartbeat が途絶えた（実行していたプロセスが止まった）job は、取得のときに失敗にする。"""
    now = datetime.now(UTC)
    store = MemoryEvaluationJobStore(clock=lambda: now)
    await store.create(
        EvaluationJobRecord(
            job_id="stale-job",
            kind="run",
            status="RUNNING",
            total_cases=3,
            time_limit_seconds=3600,
            created_at=now,
            tenant_id_hash=owner.tenant_id_hash,
            user_id_hash=owner.user_id_hash,
            lease_owner="stopped-worker",
        )
    )
    service = EvaluationJobService(store, stale_seconds=120.0, worker_id="alive-worker")
    assert (await service.get("stale-job")).status == "RUNNING"

    store._clock = lambda: now + timedelta(seconds=121)  # noqa: SLF001 - 時計を進める
    stale = await service.get("stale-job")
    assert stale.status == "FAILED"
    assert stale.error_message == EVALUATION_JOB_STALE_MESSAGE


async def test_shutdown_fails_running_jobs_of_this_process(
    service: EvaluationJobService, owner: AuditRequestContext
) -> None:
    executor = ControlledExecutor(["c1"])
    submitted = await service.submit(
        kind="run", total_cases=1, time_limit_seconds=3600, executor=executor
    )
    await executor.started.wait()

    await service.shutdown()
    assert executor.cancelled is True
    stopped = await service.get(submitted.job_id)
    assert stopped.status == "FAILED"
    assert stopped.error_message == EVALUATION_JOB_SHUTDOWN_MESSAGE


# ---- 完了の保存の失敗（#4103） ----


def _column_too_large() -> Exception:
    """JSON 型の列の上限を超えたときの python-oracledb の例外と同じ形。"""
    return Exception(SimpleNamespace(code=40604, full_code="ORA-40604"))


def _result_with_answer_diagnostics() -> dict[str, Any]:
    return {
        **_metrics_json(1),
        "case_results": [
            {
                "case_id": "c1",
                "trace_id": "trace-1",
                "answer_correctness": 0.5,
                "elapsed_ms": 1.0,
                "diagnostics": {"answer": {"retrieval_stages": []}},
            }
        ],
    }


class _FinishFailingStore(MemoryEvaluationJobStore):
    """完了の書き込みを、`fails` が真を返す間は失敗させる保存先。"""

    def __init__(self, fails: Any) -> None:
        super().__init__()
        self.fails = fails
        self.finish_calls: list[tuple[str, Mapping[str, Any] | None]] = []
        self.heartbeats = 0

    async def finish(
        self,
        job_id: str,
        *,
        lease_owner: str,
        status: Any,
        result: Mapping[str, Any] | None,
        error_message: str | None,
        evaluation_run_id: str | None,
    ) -> bool:
        self.finish_calls.append((status, result))
        if self.fails(status, result):
            raise _column_too_large()
        return await super().finish(
            job_id,
            lease_owner=lease_owner,
            status=status,
            result=result,
            error_message=error_message,
            evaluation_run_id=evaluation_run_id,
        )

    async def heartbeat(self, job_id: str, *, lease_owner: str) -> bool:
        self.heartbeats += 1
        return await super().heartbeat(job_id, lease_owner=lease_owner)


def _finish_service(
    store: MemoryEvaluationJobStore, *, retries: tuple[float, ...] = (0.0, 0.0)
) -> EvaluationJobService:
    return EvaluationJobService(
        store, heartbeat_seconds=0.005, worker_id="test-worker", finish_retry_seconds=retries
    )


async def _submit_finished_evaluation(service: EvaluationJobService) -> str:
    async def executor(progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        await progress(completed_cases=1, current_case_id=None)
        return EvaluationJobOutcome(result=_result_with_answer_diagnostics())

    job = await service.submit(
        kind="run", total_cases=1, time_limit_seconds=3600, executor=executor
    )
    return job.job_id


async def test_finished_result_is_saved_without_answer_diagnostics(
    owner: AuditRequestContext,
) -> None:
    """結果は最初の書き込みから回答の診断を外し、得点を残して SUCCEEDED にする（#4107）。"""
    store = _FinishFailingStore(lambda _status, _result: False)
    service = _finish_service(store)
    job_id = await _submit_finished_evaluation(service)
    await service.wait_for(job_id)

    job = await service.get(job_id)
    assert job.status == "SUCCEEDED"
    assert job.error_message is None
    assert job.run_result is not None
    assert job.run_result.answer_diagnostics_omitted is True
    assert job.run_result.case_results[0].answer_correctness == 0.5
    assert job.run_result.case_results[0].diagnostics.answer is None
    assert len(store.finish_calls) == 1


async def test_retried_write_also_omits_answer_diagnostics(owner: AuditRequestContext) -> None:
    """書き直しでも同じ内容（診断なし）を書く。"""
    attempts: list[object] = []

    def fails_once(_status: str, _result: object) -> bool:
        attempts.append(_status)
        return len(attempts) == 1

    store = _FinishFailingStore(fails_once)
    service = _finish_service(store)
    job_id = await _submit_finished_evaluation(service)
    await service.wait_for(job_id)

    assert (await service.get(job_id)).status == "SUCCEEDED"
    assert len(store.finish_calls) == 2
    for _status, result in store.finish_calls:
        assert result is not None
        assert result["case_results"][0]["diagnostics"]["answer"] is None


async def test_unsaved_result_is_reported_with_cause_instead_of_stale(
    owner: AuditRequestContext,
) -> None:
    """どうしても書けなければ、理由を書いて失敗にする。heartbeat の途絶の文言にしない。"""
    store = _FinishFailingStore(lambda status, _result: status == "SUCCEEDED")
    service = _finish_service(store)
    job_id = await _submit_finished_evaluation(service)
    await service.wait_for(job_id)

    job = await service.get(job_id)
    assert job.status == "FAILED"
    assert job.error_message != EVALUATION_JOB_STALE_MESSAGE
    assert job.error_message is not None
    assert "結果をデータベースに保存できませんでした" in job.error_message
    assert job.error_message.endswith("エラー種別: ORA-40604")
    assert job.completed_cases == 1
    assert job.run_result is None
    # 1 回目と書き直し 2 回のあと、理由の書き込み。
    assert [status for status, _ in store.finish_calls] == ["SUCCEEDED"] * 3 + ["FAILED"]


async def test_heartbeat_continues_until_finish_is_written(owner: AuditRequestContext) -> None:
    """書き直しの間も heartbeat を続け、別のプロセスに止まったと判定されない。"""
    recovered = asyncio.Event()
    store = _FinishFailingStore(lambda _status, _result: not recovered.is_set())
    service = _finish_service(store, retries=(0.01, 0.01, 0.01, 0.01, 0.01, 5.0))
    job_id = await _submit_finished_evaluation(service)
    await _eventually(lambda: len(store.finish_calls) >= 3)
    beats = store.heartbeats
    await _eventually(lambda: store.heartbeats > beats)

    other_process = EvaluationJobService(store, stale_seconds=120.0, worker_id="other-worker")
    assert (await other_process.get(job_id)).status == "RUNNING"

    recovered.set()
    await service.wait_for(job_id)
    assert (await service.get(job_id)).status == "SUCCEEDED"


async def test_job_is_stale_only_when_database_stays_unavailable(
    owner: AuditRequestContext,
) -> None:
    """理由も書けない（DB が使えない）ときは、これまでどおり heartbeat の途絶で失敗になる。"""
    now = datetime.now(UTC)
    store = _FinishFailingStore(lambda _status, _result: True)
    service = _finish_service(store)
    job_id = await _submit_finished_evaluation(service)
    await service.wait_for(job_id)
    assert (await service.get(job_id)).status == "RUNNING"

    store._clock = lambda: now + timedelta(seconds=121)  # noqa: SLF001 - 時計を進める
    other_process = EvaluationJobService(store, stale_seconds=120.0, worker_id="other-worker")
    stale = await other_process.get(job_id)
    assert stale.status == "FAILED"
    assert stale.error_message == EVALUATION_JOB_STALE_MESSAGE


async def test_shutdown_during_finish_retry_reports_shutdown(owner: AuditRequestContext) -> None:
    """保存の書き直しの間に backend が止まったら、停止の文言で失敗にする。"""
    store = _FinishFailingStore(lambda status, _result: status == "SUCCEEDED")
    service = _finish_service(store, retries=(60.0,))
    job_id = await _submit_finished_evaluation(service)
    await _eventually(lambda: len(store.finish_calls) == 1)

    await service.shutdown()
    job = await service.get(job_id)
    assert job.status == "FAILED"
    assert job.error_message == EVALUATION_JOB_SHUTDOWN_MESSAGE


async def test_cancel_during_finish_retry_stops_rewriting(owner: AuditRequestContext) -> None:
    """保存の書き直しの間の取り消しは、取り消しのまま残す。"""
    store = _FinishFailingStore(lambda status, _result: status == "SUCCEEDED")
    service = _finish_service(store, retries=(60.0,))
    job_id = await _submit_finished_evaluation(service)
    await _eventually(lambda: len(store.finish_calls) == 1)

    job = await service.cancel(job_id)
    assert job.status == "CANCELLED"
    await service.wait_for(job_id)
    assert (await service.get(job_id)).error_message == EVALUATION_JOB_CANCELLED_MESSAGE
    assert len(store.finish_calls) == 1


async def test_failed_job_write_is_retried_without_result(owner: AuditRequestContext) -> None:
    """評価そのものの失敗の書き込みも書き直す（結果は無いまま）。"""
    calls = 0

    def fails(_status: str, _result: object) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    store = _FinishFailingStore(fails)
    service = _finish_service(store)

    async def executor(_progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        raise RuntimeError("合成の失敗")

    submitted = await service.submit(
        kind="run", total_cases=1, time_limit_seconds=3600, executor=executor
    )
    await service.wait_for(submitted.job_id)
    job = await service.get(submitted.job_id)
    assert job.status == "FAILED"
    assert job.error_message is not None
    assert "エラー種別: RuntimeError" in job.error_message
    assert store.finish_calls == [("FAILED", None), ("FAILED", None)]


class SlowPipeline:
    """1 件目は工程を通知してから止まり（時間切れ）、2 件目以降はすぐ答える pipeline。"""

    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        self.calls += 1
        if self.calls == 1:
            if progress_callback is not None:
                await progress_callback(
                    SearchStageProgress(
                        trace_id=trace_id or "trace",
                        stage="answer",
                        outcome="started",
                        elapsed_ms=0.0,
                        attributes={},
                    )
                )
            await asyncio.sleep(10)
        return SearchResponse(
            answer="承認は部門長です。",
            citations=[],
            trace_id=trace_id or "trace",
            guardrail_warnings=[],
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics(),
        )


async def test_job_records_timed_out_case_with_stage_and_continues(
    service: EvaluationJobService,
    owner: AuditRequestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """時間切れのケースは工程付きの失敗として結果に残り、次のケースへ進む（#383 と同じ）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_answer_timeout_seconds", 0.05)
    runner = EvaluationRunner(pipeline=SlowPipeline(), settings=settings)
    cases = [
        EvaluationCase(id="slow", query="承認は?", relevant_document_ids=[]),
        EvaluationCase(id="fast", query="承認は?", relevant_document_ids=[]),
    ]
    seen: list[tuple[int, str | None]] = []

    async def executor(progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        async def spy(*, completed_cases: int, current_case_id: str | None) -> None:
            seen.append((completed_cases, current_case_id))
            await progress(completed_cases=completed_cases, current_case_id=current_case_id)

        metrics = await runner.run(cases=cases, top_k=3, time_budget_seconds=3600, progress=spy)
        return EvaluationJobOutcome(result=metrics.model_dump(mode="json"))

    submitted = await service.submit(
        kind="run", total_cases=2, time_limit_seconds=3600, executor=executor
    )
    await service.wait_for(submitted.job_id)
    done = await service.get(submitted.job_id)

    assert seen == [(0, "slow"), (1, "fast"), (2, None)]
    assert done.status == "SUCCEEDED"
    assert done.run_result is not None
    slow, fast = done.run_result.case_results
    assert slow.status == "error"
    assert slow.error_type == "TimeoutError"
    assert slow.error_stage == "answer"
    assert slow.error_message is not None and "質問の条件を整理して回答を作る" in slow.error_message
    assert fast.status == "success"


@pytest.fixture
async def api_client(service: EvaluationJobService) -> AsyncIterator[httpx.AsyncClient]:
    """1 つの event loop で投入から完了まで扱う client（job の task が loop に残るため）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=TEST_REQUEST_HEADERS
    ) as client:
        yield client


def _case(case_id: str, *, replies: int = 0) -> EvaluationCase:
    return EvaluationCase.model_validate(
        {
            "id": case_id,
            "query": "承認条件は?",
            "relevant_document_ids": [],
            "turns": [{"reply": f"返答 {index}"} for index in range(replies)],
        }
    )


@pytest.mark.parametrize(
    ("answer_runs", "floor", "answer_timeout", "expected"),
    [
        # 推論型のモデルの 20 問の評価: 20 × 600 秒。以前の固定の 3600 秒では打ち切られた。
        (20, 3600, 600.0, 12000),
        # 少ない問の数では設定の値（最小値）より短くしない。
        (2, 3600, 600.0, 3600),
        # 1 日（86400 秒）を超えない。
        (200, 3600, 600.0, RAG_EVALUATION_JOB_TIMEOUT_MAX_SECONDS),
        # 回答の上限の端数は切り上げる。
        (3, 60, 100.5, 302),
        (0, 60, 600.0, 60),
    ],
    ids=["twenty-cases", "floor", "max", "ceil", "no-cases"],
)
def test_evaluation_job_time_limit_is_proportional_to_answer_runs(
    answer_runs: int, floor: int, answer_timeout: float, expected: int
) -> None:
    """job の上限は回答の回数 × 回答の上限で、設定の値を最小、86400 秒を最大にする（#1430）。"""
    settings = Settings(
        rag_evaluation_job_timeout_seconds=floor, rag_answer_timeout_seconds=answer_timeout
    )
    assert evaluation_job_time_limit_seconds(settings, answer_runs=answer_runs) == expected


def test_evaluation_answer_runs_counts_replies() -> None:
    """回答の回数は、ケースごとの最初の質問と返答（#1284）の数。"""
    cases = [_case("c1"), _case("c2", replies=2)]
    assert evaluation_answer_runs(cases) == 1 + 3


def test_evaluation_job_timeout_setting_defaults() -> None:
    """設定は最小値（既定 3600 秒）で、上限は job の上限の最大と同じ 86400 秒。"""
    field = Settings.model_fields["rag_evaluation_job_timeout_seconds"]
    assert field.default == 3600
    assert Settings(rag_evaluation_job_timeout_seconds=RAG_EVALUATION_JOB_TIMEOUT_MAX_SECONDS)
    with pytest.raises(ValidationError):
        Settings(rag_evaluation_job_timeout_seconds=RAG_EVALUATION_JOB_TIMEOUT_MAX_SECONDS + 1)


def _run_body() -> dict[str, Any]:
    return {
        "cases": [
            {"id": "c1", "query": "承認条件は?", "relevant_document_ids": []},
            {"id": "c2", "query": "申請期限は?", "relevant_document_ids": []},
        ],
        "top_k": 5,
    }


async def test_run_job_api_submits_and_returns_result(
    api_client: httpx.AsyncClient,
    service: EvaluationJobService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /jobs/run は 202 で job を返し、GET /jobs/{id} で進捗と結果を返す。"""
    release = asyncio.Event()
    observed: dict[str, Any] = {}

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> Any:
        from app.schemas.evaluation import EvaluationMetrics

        observed["time_budget_seconds"] = kwargs["time_budget_seconds"]
        progress = kwargs["progress"]
        await progress(completed_cases=0, current_case_id="c1")
        await release.wait()
        await progress(completed_cases=2, current_case_id=None)
        return EvaluationMetrics.model_validate(_metrics_json(2))

    async def fake_save(self: OracleClient, artifact: Mapping[str, object]) -> str:
        observed["artifact_kind"] = artifact["request_summary"]["kind"]  # type: ignore[index]
        return "artifact-1"

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(OracleClient, "save_evaluation_artifact", fake_save)
    monkeypatch.setattr(get_settings(), "rag_evaluation_job_timeout_seconds", 900)
    # 2 ケース × 300 秒 = 600 秒は設定の最小値（900 秒）より短い。
    monkeypatch.setattr(get_settings(), "rag_answer_timeout_seconds", 300.0)

    response = await api_client.post("/api/evaluation/jobs/run", json=_run_body())
    assert response.status_code == 202
    job = response.json()["data"]
    assert job["kind"] == "run"
    assert job["status"] == "RUNNING"
    assert job["total_cases"] == 2
    assert job["time_limit_seconds"] == 900

    await _eventually(lambda: "time_budget_seconds" in observed)
    running = (await api_client.get(f"/api/evaluation/jobs/{job['job_id']}")).json()["data"]
    assert running["current_case_id"] == "c1"
    assert running["run_result"] is None

    release.set()
    await service.wait_for(job["job_id"])
    done = (await api_client.get(f"/api/evaluation/jobs/{job['job_id']}")).json()["data"]
    assert done["status"] == "SUCCEEDED"
    assert done["completed_cases"] == 2
    assert done["run_result"]["case_count"] == 2
    assert done["run_result"]["evaluation_suite"] == "standard"
    # job の上限は設定の最小値。同期の API の 600 秒ではない。
    assert observed == {"time_budget_seconds": 900, "artifact_kind": "run"}


async def test_cancel_job_api(
    api_client: httpx.AsyncClient,
    service: EvaluationJobService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def blocking_run(self: EvaluationRunner, **kwargs: Any) -> Any:
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(EvaluationRunner, "run", blocking_run)
    job = (await api_client.post("/api/evaluation/jobs/run", json=_run_body())).json()["data"]
    await started.wait()

    response = await api_client.post(f"/api/evaluation/jobs/{job['job_id']}/cancel")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CANCELLED"
    await service.wait_for(job["job_id"])

    missing = await api_client.get("/api/evaluation/jobs/unknown-job")
    assert missing.status_code == 404
    again = await api_client.post(f"/api/evaluation/jobs/{job['job_id']}/cancel")
    assert again.status_code == 200


async def test_job_api_rejects_when_running_limit_reached(
    api_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limited = set_evaluation_job_service(
        EvaluationJobService(MemoryEvaluationJobStore(), max_running=0)
    )
    try:
        response = await api_client.post("/api/evaluation/jobs/run", json=_run_body())
    finally:
        set_evaluation_job_service(None)
    assert response.status_code == 409
    assert limited is not None


def test_job_permissions_are_registered() -> None:
    from app.security.permissions import MENU_EVALUATION, permission_for_route

    for method, path in (
        ("POST", "/evaluation/jobs/run"),
        ("GET", "/evaluation/jobs/{job_id}"),
        ("POST", "/evaluation/jobs/{job_id}/cancel"),
    ):
        assert permission_for_route(method, path) == frozenset({MENU_EVALUATION})


# ---- Oracle の保存先（SQL の形を fake の接続で確かめる） ----


@dataclass
class _SqlCall:
    statement: str
    parameters: dict[str, object]
    input_sizes: dict[str, object]


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection
        self._input_sizes: dict[str, object] = {}
        self.rowcount = connection.rowcount
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple[object, ...]] = []

    def setinputsizes(self, **kwargs: object) -> None:
        self._input_sizes.update(kwargs)

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        self._connection.calls.append(
            _SqlCall(statement, dict(parameters or {}), dict(self._input_sizes))
        )
        if statement.lstrip().startswith("SELECT") and self._connection.rows:
            row = self._connection.rows.pop(0)
            self.description = [(key,) for key in row]
            self._rows = [tuple(row.values())]

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, *, rowcount: int = 1, rows: list[dict[str, object]] | None = None) -> None:
        self.rowcount = rowcount
        self.rows = rows or []
        self.calls: list[_SqlCall] = []
        self.commits = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> _FakeConnection:
        return self.connection

    def close(self, force: bool = False) -> None:
        return None


async def _run_inline(operation: Any) -> Any:
    return operation()


def _oracle_store(connection: _FakeConnection) -> OracleEvaluationJobStore:
    client = OracleClient(
        settings=get_settings(),
        pool=cast(OraclePoolProtocol, _FakePool(connection)),
        db_call_runner=_run_inline,
    )
    return OracleEvaluationJobStore(client)


def _normalized(statement: str) -> str:
    return " ".join(statement.split())


async def test_oracle_purges_issue_no_sql_without_retention() -> None:
    """保持日数 0（無期限）の削除は SQL を流さない（実行の記録を消さない。#3235）。"""
    from app.clients.oracle_run_history import RunHistoryStore

    connection = _FakeConnection(rowcount=5)
    store = _oracle_store(connection)
    client = OracleClient(
        settings=get_settings(),
        pool=cast(OraclePoolProtocol, _FakePool(connection)),
        db_call_runner=_run_inline,
    )

    assert await store.purge(retention_days=0) == 0
    assert await client.purge_answer_records(0) == 0
    assert await RunHistoryStore(client).purge_runs(0) == 0
    assert connection.calls == []

    assert await store.purge(retention_days=7) == 5
    assert "DELETE FROM rag_evaluation_jobs" in _normalized(connection.calls[0].statement)
    assert connection.calls[0].parameters == {"retention_days": 7}


async def test_oracle_store_writes_only_under_own_lease() -> None:
    """進捗・heartbeat・完了は自分の lease の RUNNING の行だけに、DB の時刻で書く。"""
    connection = _FakeConnection(rowcount=1)
    store = _oracle_store(connection)

    assert await store.update_progress(
        "job-1",
        lease_owner="worker-a",
        completed_cases=3,
        current_case_id="c4",
    )
    assert await store.heartbeat("job-1", lease_owner="worker-a")
    assert await store.finish(
        "job-1",
        lease_owner="worker-a",
        status="SUCCEEDED",
        result={"case_count": 4},
        error_message=None,
        evaluation_run_id="run-1",
    )
    progress, heartbeat, finish = (_normalized(call.statement) for call in connection.calls)
    for statement in (progress, heartbeat, finish):
        assert "WHERE job_id = :job_id AND status = 'RUNNING' AND lease_owner = :lease_owner" in (
            statement
        )
    assert "heartbeat_at = SYSTIMESTAMP" in progress
    assert "CASE WHEN :case_started = 1 THEN SYSTIMESTAMP ELSE NULL END" in progress
    assert connection.calls[0].parameters["case_started"] == 1
    assert "heartbeat_at = SYSTIMESTAMP" in heartbeat
    assert "finished_at = SYSTIMESTAMP" in finish
    assert json.loads(cast(str, connection.calls[2].parameters["result_json"])) == {"case_count": 4}
    assert "result_json" in connection.calls[2].input_sizes

    connection.rowcount = 0
    assert not await store.heartbeat("job-1", lease_owner="worker-a")


async def test_oracle_store_scopes_reads_and_cancel_to_owner() -> None:
    now = datetime.now(UTC)
    connection = _FakeConnection(
        rowcount=1,
        rows=[
            {
                "job_id": "job-1",
                "kind": "run",
                "status": "CANCELLED",
                "total_cases": 6,
                "completed_cases": 2,
                "current_case_id": None,
                "current_case_started_at": None,
                "time_limit_seconds": 3600,
                "error_message": EVALUATION_JOB_CANCELLED_MESSAGE,
                "result_json": None,
                "evaluation_run_id": None,
                "created_at": now,
                "started_at": now,
                "finished_at": now,
                "heartbeat_at": now,
            }
        ],
    )
    store = _oracle_store(connection)

    assert await store.cancel(
        "job-1",
        tenant_id_hash=None,
        user_id_hash="u" * 64,
        error_message=EVALUATION_JOB_CANCELLED_MESSAGE,
    )
    record = await store.get("job-1", tenant_id_hash=None, user_id_hash="u" * 64)
    assert record is not None
    assert (record.kind, record.status, record.total_cases) == ("run", "CANCELLED", 6)

    cancel, select = (_normalized(call.statement) for call in connection.calls)
    owner_sql = (
        "(tenant_id_hash = :tenant_id_hash OR (tenant_id_hash IS NULL AND :tenant_id_hash IS NULL))"
        " AND (user_id_hash = :user_id_hash OR (user_id_hash IS NULL AND :user_id_hash IS NULL))"
    )
    assert "SET status = 'CANCELLED'" in cancel
    assert "AND status = 'RUNNING'" in cancel
    assert owner_sql in cancel
    assert owner_sql in select
    assert "query" not in select.lower()


async def test_oracle_store_fails_stale_jobs_by_database_clock() -> None:
    connection = _FakeConnection(rowcount=2)
    store = _oracle_store(connection)

    assert (
        await store.fail_stale(stale_seconds=120.0, error_message=EVALUATION_JOB_STALE_MESSAGE) == 2
    )
    await store.fail_stale(
        stale_seconds=0.0,
        error_message=EVALUATION_JOB_SHUTDOWN_MESSAGE,
        lease_owner="worker-a",
    )
    stale, leased = (_normalized(call.statement) for call in connection.calls)
    assert "heartbeat_at < SYSTIMESTAMP - NUMTODSINTERVAL(:stale_seconds, 'SECOND')" in stale
    assert "SET status = 'FAILED'" in stale
    assert "lease_owner = :lease_owner" in leased
    assert "NUMTODSINTERVAL" not in leased


def test_service_uses_memory_store_without_oracle_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "oracle_dsn", "")
    service = EvaluationJobService()
    assert isinstance(service._resolve_store(), MemoryEvaluationJobStore)  # noqa: SLF001


def test_default_service_is_replaceable() -> None:
    replaced = set_evaluation_job_service(None)
    assert evaluation_jobs.get_evaluation_job_service() is replaced
