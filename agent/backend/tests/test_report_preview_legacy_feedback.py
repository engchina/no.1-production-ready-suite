"""旧版で保存した回答案を移行・再描画せず読み、計数を保つ。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from app.features.agent.report_contract import AnswerPlan, FinalSelection, ReportOutputContract
from app.features.agent.report_preview import RenderedReport, create_preview, select_preview


def legacy() -> dict[str, Any]:
    # 旧実装で生成した匿名 fixture。現在の生成処理で期待値を作り直さない。
    return cast(
        dict[str, Any],
        json.loads(
            (Path(__file__).parent / "fixtures/report-preview-v1-feedback-legacy.json").read_text()
        ),
    )


def test_legacy_frozen_answer_and_literal_citation_survive_upgrade() -> None:
    fixture = legacy()
    original = deepcopy(fixture["ledger"])
    selected = select_preview(
        fixture["ledger"],
        run_id="run-legacy",
        selection=FinalSelection(preview_id="A1"),
        contract=ReportOutputContract.model_validate(fixture["contract"]),
        source_digest="a" * 64,
    )
    assert selected.status == "complete"
    assert selected.budget_status == "OVER_TARGET"
    assert selected.body == original["records"][0]["body"]
    assert selected.body is not None
    assert "【根拠: literal-in-cell】" in selected.body
    assert fixture["ledger"] == original


def test_retry_of_legacy_operation_preserves_exact_ledger_without_rendering() -> None:
    fixture = legacy()
    original = deepcopy(fixture["ledger"])

    def forbidden_render(_plan: AnswerPlan) -> RenderedReport:
        pytest.fail("保存済み操作の再試行で描画処理を呼んだ")

    retried = create_preview(
        fixture["ledger"],
        run_id="run-legacy",
        operation_key="operation-legacy",
        plan=AnswerPlan.model_validate(original["records"][0]["plan"]),
        contract=ReportOutputContract.model_validate(fixture["contract"]),
        render_body=forbidden_render,
    )
    assert retried.result.reused
    assert retried.ledger == original
    assert retried.result.budget_codepoints == fixture["budget_codepoints"]
    assert retried.result.citation_marker_codepoints == fixture["citation_marker_codepoints"]
    assert retried.result.budget_block_costs is None
    # 表の値として出てくる引用に似た文字列は、控除する引用に数えない。
    assert retried.result.budget_codepoints == len(original["records"][0]["body"]) - len(
        "【根拠: chunk-a】"
    )
