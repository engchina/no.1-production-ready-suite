"""単位の表示名を形式だけで組み立て、保存した列名・参照を維持する。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from test_answer_evidence import _step

from app.features.agent.answer_data import data_index, model_data_references, register_data_result
from app.features.agent.answer_data_tables import BoundCell, unit_header


@pytest.mark.parametrize(
    ("column", "unit", "label"),
    [
        ("VALUE", "%", "VALUE (%)"),
        ("VALUE (%)", "%", "VALUE (%)"),
        ("VALUE（%）", "%", "VALUE（%）"),
        ("VALUE (N)", "%", "VALUE (N) (%)"),
        ("VALUE (%) extra", "%", "VALUE (%) extra (%)"),
        ("VALUE (%)", None, "VALUE (%)"),
        ("VALUE (m/min)", "m/min", "VALUE (m/min)"),
    ],
)
def test_unit_label_is_shared_by_whole_projection_matrix_and_vertical(
    column: str, unit: str | None, label: str
) -> None:
    output: dict[str, Any] = {
        "status": "done",
        "columns": [column],
        "rows": [{column: 12}],
        "column_metadata": [
            {"name": column, "unit": unit, "unit_status": "known" if unit else "unknown"}
        ],
    }
    step = _step("nl2sql_query", output)
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, output)
    before = deepcopy((output, metadata))
    refs = model_data_references(step.id, output, metadata=metadata)
    assert refs is not None
    cell = refs["quantities" if unit else "cells"][0][column]
    index = data_index([step], metadata)
    expected = f"| {label} |\n| --- |\n| 12 |"
    assert index.render(refs["table"]) == expected
    assert index.render("[[table:T1:rows=1:cols=1]]") == expected
    assert index.render(f"| 任意 |\n|---|\n| {cell} |") == expected
    assert index.render("[[table:T1:rows=1:cols=1:layout=vertical]]") == (
        f"| 項目 | 値 |\n| --- | --- |\n| {label} | 12 |"
    )
    assert (output, metadata) == before
    assert model_data_references(step.id, output, metadata=metadata) == refs


def test_unit_label_keeps_source_reference_text_and_legacy_untyped_values() -> None:
    column = "SOURCE (%)"
    output: dict[str, Any] = {
        "status": "done",
        "columns": [column],
        "rows": [{column: "[[table:T9]]"}],
        "column_metadata": [{"name": column, "unit": "%", "unit_status": "known"}],
    }
    step = _step("nl2sql_query", output)
    index = data_index([step])
    refs = model_data_references(step.id, output)
    assert refs is not None
    assert index.render(refs["table"]) == "| SOURCE (%) |\n| --- |\n| [[table:T9]] |"
    output.pop("column_metadata")
    legacy_step = _step("nl2sql_query", output)
    legacy = model_data_references(legacy_step.id, output)
    assert legacy is not None
    assert data_index([legacy_step]).render(legacy["table"]) == (
        "| SOURCE (%) |\n| --- |\n| [[table:T9]] |"
    )


def test_empty_unit_keeps_legacy_bound_cell_label() -> None:
    cell = BoundCell("source", 0, "VALUE", 12, "")
    assert unit_header(cell.column, cell.unit) == "VALUE"
