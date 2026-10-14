"""旧 Agent（v1 の `tool_names`）の移行の契約テスト。

外部 Runtime・Binding の契約テストは、組み込み Runtime への置き換え（#754）で削除した
（組み込み Runtime は tests/test_builtin_runtime.py）。
"""

from __future__ import annotations

from app.features.agent.runtime import AgentProfile, _migrate_legacy_agent


def test_legacy_tool_migration_disables_unmappable_agent() -> None:
    migrated = _migrate_legacy_agent(
        AgentProfile(
            id="legacy_agent",
            name="legacy",
            tool_names=["external_rag_search", "echo"],
        )
    )

    assert migrated.skill_ids == ["business-rag-research"]
    assert migrated.migration_required is True
    assert migrated.enabled is False
