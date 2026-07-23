"""NL2SQL 品質評価の公開契約と永続レコード。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .models import Nl2SqlEngine


class QualityEvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class QualityEvaluationVerdict(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNCERTAIN = "uncertain"
    NOT_ANALYZED = "not_analyzed"


class QualityEvaluationCase(BaseModel):
    case_no: int = Field(ge=1)
    case_id: str
    excel_row: int = Field(ge=2)
    question: str
    expected_sql: str


class QualityEvaluationJudge(BaseModel):
    verdict: Literal[
        QualityEvaluationVerdict.CORRECT,
        QualityEvaluationVerdict.INCORRECT,
        QualityEvaluationVerdict.UNCERTAIN,
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    differences: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    correction_suggestion: str = ""


class QualityEvaluationDeterministicAnalysis(BaseModel):
    is_safe: bool = False
    is_select_only: bool = False
    referenced_objects: list[str] = Field(default_factory=list)
    structure_summary: str = ""
    risk_findings: list[str] = Field(default_factory=list)


class QualityEvaluationResult(BaseModel):
    result_id: str
    job_id: str
    case_no: int = Field(ge=1)
    case_id: str
    excel_row: int = Field(ge=2)
    question: str
    expected_sql: str
    engine: Nl2SqlEngine
    repetition_no: int = Field(ge=1, le=10)
    generated_sql: str = ""
    normalized_sql: str = ""
    deterministic_analysis: QualityEvaluationDeterministicAnalysis = Field(
        default_factory=QualityEvaluationDeterministicAnalysis
    )
    generation_elapsed_ms: int = Field(default=0, ge=0)
    judge_elapsed_ms: int = Field(default=0, ge=0)
    total_elapsed_ms: int = Field(default=0, ge=0)
    verdict: QualityEvaluationVerdict = QualityEvaluationVerdict.NOT_ANALYZED
    judge: QualityEvaluationJudge | None = None
    generation_error: str = ""
    judge_error: str = ""
    created_at: str

    @property
    def generation_succeeded(self) -> bool:
        return bool(self.generated_sql) and not self.generation_error


class QualityEvaluationEngineSummary(BaseModel):
    engine: Nl2SqlEngine
    total_attempts: int = 0
    generation_successes: int = 0
    generation_success_rate: float = 0.0
    correct: int = 0
    incorrect: int = 0
    uncertain: int = 0
    not_analyzed: int = 0
    normalized_sql_consistency: float = 0.0
    error_count: int = 0


class QualityEvaluationJobRecord(BaseModel):
    job_id: str
    profile_id: str
    profile_name: str
    engines: list[Nl2SqlEngine]
    repeat_count: int = Field(ge=1, le=10)
    cases: list[QualityEvaluationCase]
    status: QualityEvaluationStatus = QualityEvaluationStatus.PENDING
    total_attempts: int = Field(ge=1)
    completed_attempts: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    current_case_id: str = ""
    current_engine: Nl2SqlEngine | None = None
    current_repetition: int = 0
    engine_summaries: list[QualityEvaluationEngineSummary] = Field(default_factory=list)
    actor_user_id: str = ""
    input_filename: str = ""
    worker_id: str = ""
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    attempt_no: int = 0
    error_message: str = ""
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str


class QualityEvaluationJobSummary(BaseModel):
    job_id: str
    profile_id: str
    profile_name: str
    engines: list[Nl2SqlEngine]
    repeat_count: int
    case_count: int
    total_attempts: int
    completed_attempts: int
    success_count: int
    error_count: int
    status: QualityEvaluationStatus
    current_case_id: str = ""
    current_engine: Nl2SqlEngine | None = None
    current_repetition: int = 0
    engine_summaries: list[QualityEvaluationEngineSummary] = Field(default_factory=list)
    error_message: str = ""
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str


class QualityEvaluationJobPage(BaseModel):
    items: list[QualityEvaluationJobSummary]
    next_cursor: str | None = None
    total: int


class QualityEvaluationResultPage(BaseModel):
    items: list[QualityEvaluationResult]
    next_cursor: str | None = None
    total: int


class QualityEvaluationEngineCapability(BaseModel):
    engine: Nl2SqlEngine
    label: str
    available: bool
    reason: str = ""


class QualityEvaluationJudgeCapability(BaseModel):
    available: bool
    reason: str = ""
    provider: str = "OCI Enterprise AI"


class QualityEvaluationLimits(BaseModel):
    max_file_bytes: int
    max_cases: int
    max_attempts: int
    min_repeat_count: int = 1
    max_repeat_count: int = 10


class QualityEvaluationCapabilities(BaseModel):
    engines: list[QualityEvaluationEngineCapability]
    judge: QualityEvaluationJudgeCapability
    limits: QualityEvaluationLimits


def job_summary(job: QualityEvaluationJobRecord) -> QualityEvaluationJobSummary:
    return QualityEvaluationJobSummary(
        job_id=job.job_id,
        profile_id=job.profile_id,
        profile_name=job.profile_name,
        engines=job.engines,
        repeat_count=job.repeat_count,
        case_count=len(job.cases),
        total_attempts=job.total_attempts,
        completed_attempts=job.completed_attempts,
        success_count=job.success_count,
        error_count=job.error_count,
        status=job.status,
        current_case_id=job.current_case_id,
        current_engine=job.current_engine,
        current_repetition=job.current_repetition,
        engine_summaries=job.engine_summaries,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
    )
