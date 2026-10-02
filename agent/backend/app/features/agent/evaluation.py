"""業務 Agent の品質評価（#776）。

評価ケース（質問と期待する回答の要点）を 1 件ずつ業務 Agent の Run として実行し、回答を
OCI Enterprise AI の既定のテキストモデルで判定する（LLM-as-judge）。形は RAG の品質評価・
NL2SQL の SQL生成評価にそろえる（バックグラウンドの job・進み具合・取り消し・概要・ケース別結果）。

- Run は評価を始めた利用者の Run として作る（RAG / NL2SQL の MCP はこの利用者として呼ぶ）。
  `metadata.evaluation_job_id` で評価の Run と分かる。
- in-process のモードでは job が自分で Run を実行し、dispatcher のモードでは別プロセスが
  実行するのを待つ（二重に実行しない）。
- 承認が要るツールに達した Run は取り消し、「承認が必要なため評価できません」とする。
- job と結果はメモリに置く（再起動で消える。Oracle への永続化は Control Plane の永続化の後）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel, Field, computed_field, field_validator

from app.features.agent import builtin_runtime
from app.features.agent.runtime import (
    Artifact,
    RunCreateRequest,
    RunEventType,
    RunState,
    RunStatus,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


EVALUATION_MAX_CASES = 50
EVALUATION_QUESTION_MAX_CHARS = 4000
EVALUATION_EXPECTED_MAX_CHARS = 8000
# 残す job の数（古いものから消す）。
EVALUATION_JOBS_LIMIT = 20
# 1 ケースの上限時間（Run の実行。判定は別）。
CASE_TIMEOUT_SECONDS = 300.0
# dispatcher のモードで Run の決着を待つ間隔。テストは短くする。
settle_poll_seconds = 1.0


class EvaluationCase(BaseModel):
    """評価ケース。`id` を省くと `case-<番号>` にする。"""

    id: str = Field(default="", max_length=100)
    question: str = Field(min_length=1, max_length=EVALUATION_QUESTION_MAX_CHARS)
    expected: str = Field(min_length=1, max_length=EVALUATION_EXPECTED_MAX_CHARS)

    @field_validator("question", "expected")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("空白だけにはできません。")
        return value.strip()


class EvaluationRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=200)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=EVALUATION_MAX_CASES)

    @field_validator("cases")
    @classmethod
    def _unique_ids(cls, cases: list[EvaluationCase]) -> list[EvaluationCase]:
        numbered = [
            case if case.id.strip() else case.model_copy(update={"id": f"case-{index}"})
            for index, case in enumerate(cases, start=1)
        ]
        ids = [case.id.strip() for case in numbered]
        if len(set(ids)) != len(ids):
            raise ValueError("ケースの id が重複しています。")
        return numbered


class JudgeVerdict(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNCERTAIN = "uncertain"


class EvaluationJudgement(BaseModel):
    """判定のモデルの出力（structured output。範囲の制約は schema に入れず、読んだ後に丸める）。"""

    verdict: JudgeVerdict
    score: float
    summary: str
    missing_points: list[str]

    @field_validator("score")
    @classmethod
    def _clamp_score(cls, value: float) -> float:
        return min(1.0, max(0.0, value))

    @field_validator("summary")
    @classmethod
    def _short_summary(cls, value: str) -> str:
        return value.strip()[:2000]

    @field_validator("missing_points")
    @classmethod
    def _few_points(cls, value: list[str]) -> list[str]:
        return [point.strip()[:500] for point in value if point.strip()][:10]


class CaseStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    JUDGED = "judged"
    RUN_FAILED = "run_failed"
    NEEDS_APPROVAL = "needs_approval"
    TIMED_OUT = "timed_out"
    JUDGE_FAILED = "judge_failed"
    CANCELLED = "cancelled"


# 判定まで進まなかった（評価できなかった）状態。
_ERROR_STATUSES = {
    CaseStatus.RUN_FAILED,
    CaseStatus.NEEDS_APPROVAL,
    CaseStatus.TIMED_OUT,
    CaseStatus.JUDGE_FAILED,
}


class EvaluationCaseResult(BaseModel):
    case: EvaluationCase
    status: CaseStatus = CaseStatus.PENDING
    run_id: str | None = None
    answer: str = ""
    judgement: EvaluationJudgement | None = None
    # 評価できなかった理由（利用者向けの日本語）。
    error: str | None = None
    duration_ms: int | None = None


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


ACTIVE_JOB_STATUSES = {JobStatus.QUEUED, JobStatus.RUNNING}


class EvaluationSummary(BaseModel):
    total: int = 0
    completed: int = 0
    correct: int = 0
    incorrect: int = 0
    uncertain: int = 0
    errors: int = 0
    # 合格率 = 正しいと判定したケース / 全ケース（評価できなかったケースは不合格に数える）。
    pass_rate: float | None = None
    # 判定したケースのスコアの平均。
    average_score: float | None = None


def summarize(results: list[EvaluationCaseResult]) -> EvaluationSummary:
    summary = EvaluationSummary(total=len(results))
    scores: list[float] = []
    for result in results:
        if result.status not in {CaseStatus.PENDING, CaseStatus.RUNNING}:
            summary.completed += 1
        if result.status in _ERROR_STATUSES:
            summary.errors += 1
        judgement = result.judgement
        if result.status != CaseStatus.JUDGED or judgement is None:
            continue
        scores.append(judgement.score)
        if judgement.verdict == JudgeVerdict.CORRECT:
            summary.correct += 1
        elif judgement.verdict == JudgeVerdict.INCORRECT:
            summary.incorrect += 1
        else:
            summary.uncertain += 1
    finished = summary.completed - sum(
        1 for result in results if result.status == CaseStatus.CANCELLED
    )
    if finished > 0:
        summary.pass_rate = summary.correct / finished
    if scores:
        summary.average_score = sum(scores) / len(scores)
    return summary


class EvaluationJob(BaseModel):
    id: str = Field(default_factory=lambda: f"eval_{uuid4().hex}")
    agent_id: str
    agent_name: str = ""
    status: JobStatus = JobStatus.QUEUED
    created_by_user_uuid: str | None = None
    results: list[EvaluationCaseResult] = Field(default_factory=list)
    # job 全体の失敗（利用者向けの日本語）。
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> EvaluationSummary:
        return summarize(self.results)


class EvaluationJobItem(BaseModel):
    """最近の評価の一覧の 1 行（ケース別結果を含めない）。"""

    id: str
    agent_id: str
    agent_name: str
    status: JobStatus
    summary: EvaluationSummary
    created_at: datetime
    finished_at: datetime | None


class EvaluationJobsData(BaseModel):
    jobs: list[EvaluationJobItem] = Field(default_factory=list)


class EvaluationBusyError(RuntimeError):
    """ほかの評価を実行している（同時に動かす job は 1 つ）。"""


class EvaluationJobActiveError(RuntimeError):
    """実行中の job は削除できない。"""


class EvaluationStore:
    """評価の job（メモリ。新しい順に `EVALUATION_JOBS_LIMIT` 件まで残す）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, EvaluationJob] = OrderedDict()

    def create(self, job: EvaluationJob) -> EvaluationJob:
        with self._lock:
            if any(item.status in ACTIVE_JOB_STATUSES for item in self._jobs.values()):
                raise EvaluationBusyError(job.id)
            self._jobs[job.id] = job
            while len(self._jobs) > EVALUATION_JOBS_LIMIT:
                jobs = self._jobs.items()
                finished = [key for key, item in jobs if item.status not in ACTIVE_JOB_STATUSES]
                oldest = finished[0] if finished else None
                if oldest is None:
                    break
                self._jobs.pop(oldest)
            return job.model_copy(deep=True)

    def get(self, job_id: str) -> EvaluationJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job.model_copy(deep=True)

    def list(self) -> list[EvaluationJob]:
        with self._lock:
            return [job.model_copy(deep=True) for job in reversed(self._jobs.values())]

    def update(self, job_id: str, **changes: Any) -> EvaluationJob:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            return job.model_copy(deep=True)

    def update_result(self, job_id: str, index: int, **changes: Any) -> None:
        with self._lock:
            result = self._jobs[job_id].results[index]
            for key, value in changes.items():
                setattr(result, key, value)

    def start(self, job_id: str) -> EvaluationJob | None:
        """待っている job を実行中にする。取り消し済み・無い job は None（実行しない）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != JobStatus.QUEUED:
                return None
            job.status = JobStatus.RUNNING
            job.started_at = _now()
            return job.model_copy(deep=True)

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return job is None or job.status == JobStatus.CANCELLED

    def cancel(self, job_id: str) -> EvaluationJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in ACTIVE_JOB_STATUSES:
                job.status = JobStatus.CANCELLED
                job.finished_at = _now()
            return job.model_copy(deep=True)

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in ACTIVE_JOB_STATUSES:
                raise EvaluationJobActiveError(job_id)
            self._jobs.pop(job_id)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


evaluation_store = EvaluationStore()


def job_item(job: EvaluationJob) -> EvaluationJobItem:
    return EvaluationJobItem(
        id=job.id,
        agent_id=job.agent_id,
        agent_name=job.agent_name,
        status=job.status,
        summary=job.summary,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


JUDGE_INSTRUCTIONS = """あなたは業務 Agent の回答を採点する評価者です。
質問・期待する回答の要点・実際の回答を読み、実際の回答が期待する要点を満たしているかを判定します。

- verdict: 要点をすべて満たし、誤りが無ければ correct。要点の欠落・誤り・質問と関係のない回答なら
  incorrect。期待する要点があいまいで判断できないときだけ uncertain。
- score: 期待する要点のうち、実際の回答が正しく満たしている割合（0 から 1）。
- summary: 判定の理由を日本語で 1〜2 文。
- missing_points: 欠けている・誤っている要点を日本語で（無ければ空の配列）。
表現の違い・言い換え・補足の有無は減点しません。数値・固有名詞・結論の違いは誤りとして扱います。"""


def _judge_input(case: EvaluationCase, answer: str) -> str:
    return (
        f"## 質問\n{case.question}\n\n"
        f"## 期待する回答の要点\n{case.expected}\n\n"
        f"## 実際の回答\n{answer}"
    )


async def judge_answer(case: EvaluationCase, answer: str) -> EvaluationJudgement:
    """回答を判定する（OCI Enterprise AI の既定のテキストモデル。structured output）。"""
    target = builtin_runtime.resolve_model_target("")
    judge = Agent(
        name="evaluator",
        instructions=JUDGE_INSTRUCTIONS,
        model=builtin_runtime.model_factory(target),
        model_settings=ModelSettings(store=False),
        output_type=EvaluationJudgement,
    )
    result = await Runner.run(judge, _judge_input(case, answer), max_turns=1)
    output = result.final_output
    if isinstance(output, EvaluationJudgement):
        return output
    return EvaluationJudgement.model_validate(output)


def _answer_text(artifacts: list[Artifact]) -> str:
    for artifact in reversed(artifacts):
        if artifact.kind == "answer" and isinstance(artifact.content, dict):
            text = artifact.content.get("text")
            if isinstance(text, str):
                return text
    return ""


def _failure_message(run: RunState) -> str:
    for event in reversed(run.events):
        if event.type == RunEventType.RUNTIME_FAILED and event.message:
            return event.message
    return "業務 Agent の実行に失敗しました。"


_SETTLED = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.WAITING_APPROVAL,
}


async def _settle_run(run_id: str) -> RunState:
    """Run を決着（完了・失敗・取消・承認待ち）させて返す。上限時間を過ぎたら TimeoutError。"""
    from app.features.agent.runtime import runtime_repository

    in_process = get_settings().agent_runtime_dispatch_mode.strip().lower() == "in_process"
    deadline = time.monotonic() + CASE_TIMEOUT_SECONDS
    if in_process:
        await asyncio.wait_for(builtin_runtime.execute_run(run_id), CASE_TIMEOUT_SECONDS)
    while True:
        run = runtime_repository.get_run(run_id)
        if run.status in _SETTLED:
            return run
        if time.monotonic() >= deadline:
            raise TimeoutError(run_id)
        await asyncio.sleep(settle_poll_seconds)


async def run_evaluation_job(job_id: str, store: EvaluationStore = evaluation_store) -> None:
    """評価の job を実行する（ケースを 1 件ずつ。取り消されたら残りを取り消しにする）。"""
    from app.features.agent.runtime import runtime_repository

    job = store.start(job_id)
    if job is None:
        _cancel_remaining(job_id, store)
        return
    try:
        for index, result in enumerate(job.results):
            if store.is_cancelled(job_id):
                break
            await _run_case(job, index, result.case, store, runtime_repository)
    except Exception:  # noqa: BLE001 - job の境界では失敗を job に記録する
        logger.exception("agent_evaluation_failed", extra={"job_id": job_id})
        store.update(
            job_id,
            status=JobStatus.FAILED,
            error="評価を続けられませんでした。",
            finished_at=_now(),
        )
        return
    if store.is_cancelled(job_id):
        _cancel_remaining(job_id, store)
        return
    store.update(job_id, status=JobStatus.COMPLETED, finished_at=_now())


def _cancel_remaining(job_id: str, store: EvaluationStore) -> None:
    """取り消した job の、まだ終わっていないケースを取り消しにする。"""
    try:
        current = store.get(job_id)
    except KeyError:
        return
    for index, item in enumerate(current.results):
        if item.status in {CaseStatus.PENDING, CaseStatus.RUNNING}:
            store.update_result(job_id, index, status=CaseStatus.CANCELLED)


async def _run_case(
    job: EvaluationJob,
    index: int,
    case: EvaluationCase,
    store: EvaluationStore,
    repository: Any,
) -> None:
    started = time.monotonic()
    store.update_result(job.id, index, status=CaseStatus.RUNNING)

    def finish(status: CaseStatus, **changes: Any) -> None:
        store.update_result(
            job.id,
            index,
            status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
            **changes,
        )

    run = repository.create_builtin_run(
        RunCreateRequest(
            goal=case.question,
            agent_id=job.agent_id,
            metadata={"evaluation_job_id": job.id, "evaluation_case_id": case.id},
        ),
        created_by_user_uuid=job.created_by_user_uuid,
    )
    store.update_result(job.id, index, run_id=run.id)
    try:
        settled = await _settle_run(run.id)
    except TimeoutError:
        repository.cancel_run(run.id)
        finish(CaseStatus.TIMED_OUT, error="上限時間（300 秒）までに回答が出ませんでした。")
        return
    if store.is_cancelled(job.id) and settled.status not in {RunStatus.COMPLETED}:
        repository.cancel_run(run.id)
        finish(CaseStatus.CANCELLED)
        return
    if settled.status == RunStatus.WAITING_APPROVAL:
        repository.cancel_run(run.id)
        finish(
            CaseStatus.NEEDS_APPROVAL,
            error="承認が必要なツールを使うため評価できません（Run は取り消しました）。",
        )
        return
    if settled.status != RunStatus.COMPLETED:
        finish(CaseStatus.RUN_FAILED, error=_failure_message(settled))
        return
    answer = _answer_text(settled.artifacts)
    try:
        judgement = await judge_answer(case, answer)
    except Exception as exc:  # noqa: BLE001 - 判定の失敗はケースの結果に残して続ける
        logger.warning(
            "agent_evaluation_judge_failed",
            extra={"job_id": job.id, "error_type": type(exc).__name__},
        )
        message = (
            exc.message
            if isinstance(exc, builtin_runtime.BuiltinRuntimeError)
            else f"判定のモデルの呼び出しに失敗しました（{type(exc).__name__}）。"
        )
        finish(CaseStatus.JUDGE_FAILED, answer=answer, error=message)
        return
    finish(CaseStatus.JUDGED, answer=answer, judgement=judgement)
