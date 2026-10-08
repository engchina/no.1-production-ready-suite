"""組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI の Responses API。#754）。

Control Plane の中で業務 Agent を実行する。外部の Runtime・Binding・Docker は使わない。

- モデル: システム設定 > モデル の OCI Enterprise AI（Agent の `model_id`、無ければ既定の
  テキストモデル）。
  SDK の tracing は無効にする（業務データを外部へ送らない）。
- 指示: Agent の指示と、割り当てた Skill の指示を合わせる。
- ツール: Skill が必要とするツールを function tool にする（#757）。`control-plane` は
  `tool_registry` のツール、それ以外の server_id は MCP 接続（RAG / NL2SQL / 外部 MCP）の
  `tools/list` のツール（名前は `<接続>__<ツール>`、一覧は Run の利用者の権限で絞られる）。
  実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・サービストークン）を通す。
  ポリシーが「拒否」のツールは渡さない。「承認」のツールは SDK の `needs_approval` で中断する
  （MCP のツールは readOnlyHint が無ければ既定で承認が必要）。
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
from openai import APIStatusError, AsyncOpenAI, BadRequestError
from pr_backend_core.observability.request_context import bind_log_context
from pr_system_settings.model import (
    enterprise_ai_connection_for_model,
    enterprise_ai_default_model_id,
    enterprise_ai_model_catalog,
)

from app.features.agent.answer_validation import (
    ANSWER_VALIDATION_KIND,
    ANSWER_VALIDATION_NAME,
    EVIDENCE_TOOLS,
    MAX_ANSWER_CHARS,
    MAX_QUERY_CHARS,
    NO_EVIDENCE_NOTICE,
    REASON_ANSWER_TOO_LONG,
    REASON_CONNECTION_NOT_FOUND,
    REASON_EMPTY_ANSWER,
    REASON_NO_RAG_EVIDENCE,
    REASON_UNUSABLE_RESULT,
    REASON_VALIDATION_ERROR,
    REASON_VALIDATOR_UNAVAILABLE,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    STATUS_UNVALIDATED,
    VALIDATE_ANSWER_TOOL,
    combine_validations,
    connection_check_inputs,
    run_evidence_groups,
    usable_result,
    validation_content,
    with_no_evidence_notice,
    with_unverified_notice,
)
from app.features.agent.config import McpConnectionConfig, runtime_config_store
from app.features.agent.skills import skill_registry
from app.features.agent.support_task import (
    BUDGET_EXCEEDED_CODE,
    SupportTaskBudget,
    budget_exceeded_message,
    build_support_task,
    has_support_task_activity,
    support_task_instructions,
)
from app.features.agent.tools import (
    ExternalToolError,
    ToolCall,
    ToolDefinition,
    ToolHandler,
    ToolInvocationContext,
    ToolPolicy,
    ToolPolicyDecision,
    ToolResult,
    list_mcp_connection_tools,
    mcp_base_tool_name,
    mcp_function_name,
    mcp_tool_definition,
    mcp_tool_handler,
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


def _omit_reasoning_text(kwargs: dict[str, Any]) -> dict[str, Any]:
    """`input` の reasoning item から本文（`content` の `reasoning_text`）を外す（#1215）。

    SDK は前の応答の reasoning item をそのまま次の呼び出しの `input` に入れる。OCI Enterprise AI の
    `openai.gpt-oss-120b` は、`input` の `reasoning_text` を「reasoning_text is not supported by
    model」の 400 で拒否するため、ツールを呼んだ後の回答が作れない（2026-10-07 に実環境で確認）。
    item（`id`・`summary`）は残す（item ごと除いても回答できることも確認した）。
    """
    items = kwargs.get("input")
    if not isinstance(items, list):
        return kwargs
    changed = False
    sanitized: list[Any] = []
    for item in items:
        data = item if isinstance(item, dict) else None
        if data is None and hasattr(item, "model_dump"):
            data = item.model_dump(exclude_none=True)
        if isinstance(data, dict) and data.get("type") == "reasoning" and "content" in data:
            sanitized.append({key: value for key, value in data.items() if key != "content"})
            changed = True
        else:
            sanitized.append(item)
    return {**kwargs, "input": sanitized} if changed else kwargs


def _oci_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """`responses.create` の引数を OCI Enterprise AI が受け付ける形にする。"""
    return _omit_reasoning_text(_omit_empty_tools(kwargs))


# OCI が「再試行してください」と返す、モデルの出力の一時的な失敗（400 `model_output_invalid`）を
# 同じ要求で再試行する回数（#1215）。
MODEL_OUTPUT_INVALID_RETRIES = 2
MODEL_OUTPUT_INVALID_CODE = "model_output_invalid"


def model_error_code(exc: BaseException) -> str | None:
    """OpenAI 互換 API のエラーの code（`invalid_value` など。無ければ None）。"""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        nested = body.get("error") if isinstance(body.get("error"), dict) else body
        value = nested.get("code") if isinstance(nested, dict) else None
        if isinstance(value, str) and value:
            return value
    return None


class _OciStreamingResponses:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create(self, **kwargs: Any) -> Any:
        return self._inner.create(**_oci_request(kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _OciResponses:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def create(self, **kwargs: Any) -> Any:
        request = _oci_request(kwargs)
        attempt = 0
        while True:
            try:
                return await self._inner.create(**request)
            except BadRequestError as exc:
                if (
                    model_error_code(exc) != MODEL_OUTPUT_INVALID_CODE
                    or attempt >= MODEL_OUTPUT_INVALID_RETRIES
                ):
                    raise
                attempt += 1
                logger.warning(
                    "builtin_runtime_model_output_invalid_retry",
                    extra={"attempt": attempt, "max_retries": MODEL_OUTPUT_INVALID_RETRIES},
                )

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


def agent_mcp_requirements(skill_ids: list[str]) -> dict[str, set[str] | None]:
    """Agent の Skill が必要とする MCP 接続と、使ってよいツール（None は接続のすべてのツール）。"""
    requirements: dict[str, set[str] | None] = {}
    for skill_id in skill_ids:
        skill = skill_registry.get(skill_id)
        if skill is None or not skill.enabled:
            continue
        for requirement in skill.mcp_requirements:
            server_id = requirement.server_id.strip()
            if not server_id or server_id == CONTROL_PLANE_SERVER_ID:
                continue
            if not requirement.tool_names:
                requirements[server_id] = None
                continue
            current = requirements.get(server_id, set())
            if current is None:
                continue
            requirements[server_id] = current | set(requirement.tool_names)
    return requirements


McpRuntimeTool = tuple[ToolDefinition, ToolHandler]


def discover_mcp_tools(
    skill_ids: list[str], *, context: ToolInvocationContext
) -> tuple[list[McpRuntimeTool], list[str]]:
    """Skill が必要とする MCP 接続のツールを取得する。取得できない接続は飛ばして理由を返す。"""
    tools: list[McpRuntimeTool] = []
    warnings: list[str] = []
    for server_id, allowed in agent_mcp_requirements(skill_ids).items():
        try:
            config = runtime_config_store.get_mcp(server_id)
        except KeyError:
            warnings.append(f"MCP 接続「{server_id}」は登録されていません。")
            continue
        try:
            listed = list_mcp_connection_tools(server_id, context=context)
        except ExternalToolError as exc:
            warnings.append(
                f"MCP 接続「{config.label or server_id}」のツールを取得できません: {exc}"
            )
            continue
        for tool in listed.tools:
            if allowed is not None and tool.name not in allowed:
                continue
            tools.append((mcp_tool_definition(config, tool), mcp_tool_handler(config, tool)))
    return tools, warnings


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
        # 本文へ全参照文書を詰めず、割り当て済みの文書の索引だけを渡す（#862）。
        from app.features.agent.plugins import plugin_resource_registry

        for resource_id in skill.resource_ids:
            resource = plugin_resource_registry.get(resource_id)
            if (
                resource
                and resource.metadata.get("external_skill_reference")
                and isinstance(resource.content, str)
            ):
                sections.append(
                    f"参照文書 {resource.name}: resource_id={resource.id}。"
                    "必要な部分だけ skill_reference_read で読んでください。"
                )
    return "\n\n".join(sections)


def _strict_schema(schema: dict[str, Any]) -> bool:
    """SDK の strict mode に使えるか（全 property が required・追加 property なし）。"""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    required = set(schema.get("required") or [])
    return schema.get("additionalProperties") is False and required == set(properties)


class _ToolRecorder:
    """function tool の実行を Run の step・イベント・成果物に記録する。

    `budget` があれば、呼ぶ前に支援タスクの予算を数え、超える呼び出しは実行せずに
    `budget_exceeded` の結果を返す（#1243）。
    """

    def __init__(self, run_id: str, budget: SupportTaskBudget | None = None) -> None:
        self.run_id = run_id
        self.budget = budget

    async def invoke(
        self,
        name: str,
        arguments: str,
        call_id: str,
        *,
        definition: ToolDefinition | None = None,
        handler: ToolHandler | None = None,
    ) -> str:
        from app.features.agent.runtime import runtime_repository

        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            parsed = {}
        call = ToolCall(
            name=name, arguments=parsed if isinstance(parsed, dict) else {}, trace_id=call_id
        )
        # 予算は await の前に数える（同じ応答の並列の呼び出しでも上限を超えない）。
        exceeded = self.budget.reserve(name) if self.budget is not None else None
        step_id, context = runtime_repository.start_builtin_tool_step(self.run_id, call)
        if exceeded is not None:
            message = budget_exceeded_message(name, exceeded)
            runtime_repository.finish_builtin_tool_step(
                self.run_id,
                step_id,
                ToolResult(
                    name=name,
                    success=False,
                    error=message,
                    error_code=BUDGET_EXCEEDED_CODE,
                    error_details={"budget": exceeded},
                ),
            )
            return json.dumps(
                {"error": message, "error_code": BUDGET_EXCEEDED_CODE, "budget": exceeded},
                ensure_ascii=False,
            )
        # 承認は SDK の needs_approval で済んでいる（拒否のツールは渡していない）。
        result = await self.execute(step_id, call, context, definition=definition, handler=handler)
        if result.success:
            return json.dumps(result.output or {}, ensure_ascii=False, default=str)
        return json.dumps(
            {"error": result.error or "tool failed", "error_code": result.error_code},
            ensure_ascii=False,
        )

    async def call(
        self,
        call: ToolCall,
        *,
        definition: ToolDefinition | None = None,
        handler: ToolHandler | None = None,
    ) -> tuple[str, ToolResult]:
        """Control Plane が自分で呼ぶツール（回答の最終の検証。#1246）を step に記録して呼ぶ。

        予算は数えない。ポリシーの「拒否」は守り、「承認」は待たない（読み取りの検証のため）。
        """
        from app.features.agent.runtime import runtime_repository

        step_id, context = runtime_repository.start_builtin_tool_step(self.run_id, call)
        result = await self.execute(step_id, call, context, definition=definition, handler=handler)
        return step_id, result

    async def execute(
        self,
        step_id: str,
        call: ToolCall,
        context: ToolInvocationContext,
        *,
        definition: ToolDefinition | None = None,
        handler: ToolHandler | None = None,
    ) -> ToolResult:
        from app.features.agent.runtime import runtime_repository

        result = await asyncio.to_thread(
            tool_registry.invoke,
            call,
            policy=_active_policy(),
            context=context,
            force=True,
            definition=definition,
            handler=handler,
        )
        runtime_repository.finish_builtin_tool_step(self.run_id, step_id, result)
        return result


def build_function_tools(
    run_id: str,
    tool_names: list[str],
    mcp_tools: list[McpRuntimeTool] | None = None,
    resource_ids: list[str] | None = None,
    budget: SupportTaskBudget | None = None,
) -> list[FunctionTool]:
    policy = _active_policy()
    recorder = _ToolRecorder(run_id, budget)
    entries: list[tuple[ToolDefinition, ToolHandler | None]] = []
    for name in tool_names:
        registered = tool_registry.get(name)
        if registered is not None:
            entries.append((registered, None))
    entries.extend(mcp_tools or [])
    if resource_ids:
        from app.features.agent.skill_resources import reference_tool

        entries.append(reference_tool(resource_ids))
    tools: list[FunctionTool] = []
    seen: set[str] = set()
    for definition, handler in entries:
        if definition.name in seen:
            continue
        seen.add(definition.name)
        decision = policy.decide(definition)
        if decision == ToolPolicyDecision.DENY:
            continue

        async def on_invoke(
            ctx: ToolContext[Any],
            arguments: str,
            *,
            _definition: ToolDefinition = definition,
            _handler: ToolHandler | None = handler,
        ) -> str:
            # 引数の型を ToolContext にすると、SDK は呼び出し ID（承認の記録と結び付ける）を渡す。
            call_id = str(getattr(ctx, "tool_call_id", "") or "")
            if _handler is None:
                return await recorder.invoke(_definition.name, arguments, call_id)
            return await recorder.invoke(
                _definition.name,
                arguments,
                call_id,
                definition=_definition,
                handler=_handler,
            )

        schema = dict(definition.input_schema)
        tools.append(
            FunctionTool(
                name=definition.name,
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
    run_id: str,
    *,
    name: str,
    instructions: str,
    skill_ids: list[str],
    model_id: str,
    agent_id: str | None = None,
    user_uuid: str | None = None,
    budget: SupportTaskBudget | None = None,
    support_task: str = "",
) -> Agent[Any]:
    """SDK の Agent を作る（MCP 接続のツール一覧を HTTP で取るので、イベントループの外で呼ぶ）。

    `support_task` は前の Run から引き継いだ支援タスクの状態（指示の末尾に足す。#1243）。
    """
    from app.features.agent.runtime import runtime_repository
    from app.features.agent.skill_resources import skill_reference_ids

    target = resolve_model_target(model_id)
    mcp_tools, warnings = discover_mcp_tools(
        skill_ids,
        context=ToolInvocationContext(run_id=run_id, agent_id=agent_id, user_uuid=user_uuid),
    )
    for warning in warnings:
        runtime_repository.note_builtin_warning(run_id, warning)
    composed = compose_instructions(instructions, skill_ids)
    if support_task:
        composed = f"{composed}\n\n{support_task}"
    return Agent(
        name=name or "agent",
        instructions=composed,
        tools=list(
            build_function_tools(
                run_id,
                agent_tool_names(skill_ids),
                mcp_tools,
                resource_ids=skill_reference_ids(skill_ids),
                budget=budget,
            )
        ),
        model=model_factory(target),
        model_settings=ModelSettings(store=False),
    )


# 会話の続きでモデルに渡す前の往復の数（#768。古いものから落とす）。
THREAD_HISTORY_TURNS = 10


def conversation_input(run_id: str, goal: str) -> str | list[Any]:
    """同じ会話の前の質問と回答を付けた入力（会話が無ければ質問の文字列だけ。#768）。"""
    from app.features.agent.runtime import runtime_repository

    history = runtime_repository.thread_history(run_id, limit=THREAD_HISTORY_TURNS)
    if not history:
        return goal
    items: list[Any] = []
    for question, answer in history:
        items.append({"role": "user", "content": question})
        items.append({"role": "assistant", "content": answer})
    items.append({"role": "user", "content": goal})
    return items


@dataclass
class _SupportTaskRun:
    """1 回の実行（開始・承認後の再開）の支援タスク（#1243）。"""

    goal: str
    previous: dict[str, Any] | None
    budget: SupportTaskBudget
    instructions: str
    max_rag_calls_per_run: int
    max_tool_calls_per_task: int


def _support_task_run(run: Any) -> _SupportTaskRun:
    """前の Run の状態と、この Run の step から予算の残りを作る。

    再開した Run は、それまでの step を数えるので消費を 0 に戻さない。
    """
    from app.features.agent.runtime import runtime_repository

    settings = get_settings()
    max_rag = int(settings.agent_max_rag_calls_per_run)
    max_task = int(settings.agent_max_tool_calls_per_task)
    goal, previous = runtime_repository.support_task_context(run.id)
    return _SupportTaskRun(
        goal=goal,
        previous=previous,
        budget=SupportTaskBudget.for_run(
            list(run.steps),
            previous,
            max_rag_calls_per_run=max_rag,
            max_tool_calls_per_task=max_task,
        ),
        instructions=support_task_instructions(
            previous,
            goal=goal,
            max_rag_calls_per_run=max_rag,
            max_tool_calls_per_task=max_task,
        ),
        max_rag_calls_per_run=max_rag,
        max_tool_calls_per_task=max_task,
    )


def _save_support_task(run_id: str, task: _SupportTaskRun) -> None:
    """支援タスクの状態を Run の成果物に残す（ツールを呼ばず、引き継ぐ状態も無ければ残さない）。

    状態は補助のため、作れなくても Run は止めない。
    """
    from app.features.agent.runtime import runtime_repository

    try:
        run = runtime_repository.get_run(run_id)
        if task.previous is None and not has_support_task_activity(run.steps):
            return
        content = build_support_task(
            task.previous,
            steps=run.steps,
            run_id=run.id,
            thread_id=run.thread_id,
            owner_user_uuid=run.created_by_user_uuid,
            goal=task.goal,
            max_rag_calls_per_run=task.max_rag_calls_per_run,
            max_tool_calls_per_task=task.max_tool_calls_per_task,
        )
        runtime_repository.save_support_task(run_id, content)
    except Exception as exc:  # noqa: BLE001 - 状態は補助。残せなくても回答は返す
        logger.warning(
            "builtin_runtime_support_task_failed",
            extra={"run_id": run_id, "exception_type": type(exc).__name__},
        )


async def execute_run(run_id: str) -> None:
    """Run を最初から実行する（作成直後・dispatcher から呼ぶ）。"""
    with bind_log_context(run_id=run_id):
        from app.features.agent.runtime import runtime_repository

        started = runtime_repository.begin_builtin_run(run_id)
        if started is None:
            return
        run, agent = started
        try:
            task = _support_task_run(run)
            sdk_agent = await asyncio.to_thread(
                build_sdk_agent,
                run_id,
                name=agent.name,
                instructions=agent.instructions,
                skill_ids=agent.skill_ids,
                model_id=agent.model_id,
                agent_id=agent.id,
                user_uuid=run.created_by_user_uuid,
                budget=task.budget,
                support_task=task.instructions,
            )
            result = await Runner.run(
                sdk_agent, conversation_input(run_id, run.goal), max_turns=_max_turns()
            )
            result = await _dry_run_approvals(run, sdk_agent, result)
            _record_usage(run_id, result, agent.model_id)
            await _finish(
                run_id, result, task, rag_tools=has_rag_evidence_tools(sdk_agent, agent.skill_ids)
            )
        except Exception as exc:  # noqa: BLE001 - 実行の境界では失敗を Run に記録する
            _record_usage(run_id, getattr(exc, "run_data", None), agent.model_id)
            _record_failure(run_id, exc)


async def resume_run(run_id: str) -> None:
    """承認がすべて決まった Run を、保存した SDK の状態から再開する。"""
    with bind_log_context(run_id=run_id):
        from app.features.agent.runtime import runtime_repository

        resumed = runtime_repository.begin_builtin_resume(run_id)
        if resumed is None:
            return
        run, agent, state_text, decisions = resumed
        try:
            task = _support_task_run(run)
            sdk_agent = await asyncio.to_thread(
                build_sdk_agent,
                run_id,
                name=agent.name,
                instructions=agent.instructions,
                skill_ids=agent.skill_ids,
                model_id=agent.model_id,
                agent_id=agent.id,
                user_uuid=run.created_by_user_uuid,
                budget=task.budget,
                support_task=task.instructions,
            )
            state = await RunState.from_string(sdk_agent, state_text)
            for item in state.get_interruptions():
                call_id = str(getattr(item, "call_id", "") or "")
                if decisions.get(call_id, False):
                    state.approve(item)
                else:
                    state.reject(item)
            result = await Runner.run(sdk_agent, state, max_turns=_max_turns())
            result = await _dry_run_approvals(run, sdk_agent, result)
            _record_usage(run_id, result, agent.model_id)
            await _finish(
                run_id, result, task, rag_tools=has_rag_evidence_tools(sdk_agent, agent.skill_ids)
            )
        except Exception as exc:  # noqa: BLE001 - 実行の境界では失敗を Run に記録する
            _record_usage(run_id, getattr(exc, "run_data", None), agent.model_id)
            _record_failure(run_id, exc)


async def _dry_run_approvals(run: Any, sdk_agent: Agent[Any], result: Any) -> Any:
    """品質評価の Run（#776）は、承認が要るツールを実行せずに続ける（dry-run）。

    呼ぼうとしたツールは step に残し（ツールの選択の判定に使う）、モデルへは「評価中のため実行して
    いない」と返して回答を完成させる。評価以外の Run はそのまま返す（承認待ちにする）。
    """
    from app.features.agent.runtime import (
        EVALUATION_DRY_RUN_KEY,
        EVALUATION_DRY_RUN_MESSAGE,
        runtime_repository,
    )

    if not run.metadata.get(EVALUATION_DRY_RUN_KEY):
        return result
    for _ in range(_max_turns()):
        interruptions = list(getattr(result, "interruptions", []) or [])
        if not interruptions:
            return result
        runtime_repository.record_builtin_dry_run_steps(
            run.id,
            [
                ToolCall(
                    name=str(getattr(item, "tool_name", "") or getattr(item, "name", "") or "tool"),
                    arguments=_arguments(getattr(item, "arguments", None)),
                    trace_id=str(getattr(item, "call_id", "") or ""),
                )
                for item in interruptions
            ],
        )
        state = result.to_state()
        for item in interruptions:
            state.reject(item, rejection_message=EVALUATION_DRY_RUN_MESSAGE)
        result = await Runner.run(sdk_agent, state, max_turns=_max_turns())
    return result


async def _finish(
    run_id: str, result: Any, task: _SupportTaskRun | None = None, *, rag_tools: bool = False
) -> None:
    from app.features.agent.runtime import runtime_repository

    # 承認待ちで止めるときも残す（再開した Run は同じ成果物を上書きする。#1243）。
    if task is not None:
        _save_support_task(run_id, task)
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
    if get_settings().agent_final_validation_enabled:
        try:
            answer = await _validate_final_answer(run_id, answer, rag_tools=rag_tools)
        except Exception as exc:  # noqa: BLE001 - 検証の失敗で回答を落とさない
            logger.warning(
                "builtin_runtime_answer_validation_failed",
                extra={"run_id": run_id, "exception_type": type(exc).__name__},
            )
            # 確かめられなかったことを成果物にも残す（#1277。黙って通さない）。
            runtime_repository.save_builtin_artifact(
                run_id,
                kind=ANSWER_VALIDATION_KIND,
                name=ANSWER_VALIDATION_NAME,
                content=validation_content(
                    STATUS_UNVALIDATED,
                    reason=REASON_VALIDATION_ERROR,
                    message="回答の検証の途中で予期しない失敗が起きました。",
                ),
            )
            answer = with_unverified_notice(answer)
    runtime_repository.complete_builtin_run(run_id, answer)


def _validator_connection(evidence_tool: str) -> McpConnectionConfig | None:
    """根拠を返したツール（`<接続>__rag_search` など）の MCP 接続。"""
    base = mcp_base_tool_name(evidence_tool)
    for config in runtime_config_store.list_mcp_servers():
        if mcp_function_name(config.server_id, base) == evidence_tool:
            return config
    return None


def has_rag_evidence_tools(sdk_agent: Agent[Any] | None, skill_ids: list[str]) -> bool:
    """Agent が RAG の根拠のツール（`rag_search` / `rag_retrieve_evidence`）を持つか（#1277）。

    Run に渡したツールにあるか、Skill の requirement が名前で求めていれば持つとみなす（RAG の
    接続のツールを取れなかった Run でも、根拠を使えなかった回答として注記するため）。
    """
    tools = list(getattr(sdk_agent, "tools", []) or [])
    if any(mcp_base_tool_name(str(getattr(tool, "name", ""))) in EVIDENCE_TOOLS for tool in tools):
        return True
    return any(
        allowed is not None and bool(allowed & EVIDENCE_TOOLS)
        for allowed in agent_mcp_requirements(skill_ids).values()
    )


async def _validate_final_answer(run_id: str, answer: str, *, rag_tools: bool = False) -> str:
    """回答の最終の検証（#1246・#1277）。検証の結果を成果物に残し、利用者に見せる回答を返す。

    モデルではなく Control Plane が、根拠を返した RAG の MCP 接続ごとに `rag_validate_answer` を
    呼ぶ（ポリシー・監査・Run の利用者のサービストークン・step はモデルのツールと同じ境界）。
    判定が valid でなければ確かめられなかった段落を外して理由を足し、検証が失敗したら
    「検証できませんでした」を足す（回答は消さない。`answer_validation.combine_validations`）。
    `rag_tools` は Agent が RAG の根拠のツールを持つか（根拠の無い回答に注記するか）。
    """
    from app.features.agent.runtime import runtime_repository

    def save(content: dict[str, Any]) -> None:
        runtime_repository.save_builtin_artifact(
            run_id, kind=ANSWER_VALIDATION_KIND, name=ANSWER_VALIDATION_NAME, content=content
        )

    run = runtime_repository.get_run(run_id)
    groups = run_evidence_groups(run.steps)
    if not groups:
        if rag_tools and answer.strip():
            save(
                validation_content(
                    STATUS_UNVALIDATED, reason=REASON_NO_RAG_EVIDENCE, message=NO_EVIDENCE_NOTICE
                )
            )
            return with_no_evidence_notice(answer)
        save(validation_content(STATUS_SKIPPED, reason=REASON_NO_RAG_EVIDENCE))
        return answer
    refs = [ref for _tool, items in groups for ref in items]
    if not answer.strip():
        save(validation_content(STATUS_SKIPPED, reason=REASON_EMPTY_ANSWER, evidence=refs))
        return answer
    validations = [
        await _validate_with_connection(run, answer, evidence_tool, items)
        for evidence_tool, items in groups
    ]
    content, published = combine_validations(answer, validations)
    save(content)
    return published


async def _validate_with_connection(
    run: Any, answer: str, evidence_tool: str, refs: list[dict[str, Any]]
) -> dict[str, Any]:
    """1 つの MCP 接続の根拠で `rag_validate_answer` を呼び、接続ごとの内容を返す。

    判定が出たら `completed`、呼べない・失敗した・判定として使えない応答なら `unvalidated`。
    """

    def unvalidated(reason: str, message: str | None, **extra: Any) -> dict[str, Any]:
        return validation_content(
            STATUS_UNVALIDATED, reason=reason, message=message, evidence=refs, **extra
        )

    config = _validator_connection(evidence_tool)
    if config is None:
        return unvalidated(REASON_CONNECTION_NOT_FOUND, "根拠を返した MCP 接続が見つかりません。")
    connection: dict[str, Any] = {"connection": config.server_id}
    if len(answer) > MAX_ANSWER_CHARS:
        return unvalidated(
            REASON_ANSWER_TOO_LONG,
            f"回答が長すぎるため検証できません（{MAX_ANSWER_CHARS} 文字まで）。",
            **connection,
        )
    context = ToolInvocationContext(
        run_id=run.id, agent_id=run.agent_id, user_uuid=run.created_by_user_uuid
    )
    try:
        listed = await asyncio.to_thread(
            list_mcp_connection_tools, config.server_id, context=context
        )
    except KeyError:
        return unvalidated(
            REASON_CONNECTION_NOT_FOUND, "根拠を返した MCP 接続が見つかりません。", **connection
        )
    except ExternalToolError as exc:
        return unvalidated(exc.code, exc.message, **connection)
    tool = next((item for item in listed.tools if item.name == VALIDATE_ANSWER_TOOL), None)
    if tool is None:
        return unvalidated(
            REASON_VALIDATOR_UNAVAILABLE,
            f"MCP 接続「{config.label or config.server_id}」は回答の検証を提供していません。",
            **connection,
        )
    definition = mcp_tool_definition(config, tool)
    step_id, result = await _ToolRecorder(run.id).call(
        ToolCall(
            name=definition.name,
            arguments={
                "query": run.goal[:MAX_QUERY_CHARS],
                "answer": answer,
                "evidence": refs,
                # 要求の充足・業務ガイドの手順と影響範囲の決定的な検査（#1276）。
                **connection_check_inputs(run.steps, evidence_tool),
            },
            trace_id=f"answer_validation_{run.id}_{config.server_id}",
        ),
        definition=definition,
        handler=mcp_tool_handler(config, tool),
    )
    common: dict[str, Any] = {**connection, "tool_name": definition.name, "step_id": step_id}
    if not result.success or not isinstance(result.output, dict):
        reason = result.error_code or (
            "policy_denied" if result.policy_decision == ToolPolicyDecision.DENY else "tool_failed"
        )
        return unvalidated(reason, result.error, **common)
    if not usable_result(result.output):
        return unvalidated(
            REASON_UNUSABLE_RESULT,
            f"検証の結果（{result.output.get('status')}）を判定に使えません。",
            result=result.output,
            **common,
        )
    return validation_content(STATUS_COMPLETED, result=result.output, evidence=refs, **common)


def _record_usage(run_id: str, source: object, model_id: str) -> None:
    """SDK の累計の利用量を Run に記録する（#772）。

    `source` は `Runner.run` の結果か、失敗の `run_data`（SDK の例外が持つときだけ）。SDK の
    `Usage` は承認待ちで保存する RunState に入り、再開後も累計で続くため、毎回上書きでよい。
    """
    from app.features.agent.runtime import RunUsage, runtime_repository

    usage = getattr(getattr(source, "context_wrapper", None), "usage", None)
    if usage is None:
        return
    runtime_repository.record_builtin_usage(
        run_id,
        RunUsage(
            model=_model_label(model_id),
            requests=int(getattr(usage, "requests", 0) or 0),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        ),
    )


def _model_label(model_id: str) -> str:
    """利用量に残すモデル名（Agent の指定が空なら、その時点の既定のテキストモデル）。"""
    explicit = (model_id or "").strip()
    if explicit:
        return explicit
    try:
        return enterprise_ai_default_model_id(get_settings()) or ""
    except Exception:  # noqa: BLE001 - モデル名は記録の補助。取れなくても利用量は残す
        return ""


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
        # OCI の error code（`invalid_value` など）まで残す（型名だけでは原因が分からない。#1215）。
        model_code = model_error_code(exc)
        reason = f"{type(exc).__name__}: {model_code}" if model_code else type(exc).__name__
        message = f"モデルの呼び出しに失敗しました（{reason}）。"
    logger.warning(
        "builtin_runtime_failed",
        extra={
            "run_id": run_id,
            "error_code": code,
            "exception_type": type(exc).__name__,
            "model_error_code": model_error_code(exc),
            # OCI の API のメッセージ（モデル名と理由。業務データ・資格情報は含まない）。
            "model_error_message": _model_error_message(exc),
        },
    )
    runtime_repository.fail_builtin_run(run_id, code=code, detail=message)


def _model_error_message(exc: BaseException) -> str | None:
    if not isinstance(exc, APIStatusError):
        return None
    body = exc.body
    if isinstance(body, dict):
        nested = body.get("error") if isinstance(body.get("error"), dict) else body
        message = nested.get("message") if isinstance(nested, dict) else None
        if isinstance(message, str):
            return message[:500]
    return None


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
