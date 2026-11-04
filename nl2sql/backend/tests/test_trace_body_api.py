"""全文取得は正本の所有範囲・profile・機密欄の権限を維持する。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from test_nl2sql_db_admin_chat_history import _http, _principal

from app.features.nl2sql import router, trace_bodies
from app.features.nl2sql.models import JobCreateRequest
from app.features.nl2sql.service import Nl2SqlService, StoredJob
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.permissions import permission_for_route


@pytest.mark.parametrize("source", ["jobs", "history", "db_admin_turn"])
def test_full_body_route_permissions_match_corresponding_trace(source: str) -> None:
    path = {
        "jobs": "/nl2sql/jobs/{job_id}/trace",
        "history": "/nl2sql/history/{history_id}/trace",
        "db_admin_turn": (
            "/nl2sql/db-admin-chat/conversations/{conversation_id}/turns/{turn_id}/trace"
        ),
    }[source]
    assert permission_for_route("GET", path) is not None
    assert permission_for_route(
        "GET", path + "/spans/{span_id}/{slot}/body"
    ) == permission_for_route("GET", path)


def test_job_body_owner_and_missing_record_are_checked_before_body_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    job = StoredJob(
        job_id="job-a", request=JobCreateRequest(question="合成の質問"), actor_user_uuid="alice"
    )
    monkeypatch.setattr(service, "_load_job_record", lambda key: job if key == job.job_id else None)
    monkeypatch.setattr(router, "nl2sql_service", service)
    seen: list[Any] = []
    monkeypatch.setattr(router, "read_saved_body", lambda *args, **kw: seen.append((args, kw)))
    for user, identity in [("bob", "job-a"), ("alice", "missing")]:
        with pytest.raises(HTTPException) as exc:
            router.get_job_trace_body(identity, "span-a", "input", _http(_principal(user)))
        assert exc.value.status_code == 404
    assert not seen
    with pytest.raises(HTTPException) as exc:
        router.get_job_trace_body("job-a", "span-a", "input", _http(_principal("alice")))
    assert exc.value.status_code == 404
    assert seen[0][0][:2] == ("jobs", "job-a")
    assert seen[0][1]["allow_restricted"] is False


def test_chat_profile_is_checked_before_full_body_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    job = StoredJob(
        job_id="job-a",
        request=JobCreateRequest(question="合成の質問", profile_id="profile-a", chat=True),
        actor_user_uuid="alice",
    )
    monkeypatch.setattr(service, "_load_job_record", lambda _: job)
    monkeypatch.setattr(router, "nl2sql_service", service)
    seen: list[Any] = []
    monkeypatch.setattr(router, "read_saved_body", lambda *args, **kw: seen.append((args, kw)))

    def denied(*_: Any) -> None:
        raise HTTPException(status_code=403, detail="合成の拒否")

    monkeypatch.setattr(router, "_assert_profile_access", denied)
    with pytest.raises(HTTPException) as exc:
        router.get_job_trace_body("job-a", "span-a", "input", _http(_principal("alice")))
    assert exc.value.status_code == 403 and not seen


def test_history_owner_check_precedes_full_body_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    item = SimpleNamespace(
        actor_user_uuid="alice", conversation_owner_user_uuid="", span_details={}
    )
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(service, "get_history_item", lambda _: item)
    monkeypatch.setattr(router, "nl2sql_service", service)
    monkeypatch.setattr(router, "read_saved_body", lambda *args, **kw: seen.append((args, kw)))
    with pytest.raises(HTTPException) as exc:
        router.history_item_trace_body(_http(_principal("bob")), "history-a", "span-a", "output")
    assert exc.value.status_code == 404 and not seen
    with pytest.raises(HTTPException):
        router.history_item_trace_body(
            _http(_principal("admin", admin=True)), "history-a", "span-a", "output"
        )
    assert seen[0][0][:2] == ("history", "history-a")
    assert seen[0][1]["allow_restricted"] is True


def test_withheld_call_never_opens_full_body_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected() -> Any:
        raise AssertionError("withheld content must not access storage")

    monkeypatch.setattr(
        "app.features.nl2sql.trace_spans.current_trace_span_store",
        lambda: SimpleNamespace(connection_factory=unexpected),
    )
    assert (
        trace_bodies.read_saved_body(
            "jobs",
            "job-a",
            {"span-a": {"call": {"input": {"status": "withheld"}}}},
            span_id="span-a",
            slot="input",
            first_part=0,
            allow_restricted=False,
        )
        is None
    )
