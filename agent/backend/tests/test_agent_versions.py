"""業務 Agent の下書き・公開・版の履歴・前の版に戻す（#770）。"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from checkpoint_support import load_state
from pytest import MonkeyPatch
from security_support import (
    ProductionAuth,
    client,
    enable_production_auth,
    login,
)

from app.features.agent.confirmation import ConfirmationRequest, item_from_arguments
from app.features.agent.runtime import (
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeRepository,
    AgentRuntimeSnapshot,
    AgentVersion,
    ApprovalDecisionRequest,
    RunCreateRequest,
    RunState,
    RunStatus,
    _prune_agent_versions,
    agent_for_run,
    runtime_repository,
)
from app.features.agent.tools import ToolCall
from app.security.permissions import MENU_RUNS, RUNS_OPERATE
from app.security.service import set_security_service

AGENT_ID = "agent-versions-770"


@pytest.fixture
def scheduled(monkeypatch: MonkeyPatch) -> Iterator[list[str]]:
    """API の Run は作るだけで実行しない（実行に使う Agent の内容は repository で確かめる）。"""
    runs: list[str] = []
    monkeypatch.setattr(
        "app.features.agent.router._schedule_builtin_run", lambda run: runs.append(run.id)
    )
    try:
        yield runs
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                repository._runs.pop(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)


def _instructions_used(run_id: str) -> str:
    started = runtime_repository.begin_builtin_run(run_id)
    assert started is not None
    return started[1].instructions


def test_agents_created_by_api_start_as_unpublished_draft(scheduled: list[str]) -> None:
    created = client.post(
        "/api/agents",
        json={
            "id": AGENT_ID,
            "name": "経理の Agent",
            "instructions": "v1 の指示",
            # 版の項目は送っても使わない。
            "versions": [{"version": 9, "name": "偽"}],
            "published_version": 9,
        },
    )
    assert created.status_code == 200, created.text
    agent = created.json()["data"]
    assert (agent["versions"], agent["published_version"]) == ([], None)
    assert agent["unpublished_changes"] is True

    # 公開していない Agent の Run は断る。下書きでは試せる（local は管理者）。
    refused = client.post("/api/runs", json={"goal": "質問", "agent_id": AGENT_ID})
    assert refused.status_code == 409
    assert "公開していない業務 Agent" in refused.json()["error_messages"][0]
    draft = client.post("/api/runs", json={"goal": "試す", "agent_id": AGENT_ID, "draft": True})
    assert draft.status_code == 200, draft.text
    assert draft.json()["data"]["metadata"]["agent_version"] == "draft"


def test_runs_use_published_version_until_republished_and_can_roll_back(
    scheduled: list[str],
) -> None:
    client.post("/api/agents", json={"id": AGENT_ID, "name": "経理", "instructions": "v1 の指示"})
    published = client.post(f"/api/agents/{AGENT_ID}/publish", json={"note": "最初の公開"})
    assert published.status_code == 200, published.text
    v1 = published.json()["data"]
    assert v1["published_version"] == 1
    assert v1["versions"][0]["note"] == "最初の公開"
    assert v1["versions"][0]["published_by"] == "local"
    assert v1["unpublished_changes"] is False

    # 下書きを変えても、利用者の Run は公開中の v1 の内容で実行する。
    patched = client.patch(f"/api/agents/{AGENT_ID}", json={"instructions": "v2 の指示（作業中）"})
    assert patched.json()["data"]["unpublished_changes"] is True
    run = client.post("/api/runs", json={"goal": "質問", "agent_id": AGENT_ID}).json()["data"]
    assert run["metadata"]["agent_version"] == 1
    assert _instructions_used(run["id"]) == "v1 の指示"
    draft_run = client.post(
        "/api/runs", json={"goal": "試す", "agent_id": AGENT_ID, "draft": True}
    ).json()["data"]
    assert _instructions_used(draft_run["id"]) == "v2 の指示（作業中）"

    v2 = client.post(f"/api/agents/{AGENT_ID}/publish", json={}).json()["data"]
    assert v2["published_version"] == 2
    run2 = client.post("/api/runs", json={"goal": "質問", "agent_id": AGENT_ID}).json()["data"]
    assert _instructions_used(run2["id"]) == "v2 の指示（作業中）"

    # 前の版に戻すと、その版を公開し直し、下書きもその内容になる。
    restored = client.post(f"/api/agents/{AGENT_ID}/versions/1/restore")
    assert restored.status_code == 200, restored.text
    data = restored.json()["data"]
    assert (data["published_version"], data["instructions"]) == (1, "v1 の指示")
    assert [item["version"] for item in data["versions"]] == [1, 2]
    run3 = client.post("/api/runs", json={"goal": "質問", "agent_id": AGENT_ID}).json()["data"]
    assert _instructions_used(run3["id"]) == "v1 の指示"
    assert client.post(f"/api/agents/{AGENT_ID}/versions/9/restore").status_code == 404


def test_agents_without_versions_are_published_as_v1() -> None:
    # #770 より前の Agent（版の項目が無い）は、読み込み時に現在の内容を v1 として公開する。
    legacy = AgentProfile(id="legacy-770", name="古い Agent", instructions="以前の指示")
    snapshot = AgentRuntimeSnapshot(agents=[legacy])
    repository = AgentRuntimeRepository()
    load_state(repository, snapshot)

    agent = next(item for item in repository.list_agents() if item.id == "legacy-770")
    assert agent.published_version == 1
    assert agent.versions[0].instructions == "以前の指示"
    assert agent.unpublished_changes is False
    default = next(item for item in repository.list_agents() if item.id == "default")
    assert default.published_version == 1
    run = repository.create_builtin_run(RunCreateRequest(goal="質問", agent_id="legacy-770"))
    assert run.metadata["agent_version"] == 1


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_draft_runs_need_agent_admin(auth: ProductionAuth, scheduled: list[str]) -> None:
    auth.user_with_permissions("operator-770", [MENU_RUNS, RUNS_OPERATE], agent_ids=["default"])

    response = client.post(
        "/api/runs",
        json={"goal": "下書きで試す", "agent_id": "default", "draft": True},
        headers=login("operator-770"),
    )

    assert response.status_code == 403
    assert "Agent 管理" in response.json()["error_messages"][0]


def test_replay_refuses_unavailable_agent_with_reason(scheduled: list[str]) -> None:
    """再実行は利用者の Run。公開していない・無効の Agent は 500 にせず理由を返す（#911）。"""
    client.post("/api/agents", json={"id": AGENT_ID, "name": "経理", "instructions": "v1 の指示"})
    draft = client.post("/api/runs", json={"goal": "試す", "agent_id": AGENT_ID, "draft": True})
    assert draft.status_code == 200, draft.text
    source_id = draft.json()["data"]["id"]

    unpublished = client.post(f"/api/runs/{source_id}/replay")
    assert unpublished.status_code == 409, unpublished.text
    assert "公開していない業務 Agent" in unpublished.json()["error_messages"][0]

    assert client.post(f"/api/agents/{AGENT_ID}/publish", json={"note": "公開"}).status_code == 200
    assert client.patch(f"/api/agents/{AGENT_ID}", json={"enabled": False}).status_code == 200
    disabled = client.post(f"/api/runs/{source_id}/replay")
    assert disabled.status_code == 409, disabled.text
    assert disabled.json()["error_messages"][0] == "この業務 Agent は実行できない状態です。"
    # 断った再実行は Run を作らない。
    assert scheduled == [source_id]


def _publish() -> dict[str, Any]:
    response = client.post(f"/api/agents/{AGENT_ID}/publish", json={})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _publish_with_instructions(instructions: str) -> dict[str, Any]:
    patched = client.patch(f"/api/agents/{AGENT_ID}", json={"instructions": instructions})
    assert patched.status_code == 200, patched.text
    return _publish()


def _versions(data: dict[str, Any]) -> list[int]:
    return [item["version"] for item in data["versions"]]


def test_publishing_keeps_only_latest_versions(scheduled: list[str]) -> None:
    """版の履歴は直近 5 版（AGENT_MAX_PUBLISHED_VERSIONS）。古い版を消し、番号は振り直さない。

    #2356。
    """
    client.post("/api/agents", json={"id": AGENT_ID, "name": "Agent A", "instructions": "v1"})
    data = _publish()
    for index in range(2, 6):
        data = _publish_with_instructions(f"v{index}")
    assert _versions(data) == [1, 2, 3, 4, 5]

    data = _publish_with_instructions("v6")
    assert data["published_version"] == 6
    assert _versions(data) == [2, 3, 4, 5, 6]
    data = _publish_with_instructions("v7")
    assert _versions(data) == [3, 4, 5, 6, 7]

    listed = client.get("/api/agents").json()["data"]
    assert listed["max_published_versions"] == 5
    stored = next(item for item in listed["agents"] if item["id"] == AGENT_ID)
    assert _versions(stored) == [3, 4, 5, 6, 7]


def test_version_limit_comes_from_settings(scheduled: list[str], monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.features.agent.runtime.get_settings",
        lambda: SimpleNamespace(agent_max_published_versions=2),
    )
    client.post("/api/agents", json={"id": AGENT_ID, "name": "Agent A", "instructions": "v1"})
    _publish()
    _publish_with_instructions("v2")
    assert _versions(_publish_with_instructions("v3")) == [2, 3]


def test_restoring_old_version_does_not_prune_and_next_publish_counts_from_latest(
    scheduled: list[str],
) -> None:
    """前の版に戻すのは版を足さない（消さない）。次の公開で直近の 5 版に整理する（#2356）。"""
    client.post("/api/agents", json={"id": AGENT_ID, "name": "Agent A", "instructions": "v1"})
    _publish()
    for index in range(2, 7):
        _publish_with_instructions(f"v{index}")
    restored = client.post(f"/api/agents/{AGENT_ID}/versions/2/restore").json()["data"]
    assert restored["published_version"] == 2
    assert _versions(restored) == [2, 3, 4, 5, 6]

    data = _publish_with_instructions("v7")
    assert data["published_version"] == 7
    assert _versions(data) == [3, 4, 5, 6, 7]


def test_prune_keeps_old_published_version() -> None:
    """公開中の版は古くても消さず、公開中 + 直近の版で計 5 版にする（#2356）。"""
    agent = AgentProfile(
        id="agent-prune-2356",
        name="Agent A",
        versioned=True,
        versions=[AgentVersion(version=number, name="Agent A") for number in range(1, 8)],
        published_version=2,
    )
    _prune_agent_versions(agent)
    assert [item.version for item in agent.versions] == [2, 4, 5, 6, 7]

    # スキルの更新の取り込みは、前の公開中の版も残す（「取り消す」で戻す。#1867）。
    agent = AgentProfile(
        id="agent-prune-2356",
        name="Agent A",
        versioned=True,
        versions=[AgentVersion(version=number, name="Agent A") for number in range(1, 9)],
        published_version=8,
    )
    _prune_agent_versions(agent, keep=(2,))
    assert [item.version for item in agent.versions] == [2, 5, 6, 7, 8]


def test_runs_of_pruned_versions_stay_readable_and_never_use_draft(
    scheduled: list[str],
) -> None:
    """消した版の Run は残って開ける。版が無い Run は下書きでなく公開中の版で動く（#2356）。"""
    client.post("/api/agents", json={"id": AGENT_ID, "name": "Agent A", "instructions": "v1"})
    _publish()
    run = client.post("/api/runs", json={"goal": "質問", "agent_id": AGENT_ID}).json()["data"]
    assert run["metadata"]["agent_version"] == 1
    # 終わった Run の版は上限で消える。
    repository: Any = runtime_repository
    repository._runs[run["id"]].status = RunStatus.COMPLETED  # noqa: SLF001 - 状態だけを作る
    for index in range(2, 7):
        _publish_with_instructions(f"v{index}")
    client.patch(f"/api/agents/{AGENT_ID}", json={"instructions": "下書き（未公開）"})
    agents = client.get("/api/agents").json()["data"]["agents"]
    assert 1 not in _versions(next(item for item in agents if item["id"] == AGENT_ID))
    detail = client.get(f"/api/runs/{run['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["metadata"]["agent_version"] == 1

    # 旧データなど、版が本当に無いまま続く Run の安全策（#4172 でも残す）。
    repository._runs[run["id"]].status = RunStatus.QUEUED  # noqa: SLF001 - 状態だけを作る
    assert _instructions_used(run["id"]) == "v6"


# ---- 未完了の Run が使う版（#4172） --------------------------------------------------------------


def _version_repository(*agent_ids: str) -> AgentRuntimeRepository:
    """指示 v1 を公開した業務 Agent を持つ repository（Run は作るだけで実行しない）。"""
    repository = AgentRuntimeRepository()
    for agent_id in agent_ids or (AGENT_ID,):
        repository.create_agent(AgentProfile(id=agent_id, name="Agent A", instructions="v1"))
    return repository


def _publish_versions(
    repository: AgentRuntimeRepository, numbers: Iterable[int], agent_id: str = AGENT_ID
) -> AgentProfile:
    """指示を変えて版を公開する（版の番号は公開の順。指示は `v<番号>`）。"""
    agent = next(item for item in repository.list_agents() if item.id == agent_id)
    for number in numbers:
        repository.patch_agent(agent_id, AgentProfilePatch(instructions=f"v{number}"))
        agent = repository.publish_agent(agent_id)
    return agent


def _queued_run(
    repository: AgentRuntimeRepository, agent_id: str = AGENT_ID, *, draft: bool = False
) -> RunState:
    return repository.create_builtin_run(
        RunCreateRequest(goal="合成の質問", agent_id=agent_id, draft=draft)
    )


def _approval_call() -> ToolCall:
    return ToolCall(name="synthetic_write", arguments={"value": 1}, trace_id="call-1")


def _confirmation() -> ConfirmationRequest:
    arguments = {
        "question": "どちらの条件で調べますか？",
        "reason": "条件が質問から決められません。",
        "options": [
            {"label": "条件 A", "description": ""},
            {"label": "条件 B", "description": ""},
        ],
        "multiple": False,
    }
    return ConfirmationRequest(
        source="agent", call_id="call-ask", items=[item_from_arguments(arguments)]
    )


def _run_in_status(repository: AgentRuntimeRepository, status: RunStatus) -> RunState:
    """公開の API の遷移で、未完了の各状態の Run を作る。"""
    run = _queued_run(repository)
    if status != RunStatus.QUEUED:
        assert repository.begin_builtin_run(run.id) is not None
    if status == RunStatus.WAITING_APPROVAL:
        repository.request_builtin_approvals(run.id, [_approval_call()], state="{}")
    elif status == RunStatus.WAITING_CONFIRMATION:
        repository.request_builtin_approvals(
            run.id, [], state="{}", confirmations=[_confirmation()]
        )
    stored = repository.get_run(run.id)
    assert stored.status == status
    return stored


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_CONFIRMATION,
    ],
)
def test_unfinished_run_keeps_its_version_over_the_limit(status: RunStatus) -> None:
    """未完了の Run の版は上限を超えても残り、Run はその版の内容で続く（#4172）。"""
    repository = _version_repository()
    run = _run_in_status(repository, status)
    assert run.metadata["agent_version"] == 1

    agent = _publish_versions(repository, range(2, 8))

    # 残す版は上限に数える: 公開中の版 + Run の版 + 直近の版で計 5 版。
    assert [item.version for item in agent.versions] == [1, 4, 5, 6, 7]
    assert agent_for_run(agent, run).instructions == "v1"


def test_run_resumed_after_approval_uses_its_version_and_later_publish_prunes_it() -> None:
    """承認を待つ間に公開が続いても再開は同じ版。完了の後の公開で上限どおりに消える（#4172）。"""
    repository = _version_repository()
    run = _run_in_status(repository, RunStatus.WAITING_APPROVAL)

    _publish_versions(repository, range(2, 8))

    [approval] = repository.get_run(run.id).approvals
    repository.decide_approval(approval.id, ApprovalDecisionRequest(approved=True))
    resumed = repository.begin_builtin_resume(run.id)
    assert resumed is not None
    assert resumed[1].instructions == "v1"
    assert resumed[0].metadata["agent_version"] == 1

    repository.complete_builtin_run(run.id, "合成の回答")
    assert repository.get_run(run.id).status == RunStatus.COMPLETED
    agent = _publish_versions(repository, [8])
    assert [item.version for item in agent.versions] == [4, 5, 6, 7, 8]
    # 完了した Run の版の番号の記録は変えない。
    assert repository.get_run(run.id).metadata["agent_version"] == 1


@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED])
def test_finished_run_does_not_keep_its_version(status: RunStatus) -> None:
    repository = _version_repository()
    run = _queued_run(repository)
    repository._runs[run.id].status = status  # noqa: SLF001 - 終了の状態だけを作る

    agent = _publish_versions(repository, range(2, 7))

    assert [item.version for item in agent.versions] == [2, 3, 4, 5, 6]
    assert repository.get_run(run.id).metadata["agent_version"] == 1


def test_only_the_same_agents_published_runs_keep_a_version() -> None:
    """別の業務 Agent の Run・下書きの Run は、この業務 Agent の版を残さない（#4172）。"""
    other_id = f"{AGENT_ID}-other"
    repository = _version_repository(AGENT_ID, other_id)
    other_run = _queued_run(repository, other_id)
    assert other_run.metadata["agent_version"] == 1
    draft_run = _queued_run(repository, draft=True)
    assert draft_run.metadata["agent_version"] == "draft"

    agent = _publish_versions(repository, range(2, 7))

    assert [item.version for item in agent.versions] == [2, 3, 4, 5, 6]


def test_more_unfinished_versions_than_the_limit_are_all_kept() -> None:
    """未完了の Run の版が上限より多くても消さない。Run が終われば上限に戻る（#4172）。"""
    repository = _version_repository()
    runs = [_queued_run(repository)]
    for number in range(2, 8):
        _publish_versions(repository, [number])
        runs.append(_queued_run(repository))

    agent = _publish_versions(repository, [8])
    assert [item.version for item in agent.versions] == [1, 2, 3, 4, 5, 6, 7, 8]

    for run in runs:
        repository._runs[run.id].status = RunStatus.COMPLETED  # noqa: SLF001 - 状態だけを作る
    agent = _publish_versions(repository, [9])
    assert [item.version for item in agent.versions] == [5, 6, 7, 8, 9]
