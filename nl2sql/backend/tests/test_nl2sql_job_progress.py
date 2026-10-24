"""ジョブの処理の経過のイベント（チャット・SQL 生成・実行履歴の `ChatProgress`。#1359・#3226）。

- 処理の経過は実行の trace（span の木）の投影: 実行の全体（root）の下に、開始待ち（`queue`）と
  5 段階の span が並ぶ。段の span は表示用のデータの `kind` に段階の id、補足の値（生成方法・
  参照した表・行数）は `params`、開いて見る詳細は `details`。
- 保存と読み込み（ジョブの文書の `progress_events`。別の worker が読み直しても同じ）。
- 配信: `GET /jobs/{job_id}/progress?since=`（polling）と `/progress/stream`（SSE。`since` /
  `Last-Event-ID` の続きから・終わったジョブの続きは 204・権限は `GET /jobs/{job_id}` と同じ）。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pr_backend_core.chat_progress import (
    ChatProgressStepEvent,
    ChatProgressStepState,
    ChatProgressTerminalEvent,
    fold_chat_progress_events,
)
from pr_backend_core.observability.tracing import SpanDisplay, SpanUpdate
from test_nl2sql_job_runtime import (
    _FakeEnterpriseAiClient,
    _put_in_flight_snapshot,
    _repository,
    _request,
    _worker,
)
from test_nl2sql_operation_profile_access import _principal

from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import JobCreateRequest, JobData, JobStatus
from app.features.nl2sql.service import (
    JOB_CANCELLED_ERROR_CODE,
    Nl2SqlService,
    StoredJob,
    _new_job_steps,
    _record_job_progress_span,
)
from app.security.permissions import QUERY_GENERATE_PERMISSION, SQL_EXECUTE_PERMISSION
from app.settings import get_settings

_PROFILE_ID = "orders-profile"
_STEPS = [
    "queue",
    "prepare_context",
    "generate_sql",
    "safety_check",
    "execute_sql",
    "format_results",
]

type Event = ChatProgressStepEvent | ChatProgressTerminalEvent
type Fixture = tuple[Nl2SqlService, MemoryIncrementalNl2SqlRepository]


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> Fixture:
    # worker は明示して動かす（記録の位置を 1 つずつ確かめる）。
    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    repository = _repository()
    return _worker(repository), repository


def chat_turn(**update: Any) -> JobCreateRequest:
    return _request().model_copy(update={"chat": True, **update})


def start(service: Nl2SqlService, req: JobCreateRequest | None = None) -> str:
    created = service.start_job(
        req or chat_turn(), actor_user_uuid="user-1", actor_is_system_admin=True
    )
    return created.job_id


def run(service: Nl2SqlService, job_id: str) -> JobData:
    assert service.run_next_nl2sql_job(worker_id="progress-test", job_id=job_id)
    job = service.get_job(job_id, actor_user_uuid="user-1")
    assert job is not None
    return job


def stages(events: list[Event]) -> dict[str, ChatProgressStepState]:
    """工程の span（`kind` が段階の id）。"""

    fold = fold_chat_progress_events(events)
    return {step.kind: step for step in fold.step_list() if step.kind in _STEPS}


def states(events: list[Event]) -> list[str]:
    return [f"{kind}:{step.status}" for kind, step in stages(events).items()]


def params(events: list[Event], kind: str) -> dict[str, Any]:
    return dict(stages(events)[kind].params or {})


def root(events: list[Event]) -> ChatProgressStepState:
    fold = fold_chat_progress_events(events)
    [found] = [step for step in fold.step_list() if (step.span or {}).get("root")]
    return found


def assert_contiguous(events: list[Event]) -> None:
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert {event.target_id for event in events} == {events[0].target_id}


# --- 記録の位置 -----------------------------------------------------------------------------


def test_created_job_has_no_progress_until_the_worker_starts(jobs: Fixture) -> None:
    """worker が受け取るまでは span が無い（画面は開始待ちの仮の表示を出す。#3226）。"""

    service, repository = jobs
    created = service.start_job(chat_turn(), actor_user_uuid="user-1", actor_is_system_admin=True)
    job = service.get_job(created.job_id, actor_user_uuid="user-1")
    assert job is not None and job.status == JobStatus.PENDING
    assert created.progress_events == [] and job.progress_events == []
    document = repository.get_document("jobs", created.job_id)
    assert document is not None
    assert document["progress_events"] == []


def test_done_chat_turn_records_the_span_tree_and_params(jobs: Fixture) -> None:
    service, repository = jobs
    job = run(service, start(service))
    assert job.status == JobStatus.DONE, job.error_message
    history = repository.list_documents("history", limit=100)
    assert len(history) == 1
    assert history[0]["progress_events"] == [
        event.model_dump(mode="json") for event in job.progress_events
    ]

    events = job.progress_events
    assert_contiguous(events)
    assert states(events) == [f"{step}:done" for step in _STEPS]
    fold = fold_chat_progress_events(events)
    assert fold.terminal == "done"
    assert fold.attempt == 0
    # 実行の全体（root）の下に工程の span が並ぶ（親子・span の名前・trace id）。
    top = root(events)
    assert top.span is not None and top.span["name"] == "nl2sql.execute"
    for kind, step in stages(events).items():
        assert step.span is not None
        assert step.span["name"] == f"nl2sql.{kind}"
        assert step.span["parent_id"] == top.step_id
        assert step.span["trace_id"] == top.span["trace_id"]
    # 保存した trace（`trace_summary`）と同じ span（id が一致する）。
    assert job.trace_summary is not None
    span_ids = {span.span_id for span in job.trace_summary.spans}
    assert {step.step_id for step in stages(events).values()} <= span_ids
    # 補足の値: 生成方法・参照した表・取得した行数（名前と文言は画面が付ける）。
    assert params(events, "generate_sql") == {"engine": "enterprise_ai_direct"}
    assert params(events, "safety_check") == {"tables": "APP.ORDERS", "table_count": 1}
    assert job.last_execution is not None
    assert params(events, "execute_sql") == {"rows": job.last_execution.row_count}
    # 段を開いて見る詳細（#2294）: 生成した SQL・安全性の確認の結果と参照した表・取得した行数。
    found = stages(events)
    generate = found["generate_sql"].details
    assert generate is not None and generate.code[0].language == "sql"
    assert job.result is not None
    assert generate.code[0].text == job.result.generated_sql
    safety = found["safety_check"].details
    assert safety is not None
    assert {field.label: field.value for field in safety.fields}["結果"] == "安全"
    assert {field.label: field.value for field in safety.fields}["参照した表"] == "APP.ORDERS"
    execute = found["execute_sql"].details
    assert execute is not None
    assert execute.fields[0].label == "取得した行数"
    assert found["queue"].details is None
    # 開始待ちはジョブの作成から worker の開始まで。各段階は開始と終了の時刻を持つ。
    queue = found["queue"]
    assert queue.started_at == datetime.fromisoformat(job.created_at)
    assert queue.finished_at == datetime.fromisoformat(job.started_at or "")
    for kind in _STEPS[1:]:
        step = found[kind]
        assert step.started_at is not None and step.finished_at is not None, kind
    # 終端は最後のイベント。
    assert isinstance(events[-1], ChatProgressTerminalEvent)

    # 別の worker（プロセス）が読み直しても同じイベント。
    observer = _worker(repository)
    seen = observer.get_job(job.job_id, actor_user_uuid="user-1")
    assert seen is not None
    assert seen.progress_events == events


def test_running_job_saves_running_spans_and_keeps_formatting_until_the_result(
    jobs: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """実行中の保存に、始まった段が実行中で出る。結果の整形は結果の保存まで実行中（#1176）。"""

    service, repository = jobs
    job_id = start(service)
    snapshots: list[dict[str, Any]] = []
    save = repository.patch_document_if_current

    def record(collection: str, entity_id: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        if collection == "jobs":
            snapshots.append(payload)
        return save(collection, entity_id, payload, **kwargs)

    monkeypatch.setattr(repository, "patch_document_if_current", record)
    run(service, job_id)
    running: set[str] = set()
    last_open: list[Event] = []
    for snapshot in snapshots:
        events = service._job_from_snapshot(snapshot).progress_events  # noqa: SLF001
        fold = fold_chat_progress_events(events)
        if fold.terminal is not None:
            continue
        # 終端になるまで、実行の全体は実行中。
        assert root(events).status == "running"
        running |= {kind for kind, step in stages(events).items() if step.status == "running"}
        last_open = events
    # 段はそれぞれ実行中の状態で保存される（画面が実行中の行を出せる）。
    assert running == set(_STEPS[1:])
    assert stages(last_open)["format_results"].status == "running"


def test_generation_only_turn_skips_execution(jobs: Fixture) -> None:
    service, _ = jobs
    job = run(service, start(service, chat_turn(generation_only=True)))
    assert job.status == JobStatus.DONE
    assert states(job.progress_events) == [
        "queue:done",
        "prepare_context:done",
        "generate_sql:done",
        "safety_check:done",
        "execute_sql:skipped",
        "format_results:done",
    ]
    execute = stages(job.progress_events)["execute_sql"]
    # 実行しなかった段は所要時間 0 の span（行は残す）。
    assert execute.started_at is not None and execute.started_at == execute.finished_at


def test_blocked_sql_fails_safety_check_and_ends_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    service = _worker(
        _repository(),
        _FakeEnterpriseAiClient('{"sql":"DELETE FROM APP.ORDERS","explanation":"削除します。"}'),
    )
    job = run(service, start(service))
    assert job.status == JobStatus.ERROR
    assert states(job.progress_events) == [
        "queue:done",
        "prepare_context:done",
        "generate_sql:done",
        "safety_check:failed",
        "execute_sql:skipped",
        "format_results:done",
    ]
    assert fold_chat_progress_events(job.progress_events).terminal == "failed"
    # 遮断した SQL の参照した表は補足に出さない。
    assert params(job.progress_events, "safety_check") == {}


def test_failed_stage_fails_its_span_and_the_rest_do_not_start(
    jobs: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = jobs

    def failing_generate(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("想定外の失敗")

    monkeypatch.setattr(service, "_generate_selected_engine", failing_generate)
    job = run(service, start(service))
    assert job.status == JobStatus.ERROR
    # 失敗した段の span は失敗。後の段は始まらない（span が無い）。
    assert states(job.progress_events) == [
        "queue:done",
        "prepare_context:done",
        "generate_sql:failed",
    ]
    assert fold_chat_progress_events(job.progress_events).terminal == "failed"
    # 例外の文はイベントに入れない（span の属性は例外の種類だけ）。
    assert "想定外" not in json.dumps([e.model_dump(mode="json") for e in job.progress_events])


def test_cancelled_job_ends_cancelled_after_the_finished_spans(jobs: Fixture) -> None:
    service, _ = jobs
    job_id = start(service)
    assert service.request_job_cancel(job_id, actor_user_uuid="user-1") is not None
    job = run(service, job_id)
    assert job.error_code == JOB_CANCELLED_ERROR_CODE

    events = job.progress_events
    # 停止は段の span の中で確かめる: 止めた段（準備）は停止、後の段は始まらない。
    assert states(events) == ["queue:done", "prepare_context:skipped"]
    prepare = stages(events)["prepare_context"]
    assert prepare.span is not None and prepare.span["cancelled"] is True
    fold = fold_chat_progress_events(events)
    assert fold.terminal == "cancelled"
    assert root(events).status == "skipped"


def test_interrupted_job_before_start_ends_failed() -> None:
    """worker が始める前に終わったジョブ（再起動で中断）は、span の無いまま失敗で終わる。"""

    job = StoredJob(job_id="job-interrupted", request=chat_turn(), steps=_new_job_steps())
    Nl2SqlService._mark_job_interrupted(job)  # noqa: SLF001
    assert states(job.progress_events) == []
    assert fold_chat_progress_events(job.progress_events).terminal == "failed"


def _span_update(
    span_id: str, kind: str | None, status: str, *, at: str, root_span: bool = False
) -> SpanUpdate:
    return SpanUpdate.model_validate(
        {
            "span_id": span_id,
            "parent_span_id": None if root_span else "a" * 16,
            "trace_id": "b" * 32,
            "operation_name": f"nl2sql.{kind or 'execute'}",
            "status": status,
            "started_at": at,
            "root": root_span,
            "display": SpanDisplay(kind=kind) if kind else None,
        }
    )


def test_reclaimed_job_starts_a_new_attempt(jobs: Fixture) -> None:
    """lease の切れたジョブを引き継いだ実行は、試行を増やして span の木を作り直す（#1358）。"""

    service, repository = jobs
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _put_in_flight_snapshot(service, repository, job_id="job-orphan", updated_at=two_hours_ago)
    # 前の実行（最初の試行）が準備の途中まで記録していた。
    previous = StoredJob(
        job_id="job-orphan",
        request=_request(),
        created_at=two_hours_ago,
        started_at=two_hours_ago,
        steps=_new_job_steps(),
        attempt=1,
    )
    for update in (
        _span_update("a" * 16, None, "running", at=two_hours_ago, root_span=True),
        _span_update("c" * 16, "prepare_context", "running", at=two_hours_ago),
    ):
        _record_job_progress_span(previous, update)
    document = repository.get_document("jobs", "job-orphan")
    assert document is not None
    document["progress_events"] = [e.model_dump(mode="json") for e in previous.progress_events]
    repository.put_document("jobs", "job-orphan", document, status="running")
    first_seq = previous.progress_events[-1].seq

    assert service.run_next_nl2sql_job(worker_id="worker-retry") is True
    job = service.get_job("job-orphan")
    assert job is not None and job.status == JobStatus.DONE and job.attempt == 2

    events = job.progress_events
    assert_contiguous(events)
    # 番号は続け、新しい試行（attempt 1）で span の木を出し直す（前の試行の span は残らない）。
    later = [event for event in events if event.seq > first_seq]
    assert {event.attempt for event in later} == {1}
    fold = fold_chat_progress_events(events)
    assert fold.attempt == 1
    assert "c" * 16 not in fold.steps
    assert states(events) == [f"{step}:done" for step in _STEPS]
    assert fold.terminal == "done"


def test_span_after_the_terminal_is_not_recorded() -> None:
    """span の知らせは記録し、終端の後の知らせ（実行の全体の終了など）は記録しない。"""

    at = datetime.now(UTC).isoformat()
    job = StoredJob(job_id="job-closed", request=chat_turn(), steps=_new_job_steps(), attempt=1)
    assert _record_job_progress_span(
        job, _span_update("a" * 16, None, "running", at=at, root_span=True)
    )
    assert _record_job_progress_span(
        job, _span_update("c" * 16, "prepare_context", "running", at=at)
    )
    # 同じ状態の同じ知らせは重ねない。
    assert not _record_job_progress_span(
        job, _span_update("c" * 16, "prepare_context", "running", at=at)
    )
    Nl2SqlService._mark_job_interrupted(job)  # noqa: SLF001
    count = len(job.progress_events)
    assert not _record_job_progress_span(
        job, _span_update("a" * 16, None, "done", at=at, root_span=True)
    )
    assert len(job.progress_events) == count
    assert states(job.progress_events) == ["prepare_context:failed"]


def test_snapshot_without_or_with_broken_events_reads_empty(jobs: Fixture) -> None:
    service, _ = jobs
    stored = StoredJob(job_id="job-old", request=_request(), steps=_new_job_steps())
    snapshot = service._job_to_snapshot(stored)  # noqa: SLF001
    snapshot.pop("progress_events")
    assert service._job_from_snapshot(snapshot).progress_events == []  # noqa: SLF001
    # 旧形式（段階の snapshot の list）・形の違う要素は捨てる。
    snapshot["progress_events"] = [{"stage": "prepare_context", "status": "done"}, "x"]
    assert service._job_from_snapshot(snapshot).progress_events == []  # noqa: SLF001


# --- 配信（polling・SSE） ------------------------------------------------------------------

_PERMISSIONS = {QUERY_GENERATE_PERMISSION, SQL_EXECUTE_PERMISSION}


class _Viewer:
    """要求の利用者（テストが差し替える）。"""

    def __init__(self) -> None:
        self.user_uuid = "user-1"
        self.profiles = {_PROFILE_ID}


@pytest.fixture
def api(jobs: Fixture, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, _Viewer]]:
    service, _ = jobs
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", service)
    viewer = _Viewer()
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next: Any) -> Any:
        current = _principal(viewer.profiles, permissions=_PERMISSIONS)
        request.state.principal = replace(current, user_uuid=viewer.user_uuid)
        return await call_next(request)

    app.include_router(nl2sql_router.router, prefix="/api")
    app.dependency_overrides[nl2sql_router._require_persistence] = lambda: None  # noqa: SLF001
    with TestClient(app) as client:
        yield client, viewer


def sse_events(text: str) -> list[dict[str, Any]]:
    """SSE の本文の `event: chat_progress` のイベント（`id:` と data）。"""

    events: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            name, _, value = line.partition(": ")
            fields[name] = value
        if fields.get("event") == "chat_progress":
            data = json.loads(fields["data"])
            assert int(fields["id"]) == data["seq"]
            events.append(data)
    return events


def test_progress_polling_returns_events_after_since(
    jobs: Fixture, api: tuple[TestClient, _Viewer]
) -> None:
    service, _ = jobs
    client, _ = api
    job = run(service, start(service))
    last = job.progress_events[-1].seq

    body = client.get(f"/api/nl2sql/jobs/{job.job_id}/progress").json()["data"]
    assert body["target_id"] == job.job_id
    assert body["attempt"] == 0
    assert body["last_seq"] == last
    assert body["terminal"] is True
    assert [item["seq"] for item in body["events"]] == list(range(1, last + 1))

    later = client.get(f"/api/nl2sql/jobs/{job.job_id}/progress", params={"since": last - 2})
    assert [item["seq"] for item in later.json()["data"]["events"]] == [last - 1, last]
    assert client.get(f"/api/nl2sql/jobs/{job.job_id}/progress?since=-1").status_code == 422


def test_progress_routes_check_access_like_get_job(
    jobs: Fixture, api: tuple[TestClient, _Viewer]
) -> None:
    service, _ = jobs
    client, viewer = api
    job_id = start(service)
    for path in ("progress", "progress/stream"):
        assert client.get(f"/api/nl2sql/jobs/missing/{path}").status_code == 404
        # 他の利用者のジョブは 404（存在を漏らさない。#1889）。
        viewer.user_uuid = "other"
        assert client.get(f"/api/nl2sql/jobs/{job_id}/{path}").status_code == 404
        viewer.user_uuid = "user-1"
        # 業務プロファイルの利用権限が外れた。
        viewer.profiles = set()
        assert client.get(f"/api/nl2sql/jobs/{job_id}/{path}").status_code == 403
        viewer.profiles = {_PROFILE_ID}


def test_progress_stream_sends_events_and_closes_at_terminal(
    jobs: Fixture, api: tuple[TestClient, _Viewer]
) -> None:
    service, _ = jobs
    client, _ = api
    job = run(service, start(service))
    last = job.progress_events[-1].seq

    response = client.get(f"/api/nl2sql/jobs/{job.job_id}/progress/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    sent = sse_events(response.text)
    assert [item["seq"] for item in sent] == list(range(1, last + 1))
    assert sent[-1]["type"] == "terminal" and sent[-1]["status"] == "done"

    # 続きから（`since`・ブラウザの張り直しの `Last-Event-ID` の大きい方）。
    resumed = client.get(
        f"/api/nl2sql/jobs/{job.job_id}/progress/stream?since=1",
        headers={"Last-Event-ID": str(last - 1)},
    )
    assert [item["seq"] for item in sse_events(resumed.text)] == [last]
    # 終わったジョブを続きから求めたら 204（ブラウザは張り直しをやめる）。
    finished = client.get(f"/api/nl2sql/jobs/{job.job_id}/progress/stream?since={last}")
    assert finished.status_code == 204
    finished = client.get(
        f"/api/nl2sql/jobs/{job.job_id}/progress/stream", headers={"Last-Event-ID": str(last)}
    )
    assert finished.status_code == 204


def test_progress_stream_delivers_events_recorded_by_another_worker(
    jobs: Fixture, api: tuple[TestClient, _Viewer]
) -> None:
    """SSE はジョブの文書を読み直して送る（worker が別のプロセスでも届く）。"""

    service, repository = jobs
    client, _ = api
    job_id = start(service)
    worker = _worker(repository)

    def run_later() -> None:
        time.sleep(0.3)
        worker.run_next_nl2sql_job(worker_id="other-process", job_id=job_id)

    thread = threading.Thread(target=run_later, daemon=True)
    thread.start()
    with client.stream("GET", f"/api/nl2sql/jobs/{job_id}/progress/stream") as response:
        text = "".join(response.iter_text())
    thread.join(timeout=10)
    sent = sse_events(text)
    assert [item["seq"] for item in sent] == list(range(1, len(sent) + 1))
    assert sent[-1]["type"] == "terminal" and sent[-1]["status"] == "done"
    persisted = repository.get_document("jobs", job_id)
    assert persisted is not None
    assert len(persisted["progress_events"]) == len(sent)


@pytest.mark.parametrize("generation_only", [False, True])
def test_query_history_keeps_the_same_progress_as_the_generation_page(
    jobs: Fixture, generation_only: bool
) -> None:
    service, repository = jobs
    request = _request().model_copy(update={"chat": False, "generation_only": generation_only})
    job = run(service, start(service, request))
    assert job.status == JobStatus.DONE
    history = repository.list_documents("history", limit=100)
    assert len(history) == 1
    assert history[0]["progress_events"] == [
        event.model_dump(mode="json") for event in job.progress_events
    ]
    execute = stages(job.progress_events)["execute_sql"]
    assert execute.status == ("skipped" if generation_only else "done")
