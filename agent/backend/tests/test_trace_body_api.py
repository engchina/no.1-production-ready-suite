"""全文読取もRun・Agentの範囲と現在の根拠権限を通す。"""

from collections.abc import Iterator
from typing import Any

import pytest
from pr_backend_core.chat_progress import CallContent, CallDetails, ChatProgressStepDetails
from pr_backend_core.observability.trace_bodies import BodyPage, prepare_body
from security_support import (
    ProductionAuth,
    client,
    enable_production_auth,
    login,
    login_configured_admin,
)

from app.features.agent import router
from app.features.agent.runtime import RunCreateRequest, RunState, runtime_repository
from app.security.permissions import permission_for_route
from app.security.service import set_security_service


@pytest.fixture
def auth(monkeypatch: pytest.MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_body_route_requires_readable_run_before_accessing_body(
    monkeypatch: pytest.MonkeyPatch, auth: ProductionAuth
) -> None:
    owner = auth.user_with_permissions("body-owner", ["agent.runs.operate"], agent_ids=["default"])
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成の質問", agent_id="default"),
        created_by_user_uuid=owner.user_uuid,
    )
    body = prepare_body("合成の本文末尾。", part_characters=4)
    calls: list[tuple[str | None, str, dict[str, Any]]] = []

    def read(namespace: str | None, run_id: str, _details: Any, **kwargs: Any) -> BodyPage:
        calls.append((namespace, run_id, kwargs))
        return BodyPage(body.reference, "complete", None, (list(body.parts())[1],), 2)

    monkeypatch.setattr(router, "read_saved_run_body", read)
    path = f"/api/runs/{run.id}/trace/spans/span-a/input/body"
    response = client.get(path + "?first_part=1", headers=login("body-owner"))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["next_part"] == 2
    assert calls == [
        (
            None,
            run.id,
            {"span_id": "span-a", "slot": "input", "first_part": 1, "allow_restricted": False},
        )
    ]
    auth.user_with_permissions("body-peer", ["agent.runs.operate"], agent_ids=["default"])
    assert client.get(path, headers=login("body-peer")).status_code == 404
    assert len(calls) == 1
    auth.user_with_permissions("body-none", [], agent_ids=["default"])
    assert client.get(path, headers=login("body-none")).status_code == 403
    assert client.get(path).status_code == 401
    assert len(calls) == 1
    admin = client.get(path, headers=login_configured_admin())
    assert admin.status_code == 200 and calls[-1][2]["allow_restricted"] is True


def test_body_route_uses_evidence_checked_copy_and_never_reads_withheld_payload(
    monkeypatch: pytest.MonkeyPatch, auth: ProductionAuth
) -> None:
    owner = auth.user_with_permissions("body-owner", ["agent.runs.operate"], agent_ids=["default"])
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成の質問", agent_id="default"),
        created_by_user_uuid=owner.user_uuid,
    )
    body = prepare_body("synthetic full body", part_characters=4)
    raw = run.model_copy(
        update={
            "span_details": {
                "span-a": ChatProgressStepDetails(
                    call=CallDetails(
                        input=CallContent(status="recorded", body_reference=body.reference)
                    )
                )
            }
        }
    )
    stored = runtime_repository.get_run
    monkeypatch.setattr(
        runtime_repository, "get_run", lambda run_id: raw if run_id == run.id else stored(run_id)
    )
    checks: list[str] = []

    async def checked(_request: Any, actual: RunState) -> tuple[RunState, int]:
        checks.append(actual.id)
        return actual.model_copy(
            update={
                "span_details": {
                    "span-a": ChatProgressStepDetails(
                        call=CallDetails.model_validate({"input": {"status": "withheld"}})
                    )
                }
            }
        ), 1

    monkeypatch.setattr(router, "_evidence_checked_run", checked)
    path = f"/api/runs/{run.id}/trace/spans/span-a/input/body"
    response = client.get(path, headers=login("body-owner"))
    assert response.status_code == 404 and checks == [run.id]
    assert not stored(run.id).span_details
    assert raw.span_details["span-a"].call is not None
    assert raw.span_details["span-a"].call.input.status == "recorded"


def test_body_route_has_same_permissions_as_trace_snapshot() -> None:
    assert permission_for_route("GET", "/runs/{run_id}/trace/spans/{span_id}/{slot}/body") == (
        permission_for_route("GET", "/runs/{run_id}/trace")
    )
