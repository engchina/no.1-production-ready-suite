"""SQL結果の行列の対応を、回答の数値転記から分離する。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from test_answer_evidence import _step

from app.features.agent.answer_data import DataReferenceError, data_index, model_data_references
from app.features.agent.runtime import RunStep


def _output(step: RunStep) -> dict[str, Any]:
    assert step.tool_result is not None
    assert isinstance(step.tool_result.output, dict)
    return step.tool_result.output


def test_table_keeps_rows_columns_null_decimal_units_and_sorting() -> None:
    step = _step(
        "nl2sql_query",
        {
            "status": "done",
            "rows": [
                {"ID": "B", "値(%)": 51.25, "任意": None, "件数": 0},
                {"ID": "A", "値(%)": 55.0, "任意": "x|y\nz", "件数": 3},
            ],
        },
    )
    assert step.tool_result is not None and isinstance(_output(step), dict)
    before = deepcopy(step)
    refs = model_data_references(step.id, _output(step))
    assert refs
    rendered = data_index([step]).render(refs["table"])
    assert rendered == (
        "| ID | 値(%) | 任意 | 件数 |\n| --- | --- | --- | --- |\n"
        "| B | 51.25 | NULL | 0 |\n| A | 55.0 | x\\|y z | 3 |"
    )
    assert data_index([step]).render(refs["cells"][0]["値(%)"] + "%") == "51.25%"
    assert data_index([step]).render(refs["cells"][1]["件数"]) == "3"
    assert step == before


def test_retyped_numeric_table_is_not_published_even_if_value_exists_in_another_row() -> None:
    step = _step(
        "nl2sql_query",
        {"status": "done", "rows": [{"ID": "B", "VALUE": 51}, {"ID": "A", "VALUE": 55}]},
    )
    with pytest.raises(DataReferenceError):
        data_index([step]).render("| ID | VALUE |\n|---|---|\n| B | 55 |")


@pytest.mark.parametrize(
    "answer",
    [
        "[[cell:000000000000000000000000]]",
        "[[cell:bad]]",
        "prefix [[table:000000000000000000000000]]",
    ],
)
def test_unknown_and_malformed_references_fail_closed(answer: str) -> None:
    with pytest.raises(DataReferenceError):
        data_index([]).render(answer)


def test_failed_or_unfinished_results_are_not_sources() -> None:
    failed = _step("nl2sql_query", {"status": "error", "rows": [{"VALUE": 51}]})
    pending = _step("nl2sql_query", {"status": "running", "rows": [{"VALUE": 51}]})
    failed_transport = _step(
        "nl2sql_query", {"status": "done", "rows": [{"VALUE": 51}]}, success=False
    )
    assert not data_index([failed, pending, failed_transport]).tables
    assert data_index([]).render("一般の回答") == "一般の回答"


def test_references_do_not_cross_runs_or_result_revisions() -> None:
    first = _step("nl2sql_query", {"status": "done", "rows": [{"VALUE": 51}]})
    assert first.tool_result is not None and isinstance(_output(first), dict)
    second = first.model_copy(deep=True)
    assert second.tool_result is not None and isinstance(_output(second), dict)
    _output(second)["rows"][0]["VALUE"] = 55
    refs = model_data_references(first.id, _output(first))
    assert refs
    with pytest.raises(DataReferenceError):
        data_index([second]).render(refs["table"])


@pytest.mark.parametrize("rewrite_first", [False, True, None])
def test_runtime_uses_saved_result_and_recovers_from_transcription(
    monkeypatch: pytest.MonkeyPatch,
    rewrite_first: bool | None,
) -> None:
    import json

    import anyio
    from agents.testing import ModelStep, assistant_message, function_call
    from answer_run_fixtures import USER_UUID, _script
    from mcp_support import fake_product_mcp

    from app.features.agent import builtin_runtime
    from app.features.agent.runtime import (
        AgentProfile,
        RunCreateRequest,
        run_answer_text,
        runtime_repository,
    )
    from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry

    fake = fake_product_mcp(monkeypatch)
    existing = fake.outputs["nl2sql_query"]
    assert isinstance(existing, dict)
    fake.outputs["nl2sql_query"] = {
        **existing,
        "status": "done",
        "columns": ["ID", "VALUE", "UNIT"],
        "rows": [{"ID": "B", "VALUE": 51.25, "UNIT": "%"}, {"ID": "A", "VALUE": 55, "UNIT": "%"}],
    }
    skill_id, agent_id = "test-data-reference-skill", "test-data-reference-agent"
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=skill_id,
            name="検証用",
            instructions="データを照会して表で答える。",
            mcp_requirements=[SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query"])],
        )
    )
    runtime_repository.create_agent(AgentProfile(id=agent_id, name="検証用", skill_ids=[skill_id]))

    def respond(call: Any) -> Any:
        inputs = call.input
        payloads = [
            json.loads(item["output"])
            for item in inputs
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        refs = payloads[-1]["answer_data"]
        assert refs["table"] == "[[table:T1]]"
        assert refs["cells"][0]["VALUE"] == "[[cell:T1:R1:C2]]"
        text = "結果\n\n" + refs["table"] + "\n\n値は" + refs["cells"][0]["VALUE"] + "%。"
        if rewrite_first:
            text = json.dumps({"patches": [{"target_id": "P1", "replacement": text}]})
        return [assistant_message(text)]

    script: list[Any] = [
        [function_call("nl2sql__nl2sql_query", {"question": "数値を表示"}, call_id="data-call")]
    ]
    if rewrite_first is not False:
        script.append([assistant_message("| ID | VALUE |\n|---|---|\n| B | 55 |")])
    if rewrite_first is None:
        script.append(
            [
                assistant_message(
                    json.dumps(
                        {
                            "patches": [
                                {
                                    "target_id": "P1",
                                    "replacement": "| ID | VALUE |\n|---|---|\n| B | 55 |",
                                }
                            ]
                        }
                    )
                )
            ]
        )
    else:
        script.append(ModelStep.respond(respond))
    model = _script(monkeypatch, *script)
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="数値を表で表示", agent_id=agent_id), created_by_user_uuid=USER_UUID
    )
    try:
        anyio.run(builtin_runtime.execute_run, run.id)
        actual = runtime_repository.get_run(run.id)
        if rewrite_first is None:
            assert actual.status.value == "failed"
            assert actual.events[-1].payload["error_code"] == "runtime.answer_data_invalid"
            assert run_answer_text(actual) is None
        else:
            assert actual.status.value == "completed", actual.events[-1].model_dump_json()
            answer = run_answer_text(actual)
            assert answer is not None
            assert "| B | 51.25 | % |" in answer
            assert "値は51.25%。" in answer
            assert "| B | 55 |" not in answer
            assert "[[cell:" not in answer and "[[table:" not in answer
        assert len(actual.steps) == 1
        if rewrite_first is not False:
            assert not model.calls[-1].tools
        model.assert_complete()
    finally:
        runtime_repository.delete_agent(agent_id)
        skill_registry.remove(skill_id)


def test_mixed_row_cell_markers_in_a_handwritten_table_are_rejected() -> None:
    step = _step(
        "nl2sql_query",
        {"status": "done", "rows": [{"ID": "B", "VALUE": 51}, {"ID": "A", "VALUE": 55}]},
    )
    assert step.tool_result is not None and isinstance(_output(step), dict)
    refs = model_data_references(step.id, _output(step))
    assert refs
    with pytest.raises(DataReferenceError):
        data_index([step]).render(
            "| ID | VALUE |\n|---|---|\n| B | " + refs["cells"][1]["VALUE"] + " |"
        )


def test_select_column_order_and_empty_result_are_preserved() -> None:
    for rows in [[{"A": 1, "B": 2}], []]:
        step = _step("nl2sql_query", {"status": "done", "columns": ["B", "A"], "rows": rows})
        assert step.tool_result is not None and isinstance(_output(step), dict)
        refs = model_data_references(step.id, _output(step))
        assert refs
        text = data_index([step]).render(refs["table"])
        assert text.startswith("| B | A |")
        if rows:
            assert "| 2 | 1 |" in text
        else:
            assert len(text.splitlines()) == 2


def test_short_references_survive_compaction_and_state_roundtrip() -> None:
    import json

    from app.features.agent.answer_data import REGISTRY_KEY, register_data_result
    from app.features.agent.context_window import OLDER_MAX_LEVEL, compact_output
    from app.features.agent.runtime import RunState, RunStatus

    first = _step(
        "nl2sql_query",
        {
            "status": "done",
            "columns": ["B", "A"],
            "rows": [{"A": 51.25, "B": None}, {"A": 0, "B": "x"}],
            "detail": "合成の長い説明" * 2000,
        },
    )
    second = first.model_copy(deep=True, update={"id": "step-second"})
    run = RunState(
        id=first.run_id,
        goal="合成の検証",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.WAITING_APPROVAL,
        steps=[first, second],
    )
    for step in run.steps:
        register_data_result(run.metadata, step.id, _output(step))
    first_refs = model_data_references(first.id, _output(first), metadata=run.metadata)
    second_refs = model_data_references(second.id, _output(second), metadata=run.metadata)
    assert first_refs is not None and second_refs is not None
    assert first_refs["table"] == "[[table:T1]]"
    assert second_refs["table"] == "[[table:T2]]"
    assert first_refs["cells"][0]["A"] == "[[cell:T1:R1:C2]]"
    output = json.dumps({**_output(second), "answer_data": second_refs}, ensure_ascii=False)
    compacted = json.loads(compact_output(output, OLDER_MAX_LEVEL))
    assert compacted["answer_data"]["table"] == "[[table:T2]]"
    restored = RunState.model_validate_json(run.model_dump_json())
    index = data_index(restored.steps, restored.metadata)
    assert index.render("[[cell:T1:R1:C2]]%") == "51.25%"
    assert index.render("[[cell:T2:R1:C1]]") == "NULL"
    assert index.render("[[cell:T2:R2:C2]]") == "0"
    assert "| B | A |" in index.render(compacted["answer_data"]["table"])
    assert all(len(entry["key"]) == 24 for entry in restored.metadata[REGISTRY_KEY])
    before = deepcopy(restored.metadata)
    register_data_result(restored.metadata, first.id, _output(first))
    assert restored.metadata == before
    partial = data_index([restored.steps[1]], restored.metadata)
    assert partial.render("[[cell:T2:R1:C2]]") == "51.25"
    with pytest.raises(DataReferenceError):
        partial.render("[[table:T1]]")


@pytest.mark.parametrize(
    "reference",
    [
        "[[cell:T1:R0:C1]]",
        "[[cell:T1:R3:C1]]",
        "[[cell:T1:R1:C3]]",
        "[[cell:T2:R1:C1]]",
        "[[table:T0]]",
        "[[cell:T1]]",
        "[[table:T1:R1:C1]]",
    ],
)
def test_short_unknown_and_out_of_bounds_fail_closed(reference: str) -> None:
    from app.features.agent.answer_data import register_data_result

    step = _step("nl2sql_query", {"status": "done", "rows": [{"A": 1, "B": 2}]})
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, _output(step))
    with pytest.raises(DataReferenceError):
        data_index([step], metadata).render(reference)


def test_short_reference_rejects_changed_result_and_other_execution() -> None:
    from app.features.agent.answer_data import register_data_result

    step = _step("nl2sql_query", {"status": "done", "rows": [{"VALUE": 51}]})
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, _output(step))
    changed = step.model_copy(deep=True)
    _output(changed)["rows"][0]["VALUE"] = 55
    with pytest.raises(DataReferenceError):
        data_index([changed], metadata).render("[[cell:T1:R1:C1]]")
    with pytest.raises(DataReferenceError):
        data_index([step], {}).render("[[cell:T1:R1:C1]]")


def test_short_registry_is_saved_by_checkpoint_and_new_results_continue_numbering() -> None:
    from checkpoint_support import CheckpointTable

    from app.features.agent.answer_data import register_data_result
    from app.features.agent.runtime import (
        AgentRuntimeOracleCheckpointRepository,
        RunState,
        RunStatus,
    )
    from app.features.agent.tools import ToolCall, ToolResult

    step = _step("nl2sql_query", {"status": "done", "rows": [{"VALUE": 51.25}]})
    run = RunState(
        id=step.run_id,
        goal="合成の検証",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.RUNNING,
        steps=[step],
    )
    register_data_result(run.metadata, step.id, _output(step))
    table = CheckpointTable()
    table.seed([run], [])
    process = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    step_id, _ = process.start_builtin_tool_step(
        run.id, ToolCall(name="nl2sql__nl2sql_query", arguments={})
    )
    process.finish_builtin_tool_step(
        run.id,
        step_id,
        ToolResult(
            name="nl2sql__nl2sql_query",
            success=True,
            output={"status": "done", "rows": [{"VALUE": 0}]},
        ),
    )
    process.flush_writes()
    restored = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect).get_run(run.id)
    index = data_index(restored.steps, restored.metadata)
    assert index.render("[[cell:T1:R1:C1]]%") == "51.25%"
    assert index.render("[[cell:T2:R1:C1]]") == "0"
