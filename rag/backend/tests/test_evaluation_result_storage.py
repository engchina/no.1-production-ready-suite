"""品質評価の結果を JSON 型の列に収める処理のテスト（#4103）。"""

from __future__ import annotations

import copy
import logging
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.api.routes import evaluation as evaluation_routes
from app.clients.oracle import OracleClient
from app.rag.evaluation_result_storage import (
    EVALUATION_RESULT_MAX_JSON_BYTES,
    fit_evaluation_result,
    without_answer_diagnostics,
)
from app.rag.evaluation_results_excel import evaluation_results_xlsx
from app.schemas.evaluation import EvaluationMetrics, EvaluationResultExportRequest


def _case(case_id: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "trace_id": f"trace-{case_id}",
        "answer_correctness": 0.75,
        "failure_reasons": ["合成の理由"],
        "elapsed_ms": 1.0,
        "diagnostics": {
            "config_fingerprint": "fingerprint",
            "answer": {"retrieval_stages": [{"stage": "合成の工程"}]},
        },
    }


def _result() -> dict[str, Any]:
    return {
        "case_count": 2,
        "answer_correctness": 0.75,
        "case_results": [_case("c1"), _case("c2")],
    }


def test_small_result_is_kept_as_is() -> None:
    result = _result()
    assert fit_evaluation_result(result) is result


def test_over_limit_result_omits_only_answer_diagnostics() -> None:
    result = _result()
    original = copy.deepcopy(result)

    fitted = fit_evaluation_result(result, max_bytes=10)

    assert fitted["answer_diagnostics_omitted"] is True
    assert fitted["answer_correctness"] == 0.75
    for case in fitted["case_results"]:
        assert case["answer_correctness"] == 0.75
        assert case["failure_reasons"] == ["合成の理由"]
        assert case["diagnostics"] == {"config_fingerprint": "fingerprint", "answer": None}
    # 渡した結果は変えない（API の応答や artifact に使い回すため）。
    assert result == original


def test_size_counts_utf8_bytes_not_characters() -> None:
    """列の上限はバイトで効くので、文字数では判定しない。"""
    result = _result()
    result["case_results"][0]["diagnostics"]["answer"]["note"] = "あ" * 1000
    assert fit_evaluation_result(result, max_bytes=2000) is not result


def test_comparison_metrics_also_omit_answer_diagnostics() -> None:
    result = {
        **_result(),
        "comparison": {"search_answer_profile_id": "profile-a", "metrics": _result()},
    }

    compact = without_answer_diagnostics(result)

    assert compact["comparison"]["search_answer_profile_id"] == "profile-a"
    metrics = compact["comparison"]["metrics"]
    assert metrics["answer_diagnostics_omitted"] is True
    assert metrics["case_results"][0]["diagnostics"]["answer"] is None
    assert metrics["case_results"][0]["answer_correctness"] == 0.75


def test_result_without_cases_is_accepted() -> None:
    """判定の校正など、ケースの結果が無い結果も壊さない。"""
    compact = without_answer_diagnostics({"status": "done"})
    assert compact == {"status": "done", "answer_diagnostics_omitted": True}


def test_excel_is_the_same_without_answer_diagnostics() -> None:
    """公式の Excel は回答の診断を使わないので、外しても同じ内容になる。"""

    def sheets(metrics: EvaluationMetrics) -> dict[str, list[tuple[Any, ...]]]:
        content = evaluation_results_xlsx(metrics, EvaluationResultExportRequest())
        workbook = load_workbook(BytesIO(content))
        return {sheet.title: list(sheet.values) for sheet in workbook.worksheets}

    full = EvaluationMetrics.model_validate(_result())
    compact = EvaluationMetrics.model_validate(without_answer_diagnostics(_result()))

    assert compact.answer_diagnostics_omitted is True
    assert sheets(compact) == sheets(full)


async def test_artifact_over_limit_is_saved_without_answer_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, Any] = {}

    async def fake_save(self: OracleClient, artifact: dict[str, Any]) -> str:
        saved.update(artifact)
        return "artifact-1"

    monkeypatch.setattr(OracleClient, "save_evaluation_artifact", fake_save)
    result = _result()
    result["case_results"][0]["diagnostics"]["answer"]["note"] = "x" * (
        EVALUATION_RESULT_MAX_JSON_BYTES + 1
    )

    run_id = await evaluation_routes._save_evaluation_artifact(
        request_summary={"kind": "run"},
        result_summary=result,
        knowledge_base_ids=[],
        passed=True,
    )

    assert run_id == "artifact-1"
    assert saved["result_summary"]["answer_diagnostics_omitted"] is True
    assert saved["result_summary"]["case_results"][0]["answer_correctness"] == 0.75


async def test_artifact_save_failure_logs_oracle_code(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """artifact の保存の失敗は評価を止めないが、原因のコードを警告に残す。"""

    async def fake_save(self: OracleClient, artifact: dict[str, Any]) -> str:
        raise Exception(SimpleNamespace(code=40604, full_code="ORA-40604"))

    monkeypatch.setattr(OracleClient, "save_evaluation_artifact", fake_save)

    with caplog.at_level(logging.WARNING, logger=evaluation_routes.logger.name):
        run_id = await evaluation_routes._save_evaluation_artifact(
            request_summary={"kind": "run"},
            result_summary=_result(),
            knowledge_base_ids=[],
            passed=True,
        )

    assert run_id is None
    record = next(
        item for item in caplog.records if item.message == "evaluation_artifact_persistence_skipped"
    )
    assert record.levelno == logging.WARNING
    assert record.__dict__["full_code"] == "ORA-40604"
