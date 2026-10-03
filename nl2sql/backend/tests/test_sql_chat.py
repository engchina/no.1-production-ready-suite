"""SQL チャットの永続化、文脈、生成だけのモード、所有者の境界。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_nl2sql_job_runtime import _FakeEnterpriseAiClient, _repository, _request, _worker

from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    AllowedObjects,
    AnalyzeData,
    JobCreateRequest,
    JobData,
    JobStatus,
    JobStepStatus,
    Nl2SqlProfile,
    QueryResults,
    SafetyReport,
)
from app.features.nl2sql.service import Nl2SqlService
from app.settings import get_settings


class RecordingClient(_FakeEnterpriseAiClient):
    def __init__(self) -> None:
        super().__init__('{"sql":"SELECT ID FROM APP.ORDERS","explanation":"注文 ID"}')
        self.prompts: list[str] = []

    def generate(self, **kwargs: object) -> str:
        self.prompts.append(str(kwargs["prompt"]))
        return self.text


type ChatFixture = tuple[Nl2SqlService, MemoryIncrementalNl2SqlRepository, RecordingClient]


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> ChatFixture:
    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    repository = _repository()
    client = RecordingClient()
    service = _worker(repository, client)
    return service, repository, client


def request(question: str = "注文一覧", previous: str | None = None) -> JobCreateRequest:
    return _request().model_copy(
        update={
            "question": question,
            "generation_only": True,
            "previous_job_id": previous,
        }
    )


def run(service: Nl2SqlService, req: JobCreateRequest, actor: str = "user-1") -> JobData:
    created = service.start_job(req, actor_user_uuid=actor, actor_is_system_admin=True)
    assert service.run_next_nl2sql_job(worker_id="chat-test", job_id=created.job_id)
    job = service.get_job(created.job_id, actor_user_uuid=actor)
    assert job is not None
    return job


def test_chat_generates_without_execution_and_restores_across_workers(
    chat: ChatFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, client = chat
    monkeypatch.setattr(
        service, "execute_sql", lambda *args, **kwargs: pytest.fail("送信で SQL を実行")
    )
    first = run(service, request())
    assert first.status == JobStatus.DONE
    assert first.result is not None
    assert first.result.results.total == 0
    assert (
        next(step for step in first.steps if step.stage == "execute_sql").status
        == JobStepStatus.SKIPPED
    )
    second = run(service, request("多い順にして", first.job_id))
    assert second.status == JobStatus.DONE
    assert second.conversation_id == first.job_id
    assert second.question == "多い順にして"
    assert "注文一覧" in client.prompts[-1]
    assert "SELECT ID FROM APP.ORDERS" in client.prompts[-1]
    assert "多い順にして" in client.prompts[-1]
    observer = _worker(repository)
    restored = observer.get_sql_chat(first.job_id, actor="user-1")
    assert restored is not None
    assert [turn.job_id for turn in restored.turns] == [first.job_id, second.job_id]
    summaries = observer.list_sql_chats(actor="user-1", profile_ids=None)
    assert [item.id for item in summaries.items] == [first.job_id]
    assert observer.list_sql_chats(actor="other", profile_ids=None).items == []
    assert observer.list_sql_chats(actor="user-1", profile_ids=set()).items == []


def test_chat_rejects_other_user_even_system_admin_and_profile_changes(chat: ChatFixture) -> None:
    service, repository, _ = chat
    first = run(service, request())
    with pytest.raises(PermissionError):
        service.start_job(
            request("続き", first.job_id), actor_user_uuid="other", actor_is_system_admin=True
        )
    with pytest.raises(PermissionError):
        service.get_sql_chat(first.job_id, actor="other")
    with pytest.raises(PermissionError):
        service.get_job(first.job_id, actor_user_uuid="other", actor_can_manage=True)
    with pytest.raises(PermissionError):
        service.request_job_cancel(first.job_id, actor_user_uuid="other", actor_can_manage=True)
    repository.save_profile(
        Nl2SqlProfile(id="another", name="別の業務", allowed_tables=["APP.ORDERS"]),
        expected_etag=None,
    )
    with pytest.raises(ValueError, match="別の業務プロファイル"):
        service.start_job(
            request("続き", first.job_id).model_copy(update={"profile_id": "another"}),
            actor_user_uuid="user-1",
        )


def test_chat_rejects_pending_and_non_chat_parents(chat: ChatFixture) -> None:
    service, _, _ = chat
    pending = service.start_job(request(), actor_user_uuid="user-1")
    with pytest.raises(ValueError, match="終わるか"):
        service.start_job(request("続き", pending.job_id), actor_user_uuid="user-1")
    ordinary = run(service, _request())
    with pytest.raises(ValueError, match="チャット"):
        service.start_job(request("続き", ordinary.job_id), actor_user_uuid="user-1")
    assert service.get_sql_chat(ordinary.job_id, actor="user-1") is None
    with pytest.raises(ValueError, match="見つかりません"):
        service.start_job(request("続き", "missing"), actor_user_uuid="user-1")


def test_existing_jobs_still_execute_by_default(
    chat: ChatFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _ = chat
    execute = service.execute_sql
    calls: list[str] = []

    def record(
        sql: str,
        allowed: AllowedObjects,
        row_limit: int | None,
        *,
        analysis: AnalyzeData | None = None,
    ) -> tuple[SafetyReport, str, QueryResults]:
        calls.append(sql)
        return execute(sql, allowed, row_limit, analysis=analysis)

    monkeypatch.setattr(service, "execute_sql", record)
    job = run(service, _request())
    assert job.status == JobStatus.DONE
    assert calls == ["SELECT ID FROM APP.ORDERS"]
    assert job.conversation_id == ""


def test_chat_safety_still_blocks_write_sql(
    chat: ChatFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, client = chat
    client.text = '{"sql":"DELETE FROM APP.ORDERS","explanation":"削除"}'
    monkeypatch.setattr(
        service, "execute_sql", lambda *args, **kwargs: pytest.fail("危険 SQL を実行")
    )
    job = run(service, request())
    assert job.status == JobStatus.ERROR
    assert job.result is not None and not job.result.safety.is_safe


def test_chat_uses_recent_ten_turns_only(chat: ChatFixture) -> None:
    service, _, client = chat
    previous = None
    for number in range(12):
        job = run(service, request(f"条件-{number:03}", previous))
        assert job.status == JobStatus.DONE
        previous = job.job_id
    assert "条件-000" not in client.prompts[-1]
    assert "条件-001" in client.prompts[-1]
    assert "条件-010" in client.prompts[-1]
    assert "条件-011" in client.prompts[-1]


def test_conversation_input_limits() -> None:
    with pytest.raises(ValidationError):
        JobCreateRequest(question="続き", previous_job_id="job", generation_only=False)
    with pytest.raises(ValidationError):
        JobCreateRequest(question="あ" * 10001, generation_only=True)


def test_chat_route_accepts_generation_capability_without_execution(
    chat: ChatFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException
    from pr_system_settings.auth.errors import SecurityApiError
    from test_nl2sql_operation_profile_access import _request as api_request

    from app.features.nl2sql import router
    from app.security.permissions import QUERY_GENERATE_PERMISSION

    service, _, _ = chat
    monkeypatch.setattr(router, "nl2sql_service", service)
    actor_request = api_request({"orders-profile"}, permissions={QUERY_GENERATE_PERMISSION})
    created = router.create_job(request(), actor_request).data
    assert created is not None
    listed = router.list_sql_chats(actor_request).data
    assert listed is not None and listed.items[0].id == created.job_id
    assert router.get_sql_chat(created.job_id, actor_request).data is not None
    with pytest.raises(SecurityApiError):
        router.create_job(_request(), actor_request)
    with pytest.raises(HTTPException) as denied:
        router.get_sql_chat(
            created.job_id, api_request(set(), permissions={QUERY_GENERATE_PERMISSION})
        )
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied_job:
        router.get_job(created.job_id, api_request(set(), permissions={QUERY_GENERATE_PERMISSION}))
    assert denied_job.value.status_code == 403


def test_stale_conversation_cannot_overwrite_later_turn(chat: ChatFixture) -> None:
    service, _, _ = chat
    first = run(service, request())
    run(service, request("追加", first.job_id))
    with pytest.raises(ValueError, match="更新されています"):
        service.start_job(request("古い画面から送信", first.job_id), actor_user_uuid="user-1")


def test_chat_history_has_generation_only_marker(chat: ChatFixture) -> None:
    service, repository, _ = chat
    job = run(service, request())
    assert job.result is not None
    history = repository.get_document("history", job.result.history_id)
    assert history is not None and history["generation_only"] is True


def test_chat_rejects_blank_or_autonomous_generation() -> None:
    from app.features.nl2sql.models import Nl2SqlEngine

    with pytest.raises(ValidationError):
        JobCreateRequest(question=" ", generation_only=True)
    with pytest.raises(ValidationError):
        JobCreateRequest(question="注文", engine=Nl2SqlEngine.SELECT_AI_AGENT, generation_only=True)
