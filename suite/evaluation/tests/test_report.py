"""Report: 評価の結果の比較の要約（製品の評価の結果と suite の結果の両方を読む）。

`fixtures/rag_evaluation_result.json` は RAG の評価ランナーの結果（`summarize_case_results` の
`EvaluationMetrics`。多段の質問の合成のケース 2 つ。診断と本文は含めない）の形。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pr_suite_evaluation.cli import load_result
from pr_suite_evaluation.report import (
    FORBIDDEN_ACTION_REASON,
    summarize_result,
    summary_markdown,
)

RAG_RESULT = Path(__file__).resolve().parent / "fixtures" / "rag_evaluation_result.json"


def test_summary_reads_the_rag_evaluation_result_and_shows_reasoning_rows() -> None:
    summary = summarize_result(load_result(RAG_RESULT))
    assert summary["required_evidence_recall"] == 0.75
    assert summary["reasoning_types"]["bridge"]["required_evidence_recall"] == 1.0
    assert summary["hops"]["1"]["required_evidence_recall"] == 0.5
    assert summary["splits"]["dev"]["reasoning_types"]["bridge"]["case_count"] == 1
    assert "unevaluable" not in summary
    table = summary_markdown({"A": summary, "D": summary})
    assert "| 必要な根拠の取得率 | 0.75 | 0.75 |" in table
    assert "| bridge: 必要な根拠の取得率 | 1.00 | 1.00 |" in table
    assert "| 2 段: 必要な根拠の取得率 | 1.00 | 1.00 |" in table
    assert "| dev / bridge: 必要な根拠の取得率 | 1.00 | 1.00 |" in table


def test_summary_compares_results_in_a_markdown_table() -> None:
    data: dict[str, Any] = {
        "case_count": 2,
        "error_count": 0,
        "response_decision_accuracy": 0.5,
        "claim_support_rate": 0.5,
        "case_results": [
            {"case_id": "a", "status": "success", "elapsed_ms": 1000.0, "failure_reasons": []},
            {
                "case_id": "b",
                "status": "success",
                "elapsed_ms": 3000.0,
                "failure_reasons": [FORBIDDEN_ACTION_REASON],
            },
            # 失敗したケースは危険な回答に数えない。
            {
                "case_id": "c",
                "status": "error",
                "elapsed_ms": 500.0,
                "failure_reasons": ["case_error", FORBIDDEN_ACTION_REASON],
            },
        ],
        "split_breakdown": {
            "holdout": {
                "case_count": 1,
                "error_count": 0,
                "metrics": {"response_decision_accuracy": 0.0},
            }
        },
        "category_breakdown": {
            "clarification_required": {
                "case_count": 2,
                "error_count": 0,
                "metrics": {"response_decision_accuracy": 0.5},
            }
        },
        # suite の結果の分母（評価不能のケースの数を表に出す）。
        "denominators": {"metrics": {"response_decision_accuracy": {"unevaluable": 3}}},
    }
    summary = summarize_result(data)
    assert summary["median_elapsed_seconds"] == 2.0
    assert summary["dangerous_case_count"] == 1
    assert summary["dangerous_case_ids"] == ["b"]
    assert summary["failed_case_ids"] == ["c"]
    assert summary["categories"]["clarification_required"]["handling_correct_rate"] == 0.5
    table = summary_markdown({"A": summary, "D": summary})
    assert "| 指標 | A | D |" in table
    assert "| 回答・確認・保留の判断の正しさ | 0.50 | 0.50 |" in table
    assert "| 評価不能のケース: 回答・確認・保留の判断の正しさ | 3 | 3 |" in table
    assert "| 危険な回答のケース | 1 | 1 |" in table
    assert "| holdout: 回答・確認・保留の判断の正しさ | 0.00 | 0.00 |" in table
    assert "| clarification_required: 回答・確認・保留の判断の正しさ | 0.50 | 0.50 |" in table
    assert "| 1 問の時間の中央値（秒） | 2.00 | 2.00 |" in table


def test_dangerous_cases_are_unmeasured_without_forbidden_action() -> None:
    data = {
        "case_count": 1,
        "error_count": 0,
        "case_results": [
            {"case_id": "a", "status": "success", "elapsed_ms": 1000.0, "failure_reasons": []}
        ],
    }
    summary = summarize_result(data)
    assert summary["dangerous_case_count"] is None
    assert "| 危険な回答のケース | - |" in summary_markdown({"D": summary})


def test_load_result_reads_suite_records_and_product_results(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"report": {"case_count": 1}, "observations": []}), "utf-8")
    product = tmp_path / "product.json"
    product.write_text(json.dumps({"data": {"case_count": 2}}), "utf-8")
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"case_count": 3}), "utf-8")
    assert [load_result(path)["case_count"] for path in (suite, product, bare)] == [1, 2, 3]
