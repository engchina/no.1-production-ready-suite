"""品質評価のケースごとの結果の保存と、途中までの結果（#4208）のテスト。内容はすべて合成の値。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.clients.oracle import OracleClient
from app.main import app
from app.rag.evaluation import EvaluationCaseResultCallback, EvaluationRunner, SearchPipeline
from app.rag.evaluation_jobs import (
    EVALUATION_JOB_STALE_MESSAGE,
    EvaluationJobOutcome,
    EvaluationJobService,
    MemoryEvaluationJobStore,
    OracleEvaluationJobStore,
    set_evaluation_job_service,
)
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.evaluation import EvaluationCaseResult, EvaluationMetrics
from tests.support import TEST_REQUEST_HEADERS


@pytest.fixture
def owner() -> Iterator[AuditRequestContext]:
    context = AuditRequestContext(tenant_id_hash="t" * 64, user_id_hash="u" * 64)
    token = set_audit_request_context(context)
    yield context
    reset_audit_request_context(token)


def _case_result(case_id: str, *, score: float = 1.0) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        case_id=case_id,
        trace_id=f"trace-{case_id}",
        elapsed_ms=1.0,
        answer_correctness=score,
    )


def _service(
    store: MemoryEvaluationJobStore | None = None, *, worker_id: str = "test-worker"
) -> EvaluationJobService:
    return EvaluationJobService(
        store or MemoryEvaluationJobStore(),
        heartbeat_seconds=0.005,
        worker_id=worker_id,
        finish_retry_seconds=(0.0,),
    )


async def _save_cases(
    service: EvaluationJobService, job_id: str, count: int, *, pass_index: int = 0
) -> None:
    for index in range(count):
        await service.save_case_result(
            job_id,
            pass_index=pass_index,
            case_index=index,
            search_answer_profile_id=f"profile-{pass_index}",
            result=_case_result(f"row-{index + 2}"),
        )


async def _submit(service: EvaluationJobService, executor: Any, *, total: int = 3) -> str:
    job_id = "job-a"
    await service.submit(
        kind="run",
        total_cases=total,
        time_limit_seconds=3600,
        executor=executor,
        job_id=job_id,
    )
    return job_id


async def test_failed_job_returns_finished_cases_as_partial_result(
    owner: AuditRequestContext,
) -> None:
    """途中で致命的に失敗しても、終わったケースの結果を返す（閾値の判定はしない）。"""
    service = _service()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(service, "job-a", 2)
        raise RuntimeError("合成の失敗")

    job_id = await _submit(service, executor)
    await service.wait_for(job_id)

    job = await service.get(job_id)
    assert job.status == "FAILED"
    assert job.run_result is None
    assert job.partial_result is not None
    assert [case.case_id for case in job.partial_result.case_results] == ["row-2", "row-3"]
    assert job.partial_result.case_count == 2
    assert job.partial_result.answer_correctness == 1.0
    assert job.partial_result.passed is None
    assert job.partial_result.answer_diagnostics_omitted is True


async def test_all_cases_survive_when_the_final_save_fails(owner: AuditRequestContext) -> None:
    """全ケースの後の保存に失敗しても（ORA-40604 など）、ケースの結果は残る。"""

    class Store(MemoryEvaluationJobStore):
        async def finish(self, job_id: str, **kwargs: Any) -> bool:
            if kwargs["status"] == "SUCCEEDED":
                raise Exception(SimpleNamespace(code=40604, full_code="ORA-40604"))
            return await super().finish(job_id, **kwargs)

    service = _service(Store())

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(service, "job-a", 3)
        return EvaluationJobOutcome(result={"case_count": 3})

    job_id = await _submit(service, executor)
    await service.wait_for(job_id)

    job = await service.get(job_id)
    assert job.status == "FAILED"
    assert job.error_message is not None and "ORA-40604" in job.error_message
    assert job.partial_result is not None
    assert job.partial_result.case_count == 3


async def test_cancelled_job_keeps_finished_cases(owner: AuditRequestContext) -> None:
    service = _service()
    saved = asyncio.Event()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(service, "job-a", 1)
        saved.set()
        await asyncio.sleep(30)
        raise AssertionError("取り消されるはず")

    job_id = await _submit(service, executor)
    await saved.wait()

    cancelled = await service.cancel(job_id)
    assert cancelled.status == "CANCELLED"
    assert cancelled.partial_result is not None
    assert cancelled.partial_result.case_count == 1
    await service.wait_for(job_id)


async def test_stale_job_keeps_finished_cases_when_another_process_reclaims_it(
    owner: AuditRequestContext,
) -> None:
    """プロセスが止まって別のプロセスが stale で回収した job でも、保存した行を返す。"""
    store = MemoryEvaluationJobStore()
    running = _service(store, worker_id="worker-a")
    saved = asyncio.Event()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(running, "job-a", 2)
        saved.set()
        await asyncio.sleep(30)
        raise AssertionError("止まるはず")

    job_id = await _submit(running, executor)
    await saved.wait()
    # 別のプロセスは task を持たないので、heartbeat の途絶で失敗にする。
    other = EvaluationJobService(store, worker_id="worker-b", stale_seconds=-1.0)

    job = await other.get(job_id)
    assert job.status == "FAILED"
    assert job.error_message == EVALUATION_JOB_STALE_MESSAGE
    assert job.partial_result is not None
    assert job.partial_result.case_count == 2
    await running.shutdown()


async def test_running_and_succeeded_jobs_have_no_partial_result(
    owner: AuditRequestContext,
) -> None:
    service = _service()
    release = asyncio.Event()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(service, "job-a", 1)
        await release.wait()
        return EvaluationJobOutcome(result={"case_count": 1})

    job_id = await _submit(service, executor)
    await asyncio.sleep(0.02)
    assert (await service.get(job_id)).partial_result is None
    release.set()
    await service.wait_for(job_id)
    done = await service.get(job_id)
    assert done.status == "SUCCEEDED"
    assert done.partial_result is None


async def test_saved_case_result_omits_answer_diagnostics(owner: AuditRequestContext) -> None:
    """保存する行に回答の診断（質問・回答・根拠の文）を入れない（#4107 と同じ方針）。"""
    store = MemoryEvaluationJobStore()
    service = _service(store)
    release = asyncio.Event()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await release.wait()
        return EvaluationJobOutcome(result={"case_count": 1})

    job_id = await _submit(service, executor)
    with_diagnostics = _case_result("row-2")
    with_diagnostics.diagnostics.answer = {"original_question": "合成の質問"}
    await service.save_case_result(
        job_id,
        pass_index=0,
        case_index=0,
        search_answer_profile_id=None,
        result=with_diagnostics,
    )

    rows = await store.list_case_results(
        job_id, tenant_id_hash=owner.tenant_id_hash, user_id_hash=owner.user_id_hash
    )
    assert len(rows) == 1
    assert rows[0].result["diagnostics"]["answer"] is None
    assert "合成の質問" not in str(rows[0].result)
    release.set()
    await service.wait_for(job_id)


async def test_case_result_save_failure_does_not_stop_the_evaluation(
    owner: AuditRequestContext,
) -> None:
    """ケースの保存の失敗はログだけ。評価は続き、最後の結果は全部入る。"""

    class Store(MemoryEvaluationJobStore):
        async def save_case_result(self, job_id: str, **kwargs: Any) -> bool:
            raise Exception(SimpleNamespace(code=12541, full_code="ORA-12541"))

    service = _service(Store())

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(service, "job-a", 2)
        return EvaluationJobOutcome(result={"case_count": 2})

    job_id = await _submit(service, executor)
    await service.wait_for(job_id)
    assert (await service.get(job_id)).status == "SUCCEEDED"


async def test_case_result_is_not_written_after_the_job_is_closed(
    owner: AuditRequestContext,
) -> None:
    """自分の lease の RUNNING の job にだけ書く（取り消しの後に遅れて書かない）。"""
    store = MemoryEvaluationJobStore()
    service = _service(store)
    release = asyncio.Event()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await release.wait()
        raise AssertionError("取り消されるはず")

    job_id = await _submit(service, executor)
    await service.cancel(job_id)
    await _save_cases(service, job_id, 1)

    assert (
        await store.list_case_results(
            job_id, tenant_id_hash=owner.tenant_id_hash, user_id_hash=owner.user_id_hash
        )
        == []
    )
    await service.wait_for(job_id)


async def test_case_results_are_scoped_to_the_job_owner(owner: AuditRequestContext) -> None:
    store = MemoryEvaluationJobStore()
    service = _service(store)
    release = asyncio.Event()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(service, "job-a", 1)
        release.set()
        await asyncio.sleep(30)
        raise AssertionError("止まるはず")

    job_id = await _submit(service, executor)
    await release.wait()
    assert (
        await store.list_case_results(job_id, tenant_id_hash="t" * 64, user_id_hash="x" * 64) == []
    )
    assert (
        len(
            await store.list_case_results(
                job_id, tenant_id_hash=owner.tenant_id_hash, user_id_hash=owner.user_id_hash
            )
        )
        == 1
    )
    await service.shutdown()


async def test_partial_result_includes_the_compared_profile(owner: AuditRequestContext) -> None:
    """比べるプロファイルの pass の結果は `comparison` に入れる。"""
    service = _service()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        await _save_cases(service, "job-a", 2)
        await _save_cases(service, "job-a", 1, pass_index=1)
        raise RuntimeError("合成の失敗")

    job_id = await _submit(service, executor, total=4)
    await service.wait_for(job_id)

    partial = (await service.get(job_id)).partial_result
    assert partial is not None
    assert partial.case_count == 2
    assert partial.comparison is not None
    assert partial.comparison.search_answer_profile_id == "profile-1"
    assert partial.comparison.metrics.case_count == 1
    assert partial.comparison.paired is not None
    assert partial.comparison.paired.status == "not_comparable"


async def test_partial_result_keeps_case_order_when_saved_out_of_order(
    owner: AuditRequestContext,
) -> None:
    """並行で実行すると保存の順は前後する。結果はケースの位置の順にする。"""
    service = _service()

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        for index in (2, 0, 1):
            await service.save_case_result(
                "job-a",
                pass_index=0,
                case_index=index,
                search_answer_profile_id=None,
                result=_case_result(f"row-{index + 2}"),
            )
        raise RuntimeError("合成の失敗")

    job_id = await _submit(service, executor)
    await service.wait_for(job_id)
    partial = (await service.get(job_id)).partial_result
    assert partial is not None
    assert [case.case_id for case in partial.case_results] == ["row-2", "row-3", "row-4"]


async def test_failed_listing_does_not_break_the_job_status(owner: AuditRequestContext) -> None:
    """保存した行を読めなくても、job の状態は返す。"""

    class Store(MemoryEvaluationJobStore):
        async def list_case_results(self, job_id: str, **kwargs: Any) -> list[Any]:
            raise Exception(SimpleNamespace(code=12541, full_code="ORA-12541"))

    service = _service(Store())

    async def executor(_progress: Any) -> EvaluationJobOutcome:
        raise RuntimeError("合成の失敗")

    job_id = await _submit(service, executor)
    await service.wait_for(job_id)
    job = await service.get(job_id)
    assert job.status == "FAILED"
    assert job.partial_result is None


async def test_oracle_store_writes_case_result_only_under_own_lease() -> None:
    """ケースの結果は自分の lease の RUNNING の job にだけ書き、読むときは持ち主で絞る。"""

    class Client:
        statements: list[tuple[str, dict[str, Any]]] = []

        async def save_evaluation_job_case_result(self, job_id: str, **kwargs: Any) -> bool:
            self.statements.append((job_id, kwargs))
            return True

        async def list_evaluation_job_case_results(
            self, job_id: str, **kwargs: Any
        ) -> list[dict[str, object]]:
            assert kwargs == {"tenant_id_hash": "t", "user_id_hash": "u"}
            return [
                {
                    "pass_index": 1,
                    "case_index": 4,
                    "case_id": "row-6",
                    "search_answer_profile_id": None,
                    "result_json": {"case_id": "row-6"},
                }
            ]

    store = OracleEvaluationJobStore(Client())  # type: ignore[arg-type]
    assert await store.save_case_result(
        "job-a",
        lease_owner="worker-a",
        pass_index=1,
        case_index=4,
        case_id="row-6",
        search_answer_profile_id=None,
        result={"case_id": "row-6"},
    )
    assert Client.statements[0][1]["lease_owner"] == "worker-a"
    rows = await store.list_case_results("job-a", tenant_id_hash="t", user_id_hash="u")
    assert rows[0].pass_index == 1
    assert rows[0].case_index == 4
    assert rows[0].result == {"case_id": "row-6"}


# ---- 評価ランナーから job まで ----


class _Pipeline:
    """決定論の回答。指定の質問で致命的でない失敗にする。"""

    def __init__(self, fail_query: str | None = None) -> None:
        self.fail_query = fail_query

    async def run(self, request: Any, trace_id: str, *_args: Any, **_kwargs: Any) -> Any:
        from app.schemas.search import SearchDiagnostics, SearchResponse

        if request.query == self.fail_query:
            raise RuntimeError("合成の失敗")
        return SearchResponse(
            trace_id=trace_id,
            answer="合成の回答",
            citations=[],
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics(),
        )


async def test_runner_reports_each_finished_case_with_its_position() -> None:
    """ケースが終わるたびに、位置（行に戻すための順番）と結果を渡す。失敗のケースも渡す。"""
    from app.schemas.evaluation import EvaluationCase

    cases = [
        EvaluationCase.model_validate(
            {"id": f"row-{index + 2}", "query": f"合成の質問 {index}", "relevant_document_ids": []}
        )
        for index in range(3)
    ]
    reported: list[tuple[int, str, str]] = []
    finished_when_progress_ends: list[int] = []

    async def on_case_result(*, case_index: int, result: EvaluationCaseResult) -> None:
        reported.append((case_index, result.case_id, result.status))

    async def progress(*, completed_cases: int, current_case_id: str | None) -> None:
        if current_case_id is None:
            finished_when_progress_ends.append(len(reported))

    callback: EvaluationCaseResultCallback = on_case_result
    pipeline = cast(SearchPipeline, _Pipeline(fail_query="合成の質問 1"))
    metrics = await EvaluationRunner(pipeline=pipeline).run(
        cases=cases, top_k=5, progress=progress, on_case_result=callback
    )

    assert sorted(reported) == [
        (0, "row-2", "success"),
        (1, "row-3", "error"),
        (2, "row-4", "success"),
    ]
    assert finished_when_progress_ends == [3]
    assert metrics.case_count == 3


@pytest.fixture
def api_service() -> Iterator[EvaluationJobService]:
    installed = set_evaluation_job_service(_service())
    yield installed
    set_evaluation_job_service(None)


@pytest.fixture
async def api_client(api_service: EvaluationJobService) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=TEST_REQUEST_HEADERS
    ) as client:
        yield client


def _run_body(**extra: Any) -> dict[str, Any]:
    return {
        "cases": [
            {"id": "row-2", "query": "合成の質問 1", "relevant_document_ids": []},
            {"id": "row-3", "query": "合成の質問 2", "relevant_document_ids": []},
        ],
        "top_k": 5,
        **extra,
    }


async def test_run_api_saves_cases_and_exports_partial_excel_after_failure(
    api_client: httpx.AsyncClient,
    api_service: EvaluationJobService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API の経路: 全ケースの後の失敗でも、途中までの結果の API と Excel で読める。"""

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> Any:
        for index, case in enumerate(kwargs["cases"]):
            await kwargs["on_case_result"](
                case_index=index,
                result=_case_result(case.id, score=0.5),
            )
        return EvaluationMetrics.model_validate({"case_count": 2})

    calls = {"count": 0}

    async def manifest(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["count"] += 1
        if calls["count"] > 1:
            raise RuntimeError("合成の失敗")
        return {}

    async def fake_save(self: OracleClient, artifact: Mapping[str, object]) -> str:
        return "artifact-1"

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr(OracleClient, "save_evaluation_artifact", fake_save)
    monkeypatch.setattr("app.api.routes.evaluation.build_run_manifest", manifest)

    job = (await api_client.post("/api/evaluation/jobs/run", json=_run_body())).json()["data"]
    await api_service.wait_for(job["job_id"])

    done = (await api_client.get(f"/api/evaluation/jobs/{job['job_id']}")).json()["data"]
    assert done["status"] == "FAILED"
    assert done["run_result"] is None
    assert done["partial_result"]["case_count"] == 2

    exported = await api_client.post(f"/api/evaluation/jobs/{job['job_id']}/results.xlsx", json={})
    assert exported.status_code == 200
    book = load_workbook(BytesIO(exported.content))
    assert book["主条件の結果"]["A2"].value == "row-2"
    assert book["主条件の結果"]["A3"].value == "row-3"
    assert [row[0].value for row in book["概要"].iter_rows()][-1] == "途中までの結果"
    assert book["概要"]["B17"].value == "全 2 ケースのうち 2 ケース"


async def test_compared_pass_is_saved_with_its_own_index_and_profile(
    api_client: httpx.AsyncClient,
    api_service: EvaluationJobService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """比べるプロファイルの pass は別の pass_index で、そのプロファイルの ID と一緒に保存する。"""

    async def fake_run(self: EvaluationRunner, **kwargs: Any) -> Any:
        await kwargs["on_case_result"](case_index=0, result=_case_result("row-2"))
        if kwargs["search_answer_profile_id"] == "profile-b":
            raise RuntimeError("合成の失敗")
        return EvaluationMetrics.model_validate({"case_count": 1})

    async def manifest(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(EvaluationRunner, "run", fake_run)
    monkeypatch.setattr("app.api.routes.evaluation.build_run_manifest", manifest)

    job = (
        await api_client.post(
            "/api/evaluation/jobs/run",
            json=_run_body(
                search_answer_profile_id="profile-a",
                compare_search_answer_profile_id="profile-b",
            ),
        )
    ).json()["data"]
    await api_service.wait_for(job["job_id"])

    partial = (await api_client.get(f"/api/evaluation/jobs/{job['job_id']}")).json()["data"][
        "partial_result"
    ]
    assert partial["case_count"] == 1
    assert partial["comparison"]["search_answer_profile_id"] == "profile-b"
    assert partial["comparison"]["metrics"]["case_count"] == 1
