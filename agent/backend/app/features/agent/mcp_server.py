"""業務 Agent を MCP（`POST /api/mcp`）で公開する（#778）。

RAG / NL2SQL と同じ共通の `pr_backend_core.mcp` の JSON-RPC の実装を使う。呼び出し元の利用者
（サービストークンの `sub`、または API キーを作った利用者）の権限と、使える業務 Agent で判定する。

- `agent_list_agents`: 呼び出し元が使える業務 Agent。
- `agent_ask`: 業務 Agent に質問する。呼び出し元の Run を作り、`wait_seconds` まで回答を待つ。
  終わらなければ `run_id` と状態を返す（`agent_get_run` で後から読む）。承認が要るときは
  承認待ちを返す（承認は Agent の画面で行う）。
- `agent_get_run`: 呼び出し元が作った Run の状態と回答。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pr_backend_core.mcp import McpServer, McpTool, McpToolError
from pydantic import BaseModel, Field

from app.features.agent import builtin_runtime
from app.features.agent.runtime import (
    BUILTIN_RUNTIME_ID,
    RunCreateRequest,
    RunEventType,
    RunState,
    RunStatus,
    runtime_repository,
)
from app.security.domain import Principal
from app.security.permissions import ADMIN, RUNS_OPERATE, RUNS_VIEW
from app.settings import get_settings

MCP_SERVER_NAME = "production-ready-agent"
ASK_DEFAULT_WAIT_SECONDS = 60
ASK_MAX_WAIT_SECONDS = 120
# 回答を待つ間隔。テストは短くする。
wait_poll_seconds = 0.5

_READ = frozenset({RUNS_VIEW, RUNS_OPERATE, ADMIN})
_OPERATE = frozenset({RUNS_OPERATE, ADMIN})
_SETTLED = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.WAITING_APPROVAL,
}
# 実行中の Run の task（GC で消えないよう参照を持つ）。
_tasks: set[asyncio.Task[None]] = set()


class ListAgentsInput(BaseModel):
    pass


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str


class ListAgentsOutput(BaseModel):
    agents: list[AgentSummary]


class AskInput(BaseModel):
    agent_id: str = Field(min_length=1, max_length=200, description="業務 Agent の ID")
    question: str = Field(min_length=1, max_length=4000, description="質問（日本語可）")
    wait_seconds: int = Field(
        default=ASK_DEFAULT_WAIT_SECONDS,
        ge=0,
        le=ASK_MAX_WAIT_SECONDS,
        description="回答を待つ秒数。終わらなければ run_id を返すので agent_get_run で読む。",
    )


class GetRunInput(BaseModel):
    run_id: str = Field(min_length=1, max_length=200)


class RunOutput(BaseModel):
    run_id: str
    agent_id: str
    # queued / running / waiting_approval / completed / failed / cancelled
    status: str
    answer: str | None = None
    pending_approvals: int = 0
    message: str


def build_agent_mcp_server(principal: Principal | None) -> McpServer:
    def require_principal() -> Principal:
        if principal is None:
            raise McpToolError("MCP_UNAUTHENTICATED", "認証できません。", status=401)
        return principal

    def list_agents(_arguments: ListAgentsInput) -> ListAgentsOutput:
        caller = require_principal()
        agents = [
            AgentSummary(id=agent.id, name=agent.name, description=agent.description or "")
            for agent in runtime_repository.list_agents()
            if agent.enabled and not agent.migration_required and caller.can_use_agent(agent.id)
        ]
        return ListAgentsOutput(agents=agents)

    async def ask(arguments: AskInput) -> RunOutput:
        caller = require_principal()
        agent = next(
            (item for item in runtime_repository.list_agents() if item.id == arguments.agent_id),
            None,
        )
        if agent is None or not caller.can_use_agent(agent.id):
            raise McpToolError(
                "AGENT_NOT_FOUND", "業務 Agent が見つからないか、使う権限がありません。", status=404
            )
        if not agent.enabled or agent.migration_required:
            raise McpToolError(
                "AGENT_NOT_AVAILABLE", "この業務 Agent は実行できない状態です。", status=409
            )
        run = runtime_repository.create_builtin_run(
            RunCreateRequest(
                goal=arguments.question,
                agent_id=agent.id,
                metadata={"source": "mcp", "mcp_session": caller.session_id},
            ),
            created_by_user_uuid=caller.user_uuid,
        )
        _schedule(run)
        settled = await _wait(run.id, arguments.wait_seconds)
        return _run_output(settled)

    def get_run(arguments: GetRunInput) -> RunOutput:
        caller = require_principal()
        try:
            run = runtime_repository.get_run(arguments.run_id)
        except KeyError:
            run = None
        # ほかの利用者の Run は「無い」として扱う（存在を漏らさない）。
        if run is None or run.created_by_user_uuid != caller.user_uuid:
            raise McpToolError("RUN_NOT_FOUND", "Run が見つかりません。", status=404)
        return _run_output(run)

    return McpServer(
        name=MCP_SERVER_NAME,
        version="1",
        instructions=(
            "業務 Agent に質問するときは agent_list_agents で ID を確かめ、agent_ask を呼ぶ。"
            "回答が間に合わないときは返った run_id で agent_get_run を呼ぶ。"
        ),
        tools=[
            McpTool(
                name="agent_list_agents",
                description="使える業務 Agent（ID・名前・説明）の一覧を返す。",
                input_model=ListAgentsInput,
                handler=list_agents,
                permissions=(_READ,),
                output_model=ListAgentsOutput,
            ),
            McpTool(
                name="agent_ask",
                description=(
                    "業務 Agent に質問し、回答を返す。業務 Agent は社内のデータ"
                    "（RAG・NL2SQL など）を使って答える。承認が要る操作は Agent の画面で"
                    "承認されるまで止まる。"
                ),
                input_model=AskInput,
                handler=ask,
                permissions=(_OPERATE,),
                read_only=False,
                output_model=RunOutput,
            ),
            McpTool(
                name="agent_get_run",
                description="agent_ask の run_id で、Run の状態と回答を返す。",
                input_model=GetRunInput,
                handler=get_run,
                permissions=(_READ,),
                output_model=RunOutput,
            ),
        ],
    )


def _schedule(run: RunState) -> None:
    """in-process のモードではこのプロセスで実行する（dispatcher のモードは別プロセス）。"""
    if run.runtime_id != BUILTIN_RUNTIME_ID or run.status != RunStatus.QUEUED:
        return
    if get_settings().agent_runtime_dispatch_mode.strip().lower() != "in_process":
        return
    task = asyncio.get_running_loop().create_task(builtin_runtime.execute_run(run.id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _wait(run_id: str, wait_seconds: int) -> RunState:
    deadline = time.monotonic() + wait_seconds
    while True:
        run = runtime_repository.get_run(run_id)
        if run.status in _SETTLED or time.monotonic() >= deadline:
            return run
        await asyncio.sleep(wait_poll_seconds)


def _answer(run: RunState) -> str | None:
    for artifact in reversed(run.artifacts):
        if artifact.kind == "answer" and isinstance(artifact.content, dict):
            text = artifact.content.get("text")
            if isinstance(text, str):
                return text
    return None


def _failure(run: RunState) -> str:
    for event in reversed(run.events):
        if event.type == RunEventType.RUNTIME_FAILED and event.message:
            return event.message
    return "業務 Agent の実行に失敗しました。"


def _run_output(run: RunState) -> RunOutput:
    pending = sum(1 for approval in run.approvals if approval.status == "pending")
    messages: dict[RunStatus, str] = {
        RunStatus.COMPLETED: "回答しました。",
        RunStatus.WAITING_APPROVAL: (
            "承認が必要な操作があります。Agent の画面で承認されると続きを実行します。"
            "agent_get_run で後から回答を読めます。"
        ),
        RunStatus.CANCELLED: "Run は取り消されました。",
        RunStatus.QUEUED: "実行を待っています。agent_get_run で後から回答を読めます。",
        RunStatus.RUNNING: "実行しています。agent_get_run で後から回答を読めます。",
    }
    status: Any = run.status
    return RunOutput(
        run_id=run.id,
        agent_id=run.agent_id,
        status=str(status.value if hasattr(status, "value") else status),
        answer=_answer(run),
        pending_approvals=pending,
        message=_failure(run) if run.status == RunStatus.FAILED else messages.get(run.status, ""),
    )
