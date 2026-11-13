"""短い位置指定の表・転置を保存値から展開し、形式不正は拒否する。"""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest
from test_answer_data_typed import _fixture, _output
from test_answer_evidence import _step

from app.features.agent.answer_data import (
    DATA_INSTRUCTIONS,
    DataReferenceError,
    data_index,
    model_data_references,
    register_data_result,
)
from app.features.agent.context_window import OLDER_MAX_LEVEL, compact_output
from app.features.agent.runtime import RunState, RunStatus


def test_short_projection_preserves_eight_rows_order_and_trusted_headers() -> None:
    step, metadata, refs = _fixture()
    before = deepcopy(step)
    assert refs["column_refs"] == {"LABEL": 1, "ORDER": 2, "CHANGE": 3}
    rendered = data_index([step], metadata).render("[[table:T1:rows=1-8:cols=2,1,3]]")
    assert rendered.startswith("| ORDER | LABEL | CHANGE |")
    assert "| 8 | record-8 | 2.0 |" in rendered
    assert "record-9" not in rendered and "record-10" not in rendered
    assert len(rendered.splitlines()) == 10
    selected = data_index([step], metadata).render("[[table:T1:rows=8,1,4-5:cols=3,1]]")
    assert selected.index("record-8") < selected.index("record-1") < selected.index("record-4")
    assert step == before


def test_vertical_projection_uses_source_labels_units_and_selected_row_order() -> None:
    step, metadata, _refs = _fixture()
    index = data_index([step], metadata)
    assert index.render("[[table:T1:rows=1:cols=3,1,2:layout=vertical]]") == (
        "| 項目 | 値 |\n| --- | --- |\n| CHANGE | 0.25 |\n| LABEL | record-1 |\n| ORDER | 1 |"
    )
    assert index.render("[[table:T1:rows=3,1:cols=3,2:layout=vertical]]") == (
        "| 項目 | R3 | R1 |\n| --- | --- | --- |\n| CHANGE | 0.75 | 0.25 |\n| ORDER | 3 | 1 |"
    )
    assert index.render("[[table:T1:rows=*:cols=*:layout=horizontal]]") == index.render(
        "[[table:T1]]"
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "rows=0:cols=*",
        "rows=-1:cols=*",
        "rows=01:cols=*",
        "rows=:cols=*",
        "rows=11:cols=*",
        "rows=2-1:cols=*",
        "rows=1,1:cols=*",
        "rows=1-3,3:cols=*",
        "rows=*,1:cols=*",
        "rows=1,:cols=*",
        "rows=1:cols=0",
        "rows=1:cols=-1",
        "rows=1:cols=4",
        "rows=1:cols=3-2",
        "rows=1:cols=1,1",
        "rows=1:cols=",
        "rows=1:cols=1-3,2",
        "rows=1:cols=1,,2",
        "rows=1:cols=1 2",
        "cols=1:rows=1",
        "rows=1:cols=1:layout=unknown",
        "rows=1:cols=1:layout=",
        "rows=1:cols=1:layout=vertical:layout=horizontal",
        "rows=1-100000000000000:cols=*",
        "rows=1:cols=1-100000000000000",
        "rows=" + "9" * 5000 + ":cols=*",
        "rows=1:cols=" + "9" * 5000,
    ],
    ids=lambda value: value[:70],
)
def test_invalid_projections_fail_before_unbounded_expansion(suffix: str) -> None:
    step, metadata, _refs = _fixture()
    with pytest.raises(DataReferenceError):
        data_index([step], metadata).render("[[table:T1:" + suffix + "]]")


@pytest.mark.parametrize(
    "answer",
    [
        "prefix [[table:T1:rows=1:cols=1]]",
        "[[table:T1:rows=1:cols=1]] suffix",
        "[[table:T2:rows=1:cols=1]]",
        "[[cell:T1:rows=1:cols=1]]",
        "[[quantity:T1:rows=1:cols=1]]",
        "[[table:T1:rows=1\n:cols=1]]",
    ],
)
def test_projection_marker_identity_and_standalone_line_are_strict(answer: str) -> None:
    step, metadata, _refs = _fixture()
    with pytest.raises(DataReferenceError):
        data_index([step], metadata).render(answer)


def test_empty_result_stars_keep_headers_and_explicit_rows_are_out_of_bounds() -> None:
    step = _step("nl2sql_query", {"status": "done", "columns": ["B", "A"], "rows": []})
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, _output(step))
    index = data_index([step], metadata)
    assert index.render("[[table:T1:rows=*:cols=2,1]]") == "| A | B |\n| --- | --- |"
    assert index.render("[[table:T1:rows=*:cols=*:layout=vertical]]") == "取得した行はありません。"
    with pytest.raises(DataReferenceError):
        index.render("[[table:T1:rows=1:cols=*]]")


def test_legacy_hash_projection_and_changed_metadata_revision_do_not_alias() -> None:
    step, metadata, _refs = _fixture()
    legacy = model_data_references(step.id, _output(step))
    assert legacy is not None
    projection = legacy["table"][:-2] + ":rows=1:cols=3]]"
    assert data_index([step]).render(projection).endswith("| 0.25 |")
    _output(step)["column_metadata"][2]["unit"] = "別単位"
    with pytest.raises(DataReferenceError):
        data_index([step], metadata).render("[[table:T1:rows=1:cols=3]]")
    with pytest.raises(DataReferenceError):
        data_index([step]).render(projection)


def test_projection_does_not_parse_reference_looking_source_values_twice() -> None:
    step, _metadata, _refs = _fixture()
    _output(step)["rows"][0]["LABEL"] = "[[table:T999:rows=1:cols=1]]"
    _output(step)["rows"][1]["LABEL"] = "[[quantity:T1:R9:C3]]"
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, _output(step))
    index = data_index([step], metadata)
    assert "[[table:T999:rows=1:cols=1]]" in index.render("[[table:T1:rows=1-2:cols=1]]")
    assert "[[quantity:T1:R9:C3]]" in index.render("[[table:T1:rows=1-2:cols=1:layout=vertical]]")


def test_column_numbers_survive_compaction_and_checkpoint_restore() -> None:
    step, metadata, refs = _fixture()
    source = {**_output(step), "answer_data": refs, "detail": "合成説明" * 10000}
    for level in (1, 2, OLDER_MAX_LEVEL):
        compacted = json.loads(compact_output(json.dumps(source), level))
        assert compacted["answer_data"]["column_refs"] == refs["column_refs"]
        assert compacted["answer_data"]["columns"] == refs["columns"]
        assert compacted["answer_data"]["row_count"] == 10
    run = RunState(
        id=step.run_id,
        goal="合成検証",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.WAITING_APPROVAL,
        steps=[step],
        metadata=metadata,
    )
    restored = RunState.model_validate_json(run.model_dump_json())
    assert (
        data_index(restored.steps, restored.metadata)
        .render("[[table:T1:rows=10,1:cols=1,3:layout=vertical]]")
        .endswith("| CHANGE | 2.5 | 0.25 |")
    )


def test_projection_does_not_relax_literal_or_composite_reference_tables() -> None:
    step, metadata, _refs = _fixture()
    index = data_index([step], metadata)
    for answer in (
        "| 名称 | 値 |\n|---|---|\n| literal | [[quantity:T1:R1:C3]] |",
        "| 名称 | 値 |\n|---|---|\n| [[cell:T1:R1:C1]] | [[cell:T1:R1:C2]]–[[cell:T1:R2:C2]] |",
    ):
        with pytest.raises(DataReferenceError):
            index.render(answer)


def test_runtime_repairs_to_projection_once_without_replaying_tools(
    monkeypatch: pytest.MonkeyPatch, record_property: Callable[[str, object], None]
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
    existing = fake.outputs["nl2sql_query"]
    assert isinstance(existing, dict)
    fake.outputs["nl2sql_query"] = {**existing, **_output(sample)}
    skill_id, agent_id = "projection-skill", "projection-agent"
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=skill_id,
            name="合成検証",
            instructions="保存された表から回答する。",
            mcp_requirements=[SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query"])],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=agent_id, name="合成検証", skill_ids=[skill_id])
    )

    def respond(call: Any) -> Any:
        payloads = [
            json.loads(item["output"])
            for item in call.input
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        refs = payloads[-1]["answer_data"]
        assert refs["column_refs"]["CHANGE"] == 3
        return [
            assistant_message(
                json.dumps(
                    {
                        "patches": [
                            {
                                "target_id": "P1",
                                "replacement": "結果\n[[table:T1:rows=1-8:cols=2,1,3]]\n\n項目\n"
                                "[[table:T1:rows=1:cols=3:layout=vertical]]",
                            }
                        ]
                    }
                )
            )
        ]

    model = _script(
        monkeypatch,
        [
            function_call(
                "nl2sql__nl2sql_query", {"question": "合成値を表示"}, call_id="projection-call"
            )
        ],
        [assistant_message("| 名前 | 値 |\n|---|---|\n| literal | 999 |")],
        ModelStep.respond(respond),
    )
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成値を部分表で表示", agent_id=agent_id),
        created_by_user_uuid=USER_UUID,
    )
    try:
        anyio.run(builtin_runtime.execute_run, run.id)
        actual = runtime_repository.get_run(run.id)
        assert actual.status.value == "completed", actual.events[-1].model_dump_json()
        answer = run_answer_text(actual)
        assert answer is not None and "record-8" in answer and "record-9" not in answer
        assert "| CHANGE | 0.25 |" in answer
        assert "999" not in answer and "[[table:" not in answer
        assert len(actual.steps) == 1 and len(model.calls) == 3 and not model.calls[-1].tools
        instructions = str(model.calls[0].system_instructions)
        assert DATA_INSTRUCTIONS in instructions
        record_property("first_turn_system_characters", len(instructions))
        model.assert_complete()
    finally:
        runtime_repository.delete_agent(agent_id)
        skill_registry.remove(skill_id)


def test_projection_preserves_null_large_decimal_escaping_and_row_independence() -> None:
    step = _step(
        "nl2sql_query",
        {
            "status": "done",
            "columns": ["LABEL", "VALUE", "FLAG"],
            "rows": [
                {"LABEL": "x|y\nz", "VALUE": None, "FLAG": False},
                {"LABEL": "other", "VALUE": "12345678901234567890.125", "FLAG": True},
            ],
            "column_metadata": [
                {"name": "LABEL", "unit_status": "unknown", "unit": None},
                {"name": "VALUE", "unit_status": "known", "unit": "N"},
                {"name": "FLAG", "unit_status": "unknown", "unit": None},
            ],
        },
    )
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, _output(step))
    duplicate = step.model_copy(deep=True, update={"id": "second-result"})
    register_data_result(metadata, duplicate.id, _output(duplicate))
    index = data_index([step, duplicate], metadata)
    actual = index.render("[[table:T2:rows=2,1:cols=2,1,3]]")
    assert actual == (
        "| VALUE | LABEL | FLAG |\n| --- | --- | --- |\n"
        "| 12345678901234567890.125 | other | true |\n| NULL | x\\|y z | false |"
    )
    assert index.render("[[table:T1:rows=2,1:cols=2,1,3]]") == actual


def test_large_column_map_is_not_truncated_or_renumbered_by_compaction() -> None:
    columns = [f"FIELD_{i}" for i in range(1, 36)]
    step = _step(
        "nl2sql_query",
        {
            "status": "done",
            "columns": columns,
            "rows": [{column: index for index, column in enumerate(columns, 1)}],
            "detail": "合成説明" * 10000,
        },
    )
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, _output(step))
    refs = model_data_references(step.id, _output(step), metadata=metadata)
    assert refs is not None
    compacted = json.loads(
        compact_output(json.dumps({**_output(step), "answer_data": refs}), OLDER_MAX_LEVEL)
    )
    assert compacted["answer_data"]["column_refs"] == {
        column: i for i, column in enumerate(columns, 1)
    }
    assert data_index([step], metadata).render("[[table:T1:rows=1:cols=35,2]]") == (
        "| FIELD_35 | FIELD_2 |\n| --- | --- |\n| 35 | 2 |"
    )


@pytest.mark.parametrize("selection", ["1-3", " 1 - 3 ", "\t1\t-\t3\t"])
def test_projection_accepts_format_whitespace_without_guessing_numbers(selection: str) -> None:
    step, metadata, _refs = _fixture()
    index = data_index([step], metadata)
    assert index.render("[[table:T1:rows=" + selection + ":cols= 3 ,\t1 ]]") == index.render(
        "[[table:T1:rows=1-3:cols=3,1]]"
    )
    assert index.render("[[table:T1:rows=\t1 :cols= 3 , 1 :layout= vertical\t]]") == index.render(
        "[[table:T1:rows=1:cols=3,1:layout=vertical]]"
    )
    assert index.render("[[table:T1:rows= * :cols= * ]]") == index.render("[[table:T1]]")


def test_single_column_literal_table_is_rejected_but_pure_reference_is_rendered() -> None:
    step, metadata, refs = _fixture()
    index = data_index([step], metadata)
    numeric = _step(
        "nl2sql_query", {"status": "done", "columns": ["VALUE"], "rows": [{"VALUE": 1}]}
    )
    with pytest.raises(DataReferenceError):
        data_index([numeric]).render("| VALUE |\n|---|\n| 999 |")
    pure = "| model label |\n|---|\n| " + refs["quantities"][0]["CHANGE"] + " |"
    assert index.render(pure) == index.render("[[table:T1:rows=1:cols=3]]")
    assert index.render("[[table:T1:rows=1:cols=3:layout=vertical]]").endswith("| CHANGE | 0.25 |")


@pytest.mark.parametrize(
    "answer",
    [
        "title\n---\ntext1|2",
        "title \\| name\n---\ntext1|2",
        "見出し\n===\ntext1|2",
        "ordinary text1|2\nnot a rule\n3|4",
    ],
)
def test_setext_and_pipe_prose_are_not_treated_as_single_column_data_tables(answer: str) -> None:
    step, metadata, _refs = _fixture()
    assert data_index([step], metadata).render(answer) == answer
