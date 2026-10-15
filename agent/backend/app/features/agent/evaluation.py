"""業務 Agent の品質評価（#776）。

評価セット（業務 Agent ごとに保存する評価ケースの集まり）を業務 Agent の Run として
上限つきの並行（#2004）で実行し、回答を OCI Enterprise AI の既定のテキストモデルで判定する
（LLM-as-judge）。業界の評価の基盤（評価セットの保存・表での編集と取り込み・ツールの
選択の評価・前回との比較）と、RAG の品質評価・NL2SQL の SQL生成評価の形（バックグラウンドの
job・進み具合・取り消し・概要・ケース別結果）にそろえる。

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
- 評価セットは、評価セットの業務 Agent 以外の業務 Agent でも評価でき、同じ質問のケースどうしで
  2 つの job を並べて比べられる（Agent をまたいだ比較。前回との比較は同じ業務 Agent の job
  だけ。#1845）。


- 評価ケースは問い返し（回答前の確認。NL2SQL の SQL の生成前の確認・RAG の条件の確認）への
  答えを持てる。
  Run が問い返しで止まったら、答えを同じ会話（`thread_id`）の次の Run として送り、最後の Run の
  回答を判定する（往復は `EVALUATION_CLARIFICATION_ROUNDS_MAX` 回まで。#1845）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from agents import Agent, Runner
from pr_backend_core import Page
from pr_backend_core.api import DEFAULT_PAGE_LIMIT, paginate
from pr_backend_core.bounded_calls import (
    Attempt,
    BoundedCallAborted,
    BoundedCaller,
    BoundedCallFailed,
    ErrorKind,
)
from pr_system_settings.model import (
    enterprise_ai_retry_output_token_budget,
    lower_reasoning_effort,
)
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.features.agent import builtin_runtime
from app.features.agent.ai_calls import evaluation_concurrency, make_ai_caller
from app.features.agent.runtime import (
    EVALUATION_DRY_RUN_KEY,
    Artifact,
    RunCreateRequest,
    RunEventType,
    RunState,
    RunStatus,
    run_support_task,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

EVALUATION_MAX_CASES = 50
EVALUATION_QUESTION_MAX_CHARS = 4000
EVALUATION_EXPECTED_MAX_CHARS = 8000
EVALUATION_EXPECTED_TOOLS_MAX = 10
# 評価ケースが持てる問い返しへの答えの数と、1 ケースで答える往復の上限（#1845。共通の
# 「回答前の確認」の往復の上限の既定 2 回にそろえる。
# platform/docs/ux-contracts/confirm-before-answer.md）。
EVALUATION_CLARIFICATION_ANSWERS_MAX = 10
EVALUATION_CLARIFICATION_ROUNDS_MAX = 2
EVALUATION_CLARIFICATION_TEXT_MAX_CHARS = 2000
# 終わった job を残す期間（日）。利用状況・フィードバックの最長の期間（365 日）と同じ（#794）。
# 以前は件数（50 件）で消していたため、前回との比較・評価の履歴が 50 件より前を失っていた。
EVALUATION_JOBS_RETENTION_DAYS = 365
# メモリと保存先を守るための件数の上限（期間内でもこれを超えたら古い終わった job から消す）。
EVALUATION_JOBS_MAX = 2000
# 評価の履歴の一覧の 1 ページの既定と上限。
EVALUATION_JOBS_PAGE_SIZE = DEFAULT_PAGE_LIMIT
EVALUATION_JOBS_PAGE_SIZE_MAX = 100
# 1 ケースの上限時間（Run の実行。判定は別）の範囲（秒。#1432）。上限は
# `AGENT_EVALUATION_CASE_TIMEOUT_SECONDS`（0 以下は自動。`case_timeout_seconds`）。
CASE_TIMEOUT_MIN_SECONDS = 300.0
CASE_TIMEOUT_MAX_SECONDS = 86_400.0
# dispatcher のモードで Run の決着を待つ間隔。テストは短くする。
settle_poll_seconds = 1.0
# MCP 接続のツールの名前の区切り（`<接続>__<ツール>`。#757）。
_MCP_SEPARATOR = "__"


def _now() -> datetime:
    return datetime.now(UTC)


def _rag_timeout_seconds() -> float:
    """接続 `rag` の 1 回の呼び出しの待ち時間（画面で変えた値を優先する）。"""
    from app.features.agent.config import runtime_config_store

    try:
        return float(runtime_config_store.get_mcp("rag").timeout_seconds)
    except KeyError:
        return float(get_settings().agent_external_rag_timeout_seconds)


def case_timeout_seconds() -> float:
    """品質評価の 1 ケースの上限時間（秒。#1432）。

    `AGENT_EVALUATION_CASE_TIMEOUT_SECONDS` が正ならその値（86400 秒まで）。0 以下は自動で、
    接続 `rag` の待ち時間 ×（Run の RAG の予算 + 1）にする。RAG を使う業務 Agent の Run は RAG を
    予算の回数まで呼び、1 回は RAG の回答生成を通しで待つ（#1425）。+1 は回答の最終の検証
    （`rag_validate_answer`）とモデルの推論の分。RAG の予算が無制限（0 以下）なら、Run のツールの
    呼び出しの上限を回数に使う。自動の値は 300 秒〜86400 秒に収める。
    """
    settings = get_settings()
    configured = float(settings.agent_evaluation_case_timeout_seconds)
    if configured > 0:
        return min(configured, CASE_TIMEOUT_MAX_SECONDS)
    rag_calls = int(settings.agent_max_rag_calls_per_run)
    if rag_calls <= 0:
        rag_calls = max(int(settings.agent_max_tool_calls_per_run), 1)
    automatic = _rag_timeout_seconds() * (rag_calls + 1)
    return min(max(automatic, CASE_TIMEOUT_MIN_SECONDS), CASE_TIMEOUT_MAX_SECONDS)


# ---- 評価セット -------------------------------------------------------------------------


class EvaluationClarificationAnswer(BaseModel):
    """問い返し（回答前の確認）の確認事項 1 件への答え（#1845）。

    共通の `ClarificationCard` の答え（`{ itemId, optionIds, otherText }`）と同じ形を snake_case で
    持つ。NL2SQL の SQL の生成前の確認なら `item_id` = `requirement.question.item_id`・
    `option_ids` = `selected_candidate_ids`・`other_text` = `free_text`、RAG の条件の確認なら
    `item_id` = 条件の id・`option_ids` = 選択肢（RAG の `conditions` の値）に当たる。
    """

    # 答える確認事項の ID（NL2SQL の item_id / RAG の条件の id）。空なら、まだ答えていない確認事項へ
    # 順に当てる（NL2SQL の item_id は質問ごとに作られるため、前もって分からないことがある）。
    item_id: str = Field(default="", max_length=128)
    # 選ぶ候補（候補の id か表示名。NL2SQL の候補の id は質問ごとに作られるため、表示名でもよい）。
    option_ids: list[str] = Field(default_factory=list, max_length=20)
    # 候補に無い答え（「その他（自由入力）」）。
    other_text: str = Field(default="", max_length=EVALUATION_CLARIFICATION_TEXT_MAX_CHARS)

    @field_validator("item_id", "other_text")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("option_ids")
    @classmethod
    def _options(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def _require_answer(self) -> EvaluationClarificationAnswer:
        if not self.option_ids and not self.other_text:
            raise ValueError("問い返しへの答えは、候補か自由入力のどちらかを入れてください。")
        return self


class EvaluationCase(BaseModel):
    """評価ケース。`id` を省くと `case-<番号>` にする。"""

    id: str = Field(default="", max_length=100)
    question: str = Field(min_length=1, max_length=EVALUATION_QUESTION_MAX_CHARS)
    expected: str = Field(min_length=1, max_length=EVALUATION_EXPECTED_MAX_CHARS)
    # 呼ぶべきツール（任意）。`rag_search` のように MCP 接続の名前を省いてもよい。
    expected_tools: list[str] = Field(default_factory=list)
    # 期待するツールをこの順に呼ぶ（#1845。各ツールの最初の呼び出しの順で比べ、間にほかのツールを
    # 挟んでもよい）。先頭のツールが「最初に呼ぶツール」になる。期待するツールが無ければ使わない。
    expected_tools_in_order: bool = False
    # 問い返し（回答前の確認）への答え（任意。#1845）。Run が問い返しで止まったら、答えを同じ会話の
    # 次の Run として送り、最後の Run の回答を判定する。
    clarification_answers: list[EvaluationClarificationAnswer] = Field(
        default_factory=list, max_length=EVALUATION_CLARIFICATION_ANSWERS_MAX
    )
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
    """id を省いたケースに使われていない `case-<番号>` を付け、明示した id の重複を断る。

    番号は位置の番号から探す。ケースを削除して足したとき（`case-2`, `case-3`, 省略）に、
    位置の番号（`case-3`）が残ったケースの id と重なっても保存できるようにする（#965）。
    """
    ids = [case.id.strip() for case in cases]
    explicit = [case_id for case_id in ids if case_id]
    if len(set(explicit)) != len(explicit):
        raise ValueError("ケースの id が重複しています。")
    used = set(explicit)
    numbered: list[EvaluationCase] = []
    for index, (case, case_id) in enumerate(zip(cases, ids, strict=True), start=1):
        if not case_id:
            number = index
            while f"case-{number}" in used:
                number += 1
            case_id = f"case-{number}"
            used.add(case_id)
        numbered.append(case.model_copy(update={"id": case_id}))
    return numbered


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
    # 判定できなかった（判定のモデルの失敗。出力の上限で止まったら 1 回再試行した後。#1845）。
    # 業務 Agent の回答の誤りではないので、合格率の分母から除く。
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


class EvaluationClarificationQuestion(BaseModel):
    """Run が出した問い返しの確認事項（評価の結果に残す分）。"""

    item_id: str = ""
    # nl2sql / rag（`support_task.clarification_kind`）。
    kind: str = ""
    label: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    # 当てた答え（往復の記録だけ。#1861）。選んだ候補の表示名と自由入力で、画面は共通の
    # `ClarificationRecord` の確認事項ごとの答えに出す。答えを当てなかった確認事項は空。
    selected: list[str] = Field(default_factory=list)
    other_text: str = ""


class EvaluationClarificationRound(BaseModel):
    """問い返しへの 1 往復（#1845）。

    `run_id` の Run が問い返しで止まり、`message` を次の Run で送った。
    """

    run_id: str
    questions: list[EvaluationClarificationQuestion] = Field(default_factory=list)
    # 当てた答え（確認事項の順。答えの無い確認事項は含めない）。
    answers: list[EvaluationClarificationAnswer] = Field(default_factory=list)
    # 次の Run の質問として送った文（利用者がチャットで答えるのと同じ）。
    message: str = ""


class EvaluationCaseResult(BaseModel):
    case: EvaluationCase
    status: CaseStatus = CaseStatus.PENDING
    run_id: str | None = None
    answer: str = ""
    judgement: EvaluationJudgement | None = None
    # 業務 Agent が呼んだ（呼ぼうとした）ツール。dry-run で実行しなかったものを含む。
    tool_calls: list[str] = Field(default_factory=list)
    # 期待するツールをすべて呼んだか（順を指定したケースは順も合うか。
    # 期待するツールが無いケースは None）。
    tool_selection_correct: bool | None = None
    # 期待するツールを順どおりに呼んだか（順を指定しないケースは None。#1845）。
    tool_order_correct: bool | None = None
    # 判定のモデルを呼んだ回数（出力の上限で止まったら広げて 1 回再試行する。#1845）。
    judge_attempts: int = 0
    # 問い返しへの往復（#1845。`run_id` は最後の Run）。
    clarification_rounds: list[EvaluationClarificationRound] = Field(default_factory=list)
    # 最後の Run も問い返しで終わった（答えが評価ケースに無い・往復の上限）。回答として判定はする
    # （あいまいな質問では問い返すのが期待する動きのこともある）。
    ended_with_clarification: bool = False
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
    # 評価できなかったケースのうち、判定のモデルが判定できなかったケース（#1845）。
    unjudged: int = 0
    # 合格率 = 正しいと判定したケース / 終わったケース（業務 Agent の実行の失敗・時間切れは不合格に
    # 数え、判定のモデルが判定できなかったケースは分母から除く。#1845）。
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
        if result.status == CaseStatus.JUDGE_FAILED:
            summary.unjudged += 1
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
    finished = summary.completed - cancelled - summary.unjudged
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
    # 評価する業務 Agent（省略すると評価セットの業務 Agent。同じ評価ケースで別の業務 Agent を
    # 評価し、job を並べて比べる。#1845）。
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)
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


class EvaluationJobsData(Page[EvaluationJobItem]):
    """評価の履歴の 1 ページ（共通の `Page`。`total` は絞り込みに合う job の件数。#1266）。"""


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

    def replace_all(self, items: Sequence[EvaluationSet]) -> None:
        """保存先の評価セットで置き換える（migration の後の読み直し。#1844）。"""
        with self._lock:
            self._sets = {item.id: item for item in items}

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


def cases_fingerprint(job: EvaluationJob) -> tuple[tuple[Any, ...], ...]:
    """job が評価したケース（質問・期待・期待するツール）。同じなら前回と比べられる（#810）。

    ツールの順を指定したケースは、指定した順のまま比べる（順の指定を変えたら別のケースにする。#1845）。
    """
    return tuple(
        sorted(
            (
                normalize_question(result.case.question),
                result.case.expected.strip(),
                (
                    ("in_order", *result.case.expected_tools)
                    if result.case.expected_tools_in_order and result.case.expected_tools
                    else tuple(sorted(result.case.expected_tools))
                ),
                # 問い返しへの答えを変えたら別のケースにする（#1845）。
                tuple(
                    (item.item_id, tuple(item.option_ids), item.other_text)
                    for item in result.case.clarification_answers
                ),
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
        return EvaluationJobsData(
            **dict(paginate(items, total=len(matched), limit=limit, offset=offset))
        )

    def with_previous(self, job: EvaluationJob) -> EvaluationJob:
        """前回（同じ評価セット・同じ評価ケースで、この job より前に完了した job）の概要を入れる。

        評価ケースを変えた後の結果と比べても差に意味が無いため、ケースが同じ job だけを比べる。
        比べた版（`previous_agent_version`）も返す（#810）。ほかの業務 Agent で評価した job は前回に
        しない（Agent をまたいだ比較は `compare_jobs`。#1845）。
        """
        fingerprint = cases_fingerprint(job)
        with self._lock:
            previous = next(
                (
                    item
                    for item in reversed(self._jobs.values())
                    if job.set_id
                    and item.set_id == job.set_id
                    and item.agent_id == job.agent_id
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
        _finish_interrupted(job)
        with self._lock:
            self._jobs[job.id] = job
            ordered = sorted(self._jobs.values(), key=lambda item: item.created_at)
            self._jobs = OrderedDict((item.id, item) for item in ordered)

    def replace_all(self, jobs: Sequence[EvaluationJob]) -> None:
        """保存先の job で置き換える（migration の後の読み直し。#1844）。

        このプロセスで実行中の job はメモリの値を残す（実行が続きを書く。保存先の途中の値で
        戻さない）。それ以外の、保存先で実行中のままの job は起動時の復元と同じく中断として終える。
        """
        with self._lock:
            active = {
                job_id: job
                for job_id, job in self._jobs.items()
                if job.status in ACTIVE_JOB_STATUSES
            }
        replaced: dict[str, EvaluationJob] = {}
        for job in jobs:
            if job.id in active:
                continue
            _finish_interrupted(job)
            replaced[job.id] = job
        with self._lock:
            # 読み直しの間に終わった job も、メモリの値（実行の結果）を残す。
            for job_id in active:
                if job_id in self._jobs:
                    replaced[job_id] = self._jobs[job_id]
            ordered = sorted(replaced.values(), key=lambda item: item.created_at)
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


def _finish_interrupted(job: EvaluationJob) -> None:
    """保存先で実行中のままの job（このプロセスで動いていない）を、中断したものとして終える。"""
    if job.status not in ACTIVE_JOB_STATUSES:
        return
    job.status = JobStatus.FAILED
    job.error = "バックエンドの再起動で評価が中断しました。もう一度始めてください。"
    job.finished_at = job.finished_at or _now()
    for result in job.results:
        if result.status in _UNFINISHED_STATUSES:
            result.status = CaseStatus.CANCELLED


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


# ---- job の比較（#1845） ------------------------------------------------------------------

ComparisonChange = Literal["improved", "regressed", "unchanged", "base_only", "target_only"]


class ComparisonOutcome(BaseModel):
    """比べる job の 1 ケースの結果（判定か、判定まで進まなかった状態）。"""

    case_id: str
    # correct / incorrect / uncertain（判定した）か、ケースの状態（run_failed など）。
    status: str
    passed: bool
    score: float | None = None
    tool_calls: list[str] = Field(default_factory=list)
    tool_selection_correct: bool | None = None
    run_id: str | None = None


class ComparisonRow(BaseModel):
    """同じ質問のケースの 2 つの結果と、その変化。"""

    question: str
    base: ComparisonOutcome | None = None
    target: ComparisonOutcome | None = None
    change: ComparisonChange


class EvaluationComparison(BaseModel):
    """2 つの評価の job をケースごとに並べた比較（業務 Agent・版・評価セットをまたいでよい）。"""

    base: EvaluationJobItem
    target: EvaluationJobItem
    rows: list[ComparisonRow] = Field(default_factory=list)
    improved: int = 0
    regressed: int = 0
    unchanged: int = 0
    base_only: int = 0
    target_only: int = 0


def _outcome(result: EvaluationCaseResult) -> ComparisonOutcome:
    judged = result.status == CaseStatus.JUDGED and result.judgement is not None
    verdict = result.judgement.verdict if judged and result.judgement else None
    return ComparisonOutcome(
        case_id=result.case.id,
        status=verdict.value if verdict else result.status.value,
        passed=verdict == JudgeVerdict.CORRECT,
        score=result.judgement.score if judged and result.judgement else None,
        tool_calls=list(result.tool_calls),
        tool_selection_correct=result.tool_selection_correct,
        run_id=result.run_id,
    )


def _keyed_results(job: EvaluationJob) -> dict[tuple[str, int], EvaluationCaseResult]:
    """質問（空白をそろえる）と、同じ質問の何件目かで引けるケースの結果。"""
    keyed: dict[tuple[str, int], EvaluationCaseResult] = {}
    seen: dict[str, int] = {}
    for result in job.results:
        question = normalize_question(result.case.question)
        number = seen.get(question, 0)
        seen[question] = number + 1
        keyed[(question, number)] = result
    return keyed


def compare_jobs(base: EvaluationJob, target: EvaluationJob) -> EvaluationComparison:
    """2 つの job を、同じ質問のケースどうしで並べる（#1845）。

    ケースは質問で対応させる（評価セットが違っても、同じ質問なら比べる）。変化は合否（「正しい」と
    判定したか）で決め、合否が同じなら変化なし（スコア・ツールの差は行で見る）。片方にしか無い
    ケースは `base_only` / `target_only`。並びは基準の job の順、その後に比べる job だけのケース。
    """
    base_results = _keyed_results(base)
    target_results = _keyed_results(target)
    keys = [*base_results, *(key for key in target_results if key not in base_results)]
    comparison = EvaluationComparison(base=job_item(base), target=job_item(target))
    for key in keys:
        before = base_results.get(key)
        after = target_results.get(key)
        left = _outcome(before) if before else None
        right = _outcome(after) if after else None
        change: ComparisonChange
        if left is None:
            change = "target_only"
        elif right is None:
            change = "base_only"
        elif right.passed and not left.passed:
            change = "improved"
        elif left.passed and not right.passed:
            change = "regressed"
        else:
            change = "unchanged"
        setattr(comparison, change, getattr(comparison, change) + 1)
        source = before or after
        question = source.case.question if source else ""
        comparison.rows.append(
            ComparisonRow(question=question, base=left, target=right, change=change)
        )
    return comparison


# ---- 判定 -------------------------------------------------------------------------------

JUDGE_INSTRUCTIONS = """あなたは業務 Agent の回答を採点する評価者です。
質問・期待する回答の要点・実際の回答を読み、実際の回答が期待する要点を満たしているかを判定します。

- verdict: 要点をすべて満たし、誤りが無ければ correct。要点の欠落・誤り・質問と関係のない回答なら
  incorrect。期待する要点があいまいで判断できないときだけ uncertain。
- score: 期待する要点のうち、実際の回答が正しく満たしている割合（0 から 1）。
- summary: 判定の理由を日本語で 1〜2 文。
- missing_points: 欠けている・誤っている要点を日本語で（無ければ空の配列）。
表現の違い・言い換え・補足の有無は減点しません。数値・固有名詞・結論の違いは誤りとして扱います。
評価のため承認が要る操作は実行していません。「実行していない」こと自体は減点しません。
業務 Agent が問い返し（回答前の確認）をして利用者が答えたときは、その答えを踏まえた最後の回答を
判定します。"""


def _judge_input(case: EvaluationCase, answer: str, clarifications: Sequence[str] = ()) -> str:
    replies = "".join(
        f"## 業務 Agent の問い返しへの利用者の答え（{number} 回目）\n{message}\n\n"
        for number, message in enumerate(clarifications, start=1)
    )
    return (
        f"## 質問\n{case.question}\n\n"
        f"## 期待する回答の要点\n{case.expected}\n\n"
        f"{replies}"
        f"## 実際の回答\n{answer}"
    )


class JudgeIncompleteError(RuntimeError):
    """判定のモデルの応答が、再試行しても出力の上限で止まった（#1845。利用者向けの日本語）。"""

    def __init__(self, message: str, *, attempts: int) -> None:
        super().__init__(message)
        self.message = message
        self.attempts = attempts


class JudgeCallError(RuntimeError):
    """判定のモデルの呼び出しの失敗（呼んだ回数を持つ。元の例外は `__cause__`）。"""

    def __init__(self, attempts: int) -> None:
        super().__init__("judge failed")
        self.attempts = attempts


def _output_incomplete(exc: BaseException) -> bool:
    return (
        isinstance(exc, builtin_runtime.BuiltinRuntimeError)
        and exc.code == builtin_runtime.MODEL_OUTPUT_INCOMPLETE_CODE
    )


def _retry_target(target: builtin_runtime.ModelTarget) -> builtin_runtime.ModelTarget:
    """出力の上限で止まった判定を送り直すときのモデル（#1845・#2023）。

    広げられれば出力の上限を広げ、広げられなければ推論の量を 1 段下げる。

    既定の上限はもうモデルの出力の上限のことが多く、そのときは同じ上限で送り直す（実環境では、
    出力が上限よりずっと少ないのに `max_output_tokens` で止まる応答があり、送り直すと判定できた。
    #1816 の評価の記録）。
    """
    widened = enterprise_ai_retry_output_token_budget(
        get_settings(), target.model_id, current=target.max_output_tokens
    )
    if widened:
        return replace(target, max_output_tokens=widened)
    # 広げられなければ推論の量を 1 段下げる（#2023。high → medium → low。low・none は下げない）。
    lower = lower_reasoning_effort(target.reasoning_effort)
    return replace(target, reasoning_effort=lower) if lower else target


async def _judge_once(
    case: EvaluationCase,
    answer: str,
    target: builtin_runtime.ModelTarget,
    clarifications: Sequence[str] = (),
) -> EvaluationJudgement:
    judge = Agent(
        name="evaluator",
        instructions=JUDGE_INSTRUCTIONS,
        model=builtin_runtime.model_factory(target),
        model_settings=builtin_runtime.model_settings_for(target),
        output_type=EvaluationJudgement,
    )
    result = await Runner.run(judge, _judge_input(case, answer, clarifications), max_turns=1)
    output = result.final_output
    if isinstance(output, EvaluationJudgement):
        return output
    return EvaluationJudgement.model_validate(output)


# 判定のモデルを呼ぶ回数の上限（最初の 1 回 + 出力の上限で止まったときの再試行 1 回。#1845）。
# 再試行は共通の実行器の「出力の打ち切り」の規則（1 回。入力・上限を変えられる呼び出しだけ）が行う。
JUDGE_MAX_ATTEMPTS = 2


async def judge_answer(
    case: EvaluationCase,
    answer: str,
    clarifications: Sequence[str] = (),
    *,
    caller: BoundedCaller | None = None,
) -> tuple[EvaluationJudgement, int]:
    """回答を判定する（OCI Enterprise AI の既定のテキストモデル。structured output）。

    判定と、判定のモデルを呼んだ回数を返す。呼び出しは上限つきの並行の実行器（#2004。job の中で
    共有する ``caller``）を通し、429・5xx・接続・時間切れは実行器が種類ごとに再試行する（429 は
    全体で待って並行の数を下げる。SDK の再試行は切る）。応答が出力の上限で止まったら（推論の
    あるモデルは推論の token も数える）、上限を広げて 1 回だけ送り直す。それでも止まれば
    `JudgeIncompleteError`（「判定できなかった」。回答の誤りとは区別する）。ほかの失敗は
    `JudgeCallError`（`__cause__` が元の例外）。権限・設定の誤り・割り当ての使い切りは
    ``BoundedCallAborted``（評価の job を止める）。
    """
    caller = caller or make_ai_caller("evaluation_judge", max_concurrency=1)
    attempts = 0

    async def once(attempt: Attempt) -> EvaluationJudgement:
        nonlocal attempts
        attempts += 1
        target = replace(builtin_runtime.resolve_model_target(""), sdk_max_retries=0)
        for _ in range(min(attempt.adaptation, JUDGE_MAX_ATTEMPTS - 1)):
            target = _retry_target(target)
        if attempt.adaptation:
            logger.warning(
                "agent_evaluation_judge_retry",
                extra={
                    "warning_code": "AGENT_EVALUATION_JUDGE_RETRY",
                    "model": target.model_id,
                    "retry_max_output_tokens": target.max_output_tokens,
                },
            )
        return await _judge_once(case, answer, target, clarifications)

    try:
        try:
            judgement = await caller.call(once, label=case.id, adaptive=True)
        except BoundedCallFailed as exc:
            if not exc.final_retry:
                raise
            # 一時的な失敗の再試行を使い切った。間をおいて最後にもう一度だけ呼ぶ（#2004）。
            judgement = await caller.call(once, label=case.id, adaptive=True, final=True)
    except BoundedCallFailed as exc:
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        if exc.kind is ErrorKind.TRUNCATED:
            message = (
                cause.message if isinstance(cause, builtin_runtime.BuiltinRuntimeError) else ""
            )
            raise JudgeIncompleteError(
                f"判定のモデルの応答が、{attempts} 回送っても出力の上限で止まりました。"
                f"回答の誤りではなく、判定できなかったケースです。{message}",
                attempts=attempts,
            ) from cause
        raise JudgeCallError(attempts) from cause
    return judgement, attempts


def tool_matches(called: str, expected: str) -> bool:
    """呼んだツールが期待するツールか（MCP 接続の名前を省いた指定も受け付ける）。"""
    return called == expected or called.endswith(f"{_MCP_SEPARATOR}{expected}")


def _first_call_index(tool_calls: list[str], expected: str) -> int | None:
    return next(
        (index for index, called in enumerate(tool_calls) if tool_matches(called, expected)), None
    )


def tool_order_correct(case: EvaluationCase, tool_calls: list[str]) -> bool | None:
    """期待するツールを順どおりに呼んだか（#1845。順を指定しないケースは None）。

    各ツールの最初の呼び出しの位置で比べ、間にほかのツールを挟んでもよい。呼ばなかったツールが
    あれば順も合わないとする。先頭のツールは、ほかの期待するツールより先に呼ぶ必要がある。
    """
    if not case.expected_tools or not case.expected_tools_in_order:
        return None
    positions: list[int] = []
    for item in case.expected_tools:
        position = _first_call_index(tool_calls, item)
        if position is None:
            return False
        positions.append(position)
    return positions == sorted(positions)


def tool_selection_correct(case: EvaluationCase, tool_calls: list[str]) -> bool | None:
    """期待するツールをすべて呼んだか（順を指定したケースは順も合うか）。"""
    if not case.expected_tools:
        return None
    called_all = all(
        any(tool_matches(called, item) for called in tool_calls) for item in case.expected_tools
    )
    return called_all and tool_order_correct(case, tool_calls) is not False


def _tool_calls(*runs: RunState) -> list[str]:
    """呼んだ（呼ぼうとした）ツール（問い返しの往復の Run を通して、最初に呼んだ順）。"""
    names = [
        step.tool_call.name for run in runs for step in run.steps if step.tool_call is not None
    ]
    return list(dict.fromkeys(names))


def _pending_items(run: RunState) -> list[dict[str, Any]]:
    state = run_support_task(run)
    items = state.get("pending_clarifications") if isinstance(state, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _candidates(item: dict[str, Any]) -> list[tuple[str, str]]:
    """確認事項の候補（id, 表示名）。NL2SQL は候補の id、RAG は選択肢の文を id にする。"""
    candidates = [
        (str(candidate.get("id") or ""), str(candidate.get("label") or ""))
        for candidate in item.get("candidates") or []
        if isinstance(candidate, dict)
    ]
    return candidates or [(str(option), str(option)) for option in item.get("options") or []]


def pending_clarifications(run: RunState) -> list[EvaluationClarificationQuestion]:
    """Run が問い返しで止まったときの確認事項（支援タスクの状態の `pending_clarifications`）。"""
    from app.features.agent.clarification_cancel import clarification_kind

    return [
        EvaluationClarificationQuestion(
            item_id=str(item.get("condition_id") or ""),
            kind=clarification_kind(item),
            label=str(item.get("label") or ""),
            question=str(item.get("question") or ""),
            options=[label or option_id for option_id, label in _candidates(item)][:20],
        )
        for item in _pending_items(run)
    ]


def _option_labels(
    answer: EvaluationClarificationAnswer, candidates: list[tuple[str, str]]
) -> list[str]:
    """答えの候補を、Run が出した候補の表示名にする（id か表示名で当てる）。"""
    labels: list[str] = []
    for option in answer.option_ids:
        normalized = normalize_question(option)
        labels.append(
            next(
                (
                    label or candidate_id
                    for candidate_id, label in candidates
                    if option == candidate_id or normalized == normalize_question(label)
                ),
                option,
            )
        )
    return labels


def _answer_index(case: EvaluationCase, item_id: str, used: set[int]) -> int | None:
    """確認事項に当てる答えの位置（同じ `item_id`、無ければ `item_id` が空の答えを順に）。"""
    answers = list(enumerate(case.clarification_answers))
    exact = next(
        (
            position
            for position, answer in answers
            if position not in used and answer.item_id and answer.item_id == item_id
        ),
        None,
    )
    if exact is not None:
        return exact
    return next(
        (position for position, answer in answers if position not in used and not answer.item_id),
        None,
    )


def answer_clarifications(
    case: EvaluationCase, run: RunState, used: set[int]
) -> EvaluationClarificationRound | None:
    """Run の問い返しに、評価ケースの答えを当てる（#1845。当たる答えが無ければ None）。

    `used` は使った答えの位置で、往復を通して 1 つの答えを 1 回だけ使う。送る文は利用者がチャットで
    答えるのと同じ自然文にし、組み込み Runtime の確認の扱い（支援タスクの状態の
    「確かめ中の問い」と、NL2SQL の `requirement_session_id`・`answer`、RAG の `conditions`）に
    任せる。
    """
    items = _pending_items(run)
    if not items:
        return None
    questions = pending_clarifications(run)
    answers: list[EvaluationClarificationAnswer] = []
    lines: list[str] = []
    for item, question in zip(items, questions, strict=True):
        position = _answer_index(case, str(item.get("condition_id") or ""), used)
        if position is None:
            continue
        used.add(position)
        answer = case.clarification_answers[position]
        answers.append(answer)
        selected = _option_labels(answer, _candidates(item))
        question.selected = selected
        question.other_text = answer.other_text
        values = [*selected, answer.other_text] if answer.other_text else selected
        topic = str(item.get("label") or "") or str(item.get("question") or "")
        lines.append(f"{topic}: {'、'.join(values)}")
    if not answers:
        return None
    return EvaluationClarificationRound(
        run_id=run.id,
        questions=questions,
        answers=answers,
        message="\n".join(lines)[:EVALUATION_QUESTION_MAX_CHARS],
    )


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
    # 評価の Run は回答前の確認を使わない（#1839）が、待ちで止まったら決着にする。
    RunStatus.WAITING_CONFIRMATION,
}


async def _settle_run(run_id: str, timeout_seconds: float) -> RunState:
    """Run を決着（完了・失敗・取消・承認待ち）させて返す。上限時間を過ぎたら TimeoutError。"""
    from app.features.agent.runtime import runtime_repository

    in_process = get_settings().agent_runtime_dispatch_mode.strip().lower() == "in_process"
    deadline = time.monotonic() + timeout_seconds
    if in_process:
        await asyncio.wait_for(builtin_runtime.execute_run(run_id), timeout_seconds)
    while True:
        run = runtime_repository.get_run(run_id)
        if run.status in _SETTLED:
            return run
        if time.monotonic() >= deadline:
            raise TimeoutError(run_id)
        await asyncio.sleep(settle_poll_seconds)


async def run_evaluation_job(job_id: str, store: EvaluationStore = evaluation_store) -> None:
    """評価の job を実行する（取り消されたら残りを取り消しにする）。

    ケースは上限つきの並行（``AGENT_EVALUATION_CONCURRENCY``。#2004）で、評価ケースの順に
    始める。結果はケースの位置に書くため、順番は並行でも変わらない。判定のモデルの呼び出しは
    job の中で 1 つの実行器を共有する（429 は全体で待ち、並行の数を下げる）。権限・設定の誤りで
    実行器が止まったら（``BoundedCallAborted``）、新しいケースを始めずに job を失敗にする。
    """
    from app.features.agent.runtime import runtime_repository

    job = store.start(job_id)
    if job is None:
        _cancel_remaining(job_id, store)
        return
    concurrency = evaluation_concurrency()
    judge_caller = make_ai_caller("evaluation_judge", max_concurrency=concurrency)
    pending = iter(enumerate(job.results))
    aborted: list[BoundedCallAborted] = []

    async def worker() -> None:
        for index, result in pending:
            if aborted or store.is_cancelled(job_id):
                return
            try:
                await _run_case(job, index, result.case, store, runtime_repository, judge_caller)
            except BoundedCallAborted as exc:
                aborted.append(exc)
                return

    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(max(1, min(concurrency, len(job.results)))):
                group.create_task(worker())
    except Exception as exc:  # noqa: BLE001 - job の境界では失敗を job に記録する
        error = exc.exceptions[0] if isinstance(exc, ExceptionGroup) and exc.exceptions else exc
        logger.error(
            "agent_evaluation_failed",
            extra={"job_id": job_id, "error_type": type(error).__name__},
            exc_info=error,
        )
        _cancel_unfinished_runs(job_id, store, runtime_repository)
        store.update(
            job_id,
            status=JobStatus.FAILED,
            error="評価を続けられませんでした。",
            finished_at=_now(),
        )
        return
    if aborted:
        # 権限・設定の誤り・割り当ての使い切り。同じ失敗をくり返さないよう残りを始めない。
        _cancel_remaining(job_id, store)
        store.update(
            job_id,
            status=JobStatus.FAILED,
            error=str(aborted[0])[:1000],
            finished_at=_now(),
        )
        return
    if store.is_cancelled(job_id):
        _cancel_remaining(job_id, store)
        return
    store.update(job_id, status=JobStatus.COMPLETED, finished_at=_now())


def _cancel_unfinished_runs(job_id: str, store: EvaluationStore, repository: Any) -> None:
    """job が途中で失敗したとき、並行で実行中だったケースの Run を取り消しにする（#2004）。"""
    try:
        current = store.get(job_id)
    except KeyError:
        return
    for index, item in enumerate(current.results):
        if item.status not in _UNFINISHED_STATUSES:
            continue
        if item.run_id:
            try:
                repository.cancel_run(item.run_id)
            except Exception:  # noqa: BLE001 - 後始末は補助。job の失敗の記録を優先する。
                logger.info("agent_evaluation_cancel_run_failed", extra={"job_id": job_id})
        store.update_result(job_id, index, status=CaseStatus.CANCELLED)


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
    judge_caller: BoundedCaller | None = None,
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

    def create_run(goal: str, *, thread_id: str | None, round_number: int) -> RunState:
        run: RunState = repository.create_builtin_run(
            RunCreateRequest(
                goal=goal,
                agent_id=job.agent_id,
                draft=draft,
                thread_id=thread_id,
                metadata={
                    "evaluation_job_id": job.id,
                    "evaluation_case_id": case.id,
                    EVALUATION_DRY_RUN_KEY: True,
                    # 問い返しに答えた往復の番号（最初の Run には付けない。#1845）。
                    **({"evaluation_clarification_round": round_number} if round_number else {}),
                },
            ),
            created_by_user_uuid=job.created_by_user_uuid,
            agent_version=pinned,
        )
        return run

    run = create_run(case.question, thread_id=None, round_number=0)
    store.update_result(job.id, index, run_id=run.id)
    timeout_seconds = case_timeout_seconds()
    runs: list[RunState] = []
    rounds: list[EvaluationClarificationRound] = []
    used: set[int] = set()
    while True:
        try:
            settled = await _settle_run(run.id, timeout_seconds)
        except TimeoutError:
            repository.cancel_run(run.id)
            finish(
                CaseStatus.TIMED_OUT,
                error=f"上限時間（{timeout_seconds:g} 秒）までに回答が出ませんでした。",
                tool_calls=_tool_calls(*runs),
                clarification_rounds=rounds,
            )
            return
        runs.append(settled)
        # 問い返しで止まったら、評価ケースの答えを同じ会話の次の Run で送る（#1845）。
        if (
            settled.status != RunStatus.COMPLETED
            or store.is_cancelled(job.id)
            or len(rounds) >= EVALUATION_CLARIFICATION_ROUNDS_MAX
        ):
            break
        answered = answer_clarifications(case, settled, used)
        if answered is None:
            break
        rounds.append(answered)
        run = create_run(answered.message, thread_id=settled.thread_id, round_number=len(rounds))
        store.update_result(job.id, index, run_id=run.id, clarification_rounds=list(rounds))
    tool_calls = _tool_calls(*runs)
    tools = {
        "tool_calls": tool_calls,
        "tool_selection_correct": tool_selection_correct(case, tool_calls),
        "tool_order_correct": tool_order_correct(case, tool_calls),
        "clarification_rounds": rounds,
        "ended_with_clarification": settled.status == RunStatus.COMPLETED
        and bool(_pending_items(settled)),
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
    if settled.status == RunStatus.WAITING_CONFIRMATION:
        repository.cancel_run(run.id)
        finish(
            CaseStatus.RUN_FAILED,
            error="回答前の確認で止まったため評価できません（Run は取り消しました）。",
            **tools,
        )
        return
    if settled.status != RunStatus.COMPLETED:
        finish(CaseStatus.RUN_FAILED, error=_failure_message(settled), **tools)
        return
    answer = _answer_text(settled.artifacts)
    try:
        judgement, attempts = await judge_answer(
            case, answer, tuple(item.message for item in rounds), caller=judge_caller
        )
    except BoundedCallAborted as exc:
        # 権限・設定の誤り（#2004）。このケースに理由を残し、job を止める。
        logger.warning(
            "agent_evaluation_judge_aborted",
            extra={"job_id": job.id, "error_kind": exc.kind.value},
        )
        finish(CaseStatus.JUDGE_FAILED, answer=answer, error=str(exc)[:1000], **tools)
        raise
    except JudgeIncompleteError as exc:
        logger.warning(
            "agent_evaluation_judge_failed",
            extra={"job_id": job.id, "error_type": "JudgeIncompleteError"},
        )
        finish(
            CaseStatus.JUDGE_FAILED,
            answer=answer,
            error=exc.message,
            judge_attempts=exc.attempts,
            **tools,
        )
        return
    except Exception as exc:  # noqa: BLE001 - 判定の失敗はケースの結果に残して続ける
        cause = exc.__cause__ if isinstance(exc, JudgeCallError) and exc.__cause__ else exc
        logger.warning(
            "agent_evaluation_judge_failed",
            extra={"job_id": job.id, "error_type": type(cause).__name__},
        )
        message = (
            cause.message
            if isinstance(cause, builtin_runtime.BuiltinRuntimeError)
            else f"判定のモデルの呼び出しに失敗しました（{type(cause).__name__}）。"
        )
        finish(
            CaseStatus.JUDGE_FAILED,
            answer=answer,
            error=message,
            judge_attempts=exc.attempts if isinstance(exc, JudgeCallError) else 1,
            **tools,
        )
        return
    finish(
        CaseStatus.JUDGED,
        answer=answer,
        judgement=judgement,
        judge_attempts=attempts,
        **tools,
    )
