"""ジョブの処理の段階のイベント（チャットの `ChatProgress`。3 製品共通の契約。#1359）。

- 記録の位置: 作成（開始待ちと 5 段階）・worker の開始・各段階の終了と開始・完了・失敗・停止・
  引き継いだ試行。補足の値（生成方法・参照した表・行数・停止の印）は `params`。
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
    ChatProgressTerminalEvent,
    fold_chat_progress_events,
)
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
from app.features.nl2sql.models import JobCreateRequest, JobData, JobStatus, JobStepStatus
from app.features.nl2sql.service import (
    JOB_CANCELLED_ERROR_CODE,
    Nl2SqlService,
    StoredJob,
    _new_job_steps,
    _record_job_progress_created,
    _record_job_progress_started,
    _record_job_progress_transition,
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


def states(events: list[Event]) -> list[str]:
    fold = fold_chat_progress_events(events)
    return [f"{step.step_id}:{step.status}" for step in fold.step_list()]


def params(events: list[Event], step_id: str) -> dict[str, Any]:
    step = fold_chat_progress_events(events).steps[step_id]
    return dict(step.params or {})


def assert_contiguous(events: list[Event]) -> None:
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert {event.target_id for event in events} == {events[0].target_id}


# --- 記録の位置 -----------------------------------------------------------------------------


def test_created_job_waits_in_queue_from_creation(jobs: Fixture) -> None:
    service, repository = jobs
    created = service.start_job(chat_turn(), actor_user_uuid="user-1", actor_is_system_admin=True)
    job_id = created.job_id
    job = service.get_job(job_id, actor_user_uuid="user-1")
    assert job is not None and job.status == JobStatus.PENDING
    # 投入の応答にも作成時のイベントを入れる（画面は会話を取り直す前から段階を出す）。
    assert created.progress_events == job.progress_events

    events = job.progress_events
    assert_contiguous(events)
    # 開始待ちと 5 段階を先に出し（並びを決める）、開始待ちだけを実行中にする。
    assert states(events) == ["queue:running", *(f"{step}:pending" for step in _STEPS[1:])]
    queue = fold_chat_progress_events(events).steps["queue"]
    assert queue.started_at == datetime.fromisoformat(job.created_at)
    assert fold_chat_progress_events(events).terminal is None
    assert {event.attempt for event in events} == {0}
    # ジョブの文書にも保存する（別の worker が読み直せる）。
    document = repository.get_document("jobs", job_id)
    assert document is not None
    assert [item["seq"] for item in document["progress_events"]] == [e.seq for e in events]


def test_done_chat_turn_records_every_step_and_params(jobs: Fixture) -> None:
    service, repository = jobs
    job = run(service, start(service))
    assert job.status == JobStatus.DONE, job.error_message

    events = job.progress_events
    assert_contiguous(events)
    assert states(events) == [f"{step}:done" for step in _STEPS]
    fold = fold_chat_progress_events(events)
    assert fold.terminal == "done"
    assert fold.attempt == 0
    # 補足の値: 生成方法・参照した表・取得した行数（名前と文言は画面が付ける）。
    assert params(events, "generate_sql") == {"engine": "enterprise_ai_direct"}
    assert params(events, "safety_check") == {"tables": "APP.ORDERS", "table_count": 1}
    assert job.last_execution is not None
    assert params(events, "execute_sql") == {"rows": job.last_execution.row_count}
    # 開始待ちは worker の開始で終わり、各段階は開始と終了の時刻を持つ。
    queue = fold.steps["queue"]
    assert queue.finished_at == datetime.fromisoformat(job.started_at or "")
    for step_id in _STEPS[1:]:
        step = fold.steps[step_id]
        assert step.started_at is not None and step.finished_at is not None, step_id
    # 終端は最後のイベント。結果の整形は保存まで実行中だった（終端の直前に完了する）。
    assert isinstance(events[-1], ChatProgressTerminalEvent)
    assert isinstance(events[-2], ChatProgressStepEvent)
    assert events[-2].step_id == "format_results" and events[-2].status == "done"

    # 別の worker（プロセス）が読み直しても同じイベント。
    observer = _worker(repository)
    seen = observer.get_job(job.job_id, actor_user_uuid="user-1")
    assert seen is not None
    assert seen.progress_events == events


def test_not_terminal_job_always_has_a_current_step(
    jobs: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """終端になるまで、実行中か待機中の段階が必ずある（#1176）。保存のたびに確かめる。"""

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
    assert snapshots
    for snapshot in snapshots:
        events = service._job_from_snapshot(snapshot).progress_events  # noqa: SLF001
        fold = fold_chat_progress_events(events)
        if fold.terminal is not None:
            continue
        assert any(step.status in {"running", "pending"} for step in fold.step_list()), states(
            events
        )


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
    execute = fold_chat_progress_events(job.progress_events).steps["execute_sql"]
    # 始まらなかった段階には終了の時刻を付けない（所要時間を出さない）。
    assert execute.started_at is None and execute.finished_at is None


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


def test_failed_stage_is_failed_and_the_rest_skipped(
    jobs: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = jobs

    def failing_generate(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("想定外の失敗")

    monkeypatch.setattr(service, "_generate_selected_engine", failing_generate)
    job = run(service, start(service))
    assert job.status == JobStatus.ERROR
    assert states(job.progress_events) == [
        "queue:done",
        "prepare_context:done",
        "generate_sql:failed",
        "safety_check:skipped",
        "execute_sql:skipped",
        "format_results:skipped",
    ]
    assert fold_chat_progress_events(job.progress_events).terminal == "failed"
    # 例外の種類・文はイベントに入れない。
    assert "想定外" not in json.dumps([e.model_dump(mode="json") for e in job.progress_events])


def test_cancelled_job_marks_the_running_step_stopped(jobs: Fixture) -> None:
    service, _ = jobs
    job_id = start(service)
    assert service.request_job_cancel(job_id, actor_user_uuid="user-1") is not None
    job = run(service, job_id)
    assert job.error_code == JOB_CANCELLED_ERROR_CODE

    events = job.progress_events
    # 停止は失敗ではない: 止めた段階は未実行と停止の印、残りは未実行、終端は停止。
    assert states(events) == [
        "queue:done",
        "prepare_context:skipped",
        *(f"{step}:skipped" for step in _STEPS[2:]),
    ]
    assert params(events, "prepare_context") == {"stopped": True}
    assert params(events, "generate_sql") == {}
    assert fold_chat_progress_events(events).terminal == "cancelled"


def test_interrupted_job_before_start_fails_the_queue() -> None:
    """worker が始める前に終わったジョブ（再起動で中断）は、開始待ちの段階を失敗にする。"""

    job = StoredJob(job_id="job-interrupted", request=chat_turn(), steps=_new_job_steps())
    _record_job_progress_created(job)
    Nl2SqlService._mark_job_interrupted(job)  # noqa: SLF001
    assert states(job.progress_events) == [
        "queue:failed",
        *(f"{step}:skipped" for step in _STEPS[1:]),
    ]
    assert fold_chat_progress_events(job.progress_events).terminal == "failed"


def test_reclaimed_job_starts_a_new_attempt(jobs: Fixture) -> None:
    """lease の切れたジョブを引き継いだ実行は、試行を増やして段階を初めからにする（#1358）。"""

    service, repository = jobs
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _put_in_flight_snapshot(service, repository, job_id="job-orphan", updated_at=two_hours_ago)
    # 前の実行（最初の試行）が生成の途中まで記録していた。
    previous = StoredJob(
        job_id="job-orphan",
        request=_request(),
        created_at=two_hours_ago,
        started_at=two_hours_ago,
        steps=_new_job_steps(),
    )
    _record_job_progress_created(previous)
    previous.attempt = 1
    _record_job_progress_started(previous)
    _record_job_progress_transition(
        previous,
        completed_stage="prepare_context",
        completed_status=JobStepStatus.DONE,
        completed_params=None,
        running_stage="generate_sql",
        running_params={"engine": "enterprise_ai_direct"},
    )
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
    # 番号は続け、新しい試行（attempt 1）で段階を出し直す。
    later = [event for event in events if event.seq > first_seq]
    assert {event.attempt for event in later} == {1}
    fold = fold_chat_progress_events(events)
    assert fold.attempt == 1
    assert states(events) == [f"{step}:done" for step in _STEPS]
    assert fold.terminal == "done"


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
        # 他の利用者のジョブ（チャットは管理の権限でも開かない）。
        viewer.user_uuid = "other"
        assert client.get(f"/api/nl2sql/jobs/{job_id}/{path}").status_code == 403
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
