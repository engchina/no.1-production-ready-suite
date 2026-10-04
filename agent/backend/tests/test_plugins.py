"""Plugin（Marketplace の配布単位）と旧 Agent の移行の契約テスト。

外部 Runtime・Binding の契約テストは、組み込み Runtime への置き換え（#754）で削除した
（組み込み Runtime は tests/test_builtin_runtime.py）。
"""

from __future__ import annotations

import pytest

from app.features.agent.plugins import PluginManifest, PluginRegistry
from app.features.agent.runtime import AgentProfile, _migrate_legacy_agent, runtime_repository
from app.features.agent.skills import AgentSkillDefinition, skill_registry


def test_plugin_duplicate_ids_fail_before_registry_mutation() -> None:
    registry = PluginRegistry()
    skill_id = "duplicate_control_plane_skill"
    manifest = PluginManifest(
        id="duplicate_control_plane_package",
        name="duplicate",
        skills=[
            AgentSkillDefinition(id=skill_id, name="first"),
            AgentSkillDefinition(id=skill_id, name="second"),
        ],
    )

    with pytest.raises(ValueError, match="duplicate skill id"):
        registry.install(manifest)

    assert skill_registry.get(skill_id) is None


def test_plugin_in_use_cannot_be_disabled_or_uninstalled() -> None:
    registry = PluginRegistry()
    plugin_id = "referenced_control_plane_package"
    skill_id = "referenced_control_plane_skill"
    agent_id = "referenced_control_plane_agent"
    registry.install(
        PluginManifest(
            id=plugin_id,
            name="referenced",
            skills=[AgentSkillDefinition(id=skill_id, name="referenced")],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=agent_id, name="referenced", skill_ids=[skill_id])
    )
    try:
        with pytest.raises(ValueError, match=r"業務 Agent（referenced）が使っています"):
            registry.set_enabled(plugin_id, False)
        with pytest.raises(ValueError, match=r"業務 Agent（referenced）が使っています"):
            registry.uninstall(plugin_id)
    finally:
        runtime_repository.delete_agent(agent_id)
        registry.uninstall(plugin_id)

    assert skill_registry.get(skill_id) is None


def test_legacy_tool_migration_disables_unmappable_agent() -> None:
    migrated = _migrate_legacy_agent(
        AgentProfile(
            id="legacy_agent",
            name="legacy",
            tool_names=["external_rag_search", "echo"],
        )
    )

    assert migrated.skill_ids == ["business_rag_research"]
    assert migrated.migration_required is True
    assert migrated.enabled is False
