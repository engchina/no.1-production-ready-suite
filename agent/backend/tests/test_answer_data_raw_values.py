"""実際の参照経路で元値だけを表示し、既存の凍結回答を変えない。"""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.features.agent.answer_data import (
    DataReferenceError,
    data_index,
    model_data_references,
    register_data_result,
)
from app.features.agent.report_contract import AnswerPlan, FinalSelection, ReportOutputContract
from app.features.agent.report_preview import RenderedReport, create_preview, select_preview
from app.features.agent.runtime import RunStep, StepStatus
from app.features.agent.tools import ToolCall, ToolResult


def result(value: Any, *, unit: str | None = "kg") -> tuple[RunStep, dict[str, Any]]:
    output = {
        "status": "done",
        "columns": ["VALUE"],
        "rows": [{"VALUE": value}],
        "column_metadata": [
            {"name": "VALUE", "unit": unit, "unit_status": "known" if unit else "unknown"}
        ],
    }
    step = RunStep(
        id="step-raw",
        run_id="run-raw",
        status=StepStatus.COMPLETED,
        tool_call=ToolCall(name="nl2sql__nl2sql_query", arguments={}),
        tool_result=ToolResult(name="nl2sql__nl2sql_query", success=True, output=output),
    )
    metadata: dict[str, Any] = {}
    register_data_result(metadata, step.id, output)
    return step, metadata


@pytest.mark.parametrize("kind", ["cell", "quantity"])
def test_unit_suffix_is_owned_by_surrounding_text_not_reference_renderer(kind: str) -> None:
    # 旧版はcellを拒否し、quantityでは12 kgkgと表示した実経路。
    step, metadata = result(12)
    index = data_index([step], metadata)
    assert index.render(f"測定値[[{kind}:T1:R1:C1]]kg。") == "測定値12kg。"
    # 単位の綴りや前後の語句を解釈して消さない。
    assert index.render(f"前kg [[{kind}:T1:R1:C1]] kg/kg後") == "前kg 12 kg/kg後"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "NULL"),
        (Decimal("12345678901234567890.0100"), "12345678901234567890.0100"),
        (123456789012345678901234567890123456789, "123456789012345678901234567890123456789"),
        ("12345678901234567890.0100", "12345678901234567890.0100"),
        (-0.25, "-0.25"),
    ],
)
@pytest.mark.parametrize("kind", ["cell", "quantity"])
def test_exact_numeric_values_and_null_never_gain_unit(
    value: Any, expected: str, kind: str
) -> None:
    step, metadata = result(value)
    before = deepcopy((step, metadata))
    index = data_index([step], metadata)
    assert index.render(f"[[{kind}:T1:R1:C1]]") == expected
    assert (step, metadata) == before


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12 kg", "12 kg"),
        ("kg 12 kg", "kg 12 kg"),
        ("12 (kg)", "12 (kg)"),
        ("001", "001"),
        ("", ""),
        (True, "true"),
        (False, "false"),
        ("[[quantity:T9:R9:C9]]", "[[quantity:T9:R9:C9]]"),
    ],
)
def test_raw_cell_keeps_strings_units_booleans_and_reference_like_literals(
    value: Any, expected: str
) -> None:
    step, metadata = result(value)
    assert data_index([step], metadata).render("[[cell:T1:R1:C1]]") == expected


@pytest.mark.parametrize("value", ["12 kg", "001", True, False])
def test_legacy_quantity_keeps_existing_numeric_type_validation(value: Any) -> None:
    step, metadata = result(value)
    with pytest.raises(DataReferenceError):
        data_index([step], metadata).render("[[quantity:T1:R1:C1]]")


def test_unknown_unit_does_not_limit_raw_cell_or_invent_legacy_quantity() -> None:
    step, metadata = result("12 unknown", unit=None)
    index = data_index([step], metadata)
    assert index.render("[[cell:T1:R1:C1]]") == "12 unknown"
    with pytest.raises(DataReferenceError):
        index.render("[[quantity:T1:R1:C1]]")


def test_fixed_legacy_hashes_remain_bound_to_original_result_metadata() -> None:
    step, metadata = result("12345678901234567890.0100")
    before = deepcopy((step, metadata))
    # 変更前の実装で生成した固定identity。新しい表示方針から期待hashを再計算しない。
    assert metadata["_answer_data_references"] == [
        {"step_id": "step-raw", "key": "2d671d0262950a41a42d45dc", "number": 1}
    ]
    index = data_index([step], metadata)
    for marker in (
        "[[cell:T1:R1:C1]]",
        "[[quantity:T1:R1:C1]]",
        "[[cell:264cd548ee3e598e498cafb5]]",
        "[[quantity:264cd548ee3e598e498cafb5]]",
    ):
        assert index.render(marker) == "12345678901234567890.0100"
    assert step.tool_result is not None and isinstance(step.tool_result.output, dict)
    refs = model_data_references(step.id, step.tool_result.output, metadata=metadata)
    assert refs is not None
    assert refs["cells"][0]["VALUE"] == "[[cell:T1:R1:C1]]"
    assert refs["quantity_columns"] == {"VALUE": {"column": 1, "unit": "kg"}}
    assert "quantities" not in refs
    assert (step, metadata) == before

    changed = step.model_copy(deep=True)
    assert changed.tool_result is not None and isinstance(changed.tool_result.output, dict)
    changed.tool_result.output["column_metadata"][0]["unit"] = "m"
    with pytest.raises(DataReferenceError):
        data_index([changed], metadata).render("[[cell:T1:R1:C1]]")


@pytest.mark.parametrize("marker", ["[[cell:T1:R2:C1]]", "[[quantity:T1:R1:C2]]"])
def test_raw_rendering_still_rejects_unavailable_positions(marker: str) -> None:
    step, metadata = result(12)
    with pytest.raises(DataReferenceError):
        data_index([step], metadata).render(marker)


def test_old_frozen_quantity_body_is_not_rewritten_using_new_renderer() -> None:
    # 旧版の実際のDataIndex→preview保存で作った合成fixture。現在のコードでは再生成しない。
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/report-preview-v1-quantity-frozen.json").read_text()
    )
    ledger = fixture["ledger"]
    before = deepcopy(ledger)
    contract = ReportOutputContract.model_validate(fixture["contract"])

    def forbidden(_plan: AnswerPlan) -> RenderedReport:
        pytest.fail("旧凍結回答の選択や再試行で再描画してはいけません。")

    retried = create_preview(
        ledger,
        run_id="run-old-quantity",
        operation_key="operation-old-quantity",
        plan=AnswerPlan.model_validate(ledger["records"][0]["plan"]),
        contract=contract,
        render_body=forbidden,
    )
    selected = select_preview(
        retried.ledger,
        run_id="run-old-quantity",
        selection=FinalSelection(preview_id="A1"),
        contract=contract,
        source_digest="a" * 64,
    )
    assert selected.body == fixture["expected_body"] == "値は12 kgkg。"
    assert retried.result.reused
    assert retried.ledger == before == ledger
    assert retried.ledger["records"][0]["record_digest"] == before["records"][0]["record_digest"]
    step, metadata = result(12)
    assert data_index([step], metadata).render("値は[[quantity:T1:R1:C1]]kg。") == "値は12kg。"
