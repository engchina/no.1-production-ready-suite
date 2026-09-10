"""受付の幂等性・再認可・回復・Oracle の実件数を境界から検証する。"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.features.nl2sql import synthetic_service as module
from app.features.nl2sql.synthetic_models import SyntheticRun, SyntheticRunRequest, SyntheticTarget
from app.features.nl2sql.synthetic_oracle import inspect_operation
from app.features.nl2sql.synthetic_service import SyntheticService
from app.features.nl2sql.synthetic_store import SyntheticConflict, SyntheticStore
from app.security.domain import Principal
from app.settings import Settings


def actor(name: Any = "owner") -> Principal:
    return Principal(
        name, name, name, "ACTIVE", False, [], {"menu.data_management"}, [], set(), "s", ""
    )


def run(**values: Any) -> SyntheticRun:
    return SyntheticRun(
        **{
            "run_id": "one",
            "actor_id": "owner",
            "context_id": "db",
            "idempotency_key": "key-one",
            "request_hash": "hash",
            "targets": [SyntheticTarget(table_name="APP.T", requested_rows=2)],
            **values,
        }
    )


def test_idempotency_lock_cas_and_context_isolation() -> None:
    store: Any = SyntheticStore()
    first = store.create(run())
    assert store.create(run(run_id="duplicate")).run_id == "one"
    with pytest.raises(SyntheticConflict):
        store.create(run(run_id="two", idempotency_key="key-two"))
    with pytest.raises(SyntheticConflict):
        store.create(run(request_hash="changed"))
    assert store.create(run(run_id="other-db", context_id="db2")).run_id == "other-db"
    first.status = "unknown"
    assert store.save(first)
    assert not store.save(first)  # stale worker cannot overwrite a newer state
    with pytest.raises(SyntheticConflict):
        store.create(run(run_id="two", idempotency_key="key-two"))
    latest = store.get("one")
    latest.status = "no_data"
    assert store.save(latest)
    assert store.create(run(run_id="two", idempotency_key="key-two")).run_id == "two"
    assert store.get("one").targets[0].loaded_rows is None


def test_idempotency_and_locks_survive_more_than_100_records() -> None:
    store: Any = SyntheticStore()
    store.create(run())
    for i in range(101):
        store.create(
            run(
                run_id=f"r{i}",
                idempotency_key=f"k{i}",
                targets=[SyntheticTarget(table_name=f"APP.T{i}", requested_rows=1)],
            )
        )
    assert store.by_key("owner", "db", "key-one").run_id == "one"
    with pytest.raises(SyntheticConflict):
        store.create(run(run_id="duplicate", idempotency_key="new"))


class OracleFixture:
    def __init__(self, operations: Any, chunks: Any) -> None:
        self.operations, self.chunks = operations, chunks
        self.calls: list[tuple[str, Any]] = []

    @contextmanager
    def connection(self) -> Iterator[Any]:
        yield self

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        yield self

    def execute(self, sql: Any, params: Any = None) -> Any:
        self.calls.append((sql, params))

    def fetchall(self) -> Any:
        return self.operations if len(self.calls) % 2 else self.chunks


@pytest.mark.parametrize(
    "op,chunks,expected,loaded",
    [
        ("COMPLETED", [("COMPLETED", 1), ("COMPLETED", 1)], "completed", 2),
        ("COMPLETED", [("COMPLETED", 0)], "no_data", 0),
        ("FAILED", [("FAILED", 0)], "failed", 0),
        ("FAILED", [("COMPLETED", 1), ("FAILED", 0)], "partial", 1),
        ("FAILED", [("COMPLETED", 2)], "partial", 2),
        ("COMPLETED", [("COMPLETED", None)], "unknown", None),
        ("RUNNING", [("COMPLETED", 2)], "running", 2),
    ],
)
def test_oracle_counts_never_use_requested_or_current_table_total(
    op: Any, chunks: Any, expected: Any, loaded: Any
) -> None:
    adapter: Any = OracleFixture(
        [(42, op, "SYNTHETIC_DATA$42_STATUS")],
        [
            (
                '"APP"."T"',
                status,
                rows,
                1 if status == "FAILED" else None,
                "失敗" if status == "FAILED" else None,
            )
            for status, rows in chunks
        ],
    )
    result = inspect_operation(
        adapter,
        run(
            status="running",
            session={
                "sid": 7,
                "serial": 9,
                "username": "APP",
                "since": datetime.now(UTC).isoformat(),
            },
        ),
    )
    assert result.status == expected
    assert result.targets[0].loaded_rows == loaded
    assert result.operation_ids == [42]
    sql, params = adapter.calls[0]
    assert "SERIAL#=:serial" in sql and params["sid"] == 7 and params["serial"] == 9
    assert "MAX(" not in sql


def test_missing_or_ambiguous_operation_cannot_be_completed() -> None:
    for operations in [
        [],
        [(1, "COMPLETED", "SYNTHETIC_DATA$1_STATUS"), (2, "COMPLETED", "SYNTHETIC_DATA$2_STATUS")],
    ]:
        result = inspect_operation(
            cast(Any, OracleFixture(operations, [])),
            run(
                status="unknown",
                session={
                    "sid": 1,
                    "serial": 2,
                    "username": "APP",
                    "since": datetime.now(UTC).isoformat(),
                },
            ),
        )
        assert result.status == "unknown"
        assert result.targets[0].loaded_rows is None


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> SyntheticService:
    settings = Settings(
        _env_file=None,
        nl2sql_runtime_mode="oracle",
        nl2sql_persistence_mode="oracle",
        oracle_user="APP",
    )
    svc = SyntheticService(settings, store=SyntheticStore(), resolve_actor=lambda _: actor())
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(svc, "preflight", Mock(return_value={"APP.T": 1}))
    monkeypatch.setattr(svc.adapter, "generate_synthetic_data", Mock())
    return svc


def request(**values: Any) -> SyntheticRunRequest:
    return SyntheticRunRequest(
        **{
            "table_name": "APP.T",
            "profile_name": "P",
            "confirmation": "APP.T",
            "idempotency_key": "1234567890123456",
            **values,
        }
    )


def test_create_validates_confirmation_and_actor_even_on_idempotent_replay(service: Any) -> None:
    with pytest.raises(HTTPException) as error:
        service.create(request(confirmation=""), actor())
    assert error.value.status_code == 400
    created = service.create(request(), actor())
    assert created.status == "pending" and created.targets[0].loaded_rows is None
    assert service.create(request(), actor()).run_id == created.run_id
    service.adapter.generate_synthetic_data.assert_not_called()
    with pytest.raises(HTTPException) as error:
        service.create(request(), replace(actor(), permissions=set()))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        service.get(created.run_id, actor("other"))
    assert error.value.status_code == 404
    assert "request" not in created.public() and "session" not in created.public()


@pytest.mark.parametrize("problem", ["revoked", "recreated", "context"])
def test_worker_rechecks_authorization_and_identity_before_any_write(
    service: Any, monkeypatch: pytest.MonkeyPatch, problem: Any
) -> None:
    created = service.create(request(), actor())
    created.status = "running"
    service.store.save(created)
    if problem == "revoked":
        service.resolve_actor = lambda _: replace(actor(), permissions=set())
    elif problem == "recreated":
        service.preflight.return_value = {"APP.T": 99}
    else:
        monkeypatch.setattr(
            module, "get_settings", lambda: Settings(_env_file=None, oracle_dsn="different")
        )
    service.execute(created.run_id)
    result = service.store.get(created.run_id)
    assert result.status == "failed" and result.targets[0].loaded_rows == 0
    service.adapter.generate_synthetic_data.assert_not_called()


def test_persist_session_before_write_and_recover_timeout_without_replay(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = service.create(request(), actor())
    created.status = "running"
    service.store.save(created)
    session = {"sid": 7, "serial": 8, "username": "APP", "since": datetime.now(UTC).isoformat()}
    monkeypatch.setattr(module, "capture_session", lambda _: session)

    def write(**kwargs: Any) -> Any:
        kwargs["on_connection"](None)
        assert service.store.get(created.run_id).session == session
        raise RuntimeError("応答が途切れました")

    service.adapter.generate_synthetic_data.side_effect = write
    service.execute(created.run_id)
    assert service.store.get(created.run_id).status == "unknown", service.store.get(
        created.run_id
    ).message
    recovered = SyntheticService(service.settings, store=service.store, adapter=service.adapter)
    monkeypatch.setattr(module, "inspect_operation", lambda _, value: value)
    recovered.tick()
    assert service.adapter.generate_synthetic_data.call_count == 1
    assert service.store.get(created.run_id).targets[0].loaded_rows is None


def test_failed_session_persistence_prevents_write(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = service.create(request(), actor())
    created.status = "running"
    service.store.save(created)
    monkeypatch.setattr(module, "capture_session", Mock(side_effect=RuntimeError("監視権限なし")))
    reached_write = []

    def write(**kwargs: Any) -> Any:
        kwargs["on_connection"](None)
        reached_write.append(True)

    service.adapter.generate_synthetic_data.side_effect = write
    service.execute(created.run_id)
    assert reached_write == []
    assert service.store.get(created.run_id).status == "failed"


@pytest.mark.asyncio
async def test_api_returns_202_without_work_and_scopes_reads(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from fastapi import FastAPI, Request

    from app.features.nl2sql import synthetic_router

    app = FastAPI()
    current = actor()

    @app.middleware("http")
    async def authentication(req: Request, next_handler: Any) -> Any:
        req.state.principal = current
        return await next_handler(req)

    app.include_router(synthetic_router.router, prefix="/api/nl2sql")
    monkeypatch.setattr(synthetic_router, "get_synthetic_service", lambda: service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/nl2sql/synthetic-data/runs", json=request().model_dump())
        assert response.status_code == 202
        body = response.json()["data"]
        assert body["status"] == "pending" and body["targets"][0]["loaded_rows"] is None
        assert "session" not in body and "request" not in body
        replay = await client.post("/api/nl2sql/synthetic-data/runs", json=request().model_dump())
        assert replay.json()["data"]["run_id"] == body["run_id"]
        path = "/api/nl2sql/synthetic-data/runs/" + body["run_id"]
        assert (await client.get(path)).status_code == 200
        assert (await client.get(path + "/results?table_name=APP.OTHER")).status_code == 400
        assert (await client.get(path + "/results?table_name=APP.T&limit=0")).status_code == 422
        current = actor("other")
        assert (await client.get(path)).status_code == 404
        assert (await client.get("/api/nl2sql/synthetic-data/runs")).json()["data"] == []
    service.adapter.generate_synthetic_data.assert_not_called()


def test_store_decodes_oracle_json_dict_text_and_lob() -> None:
    value = run()
    for payload in (
        value.model_dump(),
        value.model_dump_json(),
        Mock(read=lambda: value.model_dump_json()),
    ):
        assert SyntheticStore.decode(payload) == value


def test_worker_return_is_only_verifying_and_claim_is_not_replayed(
    service: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    created = service.create(request(), actor())
    entered, release = threading.Event(), threading.Event()
    session = {"sid": 7, "serial": 8, "username": "APP", "since": datetime.now(UTC).isoformat()}
    monkeypatch.setattr(module, "capture_session", lambda _: session)
    monkeypatch.setattr(module, "inspect_operation", lambda _, value: value)

    def write(**kwargs: Any) -> Any:
        kwargs["on_connection"](None)
        entered.set()
        assert release.wait(3)

    service.adapter.generate_synthetic_data.side_effect = write
    service.tick()
    try:
        assert entered.wait(3)
        other_worker = SyntheticService(
            service.settings, store=service.store, adapter=service.adapter
        )
        other_worker.tick()
        assert service.adapter.generate_synthetic_data.call_count == 1
    finally:
        release.set()
        for thread in service._threads.values():
            thread.join(3)
    result = service.store.get(created.run_id)
    assert result.status == "verifying" and result.targets[0].loaded_rows is None


def test_worker_debug_identity_requires_current_explicit_debug_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.security import service as security
    from app.security.dependencies import LOCAL_DEBUG_USER_UUID

    settings = Settings(_env_file=None)
    resolver = security.SecurityService(Mock(), settings)
    monkeypatch.setattr(security, "get_settings", lambda: Mock(local_debug_enabled=True))
    assert resolver.principal_for_worker(LOCAL_DEBUG_USER_UUID).is_system_admin
    monkeypatch.setattr(security, "get_settings", lambda: Mock(local_debug_enabled=False))
    with pytest.raises(security.SecurityApiError):
        resolver.principal_for_worker(LOCAL_DEBUG_USER_UUID)
