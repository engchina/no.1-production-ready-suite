"""明示対象の更新要求は有限のprotocolを検証し、進行中jobへ合流しない。"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_nl2sql_incremental_state import _incremental_service, _LobPayload, _oracle_repository

from app.features.nl2sql.incremental_store import (
    MemoryIncrementalNl2SqlRepository,
    SchemaRefreshActiveJobConflict,
)
from app.features.nl2sql.models import SchemaRefreshJob, SchemaRefreshMode
from app.features.schema import router as schema_router


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Any:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    service = _incremental_service(repository)
    monkeypatch.setattr(service, "_wake_schema_refresh_job_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(schema_router, "nl2sql_service", service)
    app = FastAPI()
    app.include_router(schema_router.router)
    with TestClient(app) as client:
        yield client, repository, service


@pytest.mark.parametrize("body", [None, {}])
def test_empty_request_preserves_full_manifest_scan(api: Any, body: Any) -> None:
    client, _, _ = api
    response = client.post("/schema/refresh-jobs", json=body)
    assert response.status_code == 202
    assert response.json()["data"]["mode"] == "full"


def test_target_request_normalizes_and_deduplicates_identifiers(api: Any) -> None:
    client, _, _ = api
    target = {"owner": "app", "object_name": '"Object_A"', "object_type": "view"}
    response = client.post(
        "/schema/refresh-jobs", json={"mode": "targeted", "target_objects": [target, target]}
    )
    assert response.status_code == 202
    job = response.json()["data"]
    assert job["mode"] == "targeted" and job["source"] == "manual"
    assert len(job["target_objects"]) == 1
    assert job["target_objects"][0]["owner"] == "APP"
    assert job["target_objects"][0]["object_name"] == "Object_A"


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "targeted"},
        {"mode": "full", "target_objects": [{"owner": "APP", "object_name": "A"}]},
        {"force": True},
        {
            "mode": "targeted",
            "target_objects": [{"owner": "APP", "object_name": "A", "unexpected": True}],
        },
        {"mode": "targeted", "target_objects": [{"owner": "", "object_name": "A"}]},
        {
            "mode": "targeted",
            "target_objects": [
                {"owner": "APP", "object_name": "A"},
                {"owner": "APP", "object_name": "A", "expected_state": "absent"},
            ],
        },
    ],
)
def test_invalid_protocol_never_creates_a_job(api: Any, body: Any) -> None:
    client, repository, _ = api
    assert client.post("/schema/refresh-jobs", json=body).status_code == 422
    assert repository.find_active_refresh_job() is None


@pytest.mark.parametrize("active_mode", [SchemaRefreshMode.FULL, SchemaRefreshMode.TARGETED])
def test_target_request_conflicts_with_active_job_without_mutation(
    api: Any, active_mode: SchemaRefreshMode
) -> None:
    client, repository, _ = api
    active = SchemaRefreshJob(job_id="active", created_at="2026-01-01T00:00:00Z", mode=active_mode)
    repository.submit_refresh_job(active)
    response = client.post(
        "/schema/refresh-jobs",
        json={"mode": "targeted", "target_objects": [{"owner": "APP", "object_name": "A"}]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "schema_refresh_active_job"
    assert repository.find_active_refresh_job() == active


def test_oracle_rejects_active_job_while_holding_atomic_submit_lock() -> None:
    active = SchemaRefreshJob(job_id="active", created_at="2026-01-01T00:00:00Z")
    candidate = SchemaRefreshJob(
        job_id="candidate", created_at="2026-01-02T00:00:00Z", mode=SchemaRefreshMode.TARGETED
    )
    repository, connections = _oracle_repository([[], [(_LobPayload(active.model_dump_json()),)]])
    with pytest.raises(SchemaRefreshActiveJobConflict):
        repository.submit_refresh_job(candidate, allow_coalesce=False)
    statements = [sql for sql, _ in connections[0].executed]
    assert statements[0].startswith("LOCK TABLE")
    assert len(statements) == 2
    assert not any(sql.startswith(("UPDATE", "INSERT")) for sql in statements)
