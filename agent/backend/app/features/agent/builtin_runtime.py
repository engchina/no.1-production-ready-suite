"""組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI の Responses API。#754）。

Control Plane の中で業務 Agent を実行する。外部の Runtime・Binding・Docker は使わない。

- モデル: システム設定 > モデル の OCI Enterprise AI（Agent の `model_id`、無ければ既定の
  テキストモデル）。
  SDK の tracing は無効にする（業務データを外部へ送らない）。
- 指示: Agent の指示と、割り当てた Skill の指示を合わせる。
- ツール: Skill が必要とする `tool_registry` のツールを function tool にする。実行は
  `tool_registry.invoke`（ポリシー・ガードレール・監査・RAG / NL2SQL のサービストークン）を通す。
  ポリシーが「拒否」のツールは渡さない。「承認」のツールは SDK の `needs_approval` で中断する。
- 承認: 中断した Run は SDK の状態を Run に保存して `waiting_approval` にする。すべての承認が
  決まったら状態を復元し、承認・却下を反映して再開する。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

from agents import (
    Agent,
    FunctionTool,
    MaxTurnsExceeded,
    Model,
    ModelSettings,
    OpenAIResponsesModel,
    Runner,
    RunState,
    set_tracing_disabled,
)
from agents.tool_context import ToolContext
from openai import AsyncOpenAI
from pr_system_settings.model import (
    enterprise_ai_connection_for_model,
    enterprise_ai_default_model_id,
    enterprise_ai_model_catalog,
)

from app.features.agent.config import runtime_config_store
from app.features.agent.skills import skill_registry
from app.features.agent.tools import (
    ToolCall,
    ToolPolicy,
    ToolPolicyDecision,
    tool_registry,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

BUILTIN_RUNTIME_ID = "builtin"
SDK_PACKAGE = "openai-agents"
# Skill の requirement のうち、Control Plane のツール（`tool_registry`）を指す server_id。
CONTROL_PLANE_SERVER_ID = "control-plane"
# 業務データを含む trace を外部（OpenAI）へ送らない。
set_tracing_disabled(True)


class BuiltinRuntimeError(RuntimeError):
    """組み込み Runtime の失敗（画面とイベントに出す code と文言）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ModelTarget:
    """Run に使うモデル（API key は持つが、repr・ログには出さない）。"""

    model_id: str
    endpoint: str
    project_ocid: str
    api_key: str

    def __repr__(self) -> str:  # pragma: no cover - API key を出さない
        return f"ModelTarget(model_id={self.model_id!r}, endpoint={self.endpoint!r})"


def sdk_version() -> str:
    try:
        return version(SDK_PACKAGE)
    except PackageNotFoundError:  # pragma: no cover - 依存は lock で固定
        return "unknown"


def resolve_model_target(model_id: str = "") -> ModelTarget:
    """Agent のモデル（空なら既定のテキストモデル）と、その接続を決める。"""
    settings = get_settings()
    resolved = (model_id or "").strip() or enterprise_ai_default_model_id(settings)
    if not resolved:
        raise BuiltinRuntimeError(
            "runtime.model_not_configured",
            "使うモデルが決まっていません。"
            "システム設定 > モデル で既定のテキストモデルを設定してください。",
        )
    connection = enterprise_ai_connection_for_model(settings, resolved)
    if not connection.is_configured():
        raise BuiltinRuntimeError(
            "runtime.model_connection_not_configured",
            "OCI Enterprise AI の接続（Endpoint URL と API key）が設定されていません。"
            "システム設定 > モデル で設定してください。",
        )
    return ModelTarget(
        model_id=resolved,
        endpoint=connection.endpoint.rstrip("/"),
        project_ocid=connection.project_ocid,
        api_key=connection.api_key,
    )


def _omit_empty_tools(kwargs: dict[str, Any]) -> dict[str, Any]:
    """ツールが無い呼び出しから `tools: []` と `tool_choice` を外す。

    OCI Enterprise AI の xAI のモデルは、空の `tools` を「ツールなしで tool_choice を指定した」
    として 400 で拒否する（gpt-oss は受け付ける。2026-10-02 に実環境で確認）。SDK はツールが
    無くても空の `tools` を送るため、送る前に外す。
    """
    tools = kwargs.get("tools")
    if isinstance(tools, list) and not tools:
        return {
            key: value
            for key, value in kwargs.items()
            if key not in {"tools", "tool_choice", "parallel_tool_calls"}
        }
    return kwargs


class _OciStreamingResponses:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create(self, **kwargs: Any) -> Any:
        return self._inner.create(**_omit_empty_tools(kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _OciResponses:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def create(self, **kwargs: Any) -> Any:
        return await self._inner.create(**_omit_empty_tools(kwargs))

    @property
    def with_streaming_response(self) -> _OciStreamingResponses:
        return _OciStreamingResponses(self._inner.with_streaming_response)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _OciCompatClient:
    """`responses.create` の引数だけを OCI Enterprise AI 向けに直し、他はそのまま渡す。"""

    def __init__(self, inner: AsyncOpenAI) -> None:
        self._inner = inner

    @property
    def responses(self) -> _OciResponses:
        return _OciResponses(self._inner.responses)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class OciResponsesModel(OpenAIResponsesModel):
    """OCI Enterprise AI の Responses API 用（SDK の `_get_client` を包む。SDK は版を固定する）。"""

    def _get_client(self) -> AsyncOpenAI:
        return cast(AsyncOpenAI, _OciCompatClient(super()._get_client()))


def _default_model_factory(target: ModelTarget) -> Model:
    headers = {"OpenAI-Project": target.project_ocid} if target.project_ocid else None
    client = AsyncOpenAI(base_url=target.endpoint, api_key=target.api_key, default_headers=headers)
    return OciResponsesModel(model=target.model_id, openai_client=client)


# テストは SDK の ScriptedModel を返す関数に差し替える。
model_factory: Callable[[ModelTarget], Model] = _default_model_factory


def _active_policy() -> ToolPolicy:
    config = runtime_config_store.get_tool_policy()
    return ToolPolicy(
        default_mode=config.default_mode,
        allow=set(config.allow),
        ask=set(config.ask),
        deny=set(config.deny),
    )


def agent_tool_names(skill_ids: list[str]) -> list[str]:
    """Agent の Skill が必要とする Control Plane のツール（登録済みのものだけ、重複なし）。"""
    names: list[str] = []
    for skill_id in skill_ids:
        skill = skill_registry.get(skill_id)
        if skill is None or not skill.enabled:
            continue
        for requirement in skill.mcp_requirements:
            if requirement.server_id != CONTROL_PLANE_SERVER_ID:
                continue
            for name in requirement.tool_names:
                if name not in names and tool_registry.get(name) is not None:
                    names.append(name)
    return names


def compose_instructions(agent_instructions: str, skill_ids: list[str]) -> str:
    """Agent の指示と Skill の指示（AgentSkills の本文）を 1 つの system の指示にする。"""
    sections = [
        "あなたは業務 Agent です。日本語で、根拠を示して簡潔に回答してください。"
        "ツールの結果に無いことは推測で補わず、分からないと答えてください。"
    ]
    if agent_instructions.strip():
        sections.append("# 業務の指示\n" + agent_instructions.strip())
    for skill_id in skill_ids:
        skill = skill_registry.get(skill_id)
        if skill is None or not skill.enabled or not skill.instructions.strip():
            continue
        sections.append(f"# Skill: {skill.name}\n{skill.instructions.strip()}")
    return "\n\n".join(sections)


def _strict_schema(schema: dict[str, Any]) -> bool:
    """SDK の strict mode に使えるか（全 property が required・追加 property なし）。"""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    required = set(schema.get("required") or [])
    return schema.get("additionalProperties") is False and required == set(properties)


class _ToolRecorder:
    """function tool の実行を Run の step・イベント・成果物に記録する。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    async def invoke(self, name: str, arguments: str, call_id: str) -> str:
        from app.features.agent.runtime import runtime_repository

        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            parsed = {}
        call = ToolCall(
            name=name, arguments=parsed if isinstance(parsed, dict) else {}, trace_id=call_id
        )
        step_id, context = runtime_repository.start_builtin_tool_step(self.run_id, call)
        # 承認は SDK の needs_approval で済んでいる（拒否のツールは渡していない）。
        result = await asyncio.to_thread(
            tool_registry.invoke, call, policy=_active_policy(), context=context, force=True
        )
        runtime_repository.finish_builtin_tool_step(self.run_id, step_id, result)
        if result.success:
            return json.dumps(result.output or {}, ensure_ascii=False, default=str)
        return json.dumps(
            {"error": result.error or "tool failed", "error_code": result.error_code},
            ensure_ascii=False,
        )


def build_function_tools(run_id: str, tool_names: list[str]) -> list[FunctionTool]:
    policy = _active_policy()
    recorder = _ToolRecorder(run_id)
    tools: list[FunctionTool] = []
    for name in tool_names:
        definition = tool_registry.get(name)
        if definition is None:
            continue
        decision = policy.decide(definition)
        if decision == ToolPolicyDecision.DENY:
            continue

        async def on_invoke(ctx: ToolContext[Any], arguments: str, *, _name: str = name) -> str:
            # 引数の型を ToolContext にすると、SDK は呼び出し ID（承認の記録と結び付ける）を渡す。
            call_id = str(getattr(ctx, "tool_call_id", "") or "")
            return await recorder.invoke(_name, arguments, call_id)

        schema = dict(definition.input_schema)
        tools.append(
            FunctionTool(
                name=name,
                description=definition.description,
                params_json_schema=schema,
                on_invoke_tool=on_invoke,
                strict_json_schema=_strict_schema(schema),
                needs_approval=decision == ToolPolicyDecision.ASK,
                timeout_seconds=max(definition.timeout_seconds, 1.0) * 2,
            )
        )
    return tools


def build_sdk_agent(
    run_id: str, *, name: str, instructions: str, skill_ids: list[str], model_id: str
) -> Agent[Any]:
    target = resolve_model_target(model_id)
    return Agent(
        name=name or "agent",
        instructions=compose_instructions(instructions, skill_ids),
        tools=list(build_function_tools(run_id, agent_tool_names(skill_ids))),
        model=model_factory(target),
        model_settings=ModelSettings(store=False),
    )


async def execute_run(run_id: str) -> None:
    """Run を最初から実行する（作成直後・dispatcher から呼ぶ）。"""
    from app.features.agent.runtime import runtime_repository

    started = runtime_repository.begin_builtin_run(run_id)
    if started is None:
        return
    run, agent = started
    try:
        sdk_agent = build_sdk_agent(
            run_id,
            name=agent.name,
            instructions=agent.instructions,
            skill_ids=agent.skill_ids,
            model_id=agent.model_id,
        )
        result = await Runner.run(sdk_agent, run.goal, max_turns=_max_turns())
        await _finish(run_id, result)
    except Exception as exc:  # noqa: BLE001 - 実行の境界では失敗を Run に記録する
        _record_failure(run_id, exc)


async def resume_run(run_id: str) -> None:
    """承認がすべて決まった Run を、保存した SDK の状態から再開する。"""
    from app.features.agent.runtime import runtime_repository

    resumed = runtime_repository.begin_builtin_resume(run_id)
    if resumed is None:
        return
    run, agent, state_text, decisions = resumed
    try:
        sdk_agent = build_sdk_agent(
            run_id,
            name=agent.name,
            instructions=agent.instructions,
            skill_ids=agent.skill_ids,
            model_id=agent.model_id,
        )
        state = await RunState.from_string(sdk_agent, state_text)
        for item in state.get_interruptions():
            call_id = str(getattr(item, "call_id", "") or "")
            if decisions.get(call_id, False):
                state.approve(item)
            else:
                state.reject(item)
        result = await Runner.run(sdk_agent, state, max_turns=_max_turns())
        await _finish(run_id, result)
    except Exception as exc:  # noqa: BLE001 - 実行の境界では失敗を Run に記録する
        _record_failure(run_id, exc)


async def _finish(run_id: str, result: Any) -> None:
    from app.features.agent.runtime import runtime_repository

    interruptions = list(getattr(result, "interruptions", []) or [])
    if interruptions:
        calls = [
            ToolCall(
                name=str(getattr(item, "tool_name", "") or getattr(item, "name", "") or "tool"),
                arguments=_arguments(getattr(item, "arguments", None)),
                trace_id=str(getattr(item, "call_id", "") or ""),
            )
            for item in interruptions
        ]
        runtime_repository.request_builtin_approvals(
            run_id, calls, state=result.to_state().to_string()
        )
        return
    output = result.final_output
    answer = (
        output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
    )
    runtime_repository.complete_builtin_run(run_id, answer)


def _arguments(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _record_failure(run_id: str, exc: Exception) -> None:
    from app.features.agent.runtime import runtime_repository

    if isinstance(exc, BuiltinRuntimeError):
        code, message = exc.code, exc.message
    elif isinstance(exc, MaxTurnsExceeded):
        code = "runtime.max_turns_exceeded"
        message = "ツールの呼び出しが上限の回数に達したため止めました。"
    else:
        code = "runtime.model_failed"
        message = f"モデルの呼び出しに失敗しました（{type(exc).__name__}）。"
    logger.warning("builtin_runtime_failed", extra={"run_id": run_id, "error_code": code})
    runtime_repository.fail_builtin_run(run_id, code=code, detail=message)


def _max_turns() -> int:
    return max(2, int(get_settings().agent_max_tool_calls_per_run) + 1)


def runtime_status() -> dict[str, Any]:
    """Runtime 画面の状態（API key は出さない）。"""
    status: dict[str, Any] = {
        "id": BUILTIN_RUNTIME_ID,
        "name": "組み込み Runtime",
        "sdk": SDK_PACKAGE,
        "sdk_version": sdk_version(),
        "model_provider": "OCI Enterprise AI（Responses API）",
        "model_id": "",
        "ready": False,
        "error_code": None,
        "message": None,
        # Agent ごとに選べるモデル（システム設定 > モデル の登録モデル。ID と表示名だけ）。
        "models": [
            {"model_id": model.model_id, "display_name": model.display_name or model.model_id}
            for model in enterprise_ai_model_catalog(get_settings())
            if model.model_id
        ],
    }
    try:
        target = resolve_model_target("")
    except BuiltinRuntimeError as exc:
        status.update(error_code=exc.code, message=exc.message)
        return status
    status.update(model_id=target.model_id, ready=True)
    return status
