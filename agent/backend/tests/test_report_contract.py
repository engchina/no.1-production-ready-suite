"""参照付き回答の設定は公開版由来で、モデルの案からは変更できない。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.features.agent.report_contract import (
    MAX_REPORT_BLOCKS,
    MAX_REPORT_TEXT_CHARS,
    AnswerBudget,
    AnswerPlan,
    FinalSelection,
    NarrativeBlock,
    ReportOutputContract,
    TableBlock,
)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"soft_target": 0},
        {"hard_max": -1},
        {"hard_max": True},
        {"soft_target": "40"},
        {"soft_target": 20, "hard_max": 10},
        {"metric": "DOM", "hard_max": 20},
        {"question": "40", "hard_max": 40},
    ],
)
def test_invalid_or_untrusted_budget_is_rejected(value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AnswerBudget.model_validate(value)


def test_budget_optional_and_soft_does_not_become_hard() -> None:
    assert ReportOutputContract().budget is None
    budget = AnswerBudget(soft_target=40)
    assert budget.hard_max is None
    assert budget.metric == "expanded_markdown_codepoints"
    with pytest.raises(ValidationError):
        cast(Any, budget).hard_max = 40


@pytest.mark.parametrize(
    "extra", [{"budget": {"hard_max": 99999}}, {"model_id": "other"}, {"body": "new"}]
)
def test_plan_cannot_supply_budget_model_or_final_body(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AnswerPlan.model_validate({"blocks": [{"kind": "narrative", "text": "文"}], **extra})


def test_json_plan_accepts_narrative_projection_and_short_confirmation_refs() -> None:
    plan = AnswerPlan.model_validate(
        {
            "blocks": [
                {"kind": "narrative", "text": "説明"},
                {"kind": "table", "table": "T1", "rows": "1-2", "cols": "2,1"},
                {
                    "kind": "confirmation_section",
                    "items": [{"field_ref": "S1:F2", "result_ref": "T1"}],
                },
            ]
        }
    )
    assert isinstance(plan.blocks, tuple)
    assert plan.blocks[1].kind == "table"
    assert plan.blocks[1].marker() == "[[table:T1:rows=1-2:cols=2,1]]"
    assert plan.blocks[2].kind == "confirmation_section"
    with pytest.raises(ValidationError):
        cast(Any, plan).blocks = ()


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "confirmation_section", "text": "model confirmation"},
        {"kind": "confirmation_section", "items": [{"field_ref": "S1:F2", "value": "invented"}]},
        {"kind": "unknown", "text": "x"},
    ],
)
def test_confirmation_values_and_unknown_block_types_are_not_model_fields(
    value: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        AnswerPlan.model_validate({"blocks": [value]})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"table": "a" * 24},
        {"table": "T0"},
        {"table": "T01"},
        {"table": "T1", "rows": "1"},
        {"table": "T1", "layout": "vertical"},
        {"table": "T1", "rows": "x" * 2049, "cols": "1"},
    ],
)
def test_table_accepts_short_ids_and_paired_bounded_selection_only(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TableBlock.model_validate(kwargs)


@pytest.mark.parametrize(
    "value",
    [
        {"preview_id": "A0"},
        {"preview_id": "A01"},
        {"preview_id": "A1", "body": "replacement"},
        {"preview_id": "a" * 24},
    ],
)
def test_final_selection_only_has_short_id(value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FinalSelection.model_validate(value)


def test_plan_has_technical_bounds_separate_from_user_answer_budget() -> None:
    with pytest.raises(ValidationError):
        AnswerPlan(blocks=tuple(NarrativeBlock(text="x") for _ in range(MAX_REPORT_BLOCKS + 1)))
    with pytest.raises(ValidationError):
        NarrativeBlock(text="x" * (MAX_REPORT_TEXT_CHARS + 1))
    with pytest.raises(ValidationError):
        AnswerPlan(blocks=tuple(NarrativeBlock(text="x" * MAX_REPORT_TEXT_CHARS) for _ in range(5)))
    assert AnswerBudget(soft_target=1_000_000).soft_target == 1_000_000


def test_narrative_does_not_hide_table_selections_outside_typed_block() -> None:
    with pytest.raises(ValidationError):
        NarrativeBlock(text="[[table:T1]]")
    assert NarrativeBlock(text="値 [[quantity:T1:R1:C1]]").kind == "narrative"
