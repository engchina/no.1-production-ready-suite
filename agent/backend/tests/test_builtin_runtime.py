"""組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI。#754）の決定論テスト。

モデルは SDK の `ScriptedModel`（台本どおりに応答する）に差し替え、OCI へは接続しない。
ツールはテスト用に登録し、ポリシー・承認・step・成果物・再開を確かめる。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from agents import FunctionTool, Usage
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from mcp_support import fake_product_mcp
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.config import runtime_config_store
from app.features.agent.runtime import (
    BUILTIN_RUNTIME_ID,
    AgentProfile,
    AgentProfilePatch,
    ApprovalDecisionRequest,
    RunCreateRequest,
    RunStatus,
    ThreadNotFoundError,
    builtin_resume_pending,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.tools import (
    ToolDefinition,
    ToolInvocationContext,
    ToolPermissionLevel,
    tool_registry,
)

LOOKUP = "test754_lookup"
WRITE = "test754_write"
SKILL_ID = "test754-skill"
AGENT_ID = "test754-agent"
USER_UUID = "11111111-2222-3333-4444-555555555555"


class _Calls:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any], ToolInvocationContext]] = []

    def handler(self, name: str) -> Any:
        def handle(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
            self.items.append((name, dict(arguments), context))
            return {"name": name, "rows": 3}

        return handle


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }


@pytest.fixture
def calls() -> Iterator[_Calls]:
    recorded = _Calls()
    for name, level, side_effects in (
        (LOOKUP, ToolPermissionLevel.READ, False),
        (WRITE, ToolPermissionLevel.WRITE, True),
    ):
        tool_registry.register(
            ToolDefinition(
                name=name,
                description=f"{name} のテスト用ツール",
                input_schema=_schema(),
                output_schema={"type": "object"},
                permission_level=level,
                side_effects=side_effects,
            ),
            recorded.handler(name),
        )
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="テストの Skill",
            instructions="売上は test754_lookup で調べる。",
            mcp_requirements=[
                SkillMcpRequirement(server_id="control-plane", tool_names=[LOOKUP, WRITE])
            ],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(
            id=AGENT_ID,
            name="テストの業務 Agent",
            instructions="経理の質問に答える。",
            skill_ids=[SKILL_ID],
        )
    )
    try:
        yield recorded
    finally:
        # このテストの Run も消す（削除した Agent を参照する Run が残ると、同じ worker の後の
        # snapshot の検証が失敗する）。
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                run = repository._runs.pop(run_id)
                for approval in run.approvals:
                    repository._approvals.pop(approval.id, None)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)
        for name in (LOOKUP, WRITE):
            tool_registry._definitions.pop(name, None)  # noqa: SLF001 - テスト用の登録を戻す
            tool_registry._handlers.pop(name, None)  # noqa: SLF001


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _create_run(goal: str = "今月の売上を教えて") -> str:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )
    assert run.runtime_id == BUILTIN_RUNTIME_ID
    assert run.status == RunStatus.QUEUED
    return run.id


def test_instructions_and_tools_come_from_agent_and_skills(calls: _Calls) -> None:
    del calls
    instructions = builtin_runtime.compose_instructions("経理の質問に答える。", [SKILL_ID])
    assert "経理の質問に答える。" in instructions
    assert "# Skill: テストの Skill" in instructions
    assert builtin_runtime.agent_tool_names([SKILL_ID]) == [LOOKUP, WRITE]
    tools = {
        tool.name: tool for tool in builtin_runtime.build_function_tools("run_x", [LOOKUP, WRITE])
    }
    # 読み取りのツールは承認なし、書き込み（副作用あり）のツールは既定の policy で承認が必要。
    assert tools[LOOKUP].needs_approval is False
    assert tools[WRITE].needs_approval is True
    assert tools[LOOKUP].strict_json_schema is True


def test_run_calls_tool_and_records_answer(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    model = _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "売上"}, call_id="call-1")],
        [assistant_message("今月の売上は 3 件です。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert [(name, args) for name, args, _ in calls.items] == [(LOOKUP, {"query": "売上"})]
    # ツールは Run の利用者として呼ぶ（RAG / NL2SQL のサービストークンの sub。#233）。
    assert calls.items[0][2].user_uuid == USER_UUID
    assert calls.items[0][2].run_id == run_id
    [step] = run.steps
    assert step.status == "completed"
    assert step.tool_call is not None and step.tool_call.trace_id == "call-1"
    answer = next(item for item in run.artifacts if item.kind == "answer")
    assert answer.content == {"text": "今月の売上は 3 件です。"}
    # モデルには Agent と Skill の指示と、ツールの結果を渡している。
    first, second = model.calls
    assert "経理の質問に答える。" in str(first.system_instructions)
    assert '"rows": 3' in str(second.input)


def test_tool_requiring_approval_waits_and_resumes(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-w")],
        [assistant_message("登録しました。")],
    )
    run_id = _create_run("登録して")

    anyio.run(builtin_runtime.execute_run, run_id)

    waiting = runtime_repository.get_run(run_id)
    assert waiting.status == RunStatus.WAITING_APPROVAL
    assert calls.items == []
    [approval] = waiting.approvals
    assert approval.tool_call.name == WRITE
    assert approval.tool_call.arguments == {"query": "登録"}

    decided = runtime_repository.decide_approval(
        approval.id, ApprovalDecisionRequest(approved=True, decided_by="approver1")
    )
    assert decided.status == RunStatus.QUEUED
    assert builtin_resume_pending(decided)

    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert [name for name, _, _ in calls.items] == [WRITE]
    # 承認後のツールも承認者ではなく Run の利用者として呼ぶ（MCP のサービストークンの sub。#233）。
    [(_, _, context)] = calls.items
    assert context.user_uuid == USER_UUID
    assert context.approval_id == approval.id
    # 承認済みの step を実行して完了にする（step を重複して作らない）。
    assert [step.status for step in run.steps] == ["completed"]
    assert run.steps[0].approval_id == approval.id
    assert "_builtin_sdk_state" not in run.metadata


def test_rejected_tool_is_not_executed(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-r")],
        [assistant_message("承認されなかったため登録していません。")],
    )
    run_id = _create_run("登録して")
    anyio.run(builtin_runtime.execute_run, run_id)
    [approval] = runtime_repository.get_run(run_id).approvals

    runtime_repository.decide_approval(approval.id, ApprovalDecisionRequest(approved=False))
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert calls.items == []
    assert [step.status for step in run.steps] == ["cancelled"]


def _usage(requests: int, input_tokens: int, output_tokens: int) -> Usage:
    return Usage(
        requests=requests,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def test_usage_is_recorded_and_accumulates_across_approval(
    monkeypatch: MonkeyPatch, calls: _Calls
) -> None:
    """モデルの利用量を Run に残し、承認待ちから再開しても累計にする（#772）。"""
    del calls
    monkeypatch.setattr(
        builtin_runtime, "enterprise_ai_default_model_id", lambda _settings: "default-model"
    )
    _script(
        monkeypatch,
        ModelStep(
            output=[function_call(WRITE, {"query": "登録"}, call_id="call-u")],
            usage=_usage(1, 100, 20),
        ),
        ModelStep(output=[assistant_message("登録しました。")], usage=_usage(1, 150, 30)),
    )
    run_id = _create_run("登録して")

    anyio.run(builtin_runtime.execute_run, run_id)
    waiting = runtime_repository.get_run(run_id)
    assert waiting.usage is not None
    assert (waiting.usage.requests, waiting.usage.total_tokens) == (1, 120)
    # Agent がモデルを指定しないときは、その時点の既定のテキストモデルの名前を残す。
    assert waiting.usage.model == "default-model"

    [approval] = waiting.approvals
    runtime_repository.decide_approval(approval.id, ApprovalDecisionRequest(approved=True))
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert run.usage is not None
    assert run.usage.model_dump() == {
        "model": "default-model",
        "requests": 2,
        "input_tokens": 250,
        "output_tokens": 50,
        "total_tokens": 300,
    }


def test_usage_is_recorded_when_the_run_fails_after_model_calls(
    monkeypatch: MonkeyPatch, calls: _Calls
) -> None:
    """ツール呼び出しの上限で止まった Run も、それまでの利用量を残す（#772）。"""
    del calls
    monkeypatch.setattr(builtin_runtime, "_max_turns", lambda: 2)
    _script(
        monkeypatch,
        *[
            ModelStep(
                output=[function_call(LOOKUP, {"query": "売上"}, call_id=f"call-{index}")],
                usage=_usage(1, 10, 5),
            )
            for index in range(2)
        ],
    )
    # Run は作成時の版を使う（#770）。モデルを変えた下書きで実行する。
    runtime_repository.patch_agent(AGENT_ID, AgentProfilePatch(model_id="explicit-model"))
    run_id = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="ずっと調べる", agent_id=AGENT_ID, draft=True),
        created_by_user_uuid=USER_UUID,
    ).id

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.FAILED
    assert run.events[-1].payload["error_code"] == "runtime.max_turns_exceeded"
    assert run.usage is not None
    assert (run.usage.model, run.usage.requests, run.usage.total_tokens) == (
        "explicit-model",
        2,
        30,
    )


def test_missing_model_settings_fail_the_run(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    del calls

    def missing(_model_id: str = "") -> ModelTarget:
        raise builtin_runtime.BuiltinRuntimeError("runtime.model_not_configured", "モデル未設定")

    monkeypatch.setattr(builtin_runtime, "resolve_model_target", missing)
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.FAILED
    assert run.events[-1].payload["error_code"] == "runtime.model_not_configured"


def test_cancelled_run_is_not_started(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    model = _script(monkeypatch, [assistant_message("実行されない")])
    run_id = _create_run()
    runtime_repository.cancel_run(run_id)

    anyio.run(builtin_runtime.execute_run, run_id)

    assert runtime_repository.get_run(run_id).status == RunStatus.CANCELLED
    assert model.calls == ()
    assert calls.items == []


def _dispatch_until_settled(run_id: str) -> None:
    """runtime-dispatcher で、この Run が承認待ちか終了になるまで claim と実行を繰り返す。"""
    from app.features.agent.runtime_dispatcher import dispatch_once

    for _ in range(20):
        status = runtime_repository.get_run(run_id).status
        if status in {RunStatus.WAITING_APPROVAL, RunStatus.COMPLETED, RunStatus.FAILED}:
            return
        anyio.run(dispatch_once, "worker-754")
    raise AssertionError(f"run did not settle: {runtime_repository.get_run(run_id).status}")


def test_api_run_is_executed_and_resumed_by_dispatcher(
    monkeypatch: MonkeyPatch, calls: _Calls
) -> None:
    """API は Run を queued で作り、dispatcher（本番の別プロセス）が実行・承認後の再開をする。"""
    from security_support import client

    from app.settings import get_settings

    monkeypatch.setattr(get_settings(), "agent_runtime_dispatch_mode", "dispatcher")
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-api")],
        [assistant_message("登録しました。")],
    )

    created = client.post("/api/runs", json={"goal": "登録して", "agent_id": AGENT_ID})
    assert created.status_code == 200, created.text
    run_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "queued"
    assert created.json()["data"]["runtime_id"] == BUILTIN_RUNTIME_ID

    _dispatch_until_settled(run_id)
    waiting = runtime_repository.get_run(run_id)
    assert waiting.status == RunStatus.WAITING_APPROVAL
    approval_id = waiting.approvals[0].id

    decided = client.post(f"/api/approvals/{approval_id}/decision", json={"approved": True})
    assert decided.status_code == 200, decided.text
    assert decided.json()["data"]["status"] == "queued"
    # 決定者はログイン中の利用者（local はローカル利用者）。
    assert decided.json()["data"]["approvals"][0]["decided_by"] == "local"

    _dispatch_until_settled(run_id)
    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert [name for name, _, _ in calls.items] == [WRITE]


def test_runtime_status_reports_model_and_missing_settings(monkeypatch: MonkeyPatch) -> None:
    from security_support import client

    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda _model_id="": ModelTarget(
            model_id="xai.grok-4", endpoint="https://oci.example", project_ocid="", api_key="secret"
        ),
    )
    ready = client.get("/api/runtime/status").json()["data"]
    assert ready["ready"] is True
    assert ready["model_id"] == "xai.grok-4"
    assert ready["sdk"] == "openai-agents"
    assert "secret" not in str(ready)

    def missing(_model_id: str = "") -> ModelTarget:
        raise builtin_runtime.BuiltinRuntimeError("runtime.model_not_configured", "モデル未設定")

    monkeypatch.setattr(builtin_runtime, "resolve_model_target", missing)
    blocked = client.get("/api/runtime/status").json()["data"]
    assert blocked["ready"] is False
    assert blocked["error_code"] == "runtime.model_not_configured"


def test_tracing_is_disabled() -> None:
    """業務データを含む trace を外部（OpenAI）へ送らない。"""
    from agents.tracing import get_trace_provider

    provider = get_trace_provider()
    assert getattr(provider, "_disabled", True) is True


def test_oci_model_omits_empty_tools_for_xai() -> None:
    """OCI の xAI のモデルは空の `tools` を 400 で拒否するため、送る前に外す（実環境で確認）。"""

    class _Responses:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {}

    class _Client:
        def __init__(self) -> None:
            self.responses = _Responses()
            self.base_url = "https://oci.example/openai/v1"

    client = _Client()
    model = builtin_runtime.OciResponsesModel(model="xai.grok-4.3", openai_client=client)  # type: ignore[arg-type]
    wrapped: Any = model._get_client()  # noqa: SLF001 - SDK の内部の差し替えを確かめる
    assert wrapped.base_url == "https://oci.example/openai/v1"

    anyio.run(lambda: wrapped.responses.create(model="m", input="x", tools=[], tool_choice="auto"))
    anyio.run(lambda: wrapped.responses.create(model="m", input="x", tools=[{"type": "function"}]))

    assert client.responses.calls[0] == {"model": "m", "input": "x"}
    assert client.responses.calls[1]["tools"] == [{"type": "function"}]


def _bad_request(code: str, message: str) -> Any:
    # openai は httpx2 の Response を受け取る（httpx の Response は型が合わない）。
    import httpx2
    from openai import BadRequestError

    body = {"code": code, "message": message, "param": None, "type": "invalid_request_error"}
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://oci.example/responses"))
    return BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


class _RecordingResponses:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._failures = list(failures or [])

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._failures:
            raise self._failures.pop(0)
        return {}


class _RecordingClient:
    def __init__(self, responses: _RecordingResponses) -> None:
        self.responses = responses


def _oci_client(responses: _RecordingResponses) -> Any:
    model = builtin_runtime.OciResponsesModel(
        model="openai.gpt-oss-120b",
        openai_client=_RecordingClient(responses),  # type: ignore[arg-type]
    )
    return model._get_client()  # noqa: SLF001 - SDK の内部の差し替えを確かめる


def test_oci_model_omits_reasoning_text_from_the_input() -> None:
    """OCI の gpt-oss は input の reasoning_text を 400 で拒否する（#1215。実環境で確認）。"""
    from openai.types.responses import ResponseReasoningItem

    responses = _RecordingResponses()
    wrapped = _oci_client(responses)
    reasoning = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [],
        "content": [{"type": "reasoning_text", "text": "規程を検索する"}],
    }
    model_reasoning = ResponseReasoningItem.model_validate({**reasoning, "id": "rs_2"})
    call = {"type": "function_call", "call_id": "c1", "name": "rag__rag_search", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "c1", "output": "{}"}
    items = [{"role": "user", "content": "質問"}, reasoning, model_reasoning, call, output]

    anyio.run(lambda: wrapped.responses.create(model="m", input=items))

    sent = responses.calls[0]["input"]
    assert sent[1] == {"id": "rs_1", "type": "reasoning", "summary": []}
    assert sent[2] == {"id": "rs_2", "type": "reasoning", "summary": []}
    assert sent[0] is items[0] and sent[3] is call and sent[4] is output
    # 渡された input は変えない（SDK の状態に影響させない）。
    assert reasoning["content"]

    anyio.run(lambda: wrapped.responses.create(model="m", input="x"))
    assert responses.calls[1]["input"] == "x"


def test_oci_model_retries_model_output_invalid_only() -> None:
    """OCI が再試行を求める `model_output_invalid` だけ、上限まで同じ要求で再試行する（#1215）。"""
    invalid = _bad_request("model_output_invalid", "The selected model could not produce ...")
    responses = _RecordingResponses([invalid, invalid])
    wrapped = _oci_client(responses)

    assert anyio.run(lambda: wrapped.responses.create(model="m", input="x")) == {}
    assert len(responses.calls) == 3

    exhausted = _RecordingResponses([invalid] * 3)
    with pytest.raises(Exception, match="model_output_invalid"):
        anyio.run(lambda: _oci_client(exhausted).responses.create(model="m", input="x"))
    assert len(exhausted.calls) == 1 + builtin_runtime.MODEL_OUTPUT_INVALID_RETRIES

    other = _RecordingResponses([_bad_request("invalid_value", "reasoning_text is not supported")])
    with pytest.raises(Exception, match="invalid_value"):
        anyio.run(lambda: _oci_client(other).responses.create(model="m", input="x"))
    assert len(other.calls) == 1


def test_model_failure_records_the_api_error_code(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    """型名（BadRequestError）だけでは原因が分からないため、API の code まで残す（#1215）。"""
    del calls
    error = _bad_request("invalid_value", "reasoning_text is not supported by model m.")

    class _FailingModel(ScriptedModel):
        async def get_response(self, *args: Any, **kwargs: Any) -> Any:
            raise error

    _script(monkeypatch)  # モデルの接続の設定（CI には model-settings.json が無い）
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: _FailingModel([]))
    run_id = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="規程を調べて", agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    ).id

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.FAILED
    failed = run.events[-1]
    assert failed.payload["error_code"] == "runtime.model_failed"
    assert failed.message == "モデルの呼び出しに失敗しました（BadRequestError: invalid_value）。"


# ---------------------------------------------------------------------------
# MCP 接続のツール（RAG / NL2SQL。#757）
# ---------------------------------------------------------------------------

MCP_SKILL_ID = "test757-skill"
MCP_AGENT_ID = "test757-agent"


@pytest.fixture
def mcp_agent(monkeypatch: MonkeyPatch) -> Iterator[Any]:
    """RAG の rag_search と、NL2SQL のすべてのツールを使う Agent（fake の MCP サーバー）。"""
    mcp = fake_product_mcp(monkeypatch)
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=MCP_SKILL_ID,
            name="MCP の Skill",
            instructions="RAG と NL2SQL で調べる。",
            mcp_requirements=[
                SkillMcpRequirement(server_id="rag", tool_names=["rag_search"]),
                SkillMcpRequirement(server_id="nl2sql"),
            ],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=MCP_AGENT_ID, name="MCP の Agent", skill_ids=[MCP_SKILL_ID])
    )
    try:
        yield mcp
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == MCP_AGENT_ID
            ]:
                run = repository._runs.pop(run_id)
                for approval in run.approvals:
                    repository._approvals.pop(approval.id, None)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(MCP_AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(MCP_SKILL_ID)


def _create_mcp_run(goal: str) -> str:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=MCP_AGENT_ID), created_by_user_uuid=USER_UUID
    )
    return run.id


def test_mcp_connection_tools_are_given_to_the_model(
    monkeypatch: MonkeyPatch, mcp_agent: Any
) -> None:
    model = _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "契約の更新条件"}, call_id="call-rag")],
        [assistant_message("更新条件は契約書の第 5 条です。")],
    )
    run_id = _create_mcp_run("契約の更新条件を調べて")

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    # Skill が許可したツールだけを渡す（rag は rag_search だけ、nl2sql はすべて）。
    tools = {tool.name: tool for tool in model.calls[0].tools if isinstance(tool, FunctionTool)}
    assert "rag__rag_search" in tools
    assert "rag__rag_list_search_answer_profiles" not in tools
    assert {"nl2sql__nl2sql_query", "nl2sql__nl2sql_get_job"} <= set(tools)
    # readOnlyHint の無いツールは承認が必要、読み取り専用のツールは承認なし。
    assert tools["nl2sql__nl2sql_query"].needs_approval is True
    assert tools["rag__rag_search"].needs_approval is False
    # ツールは Run の利用者のサービストークン（aud = 接続）で呼ぶ。
    [call] = mcp_agent.calls_of("rag_search")
    assert call["claims"]["sub"] == USER_UUID
    assert call["claims"]["aud"] == "rag"
    assert call["claims"]["run_id"] == run_id
    assert call["arguments"] == {"query": "契約の更新条件"}
    [step] = run.steps
    assert step.tool_call is not None and step.tool_call.name == "rag__rag_search"
    assert step.status == "completed"
    # RAG の結果は根拠の成果物として残る。
    evidence = next(item for item in run.artifacts if item.kind == "rag_evidence")
    assert evidence.content["evidence"][0]["file_name"] == "契約書.pdf"


def test_unavailable_mcp_connection_is_skipped_with_a_warning(
    monkeypatch: MonkeyPatch, mcp_agent: Any
) -> None:
    runtime_config_store.upsert_mcp_server("nl2sql", base_url="")
    model = _script(monkeypatch, [assistant_message("RAG だけで回答しました。")])
    run_id = _create_mcp_run("売上を調べて")

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    names = {tool.name for tool in model.calls[0].tools}
    assert "rag__rag_search" in names
    assert not any(name.startswith("nl2sql__") for name in names)
    warning = next(event for event in run.events if event.type == "runtime.event")
    assert "NL2SQL" in warning.message
    assert warning.payload == {"severity": "warning"}


def test_unreachable_mcp_service_is_a_tool_error_and_the_run_continues(
    monkeypatch: MonkeyPatch, mcp_agent: Any
) -> None:
    """RAG が止まっても Run は例外で落ちず、ツールの失敗をモデルへ返して回答まで進む（#854）。"""
    _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "契約の更新条件"}, call_id="call-rag")],
        [assistant_message("RAG に接続できないため、いまは調べられません。")],
    )
    run_id = _create_mcp_run("契約の更新条件を調べて")
    # ツールの一覧の取得の後に RAG が止まった（tools/call の前の initialize から接続できない）。
    real_list = builtin_runtime.discover_mcp_tools

    def discover_then_stop(*args: Any, **kwargs: Any) -> Any:
        found = real_list(*args, **kwargs)
        mcp_agent.method_failures["initialize"] = ["connect"] * 20
        return found

    monkeypatch.setattr(builtin_runtime, "discover_mcp_tools", discover_then_stop)

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    [step] = run.steps
    assert step.status == "failed"
    assert step.tool_result is not None
    assert step.tool_result.error_code == "mcp.unreachable"
    assert "RAG のサービスが起動しているか" in (step.tool_result.error or "")
    assert mcp_agent.calls_of("rag_search") == []


# ---------------------------------------------------------------------------
# 会話（スレッド。#768）
# ---------------------------------------------------------------------------


def test_thread_passes_previous_turns_to_the_model(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    model = _script(
        monkeypatch,
        [assistant_message("今月の売上は 120 万円です。")],
        [assistant_message("先月は 100 万円なので 20 万円増えました。")],
    )
    first_id = _create_run("今月の売上は？")
    anyio.run(builtin_runtime.execute_run, first_id)
    first = runtime_repository.get_run(first_id)
    assert first.thread_id is not None and first.thread_id.startswith("thread_")

    second = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="先月と比べると？", agent_id=AGENT_ID, thread_id=first.thread_id),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, second.id)

    assert runtime_repository.get_run(second.id).status == RunStatus.COMPLETED
    # 1 回目は質問だけ、2 回目は前の質問と回答を付けてモデルへ渡す。
    assert model.calls[0].input == [{"role": "user", "content": "今月の売上は？"}]
    assert model.calls[1].input == [
        {"role": "user", "content": "今月の売上は？"},
        {"role": "assistant", "content": "今月の売上は 120 万円です。"},
        {"role": "user", "content": "先月と比べると？"},
    ]

    thread = runtime_repository.get_thread(first.thread_id, user_uuid=USER_UUID)
    assert [run.id for run in thread.runs] == [first_id, second.id]
    [summary] = [
        item
        for item in runtime_repository.list_threads(user_uuid=USER_UUID, agent_id=AGENT_ID)
        if item.thread_id == first.thread_id
    ]
    assert (summary.title, summary.run_count, summary.last_status) == (
        "今月の売上は？",
        2,
        RunStatus.COMPLETED,
    )


def test_thread_belongs_to_its_user_and_agent(calls: _Calls) -> None:
    first_id = _create_run("自分の会話")
    thread_id = runtime_repository.get_run(first_id).thread_id
    assert thread_id is not None

    # 別の利用者は続けられず、一覧にも出ない。別の Agent でも続けられない。
    with pytest.raises(ThreadNotFoundError):
        runtime_repository.create_builtin_run(
            RunCreateRequest(goal="横取り", agent_id=AGENT_ID, thread_id=thread_id),
            created_by_user_uuid="99999999-0000-0000-0000-000000000000",
        )
    with pytest.raises(ThreadNotFoundError):
        runtime_repository.create_builtin_run(
            RunCreateRequest(goal="別の Agent", agent_id="default", thread_id=thread_id),
            created_by_user_uuid=USER_UUID,
        )
    with pytest.raises(ThreadNotFoundError):
        runtime_repository.get_thread(thread_id, user_uuid="99999999-0000-0000-0000-000000000000")
    others = runtime_repository.list_threads(user_uuid="99999999-0000-0000-0000-000000000000")
    assert thread_id not in {item.thread_id for item in others}


def test_thread_api_pages_conversations(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    """会話の履歴は新しい順に limit / offset でページングし、全件数を返す（#1265）。"""
    from security_support import client

    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda run: None)
    before = client.get(f"/api/threads?agent_id={AGENT_ID}").json()["data"]["total"]
    created = []
    for index in range(12):
        response = client.post("/api/runs", json={"goal": f"質問 {index}", "agent_id": AGENT_ID})
        assert response.status_code == 200, response.text
        created.append(response.json()["data"]["thread_id"])

    first = client.get(f"/api/threads?agent_id={AGENT_ID}").json()["data"]
    assert (first["total"], first["limit"], first["offset"]) == (before + 12, 10, 0)
    assert [item["thread_id"] for item in first["items"]] == created[::-1][:10]
    assert first["has_next"] is True
    second = client.get(f"/api/threads?agent_id={AGENT_ID}&limit=10&offset=10").json()["data"]
    assert [item["thread_id"] for item in second["items"]][:2] == created[1::-1]
    assert second["total"] == before + 12
    assert client.get("/api/threads?limit=0").status_code == 422
    assert client.get("/api/threads?offset=-1").status_code == 422


def test_thread_api_lists_and_continues_conversation(
    monkeypatch: MonkeyPatch, calls: _Calls
) -> None:
    from pr_system_settings.auth.domain import LOCAL_DEBUG_USER_UUID
    from security_support import client

    scheduled: list[str] = []
    monkeypatch.setattr(
        "app.features.agent.router._schedule_builtin_run", lambda run: scheduled.append(run.id)
    )

    created = client.post("/api/runs", json={"goal": "最初の質問", "agent_id": AGENT_ID})
    assert created.status_code == 200, created.text
    thread_id = created.json()["data"]["thread_id"]
    continued = client.post(
        "/api/runs",
        json={"goal": "続きの質問", "agent_id": AGENT_ID, "thread_id": thread_id},
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["data"]["thread_id"] == thread_id
    assert continued.json()["data"]["created_by_user_uuid"] == LOCAL_DEBUG_USER_UUID

    listed = client.get(f"/api/threads?agent_id={AGENT_ID}")
    assert listed.status_code == 200
    [thread] = [item for item in listed.json()["data"]["items"] if item["thread_id"] == thread_id]
    assert thread["title"] == "最初の質問"
    assert thread["run_count"] == 2

    assert listed.json()["data"]["total"] >= 1
    assert listed.json()["data"]["limit"] == 10
    assert listed.json()["data"]["offset"] == 0

    detail = client.get(f"/api/threads/{thread_id}")
    assert detail.status_code == 200
    assert [run["goal"] for run in detail.json()["data"]["runs"]] == ["最初の質問", "続きの質問"]

    missing = "thread_" + "0" * 32
    assert client.get(f"/api/threads/{missing}").status_code == 404
    unknown = client.post(
        "/api/runs", json={"goal": "x", "agent_id": AGENT_ID, "thread_id": missing}
    )
    assert unknown.status_code == 404
    assert unknown.json()["error_messages"] == ["会話が見つかりません。"]
    # チャットの送信・停止の失敗にそのまま出る理由は日本語（英語・内部の ID を出さない）。
    no_agent = client.post("/api/runs", json={"goal": "x", "agent_id": "agent-missing"})
    assert no_agent.status_code == 404
    assert no_agent.json()["error_messages"] == ["業務 Agent が見つかりません。"]
    no_run = client.post("/api/runs/run-missing/cancel")
    assert no_run.status_code == 404
    assert no_run.json()["error_messages"] == ["実行が見つかりません。"]
    assert (
        client.post(
            "/api/runs", json={"goal": "x", "agent_id": AGENT_ID, "thread_id": "bad"}
        ).status_code
        == 422
    )
    assert len(scheduled) == 2


@pytest.mark.parametrize("permitted", [True, False])
def test_external_skill_reads_only_its_saved_reference(
    monkeypatch: MonkeyPatch, calls: _Calls, permitted: bool
) -> None:
    from app.features.agent.plugins import PluginResource, plugin_resource_registry
    from app.features.agent.skill_resources import RESOURCE_TOOL_NAME

    del calls
    resource_id = "test-skill-reference"
    source = "test-skill-reference-source"
    plugin_resource_registry.set_declared(
        source,
        [
            PluginResource(
                id=resource_id,
                kind="prompt",
                name="REFERENCE.md",
                media_type="text/markdown",
                content="対象の根拠を確認する。",
                metadata={"external_skill_reference": True},
            )
        ],
    )
    skill = skill_registry.get(SKILL_ID)
    assert skill is not None
    skill_registry.upsert_custom(skill.model_copy(update={"resource_ids": [resource_id]}))
    model = _script(
        monkeypatch,
        [
            function_call(
                RESOURCE_TOOL_NAME,
                {"resource_id": resource_id if permitted else "unassigned-reference", "offset": 0},
                call_id="read-reference",
            )
        ],
        [assistant_message("確認しました。")],
    )
    try:
        run_id = _create_run()
        anyio.run(builtin_runtime.execute_run, run_id)
        run = runtime_repository.get_run(run_id)
        assert run.status == RunStatus.COMPLETED
        assert "REFERENCE.md" in str(model.calls[0].system_instructions)
        assert "対象の根拠を確認する。" not in str(model.calls[0].system_instructions)
        if permitted:
            assert "対象の根拠を確認する。" in str(model.calls[1].input)
            assert run.steps[0].status == "completed"
        else:
            assert "割り当てられていない" in str(model.calls[1].input)
            assert run.steps[0].status == "failed"
    finally:
        plugin_resource_registry.set_declared(source, [])
