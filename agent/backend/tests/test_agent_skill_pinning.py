"""業務 Agent の公開の版にスキルの中身を固定する（#1855）の決定論テスト。

モデルは SDK の `ScriptedModel` に差し替え、OCI へは接続しない。公開中の版の Run は固定した
スキルの指示・ツールで動き、下書きの Run は今のスキルで動くことを、モデルに渡した指示と
ツールで確かめる。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.runtime import (
    AgentProfile,
    AgentVersion,
    RunCreateRequest,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skill_references import skill_reference_keys
from app.features.agent.skills import (
    AgentSkillDefinition,
    SkillMcpRequirement,
    SkillReference,
    pin_skill,
    reset_pinned_skills,
    resolve_skill,
    skill_content_hash,
    skill_registry,
    use_pinned_skills,
)
from app.features.agent.tools import ToolDefinition, ToolPermissionLevel, tool_registry

LOOKUP = "test1855_lookup"
EXPORT = "test1855_export"
SKILL_ID = "test1855-skill"
AGENT_ID = "test1855-agent"
USER_UUID = "11111111-2222-3333-4444-555555555555"
OLD_INSTRUCTIONS = "売上は test1855_lookup で調べる（前の手順）。"
NEW_INSTRUCTIONS = "売上は test1855_export で書き出して調べる（新しい手順）。"


def _skill(instructions: str, tools: list[str]) -> AgentSkillDefinition:
    return AgentSkillDefinition(
        id=SKILL_ID,
        name="売上のスキル",
        description="売上を調べる",
        instructions=instructions,
        mcp_requirements=[SkillMcpRequirement(server_id="control-plane", tool_names=tools)],
    )


@pytest.fixture
def agent() -> Iterator[None]:
    for name in (LOOKUP, EXPORT):
        tool_registry.register(
            ToolDefinition(
                name=name,
                description=f"{name} のテスト用ツール",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                permission_level=ToolPermissionLevel.READ,
            ),
            lambda _arguments, _context: {"rows": 1},
        )
    skill_registry.upsert_custom(_skill(OLD_INSTRUCTIONS, [LOOKUP]))
    runtime_repository.create_agent(
        AgentProfile(
            id=AGENT_ID,
            name="売上の Agent",
            instructions="売上の質問に答える。",
            skill_ids=[SKILL_ID],
            versioned=True,
        )
    )
    try:
        yield
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                repository._runs.pop(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)
        for name in (LOOKUP, EXPORT):
            tool_registry._definitions.pop(name, None)  # noqa: SLF001 - テスト用の登録を戻す
            tool_registry._handlers.pop(name, None)  # noqa: SLF001


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _run(*, draft: bool) -> str:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="今月の売上は？", agent_id=AGENT_ID, draft=draft),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    assert runtime_repository.get_run(run.id).status == RunStatus.COMPLETED
    return run.id


def _agent() -> AgentProfile:
    return next(item for item in runtime_repository.list_agents() if item.id == AGENT_ID)


def _tool_names(call: Any) -> list[str]:
    return sorted(str(getattr(tool, "name", "")) for tool in call.tools)


def test_published_run_keeps_the_pinned_skill_and_draft_uses_the_current_one(
    monkeypatch: MonkeyPatch, agent: None
) -> None:
    """公開中の版はスキルを変えても振る舞いが変わらず、再公開で変わる（#1855）。"""
    del agent
    published = runtime_repository.publish_agent(AGENT_ID, published_by="admin")
    version = published.published()
    assert version is not None and version.skills is not None
    [pinned] = version.skills
    assert (pinned.id, pinned.instructions) == (SKILL_ID, OLD_INSTRUCTIONS)
    assert pinned.content_hash == skill_content_hash(_skill(OLD_INSTRUCTIONS, [LOOKUP]))
    assert (published.skills_pinned, published.skill_updates, published.unpublished_changes) == (
        True,
        [],
        False,
    )

    # スキルを変える（指示とツール）。公開中の業務 Agent に「スキルが更新されています」が付く。
    skill_registry.upsert_custom(_skill(NEW_INSTRUCTIONS, [EXPORT]))
    changed = _agent()
    assert changed.skill_updates == [SKILL_ID]
    assert changed.unpublished_changes is True

    model = _script(
        monkeypatch,
        [assistant_message("公開中の版の回答")],
        [assistant_message("下書きの回答")],
        [assistant_message("再公開の後の回答")],
    )
    _run(draft=False)
    _run(draft=True)
    published_call, draft_call = model.calls
    # 公開中の版は固定した指示とツール（前の手順・lookup）。
    assert OLD_INSTRUCTIONS in str(published_call.system_instructions)
    assert NEW_INSTRUCTIONS not in str(published_call.system_instructions)
    assert LOOKUP in _tool_names(published_call) and EXPORT not in _tool_names(published_call)
    # 下書きは今のスキル（新しい手順・export）。
    assert NEW_INSTRUCTIONS in str(draft_call.system_instructions)
    assert EXPORT in _tool_names(draft_call) and LOOKUP not in _tool_names(draft_call)

    # 再公開で今のスキルを固定し直す。
    republished = runtime_repository.publish_agent(AGENT_ID, published_by="admin")
    assert republished.skill_updates == [] and republished.unpublished_changes is False
    _run(draft=False)
    assert NEW_INSTRUCTIONS in str(model.calls[2].system_instructions)
    # 前の版に戻すと、その版に固定したスキルで動く。
    restored = runtime_repository.restore_agent_version(AGENT_ID, 1, published_by="admin")
    assert restored.skill_updates == [SKILL_ID]


def test_versions_without_pinned_skills_use_the_current_skill(
    monkeypatch: MonkeyPatch, agent: None
) -> None:
    """#1855 より前の版（固定していない版）は今のスキルで動き、更新の印は出さない。"""
    del agent
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - 固定していない版を作る
        stored = repository._agents[AGENT_ID]
        stored.versions = [
            AgentVersion(
                version=1,
                name=stored.name,
                instructions=stored.instructions,
                skill_ids=list(stored.skill_ids),
            )
        ]
        stored.published_version = 1
    legacy = _agent()
    assert (legacy.skills_pinned, legacy.skill_updates, legacy.unpublished_changes) == (
        False,
        [],
        False,
    )
    skill_registry.upsert_custom(_skill(NEW_INSTRUCTIONS, [EXPORT]))
    model = _script(monkeypatch, [assistant_message("回答")])
    _run(draft=False)
    assert NEW_INSTRUCTIONS in str(model.calls[0].system_instructions)


def test_pinned_skills_are_saved_with_the_version_but_not_on_the_run_agent(agent: None) -> None:
    del agent
    published = runtime_repository.publish_agent(AGENT_ID)
    restored = AgentProfile.model_validate_json(published.model_dump_json())
    assert restored.versions[-1].skills == published.versions[-1].skills
    # Run が使う版の固定（pinned_skills）は保存・応答に出さない。
    with_pins = published.model_copy(update={"pinned_skills": published.versions[-1].skills})
    assert "pinned_skills" not in with_pins.model_dump()


def test_content_hash_follows_the_content_and_references() -> None:
    """hash は中身（参照文書の本文を含む）で決まり、更新日時・由来層では変わらない。"""
    base = _skill(OLD_INSTRUCTIONS, [LOOKUP]).model_copy(
        update={"references": [SkillReference(path="references/a.md", characters=3, content="abc")]}
    )
    later = base.model_copy(update={"source": "project"})
    assert skill_content_hash(base) == skill_content_hash(later)
    edited = base.model_copy(
        update={"references": [SkillReference(path="references/a.md", characters=3, content="abd")]}
    )
    assert skill_content_hash(edited) != skill_content_hash(base)
    pinned = pin_skill(base)
    assert pinned.references[0].path == "references/a.md" and pinned.references[0].sha256
    # 固定した定義は参照文書の本文を持たない（本文は今のスキルから読む）。
    assert pinned.definition().references[0].content == ""


def test_resolve_skill_uses_the_pins_of_the_run(agent: None) -> None:
    """実行の固定があれば固定した中身、固定に無いスキルは使わない、無ければ今のスキル。"""
    del agent
    pinned = pin_skill(_skill(OLD_INSTRUCTIONS, [LOOKUP]))
    skill_registry.upsert_custom(_skill(NEW_INSTRUCTIONS, [EXPORT]))
    token = use_pinned_skills([pinned])
    try:
        found = resolve_skill(SKILL_ID)
        assert found is not None and found.instructions == OLD_INSTRUCTIONS
        assert resolve_skill("other-skill") is None
        assert skill_reference_keys([SKILL_ID]) == []
    finally:
        reset_pinned_skills(token)
    current = resolve_skill(SKILL_ID)
    assert current is not None and current.instructions == NEW_INSTRUCTIONS
