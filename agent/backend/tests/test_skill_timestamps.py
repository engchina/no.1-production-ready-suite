"""保存定義の復元・同一内容の保存と実編集の時刻を区別する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest

from app.features.agent import skills
from app.features.agent.skills import (
    AgentSkillDefinition,
    SkillMcpRequirement,
    SkillReference,
    SkillRegistry,
)

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
UPDATED = datetime(2026, 1, 2, tzinfo=UTC)
EDITED = datetime(2026, 1, 3, tzinfo=UTC)


def _saved() -> AgentSkillDefinition:
    return AgentSkillDefinition(
        id="custom-a",
        name="合成スキルA",
        instructions="合成手順A",
        references=[SkillReference(path="references/a.md", characters=3, content="abc")],
        created_at=CREATED,
        updated_at=UPDATED,
    )


def test_restore_is_identical_across_restarts_and_registry_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_clock() -> datetime:
        pytest.fail("復元中に時計を読んではならない")

    monkeypatch.setattr(skills, "_now", no_clock)
    document = _saved().model_dump(mode="json")
    document["references"] = []
    expected = AgentSkillDefinition.model_validate(document)
    for registry in (SkillRegistry(), SkillRegistry()):
        for _ in range(2):
            restored = registry.restore_custom(AgentSkillDefinition.model_validate(document))
            assert restored == expected
            assert registry.get(expected.id) == expected


def test_same_content_upsert_is_idempotent_and_preserves_creation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SkillRegistry()
    registry.restore_custom(_saved())
    monkeypatch.setattr(skills, "_now", lambda: EDITED)
    supplied = _saved().model_copy(update={"created_at": EDITED, "updated_at": EDITED})
    assert registry.upsert_custom(supplied) == _saved()
    assert registry.get(supplied.id) == _saved()


@pytest.mark.parametrize(
    "change",
    [
        {"name": "合成スキルB"},
        {"description": "合成説明B"},
        {"instructions": "合成手順B"},
        {"enabled": False},
        {"tags": ["tag-b"]},
        {"mcp_requirements": [SkillMcpRequirement(server_id="server-a", tool_names=["read_a"])]},
        {"references": [SkillReference(path="references/a.md", characters=3, content="xyz")]},
    ],
)
def test_actual_changes_advance_update_time_without_changing_creation_time(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    registry = SkillRegistry()
    registry.restore_custom(_saved())
    monkeypatch.setattr(skills, "_now", lambda: EDITED)
    changed = _saved().model_copy(deep=True, update=change)
    result = registry.upsert_custom(changed)
    assert result.created_at == CREATED
    assert result.updated_at == EDITED
    assert result == changed.model_copy(update={"updated_at": EDITED})
    assert registry.upsert_custom(changed) == result


def test_replace_runtime_preserves_saved_timestamps_and_other_layers() -> None:
    registry = SkillRegistry()
    registry.register(AgentSkillDefinition(id="builtin-a", name="組み込みA"))
    registry.set_declared("env", [AgentSkillDefinition(id="env-a", name="宣言A")])
    registry.restore_custom(AgentSkillDefinition(id="old-a", name="旧定義A"))
    assert registry.replace_runtime(
        [_saved(), AgentSkillDefinition(id="builtin-a", name="別定義")]
    ) == ["builtin-a"]
    assert registry.get("custom-a") == _saved()
    assert registry.get("old-a") is None
    builtin = registry.get("builtin-a")
    declared = registry.get("env-a")
    assert builtin is not None and builtin.source == "builtin"
    assert declared is not None and declared.source == "env"
    with pytest.raises(ValueError):
        registry.restore_custom(AgentSkillDefinition(id="builtin-a", name="別定義"))


def test_concurrent_same_definition_restores_and_noop_updates_keep_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SkillRegistry()
    registry.restore_custom(_saved())
    monkeypatch.setattr(skills, "_now", lambda: EDITED)
    with ThreadPoolExecutor(max_workers=4) as pool:
        restored = list(pool.map(lambda _: registry.restore_custom(_saved()), range(8)))
        saved = list(pool.map(lambda _: registry.upsert_custom(_saved()), range(8)))
    assert all(item == _saved() for item in restored + saved)
    restored[0].references[0].content = "mutated"
    assert registry.get("custom-a") == _saved()
