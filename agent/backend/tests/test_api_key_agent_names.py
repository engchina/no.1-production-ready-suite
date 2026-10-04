"""API キーの一覧の業務 Agent の名前（閲覧者が利用できる業務 Agent だけ）。"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent.api_keys import api_key_registry
from app.features.agent.control_plane_store import FileItemStore, set_control_plane_store
from app.features.agent.runtime import AgentProfile, runtime_repository
from app.security.permissions import ADMIN, MENU_SETTINGS_API_KEYS
from app.security.service import set_security_service

FINANCE = "api-key-names-finance"
HR = "api-key-names-hr"


@pytest.fixture
def auth(monkeypatch: MonkeyPatch, tmp_path: Path) -> Iterator[ProductionAuth]:
    set_control_plane_store(FileItemStore(tmp_path / "items.json"))
    for agent_id, name in ((FINANCE, "経理の Agent"), (HR, "人事の Agent")):
        runtime_repository.create_agent(AgentProfile(id=agent_id, name=name))
    api_key_registry.clear()
    try:
        yield enable_production_auth(monkeypatch)
    finally:
        set_security_service(None)
        api_key_registry.clear()
        set_control_plane_store(None)
        for agent_id in (FINANCE, HR):
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent_id)


def test_api_key_list_names_only_the_agents_the_viewer_can_use(auth: ProductionAuth) -> None:
    auth.user_with_permissions("names-admin", [ADMIN])
    admin = login("names-admin")
    created = client.post(
        "/api/settings/api-keys",
        json={"name": "基幹システム", "agent_ids": [FINANCE, HR]},
        headers=admin,
    )
    assert created.status_code == 200

    listed = client.get("/api/settings/api-keys", headers=admin).json()["data"]
    assert listed["agent_names"] == {FINANCE: "経理の Agent", HR: "人事の Agent"}

    # API キーのメニューだけ（業務 Agent の一覧は読めない）でも、利用できる業務 Agent は名前で示す。
    auth.user_with_permissions("names-viewer", [MENU_SETTINGS_API_KEYS], agent_ids=[FINANCE])
    viewer = login("names-viewer")
    assert client.get("/api/agents", headers=viewer).status_code == 403
    listed = client.get("/api/settings/api-keys", headers=viewer).json()["data"]
    assert listed["keys"][0]["agent_ids"] == [FINANCE, HR]
    # 範囲外の業務 Agent の名前は返さない（権限を広げない）。
    assert listed["agent_names"] == {FINANCE: "経理の Agent"}

    # 削除した業務 Agent は名前を返さない。
    runtime_repository.delete_agent(HR)
    listed = client.get("/api/settings/api-keys", headers=admin).json()["data"]
    assert listed["agent_names"] == {FINANCE: "経理の Agent"}
