"""SQL結果の行列の対応を、回答の数値転記から分離する。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from test_answer_evidence import _step

from app.features.agent.answer_data import DataReferenceError, data_index, model_data_references


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
    assert step.tool_result is not None and isinstance(step.tool_result.output, dict)
    before = deepcopy(step)
    refs = model_data_references(step.id, step.tool_result.output)
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
    assert first.tool_result is not None and isinstance(first.tool_result.output, dict)
    second = first.model_copy(deep=True)
    assert second.tool_result is not None and isinstance(second.tool_result.output, dict)
    second.tool_result.output["rows"][0]["VALUE"] = 55
    refs = model_data_references(first.id, first.tool_result.output)
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
        return [
            assistant_message(
                "結果\n\n" + refs["table"] + "\n\n値は" + refs["cells"][0]["VALUE"] + "%。"
            )
        ]

    script: list[Any] = [
        [function_call("nl2sql__nl2sql_query", {"question": "数値を表示"}, call_id="data-call")]
    ]
    if rewrite_first is not False:
        script.append([assistant_message("| ID | VALUE |\n|---|---|\n| B | 55 |")])
    if rewrite_first is None:
        script.append([assistant_message("| ID | VALUE |\n|---|---|\n| B | 55 |")])
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
            assert actual.status.value == "completed", actual.events[-1]
            answer = run_answer_text(actual)
            assert answer is not None
            assert "| B | 51.25 | % |" in answer
            assert "値は51.25%。" in answer
            assert "| B | 55 |" not in answer
            assert "[[cell:" not in answer and "[[table:" not in answer
        model.assert_complete()
    finally:
        runtime_repository.delete_agent(agent_id)
        skill_registry.remove(skill_id)


def test_mixed_row_cell_markers_in_a_handwritten_table_are_rejected() -> None:
    step = _step(
        "nl2sql_query",
        {"status": "done", "rows": [{"ID": "B", "VALUE": 51}, {"ID": "A", "VALUE": 55}]},
    )
    assert step.tool_result is not None and isinstance(step.tool_result.output, dict)
    refs = model_data_references(step.id, step.tool_result.output)
    assert refs
    with pytest.raises(DataReferenceError):
        data_index([step]).render(
            "| ID | VALUE |\n|---|---|\n| B | " + refs["cells"][1]["VALUE"] + " |"
        )


def test_select_column_order_and_empty_result_are_preserved() -> None:
    for rows in [[{"A": 1, "B": 2}], []]:
        step = _step("nl2sql_query", {"status": "done", "columns": ["B", "A"], "rows": rows})
        assert step.tool_result is not None and isinstance(step.tool_result.output, dict)
        refs = model_data_references(step.id, step.tool_result.output)
        assert refs
        text = data_index([step]).render(refs["table"])
        assert text.startswith("| B | A |")
        if rows:
            assert "| 2 | 1 |" in text
        else:
            assert len(text.splitlines()) == 2
