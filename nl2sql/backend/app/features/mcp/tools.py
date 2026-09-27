"""NL2SQL が MCP で公開するツール（#231）。

Agent は Run の利用者のサービストークンで `POST /api/mcp` を呼ぶ。認証と利用者の特定は
`authorize_api_request`（`service_token_paths={"/mcp"}`）が行い、DeepSec の actor も同じ経路で
入る。ここでは画面の route と同じ service 層・業務プロファイルの範囲・ジョブの所有者の判定を使う。

- `nl2sql_list_profiles`: `GET /nl2sql/profiles/search` と同じ範囲の業務プロファイル一覧
- `nl2sql_recommend_profile`: `POST /nl2sql/recommend-profile`（しきい値未満は推薦なし）
- `nl2sql_query`: `POST /nl2sql/jobs` でジョブを作り、`wait_seconds` まで結果を待つ
- `nl2sql_get_job`: `GET /nl2sql/jobs/{job_id}`（本人のジョブだけ）
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import HTTPException, Request
from pr_backend_core.mcp import McpServer, McpTool, McpToolError
from pydantic import BaseModel, ConfigDict, Field

from app.api.concurrency import run_sync_io
from app.features.nl2sql.models import (
    JobCreateRequest,
    JobData,
    JobStatus,
    ProfileRecommendationRequest,
)
from app.features.nl2sql.profile_access import (
    allowed_profile_ids_for_request,
    assert_profile_access,
    principal_from_request,
    profile_access_denied,
)
from app.features.nl2sql.service import (
    SCHEMA_CATALOG_EMPTY_ERROR_CODE,
    Nl2SqlPersistenceUnavailable,
    Nl2SqlRepositoryOperationFailed,
    SchemaCatalogEmptyError,
    nl2sql_service,
)
from app.security.permissions import (
    PROFILE_READ_PERMISSION,
    QUERY_GENERATE_PERMISSION,
    SQL_EXECUTE_PERMISSION,
)
from app.settings import get_settings

SERVER_NAME = "production-ready-nl2sql"
DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 1000
# gunicorn の `--timeout 60`（Dockerfile）を超えないよう、待ちの上限は 45 秒にする。
MAX_WAIT_SECONDS = 45
JOB_POLL_INTERVAL_SECONDS = 0.5
_TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.ERROR})
_JOB_NOT_FOUND_MESSAGE = "指定されたジョブが見つかりません。"

_INSTRUCTIONS = (
    "Production Ready NL2SQL のツールです。利用者が使える業務プロファイルの範囲で、"
    "自然言語の質問から SQL を生成・実行します。業務プロファイルが分からないときは "
    "nl2sql_list_profiles / nl2sql_recommend_profile で選び、nl2sql_query に渡してください。"
    "nl2sql_query が pending / running を返したら、"
    "job_id を nl2sql_get_job に渡して続きを取得します。"
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListProfilesInput(_Input):
    query: str | None = Field(
        default=None, max_length=200, description="業務プロファイル名・説明の絞り込み語"
    )
    limit: int = Field(default=20, ge=1, le=100, description="返す件数の上限")


class RecommendProfileInput(_Input):
    question: str = Field(min_length=1, max_length=4000, description="利用者の質問（日本語）")


class QueryInput(_Input):
    question: str = Field(min_length=1, max_length=4000, description="利用者の質問（日本語）")
    profile_id: str | None = Field(
        default=None,
        max_length=128,
        description="使う業務プロファイルの ID。省略時は既定の業務プロファイル",
    )
    row_limit: int = Field(
        default=DEFAULT_ROW_LIMIT, ge=1, le=MAX_ROW_LIMIT, description="取得する行数の上限"
    )
    wait_seconds: int = Field(
        default=40,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description="結果を待つ秒数。過ぎたら pending / running のまま job_id を返す",
    )


class GetJobInput(_Input):
    job_id: str = Field(min_length=1, max_length=128)
    wait_seconds: int = Field(default=0, ge=0, le=MAX_WAIT_SECONDS, description="完了を待つ秒数")


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


class Nl2SqlJobResult(BaseModel):
    """契約の `Nl2SqlJobResult`（Agent の `external_nl2sql_*` がそのまま通す）。"""

    job_id: str
    status: JobStatus
    profile_id: str | None = None
    generated_sql: str | None = None
    executable_sql: str | None = None
    explanation: str | None = None
    is_safe: bool | None = None
    safety_issues: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    returned_count: int = 0
    total: int | None = None
    has_more: bool = False
    truncated: bool = False
    history_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


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

    return wrapped


def _actor(request: Request) -> tuple[str, bool]:
    principal = principal_from_request(request)
    if principal is None:
        # 認証無効（NL2SQL_APP_AUTH_ENABLED=false）は画面の route と同じく actor 制約を外す。
        return "", False
    return principal.user_uuid, principal.is_system_admin


def _job_result(job: JobData) -> Nl2SqlJobResult:
    result = job.result
    output = Nl2SqlJobResult(
        job_id=job.job_id,
        status=job.status,
        profile_id=job.profile_id or None,
        error_code=job.error_code,
        error_message=job.error_message,
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
    output.columns = list(result.results.columns)
    output.rows = list(result.results.rows)
    output.returned_count = result.results.returned_count or len(result.results.rows)
    output.total = result.results.total
    output.has_more = result.results.has_more
    output.truncated = result.results.truncated
    output.history_id = result.history_id or None
    return output


def build_mcp_server(request: Request) -> McpServer:
    """リクエストの利用者で判定するツールを持つ MCP サーバーを作る。"""

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
                allowed_profile_ids=allowed_profile_ids_for_request(request, nl2sql_service),
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
            allowed = allowed_profile_ids_for_request(request, nl2sql_service)
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

    @_translate_domain_errors
    async def query(arguments: QueryInput) -> Nl2SqlJobResult:
        assert_profile_access(request, arguments.profile_id, default_profile=True)
        actor_user_uuid, actor_is_system_admin = _actor(request)
        # 画面の既定値（engine / use_ontology_context 等は JobCreateRequest の既定）で作る。
        # row_limit は必ず渡す（None だと全件を取得する）。
        job_request = JobCreateRequest(
            question=arguments.question,
            profile_id=arguments.profile_id or None,
            row_limit=arguments.row_limit,
        )
        try:
            created = await run_sync_io(
                nl2sql_service.start_job,
                job_request,
                actor_user_uuid=actor_user_uuid,
                actor_is_system_admin=actor_is_system_admin,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await wait_for_job(created.job_id, arguments.wait_seconds)

    @_translate_domain_errors
    async def get_job(arguments: GetJobInput) -> Nl2SqlJobResult:
        return await wait_for_job(arguments.job_id, arguments.wait_seconds)

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
            description="質問に合う業務プロファイルを推薦します。推薦できないときは null です。",
            input_model=RecommendProfileInput,
            handler=recommend_profile,
            output_model=RecommendProfileOutput,
            permissions=(frozenset({QUERY_GENERATE_PERMISSION}),),
        ),
        McpTool(
            name="nl2sql_query",
            description=(
                "質問から SELECT SQL を生成して実行し、結果の行を返します。"
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
            description="nl2sql_query で作ったジョブの状態と結果を返します（本人のジョブだけ）。",
            input_model=GetJobInput,
            handler=get_job,
            output_model=Nl2SqlJobResult,
            permissions=(frozenset({QUERY_GENERATE_PERMISSION}),),
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
