"""実rendererの出典だけを計数から分け、取得値の同形文字列を保つ。"""

import json
from pathlib import Path

import pytest
from test_answer_evidence import _step
from test_source_table_patches import fixture

from app.features.agent import builtin_runtime
from app.features.agent.answer_data import register_data_result
from app.features.agent.report_contract import (
    AnswerBudget,
    AnswerPlan,
    FinalSelection,
    NarrativeBlock,
    ReportOutputContract,
    TableBlock,
)
from app.features.agent.report_preview import RenderedReport, create_preview, select_preview
from app.features.agent.report_runtime import render_plan


@pytest.mark.parametrize("short_reference", [True, False])
def test_budget_excludes_only_model_citations_and_retains_same_literal_data(
    short_reference: bool,
) -> None:
    current, _, _, _ = fixture(False)
    marker = "【根拠: chunk-a】"
    output = {"status": "done", "columns": ["LABEL"], "rows": [{"LABEL": marker}]}
    step = _step("nl2sql_query", output)
    current.steps.append(step)
    register_data_result(current.metadata, step.id, output)
    citation = "[[cite:1]]" if short_reference else marker
    plan = AnswerPlan(
        blocks=(
            NarrativeBlock(text=f"根拠 {citation} と {citation}"),
            TableBlock(table="T1"),
            NarrativeBlock(text="取得値 [[cell:T1:R1:C1]]"),
        )
    )

    def render(candidate: AnswerPlan) -> RenderedReport:
        return render_plan(
            current,
            candidate,
            trusted_sources=builtin_runtime._citable_source_ids(current),
            render_sources=lambda text: builtin_runtime._render_source_citations(current, text),
            finalize=lambda body, _evidence: body,
        )

    rendered = render(plan)
    assert rendered.evidence_text == f"{marker}\n{marker}"
    assert rendered.body.count(marker) == 4
    counted = len(rendered.body) - 2 * len(marker)
    contract = ReportOutputContract(
        budget=AnswerBudget(
            metric="expanded_markdown_without_citation_markers_codepoints",
            hard_max=counted,
        )
    )
    created = create_preview(
        None,
        run_id=current.id,
        operation_key="operation-a",
        plan=plan,
        contract=contract,
        render_body=render,
    )
    assert created.result.total_codepoints == len(rendered.body)
    assert created.result.budget_codepoints == counted
    assert created.result.citation_marker_codepoints == 2 * len(marker)
    assert created.result.status == "WITHIN_BUDGET"
    restored = json.loads(json.dumps(created.ledger))
    selected = select_preview(
        restored,
        run_id=current.id,
        selection=FinalSelection(preview_id="A1"),
        contract=contract,
    )
    assert selected.body == rendered.body

    raw_contract = ReportOutputContract(budget=AnswerBudget(hard_max=counted))
    raw = create_preview(
        None,
        run_id=current.id,
        operation_key="operation-b",
        plan=plan,
        contract=raw_contract,
        render_body=render,
    )
    assert raw.result.total_codepoints == len(rendered.body)
    assert raw.result.budget_codepoints == len(rendered.body)
    assert raw.result.status == "REJECTED_HARD_MAX"


def test_pre_change_preview_keeps_its_original_record_digest_and_budget() -> None:
    ledger = json.loads((Path(__file__).parent / "fixtures" / "report-preview-v1.json").read_text())
    record = ledger["records"][0]
    assert record["record_digest"] == (
        "46f9283e3a9cd4b228a02c81ea76afc08525baae7c46a96ae5e0b15309a92ee6"
    )
    selected = select_preview(
        ledger,
        run_id="run-legacy",
        selection=FinalSelection(preview_id="A1"),
        contract=ReportOutputContract(budget=AnswerBudget(soft_target=len(record["body"]))),
    )
    assert selected.body == record["body"]
    assert selected.budget_status == "WITHIN_BUDGET"
