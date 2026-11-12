"""参照行列と単位付き値が、保存済み位置からだけ表示されることを確かめる。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from test_answer_evidence import _step

from app.features.agent.answer_data import (
    DataReferenceError,
    data_index,
    model_data_references,
    register_data_result,
)
from app.features.agent.runtime import RunState, RunStatus


def _fixture() -> tuple[Any, dict[str, Any], dict[str, Any]]:
    output = {
        "status": "done",
        "columns": ["LABEL", "ORDER", "CHANGE"],
        "rows": [{"LABEL": f"record-{i}", "ORDER": i, "CHANGE": i / 4} for i in range(1, 11)],
        "column_metadata": [
            {"name": "LABEL", "unit": None, "unit_status": "unknown"},
            {"name": "ORDER", "unit": None, "unit_status": "unknown"},
            {
                "name": "CHANGE",
                "unit": "パーセントポイント",
                "unit_status": "known",
                "provenance": ["reviewed-source"],
            },
        ],
    }
    step = _step("nl2sql_query", output)
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, output)
    refs = model_data_references(step.id, output, metadata=metadata)
    assert refs is not None
    return step, metadata, refs


def _matrix(refs: dict[str, Any], rows: list[int]) -> str:
    return "| 表示順 | 名称 | 差 |\n|---|---|---|\n" + "\n".join(
        "| "
        + " | ".join(
            [refs["cells"][i]["ORDER"], refs["cells"][i]["LABEL"], refs["quantities"][i]["CHANGE"]]
        )
        + " |"
        for i in rows
    )


def test_projection_keeps_selected_eight_rows_instead_of_replacing_with_whole_table() -> None:
    step, metadata, refs = _fixture()
    index = data_index([step], metadata)
    selected = index.render("選択した8行\n" + _matrix(refs, list(range(8))))
    assert selected.startswith("選択した8行\n| ORDER | LABEL | CHANGE (パーセントポイント) |")
    assert "record-8" in selected and "record-9" not in selected and "record-10" not in selected
    assert selected.count("\n|") == 10
    whole = index.render(refs["table"])
    assert "record-10" in whole
    reordered = index.render(_matrix(refs, [7, 0]))
    assert reordered.index("record-8") < reordered.index("record-1")


def test_known_unit_is_rendered_by_program_and_raw_cell_requires_quantity() -> None:
    step, metadata, refs = _fixture()
    index = data_index([step], metadata)
    assert index.render(refs["quantities"][0]["CHANGE"]) == "0.25 パーセントポイント"
    with pytest.raises(DataReferenceError, match="quantities"):
        index.render(refs["cells"][0]["CHANGE"] + "%")
    assert index.render(refs["cells"][0]["ORDER"]) == "1"
    with pytest.raises(DataReferenceError):
        index.render("[[quantity:T1:R1:C2]]")


@pytest.mark.parametrize(
    "kind", ["mixed_row", "mixed_table", "shifted_column", "literal", "duplicate_row", "malformed"]
)
def test_matrix_rejects_values_that_do_not_match_one_stable_projection(kind: str) -> None:
    step, metadata, refs = _fixture()
    other = step.model_copy(deep=True, update={"id": "another-step"})
    register_data_result(metadata, other.id, other.tool_result.output)
    matrix = _matrix(refs, [0, 1])
    if kind == "mixed_row":
        matrix = matrix.replace(refs["cells"][0]["LABEL"], refs["cells"][1]["LABEL"])
    elif kind == "mixed_table":
        matrix = matrix.replace(refs["cells"][0]["LABEL"], "[[cell:T2:R1:C1]]")
    elif kind == "shifted_column":
        matrix = matrix.replace(refs["cells"][1]["LABEL"], refs["cells"][1]["ORDER"])
    elif kind == "literal":
        matrix = matrix.replace(refs["cells"][0]["ORDER"], "1")
    elif kind == "duplicate_row":
        matrix = _matrix(refs, [0, 0])
    else:
        matrix = matrix.replace(refs["cells"][0]["LABEL"], "[[cell:T1:R0:C1]]")
    with pytest.raises(DataReferenceError):
        data_index([step, other], metadata).render(matrix)


def test_units_are_bound_to_result_revision_and_survive_snapshot_and_compaction() -> None:
    from app.features.agent.context_window import OLDER_MAX_LEVEL, compact_output

    step, metadata, refs = _fixture()
    run = RunState(
        id=step.run_id,
        goal="合成データの検証",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.WAITING_APPROVAL,
        steps=[step],
        metadata=metadata,
    )
    restored = RunState.model_validate_json(run.model_dump_json())
    compact_output(json.dumps({**step.tool_result.output, "answer_data": refs}), OLDER_MAX_LEVEL)
    assert (
        data_index(restored.steps, restored.metadata).render(refs["quantities"][1]["CHANGE"])
        == "0.5 パーセントポイント"
    )
    changed = deepcopy(step)
    changed.tool_result.output["column_metadata"][2]["unit"] = "%"
    with pytest.raises(DataReferenceError):
        data_index([changed], metadata).render(refs["quantities"][1]["CHANGE"])


def test_null_unknown_units_legacy_snapshots_and_source_text_are_not_reinterpreted() -> None:
    step, metadata, refs = _fixture()
    step.tool_result.output["rows"][0]["CHANGE"] = None
    step.tool_result.output["rows"][0]["LABEL"] = "[[cell:T1:R2:C2]]"
    metadata = {}
    register_data_result(metadata, step.id, step.tool_result.output)
    refs = model_data_references(step.id, step.tool_result.output, metadata=metadata)
    index = data_index([step], metadata)
    assert index.render(refs["quantities"][0]["CHANGE"]) == "NULL"
    rendered = index.render(_matrix(refs, [0]))
    assert "[[cell:T1:R2:C2]]" in rendered
    legacy = _step("nl2sql_query", {"status": "done", "rows": [{"X": 0.25}]})
    oldrefs = model_data_references(legacy.id, legacy.tool_result.output)
    assert "quantities" not in oldrefs
    assert data_index([legacy]).render(oldrefs["cells"][0]["X"] + "%") == "0.25%"


@pytest.mark.parametrize("change", ["order", "missing", "empty_unit"])
def test_inconsistent_metadata_cannot_silently_drop_known_units(change: str) -> None:
    step, metadata, _refs = _fixture()
    output = step.tool_result.output
    if change == "order":
        output["column_metadata"].reverse()
    elif change == "missing":
        output["column_metadata"].pop()
    else:
        output["column_metadata"][2]["unit"] = ""
    with pytest.raises(DataReferenceError):
        model_data_references(step.id, output, metadata=metadata)


def test_compaction_keeps_typed_reference_space_and_original_row_numbers() -> None:
    from app.features.agent.context_window import OLDER_MAX_LEVEL, compact_output

    step, metadata, refs = _fixture()
    source = {**step.tool_result.output, "answer_data": refs, "detail": "長い説明" * 2000}
    for level in [2, OLDER_MAX_LEVEL]:
        compacted = json.loads(compact_output(json.dumps(source), level))
        assert compacted["column_metadata"][2] == {
            "name": "CHANGE",
            "unit": "パーセントポイント",
            "unit_status": "known",
        }
        assert compacted["answer_data"]["table"] == "[[table:T1]]"
        assert compacted["answer_data"]["row_count"] == 10
        assert compacted["answer_data"]["columns"] == ["LABEL", "ORDER", "CHANGE"]
        assert compacted["answer_data"]["quantity_columns"] == {
            "CHANGE": {"column": 3, "unit": "パーセントポイント"}
        }
        assert len(compacted["answer_data"]["quantities"]) == 5
        assert compacted["answer_data"]["quantities"][4]["CHANGE"] == "[[quantity:T1:R5:C3]]"
        assert (
            data_index([step], metadata).render("[[quantity:T1:R10:C3]]")
            == "2.5 パーセントポイント"
        )


def test_unknown_units_and_mcp_result_normalizer_preserve_compatibility() -> None:
    from app.features.agent.tools import parse_mcp_tool_result

    step, _metadata, _refs = _fixture()
    output = step.tool_result.output
    output["column_metadata"][2].update(unit=None, unit_status="unknown")
    parsed = parse_mcp_tool_result(
        {"structuredContent": output}, server_id="nl2sql", tool_name="nl2sql_query"
    )
    assert parsed.output["column_metadata"] == output["column_metadata"]
    refs = model_data_references(step.id, parsed.output)
    assert "quantities" not in refs
    assert data_index([step]).render(refs["cells"][0]["CHANGE"] + "%") == "0.25%"


def test_canonical_headers_do_not_collide_with_original_column_names_or_swallow_source() -> None:
    step = _step(
        "nl2sql_query",
        {
            "status": "done",
            "columns": ["VALUE", "VALUE (%)"],
            "rows": [{"VALUE": 1, "VALUE (%)": "[[table:T9]]"}],
            "column_metadata": [
                {"name": "VALUE", "unit_status": "known", "unit": "%"},
                {"name": "VALUE (%)", "unit_status": "unknown", "unit": None},
            ],
        },
    )
    refs = model_data_references(step.id, step.tool_result.output)
    index = data_index([step])
    table = index.render(refs["table"])
    assert "| 1 | [[table:T9]] |" in table
    matrix = (
        "| 誤った見出し | 任意 |\n|---|---|\n| "
        + refs["quantities"][0]["VALUE"]
        + " | "
        + refs["cells"][0]["VALUE (%)"]
        + " |"
    )
    assert index.render(matrix) == table
    assert (
        index.render("| 説明 | 出典 |\n|---|---|\n| 文 | 原文 |\n\n後続の文")
        == "| 説明 | 出典 |\n|---|---|\n| 文 | 原文 |\n\n後続の文"
    )


@pytest.mark.parametrize("repair_first", [False, True])
def test_runtime_keeps_selected_rows_and_repairs_only_typed_reference_format(
    monkeypatch: pytest.MonkeyPatch, repair_first: bool
) -> None:
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

    sample, _metadata, _refs = _fixture()
    fake = fake_product_mcp(monkeypatch)
    fake.outputs["nl2sql_query"] = {
        **fake.outputs["nl2sql_query"],
        **sample.tool_result.output,
        "returned_count": 10,
        "total": 10,
    }
    skill_id, agent_id = "typed-reference-skill", "typed-reference-agent"
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=skill_id,
            name="合成検証",
            instructions="保存済みの参照で回答する。",
            mcp_requirements=[SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query"])],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=agent_id, name="合成検証", skill_ids=[skill_id])
    )

    def respond(call: Any, *, invalid: bool = False) -> list[Any]:
        outputs = [
            json.loads(item["output"])
            for item in call.input
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        refs = outputs[-1]["answer_data"]
        quantity = refs["cells"][0]["CHANGE"] + "%" if invalid else refs["quantities"][0]["CHANGE"]
        return [
            assistant_message(
                "選択した行\n" + _matrix(refs, list(range(8))) + "\n\n値は" + quantity + "。"
            )
        ]

    steps: list[Any] = [
        [
            function_call(
                "nl2sql__nl2sql_query", {"question": "合成データを表示"}, call_id="typed-query"
            )
        ]
    ]
    if repair_first:
        steps.append(ModelStep.respond(lambda call: respond(call, invalid=True)))
    steps.append(ModelStep.respond(respond))
    model = _script(monkeypatch, *steps)
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="選択した8行と値を表示", agent_id=agent_id),
        created_by_user_uuid=USER_UUID,
    )
    try:
        anyio.run(builtin_runtime.execute_run, run.id)
        actual = runtime_repository.get_run(run.id)
        assert actual.status.value == "completed", actual.events[-1].model_dump_json()
        answer = run_answer_text(actual)
        assert "record-8" in answer and "record-9" not in answer and "record-10" not in answer
        assert "値は0.25 パーセントポイント。" in answer
        assert len(actual.steps) == 1
        if repair_first:
            assert not model.calls[-1].tools
        model.assert_complete()
    finally:
        runtime_repository.delete_agent(agent_id)
        skill_registry.remove(skill_id)


def test_hashed_quantity_reference_is_bound_to_unit_metadata_revision() -> None:
    step, _metadata, _refs = _fixture()
    refs = model_data_references(step.id, step.tool_result.output)
    revised = deepcopy(step)
    revised.tool_result.output["column_metadata"][2]["unit"] = "%"
    with pytest.raises(DataReferenceError):
        data_index([revised]).render(refs["quantities"][0]["CHANGE"])


@pytest.mark.parametrize(
    "value", ["9007199254740993123456789", "-9007199254740993123456789", "1.2300", "1.23E+100"]
)
def test_exact_numeric_strings_from_result_serialization_keep_units_without_float_conversion(
    value: str,
) -> None:
    step, _metadata, _refs = _fixture()
    step.tool_result.output["rows"][0]["CHANGE"] = value
    refs = model_data_references(step.id, step.tool_result.output)
    assert (
        data_index([step]).render(refs["quantities"][0]["CHANGE"]) == value + " パーセントポイント"
    )
    with pytest.raises(DataReferenceError):
        data_index([step]).render(refs["cells"][0]["CHANGE"])


def test_decimal_and_big_integer_roundtrip_keeps_exact_quantity_text() -> None:
    from decimal import Decimal

    for value in [Decimal("1.23000000000000000001"), "9007199254740993123456789"]:
        step, _metadata, _refs = _fixture()
        step.tool_result.output["rows"][0]["CHANGE"] = value
        metadata: dict[str, Any] = {}
        register_data_result(metadata, step.id, step.tool_result.output)
        refs = model_data_references(step.id, step.tool_result.output, metadata=metadata)
        expected = str(value) + " パーセントポイント"
        assert data_index([step], metadata).render(refs["quantities"][0]["CHANGE"]) == expected
        run = RunState(
            id=step.run_id,
            goal="合成精度検証",
            agent_id="default",
            runtime_id="builtin",
            status=RunStatus.WAITING_APPROVAL,
            steps=[step],
            metadata=metadata,
        )
        restored = RunState.model_validate_json(run.model_dump_json())
        assert (
            data_index(restored.steps, restored.metadata).render(refs["quantities"][0]["CHANGE"])
            == expected
        )


@pytest.mark.parametrize("value", [True, False, "001", "1 unit", "", "NaN"])
def test_non_numeric_strings_and_booleans_do_not_become_quantities(value: Any) -> None:
    step, _metadata, _refs = _fixture()
    step.tool_result.output["rows"][0]["CHANGE"] = value
    refs = model_data_references(step.id, step.tool_result.output)
    assert "CHANGE" not in refs["quantities"][0]
