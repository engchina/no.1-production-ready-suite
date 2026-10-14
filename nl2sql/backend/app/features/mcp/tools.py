"""NL2SQL が MCP で公開するツール（#231）。

Agent は Run の利用者のサービストークンで `POST /api/mcp` を呼ぶ。認証と利用者の特定は
`authorize_api_request`（`service_token_paths={"/mcp"}`）が行い、DeepSec の actor も同じ経路で
入る。ここでは画面の route と同じ service 層・業務プロファイルの範囲・ジョブの所有者の判定を使う。

- `nl2sql_list_profiles`: `GET /nl2sql/profiles/search` と同じ範囲の業務プロファイル一覧
- `nl2sql_recommend_profile`: `POST /nl2sql/recommend-profile`（しきい値未満は推薦なし。
  `profile_ids` を渡すとその候補の中からだけ推薦する）
- `nl2sql_query`: `POST /nl2sql/jobs` でジョブを作り、`wait_seconds` まで結果を待つ。
  `profile_id` は必須（画面の API と違い、既定の業務プロファイル `"default"` に黙って切り替えない。
  #1379）
- `nl2sql_get_job`: `GET /nl2sql/jobs/{job_id}`（本人のジョブだけ）
- `nl2sql_cancel_requirement`: AI要件確認の session を閉じる
  （`POST /nl2sql/query-sessions/{id}/cancel`。利用者が Agent のチャットで
  「データベースの検索は不要」としたとき、Agent の Control Plane が呼ぶ）

AI要件確認（#1439・#1441）: `nl2sql_query` は SQL を作る前に、画面の AI要件確認と同じ流れ
（`POST /nl2sql/query-sessions` の guided・回答・`apply`・`POST /nl2sql/jobs` の
`requirement_session_id`）で、Schema・業務プロファイル・オントロジーから SQL の要件を確かめる。
足りない情報・明確でない情報があれば SQL を作らずに `status=needs_clarification` と確認の項目
（`requirement.question`。質問・候補・理由。表・列は物理名）を 1 つだけ返す。呼び出し元が利用者に
聞き、`requirement_session_id` と `answer` を付けて呼び直すと要件を更新し、確認の要るものが無くなり
SELECT と FROM が明確になってから SQL を生成・実行する。確認が要らなければ聞かずに進む。

サービストークンにプロファイルの範囲の claim（`profile_ids`。業務 Agent のデータの範囲。#1379）が
あれば、業務プロファイルは「利用者の権限 ∩ claim」で判定する: 範囲外の `profile_id` は 403
（`PROFILE_SCOPE_FORBIDDEN`）、一覧・推薦は範囲で絞る。claim の無い呼び出しは今までどおり。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from functools import wraps
from typing import Any, Literal

from fastapi import HTTPException, Request
from pr_backend_core.mcp import McpServer, McpTool, McpToolError
from pr_system_settings.auth.dependencies import service_token_profile_scope
from pr_system_settings.auth.errors import SecurityApiError
from pr_system_settings.auth.service_token import (
    PROFILE_SCOPE_FORBIDDEN_CODE,
    PROFILE_SCOPE_MAX_IDS,
    narrow_to_profile_scope,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.concurrency import run_sync_io
from app.features.nl2sql import ontology_router
from app.features.nl2sql.models import (
    JobCreateRequest,
    JobData,
    JobStatus,
    ProfileRecommendationRequest,
)
from app.features.nl2sql.ontology_models import (
    ClarificationMode,
    QuerySessionStatus,
    RequirementPhase,
)
from app.features.nl2sql.ontology_router import (
    ClarificationAnswerRequest,
    QuerySessionApiCreate,
    QuerySessionData,
    RequirementApplyRequest,
)
from app.features.nl2sql.ontology_service import OntologyNotFoundError, OntologyServiceError
from app.features.nl2sql.oracle_adapter import SelectAiCredentialMissingError
from app.features.nl2sql.profile_access import (
    allowed_profile_ids_for_request,
    assert_profile_access,
    principal_from_request,
    profile_access_denied,
)
from app.features.nl2sql.router import _with_requirement
from app.features.nl2sql.select_ai_readiness import select_ai_credential_missing_message
from app.features.nl2sql.service import (
    JOB_FAILED_ERROR_CODE,
    SCHEMA_CATALOG_EMPTY_ERROR_CODE,
    Nl2SqlPersistenceUnavailable,
    Nl2SqlRepositoryOperationFailed,
    SchemaCatalogEmptyError,
    nl2sql_service,
)
from app.features.nl2sql.sql_requirement import RequirementAiUnavailableError
from app.security.permissions import (
    PROFILE_READ_PERMISSION,
    QUERY_GENERATE_PERMISSION,
    SQL_EXECUTE_PERMISSION,
)
from app.settings import get_settings

SERVER_NAME = "production-ready-nl2sql"
DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 1000
# 1 回の MCP 呼び出しで worker を長く塞がないよう、待ちの上限は 45 秒にする
# （gunicorn の worker の timeout より十分短くする。unit は init_script.sh の `--timeout 300`）。
MAX_WAIT_SECONDS = 45
JOB_POLL_INTERVAL_SECONDS = 0.5
_TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.ERROR})
_JOB_NOT_FOUND_MESSAGE = "指定されたジョブが見つかりません。"
_REQUIREMENT_NOT_FOUND_MESSAGE = (
    "指定された AI要件確認が見つかりません。requirement_session_id を付けずに "
    "nl2sql_query を呼び直してください。"
)
_REQUIREMENT_CLOSED_MESSAGE = (
    "この AI要件確認は終了しています（取り消し・期限切れ）。requirement_session_id を付けずに "
    "nl2sql_query を呼び直してください。"
)
_REQUIREMENT_SKIPPED_MESSAGE = (
    "OCI Enterprise AI が設定されていないため、AI要件確認をせずに SQL を生成しました。"
)
REQUIREMENT_AI_NOT_CONFIGURED_CODE = "REQUIREMENT_AI_NOT_CONFIGURED"
# この業務プロファイルの範囲では SQL を作れない（AI要件確認。#1441）。
REQUIREMENT_UNANSWERABLE_CODE = "REQUIREMENT_UNANSWERABLE"
# 変更できない（終了した・実行中の）AI要件確認の session。
_CLOSED_SESSION_STATUSES = frozenset(
    {
        QuerySessionStatus.EXECUTING,
        QuerySessionStatus.DONE,
        QuerySessionStatus.ERROR,
        QuerySessionStatus.CANCELLED,
    }
)

_INSTRUCTIONS = (
    "Production Ready NL2SQL のツールです。利用者が使える業務プロファイルの範囲で、"
    "自然言語の質問から SQL を生成・実行します。nl2sql_query には業務プロファイルの ID"
    "（profile_id）が必須です。分からないときは "
    "nl2sql_list_profiles / nl2sql_recommend_profile で選び、nl2sql_query に渡してください。"
    "nl2sql_query が pending / running を返したら、"
    "job_id と wait_seconds（最大 45 秒）を nl2sql_get_job に渡し、"
    "done / error になるまで続きを取得します。"
    "nl2sql_query が needs_clarification を返したら SQL はまだ作っていません。"
    "requirement.question の問いを利用者に 1 つだけ確かめ、答えを answer に入れ、"
    "requirement.session_id を requirement_session_id に渡して nl2sql_query を呼び直します。"
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListProfilesInput(_Input):
    query: str | None = Field(
        default=None, max_length=200, description="業務プロファイル名・説明の絞り込み語"
    )
    limit: int = Field(default=20, ge=1, le=100, description="返す件数の上限")


def _require_question(value: str) -> str:
    # 空白だけの質問は画面・API（JobCreateRequest）と同じく拒否する。ジョブの作成で検証に落ちて
    # ツールの内部エラーにならないよう、引数の誤り（MCP_TOOL_ARGUMENTS_INVALID）にする（#1054）。
    if not value.strip():
        raise ValueError("質問を入力してください。")
    return value


def _require_profile_id(value: str) -> str:
    # 空白だけの ID を既定の業務プロファイルとして扱わない（#1379）。
    stripped = value.strip()
    if not stripped:
        raise ValueError("業務プロファイルの ID を指定してください。")
    return stripped


class RecommendProfileInput(_Input):
    question: str = Field(min_length=1, max_length=4000, description="利用者の質問（日本語）")
    profile_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=PROFILE_SCOPE_MAX_IDS,
        description="推薦の候補にする業務プロファイルの ID（省略時は使えるすべて）",
    )

    _validate_question = field_validator("question")(_require_question)

    @field_validator("profile_ids")
    @classmethod
    def _validate_profile_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(_require_profile_id(item) for item in value))


class RequirementAnswerInput(_Input):
    """AI要件確認の確認の項目への利用者の答え（#1441）。"""

    item_id: str = Field(
        min_length=1,
        max_length=128,
        description="答える確認の項目の ID（前の結果の requirement.question.item_id）",
    )
    selected_candidate_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "利用者が選んだ候補の ID（requirement.question.candidates の id）。"
            "候補に無い答えは free_text に入れる"
            "（selected_candidate_ids と free_text はどちらか一方）"
        ),
    )
    free_text: str = Field(
        default="", max_length=2000, description="候補に無い利用者の答え（利用者の言葉のまま）"
    )

    @model_validator(mode="after")
    def _require_answer(self) -> RequirementAnswerInput:
        # 画面の AI要件確認と同じく、候補を選ぶか自由入力のどちらか一方で答える。
        if bool(self.selected_candidate_ids) == bool(self.free_text.strip()):
            raise ValueError(
                "selected_candidate_ids か free_text のどちらか一方だけを指定してください。"
            )
        return self


class QueryInput(_Input):
    question: str = Field(min_length=1, max_length=4000, description="利用者の質問（日本語）")

    _validate_question = field_validator("question")(_require_question)
    profile_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "使う業務プロファイルの ID（必須）。分からないときは nl2sql_list_profiles / "
            "nl2sql_recommend_profile で選ぶ"
        ),
    )
    _validate_profile_id = field_validator("profile_id")(_require_profile_id)
    row_limit: int = Field(
        default=DEFAULT_ROW_LIMIT, ge=1, le=MAX_ROW_LIMIT, description="取得する行数の上限"
    )
    wait_seconds: int = Field(
        default=40,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description="結果を待つ秒数。過ぎたら pending / running のまま job_id を返す",
    )
    requirement_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "前の nl2sql_query が needs_clarification で返した requirement.session_id。"
            "確認の続き（answer を付ける）と SQL の生成に使う。省略すると新しく確かめ始める"
        ),
    )
    answer: RequirementAnswerInput | None = Field(
        default=None,
        description=(
            "requirement.question への利用者の答え（requirement_session_id と一緒に渡す）。"
            "利用者が答えていない問いを推測で埋めない"
        ),
    )

    @model_validator(mode="after")
    def _answer_needs_session(self) -> QueryInput:
        if self.answer is not None and self.requirement_session_id is None:
            raise ValueError("answer は requirement_session_id と一緒に指定してください。")
        return self


class CancelRequirementInput(_Input):
    requirement_session_id: str = Field(
        min_length=1,
        max_length=128,
        description="閉じる AI要件確認の session（nl2sql_query の requirement.session_id）",
    )


class GetJobInput(_Input):
    job_id: str = Field(min_length=1, max_length=128)
    wait_seconds: int = Field(
        default=0,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description=(
            "完了を待つ秒数（0 はすぐ返す）。pending / running の間は 40 程度を渡し、"
            "終わるまでの呼び出しの回数を減らす"
        ),
    )


class ProfileItem(BaseModel):
    id: str
    name: str
    category: str
    description: str


class ListProfilesOutput(BaseModel):
    profiles: list[ProfileItem]


class RecommendationCandidate(BaseModel):
    id: str
    name: str
    reason: str | None = None
    score: float | None = None


class RecommendProfileOutput(BaseModel):
    recommended_profile_id: str | None
    rewritten_question: str | None
    candidates: list[RecommendationCandidate]


class QueryStatus(StrEnum):
    """`nl2sql_query` / `nl2sql_get_job` の状態（ジョブの状態と、AI要件確認で止めた状態）。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    # SQL を作る前に、利用者に確かめる項目がある（#1441）。
    NEEDS_CLARIFICATION = "needs_clarification"
    # この業務プロファイルの範囲では SQL を作れない（表をつなげない・SELECT / FROM が決まらない）。
    UNANSWERABLE = "unanswerable"


class RequirementCandidateOutput(BaseModel):
    id: str
    label: str
    description: str = ""
    # 表・列の物理名（論理名を括弧で添える）。
    refs: str = ""


class RequirementQuestionOutput(BaseModel):
    """利用者に確かめる 1 つの項目（表・列は物理名）。"""

    item_id: str
    section: str = Field(
        description=(
            "SQL のどの部分か（select / from / join / where / period / group_by / order_by / "
            "limit / subquery / other）"
        )
    )
    label: str
    question: str
    reason: str = ""
    answer_kind: str = Field(description="single_select / multi_select / free_text")
    candidates: list[RequirementCandidateOutput] = Field(default_factory=list)
    # AI の今の理解（あれば）。
    value: str = ""
    refs: str = ""


class RequirementOutput(BaseModel):
    """AI要件確認の状態（#1441。画面の AI要件確認と同じ session）。"""

    session_id: str
    status: str = Field(
        description=(
            "needs_answer / ready / unanswerable / applied / cancelled / skipped"
            "（OCI Enterprise AI が無い構成で確かめなかった）"
        )
    )
    question: RequirementQuestionOutput | None = None
    remaining_count: int = 0
    answered_count: int = 0
    select_from_clear: bool = False
    minimum_missing: list[str] = Field(default_factory=list)
    # 確かめた要件のまとめと、それを書き直した質問の文（表・列は物理名）。
    summary: str = ""
    clarified_question: str = ""
    sql_skeleton: str = ""
    message: str = ""


class CancelRequirementOutput(BaseModel):
    requirement_session_id: str
    status: str = Field(description="cancelled（閉じた）/ closed（もう閉じていた）")


class Nl2SqlJobResult(BaseModel):
    """契約の `Nl2SqlJobResult`（Agent の `external_nl2sql_*` がそのまま通す）。"""

    # AI要件確認で止めた（needs_clarification / unanswerable）ときはジョブを作らないので null。
    job_id: str | None = None
    status: QueryStatus
    profile_id: str | None = None
    generated_sql: str | None = None
    executable_sql: str | None = None
    explanation: str | None = None
    is_safe: bool | None = None
    safety_issues: list[str] = Field(default_factory=list)
    safety_check: Literal["checked", "skipped"] | None = Field(
        default=None,
        description=(
            "SQL の安全確認。checked（確かめた）/ skipped（Select AI・Select AI Agent が生成した "
            "SELECT 文のため省略。業務プロファイルの表・列の範囲の照合も行わない）"
        ),
    )
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    returned_count: int = 0
    total: int | None = None
    has_more: bool = False
    truncated: bool = False
    history_id: str | None = None
    error_code: str | None = Field(
        default=None,
        description=(
            "失敗の分類。status が error のときは必ず入る。Oracle のエラーなら ORA-xxxxx / "
            "DPY-xxxx、それ以外は SQL_BLOCKED（安全性の判定で止めた）・SQL_EXECUTION_FAILED・"
            "SCHEMA_CATALOG_EMPTY・JOB_CANCELLED・JOB_INTERRUPTED・NL2SQL_JOB_FAILED・"
            "NL2SQL_PROFILE_REVALIDATION_REQUIRED（公開の後に業務プロファイルか Schema が変わり、"
            "管理者が業務定義を再検証して公開するまで再試行しても直らない）、"
            "OCI Enterprise AI の HTTP の失敗なら ENTERPRISE_AI_AUTHORIZATION_FAILED・"
            "ENTERPRISE_AI_AUTHENTICATION_FAILED・ENTERPRISE_AI_MODEL_NOT_FOUND"
            "（設定の誤りで、再試行しても直らない）・ENTERPRISE_AI_RATE_LIMITED・"
            "ENTERPRISE_AI_UNAVAILABLE・ENTERPRISE_AI_REQUEST_REJECTED など"
        ),
    )
    error_message: str | None = None
    # AI要件確認の状態（nl2sql_query だけ。#1441）。
    requirement: RequirementOutput | None = None


def _translate_domain_errors[A, R](
    handler: Callable[[A], Awaitable[R]],
) -> Callable[[A], Awaitable[R]]:
    """画面では例外 handler が返す業務エラーを、ツールの `isError` に変換する。"""

    @wraps(handler)
    async def wrapped(arguments: A) -> R:
        try:
            await run_sync_io(nl2sql_service.ensure_persistence_available)
            return await handler(arguments)
        except (Nl2SqlPersistenceUnavailable, Nl2SqlRepositoryOperationFailed) as exc:
            raise McpToolError(exc.reason_code, exc.public_message) from exc
        except SchemaCatalogEmptyError as exc:
            raise McpToolError(SCHEMA_CATALOG_EMPTY_ERROR_CODE, str(exc)) from exc
        except SelectAiCredentialMissingError as exc:
            # Select AI / Select AI Agent の生成は Credential が前提（画面の 409 と同じ。#1688）。
            raise McpToolError(exc.code, select_ai_credential_missing_message(exc)) from exc
        except OntologyServiceError as exc:
            # AI要件確認の業務エラー（Schema が読めない・LLM が使えない・session の終了など）。
            message = exc.message_ja
            finding_codes = getattr(exc, "finding_codes", None)
            if finding_codes:
                message = f"{message} 対象: {', '.join(str(item) for item in finding_codes)}"
            # AI要件確認の LLM の失敗は、種類・HTTP ステータス・モデル ID・所要時間などを添える
            # （#1715。secret・prompt は入れない）。
            details = getattr(exc, "details", None)
            raise McpToolError(exc.code, message, details=details or None) from exc

    return wrapped


def _actor(request: Request) -> tuple[str, bool]:
    principal = principal_from_request(request)
    if principal is None:
        # 認証無効（NL2SQL_APP_AUTH_ENABLED=false）は画面の route と同じく actor 制約を外す。
        return "", False
    return principal.user_uuid, principal.is_system_admin


def _job_error_message(job: JobData) -> str | None:
    """画面は失敗の文と詳細（例外・ORA-… の元の文）を分けて出す（#1072）。MCP の呼び出し側
    （Agent）は文を 1 つで受け取るため、原因を判断できるよう詳細を文の後ろに付けて返す。"""
    if job.error_message and job.error_detail:
        return f"{job.error_message}\n詳細: {job.error_detail}"
    return job.error_message


def _job_result(job: JobData) -> Nl2SqlJobResult:
    result = job.result
    output = Nl2SqlJobResult(
        job_id=job.job_id,
        status=QueryStatus(job.status.value),
        profile_id=job.profile_id or None,
        # 旧い snapshot など、error_code を持たない失敗も分類できるようにする（#847）。
        error_code=job.error_code
        or (JOB_FAILED_ERROR_CODE if job.status == JobStatus.ERROR else None),
        error_message=_job_error_message(job),
    )
    if result is None:
        return output
    safety = result.safety
    issues = [safety.blocked_reason] if safety.blocked_reason else []
    output.generated_sql = result.generated_sql or None
    output.executable_sql = result.executable_sql or None
    output.explanation = result.explanation or None
    output.is_safe = safety.is_safe
    output.safety_issues = [*issues, *safety.warnings]
    output.safety_check = "checked" if safety.safety_checked else "skipped"
    output.columns = list(result.results.columns)
    output.rows = list(result.results.rows)
    output.returned_count = result.results.returned_count or len(result.results.rows)
    output.total = result.results.total
    output.has_more = result.results.has_more
    output.truncated = result.results.truncated
    output.history_id = result.history_id or None
    return output


def _requirement_output(data: QuerySessionData) -> RequirementOutput:
    """AI要件確認の session を、呼び出し元（Agent）に返す形にする（表・列は物理名）。"""

    session = data.session
    state = data.requirement_state
    draft = session.requirement
    if session.status == QuerySessionStatus.CANCELLED:
        status = "cancelled"
    elif session.requirement_applied_at is not None:
        status = "applied"
    elif state is not None:
        status = state.status.value
    else:
        status = RequirementPhase.UNANSWERABLE.value
    item = state.current_item if state is not None else None
    question = None
    if item is not None and status == RequirementPhase.NEEDS_ANSWER.value:
        question = RequirementQuestionOutput(
            item_id=item.id,
            section=item.section.value,
            label=item.label_ja,
            question=item.question_ja or item.label_ja,
            reason=item.reason_ja,
            answer_kind=item.answer_kind.value,
            candidates=[
                RequirementCandidateOutput(
                    id=candidate.id,
                    label=candidate.label_ja,
                    description=candidate.description_ja,
                    refs=candidate.refs_ja,
                )
                for candidate in item.candidates
            ],
            value=item.value_ja,
            refs=item.refs_ja,
        )
    return RequirementOutput(
        session_id=session.id,
        status=status,
        question=question,
        remaining_count=len(state.unresolved_items) if state is not None else 0,
        answered_count=state.answered_count if state is not None else 0,
        select_from_clear=state.select_from_clear if state is not None else False,
        minimum_missing=list(state.minimum_missing_ja) if state is not None else [],
        summary=state.summary_ja if state is not None else "",
        clarified_question=draft.clarified_question_ja if draft is not None else "",
        sql_skeleton=(state.sql_skeleton if state is not None else "")
        or (draft.sql_skeleton if draft is not None else ""),
        message=state.message_ja if state is not None else "",
    )


def _requirement_ready(data: QuerySessionData) -> bool:
    """SQL の生成に進める（確認の要る項目が無く、SELECT と FROM が明確）か。

    画面は再検査の回数の上限に達したら残った項目を AI に任せて進められるが、MCP（Agent）は
    確認の要るものが無くなるまで 1 問ずつ聞く（#1441）。
    """

    state = data.requirement_state
    return (
        state is not None
        and state.status == RequirementPhase.READY
        and state.select_from_clear
        and not state.unresolved_items
        and state.can_apply
    )


def _profile_scope_forbidden(profile_id: str) -> SecurityApiError:
    return SecurityApiError(
        403,
        f"業務プロファイル「{profile_id}」は、この呼び出し元（業務 Agent）の"
        "データの範囲の外です。nl2sql_list_profiles で使える業務プロファイルを確かめてください。",
        code=PROFILE_SCOPE_FORBIDDEN_CODE,
    )


def build_mcp_server(request: Request) -> McpServer:
    """リクエストの利用者で判定するツールを持つ MCP サーバーを作る。"""

    def scope() -> frozenset[str] | None:
        """呼び出し元の範囲（サービストークンの claim。#1379）。None は範囲なし。"""
        return service_token_profile_scope(request)

    def allowed_profile_ids() -> set[str] | None:
        """使える業務プロファイル（利用者の権限 ∩ claim）。None は制限なし。"""
        allowed = narrow_to_profile_scope(
            allowed_profile_ids_for_request(request, nl2sql_service), scope()
        )
        return set(allowed) if allowed is not None else None

    def own_job(job_id: str) -> JobData:
        actor_user_uuid, _ = _actor(request)
        try:
            # 画面の route と違い、管理権限があっても本人のジョブだけを返す（契約）。
            job = nl2sql_service.get_job(
                job_id, actor_user_uuid=actor_user_uuid, actor_can_manage=False
            )
        except PermissionError:
            job = None  # 他人のジョブは存在も明かさない
        if job is None:
            raise HTTPException(status_code=404, detail=_JOB_NOT_FOUND_MESSAGE)
        return job

    def own_session(session_id: str) -> QuerySessionData:
        """本人の AI要件確認の session（ジョブと同じく、管理権限があっても本人のものだけ）。"""
        try:
            data = ontology_router.ontology_runtime.get_session(session_id)
        except OntologyNotFoundError:
            data = None
        principal = principal_from_request(request)
        # 持ち主の無い session（認証無効の期間に作ったもの）も本人の物とみなさない。
        if (
            data is not None
            and principal is not None
            and data.session.actor_user_uuid.strip() != principal.user_uuid
        ):
            data = None  # 他人の session は存在も明かさない
        if data is None or data.session.clarification_mode != ClarificationMode.GUIDED:
            raise HTTPException(status_code=404, detail=_REQUIREMENT_NOT_FOUND_MESSAGE)
        scoped = scope()
        if scoped is not None and data.session.profile_id not in scoped:
            raise _profile_scope_forbidden(data.session.profile_id)
        assert_profile_access(request, data.session.profile_id)
        return data

    async def wait_for_job(job_id: str, wait_seconds: int) -> Nl2SqlJobResult:
        deadline = time.monotonic() + wait_seconds
        while True:
            job = await run_sync_io(own_job, job_id)
            remaining = deadline - time.monotonic()
            if job.status in _TERMINAL_STATUSES or remaining <= 0:
                return _job_result(job)
            await asyncio.sleep(min(JOB_POLL_INTERVAL_SECONDS, remaining))

    @_translate_domain_errors
    async def list_profiles(arguments: ListProfilesInput) -> ListProfilesOutput:
        def run() -> ListProfilesOutput:
            page = nl2sql_service.search_profiles(
                cursor=None,
                limit=arguments.limit,
                query=arguments.query or "",
                include_archived=False,
                allowed_profile_ids=allowed_profile_ids(),
            )
            return ListProfilesOutput(
                profiles=[
                    ProfileItem(
                        id=item.id,
                        name=item.name,
                        category=item.category,
                        description=item.description,
                    )
                    for item in page.items
                ]
            )

        return await run_sync_io(run)

    @_translate_domain_errors
    async def recommend_profile(arguments: RecommendProfileInput) -> RecommendProfileOutput:
        def run() -> RecommendProfileOutput:
            allowed = allowed_profile_ids()
            if arguments.profile_ids is not None:
                # 候補の一覧（#1379）。権限・claim の外の ID は候補にしない（積）。
                requested = set(arguments.profile_ids)
                allowed = requested if allowed is None else allowed & requested
            if allowed is not None and not allowed:
                return RecommendProfileOutput(
                    recommended_profile_id=None, rewritten_question=None, candidates=[]
                )
            try:
                data = nl2sql_service.recommend_profile(
                    ProfileRecommendationRequest(question=arguments.question),
                    allowed_profile_ids=allowed,
                )
            except ValueError as exc:
                if "権限" in str(exc):
                    raise profile_access_denied() from exc
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            candidates = [
                RecommendationCandidate(
                    id=candidate.profile_id,
                    name=candidate.profile_name,
                    reason="、".join(candidate.matched_terms) or None,
                    score=candidate.score,
                )
                for candidate in data.candidates
                if allowed is None or candidate.profile_id in allowed
            ]
            recommended = data.recommended_profile_id or None
            # 画面と同じく、しきい値未満の推薦は採用しない。
            if data.confidence < data.confidence_threshold:
                recommended = None
            if recommended is not None and allowed is not None and recommended not in allowed:
                recommended = None
            return RecommendProfileOutput(
                recommended_profile_id=recommended,
                rewritten_question=(data.rewritten_question or None) if recommended else None,
                candidates=candidates,
            )

        return await run_sync_io(run)

    async def start_and_wait(job_request: JobCreateRequest, wait_seconds: int) -> Nl2SqlJobResult:
        actor_user_uuid, actor_is_system_admin = _actor(request)
        try:
            created = await run_sync_io(
                nl2sql_service.start_job,
                job_request,
                actor_user_uuid=actor_user_uuid,
                actor_is_system_admin=actor_is_system_admin,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await wait_for_job(created.job_id, wait_seconds)

    @_translate_domain_errors
    async def query(arguments: QueryInput) -> Nl2SqlJobResult:
        scoped = scope()
        if scoped is not None and arguments.profile_id not in scoped:
            raise _profile_scope_forbidden(arguments.profile_id)
        assert_profile_access(request, arguments.profile_id)
        actor_user_uuid, actor_is_system_admin = _actor(request)
        runtime = ontology_router.ontology_runtime

        def requirement_step() -> QuerySessionData:
            """AI要件確認を始める・続ける（#1441。SQL はまだ作らない）。"""
            if arguments.requirement_session_id is None:
                # 業務プロファイルは呼び出し元が profile_id で明示して選んだ（推薦の確認を省く）。
                return runtime.create_session(
                    QuerySessionApiCreate(
                        question=arguments.question,
                        profile_id=arguments.profile_id,
                        row_limit=arguments.row_limit,
                        clarification_mode=ClarificationMode.GUIDED,
                    ),
                    actor_user_uuid=actor_user_uuid,
                    actor_is_system_admin=actor_is_system_admin,
                    profile_confirmed=True,
                )
            data = own_session(arguments.requirement_session_id)
            if data.session.profile_id != arguments.profile_id:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "requirement_session_id の業務プロファイルと profile_id が違います。"
                        "同じ profile_id で呼び直してください。"
                    ),
                )
            if data.session.requirement_applied_at is not None:
                return data
            if data.session.status in _CLOSED_SESSION_STATUSES:
                raise HTTPException(status_code=409, detail=_REQUIREMENT_CLOSED_MESSAGE)
            if arguments.answer is None:
                return data
            answer = arguments.answer
            return runtime.answer_clarification(
                data.session.id,
                ClarificationAnswerRequest(
                    # 会話は 1 問ずつ進むので、今の版に答える（古い版の上書きは session が防ぐ）。
                    base_version=data.session.current_intent_version,
                    item_id=answer.item_id,
                    selected_candidate_ids=list(dict.fromkeys(answer.selected_candidate_ids)),
                    free_text=answer.free_text.strip(),
                ),
            )

        started = time.monotonic()
        try:
            data = await run_sync_io(requirement_step)
        except RequirementAiUnavailableError as exc:
            if exc.code != REQUIREMENT_AI_NOT_CONFIGURED_CODE or arguments.requirement_session_id:
                raise
            # OCI Enterprise AI を設定していない構成（Select AI だけ・ローカルの決定論）は、
            # AI要件確認をせずに SQL を作る。確かめていないことを requirement で返す。
            result = await start_and_wait(
                JobCreateRequest(
                    question=arguments.question,
                    profile_id=arguments.profile_id,
                    row_limit=arguments.row_limit,
                ),
                arguments.wait_seconds,
            )
            result.requirement = RequirementOutput(
                session_id="", status="skipped", message=_REQUIREMENT_SKIPPED_MESSAGE
            )
            return result
        if data.session.requirement_applied_at is None:
            if not _requirement_ready(data):
                requirement = _requirement_output(data)
                if requirement.question is not None:
                    # 確認の要る項目がある: SQL を作らずに 1 問だけ返す。
                    return Nl2SqlJobResult(
                        status=QueryStatus.NEEDS_CLARIFICATION,
                        profile_id=arguments.profile_id,
                        requirement=requirement,
                    )
                # 聞く項目が無いのに SELECT / FROM が決まらない・範囲で答えられない: 閉じて返す。
                if data.session.status not in _CLOSED_SESSION_STATUSES:
                    await run_sync_io(runtime.cancel_session, data.session.id)
                return Nl2SqlJobResult(
                    status=QueryStatus.UNANSWERABLE,
                    profile_id=arguments.profile_id,
                    error_code=REQUIREMENT_UNANSWERABLE_CODE,
                    error_message=requirement.message or None,
                    requirement=requirement.model_copy(update={"status": "unanswerable"}),
                )
            data = await run_sync_io(
                runtime.apply_requirement,
                data.session.id,
                RequirementApplyRequest(base_version=data.session.current_intent_version),
            )
        draft = data.session.requirement
        if draft is None:  # pragma: no cover - guided の session は必ず下書きを持つ
            raise HTTPException(status_code=409, detail="AI要件確認の要件がありません。")
        # 画面と同じく、確かめた要件（構造化した要件と書き直した質問の文）で job を作る。質問の文は
        # 利用者の質問のままにし、書き直した文は生成にだけ使う（`generation_question`。#1727）。
        # 生成の方式は指定せず、ジョブの作成が業務プロファイルの既定の生成方式にする（#1524）。
        # use_ontology_context 等は画面と同じく JobCreateRequest の既定で作る。
        # row_limit は必ず渡す（None だと全件を取得する）。
        job_request = _with_requirement(
            JobCreateRequest(
                question=data.session.original_question,
                profile_id=arguments.profile_id,
                row_limit=arguments.row_limit,
                requirement_session_id=data.session.id,
            ),
            getattr(request.state, "principal", None),
        )
        # AI要件確認（LLM）にかかった分だけ、ジョブを待つ秒数を減らす（1 回の呼び出しの長さを
        # 「AI要件確認 + wait_seconds」にしない。終わらなければ job_id を返し、続きは
        # nl2sql_get_job）。
        remaining = max(0, arguments.wait_seconds - int(time.monotonic() - started))
        result = await start_and_wait(job_request, remaining)
        result.requirement = _requirement_output(data)
        return result

    @_translate_domain_errors
    async def get_job(arguments: GetJobInput) -> Nl2SqlJobResult:
        return await wait_for_job(arguments.job_id, arguments.wait_seconds)

    @_translate_domain_errors
    async def cancel_requirement(arguments: CancelRequirementInput) -> CancelRequirementOutput:
        def run() -> CancelRequirementOutput:
            runtime = ontology_router.ontology_runtime
            data = own_session(arguments.requirement_session_id)
            if data.session.status in _CLOSED_SESSION_STATUSES:
                return CancelRequirementOutput(
                    requirement_session_id=data.session.id, status="closed"
                )
            runtime.cancel_session(data.session.id)
            return CancelRequirementOutput(
                requirement_session_id=data.session.id, status="cancelled"
            )

        return await run_sync_io(run)

    tools = [
        McpTool(
            name="nl2sql_list_profiles",
            description="利用者が使える業務プロファイル（SQL を生成する対象の範囲）を一覧します。",
            input_model=ListProfilesInput,
            handler=list_profiles,
            output_model=ListProfilesOutput,
            permissions=(frozenset({PROFILE_READ_PERMISSION}),),
        ),
        McpTool(
            name="nl2sql_recommend_profile",
            description=(
                "質問に合う業務プロファイルを推薦します。推薦できないときは null です。"
                "profile_ids を渡すと、その中からだけ推薦します。"
            ),
            input_model=RecommendProfileInput,
            handler=recommend_profile,
            output_model=RecommendProfileOutput,
            permissions=(frozenset({QUERY_GENERATE_PERMISSION}),),
        ),
        McpTool(
            name="nl2sql_query",
            description=(
                "質問から SELECT SQL を生成して実行し、結果の行を返します。"
                "SQL の生成の方式は、業務プロファイルの既定の生成方式です。"
                "SQL を作る前に、足りない情報・明確でない情報を確かめます（AI要件確認）。"
                "確かめる項目があれば SQL を作らずに status=needs_clarification と "
                "requirement.question（質問・候補・理由。表・列は物理名）を 1 つだけ返すので、"
                "利用者に聞いてから requirement_session_id と answer を付けて呼び直します。"
                "wait_seconds 内に終わらなければ job_id を返すので、"
                "nl2sql_get_job で続きを取ります。"
            ),
            input_model=QueryInput,
            handler=query,
            output_model=Nl2SqlJobResult,
            permissions=(
                frozenset({QUERY_GENERATE_PERMISSION}),
                frozenset({SQL_EXECUTE_PERMISSION}),
            ),
            read_only=False,
        ),
        McpTool(
            name="nl2sql_get_job",
            description=(
                "nl2sql_query で作ったジョブの状態と結果を返します（本人のジョブだけ）。"
                "wait_seconds の間、完了を待ってから返します。"
            ),
            input_model=GetJobInput,
            handler=get_job,
            output_model=Nl2SqlJobResult,
            permissions=(frozenset({QUERY_GENERATE_PERMISSION}),),
        ),
        McpTool(
            name="nl2sql_cancel_requirement",
            description=(
                "nl2sql_query の AI要件確認（needs_clarification）を取り消して閉じます"
                "（本人の確認だけ）。利用者がデータベースの検索は不要と伝えたときに使います。"
            ),
            input_model=CancelRequirementInput,
            handler=cancel_requirement,
            output_model=CancelRequirementOutput,
            permissions=(frozenset({QUERY_GENERATE_PERMISSION}),),
            read_only=False,
        ),
    ]
    return McpServer(
        name=SERVER_NAME,
        version=get_settings().app_version,
        tools=tools,
        instructions=_INSTRUCTIONS,
    )


def has_any_permission_for(request: Request) -> Callable[[frozenset[str]], bool]:
    principal = principal_from_request(request)
    if principal is None:
        # 認証無効（NL2SQL_APP_AUTH_ENABLED=false）は他の API と同じく権限を判定しない。
        return lambda _permissions: True
    return principal.has_any_permission
