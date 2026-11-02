"""品質評価の結果の保存の内容のテスト（#4103・#4107）。"""

from __future__ import annotations

import copy
import json
import logging
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.api.routes import evaluation as evaluation_routes
from app.clients.oracle import OracleClient
from app.rag.evaluation_result_storage import without_answer_diagnostics
from app.rag.evaluation_results_excel import evaluation_results_xlsx
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationMetrics,
    EvaluationResultExportRequest,
)


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


QUESTION_MARKER = "合成の質問の原文"
EVIDENCE_MARKER = "合成の根拠の原文"


def _result_with_source_text() -> dict[str, Any]:
    """回答の診断が本文を持つ項目を、実際の結果と同じ形で持つ合成の結果。"""
    result = _result()
    result["case_results"][0]["diagnostics"]["answer"] = {
        "original_question": QUESTION_MARKER,
        "rewritten_question": QUESTION_MARKER,
        "request_coverage": {"requests": [{"id": "r1", "text": QUESTION_MARKER}]},
        "envelope": {
            "requests": [{"id": "r1", "text": QUESTION_MARKER}],
            "items": [{"evidence_id": "e1", "text": EVIDENCE_MARKER}],
        },
        "execution_steps": [{"name": "工程", "details": {"lines": [f"質問: {QUESTION_MARKER}"]}}],
        "generation_citation_traces": [{"input": f"{QUESTION_MARKER} {EVIDENCE_MARKER}"}],
    }
    return result


def test_saved_result_omits_only_answer_diagnostics() -> None:
    result = _result()
    original = copy.deepcopy(result)

    compact = without_answer_diagnostics(result)

    assert compact["answer_diagnostics_omitted"] is True
    assert compact["answer_correctness"] == 0.75
    for case in compact["case_results"]:
        assert case["trace_id"].startswith("trace-")
        assert case["answer_correctness"] == 0.75
        assert case["failure_reasons"] == ["合成の理由"]
        assert case["diagnostics"] == {"config_fingerprint": "fingerprint", "answer": None}
    # 渡した結果は変えない（API の応答や除去実験に使い回すため）。
    assert result == original


def test_saved_result_has_no_source_text_of_answer_diagnostics() -> None:
    """質問・根拠の文は、固定の項目にも工程の表示の文にも残らない（#4107）。"""
    result = _result_with_source_text()
    result["comparison"] = {"search_answer_profile_id": "p", "metrics": _result_with_source_text()}
    assert QUESTION_MARKER in json.dumps(result, ensure_ascii=False)

    saved = json.dumps(without_answer_diagnostics(result), ensure_ascii=False)

    assert QUESTION_MARKER not in saved
    assert EVIDENCE_MARKER not in saved


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
    assert without_answer_diagnostics({"status": "done"}) == {"status": "done"}


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


def test_excel_question_comes_from_inputs_not_saved_result() -> None:
    """公式の Excel の質問は、画面が保持した実行時の入力から出す（保存した結果に頼らない）。"""
    case = EvaluationCase(id="c1", query=QUESTION_MARKER)
    metrics = EvaluationMetrics.model_validate(without_answer_diagnostics(_result()))

    content = evaluation_results_xlsx(metrics, EvaluationResultExportRequest(cases=[case]))

    rows = list(load_workbook(BytesIO(content))["主条件の結果"].values)
    assert rows[0][:2] == ("ケース ID", "質問")
    assert rows[1][:2] == ("c1", QUESTION_MARKER)


async def test_artifact_is_saved_without_answer_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, Any] = {}

    async def fake_save(self: OracleClient, artifact: dict[str, Any]) -> str:
        saved.update(artifact)
        return "artifact-1"

    monkeypatch.setattr(OracleClient, "save_evaluation_artifact", fake_save)

    run_id = await evaluation_routes._save_evaluation_artifact(
        request_summary={"kind": "run"},
        result_summary=_result_with_source_text(),
        knowledge_base_ids=[],
        passed=True,
    )

    assert run_id == "artifact-1"
    assert saved["result_summary"]["answer_diagnostics_omitted"] is True
    assert saved["result_summary"]["case_results"][0]["answer_correctness"] == 0.75
    assert QUESTION_MARKER not in json.dumps(saved, ensure_ascii=False)


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
