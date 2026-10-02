"""業務 Agent の品質評価（#776）。

評価セット（業務 Agent ごとに保存する評価ケースの集まり）を 1 件ずつ業務 Agent の Run として実行し、
回答を OCI Enterprise AI の既定のテキストモデルで判定する（LLM-as-judge）。業界の評価の基盤
（評価セットの保存・表での編集と取り込み・ツールの選択の評価・前回との比較）と、RAG の
品質評価・NL2SQL の SQL生成評価の形（バックグラウンドの job・進み具合・取り消し・概要・
ケース別結果）にそろえる。

- Run は評価を始めた利用者の Run として作る（RAG / NL2SQL の MCP はこの利用者として呼ぶ）。
  `metadata.evaluation_job_id` で評価の Run と分かる。
- 評価の Run は承認が要るツールを実行しない（dry-run。組み込み Runtime が「評価中のため実行して
  いない」とモデルへ返して回答を完成させる）。呼ぼうとしたツールは step に残り、評価ケースの
  「期待するツール」と比べてツールの選択を判定する。
- in-process のモードでは job が自分で Run を実行し、dispatcher のモードでは別プロセスが
  実行するのを待つ（二重に実行しない）。
- 評価セットと job は Control Plane の定義と同じ保存先（`AGENT_CONTROL_PLANE_ITEMS`。#764）に置く。
  再起動の時点で実行中だった job は「中断」にする。
- 評価する版（公開中の版 / 下書き）を選び、job に残す。前回との比較は同じ評価ケースの job と
  だけ行う。フィードバック・Run の詳細からケースを足し（出どころの `source_run_id` を残す）、
  業種テンプレートの評価ケースで評価セットを作れる（#810）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel, Field, computed_field, field_validator

from app.features.agent import builtin_runtime
from app.features.agent.runtime import (
    EVALUATION_DRY_RUN_KEY,
    Artifact,
    RunCreateRequest,
    RunEventType,
    RunState,
    RunStatus,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

EVALUATION_MAX_CASES = 50
EVALUATION_QUESTION_MAX_CHARS = 4000
EVALUATION_EXPECTED_MAX_CHARS = 8000
EVALUATION_EXPECTED_TOOLS_MAX = 10
# 終わった job を残す期間（日）。利用状況・フィードバックの最長の期間（365 日）と同じ（#794）。
# 以前は件数（50 件）で消していたため、前回との比較・評価の履歴が 50 件より前を失っていた。
EVALUATION_JOBS_RETENTION_DAYS = 365
# メモリと保存先を守るための件数の上限（期間内でもこれを超えたら古い終わった job から消す）。
EVALUATION_JOBS_MAX = 2000
# 評価の履歴の一覧の 1 ページの既定と上限。
EVALUATION_JOBS_PAGE_SIZE = 10
EVALUATION_JOBS_PAGE_SIZE_MAX = 100
# 1 ケースの上限時間（Run の実行。判定は別）。
CASE_TIMEOUT_SECONDS = 300.0
# dispatcher のモードで Run の決着を待つ間隔。テストは短くする。
settle_poll_seconds = 1.0
# MCP 接続のツールの名前の区切り（`<接続>__<ツール>`。#757）。
_MCP_SEPARATOR = "__"


def _now() -> datetime:
    return datetime.now(UTC)


# ---- 評価セット -------------------------------------------------------------------------


class EvaluationCase(BaseModel):
    """評価ケース。`id` を省くと `case-<番号>` にする。"""

    id: str = Field(default="", max_length=100)
    question: str = Field(min_length=1, max_length=EVALUATION_QUESTION_MAX_CHARS)
    expected: str = Field(min_length=1, max_length=EVALUATION_EXPECTED_MAX_CHARS)
    # 呼ぶべきツール（任意）。`rag_search` のように MCP 接続の名前を省いてもよい。
    expected_tools: list[str] = Field(default_factory=list)
    # ケースの出どころ（フィードバック・Run の詳細から追加したときの Run の ID。#810）。
    source_run_id: str | None = Field(default=None, max_length=100)

    @field_validator("question", "expected")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("空白だけにはできません。")
        return value.strip()

    @field_validator("expected_tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if len(cleaned) > EVALUATION_EXPECTED_TOOLS_MAX:
            raise ValueError(f"期待するツールは {EVALUATION_EXPECTED_TOOLS_MAX} 個までです。")
        return cleaned


def normalize_question(question: str) -> str:
    """同じ質問かを比べるための形（前後と連続した空白を 1 つにする。#810）。"""
    return " ".join(question.split())


def next_case_id(cases: list[EvaluationCase]) -> str:
    """まだ使っていない `case-<番号>`（ケースの数 + 1 から探す）。"""
    used = {case.id for case in cases}
    number = len(cases) + 1
    while f"case-{number}" in used:
        number += 1
    return f"case-{number}"


def number_cases(cases: list[EvaluationCase]) -> list[EvaluationCase]:
    """id を省いたケースに `case-<番号>` を付け、重複を断る。"""
    numbered = [
        case if case.id.strip() else case.model_copy(update={"id": f"case-{index}"})
        for index, case in enumerate(cases, start=1)
    ]
    ids = [case.id.strip() for case in numbered]
    if len(set(ids)) != len(ids):
        raise ValueError("ケースの id が重複しています。")
    return [case.model_copy(update={"id": case.id.strip()}) for case in numbered]


class EvaluationSetInput(BaseModel):
    agent_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=EVALUATION_MAX_CASES)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名前を入力してください。")
        return value.strip()

    @field_validator("cases")
    @classmethod
    def _cases(cls, cases: list[EvaluationCase]) -> list[EvaluationCase]:
        return number_cases(cases)


class EvaluationSet(EvaluationSetInput):
    id: str = Field(default_factory=lambda: f"evset_{uuid4().hex}")
    created_by_user_uuid: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class EvaluationSetItem(BaseModel):
    """評価セットの一覧の 1 行（ケースを含めない）。"""

    id: str
    agent_id: str
    name: str
    description: str
    case_count: int
    updated_at: datetime
    last_job_id: str | None = None
    last_job_status: str | None = None
    last_pass_rate: float | None = None


class EvaluationSetsData(BaseModel):
    sets: list[EvaluationSetItem] = Field(default_factory=list)


class EvaluationCaseDraft(BaseModel):
    """Run（フィードバック・Run の詳細）から作る評価ケースの下書き（保存しない。#810）。"""

    agent_id: str
    agent_name: str
    source_run_id: str
    question: str
    # 管理者の評価のコメント（無ければ空。画面で必須の入力にする）。
    expected: str = ""
    # Run が呼んだ（呼ぼうとした）ツール。
    expected_tools: list[str] = Field(default_factory=list)
    # 同じ質問のケースを既に持つ評価セットの ID（重複して足さないため）。
    existing_set_ids: list[str] = Field(default_factory=list)


class EvaluationSetFromTemplateRequest(BaseModel):
    """業務 Agent の作成に使った業種テンプレートの評価ケースで評価セットを作る（#810）。"""

    agent_id: str = Field(min_length=1, max_length=200)


class EvaluationCasesData(BaseModel):
    """Excel から読んだケース（保存はしない。画面が評価セットのフォームに入れる）。"""

    cases: list[EvaluationCase]


# ---- 判定と結果 -------------------------------------------------------------------------


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
    # dry-run の後も承認待ちになった（通常は起きない。安全のために残す）。
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
_UNFINISHED_STATUSES = {CaseStatus.PENDING, CaseStatus.RUNNING}


class EvaluationCaseResult(BaseModel):
    case: EvaluationCase
    status: CaseStatus = CaseStatus.PENDING
    run_id: str | None = None
    answer: str = ""
    judgement: EvaluationJudgement | None = None
    # 業務 Agent が呼んだ（呼ぼうとした）ツール。dry-run で実行しなかったものを含む。
    tool_calls: list[str] = Field(default_factory=list)
    # 期待するツールをすべて呼んだか（期待するツールが無いケースは None）。
    tool_selection_correct: bool | None = None
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
    # 合格率 = 正しいと判定したケース / 終わったケース（評価できなかったケースは不合格に数える）。
    pass_rate: float | None = None
    # 判定したケースのスコアの平均。
    average_score: float | None = None
    # ツールの選択の正しさ（期待するツールを指定したケースのうち、すべて呼んだ割合）。
    tool_cases: int = 0
    tool_correct: int = 0
    tool_accuracy: float | None = None


def summarize(results: list[EvaluationCaseResult]) -> EvaluationSummary:
    summary = EvaluationSummary(total=len(results))
    scores: list[float] = []
    cancelled = 0
    for result in results:
        if result.status not in _UNFINISHED_STATUSES:
            summary.completed += 1
        if result.status == CaseStatus.CANCELLED:
            cancelled += 1
        if result.status in _ERROR_STATUSES:
            summary.errors += 1
        if result.tool_selection_correct is not None:
            summary.tool_cases += 1
            summary.tool_correct += int(result.tool_selection_correct)
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
    finished = summary.completed - cancelled
    if finished > 0:
        summary.pass_rate = summary.correct / finished
    if scores:
        summary.average_score = sum(scores) / len(scores)
    if summary.tool_cases:
        summary.tool_accuracy = summary.tool_correct / summary.tool_cases
    return summary


# 評価する版（#810）。published = 公開中の版、draft = 下書き。
EvaluationTarget = Literal["published", "draft"]
# job に残す版（版の番号か "draft"。#810 より前の job は None）。
AgentVersionRef = int | str | None


class EvaluationRequest(BaseModel):
    set_id: str = Field(min_length=1, max_length=100)
    # 省略すると、公開していない変更があれば下書き、なければ公開中の版（#810）。
    agent_version: EvaluationTarget | None = None


class EvaluationJob(BaseModel):
    id: str = Field(default_factory=lambda: f"eval_{uuid4().hex}")
    agent_id: str
    agent_name: str = ""
    set_id: str = ""
    set_name: str = ""
    # 評価した版（版の番号か "draft"。Run の `metadata.agent_version` と同じ値。#810）。
    agent_version: AgentVersionRef = None
    status: JobStatus = JobStatus.QUEUED
    created_by_user_uuid: str | None = None
    results: list[EvaluationCaseResult] = Field(default_factory=list)
    # job 全体の失敗（利用者向けの日本語）。
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # 同じ評価セット・同じ評価ケースの前回（完了した job）の概要。比較に使う（応答のときに入れる）。
    # ケースを変えた後の結果とは比べない。どの版どうしの比較かを画面に出す（#810）。
    previous_job_id: str | None = None
    previous_summary: EvaluationSummary | None = None
    previous_agent_version: AgentVersionRef = None
    previous_created_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> EvaluationSummary:
        return summarize(self.results)


class EvaluationJobItem(BaseModel):
    """最近の評価の一覧の 1 行（ケース別結果を含めない）。"""

    id: str
    agent_id: str
    agent_name: str
    set_id: str
    set_name: str
    agent_version: AgentVersionRef = None
    status: JobStatus
    summary: EvaluationSummary
    created_at: datetime
    finished_at: datetime | None


class EvaluationJobsData(BaseModel):
    jobs: list[EvaluationJobItem] = Field(default_factory=list)
    # 絞り込みに合う job の件数（ページングの総数）。
    total: int = 0
    offset: int = 0
    limit: int = EVALUATION_JOBS_PAGE_SIZE


class EvaluationBusyError(RuntimeError):
    """ほかの評価を実行している（同時に動かす job は 1 つ）。"""


class EvaluationJobActiveError(RuntimeError):
    """実行中の job は削除できない。"""


class EvaluationSetInUseError(RuntimeError):
    """実行中の評価が使っている評価セットは削除できない。"""


class EvaluationCaseDuplicateError(ValueError):
    """同じ質問のケースが評価セットに既にある（#810）。"""


class EvaluationSetFullError(ValueError):
    """評価セットのケースが上限（`EVALUATION_MAX_CASES`）に達している（#810）。"""


# ---- 保存（Control Plane の保存先。#764） -------------------------------------------------


def _save(kind: str, item_id: str, document: dict[str, Any]) -> None:
    from app.features.agent import control_plane_store

    control_plane_store.save_evaluation_item(kind, item_id, document)


def _delete(kind: str, item_id: str) -> None:
    from app.features.agent import control_plane_store

    control_plane_store.delete_evaluation_item(kind, item_id)


def _save_quietly(kind: str, item_id: str, document: dict[str, Any]) -> None:
    """job の途中経過の保存（失敗しても評価は続ける。終わりにもう一度保存する）。"""
    try:
        _save(kind, item_id, document)
    except Exception:  # noqa: BLE001 - 保存先の一時的な失敗で評価を止めない
        logger.warning("agent_evaluation_not_saved", extra={"kind": kind, "item_id": item_id})


class EvaluationSetStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sets: dict[str, EvaluationSet] = {}

    def list(self, agent_id: str | None = None) -> list[EvaluationSet]:
        with self._lock:
            items = [
                item.model_copy(deep=True)
                for item in self._sets.values()
                if agent_id is None or item.agent_id == agent_id
            ]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def get(self, set_id: str) -> EvaluationSet:
        with self._lock:
            item = self._sets.get(set_id)
            if item is None:
                raise KeyError(set_id)
            return item.model_copy(deep=True)

    def create(self, data: EvaluationSetInput, *, created_by: str | None) -> EvaluationSet:
        item = EvaluationSet(**data.model_dump(), created_by_user_uuid=created_by)
        _save("evaluation_set", item.id, item.model_dump(mode="json"))
        with self._lock:
            self._sets[item.id] = item
        return item.model_copy(deep=True)

    def update(self, set_id: str, data: EvaluationSetInput) -> EvaluationSet:
        with self._lock:
            current = self._sets.get(set_id)
            if current is None:
                raise KeyError(set_id)
            updated = current.model_copy(
                update={
                    "name": data.name,
                    "description": data.description,
                    "cases": _keep_sources(current.cases, data.cases),
                    "updated_at": _now(),
                }
            )
        _save("evaluation_set", set_id, updated.model_dump(mode="json"))
        with self._lock:
            self._sets[set_id] = updated
        return updated.model_copy(deep=True)

    def append_case(self, set_id: str, case: EvaluationCase) -> EvaluationSet:
        """ケースを 1 件足す（同じ質問は断り、件数の上限を守る。#810）。"""
        with self._lock:
            current = self._sets.get(set_id)
            if current is None:
                raise KeyError(set_id)
            question = normalize_question(case.question)
            if any(normalize_question(item.question) == question for item in current.cases):
                raise EvaluationCaseDuplicateError(set_id)
            if len(current.cases) >= EVALUATION_MAX_CASES:
                raise EvaluationSetFullError(set_id)
            case_id = case.id.strip()
            if not case_id or any(item.id == case_id for item in current.cases):
                case_id = next_case_id(current.cases)
            updated = current.model_copy(
                update={
                    "cases": [*current.cases, case.model_copy(update={"id": case_id})],
                    "updated_at": _now(),
                }
            )
        _save("evaluation_set", set_id, updated.model_dump(mode="json"))
        with self._lock:
            self._sets[set_id] = updated
        return updated.model_copy(deep=True)

    def sets_with_question(self, agent_id: str, question: str) -> tuple[str, ...]:
        """同じ質問のケースを持つ、業務 Agent の評価セットの ID。"""
        normalized = normalize_question(question)
        with self._lock:
            return tuple(
                item.id
                for item in self._sets.values()
                if item.agent_id == agent_id
                and any(normalize_question(case.question) == normalized for case in item.cases)
            )

    def delete(self, set_id: str) -> None:
        with self._lock:
            if set_id not in self._sets:
                raise KeyError(set_id)
        _delete("evaluation_set", set_id)
        with self._lock:
            self._sets.pop(set_id, None)

    def restore(self, item: EvaluationSet) -> None:
        with self._lock:
            self._sets[item.id] = item

    def clear(self) -> None:
        with self._lock:
            self._sets.clear()


def _keep_sources(
    current: list[EvaluationCase], incoming: list[EvaluationCase]
) -> list[EvaluationCase]:
    """保存（PUT）で出どころ（`source_run_id`）が落ちたケースは、同じ ID・同じ質問なら元の値を保つ。

    Excel の取り込みなど、出どころの列を持たない経路で編集しても、フィードバックからの追加の記録を
    失わない（#810）。
    """
    sources = {
        (case.id, normalize_question(case.question)): case.source_run_id
        for case in current
        if case.source_run_id
    }
    return [
        case
        if case.source_run_id
        or (source := sources.get((case.id, normalize_question(case.question)))) is None
        else case.model_copy(update={"source_run_id": source})
        for case in incoming
    ]


def cases_fingerprint(job: EvaluationJob) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """job が評価したケース（質問・期待・期待するツール）。同じなら前回と比べられる（#810）。"""
    return tuple(
        sorted(
            (
                normalize_question(result.case.question),
                result.case.expected.strip(),
                tuple(sorted(result.case.expected_tools)),
            )
            for result in job.results
        )
    )


class EvaluationStore:
    """評価の job（終わった job は `EVALUATION_JOBS_RETENTION_DAYS` 日残す。#794）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, EvaluationJob] = OrderedDict()

    def _persist(self, job: EvaluationJob) -> None:
        _save_quietly("evaluation_job", job.id, job.model_dump(mode="json"))

    def create(self, job: EvaluationJob) -> EvaluationJob:
        with self._lock:
            if any(item.status in ACTIVE_JOB_STATUSES for item in self._jobs.values()):
                raise EvaluationBusyError(job.id)
            self._jobs[job.id] = job
            removed = self._prune_locked(job.created_at)
            copy = job.model_copy(deep=True)
        self._persist(copy)
        for job_id in removed:
            try:
                _delete("evaluation_job", job_id)
            except Exception:  # noqa: BLE001 - 古い job の削除の失敗は無視する
                logger.warning("agent_evaluation_prune_failed", extra={"job_id": job_id})
        return copy

    def _prune_locked(self, now: datetime) -> list[str]:
        """保持期間を過ぎた・件数の上限を超えた、終わった job を古い順に消す（実行中は残す）。"""
        cutoff = now - timedelta(days=EVALUATION_JOBS_RETENTION_DAYS)
        removed: list[str] = []
        for key, item in list(self._jobs.items()):
            if item.status in ACTIVE_JOB_STATUSES:
                continue
            if item.created_at < cutoff or len(self._jobs) > EVALUATION_JOBS_MAX:
                self._jobs.pop(key)
                removed.append(key)
        return removed

    def get(self, job_id: str) -> EvaluationJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job.model_copy(deep=True)

    def list(self, set_id: str | None = None) -> list[EvaluationJob]:
        with self._lock:
            return [
                job.model_copy(deep=True)
                for job in reversed(self._jobs.values())
                if set_id is None or job.set_id == set_id
            ]

    def page(
        self,
        *,
        set_id: str | None = None,
        allowed: Callable[[str], bool] = lambda _agent_id: True,
        offset: int = 0,
        limit: int = EVALUATION_JOBS_PAGE_SIZE,
    ) -> EvaluationJobsData:
        """評価の履歴の 1 ページ（新しい順。`allowed` は業務 Agent の対象範囲）。"""
        with self._lock:
            matched = [
                job
                for job in reversed(self._jobs.values())
                if (set_id is None or job.set_id == set_id) and allowed(job.agent_id)
            ]
            items = [job_item(job) for job in matched[offset : offset + limit]]
        return EvaluationJobsData(jobs=items, total=len(matched), offset=offset, limit=limit)

    def with_previous(self, job: EvaluationJob) -> EvaluationJob:
        """前回（同じ評価セット・同じ評価ケースで、この job より前に完了した job）の概要を入れる。

        評価ケースを変えた後の結果と比べても差に意味が無いため、ケースが同じ job だけを比べる。
        比べた版（`previous_agent_version`）も返す（#810）。
        """
        fingerprint = cases_fingerprint(job)
        with self._lock:
            previous = next(
                (
                    item
                    for item in reversed(self._jobs.values())
                    if job.set_id
                    and item.set_id == job.set_id
                    and item.id != job.id
                    and item.status == JobStatus.COMPLETED
                    and item.created_at < job.created_at
                    and cases_fingerprint(item) == fingerprint
                ),
                None,
            )
            if previous is None:
                return job
            return job.model_copy(
                update={
                    "previous_job_id": previous.id,
                    "previous_summary": previous.summary,
                    "previous_agent_version": previous.agent_version,
                    "previous_created_at": previous.created_at,
                }
            )

    def latest_for_set(self, set_id: str) -> EvaluationJob | None:
        with self._lock:
            return next(
                (
                    item.model_copy(deep=True)
                    for item in reversed(self._jobs.values())
                    if item.set_id == set_id
                ),
                None,
            )

    def has_active_for_set(self, set_id: str) -> bool:
        with self._lock:
            return any(
                item.set_id == set_id and item.status in ACTIVE_JOB_STATUSES
                for item in self._jobs.values()
            )

    def _change(self, job_id: str, mutate: Callable[[EvaluationJob], None]) -> EvaluationJob:
        with self._lock:
            job = self._jobs[job_id]
            mutate(job)
            copy = job.model_copy(deep=True)
        self._persist(copy)
        return copy

    def update(self, job_id: str, **changes: Any) -> EvaluationJob:
        def mutate(job: EvaluationJob) -> None:
            for key, value in changes.items():
                setattr(job, key, value)

        return self._change(job_id, mutate)

    def update_result(self, job_id: str, index: int, **changes: Any) -> None:
        def mutate(job: EvaluationJob) -> None:
            for key, value in changes.items():
                setattr(job.results[index], key, value)

        self._change(job_id, mutate)

    def start(self, job_id: str) -> EvaluationJob | None:
        """待っている job を実行中にする。取り消し済み・無い job は None（実行しない）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != JobStatus.QUEUED:
                return None
        return self.update(job_id, status=JobStatus.RUNNING, started_at=_now())

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return job is None or job.status == JobStatus.CANCELLED

    def cancel(self, job_id: str) -> EvaluationJob:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)

        def mutate(job: EvaluationJob) -> None:
            if job.status in ACTIVE_JOB_STATUSES:
                job.status = JobStatus.CANCELLED
                job.finished_at = _now()

        return self._change(job_id, mutate)

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in ACTIVE_JOB_STATUSES:
                raise EvaluationJobActiveError(job_id)
        _delete("evaluation_job", job_id)
        with self._lock:
            self._jobs.pop(job_id, None)

    def restore(self, job: EvaluationJob) -> None:
        """保存した job を戻す。実行中だった job は再起動で中断したものとして終える。"""
        if job.status in ACTIVE_JOB_STATUSES:
            job.status = JobStatus.FAILED
            job.error = "バックエンドの再起動で評価が中断しました。もう一度始めてください。"
            job.finished_at = job.finished_at or _now()
            for result in job.results:
                if result.status in _UNFINISHED_STATUSES:
                    result.status = CaseStatus.CANCELLED
        with self._lock:
            self._jobs[job.id] = job
            ordered = sorted(self._jobs.values(), key=lambda item: item.created_at)
            self._jobs = OrderedDict((item.id, item) for item in ordered)

    def prune(self, now: datetime | None = None) -> tuple[str, ...]:
        """保持期間を過ぎた job を消す（起動時の復元の後。保存先からも消す）。"""
        with self._lock:
            removed = self._prune_locked(now or _now())
        for job_id in removed:
            try:
                _delete("evaluation_job", job_id)
            except Exception:  # noqa: BLE001 - 古い job の削除の失敗は無視する
                logger.warning("agent_evaluation_prune_failed", extra={"job_id": job_id})
        return tuple(removed)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


evaluation_set_store = EvaluationSetStore()
evaluation_store = EvaluationStore()


def set_item(item: EvaluationSet) -> EvaluationSetItem:
    latest = evaluation_store.latest_for_set(item.id)
    return EvaluationSetItem(
        id=item.id,
        agent_id=item.agent_id,
        name=item.name,
        description=item.description,
        case_count=len(item.cases),
        updated_at=item.updated_at,
        last_job_id=latest.id if latest else None,
        last_job_status=latest.status.value if latest else None,
        last_pass_rate=latest.summary.pass_rate if latest else None,
    )


def job_item(job: EvaluationJob) -> EvaluationJobItem:
    return EvaluationJobItem(
        id=job.id,
        agent_id=job.agent_id,
        agent_name=job.agent_name,
        set_id=job.set_id,
        set_name=job.set_name,
        agent_version=job.agent_version,
        status=job.status,
        summary=job.summary,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


# ---- 判定 -------------------------------------------------------------------------------

JUDGE_INSTRUCTIONS = """あなたは業務 Agent の回答を採点する評価者です。
質問・期待する回答の要点・実際の回答を読み、実際の回答が期待する要点を満たしているかを判定します。

- verdict: 要点をすべて満たし、誤りが無ければ correct。要点の欠落・誤り・質問と関係のない回答なら
  incorrect。期待する要点があいまいで判断できないときだけ uncertain。
- score: 期待する要点のうち、実際の回答が正しく満たしている割合（0 から 1）。
- summary: 判定の理由を日本語で 1〜2 文。
- missing_points: 欠けている・誤っている要点を日本語で（無ければ空の配列）。
表現の違い・言い換え・補足の有無は減点しません。数値・固有名詞・結論の違いは誤りとして扱います。
評価のため承認が要る操作は実行していません。「実行していない」こと自体は減点しません。"""


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


def tool_matches(called: str, expected: str) -> bool:
    """呼んだツールが期待するツールか（MCP 接続の名前を省いた指定も受け付ける）。"""
    return called == expected or called.endswith(f"{_MCP_SEPARATOR}{expected}")


def tool_selection_correct(case: EvaluationCase, tool_calls: list[str]) -> bool | None:
    if not case.expected_tools:
        return None
    return all(
        any(tool_matches(called, item) for called in tool_calls) for item in case.expected_tools
    )


def _tool_calls(run: RunState) -> list[str]:
    names = [step.tool_call.name for step in run.steps if step.tool_call is not None]
    return list(dict.fromkeys(names))


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
        if item.status in _UNFINISHED_STATUSES:
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

    # 評価する版（#810）。下書きは "draft"、公開中の版は始めたときの版に固定する（評価の途中で
    # 公開し直しても、ケースごとに版が変わらない）。#810 より前の job（None）は公開中の版。
    draft = job.agent_version == "draft"
    pinned = job.agent_version if isinstance(job.agent_version, int) else None
    run = repository.create_builtin_run(
        RunCreateRequest(
            goal=case.question,
            agent_id=job.agent_id,
            draft=draft,
            metadata={
                "evaluation_job_id": job.id,
                "evaluation_case_id": case.id,
                EVALUATION_DRY_RUN_KEY: True,
            },
        ),
        created_by_user_uuid=job.created_by_user_uuid,
        agent_version=pinned,
    )
    store.update_result(job.id, index, run_id=run.id)
    try:
        settled = await _settle_run(run.id)
    except TimeoutError:
        repository.cancel_run(run.id)
        finish(CaseStatus.TIMED_OUT, error="上限時間（300 秒）までに回答が出ませんでした。")
        return
    tool_calls = _tool_calls(settled)
    tools = {
        "tool_calls": tool_calls,
        "tool_selection_correct": tool_selection_correct(case, tool_calls),
    }
    if store.is_cancelled(job.id) and settled.status != RunStatus.COMPLETED:
        repository.cancel_run(run.id)
        finish(CaseStatus.CANCELLED, **tools)
        return
    if settled.status == RunStatus.WAITING_APPROVAL:
        repository.cancel_run(run.id)
        finish(
            CaseStatus.NEEDS_APPROVAL,
            error="承認が必要なツールで止まったため評価できません（Run は取り消しました）。",
            **tools,
        )
        return
    if settled.status != RunStatus.COMPLETED:
        finish(CaseStatus.RUN_FAILED, error=_failure_message(settled), **tools)
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
        finish(CaseStatus.JUDGE_FAILED, answer=answer, error=message, **tools)
        return
    finish(CaseStatus.JUDGED, answer=answer, judgement=judgement, **tools)
