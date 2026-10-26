"""合成資料の業務 Agent の作成・公開の命令（#3545）を、memory の repository で確かめる。

RAG の API は呼ばない（プロファイルの ID は RAG の命令の state か引数で受け取る）。保存先は
テストの既定（memory）で、Oracle の保存先を開く部分（`_open_repository`）は差し替える。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.cli import agent_test_resources as cli
from app.features.agent.data_scope import AgentDataScope
from app.features.agent.runtime import AgentProfile, AgentProfilePatch, AgentRuntimeRepository
from app.settings import get_settings

DEFINITIONS: dict[str, dict[str, Any]] = {
    key: {
        "name": f"テスト_{key}_合成Agent",
        "description": "合成資料",
        "instructions": "資料を調べて回答する。",
        "skill_ids": ["business-rag-research"],
        "enabled": True,
    }
    for key in ("A", "B")
}


def _scopes(suffix: str = "") -> dict[str, AgentDataScope]:
    return {
        key: AgentDataScope(profile_ids=[f"profile-{key}{suffix}"], default_profile_id="")
        for key in DEFINITIONS
    }


def _never_refuse(_agent: AgentProfile) -> str | None:
    return None


def _provision(
    repository: AgentRuntimeRepository, scopes: dict[str, AgentDataScope], **kwargs: Any
) -> dict[str, Any]:
    kwargs.setdefault("publish_refusal", _never_refuse)
    return cli.provision(repository, DEFINITIONS, scopes, log=lambda _: None, **kwargs)


def _named(repository: AgentRuntimeRepository, name: str) -> list[AgentProfile]:
    return [agent for agent in repository.list_agents() if agent.name == name]


def _rag_state(tmp_path: Path, **overrides: Any) -> Path:
    state = {
        "complete": True,
        "namespace": "",
        "groups": {
            key: {
                "kb_id": f"kb-{key}",
                "profile_ids": [f"profile-{key}"],
                "default_profile_id": f"profile-{key}",
            }
            for key in DEFINITIONS
        },
        "documents": {},
        **overrides,
    }
    path = tmp_path / "rag-state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def test_creates_and_publishes_then_rerun_does_not_write() -> None:
    repository = AgentRuntimeRepository()
    first = _provision(repository, _scopes())
    assert first["changed"]
    for key, group in first["groups"].items():
        (agent,) = _named(repository, DEFINITIONS[key]["name"])
        assert agent.id == group["agent_id"] and agent.id.startswith("agent_")
        assert group["published_version"] == agent.published_version == 1
        assert agent.owner_user_uuid is None and agent.enabled
        published = agent.published()
        assert published is not None and published.skills  # スキルの中身を版に固定した
        assert published.data_scopes["rag"].model_dump() == {
            "profile_ids": [f"profile-{key}"],
            "default_profile_id": f"profile-{key}",
        }
    before = {agent.id: agent.updated_at for agent in repository.list_agents()}
    second = _provision(repository, _scopes())
    assert not second["changed"]
    assert second["groups"] == first["groups"]
    assert {agent.id: agent.updated_at for agent in repository.list_agents()} == before


def test_changed_profile_ids_update_the_draft_and_publish_a_new_version() -> None:
    repository = AgentRuntimeRepository()
    first = _provision(repository, _scopes())
    second = _provision(repository, _scopes("-rebuilt"))
    assert second["changed"]
    for key, group in second["groups"].items():
        assert group["agent_id"] == first["groups"][key]["agent_id"]
        assert group["published_version"] == 2
        (agent,) = _named(repository, DEFINITIONS[key]["name"])
        assert agent.data_scopes["rag"].profile_ids == [f"profile-{key}-rebuilt"]
        published = agent.published()
        assert published is not None
        assert published.data_scopes["rag"].default_profile_id == f"profile-{key}-rebuilt"


def test_disabled_agent_is_enabled_without_a_new_version() -> None:
    """有効 / 無効は版の項目ではないので、戻しても公開し直さない。"""
    repository = AgentRuntimeRepository()
    first = _provision(repository, _scopes())
    agent_id = first["groups"]["A"]["agent_id"]
    repository.patch_agent(agent_id, AgentProfilePatch(enabled=False))
    result = _provision(repository, _scopes())
    assert result["changed"]
    assert result["groups"]["A"]["published_version"] == 1
    assert next(a for a in repository.list_agents() if a.id == agent_id).enabled


def test_publish_gate_refusal_keeps_the_draft_and_fails() -> None:
    repository = AgentRuntimeRepository()
    with pytest.raises(cli.ProvisionError, match="評価の門") as caught:
        _provision(repository, _scopes(), publish_refusal=lambda _: "下書きを評価してください。")
    assert "下書きを評価してください。" in str(caught.value)
    (agent,) = _named(repository, DEFINITIONS["A"]["name"])
    assert agent.published_version is None


def test_duplicate_names_stop_without_writes() -> None:
    repository = AgentRuntimeRepository()
    for _ in range(2):
        repository.create_agent(AgentProfile(name=DEFINITIONS["A"]["name"], versioned=True))
    before = [agent.model_dump() for agent in repository.list_agents()]
    with pytest.raises(cli.ProvisionError, match="複数"):
        _provision(repository, _scopes())
    assert [agent.model_dump() for agent in repository.list_agents()] == before


def test_personal_agent_with_the_same_name_is_not_taken_over() -> None:
    repository = AgentRuntimeRepository()
    repository.create_agent(
        AgentProfile(name=DEFINITIONS["A"]["name"], versioned=True, owner_user_uuid="user-1")
    )
    with pytest.raises(cli.ProvisionError, match="個人"):
        _provision(repository, _scopes())
    (agent,) = _named(repository, DEFINITIONS["A"]["name"])
    assert agent.owner_user_uuid == "user-1" and agent.published_version is None


def test_unknown_skill_is_reported_without_saving() -> None:
    repository = AgentRuntimeRepository()
    definitions = {"A": {**DEFINITIONS["A"], "skill_ids": ["missing-skill"]}}
    with pytest.raises(cli.ProvisionError, match="保存できません"):
        cli.provision(
            repository, definitions, _scopes(), publish_refusal=_never_refuse, log=lambda _: None
        )
    assert not _named(repository, DEFINITIONS["A"]["name"])


def test_namespace_creates_separate_agents(tmp_path: Path) -> None:
    repository = AgentRuntimeRepository()
    original = _provision(repository, _scopes())
    scopes, namespace = cli.read_rag_state(_rag_state(tmp_path, namespace="isolated"))
    separate = _provision(repository, scopes, namespace=namespace)
    assert separate["namespace"] == "isolated"
    assert separate["groups"]["A"]["agent_id"] != original["groups"]["A"]["agent_id"]
    assert _named(repository, "テスト_isolated_A_合成Agent")


def test_missing_group_in_profiles_is_rejected() -> None:
    with pytest.raises(cli.ProvisionError, match="B"):
        _provision(AgentRuntimeRepository(), {"A": _scopes()["A"]})


@pytest.mark.parametrize(
    "overrides",
    [{"complete": False}, {"groups": {}}, {"groups": {"A": {"profile_ids": []}}}],
    ids=["incomplete", "no-groups", "no-profile"],
)
def test_rag_state_must_be_complete_and_have_profiles(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    with pytest.raises(cli.ProvisionError):
        cli.read_rag_state(_rag_state(tmp_path, **overrides))


@pytest.mark.parametrize(
    "values",
    [["A"], ["A=x", "A=y"], ["A="], ["A B=x"]],
    ids=["no-separator", "duplicate", "empty-id", "bad-group"],
)
def test_profile_arguments_are_validated(values: list[str]) -> None:
    with pytest.raises(cli.ProvisionError):
        cli.parse_profiles(values)


def test_definitions_reject_data_scopes_and_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    extras: list[dict[str, Any]] = [{"data_scopes": {}}, {"owner_user_uuid": "x"}]
    for extra in extras:
        definition = {**DEFINITIONS["A"], **extra}
        path.write_text(json.dumps({"version": 1, "agents": {"A": definition}}), encoding="utf-8")
        with pytest.raises(cli.ProvisionError, match="使えない項目"):
            cli.read_definitions(path)


def test_repository_definitions_are_valid() -> None:
    definitions = cli.read_definitions(cli.DEFAULT_DEFINITIONS)
    assert set(definitions) == {"A", "B"}
    for definition in definitions.values():
        assert definition["skill_ids"] == ["business-rag-research"]
        assert definition["enabled"] is True


def test_main_refuses_a_storage_the_backend_cannot_share(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """テストの保存先（memory）は動いている backend と共有できないので、書かずに止める。"""
    assert cli.main(["--profile", "A=p-A", "--profile", "B=p-B"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert not output["ok"] and "Oracle ではありません" in output["error"]["message"]


def test_main_writes_the_result_and_asks_for_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = AgentRuntimeRepository()
    monkeypatch.setattr(cli, "_open_repository", lambda: repository)
    state = tmp_path / "out" / "agent-state.json"
    argv = ["--rag-state", str(_rag_state(tmp_path)), "--state", str(state)]
    assert cli.main(argv) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] and output["data"] == json.loads(state.read_text(encoding="utf-8"))
    assert set(output["data"]["groups"]) == {"A", "B"}
    assert output["data"]["restart_required"]["processes"]
    assert state.stat().st_mode & 0o777 == 0o600
    # 2 回目は書かないので、再起動も求めない。
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["data"]["restart_required"] is None


def test_main_applies_the_publish_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開は画面と同じ評価の門を通す（下書きの評価が無ければ公開しない）。"""
    repository = AgentRuntimeRepository()
    monkeypatch.setattr(cli, "_open_repository", lambda: repository)
    monkeypatch.setattr(get_settings(), "agent_publish_min_pass_rate", 0.8)
    assert cli.main(["--profile", "A=p-A", "--profile", "B=p-B"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert "評価の門" in output["error"]["message"]
    (agent,) = _named(repository, cli.read_definitions(cli.DEFAULT_DEFINITIONS)["A"]["name"])
    assert agent.published_version is None


def test_main_rejects_namespace_with_rag_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--rag-state", str(_rag_state(tmp_path)), "--namespace", "other"]
    assert cli.main(argv) == 1
    assert "state の名前の区分" in json.loads(capsys.readouterr().out)["error"]["message"]
