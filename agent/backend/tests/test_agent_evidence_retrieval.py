"""Agent が編成する根拠の取得の決定論テスト。

- 保存済みの `evidence_planning`・SKILL.md の metadata `production-ready-evidence-planning` は
  読むときに無視する（移行しない）。
- 画像を読めないモデルには `rag_read_source` の `include_image` を見せない（本文は読める）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from agents import FunctionTool
from agents.testing import ScriptedModel, assistant_message
from mcp_support import DEFAULT_OUTPUTS, FakeProductMcp, fake_product_mcp
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    runtime_repository,
)
from app.features.agent.skills import (
    AgentSkillDefinition,
    SkillMcpRequirement,
    skill_registry,
)
from app.features.agent.skills_loader import load_skills_from_dir
from app.features.agent.support_task import image_input_schema

SKILL_ID = "test5125-skill"
AGENT_ID = "test5125-agent"
USER_UUID = "51255125-2222-3333-4444-555555555555"
GOAL = "機器 A の点検の周期と、点検の画面の確定ボタンの位置は？"
ANSWER = "機器 A の点検は毎月 1 回です。確定ボタンは画面の右下にあります。"
REMOVED_ARGUMENTS = frozenset({"planning", "queries", "request_id", "original_question"})

_TEXT = {
    **DEFAULT_OUTPUTS["rag_search"]["evidence"][0],
    "evidence_id": "chunk-text",
    "chunk_id": "chunk-text",
    "file_name": "点検の手順.pdf",
    "excerpt": "機器 A の点検は毎月 1 回行う。",
    "text_length": 15,
    "retrieval_rank": 1,
}
_TABLE = {
    **_TEXT,
    "evidence_id": "chunk-table",
    "chunk_id": "chunk-table",
    "content_kind": "table",
    "evidence_type": "table",
    "excerpt": "| 機器 | 周期 |\n| A | 毎月 |",
    "retrieval_rank": 2,
}
_FIGURE = {
    **_TEXT,
    "evidence_id": "chunk-figure",
    "chunk_id": "chunk-figure",
    "content_kind": "figure",
    "evidence_type": "figure_description",
    "excerpt": "点検の画面の図。確定のボタンがある。",
    "retrieval_rank": 3,
    "image_ref": {
        "document_id": "doc-1",
        "chunk_id": "chunk-figure",
        "chunk_set_id": "cs-1",
        "page": 2,
        "page_label": None,
        "bbox": [10.0, 20.0, 110.0, 220.0],
        "bbox_unit": "absolute",
    },
}


def _skill() -> AgentSkillDefinition:
    return AgentSkillDefinition(
        id=SKILL_ID,
        name="根拠の取得の Skill",
        instructions="rag_search で根拠を集め、根拠から答える。",
        mcp_requirements=[
            SkillMcpRequirement(server_id="rag", tool_names=["rag_search", "rag_read_source"])
        ],
    )


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(monkeypatch)
    skill_registry.upsert_custom(_skill())
    runtime_repository.create_agent(
        AgentProfile(
            id=AGENT_ID, name="根拠の取得の業務 Agent", skill_ids=[SKILL_ID], versioned=True
        )
    )
    try:
        yield fake
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


def _script(monkeypatch: MonkeyPatch, *steps: Any, vision: bool = False) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
            vision_enabled=vision,
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _run(goal: str = GOAL) -> str:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=AGENT_ID, draft=True),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return run.id


def _tools(model: ScriptedModel) -> dict[str, FunctionTool]:
    return {tool.name: tool for tool in model.calls[0].tools if isinstance(tool, FunctionTool)}


def test_saved_evidence_planning_is_ignored() -> None:
    # 移行しない保存済みの値は、読むときに無視する（#5125）。
    requirement = SkillMcpRequirement.model_validate(
        {"server_id": "rag", "tool_names": ["rag_search"], "evidence_planning": "caller"}
    )
    assert "evidence_planning" not in requirement.model_dump()


def test_skill_md_evidence_planning_metadata_is_ignored(tmp_path: Any) -> None:
    folder = tmp_path / "evidence-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: evidence-skill\ndescription: 根拠を集めるときに使う。\nmetadata:\n"
        '  production-ready-mcp-tools: "rag:rag_search nl2sql"\n'
        '  production-ready-evidence-planning: "rag:caller"\n'
        "---\n本文。\n",
        encoding="utf-8",
    )
    [skill] = load_skills_from_dir(str(tmp_path))
    assert [item.model_dump() for item in skill.mcp_requirements] == [
        {"server_id": "rag", "tool_names": ["rag_search"]},
        {"server_id": "nl2sql", "tool_names": []},
    ]


@pytest.mark.parametrize(
    "skill_id",
    ["rag-chat", "rag-search", "rag-chat-and-data", "rag-search-and-data"],
)
def test_builtin_rag_skills_have_no_evidence_planning(skill_id: str) -> None:
    skill = skill_registry.get(skill_id)
    assert skill is not None
    assert all("evidence_planning" not in item.model_dump() for item in skill.mcp_requirements)


@pytest.mark.parametrize("vision", [False, True], ids=["text-only", "vision"])
def test_read_source_image_argument_follows_the_model(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, vision: bool
) -> None:
    """画像を読めないモデルには include_image を見せない（本文の読み取りは残す。#5125）。"""
    model = _script(monkeypatch, [assistant_message(ANSWER)], vision=vision)
    _run()

    params = _tools(model)["rag__rag_read_source"].params_json_schema
    assert "chunk_id" in params["properties"]
    assert ("include_image" in params["properties"]) is vision


def test_image_input_schema_only_changes_read_source() -> None:
    schema = {
        "type": "object",
        "properties": {"chunk_id": {"type": "string"}, "include_image": {"type": "boolean"}},
        "required": ["chunk_id", "include_image"],
    }
    visible = image_input_schema("rag__rag_read_source", schema)
    assert visible["properties"] == {"chunk_id": {"type": "string"}}
    assert visible["required"] == ["chunk_id"]
    assert image_input_schema("rag__rag_search", schema) is schema
