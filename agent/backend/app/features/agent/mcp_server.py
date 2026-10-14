"""業務 Agent を外部の MCP クライアントに公開する（`POST /api/mcp`。#1874）。

外部の Agent（Claude Code・Codex など）が、業務 Agent を「専門家」として呼ぶためのチャットの
ツールを出す。RAG / NL2SQL と同じ共通の `pr_backend_core.mcp`（Streamable HTTP の JSON 応答）を
使い、認証は共通の API キー（`Authorization: Bearer prak_…`。使える製品に `agent`）。

- 利用者はキーを発行した利用者で、画面と同じ権限（capability）と対象範囲（使える業務 Agent）で
  判定する。キーに業務 Agent の範囲（`resource_scopes["agent"]`）があれば、さらにその範囲だけ
  （範囲の外は 403）。
- ツール: `agent_list`（使える業務 Agent）・`agent_chat`（質問。`thread_id` で会話を続ける）・
  `agent_get_run`（状態・回答・根拠・回答前の確認）・`agent_answer_confirmation`（回答前の確認に
  答える / 確認せずに回答）。回答を待つのは `wait_seconds` まで。終わらなければ途中の状態を返し、
  `agent_get_run` で続きを読む。
- MCP から作った Run は無人の実行と同じく、承認の要るツールを呼ばない（#1813 と同じ扱い。
  `runtime.is_mcp_run`）。承認する人が画面にいるとは限らないため。
- キーごとに直近 24 時間に作れる Run の数（`PLATFORM_API_KEY_RUNS_PER_DAY`）。超えたら 429 相当の
  ツールのエラー（`details.retry_after_seconds`）。1 分あたりの呼び出しの回数は共通の認証が数える。
- Run の metadata にキーの ID と名前を残し、実行履歴・監査に「MCP（キーの名前）」として出す。

Agent は MCP を呼ぶ側でもある（RAG / NL2SQL / 外部 MCP）。ここは呼ばれる側で、RAG は Agent を
呼ばない（向きは外部のクライアント → Agent → RAG / NL2SQL）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pr_backend_core.mcp import McpServer, McpTool, McpToolError, mcp_http_response
from pydantic import BaseModel, Field

from app.features.agent import router as agent_api
from app.features.agent.answer_validation import ANSWER_VALIDATION_KIND
from app.features.agent.confirmation import (
    MAX_ITEMS,
    ConfirmationAnswer,
    ConfirmationAnswerError,
    ConfirmationRequest,
)
from app.features.agent.rag_figures import RAG_EVIDENCE_KIND
from app.features.agent.runtime import (
    AGENT_UNPUBLISHED_MESSAGE,
    MCP_API_KEY_ID_KEY,
    MCP_API_KEY_NAME_KEY,
    MCP_APPROVAL_SKIPPED_CODE,
    MCP_RUN_SOURCE,
    RUN_SOURCE_KEY,
    AgentNotPublishedError,
    AgentProfile,
    ConfirmationClosedError,
    RunCreateRequest,
    RunEventType,
    RunState,
    RunStatus,
    ThreadNotFoundError,
    agent_unavailable_reason,
    run_answer_text,
    runtime_repository,
)
from app.features.agent.skills import skill_registry
from app.security.domain import Principal
from app.security.permissions import ADMIN, RUNS_OPERATE, RUNS_VIEW
from app.settings import get_settings

MCP_SERVER_NAME = "production-ready-agent"
# 既定の待ち時間は、MCP のクライアントのツールの待ち時間の既定（Claude Code は 60 秒）より短く
# する。終わらなければ途中の状態を返すので、クライアントの時間切れで結果を失わない。
CHAT_DEFAULT_WAIT_SECONDS = 50
GET_RUN_DEFAULT_WAIT_SECONDS = 30
MAX_WAIT_SECONDS = 300
QUESTION_MAX_CHARS = 8000
# 回答を待つ間隔（テストは短くする）。
wait_poll_seconds = 0.5

# 根拠の抜き出しの長さと件数（呼び出し側のモデルの文脈を膨らませない）。
_SOURCE_EXCERPT_CHARS = 400
_MAX_SOURCES = 10
# RAG の MCP 接続のツール（`rag__rag_search`・`rag__rag_retrieve_evidence` など）。
_RAG_TOOL_PREFIX = "rag__"

_READ = frozenset({RUNS_VIEW, RUNS_OPERATE, ADMIN})
_OPERATE = frozenset({RUNS_OPERATE, ADMIN})
# 待つのをやめる状態（終わった・人の答えを待つ）。
_SETTLED = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_CONFIRMATION,
    }
)
_RUN_LIMIT_WINDOW = timedelta(hours=24)

AGENT_NOT_FOUND_CODE = "AGENT_NOT_FOUND"
AGENT_OUT_OF_KEY_SCOPE_CODE = "AGENT_OUT_OF_KEY_SCOPE"
AGENT_NOT_AVAILABLE_CODE = "AGENT_NOT_AVAILABLE"
RUN_NOT_FOUND_CODE = "RUN_NOT_FOUND"
THREAD_NOT_FOUND_CODE = "THREAD_NOT_FOUND"
RUN_LIMIT_CODE = "API_KEY_RUN_LIMIT"
CONFIRMATION_NOT_FOUND_CODE = "CONFIRMATION_NOT_FOUND"
CONFIRMATION_CLOSED_CODE = "CONFIRMATION_CLOSED"
CONFIRMATION_ANSWER_INVALID_CODE = "CONFIRMATION_ANSWER_INVALID"

_OUT_OF_SCOPE_MESSAGE = (
    "この API キーではこの業務 Agent を使えません。キーの「使える業務 Agent」を確認してください。"
)

router = APIRouter(tags=["mcp"])


# ---- 入出力 -------------------------------------------------------------------------


class AgentListInput(BaseModel):
    pass


class SkillSummary(BaseModel):
    id: str
    name: str
    description: str = ""


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    # 業務 Agent が使うスキル（公開中の版）。何を調べられるかの目安。
    skills: list[SkillSummary] = Field(default_factory=list)


class AgentListOutput(BaseModel):
    agents: list[AgentSummary]


class AgentChatInput(BaseModel):
    agent_id: str = Field(
        min_length=1, max_length=200, description="業務 Agent の ID（agent_list の id）"
    )
    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS, description="質問")
    thread_id: str | None = Field(
        default=None,
        pattern=r"^thread_[0-9a-f]{32}$",
        description=(
            "続ける会話の ID（前の agent_chat / agent_get_run の thread_id）。"
            "省略すると新しい会話を始める。前の質問と回答を踏まえて答える。"
        ),
    )
    wait_seconds: int = Field(
        default=CHAT_DEFAULT_WAIT_SECONDS,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description=(
            "回答を待つ秒数。終わらなければ途中の状態と run_id を返すので、agent_get_run で読む。"
        ),
    )


class AgentGetRunInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=200, description="agent_chat の run_id")
    wait_seconds: int = Field(
        default=GET_RUN_DEFAULT_WAIT_SECONDS,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description="まだ終わっていなければ、終わるまで待つ秒数（0 は今の状態をすぐ返す）。",
    )


class ConfirmationAnswerInput(BaseModel):
    item_id: str = Field(min_length=1, max_length=128, description="確認事項の ID")
    option_ids: list[str] = Field(
        default_factory=list, max_length=8, description="選んだ選択肢の ID"
    )
    other_text: str = Field(
        default="", max_length=2000, description="選択肢に無いときの自由入力の答え"
    )


class AgentAnswerConfirmationInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=200, description="確認を出した Run の ID")
    confirmation_id: str = Field(
        min_length=1, max_length=200, description="確認の ID（confirmation.id）"
    )
    action: Literal["answer", "skip"] = Field(
        default="answer",
        description="answer は確認事項に答える。skip は確認せずに（前提を示して）回答させる。",
    )
    answers: list[ConfirmationAnswerInput] = Field(
        default_factory=list,
        max_length=MAX_ITEMS,
        description="answer のとき、すべての確認事項への答え（選択肢か自由入力）。",
    )
    wait_seconds: int = Field(
        default=CHAT_DEFAULT_WAIT_SECONDS,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description="答えた後の回答を待つ秒数。",
    )


class ConfirmationOptionOutput(BaseModel):
    id: str
    label: str
    description: str = ""


class ConfirmationItemOutput(BaseModel):
    id: str
    prompt: str
    reason: str = ""
    # single は 1 つ選ぶ、multiple は複数選べる。
    selection: str = "single"
    options: list[ConfirmationOptionOutput] = Field(default_factory=list)


class ConfirmationOutput(BaseModel):
    """回答前の確認（#1839）。agent_answer_confirmation で答える。"""

    id: str
    # agent（業務 Agent 自身）/ nl2sql / rag（調べた先が確認を求めた）。
    source: str
    items: list[ConfirmationItemOutput]
    round: int
    max_rounds: int
    # 確認せずに回答（action=skip）を選べるか。
    can_skip: bool


class SourceOutput(BaseModel):
    """回答の根拠（RAG の資料。回答に使ったもの）。"""

    title: str
    location: str = ""
    excerpt: str = ""


class RunOutput(BaseModel):
    run_id: str
    agent_id: str
    # 会話の ID。続きの質問は agent_chat の thread_id に渡す。
    thread_id: str | None = None
    # queued / running / waiting_confirmation / waiting_approval / completed / failed / cancelled
    status: str
    # 待ちをやめた理由に関わらず、Run がまだ進むか（agent_get_run で読み直す）。
    in_progress: bool
    answer: str | None = None
    # 回答前の確認（答えていないもの）。あれば agent_answer_confirmation で答える。
    confirmation: ConfirmationOutput | None = None
    sources: list[SourceOutput] = Field(default_factory=list)
    # 使ったツールの名前（Agent の中の名前。例: rag__rag_search）。
    tools_used: list[str] = Field(default_factory=list)
    # 承認が要るため使わなかったツール（MCP から作った Run は承認の要るツールを呼ばない）。
    skipped_approval_tools: list[str] = Field(default_factory=list)
    pending_approvals: int = 0
    # 次にすること（日本語）。
    message: str


# ---- 判定 ---------------------------------------------------------------------------


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise McpToolError("MCP_UNAUTHENTICATED", "認証できません。", status=401)
    return principal


def _key_scope(request: Request) -> frozenset[str] | None:
    """API キーの業務 Agent の範囲（キーで来ていない・範囲なしは None）。"""
    record = getattr(request.state, "api_key", None)
    scope = getattr(record, "scope", None)
    return scope("agent") if callable(scope) else None


def _key_metadata(request: Request) -> dict[str, str]:
    """Run に残す出所（MCP とキーの ID・名前。監査の「MCP（キーの名前）」）。"""
    record = getattr(request.state, "api_key", None)
    metadata = {RUN_SOURCE_KEY: MCP_RUN_SOURCE}
    if record is not None:
        metadata[MCP_API_KEY_ID_KEY] = str(record.key_id)
        metadata[MCP_API_KEY_NAME_KEY] = str(record.name)
    return metadata


def _visible(request: Request, agent_id: str) -> bool:
    """利用者の対象範囲（ロール）とキーの範囲の両方に入る業務 Agent か。"""
    scope = _key_scope(request)
    if scope is not None and agent_id not in scope:
        return False
    return agent_api._agent_allowed(request, agent_id)


def _require_agent_in_scope(request: Request, agent_id: str) -> None:
    scope = _key_scope(request)
    if scope is not None and agent_id not in scope:
        raise McpToolError(AGENT_OUT_OF_KEY_SCOPE_CODE, _OUT_OF_SCOPE_MESSAGE, status=403)
    if not agent_api._agent_allowed(request, agent_id):
        raise McpToolError(
            AGENT_NOT_FOUND_CODE, "業務 Agent が見つからないか、使う権限がありません。", status=403
        )


def _find_agent(agent_id: str) -> AgentProfile | None:
    try:
        return agent_api._control_plane_agent(agent_id)
    except KeyError:
        return None


def _require_available_agent(request: Request, agent_id: str) -> AgentProfile:
    _require_agent_in_scope(request, agent_id)
    agent = _find_agent(agent_id)
    if agent is None:
        raise McpToolError(AGENT_NOT_FOUND_CODE, "業務 Agent が見つかりません。", status=404)
    reason = agent_unavailable_reason(agent)
    if reason is not None:
        raise McpToolError(AGENT_NOT_AVAILABLE_CODE, reason, status=409)
    return agent


def _own_run(request: Request, run_id: str) -> RunState:
    """呼び出し元が作った Run（ほかの利用者の Run は「無い」として扱い、存在を漏らさない）。"""
    caller = _principal(request)
    try:
        run = runtime_repository.get_run(run_id)
    except KeyError:
        run = None
    if run is None or run.created_by_user_uuid != caller.user_uuid:
        raise McpToolError(RUN_NOT_FOUND_CODE, "Run が見つかりません。", status=404)
    _require_agent_in_scope(request, run.agent_id)
    return run


def _require_run_budget(request: Request) -> None:
    """キーごとの直近 24 時間の Run の数（`PLATFORM_API_KEY_RUNS_PER_DAY`。#1874）。"""
    record = getattr(request.state, "api_key", None)
    limit = int(get_settings().app_api_key_runs_per_day)
    if record is None or limit <= 0:
        return
    now = datetime.now(UTC)
    since = now - _RUN_LIMIT_WINDOW
    created = sorted(
        run.created_at
        for run in runtime_repository.list_runs()
        if run.metadata.get(MCP_API_KEY_ID_KEY) == record.key_id and run.created_at >= since
    )
    if len(created) < limit:
        return
    retry_after = max(1, int((created[-limit] + _RUN_LIMIT_WINDOW - now).total_seconds()) + 1)
    raise McpToolError(
        RUN_LIMIT_CODE,
        f"この API キーで作れる実行（Run）は直近 24 時間に {limit} 件までです。"
        f"{retry_after} 秒ほど後に呼び直してください。",
        status=429,
        details={"retry_after_seconds": retry_after, "limit": limit},
    )


# ---- 出力 ---------------------------------------------------------------------------


def _pending_confirmation(run: RunState) -> ConfirmationRequest | None:
    return next((item for item in run.confirmations if item.status == "pending"), None)


def _confirmation_output(item: ConfirmationRequest) -> ConfirmationOutput:
    return ConfirmationOutput(
        id=item.id,
        source=item.source,
        items=[
            ConfirmationItemOutput(
                id=entry.id,
                prompt=entry.prompt,
                reason=entry.reason,
                selection=entry.selection,
                options=[
                    ConfirmationOptionOutput(
                        id=option.id, label=option.label, description=option.description
                    )
                    for option in entry.options
                ],
            )
            for entry in item.items
        ],
        round=item.round,
        max_rounds=item.max_rounds,
        can_skip=item.can_skip,
    )


def _location(locator: object) -> str:
    if not isinstance(locator, Mapping):
        return ""
    parts: list[str] = []
    sheet = locator.get("sheet_name")
    if isinstance(sheet, str) and sheet.strip():
        parts.append(sheet.strip())
    start, end = locator.get("page_start"), locator.get("page_end")
    if isinstance(start, int) and start > 0:
        parts.append(
            f"p.{start}" if not isinstance(end, int) or end == start else f"p.{start}-{end}"
        )
    sections = locator.get("section_path")
    if isinstance(sections, list):
        names = [item for item in sections if isinstance(item, str) and item.strip()]
        if names:
            parts.append(" > ".join(names))
    return " · ".join(parts)


def _evidence_items(run: RunState) -> list[Mapping[str, Any]]:
    """Run の RAG の根拠（`rag_evidence` の成果物と、RAG のツールの結果の `evidence`）。"""
    items: list[Mapping[str, Any]] = []
    contents: list[object] = [
        artifact.content for artifact in run.artifacts if artifact.kind == RAG_EVIDENCE_KIND
    ]
    contents.extend(
        step.tool_result.output
        for step in run.steps
        if step.tool_result is not None
        and step.tool_call is not None
        and step.tool_call.name.startswith(_RAG_TOOL_PREFIX)
    )
    for content in contents:
        evidence = content.get("evidence") if isinstance(content, Mapping) else None
        if isinstance(evidence, list):
            items.extend(item for item in evidence if isinstance(item, Mapping))
    return items


def _validated_chunk_ids(run: RunState) -> set[str]:
    """最終の検証（`answer_validation`）が回答の根拠として確かめた chunk。"""
    ids: set[str] = set()
    for artifact in run.artifacts:
        if artifact.kind != ANSWER_VALIDATION_KIND or not isinstance(artifact.content, dict):
            continue
        evidence = artifact.content.get("evidence")
        if isinstance(evidence, list):
            ids.update(
                str(item["chunk_id"])
                for item in evidence
                if isinstance(item, Mapping) and item.get("chunk_id")
            )
    return ids


def _sources(run: RunState) -> list[SourceOutput]:
    """回答の根拠（RAG の資料）。回答に使った根拠（`used_in_answer`・最終の検証で確かめた chunk）を
    選び、印が 1 件も無ければ全件。画面のチャットの出典（`answerSources`）と同じ考え方。
    """
    items = _evidence_items(run)
    validated = _validated_chunk_ids(run)
    used = [
        item
        for item in items
        if item.get("used_in_answer") is True or str(item.get("chunk_id") or "") in validated
    ]
    sources: list[SourceOutput] = []
    seen: set[str] = set()
    for item in used or items:
        title = str(item.get("file_name") or item.get("document_id") or "名前の無い資料")
        key = str(
            item.get("evidence_id") or item.get("chunk_id") or f"{title}:{item.get('locator')}"
        )
        if key in seen:
            continue
        seen.add(key)
        excerpt = item.get("excerpt")
        sources.append(
            SourceOutput(
                title=title,
                location=_location(item.get("locator")),
                excerpt=excerpt[:_SOURCE_EXCERPT_CHARS] if isinstance(excerpt, str) else "",
            )
        )
        if len(sources) >= _MAX_SOURCES:
            break
    return sources


def _failure_message(run: RunState) -> str:
    for event in reversed(run.events):
        if event.type == RunEventType.RUNTIME_FAILED and event.message:
            return event.message
    return "業務 Agent の実行に失敗しました。"


def run_output(run: RunState) -> RunOutput:
    confirmation = _pending_confirmation(run)
    tools_used: list[str] = []
    skipped: list[str] = []
    for step in run.steps:
        if step.tool_call is None:
            continue
        name = step.tool_call.name
        result = step.tool_result
        if result is not None and result.error_code == MCP_APPROVAL_SKIPPED_CODE:
            if name not in skipped:
                skipped.append(name)
        elif name not in tools_used:
            tools_used.append(name)
    pending_approvals = sum(1 for approval in run.approvals if approval.status == "pending")
    status = run.status
    in_progress = status in {RunStatus.QUEUED, RunStatus.RUNNING}
    if confirmation is not None:
        message = (
            "回答の前に確認が必要です。confirmation の確認事項に agent_answer_confirmation で"
            "答えてください（利用者に聞くか、分かっていれば答える。can_skip なら action=skip で"
            "確認せずに回答させられます）。"
        )
    elif status == RunStatus.COMPLETED:
        message = "回答しました。続きの質問は thread_id を agent_chat に渡してください。"
    elif status == RunStatus.FAILED:
        message = _failure_message(run)
    elif status == RunStatus.CANCELLED:
        message = "実行は取り消されました。"
    elif status == RunStatus.WAITING_APPROVAL:
        message = (
            "承認が必要な操作があります。Agent の画面の「承認」で判断されると続きを実行します。"
            "agent_get_run で後から読めます。"
        )
    else:
        message = "実行しています。agent_get_run（wait_seconds）で続きを読んでください。"
    return RunOutput(
        run_id=run.id,
        agent_id=run.agent_id,
        thread_id=run.thread_id,
        status=str(status.value),
        in_progress=in_progress,
        answer=run_answer_text(run),
        confirmation=_confirmation_output(confirmation) if confirmation is not None else None,
        sources=_sources(run),
        tools_used=tools_used,
        skipped_approval_tools=skipped,
        pending_approvals=pending_approvals,
        message=message,
    )


async def _wait(run_id: str, wait_seconds: int) -> RunState:
    deadline = time.monotonic() + wait_seconds
    while True:
        run = runtime_repository.get_run(run_id)
        if run.status in _SETTLED or time.monotonic() >= deadline:
            return run
        await asyncio.sleep(wait_poll_seconds)


# ---- サーバー -------------------------------------------------------------------------


def build_agent_mcp_server(request: Request) -> McpServer:
    def list_agents(_arguments: AgentListInput) -> AgentListOutput:
        _principal(request)
        agents: list[AgentSummary] = []
        for agent in runtime_repository.list_agents():
            # 公開した版の無い・無効な業務 Agent は利用者の Run で使えないので出さない。
            if agent_unavailable_reason(agent) is not None or not _visible(request, agent.id):
                continue
            published = agent.published()
            skill_ids = published.skill_ids if published is not None else agent.skill_ids
            skills = [
                SkillSummary(id=skill.id, name=skill.name, description=skill.description)
                for skill in (skill_registry.get(skill_id) for skill_id in skill_ids)
                if skill is not None
            ]
            agents.append(
                AgentSummary(
                    id=agent.id,
                    name=published.name if published is not None else agent.name,
                    description=(
                        published.description if published is not None else agent.description
                    ),
                    skills=skills,
                )
            )
        return AgentListOutput(agents=agents)

    async def chat(arguments: AgentChatInput) -> RunOutput:
        caller = _principal(request)
        agent = _require_available_agent(request, arguments.agent_id)
        _require_run_budget(request)
        try:
            run = runtime_repository.create_builtin_run(
                RunCreateRequest(
                    goal=arguments.question,
                    agent_id=agent.id,
                    thread_id=arguments.thread_id,
                ),
                created_by_user_uuid=caller.user_uuid,
                extra_metadata=_key_metadata(request),
            )
        except ThreadNotFoundError as exc:
            # 他人・別の業務 Agent の会話も「無い」として扱う（存在を漏らさない）。
            raise McpToolError(
                THREAD_NOT_FOUND_CODE, "続ける会話が見つかりません。", status=404
            ) from exc
        except AgentNotPublishedError as exc:
            raise McpToolError(
                AGENT_NOT_AVAILABLE_CODE, AGENT_UNPUBLISHED_MESSAGE, status=409
            ) from exc
        agent_api._schedule_builtin_run(run)
        return run_output(await _wait(run.id, arguments.wait_seconds))

    async def get_run(arguments: AgentGetRunInput) -> RunOutput:
        run = _own_run(request, arguments.run_id)
        return run_output(await _wait(run.id, arguments.wait_seconds))

    async def answer_confirmation(arguments: AgentAnswerConfirmationInput) -> RunOutput:
        caller = _principal(request)
        run = _own_run(request, arguments.run_id)
        _require_available_agent(request, run.agent_id)
        _require_run_budget(request)
        try:
            outcome = runtime_repository.decide_confirmation(
                run.id,
                arguments.confirmation_id,
                action=arguments.action,
                answers=[
                    ConfirmationAnswer.model_validate(item.model_dump())
                    for item in arguments.answers
                ],
                decided_by=caller.login_user_id,
            )
        except KeyError as exc:
            raise McpToolError(
                CONFIRMATION_NOT_FOUND_CODE, "回答前の確認が見つかりません。", status=404
            ) from exc
        except ConfirmationClosedError as exc:
            raise McpToolError(
                CONFIRMATION_CLOSED_CODE,
                "この確認は答え済みか、会話が先へ進んだため答えられません。"
                "agent_get_run で今の状態を読んでください。",
                status=409,
            ) from exc
        except ConfirmationAnswerError as exc:
            raise McpToolError(CONFIRMATION_ANSWER_INVALID_CODE, str(exc), status=422) from exc
        except AgentNotPublishedError as exc:
            raise McpToolError(
                AGENT_NOT_AVAILABLE_CODE, AGENT_UNPUBLISHED_MESSAGE, status=409
            ) from exc
        if outcome.next_run is not None:
            agent_api._schedule_builtin_run(outcome.next_run)
        agent_api._schedule_builtin_run(outcome.run)
        target = outcome.next_run or outcome.run
        return run_output(await _wait(target.id, arguments.wait_seconds))

    return McpServer(
        name=MCP_SERVER_NAME,
        version="1",
        instructions=(
            "業務 Agent（社内の資料・業務データを調べて答える専門家）に質問する。"
            "agent_list で使える業務 Agent を確かめ、agent_chat で質問する。"
            "in_progress なら agent_get_run で続きを読む。confirmation が返ったら、"
            "確認事項に agent_answer_confirmation で答える。続きの質問は返った thread_id を"
            "agent_chat に渡す。回答の根拠は sources にある。"
        ),
        tools=[
            McpTool(
                name="agent_list",
                description=(
                    "使える業務 Agent（ID・名前・説明・使うスキル）の一覧を返す。"
                    "質問の内容に合う業務 Agent を選ぶのに使う。"
                ),
                input_model=AgentListInput,
                handler=list_agents,
                permissions=(_READ,),
                output_model=AgentListOutput,
            ),
            McpTool(
                name="agent_chat",
                description=(
                    "業務 Agent に質問し、回答（根拠つき）を返す。業務 Agent は社内の資料（RAG）・"
                    "業務データ（NL2SQL）などを調べて答える。thread_id を渡すと同じ会話の続きとして"
                    "答える。回答の前に確認が要るときは confirmation を返す。wait_seconds までに"
                    "終わらなければ in_progress=true と run_id を返す（agent_get_run で読む）。"
                    "承認の要る操作は行わない。"
                ),
                input_model=AgentChatInput,
                handler=chat,
                permissions=(_OPERATE,),
                read_only=False,
                output_model=RunOutput,
            ),
            McpTool(
                name="agent_get_run",
                description=(
                    "agent_chat の run_id で、状態・回答・根拠・回答前の確認を返す。"
                    "まだ終わっていなければ wait_seconds まで待つ。"
                ),
                input_model=AgentGetRunInput,
                handler=get_run,
                permissions=(_READ,),
                output_model=RunOutput,
            ),
            McpTool(
                name="agent_answer_confirmation",
                description=(
                    "回答前の確認（agent_chat / agent_get_run の confirmation）に答える。"
                    "action=answer はすべての確認事項に、選択肢の ID（option_ids）か自由入力"
                    "（other_text）で答える。action=skip は確認せずに前提を示して回答させる"
                    "（can_skip のときだけ）。答えた後の回答を返す。"
                ),
                input_model=AgentAnswerConfirmationInput,
                handler=answer_confirmation,
                permissions=(_OPERATE,),
                read_only=False,
                output_model=RunOutput,
            ),
        ],
    )


def has_any_permission_for(request: Request) -> Callable[[frozenset[str]], bool]:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        return lambda _permissions: False
    return principal.has_any_permission


@router.post("/mcp", response_class=Response)
async def mcp_endpoint(request: Request) -> Response:
    """MCP の JSON-RPC（initialize / ping / tools/list / tools/call）を処理する。

    認証は共通の API キー（`authorize_api_request` の `api_key_paths`）。ツールごとの権限は
    ツールが利用者の capability で判定する。
    """
    if not isinstance(getattr(request.state, "principal", None), Principal):
        raise HTTPException(
            status_code=401, detail="API キーを Authorization header で指定してください。"
        )
    return await mcp_http_response(
        request,
        build_agent_mcp_server(request),
        has_any_permission=has_any_permission_for(request),
    )
