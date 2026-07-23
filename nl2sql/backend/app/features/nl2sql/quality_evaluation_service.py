"""NL2SQL 品質評価の Excel 検証、非同期実行、集計、出力。"""

from __future__ import annotations

import base64
import io
import json
import logging
import socket
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]
from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]

from app.settings import get_settings

from .models import AllowedObjects, Nl2SqlEngine
from .quality_evaluation_models import (
    QualityEvaluationCapabilities,
    QualityEvaluationCase,
    QualityEvaluationDeterministicAnalysis,
    QualityEvaluationEngineCapability,
    QualityEvaluationEngineSummary,
    QualityEvaluationJobPage,
    QualityEvaluationJobRecord,
    QualityEvaluationJobSummary,
    QualityEvaluationJudge,
    QualityEvaluationJudgeCapability,
    QualityEvaluationLimits,
    QualityEvaluationResult,
    QualityEvaluationResultPage,
    QualityEvaluationStatus,
    QualityEvaluationVerdict,
    job_summary,
)
from .quality_evaluation_store import (
    MemoryQualityEvaluationRepository,
    OracleQualityEvaluationRepository,
    QualityEvaluationRepository,
)
from .service import GeneratedSql, Nl2SqlService, is_select_only, nl2sql_service, one_line_sql

logger = logging.getLogger(__name__)

_ENGINE_LABELS = {
    Nl2SqlEngine.SELECT_AI: "Select AI",
    Nl2SqlEngine.SELECT_AI_AGENT: "Select AI Agent",
    Nl2SqlEngine.ENTERPRISE_AI_DIRECT: "Enterprise AI Direct",
}
_ALLOWED_ENGINES = frozenset(_ENGINE_LABELS)
_HEADER_ALIASES = {
    "case_id": {"ケースID", "CASE_ID", "CASEID"},
    "question": {"質問", "QUESTION"},
    "expected_sql": {"期待SQL", "EXPECTED_SQL", "EXPECTEDSQL"},
}
_TERMINAL_STATUSES = {
    QualityEvaluationStatus.COMPLETED,
    QualityEvaluationStatus.COMPLETED_WITH_ERRORS,
    QualityEvaluationStatus.FAILED,
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def _decode_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return max(0, int(base64.urlsafe_b64decode(padded).decode("ascii")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("カーソルが不正です。") from exc


def _safe_excel_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _normalized_sql(sql: str) -> str:
    return one_line_sql(sql).rstrip(";").upper()


class QualityEvaluationValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


EngineRunner = Callable[[str, Nl2SqlEngine, str], GeneratedSql | str]
JudgeRunner = Callable[
    [str, str, str, str, QualityEvaluationDeterministicAnalysis], QualityEvaluationJudge
]


class QualityEvaluationService:
    def __init__(
        self,
        nl2sql: Nl2SqlService,
        *,
        repository: QualityEvaluationRepository | None = None,
        engine_runner: EngineRunner | None = None,
        judge_runner: JudgeRunner | None = None,
        readiness_provider: Callable[[], dict[Nl2SqlEngine, tuple[bool, str]]] | None = None,
    ) -> None:
        self._nl2sql = nl2sql
        self._repository = repository or self._build_repository()
        self._engine_runner = engine_runner
        self._judge_runner = judge_runner
        self._readiness_provider = readiness_provider
        self._dispatch_lock = threading.Lock()
        self._active_threads: dict[str, threading.Thread] = {}

    def _build_repository(self) -> QualityEvaluationRepository:
        settings = get_settings()
        if settings.nl2sql_persistence_mode.strip().lower() == "oracle":
            return OracleQualityEvaluationRepository(
                connection_factory=self._nl2sql._oracle_adapter.connection  # noqa: SLF001
            )
        return MemoryQualityEvaluationRepository()

    @property
    def repository_mode(self) -> str:
        return self._repository.mode

    def capabilities(self) -> QualityEvaluationCapabilities:
        settings = get_settings()
        readiness = (
            self._readiness_provider()
            if self._readiness_provider
            else self._nl2sql.quality_evaluation_engine_readiness()
        )
        if self._engine_runner and not self._readiness_provider:
            readiness = {engine: (True, "") for engine in _ALLOWED_ENGINES}
        judge_ready = bool(
            self._judge_runner
            or self._nl2sql._enterprise_ai_client.is_configured()  # noqa: SLF001
        )
        return QualityEvaluationCapabilities(
            engines=[
                QualityEvaluationEngineCapability(
                    engine=engine,
                    label=_ENGINE_LABELS[engine],
                    available=readiness.get(engine, (False, "利用できません。"))[0],
                    reason=readiness.get(engine, (False, "利用できません。"))[1],
                )
                for engine in _ENGINE_LABELS
            ],
            judge=QualityEvaluationJudgeCapability(
                available=judge_ready,
                reason="" if judge_ready else "OCI Enterprise AI Judge が構成されていません。",
            ),
            limits=QualityEvaluationLimits(
                max_file_bytes=settings.nl2sql_quality_evaluation_max_file_bytes,
                max_cases=settings.nl2sql_quality_evaluation_max_cases,
                max_attempts=settings.nl2sql_quality_evaluation_max_attempts,
            ),
        )

    def template_workbook(self) -> bytes:
        workbook = Workbook()
        cases = workbook.active
        cases.title = "cases"
        cases.append(["ケースID", "質問", "期待SQL"])
        cases.append(
            [
                "CASE-001",
                "部門ごとの売上合計を取得してください",
                "SELECT department_id, SUM(amount) FROM sales GROUP BY department_id",
            ]
        )
        readme = workbook.create_sheet("記入方法", 0)
        readme.append(["NL2SQL 品質評価テンプレート"])
        readme.append(["必須列", "質問、期待SQL"])
        readme.append(["任意列", "ケースID（空欄時は自動付与）"])
        readme.append(["注意", "期待SQLは SELECT/WITH のみ。数式セルは使用できません。"])
        for sheet in (cases, readme):
            sheet.freeze_panes = "A2"
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E78")
        cases.auto_filter.ref = "A1:C2"
        for column, width in {"A": 18, "B": 48, "C": 72}.items():
            cases.column_dimensions[column].width = width
        readme.column_dimensions["A"].width = 24
        readme.column_dimensions["B"].width = 76
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def parse_cases(self, content: bytes, filename: str) -> list[QualityEvaluationCase]:
        limits = self.capabilities().limits
        errors: list[str] = []
        if Path(filename).suffix.lower() != ".xlsx":
            errors.append(".xlsx ファイルのみアップロードできます。")
        if len(content) > limits.max_file_bytes:
            errors.append(
                "ファイルサイズが上限 "
                f"{limits.max_file_bytes // (1024 * 1024)} MiB を超えています。"
            )
        if errors:
            raise QualityEvaluationValidationError(errors)
        try:
            workbook = load_workbook(io.BytesIO(content), data_only=False, read_only=False)
        except Exception as exc:
            raise QualityEvaluationValidationError(
                ["ファイルを Excel ブックとして読み込めません。"]
            ) from exc
        sheet = next(
            (item for item in workbook.worksheets if item.title.strip().casefold() == "cases"),
            workbook.active,
        )
        header_map: dict[str, int] = {}
        for index, cell in enumerate(sheet[1], start=1):
            if cell.data_type == "f":
                errors.append("行 1: ヘッダーに数式は使用できません。")
                continue
            value = str(cell.value or "").strip().upper().replace(" ", "")
            for field, aliases in _HEADER_ALIASES.items():
                if value in aliases:
                    header_map[field] = index
        required_headers = (
            ("question", "質問 / QUESTION"),
            ("expected_sql", "期待SQL / EXPECTED_SQL"),
        )
        for field, label in required_headers:
            if field not in header_map:
                errors.append(f"行 1: 必須ヘッダー「{label}」がありません。")
        if errors:
            raise QualityEvaluationValidationError(errors)

        cases: list[QualityEvaluationCase] = []
        seen_case_ids: set[str] = set()
        input_case_count = 0
        for row_number in range(2, sheet.max_row + 1):
            tracked_columns = [header_map["question"], header_map["expected_sql"]]
            if "case_id" in header_map:
                tracked_columns.append(header_map["case_id"])
            cells = [sheet.cell(row_number, column) for column in tracked_columns]
            if all(cell.value is None or str(cell.value).strip() == "" for cell in cells):
                continue
            input_case_count += 1
            formula_fields = [
                name
                for name, column in header_map.items()
                if sheet.cell(row_number, column).data_type == "f"
            ]
            if formula_fields:
                errors.append(
                    f"行 {row_number}: 数式セルは使用できません（{', '.join(formula_fields)}）。"
                )
                continue
            question = str(sheet.cell(row_number, header_map["question"]).value or "").strip()
            expected_sql = str(
                sheet.cell(row_number, header_map["expected_sql"]).value or ""
            ).strip()
            case_id_value = (
                str(sheet.cell(row_number, header_map["case_id"]).value or "").strip()
                if "case_id" in header_map
                else ""
            )
            case_id = case_id_value or f"CASE-{input_case_count:04d}"
            if not question:
                errors.append(f"行 {row_number}: 質問は必須です。")
            if not expected_sql:
                errors.append(f"行 {row_number}: 期待SQLは必須です。")
            elif not is_select_only(expected_sql):
                errors.append(f"行 {row_number}: 期待SQLは SELECT/WITH のみ指定できます。")
            duplicate_case_id = case_id in seen_case_ids
            if duplicate_case_id:
                errors.append(f"行 {row_number}: ケースID「{case_id}」が重複しています。")
            else:
                # 不正な行も含め、ファイル全体で Case ID の一意性を検証する。
                seen_case_ids.add(case_id)
            if (
                question
                and expected_sql
                and is_select_only(expected_sql)
                and not duplicate_case_id
            ):
                cases.append(
                    QualityEvaluationCase(
                        case_no=len(cases) + 1,
                        case_id=case_id,
                        excel_row=row_number,
                        question=question,
                        expected_sql=expected_sql,
                    )
                )
        if not cases and not errors:
            errors.append("評価ケースがありません。")
        if len(cases) > limits.max_cases:
            errors.append(f"ケース数が上限 {limits.max_cases} 件を超えています。")
        if errors:
            raise QualityEvaluationValidationError(errors)
        return cases

    def submit(
        self,
        *,
        profile_id: str,
        engines: list[Nl2SqlEngine],
        repeat_count: int,
        content: bytes,
        filename: str,
        actor_user_id: str = "",
    ) -> QualityEvaluationJobSummary:
        capabilities = self.capabilities()
        if not capabilities.judge.available:
            raise QualityEvaluationValidationError([capabilities.judge.reason])
        if not 1 <= repeat_count <= 10:
            raise QualityEvaluationValidationError(["繰り返し回数は 1〜10 で指定してください。"])
        deduplicated_engines = list(dict.fromkeys(engines))
        if not deduplicated_engines:
            raise QualityEvaluationValidationError(["実行エンジンを1つ以上選択してください。"])
        if any(engine not in _ALLOWED_ENGINES for engine in deduplicated_engines):
            raise QualityEvaluationValidationError(["未対応の実行エンジンが指定されています。"])
        readiness = {item.engine: item for item in capabilities.engines}
        unavailable = [
            readiness[engine].reason
            for engine in deduplicated_engines
            if not readiness[engine].available
        ]
        if unavailable:
            raise QualityEvaluationValidationError(unavailable)
        try:
            profile = self._nl2sql.get_profile(profile_id)
        except ValueError as exc:
            raise QualityEvaluationValidationError([str(exc)]) from exc
        cases = self.parse_cases(content, filename)
        total_attempts = len(cases) * len(deduplicated_engines) * repeat_count
        if total_attempts > capabilities.limits.max_attempts:
            raise QualityEvaluationValidationError(
                [
                    f"総試行回数 {total_attempts} が上限 "
                    f"{capabilities.limits.max_attempts} 回を超えています。"
                ]
            )
        now = _utc_now()
        job = QualityEvaluationJobRecord(
            job_id=str(uuid.uuid4()),
            profile_id=profile.id,
            profile_name=profile.name,
            engines=deduplicated_engines,
            repeat_count=repeat_count,
            cases=cases,
            total_attempts=total_attempts,
            actor_user_id=actor_user_id,
            input_filename=Path(filename).name[:255],
            created_at=now,
            updated_at=now,
        )
        self._repository.save_job(job)
        logger.info(
            "quality_evaluation_submitted",
            extra={
                "job_id": job.job_id,
                "profile_id": job.profile_id,
                "engine_count": len(job.engines),
                "case_count": len(job.cases),
                "repeat_count": job.repeat_count,
            },
        )
        if get_settings().nl2sql_quality_evaluation_worker_mode.strip().lower() == "inprocess":
            self._dispatch(job.job_id)
        return job_summary(job)

    def _dispatch(self, job_id: str) -> None:
        with self._dispatch_lock:
            current = self._active_threads.get(job_id)
            if current and current.is_alive():
                return
            worker = threading.Thread(
                target=self.run_job,
                kwargs={"job_id": job_id},
                name=f"nl2sql-quality-evaluation-{job_id[:8]}",
                daemon=True,
            )
            self._active_threads[job_id] = worker
            worker.start()

    def get_job(self, job_id: str) -> QualityEvaluationJobSummary:
        job = self._repository.get_job(job_id)
        if job is None:
            raise ValueError("指定された品質評価 job が見つかりません。")
        return job_summary(job)

    def list_jobs(self, *, cursor: str | None, limit: int) -> QualityEvaluationJobPage:
        offset = _decode_offset(cursor)
        page_size = min(max(limit, 1), 100)
        jobs, total = self._repository.list_jobs(offset=offset, limit=page_size)
        next_offset = offset + len(jobs)
        return QualityEvaluationJobPage(
            items=[job_summary(item) for item in jobs],
            next_cursor=_encode_offset(next_offset) if next_offset < total else None,
            total=total,
        )

    def list_results(
        self, *, job_id: str, cursor: str | None, limit: int
    ) -> QualityEvaluationResultPage:
        if self._repository.get_job(job_id) is None:
            raise ValueError("指定された品質評価 job が見つかりません。")
        offset = _decode_offset(cursor)
        page_size = min(max(limit, 1), 100)
        results, total = self._repository.list_results(
            job_id=job_id, offset=offset, limit=page_size
        )
        next_offset = offset + len(results)
        return QualityEvaluationResultPage(
            items=results,
            next_cursor=_encode_offset(next_offset) if next_offset < total else None,
            total=total,
        )

    def run_next_job(self, *, worker_id: str | None = None) -> bool:
        claimed = self._repository.claim_job(
            worker_id=worker_id or self._worker_id(),
            lease_seconds=get_settings().nl2sql_quality_evaluation_lease_seconds,
        )
        if claimed is None:
            return False
        self._process_claimed_job(claimed)
        return True

    def run_job(self, *, job_id: str, worker_id: str | None = None) -> None:
        claimed = self._repository.claim_job(
            worker_id=worker_id or self._worker_id(),
            lease_seconds=get_settings().nl2sql_quality_evaluation_lease_seconds,
            job_id=job_id,
        )
        if claimed is not None:
            self._process_claimed_job(claimed)

    def _worker_id(self) -> str:
        return f"{socket.gethostname()}:{threading.get_native_id()}:{uuid.uuid4().hex[:8]}"

    def _process_claimed_job(self, job: QualityEvaluationJobRecord) -> None:
        try:
            for case in job.cases:
                for engine in job.engines:
                    for repetition in range(1, job.repeat_count + 1):
                        if self._repository.has_result(
                            job_id=job.job_id,
                            case_no=case.case_no,
                            engine=engine.value,
                            repetition_no=repetition,
                        ):
                            continue
                        now = datetime.now(UTC)
                        job = job.model_copy(
                            update={
                                "current_case_id": case.case_id,
                                "current_engine": engine,
                                "current_repetition": repetition,
                                "heartbeat_at": now.isoformat(),
                                "lease_expires_at": (
                                    now
                                    + timedelta(
                                        seconds=max(
                                            30.0,
                                            get_settings().nl2sql_quality_evaluation_lease_seconds,
                                        )
                                    )
                                ).isoformat(),
                                "updated_at": now.isoformat(),
                            },
                            deep=True,
                        )
                        self._repository.save_job(job)
                        result = self._evaluate_attempt(job, case, engine, repetition)
                        self._repository.save_result(result)
                        job = self._refresh_progress(job)
            results = self._repository.all_results(job.job_id)
            errors = sum(1 for item in results if item.generation_error or item.judge_error)
            finished = _utc_now()
            job = job.model_copy(
                update={
                    "status": (
                        QualityEvaluationStatus.COMPLETED_WITH_ERRORS
                        if errors
                        else QualityEvaluationStatus.COMPLETED
                    ),
                    "completed_attempts": len(results),
                    "success_count": sum(item.generation_succeeded for item in results),
                    "error_count": errors,
                    "engine_summaries": self._summaries(job, results),
                    "current_case_id": "",
                    "current_engine": None,
                    "current_repetition": 0,
                    "heartbeat_at": finished,
                    "lease_expires_at": None,
                    "finished_at": finished,
                    "updated_at": finished,
                },
                deep=True,
            )
            self._repository.save_job(job)
            logger.info(
                "quality_evaluation_completed",
                extra={
                    "job_id": job.job_id,
                    "profile_id": job.profile_id,
                    "completed_attempts": job.completed_attempts,
                    "error_count": job.error_count,
                    "status": job.status.value,
                },
            )
        except Exception as exc:
            logger.exception(
                "quality_evaluation_failed",
                extra={"job_id": job.job_id, "profile_id": job.profile_id},
            )
            failed = job.model_copy(
                update={
                    "status": QualityEvaluationStatus.FAILED,
                    "error_message": str(exc)[:1000],
                    "lease_expires_at": None,
                    "finished_at": _utc_now(),
                    "updated_at": _utc_now(),
                },
                deep=True,
            )
            self._repository.save_job(failed)

    def _refresh_progress(self, job: QualityEvaluationJobRecord) -> QualityEvaluationJobRecord:
        results = self._repository.all_results(job.job_id)
        now = _utc_now()
        refreshed = job.model_copy(
            update={
                "completed_attempts": len(results),
                "success_count": sum(item.generation_succeeded for item in results),
                "error_count": sum(
                    bool(item.generation_error or item.judge_error) for item in results
                ),
                "engine_summaries": self._summaries(job, results),
                "heartbeat_at": now,
                "updated_at": now,
            },
            deep=True,
        )
        return self._repository.save_job(refreshed)

    def _evaluate_attempt(
        self,
        job: QualityEvaluationJobRecord,
        case: QualityEvaluationCase,
        engine: Nl2SqlEngine,
        repetition: int,
    ) -> QualityEvaluationResult:
        total_started = time.perf_counter()
        generated_sql = ""
        generation_error = ""
        judge_error = ""
        judge: QualityEvaluationJudge | None = None
        analysis = QualityEvaluationDeterministicAnalysis()
        generation_started = time.perf_counter()
        try:
            generated = (
                self._engine_runner(case.question, engine, job.profile_id)
                if self._engine_runner
                else self._nl2sql.generate_sql_strict_for_quality_evaluation(
                    question=case.question, engine=engine, profile_id=job.profile_id
                )
            )
            generated_sql = (
                generated.generated_sql if isinstance(generated, GeneratedSql) else generated
            ).strip()
            if not generated_sql:
                raise RuntimeError("選択された engine が SQL を返しませんでした。")
        except Exception as exc:
            generation_error = str(exc)[:1000]
        generation_elapsed_ms = round((time.perf_counter() - generation_started) * 1000)
        judge_elapsed_ms = 0
        if generated_sql:
            try:
                allowed = self._nl2sql.resolve_allowed_objects(job.profile_id, AllowedObjects())
                local = self._nl2sql.analyze_sql(
                    generated_sql,
                    allowed,
                    self._nl2sql.get_profile(job.profile_id).default_row_limit,
                    use_llm=False,
                )
                analysis = QualityEvaluationDeterministicAnalysis(
                    is_safe=local.safety.is_safe,
                    is_select_only=local.safety.is_select_only,
                    referenced_objects=local.object_names,
                    structure_summary=local.structure_summary,
                    risk_findings=local.risk_findings,
                )
            except Exception as exc:
                analysis = QualityEvaluationDeterministicAnalysis(
                    risk_findings=[f"決定論的 SQL 解析に失敗しました: {str(exc)[:500]}"]
                )
            judge_started = time.perf_counter()
            try:
                judge = (
                    self._judge_runner(
                        case.question,
                        case.expected_sql,
                        generated_sql,
                        job.profile_id,
                        analysis,
                    )
                    if self._judge_runner
                    else self._judge(
                        question=case.question,
                        expected_sql=case.expected_sql,
                        generated_sql=generated_sql,
                        profile_id=job.profile_id,
                        analysis=analysis,
                    )
                )
            except Exception as exc:
                judge_error = str(exc)[:1000]
                judge = None
            judge_elapsed_ms = round((time.perf_counter() - judge_started) * 1000)
        result = QualityEvaluationResult(
            result_id=str(uuid.uuid4()),
            job_id=job.job_id,
            case_no=case.case_no,
            case_id=case.case_id,
            excel_row=case.excel_row,
            question=case.question,
            expected_sql=case.expected_sql,
            engine=engine,
            repetition_no=repetition,
            generated_sql=generated_sql,
            normalized_sql=_normalized_sql(generated_sql) if generated_sql else "",
            deterministic_analysis=analysis,
            generation_elapsed_ms=generation_elapsed_ms,
            judge_elapsed_ms=judge_elapsed_ms,
            total_elapsed_ms=round((time.perf_counter() - total_started) * 1000),
            verdict=judge.verdict if judge else QualityEvaluationVerdict.NOT_ANALYZED,
            judge=judge,
            generation_error=generation_error,
            judge_error=judge_error,
            created_at=_utc_now(),
        )
        logger.info(
            "quality_evaluation_attempt_completed",
            extra={
                "job_id": job.job_id,
                "profile_id": job.profile_id,
                "engine": engine.value,
                "case_no": case.case_no,
                "repetition_no": repetition,
                "generation_elapsed_ms": generation_elapsed_ms,
                "judge_elapsed_ms": judge_elapsed_ms,
                "generation_succeeded": result.generation_succeeded,
                "verdict": result.verdict.value,
            },
        )
        return result

    def _judge(
        self,
        *,
        question: str,
        expected_sql: str,
        generated_sql: str,
        profile_id: str,
        analysis: QualityEvaluationDeterministicAnalysis,
    ) -> QualityEvaluationJudge:
        profile = self._nl2sql.get_profile(profile_id)
        allowed = self._nl2sql.resolve_allowed_objects(profile_id, AllowedObjects())
        schema_context = self._nl2sql._enterprise_ai_schema_context(  # noqa: SLF001
            profile=profile, allowed=allowed
        )
        system_prompt = (
            "あなたは Oracle SQL の品質評価者です。SQL を実行せず、質問に対する期待 SQL "
            "と生成 SQL の意味が等価かを判定してください。表現の違いではなく、結合、条件、集計、"
            "NULL、重複、順序、行数制限の意味を比較します。必ず JSON object だけを返し、"
            "verdict は correct / incorrect / uncertain のいずれか、confidence は 0〜1、"
            "summary は日本語、differences と risks は日本語文字列配列、"
            "correction_suggestion は日本語文字列とします。"
        )
        prompt = json.dumps(
            {
                "question": question,
                "expected_sql": expected_sql,
                "generated_sql": generated_sql,
                "deterministic_analysis": analysis.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        raw = self._nl2sql._enterprise_ai_client.generate(  # noqa: SLF001
            prompt=prompt,
            context=schema_context,
            system_prompt=system_prompt,
        )
        payload = self._nl2sql._json_object_from_text(raw)  # noqa: SLF001
        return QualityEvaluationJudge.model_validate(payload)

    def _summaries(
        self, job: QualityEvaluationJobRecord, results: list[QualityEvaluationResult]
    ) -> list[QualityEvaluationEngineSummary]:
        by_engine: dict[Nl2SqlEngine, list[QualityEvaluationResult]] = defaultdict(list)
        for result in results:
            by_engine[result.engine].append(result)
        summaries: list[QualityEvaluationEngineSummary] = []
        for engine in job.engines:
            items = by_engine[engine]
            successes = [item for item in items if item.generation_succeeded]
            verdicts = Counter(item.verdict for item in items)
            by_case: dict[int, list[str]] = defaultdict(list)
            for item in successes:
                by_case[item.case_no].append(item.normalized_sql)
            consistency_values = []
            for values in by_case.values():
                counts = Counter(values)
                consistency_values.append(max(counts.values()) / len(values))
            summaries.append(
                QualityEvaluationEngineSummary(
                    engine=engine,
                    total_attempts=len(items),
                    generation_successes=len(successes),
                    generation_success_rate=(len(successes) / len(items) if items else 0.0),
                    correct=verdicts[QualityEvaluationVerdict.CORRECT],
                    incorrect=verdicts[QualityEvaluationVerdict.INCORRECT],
                    uncertain=verdicts[QualityEvaluationVerdict.UNCERTAIN],
                    not_analyzed=verdicts[QualityEvaluationVerdict.NOT_ANALYZED],
                    normalized_sql_consistency=(
                        sum(consistency_values) / len(consistency_values)
                        if consistency_values
                        else 0.0
                    ),
                    error_count=sum(
                        bool(item.generation_error or item.judge_error) for item in items
                    ),
                )
            )
        return summaries

    def results_workbook(self, job_id: str) -> tuple[str, bytes]:
        job = self._repository.get_job(job_id)
        if job is None:
            raise ValueError("指定された品質評価 job が見つかりません。")
        if job.status not in _TERMINAL_STATUSES:
            raise ValueError("評価が完了していないため Excel をダウンロードできません。")
        results = self._repository.all_results(job_id)
        workbook = Workbook()
        summary = workbook.active
        summary.title = "概要"
        summary.append(["項目", "値"])
        summary_rows: list[list[Any]] = [
            ["Job ID", job.job_id],
            ["Profile", job.profile_name],
            ["状態", job.status.value],
            ["ケース数", len(job.cases)],
            ["繰り返し回数", job.repeat_count],
            ["総試行回数", job.total_attempts],
            ["注記", "LLM 判定は補助意見であり、SQL のデータベース実行結果ではありません。"],
        ]
        for row in summary_rows:
            summary.append([_safe_excel_value(value) for value in row])
        summary.append([])
        summary.append(
            [
                "エンジン",
                "生成成功率",
                "correct",
                "incorrect",
                "uncertain",
                "not_analyzed",
                "SQL一致性",
                "エラー数",
            ]
        )
        for engine_summary in self._summaries(job, results):
            summary.append(
                [
                    _ENGINE_LABELS[engine_summary.engine],
                    engine_summary.generation_success_rate,
                    engine_summary.correct,
                    engine_summary.incorrect,
                    engine_summary.uncertain,
                    engine_summary.not_analyzed,
                    engine_summary.normalized_sql_consistency,
                    engine_summary.error_count,
                ]
            )
        details = workbook.create_sheet("評価結果")
        headers = [
            "ケース番号",
            "ケースID",
            "Excel行",
            "質問",
            "期待SQL",
            "エンジン",
            "繰り返し番号",
            "生成SQL",
            "正規化SQL",
            "安全",
            "SELECTのみ",
            "参照オブジェクト",
            "構造要約",
            "リスク",
            "生成時間(ms)",
            "LLM分析時間(ms)",
            "総時間(ms)",
            "LLM判定",
            "確信度",
            "LLM分析概要",
            "差分",
            "LLMリスク",
            "修正提案",
            "生成エラー",
            "LLM分析エラー",
            "人手判定",
            "人手コメント",
        ]
        details.append(headers)
        for result in results:
            judge = result.judge
            row = [
                result.case_no,
                result.case_id,
                result.excel_row,
                result.question,
                result.expected_sql,
                _ENGINE_LABELS[result.engine],
                result.repetition_no,
                result.generated_sql,
                result.normalized_sql,
                "OK" if result.deterministic_analysis.is_safe else "NG",
                "OK" if result.deterministic_analysis.is_select_only else "NG",
                "\n".join(result.deterministic_analysis.referenced_objects),
                result.deterministic_analysis.structure_summary,
                "\n".join(result.deterministic_analysis.risk_findings),
                result.generation_elapsed_ms,
                result.judge_elapsed_ms,
                result.total_elapsed_ms,
                result.verdict.value,
                judge.confidence if judge else None,
                judge.summary if judge else "",
                "\n".join(judge.differences) if judge else "",
                "\n".join(judge.risks) if judge else "",
                judge.correction_suggestion if judge else "",
                result.generation_error,
                result.judge_error,
                "",
                "",
            ]
            details.append([_safe_excel_value(value) for value in row])
        self._format_workbook(summary, details, len(headers))
        buffer = io.BytesIO()
        workbook.save(buffer)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"nl2sql_quality_evaluation_{timestamp}_{job.job_id}.xlsx"
        return filename, buffer.getvalue()

    def _format_workbook(self, summary: Any, details: Any, detail_columns: int) -> None:
        header_fill = PatternFill("solid", fgColor="1F4E78")
        for cell in summary[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
        for cell in summary[10]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
        summary.freeze_panes = "A2"
        summary.column_dimensions["A"].width = 28
        summary.column_dimensions["B"].width = 88
        for row in summary.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in details[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        details.freeze_panes = "A2"
        details.auto_filter.ref = f"A1:{get_column_letter(detail_columns)}{max(details.max_row, 1)}"
        widths = [12, 18, 10, 42, 64, 24, 12, 64, 64, 10, 12, 32, 38, 38]
        widths += [14, 16, 14, 16, 12, 40, 40, 40, 48, 38, 38, 16, 40]
        for index, width in enumerate(widths, start=1):
            details.column_dimensions[get_column_letter(index)].width = width
        for row in details.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)


quality_evaluation_service = QualityEvaluationService(nl2sql_service)
