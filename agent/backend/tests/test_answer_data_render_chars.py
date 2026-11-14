"""表のMarkdown文字数を、値や参照を変更せず列単位で渡す。"""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest
from test_answer_evidence import _step

from app.features.agent.answer_data import data_index, model_data_references, register_data_result
from app.features.agent.context_window import OLDER_MAX_LEVEL, compact_output
from app.features.agent.runtime import RunState, RunStatus


@pytest.mark.parametrize(
    ("columns", "rows", "column_costs"),
    [
        (
            ["ID", "VALUE"],
            [{"ID": "a|b", "VALUE": None}, {"ID": "c\r\nd", "VALUE": True}],
            [(2, 8), (5, 8)],
        ),
        (["列👩\u200d🔬"], [{"列👩\u200d🔬": "e\u0301"}, {"列👩\u200d🔬": "あ"}], [(4, 3)]),
        (
            ["VALUE (%)"],
            [
                {"VALUE (%)": Decimal("12345678901234567890.125")},
                {"VALUE (%)": Decimal("1.20")},
                {"VALUE (%)": False},
            ],
            [(9, 33)],
        ),
        (["A", "B"], [{"A": 1}, {"B": "|\\\r\n"}], [(1, 5), (1, 10)]),
        ([], [], []),
        (["X"], [], [(1, 0)]),
        ([], [{"X": None}], [(1, 4)]),
    ],
)
def test_render_chars_matches_whole_renderer_and_escaped_column_aggregates(
    columns: list[str], rows: list[dict[str, Any]], column_costs: list[tuple[int, int]]
) -> None:
    output: dict[str, Any] = {"status": "done", "columns": columns, "rows": rows}
    step = _step("nl2sql_query", output)
    before = deepcopy(step)
    refs = model_data_references(step.id, output)
    assert refs is not None
    assert refs["render_chars"] == {
        "metric": "markdown_codepoints",
        "whole": len(data_index([step]).render(refs["table"])),
        "columns": [{"header": header, "values": values} for header, values in column_costs],
    }
    assert step == before
    assert "rows" not in refs["render_chars"] and "cells" not in refs["render_chars"]


def test_column_costs_reconstruct_all_rows_column_projection_and_vertical_length() -> None:
    output: dict[str, Any] = {
        "status": "done",
        "columns": ["LABEL", "VALUE (N)", "FLAG"],
        "rows": [
            {"LABEL": "a|b", "VALUE (N)": 12, "FLAG": False},
            {"LABEL": "c", "VALUE (N)": None, "FLAG": True},
        ],
        "column_metadata": [
            {"name": "LABEL", "unit_status": "unknown", "unit": None},
            {"name": "VALUE (N)", "unit_status": "known", "unit": "N"},
            {"name": "FLAG", "unit_status": "unknown", "unit": None},
        ],
    }
    step = _step("nl2sql_query", output)
    refs = model_data_references(step.id, output)
    assert refs is not None
    index = data_index([step])
    costs = refs["render_chars"]["columns"]
    row_count = len(output["rows"])
    for selected in [[1], [2, 1], [3, 1, 2]]:
        count = len(selected)
        values = sum(costs[i - 1]["header"] + costs[i - 1]["values"] for i in selected)
        horizontal = values + (row_count + 1) * (3 * count + 2) + (6 * count + 1)
        marker = refs["table"][:-2] + ":rows=*:cols=" + ",".join(map(str, selected))
        assert horizontal == len(index.render(marker + "]]"))
        width = row_count + 1
        headers = len("項目") + sum(len(f"R{i}") for i in range(1, row_count + 1))
        vertical = values + headers + (count + 1) * (3 * width + 2) + (6 * width + 1)
        assert vertical == len(index.render(marker + ":layout=vertical]]"))
    assert index.render(refs["cells"][0]["VALUE (N)"]) == "12"
    single_output = {**output, "rows": output["rows"][:1]}
    single_step = _step("nl2sql_query", single_output)
    single_refs = model_data_references(single_step.id, single_output)
    assert single_refs is not None
    single_costs = single_refs["render_chars"]["columns"]
    values = sum(cost["header"] + cost["values"] for cost in single_costs)
    vertical = values + len("項目") + len("値") + (len(single_costs) + 1) * 8 + 13
    marker = single_refs["table"][:-2] + ":rows=*:cols=*:layout=vertical]]"
    assert vertical == len(data_index([single_step]).render(marker))


def test_render_cost_is_complete_after_compaction_and_checkpoint_restore() -> None:
    output: dict[str, Any] = {
        "status": "done",
        "columns": [f"C{i}" for i in range(12)],
        "rows": [{f"C{i}": f"row-{row}-{i}" for i in range(12)} for row in range(15)],
    }
    step = _step("nl2sql_query", output)
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, output)
    refs = model_data_references(step.id, output, metadata=metadata)
    assert refs is not None
    before = deepcopy((step, metadata, refs))
    source = json.dumps({**output, "answer_data": refs, "detail": "長文" * 2000})
    for level in range(1, OLDER_MAX_LEVEL + 1):
        compacted = json.loads(compact_output(source, level))
        assert compacted["answer_data"]["render_chars"] == refs["render_chars"]
    restored = RunState.model_validate_json(
        RunState(
            id="synthetic",
            goal="参照を確認",
            agent_id="default",
            status=RunStatus.RUNNING,
            steps=[step],
            metadata=metadata,
        ).model_dump_json()
    )
    restored_step = restored.steps[0]
    assert restored_step.tool_result is not None
    assert isinstance(restored_step.tool_result.output, dict)
    assert (
        model_data_references(
            restored_step.id, restored_step.tool_result.output, metadata=restored.metadata
        )
        == refs
    )
    assert data_index(restored.steps, restored.metadata).render(refs["table"]) == data_index(
        [step], metadata
    ).render(refs["table"])
    assert (step, metadata, refs) == before
