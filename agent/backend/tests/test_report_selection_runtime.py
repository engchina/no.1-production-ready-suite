"""公開Agentのidentityと、Runに実在する回答案の選択を接続する。"""

from __future__ import annotations

import json

import pytest
from agents import Model
from agents.testing import assistant_message, function_call
from test_referenced_report_runtime import contract, create, draft, run, script

from app.features.agent import builtin_runtime, runtime
from app.features.agent.report_selection import ReportSelectionModel
from app.features.agent.runtime import AgentRuntimeRepository, RunCreateRequest, RunStatus


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> AgentRuntimeRepository:
    value = AgentRuntimeRepository()
    monkeypatch.setattr(runtime, "runtime_repository", value)
    return value


def test_report_requires_a_real_draft_then_exposes_only_saved_selection(
    repository: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = create(repository, contract())
    model = script(
        monkeypatch,
        [function_call("draft_report", json.dumps(draft("固定した本文")), call_id="draft-a")],
        [assistant_message('{"preview_id":"A1"}')],
    )
    result = run(repository, agent)
    assert result.status == RunStatus.COMPLETED
    assert len(result.steps) == 1 and len(model.calls) == 2
    assert model.calls[0].output_schema is None
    assert model.calls[0].model_settings.tool_choice == "required"
    selected = model.calls[1].output_schema
    assert selected is not None
    assert selected.json_schema()["properties"]["preview_id"]["enum"] == ["A1"]


def test_noncompliant_first_selector_still_fails_closed_without_replaying_tools(
    repository: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = create(repository, contract())
    model = script(monkeypatch, [assistant_message('{"preview_id":"A1"}')])
    result = run(repository, agent)
    assert result.status == RunStatus.FAILED and result.steps == []
    assert len(model.calls) == 1
    assert model.calls[0].output_schema is None
    assert model.calls[0].model_settings.tool_choice == "required"
    assert not any(artifact.kind == "answer" for artifact in result.artifacts)
    assert result.events[-1].payload["error_code"] == "runtime.report_incomplete"


def test_only_execution_config_wraps_report_model_and_plain_mode_is_unchanged(
    repository: AgentRuntimeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = script(monkeypatch)
    agent = create(repository, contract())
    item = repository.create_builtin_run(RunCreateRequest(goal="質問", agent_id=agent))
    report = builtin_runtime.build_sdk_agent(
        item.id,
        name="合成",
        instructions="",
        skill_ids=[],
        model_id="scripted",
        output_contract=contract(),
    )
    plain = builtin_runtime.build_sdk_agent(
        item.id, name="合成", instructions="", skill_ids=[], model_id="scripted"
    )
    assert report.model is model and plain.model is model and isinstance(model, Model)
    assert isinstance(builtin_runtime._run_config(item.id, report).model, ReportSelectionModel)
    assert builtin_runtime._run_config(item.id, plain).model is None
