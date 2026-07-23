from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]

from app.features.nl2sql.models import (
    Nl2SqlEngine,
    SampleDataMutationRequest,
    SampleDataStep,
)
from app.features.nl2sql.quality_evaluation_models import (
    QualityEvaluationCase,
    QualityEvaluationJobRecord,
    QualityEvaluationJudge,
    QualityEvaluationStatus,
    QualityEvaluationVerdict,
)
from app.features.nl2sql.quality_evaluation_service import (
    QualityEvaluationService,
    QualityEvaluationValidationError,
)
from app.features.nl2sql.quality_evaluation_store import MemoryQualityEvaluationRepository
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.main import app
from app.settings import get_settings


def _xlsx(
    rows: list[list[object]],
    *,
    headers: list[str] | None = None,
    sheet_name: str = "cases",
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(headers or ["ケースID", "質問", "期待SQL"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _service(**kwargs: object) -> QualityEvaluationService:
    return QualityEvaluationService(
        Nl2SqlService(store=MemoryNl2SqlStore()),
        repository=MemoryQualityEvaluationRepository(),
        **kwargs,  # type: ignore[arg-type]
    )


def _judge(*_args: object) -> QualityEvaluationJudge:
    return QualityEvaluationJudge(
        verdict=QualityEvaluationVerdict.CORRECT,
        confidence=0.92,
        summary="質問の意味と一致します。",
    )


def test_parse_cases_accepts_japanese_and_english_headers_and_active_sheet() -> None:
    service = _service()
    japanese = service.parse_cases(
        _xlsx([["JP-1", "一覧を取得", "SELECT * FROM orders"]]), "cases.xlsx"
    )
    english = service.parse_cases(
        _xlsx(
            [["EN-1", "count rows", "WITH q AS (SELECT 1 x FROM dual) SELECT * FROM q"]],
            headers=["CASE_ID", "QUESTION", "EXPECTED_SQL"],
            sheet_name="Sheet1",
        ),
        "cases.xlsx",
    )
    assert japanese[0].case_id == "JP-1"
    assert english[0].excel_row == 2


def test_parse_cases_ignores_empty_rows_and_assigns_case_id() -> None:
    cases = _service().parse_cases(
        _xlsx([[None, None, None], [None, "件数", "SELECT COUNT(*) FROM orders"]]),
        "cases.xlsx",
    )
    assert len(cases) == 1
    assert cases[0].case_id == "CASE-0001"
    assert cases[0].excel_row == 3


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([["A", "質問", "=A1"]], "数式セル"),
        (
            [["A", "質問1", "SELECT 1 FROM dual"], ["A", "質問2", "SELECT 2 FROM dual"]],
            "重複",
        ),
        ([["A", "質問", "DELETE FROM orders"]], "SELECT/WITH"),
        ([["A", "", "SELECT 1 FROM dual"]], "質問は必須"),
    ],
)
def test_parse_cases_rejects_entire_workbook_with_excel_row_errors(
    rows: list[list[object]], expected: str
) -> None:
    with pytest.raises(QualityEvaluationValidationError) as caught:
        _service().parse_cases(_xlsx(rows), "cases.xlsx")
    assert expected in str(caught.value)
    assert "行 " in str(caught.value)


def test_parse_cases_rejects_extension_and_file_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_max_file_bytes", 32)
    with pytest.raises(QualityEvaluationValidationError) as caught:
        _service().parse_cases(b"x" * 33, "cases.xls")
    assert ".xlsx" in str(caught.value)
    assert "ファイルサイズ" in str(caught.value)


def test_parse_cases_enforces_case_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_max_cases", 1)
    with pytest.raises(QualityEvaluationValidationError, match="ケース数が上限 1"):
        _service().parse_cases(
            _xlsx(
                [
                    ["A", "質問1", "SELECT 1 FROM dual"],
                    ["B", "質問2", "SELECT 2 FROM dual"],
                ]
            ),
            "cases.xlsx",
        )


def test_submit_validates_engines_repeat_profile_and_attempt_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(engine_runner=lambda *_args: "SELECT 1 FROM dual", judge_runner=_judge)
    workbook = _xlsx([["A", "質問", "SELECT 1 FROM dual"]])
    with pytest.raises(QualityEvaluationValidationError, match="1つ以上"):
        service.submit(
            profile_id="default",
            engines=[],
            repeat_count=1,
            content=workbook,
            filename="cases.xlsx",
        )
    with pytest.raises(QualityEvaluationValidationError, match="1〜10"):
        service.submit(
            profile_id="default",
            engines=[Nl2SqlEngine.SELECT_AI],
            repeat_count=11,
            content=workbook,
            filename="cases.xlsx",
        )
    with pytest.raises(QualityEvaluationValidationError, match="profile"):
        service.submit(
            profile_id="missing",
            engines=[Nl2SqlEngine.SELECT_AI],
            repeat_count=1,
            content=workbook,
            filename="cases.xlsx",
        )
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_max_attempts", 1)
    with pytest.raises(QualityEvaluationValidationError, match="総試行回数"):
        service.submit(
            profile_id="default",
            engines=[Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT],
            repeat_count=1,
            content=workbook,
            filename="cases.xlsx",
        )


def test_submit_accepts_repeat_boundaries_and_rejects_unavailable_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_worker_mode", "external")
    workbook = _xlsx([["A", "質問", "SELECT 1 FROM dual"]])
    service = _service(engine_runner=lambda *_args: "SELECT 1 FROM dual", judge_runner=_judge)
    assert service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.SELECT_AI],
        repeat_count=1,
        content=workbook,
        filename="cases.xlsx",
    ).total_attempts == 1
    assert service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.SELECT_AI],
        repeat_count=10,
        content=workbook,
        filename="cases.xlsx",
    ).total_attempts == 10

    unavailable = _service(
        engine_runner=lambda *_args: "SELECT 1 FROM dual",
        judge_runner=_judge,
        readiness_provider=lambda: {
            Nl2SqlEngine.SELECT_AI: (False, "Select AI profile が未構成です。")
        },
    )
    with pytest.raises(QualityEvaluationValidationError, match="未構成"):
        unavailable.submit(
            profile_id="default",
            engines=[Nl2SqlEngine.SELECT_AI],
            repeat_count=1,
            content=workbook,
            filename="cases.xlsx",
        )


def test_worker_runs_every_attempt_and_isolates_generation_and_judge_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[tuple[str, Nl2SqlEngine], int] = {}

    def engine(question: str, selected: Nl2SqlEngine, _profile: str) -> str:
        key = (question, selected)
        calls[key] = calls.get(key, 0) + 1
        if selected == Nl2SqlEngine.SELECT_AI_AGENT and calls[key] == 1:
            raise RuntimeError("strict engine failure")
        return "SELECT 1 FROM dual"

    def judge(
        question: str,
        _expected: str,
        _generated: str,
        _profile: str,
        _analysis: object,
    ) -> QualityEvaluationJudge:
        if question == "judge failure":
            raise RuntimeError("invalid judge response")
        return _judge()

    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_worker_mode", "external")
    service = _service(engine_runner=engine, judge_runner=judge)
    submitted = service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT],
        repeat_count=2,
        content=_xlsx(
            [
                ["A", "ok", "SELECT 1 FROM dual"],
                ["B", "judge failure", "SELECT 1 FROM dual"],
            ]
        ),
        filename="cases.xlsx",
    )
    service.run_job(job_id=submitted.job_id, worker_id="test-worker")
    job = service.get_job(submitted.job_id)
    page = service.list_results(job_id=submitted.job_id, cursor=None, limit=100)
    assert job.status == QualityEvaluationStatus.COMPLETED_WITH_ERRORS
    assert job.completed_attempts == 8
    assert len(page.items) == 8
    assert sum(bool(item.generation_error) for item in page.items) == 2
    assert sum(bool(item.judge_error) for item in page.items) == 3
    assert sum(item.verdict == QualityEvaluationVerdict.CORRECT for item in page.items) == 3


def test_worker_aggregates_verdict_distribution_and_normalized_consistency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_worker_mode", "external")
    call_count = 0

    def engine(*_args: object) -> str:
        nonlocal call_count
        call_count += 1
        return "SELECT 1 FROM dual" if call_count == 1 else "SELECT 2 FROM dual"

    def judge(
        _question: str,
        _expected: str,
        generated: str,
        _profile: str,
        _analysis: object,
    ) -> QualityEvaluationJudge:
        return QualityEvaluationJudge(
            verdict=(
                QualityEvaluationVerdict.CORRECT
                if "SELECT 1" in generated
                else QualityEvaluationVerdict.INCORRECT
            ),
            confidence=0.8,
            summary="比較しました。",
        )

    service = _service(engine_runner=engine, judge_runner=judge)
    submitted = service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.SELECT_AI],
        repeat_count=2,
        content=_xlsx([["A", "質問", "SELECT 1 FROM dual"]]),
        filename="cases.xlsx",
    )
    service.run_job(job_id=submitted.job_id)
    summary = service.get_job(submitted.job_id).engine_summaries[0]
    assert summary.generation_success_rate == 1.0
    assert summary.correct == 1
    assert summary.incorrect == 1
    assert summary.normalized_sql_consistency == 0.5


def test_worker_records_empty_engine_output_as_generation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_worker_mode", "external")
    service = _service(engine_runner=lambda *_args: "   ", judge_runner=_judge)
    submitted = service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.SELECT_AI],
        repeat_count=1,
        content=_xlsx([["A", "質問", "SELECT 1 FROM dual"]]),
        filename="cases.xlsx",
    )
    service.run_job(job_id=submitted.job_id)
    result = service.list_results(job_id=submitted.job_id, cursor=None, limit=1).items[0]
    assert result.generated_sql == ""
    assert "SQL を返しませんでした" in result.generation_error
    assert result.verdict == QualityEvaluationVerdict.NOT_ANALYZED
    assert service.get_job(submitted.job_id).status == QualityEvaluationStatus.COMPLETED_WITH_ERRORS


@pytest.mark.parametrize(
    "engine",
    [
        Nl2SqlEngine.SELECT_AI,
        Nl2SqlEngine.SELECT_AI_AGENT,
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
    ],
)
def test_strict_generation_never_uses_deterministic_fallback(
    engine: Nl2SqlEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.import_sample_data(
        SampleDataMutationRequest(step=SampleDataStep.ALL, confirmation="SQL_ASSIST_SAMPLE")
    )
    monkeypatch.setattr(
        service,
        "quality_evaluation_engine_readiness",
        lambda: {engine: (True, "")},
    )
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: False)
    monkeypatch.setattr(service._enterprise_ai_client, "is_configured", lambda: False)
    with pytest.raises(RuntimeError):
        service.generate_sql_strict_for_quality_evaluation(
            question="社員一覧",
            engine=engine,
            profile_id="default",
        )


def test_memory_repository_reclaims_expired_lease_and_preserves_result_idempotency() -> None:
    repository = MemoryQualityEvaluationRepository()
    now = datetime.now(UTC)
    job = QualityEvaluationJobRecord(
        job_id="job-1",
        profile_id="default",
        profile_name="default",
        engines=[Nl2SqlEngine.SELECT_AI],
        repeat_count=1,
        cases=[
            QualityEvaluationCase(
                case_no=1,
                case_id="A",
                excel_row=2,
                question="q",
                expected_sql="SELECT 1 FROM dual",
            )
        ],
        status=QualityEvaluationStatus.RUNNING,
        total_attempts=1,
        lease_expires_at=(now - timedelta(seconds=1)).isoformat(),
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    )
    repository.save_job(job)
    reclaimed = repository.claim_job(worker_id="new-worker", lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.worker_id == "new-worker"
    assert reclaimed.attempt_no == 1


def test_results_workbook_has_review_columns_format_and_formula_injection_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_worker_mode", "external")
    service = _service(
        engine_runner=lambda *_args: "SELECT 1 FROM dual",
        judge_runner=_judge,
    )
    submitted = service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.ENTERPRISE_AI_DIRECT],
        repeat_count=1,
        content=_xlsx([["+CASE", "+question", "SELECT 1 FROM dual"]]),
        filename="cases.xlsx",
    )
    service.run_job(job_id=submitted.job_id)
    filename, content = service.results_workbook(submitted.job_id)
    workbook = load_workbook(io.BytesIO(content))
    details = workbook["評価結果"]
    headers = [cell.value for cell in details[1]]
    assert filename.startswith("nl2sql_quality_evaluation_")
    assert workbook.sheetnames == ["概要", "評価結果"]
    assert headers[-2:] == ["人手判定", "人手コメント"]
    assert details.freeze_panes == "A2"
    assert details.auto_filter.ref is not None
    assert str(details.cell(2, 2).value).startswith("'")
    assert str(details.cell(2, 4).value).startswith("'")


def test_result_and_job_pagination_use_opaque_cursors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_quality_evaluation_worker_mode", "external")
    service = _service(
        engine_runner=lambda *_args: "SELECT 1 FROM dual",
        judge_runner=_judge,
    )
    first = service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.SELECT_AI],
        repeat_count=2,
        content=_xlsx([["A", "質問", "SELECT 1 FROM dual"]]),
        filename="cases.xlsx",
    )
    service.submit(
        profile_id="default",
        engines=[Nl2SqlEngine.SELECT_AI],
        repeat_count=1,
        content=_xlsx([["B", "質問", "SELECT 1 FROM dual"]]),
        filename="cases.xlsx",
    )
    service.run_job(job_id=first.job_id)

    jobs_page = service.list_jobs(cursor=None, limit=1)
    results_page = service.list_results(job_id=first.job_id, cursor=None, limit=1)
    assert len(jobs_page.items) == 1
    assert jobs_page.next_cursor
    assert len(service.list_jobs(cursor=jobs_page.next_cursor, limit=1).items) == 1
    assert len(results_page.items) == 1
    assert results_page.next_cursor
    assert len(
        service.list_results(
            job_id=first.job_id,
            cursor=results_page.next_cursor,
            limit=1,
        ).items
    ) == 1


def test_openapi_exposes_new_quality_flow_and_removes_legacy_evaluation_routes() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/api/nl2sql/quality-evaluations/capabilities",
        "/api/nl2sql/quality-evaluations/template.xlsx",
        "/api/nl2sql/quality-evaluations",
        "/api/nl2sql/quality-evaluations/{job_id}",
        "/api/nl2sql/quality-evaluations/{job_id}/results",
        "/api/nl2sql/quality-evaluations/{job_id}/results.xlsx",
    }
    assert expected <= set(paths)
    assert "/api/nl2sql/analyze" in paths
    assert "/api/nl2sql/reverse" in paths
    assert "/api/nl2sql/synthetic-data/generate" in paths
    removed = {
        "/api/nl2sql/evaluate",
        "/api/nl2sql/evaluation-sets",
        "/api/nl2sql/evaluation-runs",
        "/api/nl2sql/compare",
        "/api/nl2sql/synthetic-cases",
    }
    assert removed.isdisjoint(paths)
