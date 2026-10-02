"""業務 Agent の自動実行（スケジュールと Webhook のトリガー。#784）。"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent import automations
from app.features.agent.automations import (
    AutomationSchedule,
    ScheduleFrequency,
    automation_store,
    next_run_after,
    scheduler_tick,
)
from app.features.agent.control_plane_store import (
    FileItemStore,
    restore_control_plane,
    set_control_plane_store,
)
from app.features.agent.runtime import RunStatus, runtime_repository
from app.security.permissions import ADMIN, MENU_AUTOMATIONS
from app.security.service import set_security_service

# 2026-10-02（金）09:30 JST。
NOW = datetime(2026, 10, 2, 0, 30, tzinfo=UTC)


def _jst(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC) - timedelta(hours=9)


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        (AutomationSchedule(frequency=ScheduleFrequency.DAILY, time="10:00"), _jst(2, 10)),
        (AutomationSchedule(frequency=ScheduleFrequency.DAILY, time="09:00"), _jst(3, 9)),
        # 金曜の 09:30 の後の平日の 09:00 は月曜。
        (AutomationSchedule(frequency=ScheduleFrequency.WEEKDAYS, time="09:00"), _jst(5, 9)),
        # 毎週の水曜（2）と土曜（5）。
        (
            AutomationSchedule(frequency=ScheduleFrequency.WEEKLY, time="08:00", weekdays=[2, 5]),
            _jst(3, 8),
        ),
        (AutomationSchedule(frequency=ScheduleFrequency.HOURLY, minute=15), _jst(2, 10, 15)),
        (AutomationSchedule(frequency=ScheduleFrequency.HOURLY, minute=45), _jst(2, 9, 45)),
    ],
    ids=[
        "daily-today",
        "daily-tomorrow",
        "weekdays-monday",
        "weekly",
        "hourly-next",
        "hourly-same",
    ],
)
def test_next_run_is_computed_in_the_timezone(
    schedule: AutomationSchedule, expected: datetime
) -> None:
    assert next_run_after(schedule, NOW) == expected


def test_invalid_schedules_are_rejected() -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        AutomationSchedule(time="25:00")
    with pytest.raises(ValueError, match="曜日"):
        AutomationSchedule(frequency=ScheduleFrequency.WEEKLY, weekdays=[])
    with pytest.raises(ValueError, match="タイムゾーン"):
        AutomationSchedule(timezone="Mars/Olympus")


@pytest.fixture
def store(monkeypatch: MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "items.json"
    set_control_plane_store(FileItemStore(path))
    automation_store.clear()
    # API の Run は作るだけで、このテストでは実行しない（実行は組み込み Runtime のテスト）。
    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda _run: None)
    try:
        yield path
    finally:
        automation_store.clear()
        set_control_plane_store(None)
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id
                for run_id, run in repository._runs.items()
                if run.metadata.get("automation_id")
            ]:
                repository._runs.pop(run_id)


BODY: dict[str, Any] = {
    "agent_id": "default",
    "name": "毎朝の売上の要約",
    "goal": "昨日の売上を要約してください。",
    "trigger": "schedule",
    "schedule": {"frequency": "daily", "time": "09:00", "timezone": "Asia/Tokyo"},
}


def _due(automation_id: str) -> None:
    """次回を過去にして、すぐに実行される状態にする。"""

    def mutate(item: Any) -> None:
        item.next_run_at = datetime.now(UTC) - timedelta(minutes=1)

    automation_store.change(automation_id, mutate)


def test_scheduled_automation_creates_runs_and_skips_while_the_previous_runs(store: Path) -> None:
    del store
    created = client.post("/api/automations", json=BODY)
    assert created.status_code == 200, created.text
    automation = created.json()["data"]
    assert automation["next_run_at"] is not None
    assert "webhook_token_hash" not in automation
    _due(automation["id"])

    [fired] = scheduler_tick()

    assert fired.result == "created"
    run = runtime_repository.get_run(str(fired.run_id))
    assert run.goal == "昨日の売上を要約してください。"
    assert run.metadata["automation_id"] == automation["id"]
    assert run.metadata["automation_trigger"] == "schedule"
    current = automation_store.get(automation["id"])
    assert current.last_result == "created" and current.last_run_id == run.id
    assert current.next_run_at is not None and current.next_run_at > datetime.now(UTC)

    # 前回の Run（queued のまま）が終わっていなければ、次の回は飛ばす。
    _due(automation["id"])
    [skipped] = scheduler_tick()
    assert (skipped.result, skipped.run_id) == ("skipped", None)
    # 終われば次の回は実行する。
    runtime_repository.cancel_run(run.id)
    _due(automation["id"])
    [again] = scheduler_tick()
    assert again.result == "created"

    detail = client.get(f"/api/automations/{automation['id']}").json()["data"]
    assert [item["run_id"] for item in detail["runs"]] == [again.run_id, run.id]
    assert detail["runs"][1]["status"] == RunStatus.CANCELLED


def test_disabled_and_webhook_automations_are_not_scheduled(store: Path) -> None:
    del store
    disabled = client.post("/api/automations", json={**BODY, "enabled": False}).json()["data"]
    assert disabled["next_run_at"] is None
    webhook = client.post(
        "/api/automations", json={**BODY, "trigger": "webhook", "schedule": None}
    ).json()["data"]
    assert (webhook["schedule"], webhook["next_run_at"]) == (None, None)
    assert scheduler_tick() == []


def test_owner_that_cannot_run_the_agent_does_not_start(
    store: Path, monkeypatch: MonkeyPatch
) -> None:
    del store
    automation = client.post("/api/automations", json=BODY).json()["data"]
    monkeypatch.setattr(automations, "owner_check", lambda _item: "利用者が無効です。")
    fired = client.post(f"/api/automations/{automation['id']}/run").json()["data"]
    assert (fired["result"], fired["run_id"], fired["message"]) == (
        "failed_to_start",
        None,
        "利用者が無効です。",
    )
    assert automation_store.get(automation["id"]).last_result == "failed_to_start"


def test_manual_run(store: Path) -> None:
    del store
    automation = client.post("/api/automations", json=BODY).json()["data"]
    fired = client.post(f"/api/automations/{automation['id']}/run")
    assert fired.status_code == 200, fired.text
    run = runtime_repository.get_run(fired.json()["data"]["run_id"])
    assert run.metadata["automation_trigger"] == "manual"


def test_webhook_runs_the_agent_with_the_received_data(store: Path) -> None:
    automation = client.post(
        "/api/automations", json={**BODY, "trigger": "webhook", "schedule": None}
    ).json()["data"]
    url = f"/api/hooks/{automation['id']}"
    # 秘密を発行する前・誤った秘密は 401（存在を漏らさない）。
    assert client.post(url, json={"order": 1}).status_code == 401
    issued = client.post(f"/api/automations/{automation['id']}/webhook-token")
    assert issued.status_code == 200, issued.text
    token = issued.json()["data"]["token"]
    assert token.startswith("prwh_")
    assert issued.json()["data"]["automation"]["webhook_token_prefix"] == token[:9]
    headers = {"authorization": f"Bearer {token}"}
    assert (
        client.post(url, json={}, headers={"authorization": "Bearer prwh_wrong"}).status_code == 401
    )
    assert client.post("/api/hooks/auto_missing", json={}, headers=headers).status_code == 401

    received = client.post(url, json={"order_id": "A-100", "amount": 30000}, headers=headers)
    assert received.status_code == 202, received.text
    run = runtime_repository.get_run(received.json()["data"]["run_id"])
    assert run.metadata["automation_trigger"] == "webhook"
    assert '"order_id": "A-100"' in run.goal
    assert run.goal.startswith("昨日の売上を要約してください。")

    # 秘密は保存先に hash だけ。
    stored = json.loads(store.read_text())["automation"][automation["id"]]
    assert stored["webhook_token_hash"] and token not in json.dumps(stored)

    runtime_repository.cancel_run(run.id)
    too_big = client.post(url, content=b'{"x": "' + b"a" * 17_000 + b'"}', headers=headers)
    assert too_big.status_code == 413
    broken = client.post(url, content=b"{broken", headers=headers)
    assert broken.status_code == 422
    client.put(
        f"/api/automations/{automation['id']}",
        json={**BODY, "trigger": "webhook", "schedule": None, "enabled": False},
    )
    assert client.post(url, json={}, headers=headers).status_code == 409


def test_automations_are_restored_without_catching_up_missed_runs(store: Path) -> None:
    del store
    automation = client.post("/api/automations", json=BODY).json()["data"]
    _due(automation["id"])

    automation_store.clear()
    restored = restore_control_plane()

    assert restored["automation"] == 1
    current = automation_store.get(automation["id"])
    # 停止中に過ぎた回は実行せず、次回へ送る。
    assert current.next_run_at is not None and current.next_run_at > datetime.now(UTC)
    assert scheduler_tick() == []


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_automation_permissions(auth: ProductionAuth, store: Path) -> None:
    del store
    auth.user_with_permissions("viewer-784", [MENU_AUTOMATIONS], agent_ids=["default"])
    viewer = login("viewer-784")
    assert client.post("/api/automations", json=BODY, headers=viewer).status_code == 403
    admin = auth.user_with_permissions("admin-784", [ADMIN])
    created = client.post("/api/automations", json=BODY, headers=login("admin-784"))
    assert created.status_code == 200, created.text
    automation = created.json()["data"]
    assert automation["run_as_user_uuid"] == admin.user_uuid
    assert [
        item["id"]
        for item in client.get("/api/automations", headers=viewer).json()["data"]["automations"]
    ] == [automation["id"]]
    assert (
        client.post(f"/api/automations/{automation['id']}/run", headers=viewer).status_code == 403
    )
    # 利用できない業務 Agent の自動実行は見えない。
    auth.user_with_permissions("other-784", [MENU_AUTOMATIONS], agent_ids=["sales"])
    other = login("other-784")
    assert client.get("/api/automations", headers=other).json()["data"]["automations"] == []
    assert client.get(f"/api/automations/{automation['id']}", headers=other).status_code == 404


def test_unpublished_agents_cannot_be_automated(store: Path) -> None:
    """自動実行は利用者の Run なので、公開した版の無い業務 Agent は選べない（#792）。"""
    del store
    draft_id = "automation-792-draft"
    created = client.post(
        "/api/agents", json={"id": draft_id, "name": "下書きの Agent", "instructions": "答える。"}
    )
    assert created.status_code == 200, created.text
    try:
        refused = client.post("/api/automations", json={**BODY, "agent_id": draft_id})
        assert refused.status_code == 422
        assert "公開していない業務 Agent" in refused.text
        automation = client.post("/api/automations", json=BODY).json()["data"]
        moved = client.put(
            f"/api/automations/{automation['id']}", json={**BODY, "agent_id": draft_id}
        )
        assert moved.status_code == 422
    finally:
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(draft_id)
