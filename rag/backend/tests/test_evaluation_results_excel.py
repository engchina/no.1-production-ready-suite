"""品質評価の結果 Excel と、所有者を検証する出力 API。"""

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.api.routes import evaluation as route
from app.clients.oracle import OracleClient
from app.main import app
from app.rag.evaluation_jobs import EvaluationJobNotFoundError
from app.rag.evaluation_results_excel import evaluation_results_xlsx
from app.schemas.evaluation import (
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationJob,
    EvaluationMetrics,
    EvaluationResultExportRequest,
)
from tests.support import AsgiTestClient


def metrics() -> EvaluationMetrics:
    return EvaluationMetrics(
        case_count=1,
        mrr=0.5,
        case_results=[
            EvaluationCaseResult(
                case_id="row-2",
                trace_id="trace-a",
                elapsed_ms=10,
                mrr=0.5,
                required_evidence_recall=0,
                failure_reasons=["required_evidence_missed"],
            )
        ],
    )


def inputs() -> EvaluationResultExportRequest:
    return EvaluationResultExportRequest.model_validate(
        {
            "profile_name": "プロファイル A",
            "compare_profile_name": "プロファイル B",
            "rows": [
                {
                    "row": 2,
                    "case_id": "row-2",
                    "question": "=2+2",
                    "evidence": ["資料 A.pdf"],
                    "standard_answer_preview": "短いプレビュー",
                    "required_answer_points": ["要点 A"],
                    "answer_process": "手順 A",
                }
            ],
            "cases": [
                {
                    "id": "row-2",
                    "query": "=2+2",
                    "standard_answer": "合成の回答です。" * 100,
                    "required_answer_points": ["要点 A"],
                }
            ],
        }
    )


def test_export_keeps_full_inputs_scores_unassessed_and_comparison() -> None:
    result = metrics()
    result.comparison = EvaluationComparison(
        search_answer_profile_id="profile-b", metrics=metrics()
    )
    book = load_workbook(BytesIO(evaluation_results_xlsx(result, inputs())))
    assert book.sheetnames == ["概要", "主条件の結果", "比較対象の結果"]
    sheet = book["主条件の結果"]
    assert sheet["B2"].value == "=2+2"
    assert sheet["B2"].data_type == "s"
    assert sheet["D2"].value == "合成の回答です。" * 100
    assert sheet["F2"].value == "手順 A"
    assert sheet["G2"].value == 0
    assert sheet["H2"].value == 0.5
    assert sheet["H2"].number_format == "0.0%"
    assert sheet["I2"].value == "未評価"
    assert sheet["V2"].value == "trace-a"
    assert "期待する応答の判断" not in [cell.value for cell in sheet[1]]
    assert sheet.freeze_panes == "B2"
    assert book["概要"]["C1"].value == "比較対象: プロファイル B"


def test_saved_old_result_keeps_its_verdict_without_retired_columns() -> None:
    """保存済みの古い結果（網羅・裏付けの値つき）は読めて、合否は保存した値のまま（#4781）。

    画面と Excel は正確性とコードで計算する指標だけを出し、古い列は出さない。合否は計算し直さない。
    """
    case = {
        "case_id": "row-2",
        "trace_id": "trace-a",
        "elapsed_ms": 10,
        "answer_correctness": 1.0,
        "requirement_coverage": 0.5,
        "claim_support_rate": 1.0,
        "evidence_coverage": 0.5,
        "failure_reasons": ["missing_content"],
    }
    saved = {
        "metrics_version": "six_metrics_v1",
        "case_count": 1,
        "answer_correctness": 1.0,
        "requirement_coverage": 0.5,
        "claim_support_rate": 1.0,
        "passed": False,
        "threshold_failures": [{"metric": "requirement_coverage", "actual": 0.5, "threshold": 0.9}],
        "failure_reason_counts": {"missing_content": 1},
        "case_results": [case],
    }
    result = EvaluationMetrics.model_validate(saved)
    assert result.passed is False
    assert result.metrics_version == "six_metrics_v1"
    assert result.threshold_failures[0].metric == "requirement_coverage"
    book = load_workbook(BytesIO(evaluation_results_xlsx(result, inputs())))
    headers = [cell.value for cell in book["主条件の結果"][1]]
    assert "回答の正確性" in headers
    assert not {"回答の網羅性", "根拠への忠実性", "根拠の網羅率（参考）"} & set(headers)
    summary = [row[0].value for row in book["概要"].iter_rows()]
    assert "回答の網羅性" not in summary
    reasons = book["主条件の結果"].cell(row=2, column=headers.index("失敗理由") + 1).value
    assert reasons == "標準回答の必要な項目が抜けている"


@pytest.mark.parametrize(
    "state,code", [("SUCCEEDED", 200), ("RUNNING", 409), ("FAILED", 409), ("missing", 404)]
)
def test_export_api_uses_authorized_job_and_rejects_unavailable_results(
    monkeypatch: pytest.MonkeyPatch, state: str, code: int
) -> None:
    class Service:
        async def get_with_owner(
            self, job_id: str
        ) -> tuple[EvaluationJob, tuple[str | None, str | None]]:
            return await self.get(job_id), (None, None)

        async def get(self, job_id: str) -> EvaluationJob:
            assert job_id == "job-a"
            if state == "missing":
                raise EvaluationJobNotFoundError("見つかりません。")
            return EvaluationJob.model_validate(
                dict(
                    job_id=job_id,
                    kind="run",
                    status=state,
                    total_cases=1,
                    completed_cases=1,
                    time_limit_seconds=60,
                    created_at=datetime.now(UTC),
                    run_result=metrics() if state == "SUCCEEDED" else None,
                )
            )

    monkeypatch.setattr(route, "get_evaluation_job_service", Service)

    async def citations(self: Any, trace_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        assert trace_ids == ["trace-a"]
        return {
            "trace-a": [
                {"source": "合成資料.pdf", "metadata": {"page": 3, "evidence_model_used": True}}
            ]
        }

    monkeypatch.setattr(OracleClient, "evaluation_citations_by_trace_ids", citations)

    body: dict[str, Any] = inputs().model_dump()
    body["metrics"] = {"mrr": 1}
    response = AsgiTestClient(app).post("/api/evaluation/jobs/job-a/results.xlsx", json=body)
    assert response.status_code == code
    if code == 200:
        book = load_workbook(BytesIO(response.content))
        assert book["主条件の結果"]["H2"].value == 0.5
        assert (
            book["主条件の結果"].cell(2, book["主条件の結果"].max_column).value
            == "合成資料.pdf | p.3"
        )
        assert "evaluation_results.xlsx" in response.headers["content-disposition"]


def test_export_includes_all_cases_beyond_one_page() -> None:
    result = metrics()
    result.case_results = [
        result.case_results[0].model_copy(update={"case_id": f"case-{i}"}) for i in range(40)
    ]
    result.case_count = 40
    sheet = load_workbook(
        BytesIO(evaluation_results_xlsx(result, EvaluationResultExportRequest()))
    )["主条件の結果"]
    assert sheet.max_row == 41
    assert sheet["A41"].value == "case-39"


@pytest.fixture(autouse=True)
def _empty_saved_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read(self: Any, trace_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        return {}

    monkeypatch.setattr(OracleClient, "evaluation_citations_by_trace_ids", read)


def test_generated_export_actual_evidence_is_separate_and_blank_for_old_records() -> None:
    result = metrics()
    result.comparison = EvaluationComparison(
        search_answer_profile_id="profile-b", metrics=metrics()
    )
    result.comparison.metrics.case_results[0].trace_id = "trace-b"
    book = load_workbook(
        BytesIO(
            evaluation_results_xlsx(result, inputs(), evidence={"trace-a": "合成資料.pdf | p.3"})
        )
    )
    primary = book["主条件の結果"]
    comparison = book["比較対象の結果"]
    headers = {cell.value: cell.column for cell in primary[1]}
    column = headers["回答に使用した根拠"]
    assert primary.cell(2, column).value == "合成資料.pdf | p.3"
    assert comparison.cell(2, column).value is None
    assert primary["C2"].value == "資料 A.pdf"
    assert primary.cell(2, column).alignment.wrap_text is True


async def test_export_reads_only_final_trace_citations_and_does_not_mix_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    async def read(self: Any, trace_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        calls.append(trace_ids)
        return {
            "trace-a": [
                {"source": "合成資料.pdf", "metadata": {"page": 3, "evidence_model_used": True}}
            ],
            "trace-b": [
                {"source": "比較資料.pdf", "metadata": {"page": 5, "evidence_model_used": True}}
            ],
        }

    monkeypatch.setattr(OracleClient, "evaluation_citations_by_trace_ids", read)
    result = metrics()
    result.comparison = EvaluationComparison(
        search_answer_profile_id="profile-b", metrics=metrics()
    )
    result.comparison.metrics.case_results[0].trace_id = "trace-b"
    assert await route._case_evidence(result) == {
        "trace-a": "合成資料.pdf | p.3",
        "trace-b": "比較資料.pdf | p.5",
    }
    assert calls == [["trace-a", "trace-b"]]
