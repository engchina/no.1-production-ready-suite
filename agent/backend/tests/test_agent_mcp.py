"""業務 Agent の MCP（`POST /api/mcp`）と API キー（#778）。

業務 Agent のモデルは SDK の `ScriptedModel` に差し替え、OCI へは接続しない。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from agents.testing import ScriptedModel, assistant_message
from pr_system_settings.auth.service_token import issue_service_token
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent import builtin_runtime, mcp_server
from app.features.agent.api_keys import api_key_registry
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.control_plane_store import (
    FileItemStore,
    restore_control_plane,
    set_control_plane_store,
)
from app.features.agent.runtime import AgentProfile, runtime_repository
from app.security.permissions import ADMIN, MENU_SETTINGS_API_KEYS, RUNS_OPERATE, RUNS_VIEW
from app.security.service import set_security_service
from app.settings import get_settings

AGENT_ID = "mcp-778-agent"
OTHER_AGENT_ID = "mcp-778-other"
SECRET = "s" * 40


@pytest.fixture
def agents(monkeypatch: MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "app_service_token_secret", SECRET)
    monkeypatch.setattr(mcp_server, "wait_poll_seconds", 0.01)
    set_control_plane_store(FileItemStore(tmp_path / "items.json"))
    for agent_id, name in ((AGENT_ID, "経理の Agent"), (OTHER_AGENT_ID, "人事の Agent")):
        runtime_repository.create_agent(
            AgentProfile(
                id=agent_id, name=name, description=f"{name}の説明", instructions="答える。"
            )
        )
    api_key_registry.clear()
    try:
        yield
    finally:
        api_key_registry.clear()
        set_control_plane_store(None)
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id
                for run_id, run in repository._runs.items()
                if run.agent_id in {AGENT_ID, OTHER_AGENT_ID}
            ]:
                repository._runs.pop(run_id)
        for agent_id in (AGENT_ID, OTHER_AGENT_ID):
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent_id)


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "m", endpoint="https://oci.example", project_ocid="", api_key="k"
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _rpc(
    method: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> Any:
    return client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers=headers or {},
    )


def _call(
    name: str, arguments: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = _rpc("tools/call", {"name": name, "arguments": arguments}, headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_mcp_lists_tools_and_answers_with_the_agent(monkeypatch: MonkeyPatch, agents: None) -> None:
    del agents
    _script(monkeypatch, [assistant_message("今月の経費は 50 万円です。")])

    initialized = _rpc("initialize", {"protocolVersion": "2025-06-18"})
    assert initialized.json()["result"]["serverInfo"]["name"] == "production-ready-agent"
    tools = {tool["name"]: tool for tool in _rpc("tools/list").json()["result"]["tools"]}
    assert set(tools) == {"agent_list_agents", "agent_ask", "agent_get_run"}
    assert tools["agent_ask"]["annotations"]["readOnlyHint"] is False

    listed = _call("agent_list_agents", {})["structuredContent"]["agents"]
    assert {"id": AGENT_ID, "name": "経理の Agent", "description": "経理の Agentの説明"} in listed

    answered = _call("agent_ask", {"agent_id": AGENT_ID, "question": "今月の経費は？"})
    output = answered["structuredContent"]
    assert answered["isError"] is False
    assert (output["status"], output["answer"]) == ("completed", "今月の経費は 50 万円です。")
    run = runtime_repository.get_run(output["run_id"])
    assert run.metadata["source"] == "mcp"
    # 後から同じ Run を読める。
    again = _call("agent_get_run", {"run_id": output["run_id"]})["structuredContent"]
    assert again["answer"] == "今月の経費は 50 万円です。"


def test_ask_returns_the_run_when_the_answer_is_not_ready(agents: None) -> None:
    del agents
    output = _call("agent_ask", {"agent_id": AGENT_ID, "question": "q", "wait_seconds": 0})
    assert output["structuredContent"]["status"] in {"queued", "running"}
    assert "agent_get_run" in output["structuredContent"]["message"]

    missing = _call("agent_ask", {"agent_id": "no-such-agent", "question": "q"})
    assert missing["isError"] is True
    assert missing["structuredContent"]["error_code"] == "AGENT_NOT_FOUND"
    invalid = _call("agent_ask", {"agent_id": AGENT_ID, "question": "q", "wait_seconds": 999})
    assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def _create_key(headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = client.post(
        "/api/settings/api-keys", json={"name": "基幹システム", **body}, headers=headers
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def test_api_key_acts_as_its_creator_and_is_limited_to_its_agents(
    auth: ProductionAuth, agents: None, tmp_path: Path
) -> None:
    del agents
    auth.user_with_permissions("admin-778", [ADMIN])
    admin = login("admin-778")
    created = _create_key(admin, agent_ids=[AGENT_ID], expires_in_days=30)
    token = created["token"]
    assert token.startswith("prak_")
    key = created["key"]
    assert key["token_prefix"] == token[: len(key["token_prefix"])]
    assert "token_hash" not in key
    assert key["expires_at"] is not None

    # 秘密は保存しない（hash だけ）。
    stored = json.loads((tmp_path / "items.json").read_text())["api_key"][key["id"]]
    assert token not in json.dumps(stored)
    assert stored["token_hash"] != token

    listed = client.get("/api/settings/api-keys", headers=admin).json()["data"]
    assert [item["name"] for item in listed["keys"]] == ["基幹システム"]
    assert listed["keys"][0]["owner_display_name"] == "admin-778"
    assert listed["persistent"] is True

    # キーに付けた業務 Agent だけ（Cookie・CSRF なしで呼べる）。
    key_headers = _bearer(token)
    agents_seen = _call("agent_list_agents", {}, key_headers)["structuredContent"]["agents"]
    assert [item["id"] for item in agents_seen] == [AGENT_ID]
    other = _call("agent_ask", {"agent_id": OTHER_AGENT_ID, "question": "q"}, key_headers)
    assert other["structuredContent"]["error_code"] == "AGENT_NOT_FOUND"

    # 誤ったキー・削除したキーは 401。
    assert _rpc("tools/list", headers=_bearer(token[:-1] + "x")).status_code == 401
    assert client.delete(f"/api/settings/api-keys/{key['id']}", headers=admin).status_code == 200
    assert _rpc("tools/list", headers=_bearer(token)).status_code == 401
    assert key["id"] not in json.loads((tmp_path / "items.json").read_text()).get("api_key", {})


def test_api_keys_survive_restart_and_expire(auth: ProductionAuth, agents: None) -> None:
    del agents
    auth.user_with_permissions("admin-778b", [ADMIN])
    token = _create_key(login("admin-778b"), expires_in_days=None)["token"]

    # 再起動（メモリを消して保存先から復元）しても使える。
    api_key_registry.clear()
    assert restore_control_plane()["api_key"] == 1
    assert _rpc("tools/list", headers=_bearer(token)).status_code == 200

    record = api_key_registry.list()[0]
    record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    api_key_registry.restore(record)
    assert _rpc("tools/list", headers=_bearer(token)).status_code == 401


def test_api_key_management_needs_admin(auth: ProductionAuth, agents: None) -> None:
    del agents
    auth.user_with_permissions("viewer-778", [MENU_SETTINGS_API_KEYS])
    viewer = login("viewer-778")
    assert client.get("/api/settings/api-keys", headers=viewer).status_code == 200
    created = client.post("/api/settings/api-keys", json={"name": "x"}, headers=viewer)
    assert created.status_code == 403
    auth.user_with_permissions("admin-778c", [ADMIN])
    admin = login("admin-778c")
    unknown = client.post(
        "/api/settings/api-keys", json={"name": "x", "agent_ids": ["nope"]}, headers=admin
    )
    assert unknown.status_code == 422
    empty = client.post(
        "/api/settings/api-keys", json={"name": "x", "agent_ids": []}, headers=admin
    )
    assert empty.status_code == 422


def test_service_token_uses_the_subject_permissions(auth: ProductionAuth, agents: None) -> None:
    del agents
    viewer = auth.user_with_permissions("svc-viewer-778", [RUNS_VIEW], agent_ids=[AGENT_ID])
    token = issue_service_token(SECRET, subject=viewer.user_uuid, audience="agent", issuer="rag")
    tools = _rpc("tools/list", headers=_bearer(token)).json()["result"]["tools"]
    # 閲覧だけの利用者には質問のツールを出さない。
    assert {tool["name"] for tool in tools} == {"agent_list_agents", "agent_get_run"}
    listed = _call("agent_list_agents", {}, _bearer(token))["structuredContent"]["agents"]
    assert [item["id"] for item in listed] == [AGENT_ID]

    operator = auth.user_with_permissions("svc-op-778", [RUNS_OPERATE], agent_ids=[AGENT_ID])
    op_token = issue_service_token(SECRET, subject=operator.user_uuid, audience="agent", issuer="x")
    ask = {"agent_id": AGENT_ID, "question": "q", "wait_seconds": 0}
    asked = _call("agent_ask", ask, _bearer(op_token))["structuredContent"]
    # ほかの利用者の Run は読めない。
    other = _call("agent_get_run", {"run_id": asked["run_id"]}, headers=_bearer(token))
    assert other["structuredContent"]["error_code"] == "RUN_NOT_FOUND"

    wrong_audience = issue_service_token(
        SECRET, subject=viewer.user_uuid, audience="rag", issuer="x"
    )
    assert _rpc("tools/list", headers=_bearer(wrong_audience)).status_code == 401
    # Cookie も Bearer も無ければ 401。
    assert _rpc("tools/list").status_code == 401


def test_system_admin_can_bind_a_key_to_a_dedicated_service_user(
    auth: ProductionAuth, agents: None
) -> None:
    """キーの実行する利用者（連携用の専用の利用者）を選べるのはシステム管理者だけ（#778）。"""
    del agents
    service = auth.user_with_permissions("svc-erp-778", [RUNS_OPERATE], agent_ids=[OTHER_AGENT_ID])
    auth.user_with_permissions("agent-admin-778", [ADMIN])
    body = {"name": "基幹システム", "run_as_user_uuid": service.user_uuid}
    # Agent 管理だけの利用者は、ほかの利用者として動くキーを作れない。
    denied = client.post("/api/settings/api-keys", json=body, headers=login("agent-admin-778"))
    assert denied.status_code == 403

    auth.create_user("sysadmin-778", system_admin=True)
    sysadmin = login("sysadmin-778")
    created = _create_key(sysadmin, run_as_user_uuid=service.user_uuid)
    key = created["key"]
    assert (key["owner_display_name"], key["created_by_display_name"]) == (
        "svc-erp-778",
        "sysadmin-778",
    )
    # キーは実行する利用者の権限と対象範囲で動く（作ったシステム管理者の権限ではない）。
    listed = _call("agent_list_agents", {}, _bearer(created["token"]))["structuredContent"]
    assert [item["id"] for item in listed["agents"]] == [OTHER_AGENT_ID]
    asked = _call(
        "agent_ask",
        {"agent_id": OTHER_AGENT_ID, "question": "q", "wait_seconds": 0},
        _bearer(created["token"]),
    )["structuredContent"]
    assert runtime_repository.get_run(asked["run_id"]).created_by_user_uuid == service.user_uuid

    # 実行する利用者は、有効で初回のパスワード変更が済んでいること。
    unknown = client.post(
        "/api/settings/api-keys",
        json={"name": "x", "run_as_user_uuid": "00000000-0000-0000-0000-00000000ffff"},
        headers=sysadmin,
    )
    assert unknown.status_code == 422
    fresh = auth.create_user("svc-fresh-778", force_password_change=True)
    pending = client.post(
        "/api/settings/api-keys",
        json={"name": "x", "run_as_user_uuid": fresh.user_uuid},
        headers=sysadmin,
    )
    assert pending.status_code == 422
    assert "初回のパスワード変更" in pending.text


def test_unpublished_agents_are_not_listed_or_asked(agents: None) -> None:
    """公開した版の無い業務 Agent は利用者の Run で使えないので、一覧に出さず断る（#792）。"""
    del agents
    draft_id = "mcp-792-draft"
    created = client.post(
        "/api/agents", json={"id": draft_id, "name": "下書きの Agent", "instructions": "答える。"}
    )
    assert created.status_code == 200, created.text
    try:
        assert created.json()["data"]["published_version"] is None
        listed = _call("agent_list_agents", {})["structuredContent"]["agents"]
        assert draft_id not in {item["id"] for item in listed}
        refused = _call("agent_ask", {"agent_id": draft_id, "question": "q"})
        assert refused["isError"] is True
        assert "公開していない業務 Agent" in json.dumps(refused, ensure_ascii=False)

        published = client.post(f"/api/agents/{draft_id}/publish", json={"note": "初版"})
        assert published.status_code == 200, published.text
        listed = _call("agent_list_agents", {})["structuredContent"]["agents"]
        assert draft_id in {item["id"] for item in listed}
    finally:
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(draft_id)
