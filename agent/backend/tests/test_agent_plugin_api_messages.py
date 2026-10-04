"""業務 Agent の ID の検証（#1033）と、プラグインの無効化・削除の拒否の文（#1032）。"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.features.agent.plugins import plugin_registry
from app.features.agent.runtime import runtime_repository
from app.main import app

client = TestClient(app)

PLUGIN_ID = "plugin-1032"
SKILL_ID = "skill_1032"
AGENT_ID = "agent-1032"
AGENT_ID_MESSAGE = (
    "業務 Agent の ID は英数字で始め、英数字・_・-・. の 100 文字以内にしてください。"
)
REFERENCED_MESSAGE = (
    "このプラグインのスキルは業務 Agent（経理の Agent）が使っています。"
    "業務 Agent のスキルから外して公開してから、無効化・削除してください。"
)


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    for agent in runtime_repository.list_agents():
        if agent.id.startswith(("agent-1033", AGENT_ID)) or agent.name == "agent-1033":
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent.id)
    with contextlib.suppress(KeyError, ValueError):
        runtime_repository.delete_agent(AGENT_ID)
    with contextlib.suppress(KeyError, ValueError):
        plugin_registry.uninstall(PLUGIN_ID)


def _messages(response: Any) -> list[str]:
    messages: list[str] = response.json()["error_messages"]
    return messages


@pytest.mark.parametrize(
    "agent_id",
    ["a/b", "a b", "", "  ", ".hidden", "-x", "経理", "x" * 101],
    ids=["slash", "space", "empty", "blank", "dot", "hyphen", "japanese", "too-long"],
)
def test_agent_id_must_be_url_safe(agent_id: str) -> None:
    before = {agent.id for agent in runtime_repository.list_agents()}

    created = client.post("/api/agents", json={"id": agent_id, "name": "agent-1033"})

    assert created.status_code == 422
    assert _messages(created) == [AGENT_ID_MESSAGE]
    assert {agent.id for agent in runtime_repository.list_agents()} == before


def test_agent_id_is_trimmed_or_generated() -> None:
    created = client.post("/api/agents", json={"id": " agent-1033_v1.0 ", "name": "agent-1033"})
    assert created.status_code == 200
    assert created.json()["data"]["id"] == "agent-1033_v1.0"
    assert "agent-1033_v1.0" in {agent.id for agent in runtime_repository.list_agents()}
    assert client.request("DELETE", "/api/agents/agent-1033_v1.0").status_code == 200

    # ID を送らなければ従来どおり backend が作る（画面の作成）。
    generated = client.post("/api/agents", json={"name": "agent-1033"})
    assert generated.status_code == 200
    generated_id = generated.json()["data"]["id"]
    assert generated_id.startswith("agent_")
    assert client.request("DELETE", f"/api/agents/{generated_id}").status_code == 200


def _install_plugin_and_agent() -> None:
    manifest = {
        "id": PLUGIN_ID,
        "name": "経理のプラグイン",
        "version": "1.0.0",
        "skills": [{"id": SKILL_ID, "name": "経理の手順"}],
    }
    assert client.post("/api/plugins", json={"manifest": manifest}).status_code == 200
    agent = client.post(
        "/api/agents", json={"id": AGENT_ID, "name": "経理の Agent", "skill_ids": [SKILL_ID]}
    )
    assert agent.status_code == 200


def test_plugin_used_by_agent_cannot_be_disabled_or_uninstalled() -> None:
    _install_plugin_and_agent()

    disabled = client.patch(f"/api/plugins/{PLUGIN_ID}", json={"enabled": False})
    assert disabled.status_code == 409
    assert _messages(disabled) == [REFERENCED_MESSAGE]
    removed = client.request("DELETE", f"/api/plugins/{PLUGIN_ID}")
    assert removed.status_code == 409
    assert _messages(removed) == [REFERENCED_MESSAGE]

    # 下書きから外しても、公開中の版が使っていれば断る（公開中の版がスキルを欠いて動くため）。
    assert client.post(f"/api/agents/{AGENT_ID}/publish", json={}).status_code == 200
    assert client.patch(f"/api/agents/{AGENT_ID}", json={"skill_ids": []}).status_code == 200
    still_used = client.patch(f"/api/plugins/{PLUGIN_ID}", json={"enabled": False})
    assert still_used.status_code == 409
    assert _messages(still_used) == [REFERENCED_MESSAGE]

    # 外した下書きを公開すれば無効化・削除できる。
    assert client.post(f"/api/agents/{AGENT_ID}/publish", json={}).status_code == 200
    assert client.patch(f"/api/plugins/{PLUGIN_ID}", json={"enabled": False}).status_code == 200
    assert client.request("DELETE", f"/api/plugins/{PLUGIN_ID}").status_code == 200
    assert plugin_registry.get(PLUGIN_ID) is None


def test_plugin_not_found_is_japanese() -> None:
    for response in (
        client.get("/api/plugins/missing-1032"),
        client.patch("/api/plugins/missing-1032", json={"enabled": False}),
        client.patch("/api/plugins/missing-1032", json={}),
        client.request("DELETE", "/api/plugins/missing-1032"),
    ):
        assert response.status_code == 404
        assert _messages(response) == ["プラグインが見つかりません。"]
