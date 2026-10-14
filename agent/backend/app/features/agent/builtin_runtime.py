"""組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI の Responses API。#754）。

Control Plane の中で業務 Agent を実行する。外部の Runtime・Binding・Docker は使わない。

- モデル: システム設定 > モデル の OCI Enterprise AI（Agent の `model_id`、無ければ既定の
  テキストモデル）。
  SDK の tracing は無効にする（業務データを外部へ送らない）。
- 指示: Agent の指示と、割り当てた Skill の指示を合わせる。指示の中の MCP のツールの素の名前は、
  モデルに渡す名前（`<接続>__<ツール>`）に書き直す。無い名前のツールを呼んだら、呼ぶ名前をモデルへ
  返して呼び直させる（Run は落とさない。#1303）。
- ツール: Skill が必要とするツールを function tool にする（#757）。`control-plane` は
  `tool_registry` のツール、それ以外の server_id は MCP 接続（RAG / NL2SQL / 外部 MCP）の
  `tools/list` のツール（名前は `<接続>__<ツール>`、一覧は Run の利用者の権限で絞られる）。
  実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・サービストークン）を通す。
  ポリシーが「拒否」のツールは渡さない。「承認」のツールは SDK の `needs_approval` で中断する
  （MCP のツールは readOnlyHint が無ければ既定で承認が必要）。
- 承認: 中断した Run は SDK の状態を Run に保存して `waiting_approval` にする。すべての承認が
  決まったら状態を復元し、承認・却下を反映して再開する。無人の Run（品質評価・自動実行）は
  承認待ちにせず、承認が要るツールを却下してモデルへ理由を返し、回答まで続ける（#776・#1813）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast

from agents import (
    Agent,
    FunctionTool,
    MaxTurnsExceeded,
    Model,
    ModelSettings,
    OpenAIResponsesModel,
    RunConfig,
    RunContextWrapper,
    Runner,
    RunState,
    set_tracing_disabled,
)
from agents.run_config import ToolErrorFormatterArgs
from agents.tool_context import ToolContext
from openai import APIStatusError, AsyncOpenAI, BadRequestError
from openai.types.shared import Reasoning, ReasoningEffort
from pr_backend_core.chat_history import (
    CHAT_HISTORY_UNTRUSTED_NOTE,
    ChatHistoryBudget,
    build_chat_history,
)
from pr_backend_core.observability.request_context import bind_log_context
from pr_system_settings.enterprise_ai_errors import (
    enterprise_ai_incomplete_from_response,
    enterprise_ai_incomplete_message,
)
from pr_system_settings.model import (
    DEFAULT_ENTERPRISE_AI_MAX_OUTPUT_TOKENS,
    enterprise_ai_connection_for_model,
    enterprise_ai_default_model_id,
    enterprise_ai_model_catalog,
    enterprise_ai_output_token_budget,
    enterprise_ai_reasoning_effort,
    enterprise_ai_seed,
    reasoning_effort_to_send,
    seed_to_send,
)

from app.features.agent import data_scope, tool_grounding
from app.features.agent.answer_outcome import answer_outcome
from app.features.agent.answer_passages import is_absence_only, is_clarification_only
from app.features.agent.answer_validation import (
    ANSWER_VALIDATION_KIND,
    ANSWER_VALIDATION_NAME,
    EVIDENCE_TOOLS,
    MAX_ANSWER_CHARS,
    MAX_QUERY_CHARS,
    NO_EVIDENCE_NOTICE,
    REASON_ABSENCE_ONLY,
    REASON_ANSWER_TOO_LONG,
    REASON_CLARIFICATION_ONLY,
    REASON_CONNECTION_NOT_FOUND,
    REASON_EMPTY_ANSWER,
    REASON_NO_RAG_EVIDENCE,
    REASON_RAG_DECLINED,
    REASON_UNUSABLE_RESULT,
    REASON_VALIDATION_ERROR,
    REASON_VALIDATOR_UNAVAILABLE,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    STATUS_UNVALIDATED,
    VALIDATE_ANSWER_TOOL,
    carried_evidence,
    carried_evidence_groups,
    combine_validations,
    connection_check_inputs,
    run_evidence_groups,
    usable_result,
    validation_content,
    with_no_evidence_notice,
    with_unverified_notice,
)
from app.features.agent.clarification_cancel import (
    CANCEL_METADATA_KEY,
    CANCEL_TRACE_PREFIX,
    CLARIFICATION_KINDS,
    KIND_NL2SQL,
    KIND_RAG,
    NL2SQL_CANCEL_TOOL,
    TRIGGER_BUTTON,
    TRIGGER_MESSAGE,
    button_cancel_kinds,
    cancel_instructions,
    cancel_record,
    declined_kinds,
    is_declined_tool,
    message_cancel_kinds,
    pending_of,
)
from app.features.agent.config import McpConnectionConfig, runtime_config_store
from app.features.agent.confirmation import (
    CONFIRMATION_TOOL,
    POLICY_ASSUME,
    RESPONSE_METADATA_KEY,
    SOURCE_AGENT,
    SOURCE_NL2SQL,
    SOURCE_RAG,
    ConfirmationBudget,
    ConfirmationRequest,
    agent_confirmation_skipped,
    confirmation_gate,
    confirmation_tool,
    ground_options,
    item_from_arguments,
    items_from_pending,
    nl2sql_context,
    policy_instructions,
    rag_conditions,
    response_instructions,
    used_rounds,
    validation_premises,
)
from app.features.agent.skills import (
    reset_pinned_skills,
    resolve_skill,
    use_pinned_skills,
)
from app.features.agent.support_task import (
    ADJUSTED_LIMITS_HINT,
    BUDGET_EXCEEDED_CODE,
    GUIDE_CHECK_TRACE_PREFIX,
    NL2SQL_QUERY,
    RAG_BUDGET_TOOLS,
    RAG_LOOKUP_GUIDES,
    RAG_READ_DOCUMENT,
    RAG_READ_SECTION,
    RAG_READ_SOURCE,
    RAG_RETRIEVE_EVIDENCE,
    RAG_SEARCH,
    SupportTaskBudget,
    adjusted_search_limits,
    assume_step,
    budget_exceeded_message,
    build_support_task,
    guide_check_note,
    has_support_task_activity,
    is_environment_tool,
    nl2sql_clarification_pending,
    nl2sql_next_step,
    nl2sql_skip_step,
    rag_next_step,
    record_codes,
    repeated_query_note,
    search_limit_schema,
    section_read_note,
    superseded_versions_note,
    support_task_instructions,
    text_references,
    tool_clarification_pending,
)
from app.features.agent.tools import (
    MCP_TOOL_SEPARATOR,
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
    # 推論（思考）の量（#1425。システム設定 > モデル の登録モデルの値）。none は送らない。
    reasoning_effort: str = ""
    # LLM の呼び出しの seed（#1434。システム設定 > モデル の共通の値、既定 42）。
    seed: int = 42
    # 1 回の呼び出しの出力の上限（#1719。システム設定 > モデル の登録モデルの値。既定 32768）。
    max_output_tokens: int = DEFAULT_ENTERPRISE_AI_MAX_OUTPUT_TOKENS

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
        # OpenAI 互換 API の base URL（Endpoint URL を入力どおり。#1440。platform の関数）。
        endpoint=connection.base_url,
        project_ocid=connection.project_ocid,
        api_key=connection.api_key,
        reasoning_effort=enterprise_ai_reasoning_effort(settings, resolved),
        seed=enterprise_ai_seed(settings),
        # モデルの出力の上限と入力の上限の小さい方（#1719。prompt は会話で伸びるので見積もらない）。
        max_output_tokens=enterprise_ai_output_token_budget(settings, resolved),
    )


def model_settings_for(target: ModelTarget) -> ModelSettings:
    """Run・評価の判定に使う SDK の ModelSettings（#1425 / #1434）。

    - モデルの推論（思考）の量を Responses API の `reasoning: {"effort": ...}` で送る
      （none は送らない。解釈は platform の `reasoning_effort_to_send`）
    - seed を top-level の `seed` で送る（Responses API に seed の引数が無いため、SDK の
      `extra_body`。解釈は platform の `seed_to_send`）
    - モデルの出力の上限を `max_output_tokens` で送る（#1719。SDK の `max_tokens`。送らないと
      endpoint の既定の上限になり、推論のあるモデルは推論だけで上限に達することがある）
    """
    extra_body = {"seed": seed_to_send(target.seed)}
    max_tokens = max(1, int(target.max_output_tokens))
    effort = reasoning_effort_to_send(target.reasoning_effort)
    if not effort:
        return ModelSettings(store=False, max_tokens=max_tokens, extra_body=extra_body)
    # 値は共通のモデル設定（pr_system_settings）が SDK と同じ候補に絞って保存している。
    return ModelSettings(
        store=False,
        reasoning=Reasoning(effort=cast(ReasoningEffort, effort)),
        max_tokens=max_tokens,
        extra_body=extra_body,
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


MODEL_OUTPUT_INCOMPLETE_CODE = "runtime.model_output_incomplete"


def _raise_for_incomplete(response: Any, request: Mapping[str, Any]) -> None:
    """未完了の応答（`status=incomplete`）を、理由・上限・使った token の分かる失敗にする（#1719）。

    SDK は未完了を `ModelBehaviorError`（型名だけが Run の失敗に残る）にするため、その前に
    platform の `enterprise_ai_incomplete_message` の文で失敗させる。送った上限はもうモデルの
    出力の上限（`max_output_tokens`）なので、広げて送り直さない。
    """
    if getattr(response, "status", None) != "incomplete":
        return
    data = response.model_dump() if hasattr(response, "model_dump") else response
    if not isinstance(data, Mapping):
        return
    limit = request.get("max_output_tokens")
    incomplete = enterprise_ai_incomplete_from_response(
        data, max_output_tokens=limit if isinstance(limit, int) else None
    )
    if incomplete is None:
        return
    raise BuiltinRuntimeError(
        MODEL_OUTPUT_INCOMPLETE_CODE,
        enterprise_ai_incomplete_message(incomplete, model_id=str(request.get("model") or "")),
    )


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
                response = await self._inner.create(**request)
                if not request.get("stream"):
                    _raise_for_incomplete(response, request)
                return response
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
        skill = resolve_skill(skill_id)
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
        skill = resolve_skill(skill_id)
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


def approval_required_tools(
    skill_ids: list[str], *, context: ToolInvocationContext
) -> tuple[list[str], list[str]]:
    """スキルが使うツールのうち、ツールのポリシーで承認が要るもの（#1813）。

    自動実行の編集画面の警告に使う（自動実行の Run はこれらのツールを呼ばない）。判定は Run の
    `build_function_tools` と同じ `ToolPolicy.decide`（拒否のツールはモデルに渡さないので
    含めない）。
    MCP 接続のツールは `tools/list` で取る。取れない接続は理由を 2 つ目の値で返す。
    """
    from app.features.agent.skill_references import reference_tool, skill_reference_keys

    policy = _active_policy()
    mcp_tools, warnings = discover_mcp_tools(skill_ids, context=context)
    definitions: list[ToolDefinition] = [
        definition
        for name in agent_tool_names(skill_ids)
        if (definition := tool_registry.get(name)) is not None
    ]
    definitions.extend(definition for definition, _handler in mcp_tools)
    keys = skill_reference_keys(skill_ids)
    if keys:
        definitions.append(reference_tool(keys)[0])
    names: list[str] = []
    for definition in definitions:
        if definition.name in names:
            continue
        if policy.decide(definition) == ToolPolicyDecision.ASK:
            names.append(definition.name)
    return names, warnings


# 指示の中のツールの名前の前後に来ない文字（function tool の名前に使える文字。#1303）。
_TOOL_NAME_BOUNDARY = "A-Za-z0-9_-"


def instruction_tool_names(skill_ids: list[str], exposed: Collection[str]) -> dict[str, str]:
    """指示に書いた MCP のツールの素の名前 → モデルに渡す名前（`<接続>__<ツール>`。#1303）。

    Skill の指示は MCP のツールを接頭辞の無い名前（`rag_search`）で書くが、モデルに渡す
    function tool の名前は `<接続>__<ツール>`（#757）。指示の名前のまま呼ぶと SDK が
    `ModelBehaviorError` で Run を落とすため、指示を Run のツールの名前で書き直す。

    対象は Skill の requirement が名前で宣言したツールのうち、この Run でモデルに渡すもの
    だけ（ポリシーで拒否したもの・一覧に無いものは書き換えない）。同じ素の名前が複数の接続に
    あって 1 つに決まらないものと、素の名前のままのツールがあるものは書き換えない。
    """
    exposed_names = set(exposed)
    candidates: dict[str, set[str]] = {}
    for skill_id in skill_ids:
        skill = resolve_skill(skill_id)
        if skill is None or not skill.enabled:
            continue
        for requirement in skill.mcp_requirements:
            server_id = requirement.server_id.strip()
            if not server_id or server_id == CONTROL_PLANE_SERVER_ID:
                continue
            for tool_name in requirement.tool_names:
                function_name = mcp_function_name(server_id, tool_name)
                if function_name in exposed_names and tool_name not in exposed_names:
                    candidates.setdefault(tool_name, set()).add(function_name)
    return {name: next(iter(found)) for name, found in candidates.items() if len(found) == 1}


def render_tool_names(text: str, names: Mapping[str, str]) -> str:
    """指示の中の素のツールの名前を、モデルに渡す名前に置き換える（語の一部は置き換えない）。"""
    if not names or not text:
        return text
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    pattern = re.compile(
        rf"(?<![{_TOOL_NAME_BOUNDARY}])({alternatives})(?![{_TOOL_NAME_BOUNDARY}])"
    )
    return pattern.sub(lambda match: names[match.group(1)], text)


def compose_instructions(
    agent_instructions: str,
    skill_ids: list[str],
    exposed_tool_names: Collection[str] | None = None,
) -> str:
    """Agent の指示と Skill の指示（AgentSkills の本文）を 1 つの system の指示にする。

    `exposed_tool_names`（この Run でモデルに渡すツールの名前）を渡すと、指示の中の MCP の
    ツールの素の名前を、モデルが呼ぶ名前（`<接続>__<ツール>`）に書き直す（#1303）。
    """
    exposed = exposed_tool_names or ()
    sections = [
        "あなたは業務 Agent です。日本語で、根拠を示して簡潔に回答してください。"
        "ツールの結果に無いことは推測で補わず、分からないと答えてください。"
        # 業務の事実はツールの結果だけで答える（#1835。ツールを呼ばずに作った回答は Control Plane
        # が差し戻し、それでも呼ばなければ拒答にする）。
        "業務の事実（規程・手順・数値・状態・記録など）は、ツールの結果だけで答えてください。"
        "ツールで確かめられないとき・業務の範囲外の質問のときは、一般的な知識で答えず、"
        "確かめられないため答えられないと答えてください。"
        "質問が曖昧で対象が分からないときは、確認の質問をしてください。"
    ]
    if agent_instructions.strip():
        agent_names = instruction_tool_names(skill_ids, exposed)
        sections.append(
            "# 業務の指示\n" + render_tool_names(agent_instructions.strip(), agent_names)
        )
    for skill_id in skill_ids:
        skill = resolve_skill(skill_id)
        if skill is None or not skill.enabled or not skill.instructions.strip():
            continue
        # Skill の指示は、その Skill が宣言した接続のツールの名前で書き直す。
        skill_names = instruction_tool_names([skill_id], exposed)
        sections.append(
            f"# Skill: {skill.name}\n{render_tool_names(skill.instructions.strip(), skill_names)}"
        )
        # 本文へ参照文書を詰めず、パスの索引だけを渡す（progressive disclosure。#862 / #1803）。
        for reference in skill.references:
            sections.append(
                f"参照文書 {reference.path}（skill_id={skill.id}、{reference.characters} 文字）。"
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


# 業務ガイドの照合（#1322）に渡す条件・質問の上限（`rag_lookup_guides` の契約の上限）。
GUIDE_CHECK_MAX_CONDITIONS = 30
GUIDE_CHECK_MAX_QUERY_CHARS = 8000


def _connection_prefix(function_name: str) -> str | None:
    """`<接続>__<ツール>` の接続の部分（MCP 接続のツールでなければ None）。"""
    if MCP_TOOL_SEPARATOR not in function_name:
        return None
    return function_name[: -len(mcp_base_tool_name(function_name)) - len(MCP_TOOL_SEPARATOR)]


def _guide_consulted(steps: list[Any], prefix: str) -> bool:
    """この Run で、その接続の業務ガイドを引いた（`rag_lookup_guides`・`rag_search` が成功）か。"""
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success:
            continue
        if _connection_prefix(call.name) != prefix:
            continue
        if mcp_base_tool_name(call.name) in {RAG_LOOKUP_GUIDES, RAG_SEARCH}:
            return True
    return False


def guide_check_query(goal: str, query: object) -> str:
    """業務ガイドの照合の質問（利用者の質問と、モデルが根拠を集めた質問。同じなら 1 つ）。

    条件の語（例: 検証用アカウント → 個別）は利用者の質問に、業務ガイドの語はモデルの質問に出る
    ことがあるので両方を渡す。
    """
    parts = [" ".join(goal.split())]
    if isinstance(query, str) and " ".join(query.split()) not in parts:
        parts.append(" ".join(query.split()))
    return "\n".join(part for part in parts if part)[:GUIDE_CHECK_MAX_QUERY_CHARS]


def support_task_goal(goal: str, previous: Mapping[str, Any] | None) -> str:
    """業務ガイドの照合の質問に使う利用者の質問（#1322）。

    前の Run で問いを確かめ中なら、利用者の新しい発言は問いへの答え（「個別の利用者に付けます」）
    なので、前の質問（目的）も含める（業務ガイドの語は前の質問に出る）。
    """
    if isinstance(previous, Mapping) and previous.get("pending_clarifications"):
        before = previous.get("goal")
        if isinstance(before, str) and before.strip() and before.strip() != goal.strip():
            return f"{before.strip()}\n{goal}"
    return goal


def support_task_known_conditions(previous: Mapping[str, Any] | None) -> dict[str, str]:
    """前の Run までに分かった条件（条件の id → 値）。"""
    known = previous.get("known_conditions") if isinstance(previous, Mapping) else None
    if not isinstance(known, Mapping):
        return {}
    conditions: dict[str, str] = {}
    for key, entry in known.items():
        value = entry.get("value") if isinstance(entry, Mapping) else None
        if isinstance(key, str) and isinstance(value, str) and value:
            conditions[key] = value
    return conditions


class _ToolRecorder:
    """function tool の実行を Run の step・イベント・成果物に記録する。

    `budget` があれば、呼ぶ前に支援タスクの予算を数え、超える呼び出しは実行せずに
    `budget_exceeded` の結果を返す（#1243）。

    `environment_tools` はこの Run でモデルに渡した、現場のデータを確かめる道具（RAG 以外の MCP
    接続のツール）。`rag_search` が回答を確定できないと返したら、モデルへの結果に次の手
    （`next_step`）を足す（#1283。記録する step の結果は RAG の結果のまま）。

    モデルが自分で `rag_lookup_guides` を呼んだときも、最上位の業務ガイドの判断から次の手
    （`next_step`。答える判断なら資料の根拠を集めてから答える）を結果に足す（#1322）。

    業務ガイドの照合（#1322）: モデルが業務ガイド（`rag_lookup_guides`・`rag_search`）を引かずに
    `rag_retrieve_evidence` で根拠を集めたら、Control Plane が同じ接続の `rag_lookup_guides` を
    1 回呼び（step に残す。予算には数えない）、最上位の業務ガイドと次の手（条件が分からなければ
    確認の質問）を根拠の結果に `guide_check` として足す。根拠を集める道具は業務ガイドの条件・分岐を
    見ないため、確かめる条件があってもモデルが分岐ごとに答えてしまう（#1317 の実環境の評価）。
    `guide_tools` はモデルに渡した `rag_lookup_guides`（接続の接頭辞 → 定義・handler）、
    `goal` は利用者の質問（前の Run で問いを確かめ中なら、その質問も含む）、`known_conditions` は
    前の Run までに分かった条件。
    """

    def __init__(self, run_id: str, budget: SupportTaskBudget | None = None) -> None:
        self.run_id = run_id
        self.budget = budget
        self.environment_tools: list[str] = []
        self.guide_tools: dict[str, tuple[ToolDefinition, ToolHandler | None]] = {}
        self.goal = ""
        self.known_conditions: dict[str, str] = {}
        # 業務 Agent のデータの範囲（#1378。接続 → 使えるプロファイル）。空は範囲なし。
        self.data_scopes: data_scope.AgentDataScopes = {}
        # 照合した（する）接続。並列の呼び出しでも 1 回だけにする。
        self._guide_checked: set[str] = set()
        # 回答前の確認（#1839）。利用者に確認しない Run では、RAG の確認の次の手を「前提を示して
        # 答える」に変える。
        self.confirmation: ConfirmationBudget | None = None

    def _without_asking(self, next_step: dict[str, Any] | None) -> dict[str, Any] | None:
        if self.confirmation is None or self.confirmation.asks:
            return next_step
        return assume_step(next_step)

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
        call_arguments: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        adjusted_limits: dict[str, dict[str, int]] = {}
        if definition is not None and mcp_base_tool_name(name) in RAG_BUDGET_TOOLS:
            # 既定より小さい件数（top_k・evidence_limit）は既定に引き上げ、上限を超える値は上限に
            # 丸める（#1351・#1403）。step には送った値を残し、モデルへの結果に直したことを足す。
            adjusted_limits = adjusted_search_limits(call_arguments, definition.input_schema)
            if adjusted_limits:
                call_arguments = {
                    **call_arguments,
                    **{key: value["sent"] for key, value in adjusted_limits.items()},
                }
                logger.info(
                    "builtin_runtime_search_limits_adjusted",
                    extra={
                        "run_id": self.run_id,
                        "tool_name": name,
                        "adjusted_limits": adjusted_limits,
                    },
                )
        # データの範囲（#1378）: プロファイルを埋める・上書きする・範囲外を拒否する。MCP 接続の
        # ツールだけ（Control Plane のツールは範囲の対象ではない）。
        scoped = (
            data_scope.enforce(name, call_arguments, self.data_scopes)
            if definition is not None
            else None
        )
        if scoped is not None:
            call_arguments = scoped.arguments
        call = ToolCall(
            name=name,
            arguments=call_arguments,
            trace_id=call_id,
            data_scope=scoped.note if scoped is not None else None,
        )
        if scoped is not None and scoped.error is not None:
            # 範囲外は呼び先へ送らない（予算にも数えない）。step には拒否を残す。
            return self.reject_out_of_scope(call, scoped)
        missing = (
            data_scope.missing_profile_error(name, call_arguments)
            if definition is not None
            else None
        )
        if missing is not None:
            # 必須のプロファイルが無い（#1379）: 呼び先へ送らずに、選ぶツールを案内する。
            return self.reject_call(call, missing)
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
        result = await self.execute(
            step_id, call, context, definition=definition, handler=handler, scoped=scoped
        )
        if result.success:
            output = result.output or {}
            base = mcp_base_tool_name(name)
            if base == RAG_SEARCH and isinstance(output, dict):
                next_step = self._without_asking(rag_next_step(output, self.environment_tools))
                if next_step is not None:
                    output = {**output, "next_step": next_step}
            if base == NL2SQL_QUERY and isinstance(output, dict):
                # SQL の生成前の確認の項目（#1441）: SQL を作る前に、利用者に 1 問ずつ聞かせる。
                # 利用者が「確認せずに回答」を選んだ Run（#1903）は、聞かずに skip_clarification で
                # 呼び直させる。
                if self.confirmation is not None and self.confirmation.skipped:
                    next_step = nl2sql_skip_step(output, call.arguments, tool_name=name)
                else:
                    next_step = nl2sql_next_step(output, call.arguments, tool_name=name)
                if next_step is not None:
                    output = {**output, "next_step": next_step}
            if base == RAG_RETRIEVE_EVIDENCE and isinstance(output, dict):
                note = await self.check_guides(name, call.arguments)
                if note is not None:
                    output = {**output, "guide_check": note}
            if base == RAG_LOOKUP_GUIDES and isinstance(output, dict):
                # モデルが自分で引いた業務ガイドにも次の手を足す（業務ガイドだけで答えさせない）。
                note = guide_check_note(output, evidence_gathered=False)
                if note is not None:
                    output = {**output, "next_step": self._without_asking(note["next_step"])}
            if isinstance(output, dict):
                output = self.hop_hints(
                    call, output, step_id=step_id, adjusted_limits=adjusted_limits
                )
            return json.dumps(output, ensure_ascii=False, default=str)
        return json.dumps(
            {"error": result.error or "tool failed", "error_code": result.error_code},
            ensure_ascii=False,
        )

    def reject_out_of_scope(self, call: ToolCall, scoped: data_scope.ScopeEnforcement) -> str:
        """範囲の外のプロファイルの呼び出しを、呼び先へ送らずにツールのエラーにする（#1378）。"""
        return self.reject_call(call, scoped.error_payload())

    def reject_call(self, call: ToolCall, payload: dict[str, Any]) -> str:
        """呼び先へ送らずにツールのエラーにする（step に拒否を残し、モデルへ案内を返す）。"""
        from app.features.agent.runtime import runtime_repository

        error_code = str(payload.get("error_code") or "")
        step_id, _context = runtime_repository.start_builtin_tool_step(self.run_id, call)
        runtime_repository.finish_builtin_tool_step(
            self.run_id,
            step_id,
            ToolResult(
                name=call.name,
                success=False,
                error=str(payload.get("error") or ""),
                error_code=error_code,
                error_details={key: value for key, value in payload.items() if key != "error"},
            ),
        )
        logger.info(
            "builtin_runtime_tool_call_rejected",
            extra={"run_id": self.run_id, "tool_name": call.name, "error_code": error_code},
        )
        return json.dumps(payload, ensure_ascii=False)

    def hop_hints(
        self,
        call: ToolCall,
        output: dict[str, Any],
        *,
        step_id: str,
        adjusted_limits: dict[str, dict[str, int]] | None = None,
    ) -> dict[str, Any]:
        """多段の質問の案内をモデルへの結果に足す（#1345。記録する step の結果は RAG のまま）。

        根拠を集める・読むツールの本文に同じ文書の別の箇所への参照があれば読む先（`references`）を、
        この Run で同じ（ほぼ同じ）query の検索を繰り返したら繰り返しを止める案内
        （`repeated_query`。#1351）を、query の実体に当たる台帳・一覧の行に略号・区分のような
        短い値があればその意味を引く次の段の案内（`record_codes`。#1365）を、Runtime が件数
        （`top_k`・`evidence_limit`）を既定に引き上げた・上限に丸めたらその値と次から省略する案内
        （`adjusted_limits`。#1403）を、質問が名指しする年度・版が当たった文書の旧版に当たり、
        旧版を検索していなければ旧版も含めた検索し直しの案内（`superseded_versions`。#1405）を、
        RAG の予算があればこの Run で残る検索の回数
        （`rag_calls_remaining`）を、根拠の章節が抜粋より長く質問が手順・一覧・条件のまとまりを
        求めるときは章節を読み切る案内（`section_reads`。#1438）を足す。
        """
        from app.features.agent.runtime import runtime_repository

        tool = mcp_base_tool_name(call.name)
        if tool not in RAG_BUDGET_TOOLS and tool not in {
            RAG_READ_SOURCE,
            RAG_READ_DOCUMENT,
            RAG_READ_SECTION,
        }:
            return output
        references = text_references(tool, output)
        if references:
            output = {**output, "references": references}
        section_note = section_read_note(tool, output, [self.goal, call.arguments.get("query")])
        if section_note is not None:
            output = {**output, "section_reads": section_note}
        if tool in RAG_BUDGET_TOOLS:
            steps = runtime_repository.get_run(self.run_id).steps
            repeated = repeated_query_note(
                call.name, call.arguments, steps, current_step_id=step_id
            )
            if repeated is not None:
                output = {**output, "repeated_query": repeated}
            codes = record_codes(
                output,
                call.arguments.get("query"),
                [
                    step.tool_call.arguments.get("query")
                    for step in steps
                    if step.tool_call is not None
                    and mcp_base_tool_name(step.tool_call.name) in RAG_BUDGET_TOOLS
                ],
            )
            if codes is not None:
                output = {**output, "record_codes": codes}
            if adjusted_limits:
                output = {
                    **output,
                    "adjusted_limits": {**adjusted_limits, "next_step": ADJUSTED_LIMITS_HINT},
                }
            superseded = superseded_versions_note(
                call.arguments, output, [self.goal, call.arguments.get("query")]
            )
            if superseded is not None:
                output = {**output, "superseded_versions": superseded}
        remaining = self.budget.rag_calls_remaining if self.budget is not None else None
        if remaining is not None:
            output = {**output, "rag_calls_remaining": remaining}
        return output

    async def check_guides(
        self, evidence_tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """根拠を集めた接続の業務ガイドを照合し、モデルへの案内を返す（#1322。しなければ None）。

        この Run でその接続の業務ガイドを引いていない（`rag_lookup_guides`・`rag_search` を呼んで
        いない）ときに 1 回だけ呼ぶ。照合の失敗は根拠の結果を変えない（案内を足さないだけ）。
        """
        from app.features.agent.runtime import runtime_repository

        prefix = _connection_prefix(evidence_tool)
        tool = self.guide_tools.get(prefix) if prefix is not None else None
        profile = arguments.get("search_answer_profile_id")
        if prefix is None or tool is None or prefix in self._guide_checked:
            return None
        if not isinstance(profile, str) or not profile.strip():
            return None
        self._guide_checked.add(prefix)
        if _guide_consulted(runtime_repository.get_run(self.run_id).steps, prefix):
            return None
        query = guide_check_query(self.goal, arguments.get("query"))
        if not query:
            return None
        provided = arguments.get("conditions")
        conditions = dict(self.known_conditions)
        if isinstance(provided, dict):
            conditions.update(
                {
                    key: value
                    for key, value in provided.items()
                    if isinstance(key, str) and isinstance(value, str) and value.strip()
                }
            )
        definition, handler = tool
        _step_id, result = await self.call(
            ToolCall(
                name=definition.name,
                arguments={
                    "query": query,
                    "search_answer_profile_id": profile,
                    "conditions": dict(list(conditions.items())[:GUIDE_CHECK_MAX_CONDITIONS]),
                    "limit": 1,
                },
                trace_id=f"{GUIDE_CHECK_TRACE_PREFIX}{self.run_id}_{prefix}",
            ),
            definition=definition,
            handler=handler,
        )
        if not result.success or not isinstance(result.output, dict):
            return None
        note = guide_check_note(result.output)
        if note is not None:
            note = {**note, "next_step": self._without_asking(note.get("next_step"))}
        return note

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
        scoped: data_scope.ScopeEnforcement | None = None,
    ) -> ToolResult:
        from app.features.agent.runtime import runtime_repository

        if self.data_scopes:
            # 呼び先でも範囲を強制させる（サービストークンの claim `profile_ids`。#1379）。
            context = context.model_copy(
                update={"profile_scopes": data_scope.token_profile_scopes(self.data_scopes)}
            )
        result = await asyncio.to_thread(
            tool_registry.invoke,
            call,
            policy=_active_policy(),
            context=context,
            force=True,
            definition=definition,
            handler=handler,
        )
        if scoped is not None and result.success and isinstance(result.output, dict):
            # 一覧・推薦の結果は範囲で絞ってから記録し、モデルへ返す（#1378）。
            output, removed = scoped.filter_output(result.output)
            if output is not result.output:
                result = result.model_copy(
                    update={
                        "output": output,
                        "audit_metadata": {
                            **result.audit_metadata,
                            "data_scope": {"filtered_out": removed},
                        },
                    }
                )
        runtime_repository.finish_builtin_tool_step(self.run_id, step_id, result)
        return result


def build_function_tools(
    run_id: str,
    tool_names: list[str],
    mcp_tools: list[McpRuntimeTool] | None = None,
    reference_keys: list[tuple[str, str]] | None = None,
    budget: SupportTaskBudget | None = None,
    *,
    goal: str = "",
    known_conditions: Mapping[str, str] | None = None,
    data_scopes: data_scope.AgentDataScopes | None = None,
    declined: Collection[str] = (),
    confirmation: ConfirmationBudget | None = None,
) -> list[FunctionTool]:
    """ツールを function tool にする。

    `declined` はこの Run で利用者が確認を取り消した種類（#1441・#1445。そのツールは渡さない）。
    `confirmation` は回答前の確認の往復（#1839）。確認できる Run にだけ `ask_user_confirmation` を
    渡し、呼び出しごとに中断するか（利用者の答えを待つか）を往復の残りで決める。
    """
    policy = _active_policy()
    recorder = _ToolRecorder(run_id, budget)
    recorder.data_scopes = dict(data_scopes or {})
    recorder.confirmation = confirmation
    entries: list[tuple[ToolDefinition, ToolHandler | None]] = []
    for name in tool_names:
        registered = tool_registry.get(name)
        if registered is not None:
            entries.append((registered, None))
    entries.extend(mcp_tools or [])
    if reference_keys:
        from app.features.agent.skill_references import reference_tool

        entries.append(reference_tool(reference_keys))
    if confirmation is not None and (confirmation.asks or confirmation.known_calls):
        # 答えた確認の再開では、上限に達していてもツールを渡す（SDK が同じ呼び出しを実行する）。
        entries.append(confirmation_tool(confirmation))
    tools: list[FunctionTool] = []
    seen: set[str] = set()
    for definition, handler in entries:
        if definition.name in seen:
            continue
        seen.add(definition.name)
        if is_declined_tool(definition.name, declined):
            continue
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

        # データの範囲が 1 つなら、プロファイルの引数をモデルに見せない（#1378）。実行の定義
        # （`tool_registry.invoke` に渡す schema）は呼び先の契約のまま。
        # 根拠のツールの件数の説明には「多段の質問では省略する」を足す（#1403）。
        schema = search_limit_schema(
            definition.name,
            data_scope.scoped_input_schema(
                definition.name, dict(definition.input_schema), recorder.data_scopes
            ),
        )
        tools.append(
            FunctionTool(
                name=definition.name,
                description=definition.description,
                params_json_schema=schema,
                on_invoke_tool=on_invoke,
                strict_json_schema=_strict_schema(schema),
                needs_approval=(
                    confirmation_gate(confirmation)
                    if definition.name == CONFIRMATION_TOOL and confirmation is not None
                    else decision == ToolPolicyDecision.ASK
                ),
                timeout_seconds=max(definition.timeout_seconds, 1.0) * 2,
            )
        )
    recorder.environment_tools = [tool.name for tool in tools if is_environment_tool(tool.name)]
    exposed = {tool.name for tool in tools}
    if (
        confirmation is not None
        and CONFIRMATION_TOOL in exposed
        and get_settings().agent_tool_grounding_required
        and tool_grounding.grounding_tools(exposed)
    ):
        # 回答前の確認の前に、資料・業務データのツールで調べさせる（#1917。拒答のガード #1835 と
        # 同じ設定で切れる）。
        confirmation.tools_consulted = lambda: _tools_consulted(run_id)
    if confirmation is not None and CONFIRMATION_TOOL in exposed:
        # ツール（NL2SQL・RAG）の確認事項を聞き直させない（#1934）。
        confirmation.tool_clarification_pending = lambda: _tool_clarification_pending(run_id)
    for definition, handler in entries:
        prefix = _connection_prefix(definition.name)
        if (
            prefix is not None
            and definition.name in exposed
            and mcp_base_tool_name(definition.name) == RAG_LOOKUP_GUIDES
        ):
            recorder.guide_tools.setdefault(prefix, (definition, handler))
    recorder.goal = goal
    recorder.known_conditions = dict(known_conditions or {})
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
    goal: str = "",
    known_conditions: Mapping[str, str] | None = None,
    data_scopes: data_scope.AgentDataScopes | None = None,
    cancellations: list[dict[str, Any]] | None = None,
    confirmation: ConfirmationBudget | None = None,
    confirmation_response: Mapping[str, Any] | None = None,
) -> Agent[Any]:
    """SDK の Agent を作る（MCP 接続のツール一覧を HTTP で取るので、イベントループの外で呼ぶ）。

    `confirmation` は回答前の確認の往復（#1839）、`confirmation_response` はこの Run が答えた
    ツールの結果の確認（metadata `clarification_response`）。方針と答えを指示に足す。

    `support_task` は前の Run から引き継いだ支援タスクの状態（指示の末尾に足す。#1243）。
    `cancellations` はこの Run で利用者が取り消した確認（#1441・#1445）。その種類のツールを渡さず、
    ほかの手段で続けるか意図を聞き直す指示を足す。
    """
    from app.features.agent.runtime import runtime_repository
    from app.features.agent.skill_references import skill_reference_keys

    target = resolve_model_target(model_id)
    mcp_tools, warnings = discover_mcp_tools(
        skill_ids,
        context=ToolInvocationContext(run_id=run_id, agent_id=agent_id, user_uuid=user_uuid),
    )
    for warning in warnings:
        runtime_repository.note_builtin_warning(run_id, warning)
    tools = build_function_tools(
        run_id,
        agent_tool_names(skill_ids),
        mcp_tools,
        reference_keys=skill_reference_keys(skill_ids),
        budget=budget,
        goal=goal,
        known_conditions=known_conditions,
        data_scopes=data_scopes,
        declined=declined_kinds({CANCEL_METADATA_KEY: cancellations or []}),
        confirmation=confirmation,
    )
    # 指示のツールの名前は、モデルに渡すツールの名前にそろえる（#1303）。
    exposed = [tool.name for tool in tools]
    composed = compose_instructions(instructions, skill_ids, exposed)
    # データの範囲の案内（#1378。強制は tool handler がする）。
    scope_text = data_scope.scope_instructions(data_scopes, exposed)
    if scope_text:
        scope_names = instruction_tool_names(skill_ids, exposed)
        composed = f"{composed}\n\n{render_tool_names(scope_text, scope_names)}"
    if support_task:
        support_names = instruction_tool_names(skill_ids, exposed)
        composed = f"{composed}\n\n{render_tool_names(support_task, support_names)}"
    cancel_text = cancel_instructions(list(cancellations or []))
    if cancel_text:
        composed = f"{composed}\n\n{cancel_text}"
    if confirmation is not None:
        # 回答前の確認の方針と、カードで答えた内容（#1839）。ツールの名前はモデルに渡す名前にする。
        confirmation_names = instruction_tool_names(skill_ids, exposed)
        for text in (
            policy_instructions(confirmation, tool_exposed=CONFIRMATION_TOOL in exposed),
            response_instructions(confirmation_response),
        ):
            if text:
                composed = f"{composed}\n\n{render_tool_names(text, confirmation_names)}"
    return Agent(
        name=name or "agent",
        instructions=composed,
        tools=list(tools),
        model=model_factory(target),
        model_settings=model_settings_for(target),
    )


# 無いツールの呼び出しの返答で、候補として並べるツールの数の上限（#1303）。
_TOOL_NOT_FOUND_LIST_MAX = 20


def tool_not_found_message(called: str, exposed: Collection[str]) -> str:
    """モデルが渡していないツールを呼んだときに返す案内（呼ぶ名前を示す。#1303）。

    素の名前（`rag_search`）や別の接頭辞で呼んだときは、ツールの部分が同じ名前を候補にする。
    候補が 1 つなら呼び直す名前を、複数（同じツールの名前が複数の接続にある）なら候補を並べ、
    どれかを選ばせる（Control Plane がどれかに決めて実行することはしない）。
    """
    base = mcp_base_tool_name(called)
    names = sorted(set(exposed))
    candidates = [name for name in names if name != called and mcp_base_tool_name(name) == base]
    if len(candidates) == 1:
        return (
            f"ツール「{called}」はありません。呼ぶときの名前は「{candidates[0]}」です。"
            f"同じ引数で「{candidates[0]}」を呼び直してください。"
        )
    if candidates:
        return (
            f"ツール「{called}」はありません。同じ名前のツールが複数の接続にあります"
            f"（{'、'.join(candidates)}）。目的に合う接続のツールの名前で呼んでください。"
        )
    if not names:
        return f"ツール「{called}」はありません。この実行で使えるツールはありません。"
    listed = "、".join(names[:_TOOL_NOT_FOUND_LIST_MAX])
    more = "ほか" if len(names) > _TOOL_NOT_FOUND_LIST_MAX else ""
    return f"ツール「{called}」はありません。使えるツールの名前: {listed}{more}。"


def _run_config(run_id: str, sdk_agent: Agent[Any]) -> RunConfig:
    """Run の SDK の設定。無いツールの呼び出しで Run を落とさず、モデルへ案内を返す（#1303）。

    SDK の既定（`raise_error`）は、モデルが渡していない名前のツールを 1 回呼んだだけで
    `ModelBehaviorError` にして Run 全体を失敗させる。呼ぶ名前を返して、モデルに呼び直させる。
    素の名前を Control Plane が別のツールへ読み替えて実行することはしない（呼び出し・承認・監査は
    モデルが呼んだ名前のまま。同じ名前が複数の接続にあるときに誤った接続を呼ばない）。
    """
    exposed = [str(getattr(tool, "name", "")) for tool in sdk_agent.tools]

    def format_error(args: ToolErrorFormatterArgs[Any]) -> str | None:
        if args.kind != "tool_not_found":
            return None
        message = tool_not_found_message(args.tool_name, exposed)
        logger.warning(
            "builtin_runtime_tool_not_found",
            extra={"run_id": run_id, "tool_name": args.tool_name, "call_id": args.call_id},
        )
        return message

    return RunConfig(
        tool_not_found_behavior="return_error_to_model",
        tool_error_formatter=format_error,
    )


def conversation_input(run_id: str, goal: str) -> str | list[Any]:
    """同じ会話の前の往復を付けた入力（会話が無ければ質問の文字列だけ。#768）。

    前の往復は共通の予算（`PLATFORM_CHAT_HISTORY_*`。新しい往復から詰め、古い往復を落とす）で
    選び、未信頼のデータ（`<conversation_history untrusted="true">`）として質問の前の利用者の
    メッセージに入れる（3 製品共通。#1818）。前の回答はツールの結果（文書・SQL の結果）に由来する
    文を含むので、assistant の発話としては渡さない。
    """
    from app.features.agent.runtime import runtime_repository

    history = build_chat_history(
        runtime_repository.thread_history(run_id), ChatHistoryBudget.from_settings(get_settings())
    )
    if not history:
        return goal
    return [
        {"role": "user", "content": f"{CHAT_HISTORY_UNTRUSTED_NOTE}\n{history}"},
        {"role": "user", "content": goal},
    ]


@dataclass
class _SupportTaskRun:
    """1 回の実行（開始・承認後の再開）の支援タスク（#1243）。"""

    goal: str
    previous: dict[str, Any] | None
    budget: SupportTaskBudget
    instructions: str
    max_rag_calls_per_run: int
    max_tool_calls_per_task: int
    # 回答前の確認（#1839）: 往復の残り・カードの答え（次の Run の metadata）・答えた RAG の条件。
    confirmation: ConfirmationBudget | None = None
    response: dict[str, Any] | None = None
    answered_conditions: dict[str, str] | None = None

    def known_conditions(self) -> dict[str, str]:
        """前の Run までに分かった条件と、この Run で利用者がカードで答えた条件。"""
        return {**support_task_known_conditions(self.previous), **(self.answered_conditions or {})}


def confirmation_budget(run: Any, agent: Any) -> ConfirmationBudget:
    """この Run の回答前の確認の往復（業務 Agent の方針・上限、無人の Run、確認せずに回答）。"""
    from app.features.agent.runtime import EVALUATION_DRY_RUN_KEY, is_automation_run

    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    response = metadata.get(RESPONSE_METADATA_KEY)
    confirmations = list(getattr(run, "confirmations", []) or [])
    # 利用者が「確認せずに回答」を選んだ Run は、聞き直さずに前提を示して答える: ツールの結果の
    # 確認のスキップで作った次の Run と、Agent 自身の確認のスキップで再開した Run（#1903。
    # その Run の残りでも、NL2SQL・RAG の確認を聞かない）。
    skipped = (
        isinstance(response, dict) and response.get("action") == "skip"
    ) or agent_confirmation_skipped(confirmations)
    budget = ConfirmationBudget(
        policy=str(getattr(agent, "clarification_policy", "confirm")),
        max_rounds=int(getattr(agent, "clarification_max_rounds", 0) or 0),
        used=used_rounds(metadata, confirmations),
        unattended=bool(metadata.get(EVALUATION_DRY_RUN_KEY)) or is_automation_run(metadata),
        skipped=skipped,
    )
    budget.known_calls = {
        item.call_id
        for item in confirmations
        if item.source == SOURCE_AGENT and item.call_id and item.status in {"answered", "skipped"}
    }
    return budget


def _support_task_run(run: Any, agent: Any = None) -> _SupportTaskRun:
    """前の Run の状態と、この Run の step から予算の残りを作る。

    再開した Run は、それまでの step を数えるので消費を 0 に戻さない。
    """
    from app.features.agent.runtime import runtime_repository

    settings = get_settings()
    max_rag = int(settings.agent_max_rag_calls_per_run)
    max_task = int(settings.agent_max_tool_calls_per_task)
    goal, previous = runtime_repository.support_task_context(run.id)
    response = run.metadata.get(RESPONSE_METADATA_KEY) if isinstance(run.metadata, dict) else None
    response = response if isinstance(response, dict) else None
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
            # 範囲で 1 つに決まっている接続は、前のプロファイルを出さない（#1880）。
            fixed_profile_connections=data_scope.fixed_profile_connections(
                getattr(agent, "data_scopes", None)
            ),
        ),
        max_rag_calls_per_run=max_rag,
        max_tool_calls_per_task=max_task,
        confirmation=confirmation_budget(run, agent) if agent is not None else None,
        response=response,
        answered_conditions=rag_conditions(response or {}),
    )


def _save_support_task(run_id: str, task: _SupportTaskRun) -> dict[str, Any] | None:
    """支援タスクの状態を Run の成果物に残す（ツールを呼ばず、引き継ぐ状態も無ければ残さない）。

    状態は補助のため、作れなくても Run は止めない。残した状態を返す（残さなければ None）。
    """
    from app.features.agent.runtime import run_thread_owner, runtime_repository

    try:
        run = runtime_repository.get_run(run_id)
        cancellations = _run_cancellations(run)
        if (
            task.previous is None
            and not has_support_task_activity(run.steps)
            and not cancellations
            and not task.answered_conditions
        ):
            return None
        content = build_support_task(
            task.previous,
            steps=run.steps,
            run_id=run.id,
            thread_id=run.thread_id,
            # 状態は会話の持ち主のもの（system_admin が続けた Run も同じ会話の状態。#1928）。
            owner_user_uuid=run_thread_owner(run),
            goal=task.goal,
            max_rag_calls_per_run=task.max_rag_calls_per_run,
            max_tool_calls_per_task=task.max_tool_calls_per_task,
            cancellations=cancellations,
            answered_conditions=task.answered_conditions,
            ask_rag=task.confirmation is None or task.confirmation.asks,
            ask_nl2sql=task.confirmation is None or not task.confirmation.skipped,
        )
        runtime_repository.save_support_task(run_id, content)
        return content
    except Exception as exc:  # noqa: BLE001 - 状態は補助。残せなくても回答は返す
        logger.warning(
            "builtin_runtime_support_task_failed",
            extra={"run_id": run_id, "exception_type": type(exc).__name__},
        )
        return None


async def execute_run(run_id: str) -> None:
    """Run を最初から実行する（作成直後・dispatcher から呼ぶ）。"""
    with bind_log_context(run_id=run_id):
        from app.features.agent.runtime import runtime_repository

        started = runtime_repository.begin_builtin_run(run_id)
        if started is None:
            return
        run, agent = started
        # 公開中の版に固定したスキルで動く（#1855。下書きの Run・固定の無い古い版は今のスキル）。
        pinned = use_pinned_skills(agent.pinned_skills)
        try:
            task = _support_task_run(run, agent)
            cancellations = await _apply_clarification_cancel(run, task.previous, agent.data_scopes)
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
                goal=support_task_goal(run.goal, task.previous),
                known_conditions=task.known_conditions(),
                data_scopes=agent.data_scopes,
                cancellations=cancellations,
                confirmation=task.confirmation,
                confirmation_response=task.response,
            )
            result = await Runner.run(
                sdk_agent,
                conversation_input(run_id, run.goal),
                max_turns=_max_turns(),
                run_config=_run_config(run_id, sdk_agent),
            )
            result = await _unattended_approvals(run, sdk_agent, result, agent.data_scopes)
            result, refused = await _require_tool_grounding(
                run, sdk_agent, result, agent.data_scopes, assume=_assumes(task.confirmation)
            )
            _record_usage(run_id, result, agent.model_id)
            await _finish(
                run_id,
                result,
                task,
                rag_tools=has_rag_evidence_tools(sdk_agent, agent.skill_ids, cancellations),
                data_scopes=agent.data_scopes,
                refusal=_refusal_text(agent, sdk_agent) if refused else None,
            )
        except Exception as exc:  # noqa: BLE001 - 実行の境界では失敗を Run に記録する
            _record_usage(run_id, getattr(exc, "run_data", None), agent.model_id)
            _record_failure(run_id, exc)
        finally:
            reset_pinned_skills(pinned)


async def resume_run(run_id: str) -> None:
    """承認がすべて決まった Run を、保存した SDK の状態から再開する。"""
    with bind_log_context(run_id=run_id):
        from app.features.agent.runtime import runtime_repository

        resumed = runtime_repository.begin_builtin_resume(run_id)
        if resumed is None:
            return
        run, agent, state_text, decisions = resumed
        pinned = use_pinned_skills(agent.pinned_skills)
        try:
            task = _support_task_run(run, agent)
            # 取り消しは実行の始めに済んでいる（session も閉じた）。記録だけを使う。
            cancellations = _run_cancellations(run)
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
                goal=support_task_goal(run.goal, task.previous),
                known_conditions=task.known_conditions(),
                data_scopes=agent.data_scopes,
                cancellations=cancellations,
                confirmation=task.confirmation,
                confirmation_response=task.response,
            )
            state = await RunState.from_string(sdk_agent, state_text)
            for item in state.get_interruptions():
                call_id = str(getattr(item, "call_id", "") or "")
                if decisions.get(call_id, False):
                    state.approve(item)
                else:
                    state.reject(item)
            result = await Runner.run(
                sdk_agent, state, max_turns=_max_turns(), run_config=_run_config(run_id, sdk_agent)
            )
            result = await _unattended_approvals(run, sdk_agent, result, agent.data_scopes)
            result, refused = await _require_tool_grounding(
                run, sdk_agent, result, agent.data_scopes, assume=_assumes(task.confirmation)
            )
            _record_usage(run_id, result, agent.model_id)
            await _finish(
                run_id,
                result,
                task,
                rag_tools=has_rag_evidence_tools(sdk_agent, agent.skill_ids, cancellations),
                data_scopes=agent.data_scopes,
                refusal=_refusal_text(agent, sdk_agent) if refused else None,
            )
        except Exception as exc:  # noqa: BLE001 - 実行の境界では失敗を Run に記録する
            _record_usage(run_id, getattr(exc, "run_data", None), agent.model_id)
            _record_failure(run_id, exc)
        finally:
            reset_pinned_skills(pinned)


def _run_cancellations(run: Any) -> list[dict[str, Any]]:
    """Run の記録の、利用者が取り消した確認（#1441・#1445）。"""
    records = run.metadata.get(CANCEL_METADATA_KEY) if isinstance(run.metadata, dict) else None
    if not isinstance(records, list):
        return []
    return [
        record
        for record in records
        if isinstance(record, dict) and record.get("kind") in CLARIFICATION_KINDS
    ]


async def _apply_clarification_cancel(
    run: Any,
    previous: Mapping[str, Any] | None,
    data_scopes: data_scope.AgentDataScopes | None = None,
) -> list[dict[str, Any]]:
    """この Run で取り消す確認を決め、NL2SQL の確認の session を閉じ、Run に記録する（#1441）。

    取り消しは、取り消しの操作で作った Run（`clarification_cancel`）と、確かめ中の問いがあるときの
    取り消しの文（「データベースは関係ない」など）。取り消す確認が無ければ何もしない。
    """
    from app.features.agent.runtime import runtime_repository

    triggers: dict[str, str] = {kind: TRIGGER_BUTTON for kind in button_cancel_kinds(run.metadata)}
    for kind in message_cancel_kinds(run.goal, previous):
        triggers.setdefault(kind, TRIGGER_MESSAGE)
    if not triggers:
        return []
    at = datetime.now(UTC).isoformat()
    records: list[dict[str, Any]] = []
    for kind in CLARIFICATION_KINDS:
        if kind not in triggers:
            continue
        pending = pending_of(previous, kind)
        record = cancel_record(kind, trigger=triggers[kind], run_id=run.id, at=at, pending=pending)
        if kind == KIND_NL2SQL:
            for index, session in enumerate(record["requirement_sessions"]):
                session_id = session["requirement_session_id"]
                connection = next(
                    (
                        str(item.get("connection") or "")
                        for item in pending
                        if item.get("requirement_session_id") == session_id
                    ),
                    "",
                )
                session["status"] = await _cancel_requirement_session(
                    run,
                    connection or data_scope.NL2SQL_CONNECTION_ID,
                    session_id,
                    trace_id=f"{CANCEL_TRACE_PREFIX}{run.id}_{index}",
                    data_scopes=data_scopes,
                )
        records.append(record)
    runtime_repository.record_clarification_cancel(run.id, records)
    logger.info(
        "builtin_runtime_clarification_cancelled",
        extra={
            "run_id": run.id,
            "kinds": [record["kind"] for record in records],
            "triggers": [record["trigger"] for record in records],
        },
    )
    return records


async def _cancel_requirement_session(
    run: Any,
    connection: str,
    session_id: str,
    *,
    trace_id: str,
    data_scopes: data_scope.AgentDataScopes | None = None,
) -> str:
    """NL2SQL の SQL の生成前の確認の session を閉じる（`nl2sql_cancel_requirement`。#1441）。

    Control Plane が Run の利用者のサービストークンで呼ぶ（ポリシー・監査・step はモデルのツールと
    同じ境界。予算には数えない）。閉じられなくても Run は続ける（session は操作が途絶えると期限で
    閉じる）。返すのは記録に残す状態（cancelled / closed / failed / unavailable）。
    """
    function_name = f"{connection}{MCP_TOOL_SEPARATOR}{NL2SQL_CANCEL_TOOL}"
    config = next(
        (
            item
            for item in runtime_config_store.list_mcp_servers()
            if mcp_function_name(item.server_id, NL2SQL_CANCEL_TOOL) == function_name
        ),
        None,
    )
    if config is None:
        return "unavailable"
    context = ToolInvocationContext(
        run_id=run.id, agent_id=run.agent_id, user_uuid=run.created_by_user_uuid
    )
    try:
        listed = await asyncio.to_thread(
            list_mcp_connection_tools, config.server_id, context=context
        )
    except (KeyError, ExternalToolError):
        return "unavailable"
    tool = next((item for item in listed.tools if item.name == NL2SQL_CANCEL_TOOL), None)
    if tool is None:
        return "unavailable"
    definition = mcp_tool_definition(config, tool)
    recorder = _ToolRecorder(run.id)
    recorder.data_scopes = dict(data_scopes or {})
    _step_id, result = await recorder.call(
        ToolCall(
            name=definition.name,
            arguments={"requirement_session_id": session_id},
            trace_id=trace_id,
        ),
        definition=definition,
        handler=mcp_tool_handler(config, tool),
    )
    if not result.success or not isinstance(result.output, dict):
        return "failed"
    status = result.output.get("status")
    return status if isinstance(status, str) and status else "cancelled"


async def close_requirement_sessions(run_id: str, sessions: list[dict[str, Any]]) -> None:
    """「確認をやめる」で外した NL2SQL の SQL の生成前の確認の session を閉じる（#1839）。

    閉じられなくても利用者の操作は済んでいる（session は操作が途絶えると期限で閉じる）。
    """
    from app.features.agent.runtime import runtime_repository

    with bind_log_context(run_id=run_id):
        try:
            run = runtime_repository.get_run(run_id)
            agent = next(
                (item for item in runtime_repository.list_agents() if item.id == run.agent_id),
                None,
            )
            for index, session in enumerate(sessions):
                await _cancel_requirement_session(
                    run,
                    str(session.get("connection") or "") or data_scope.NL2SQL_CONNECTION_ID,
                    str(session.get("requirement_session_id") or ""),
                    trace_id=f"{CANCEL_TRACE_PREFIX}{run.id}_withdrawn_{index}",
                    data_scopes=agent.data_scopes if agent is not None else None,
                )
        except Exception as exc:  # noqa: BLE001 - 閉じられなくても期限で閉じる
            logger.warning(
                "builtin_runtime_withdrawn_session_close_failed",
                extra={"run_id": run_id, "exception_type": type(exc).__name__},
            )


def _scoped_call(call: ToolCall, data_scopes: data_scope.AgentDataScopes | None) -> ToolCall:
    """承認・dry-run に記録する呼び出しに、データの範囲を当てる（実行するときと同じ引数。#1378）。

    範囲外（拒否）の呼び出しはモデルの引数のまま記録する（実行するときにツールのエラーになる）。
    """
    scoped = data_scope.enforce(call.name, call.arguments, data_scopes)
    if scoped is None:
        return call
    arguments = call.arguments if scoped.error is not None else scoped.arguments
    return call.model_copy(update={"arguments": arguments, "data_scope": scoped.note})


async def _unattended_approvals(
    run: Any,
    sdk_agent: Agent[Any],
    result: Any,
    data_scopes: data_scope.AgentDataScopes | None = None,
) -> Any:
    """無人の Run は、承認が要るツールを実行せずに続ける（承認待ちにしない）。

    - 品質評価の Run（#776。dry-run）: 呼ぼうとしたツールは step に残し（ツールの選択の判定に
      使う）、モデルへは「評価中のため実行していない」と返して回答を完成させる。
    - 自動実行の Run（#1813。`metadata.automation_id`。スケジュールも今すぐ実行も）: 誰も承認
      しないため、承認が要るツールは呼ばない（無人の実行は事前に許可したツールだけを使う）。
      モデルへは「自動実行では使えない」と返し、確かめられなかった点として回答を完成させる。
      step（監査）と Run の経過に、承認が要るため使わなかったことを残す。
    - 外部の MCP クライアントの Run（#1874。`metadata.source=mcp`）: 承認する人が画面に
      いるとは限らないため、自動実行と同じく承認が要るツールを呼ばない。

    それ以外の Run（画面のチャット・実行）はそのまま返す（承認待ちにする）。
    """
    from app.features.agent.runtime import (
        AUTOMATION_APPROVAL_SKIPPED_CODE,
        EVALUATION_DRY_RUN_KEY,
        EVALUATION_DRY_RUN_MESSAGE,
        MCP_APPROVAL_SKIPPED_CODE,
        automation_approval_message,
        is_automation_run,
        is_mcp_run,
        mcp_approval_message,
        runtime_repository,
    )

    evaluation = bool(run.metadata.get(EVALUATION_DRY_RUN_KEY))
    mcp = not evaluation and is_mcp_run(run.metadata)
    if not evaluation and not mcp and not is_automation_run(run.metadata):
        return result
    for _ in range(_max_turns()):
        interruptions = list(getattr(result, "interruptions", []) or [])
        if mcp:
            # MCP の Run は回答前の確認（`ask_user_confirmation`。#1839）を出せる（呼び出し元の
            # クライアントが agent_answer_confirmation で答える）。確認は残し、承認だけを断る
            # （SDK は決めていない中断を次の結果にもう一度返す）。
            interruptions = [
                item for item in interruptions if _interruption_call(item).name != CONFIRMATION_TOOL
            ]
        if not interruptions:
            return result
        calls = [_scoped_call(_interruption_call(item), data_scopes) for item in interruptions]
        if evaluation:
            runtime_repository.record_builtin_dry_run_steps(run.id, calls)
        else:
            runtime_repository.record_builtin_unattended_skips(run.id, calls, mcp=mcp)
            logger.info(
                "builtin_runtime_tool_call_rejected",
                extra={
                    "run_id": run.id,
                    "tool_names": [call.name for call in calls],
                    "error_code": (
                        MCP_APPROVAL_SKIPPED_CODE if mcp else AUTOMATION_APPROVAL_SKIPPED_CODE
                    ),
                },
            )
        state = result.to_state()
        for item, call in zip(interruptions, calls, strict=True):
            state.reject(
                item,
                rejection_message=(
                    EVALUATION_DRY_RUN_MESSAGE
                    if evaluation
                    else mcp_approval_message(call.name)
                    if mcp
                    else automation_approval_message(call.name)
                ),
            )
        result = await Runner.run(
            sdk_agent, state, max_turns=_max_turns(), run_config=_run_config(run.id, sdk_agent)
        )
    return result


def _answer_text(output: object) -> str:
    return (
        output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
    )


def _assumes(budget: ConfirmationBudget | None) -> bool:
    """利用者に確認しない Run か（方針「前提を示して回答」・「確認せずに回答」を選んだ。#1903）。

    無人の Run（品質評価・自動実行）は含めない（差し戻しの文は今の「確認のツールを渡していない」の
    文のまま）。
    """
    return budget is not None and budget.policy == POLICY_ASSUME and not budget.unattended


async def _require_tool_grounding(
    run: Any,
    sdk_agent: Agent[Any],
    result: Any,
    data_scopes: data_scope.AgentDataScopes | None = None,
    *,
    assume: bool = False,
) -> tuple[Any, bool]:
    """根拠・データのツールを呼ばずに作った回答を差し戻し、それでも呼ばなければ拒答にする（#1835）。

    根拠・データのツールを持つ Run で、モデルがどれも呼ばずに最終の回答を作ったら（確認の質問
    だけの回答は除く。`tool_grounding.needs_grounding`）、`tool_grounding.MAX_RETRIES` 回まで
    「ツールで確かめてから答える。確かめられないなら拒答する」を利用者のメッセージとして足して
    Run を続ける（同じ SDK の Agent・ツール・予算。利用量は前の分に積む）。続けた Run が承認で
    止まったら、そのまま返す（承認待ち。無人の Run は `_unattended_approvals` で続ける）。
    差し戻しても呼ばなければ、2 つ目の値を True にして返す（`_finish` が拒答の文に置き換える）。
    差し戻しと元の回答は Run の経過に残す。差し戻しの文は、この Run でモデルに回答前の確認の
    ツール（`ask_user_confirmation`）を渡したかで分ける（#1881。`tool_grounding.retry_message`）。
    差し戻しの後も、確認のツールを渡したのに本文で確認の質問だけを返した回答は、拒答にせず、
    Run の経過に残す。
    確認のツールを渡した Run（#1881）と `assume`（利用者に確認しない Run。#1903。`_assumes`）では、
    ツールを呼ばずに本文の確認の質問だけを返した回答も 1 回差し戻す（あいさつへの返事は除く）。
    `assume` の差し戻しの文は「確認の質問はせず、ツールで調べ、決まらない点は前提を示して答える」。
    差し戻しの後も本文の聞き返しだけなら、拒答にはせず、Run の経過に残してそのまま出す。
    """
    from app.features.agent.runtime import runtime_repository

    if not get_settings().agent_tool_grounding_required:
        return result, False
    tool_names = [str(getattr(tool, "name", "")) for tool in sdk_agent.tools]
    if not tool_grounding.grounding_tools(tool_names):
        return result, False
    confirmable = CONFIRMATION_TOOL in tool_names
    # 確認のツールを渡した Run（#1881）と利用者に確認しない Run（#1903）は、本文の確認の質問だけの
    # 回答も差し戻す（あいさつへの返事は除く）。差し戻しの後も本文の聞き返しなら、拒答にせず出す。
    text_clarification_retried = confirmable or assume
    while not list(getattr(result, "interruptions", []) or []):
        answer = _answer_text(result.final_output)
        current = runtime_repository.get_run(run.id)
        if declined_kinds(current.metadata):
            # 利用者が確認を取り消し（「資料の検索は不要」など。#1441・#1445）、その手段を使わずに
            # 続けると決めた Run。ほかの手段で続けるか意図を聞き直す回答を拒答にしない。
            return result, False
        steps = list(current.steps)
        if not tool_grounding.needs_grounding(
            answer,
            steps=steps,
            tool_names=tool_names,
            clarification_exempt=not text_clarification_retried,
            question=str(getattr(current, "goal", "") or ""),
        ):
            if tool_grounding.retried(current.metadata) and tool_grounding.clarified_in_text(
                answer, steps=steps, tool_names=tool_names
            ):
                # 差し戻しの後も、確認のカードではなく本文で聞き返した（#1881）。拒答にはせず、
                # 経過に残す（差し戻していない回答のあいさつ・用件の聞き返しは残さない）。
                runtime_repository.record_tool_grounding(run.id, "text_clarification", answer)
            return result, False
        if _grounded_in_thread(run.id):
            # 同じ会話の前の Run がツールで答えた。前の回答の言い換え・整形を拒答にしない。
            return result, False
        if tool_grounding.retried(current.metadata) >= tool_grounding.MAX_RETRIES:
            if text_clarification_retried and is_clarification_only(answer):
                # 差し戻しの後も本文で聞き返した（#1881・#1903）。対象が曖昧な質問の聞き返しは
                # 拒答にせず、経過に残してそのまま出す。
                runtime_repository.record_tool_grounding(run.id, "text_clarification", answer)
                return result, False
            runtime_repository.record_tool_grounding(run.id, "refused", answer)
            logger.info(
                "builtin_runtime_tool_grounding_refused",
                extra={"run_id": run.id, "tool_names": tool_names},
            )
            return result, True
        runtime_repository.record_tool_grounding(run.id, "retry", answer)
        logger.info(
            "builtin_runtime_tool_grounding_retry",
            extra={"run_id": run.id, "tool_names": tool_names},
        )
        # 利用量は新しい Run の文脈に前の分を足して続ける（承認待ちで保存する状態にも入る）。
        context: RunContextWrapper[Any] = RunContextWrapper(context=result.context_wrapper.context)
        context.usage.add(result.context_wrapper.usage)
        result = await Runner.run(
            sdk_agent,
            tool_grounding.retry_input(
                result.to_input_list(), answer, confirmable=confirmable, assume=assume
            ),
            context=context,
            max_turns=_max_turns(),
            run_config=_run_config(run.id, sdk_agent),
        )
        result = await _unattended_approvals(run, sdk_agent, result, data_scopes)
    return result, False


def _grounded_in_thread(run_id: str) -> bool:
    """会話の履歴の上限（`PLATFORM_CHAT_HISTORY_MAX_TURNS`。#1818）の中の前の Run のどれかが、
    根拠・データのツールで答えたか（#1835）。上限が 0（履歴を渡さない）なら前の Run を見ない。"""
    from app.features.agent.runtime import runtime_repository

    max_turns = ChatHistoryBudget.from_settings(get_settings()).max_turns
    if max_turns <= 0:
        return False
    previous = runtime_repository.thread_previous_runs(run_id)[-max_turns:]
    return tool_grounding.grounded_in_thread(list(item.steps) for item in previous)


def _refusal_text(agent: Any, sdk_agent: Agent[Any]) -> str:
    """拒答の文（業務 Agent の説明・スキルの表示名と、モデルに渡したツールから。#1835）。"""
    skill_names = [
        skill.name
        for skill_id in getattr(agent, "skill_ids", []) or []
        if (skill := resolve_skill(skill_id)) is not None and skill.enabled
    ]
    return tool_grounding.refusal_text(
        description=str(getattr(agent, "description", "") or ""),
        skill_names=skill_names,
        tool_names=[str(getattr(tool, "name", "")) for tool in sdk_agent.tools],
    )


def _interruption_call(item: Any) -> ToolCall:
    """SDK の中断（承認待ち）の項目を、記録する呼び出しにする。"""
    return ToolCall(
        name=str(getattr(item, "tool_name", "") or getattr(item, "name", "") or "tool"),
        arguments=_arguments(getattr(item, "arguments", None)),
        trace_id=str(getattr(item, "call_id", "") or ""),
    )


async def _finish(
    run_id: str,
    result: Any,
    task: _SupportTaskRun | None = None,
    *,
    rag_tools: bool = False,
    data_scopes: data_scope.AgentDataScopes | None = None,
    refusal: str | None = None,
) -> None:
    """Run を承認待ちにするか、回答を確定する。

    `refusal` があれば（根拠・データのツールを呼ばずに作った回答。#1835）、モデルの回答の代わりに
    その拒答の文を回答にし、最終の検証はせずに `skipped`（`no_tool_grounding`）を残す。
    """
    from app.features.agent.runtime import runtime_repository

    # 承認待ちで止めるときも残す（再開した Run は同じ成果物を上書きする。#1243）。
    state_content = _save_support_task(run_id, task) if task is not None else None
    interruptions = list(getattr(result, "interruptions", []) or [])
    if interruptions:
        # 承認で見せる引数は、実行するときと同じくデータの範囲を当てたもの（#1378）。
        # Agent 自身の回答前の確認（#1839）は承認ではなく、利用者の答えを待つ確認にする。
        calls: list[ToolCall] = []
        confirmations: list[ConfirmationRequest] = []
        for item in interruptions:
            call = _interruption_call(item)
            if call.name == CONFIRMATION_TOOL:
                confirmations.append(_agent_confirmation(call, task, run_id))
            else:
                calls.append(_scoped_call(call, data_scopes))
        runtime_repository.request_builtin_approvals(
            run_id, calls, state=result.to_state().to_string(), confirmations=confirmations
        )
        return
    if refusal is not None:
        runtime_repository.save_builtin_artifact(
            run_id,
            kind=ANSWER_VALIDATION_KIND,
            name=ANSWER_VALIDATION_NAME,
            content=validation_content(
                STATUS_SKIPPED, reason=tool_grounding.REASON_NO_TOOL_GROUNDING
            ),
        )
        runtime_repository.complete_builtin_run(
            run_id, refusal, outcome=tool_grounding.refusal_outcome()
        )
        return
    answer = _answer_text(result.final_output)
    # モデルの最終の回答（最終の検証で段落を外す前。対応を決めるのに使う。#1305）。
    model_answer = answer
    validation: dict[str, Any] | None = None
    if get_settings().agent_final_validation_enabled:
        try:
            answer, validation = await _validate_final_answer(
                run_id, answer, rag_tools=rag_tools, data_scopes=data_scopes
            )
        except Exception as exc:  # noqa: BLE001 - 検証の失敗で回答を落とさない
            logger.warning(
                "builtin_runtime_answer_validation_failed",
                extra={"run_id": run_id, "exception_type": type(exc).__name__},
            )
            # 確かめられなかったことを成果物にも残す（#1277。黙って通さない）。
            validation = validation_content(
                STATUS_UNVALIDATED,
                reason=REASON_VALIDATION_ERROR,
                message="回答の検証の途中で予期しない失敗が起きました。",
            )
            runtime_repository.save_builtin_artifact(
                run_id, kind=ANSWER_VALIDATION_KIND, name=ANSWER_VALIDATION_NAME, content=validation
            )
            answer = with_unverified_notice(answer)
    runtime_repository.complete_builtin_run(
        run_id,
        answer,
        outcome=_answer_outcome(run_id, model_answer, validation),
        confirmations=_tool_confirmations(task, state_content),
    )


def _tools_consulted(run_id: str) -> bool:
    """この Run でモデルが MCP 接続のツールを呼んだか（回答前の確認の前の調べ。#1917）。"""
    from app.features.agent.runtime import runtime_repository

    return tool_grounding.tools_consulted(list(runtime_repository.get_run(run_id).steps))


def _tool_clarification_pending(run_id: str) -> bool:
    """この Run の最後の NL2SQL・RAG の結果が確認事項を返して止まっているか（#1934）。"""
    from app.features.agent.runtime import runtime_repository

    return tool_clarification_pending(list(runtime_repository.get_run(run_id).steps))


def _agent_confirmation(
    call: ToolCall, task: _SupportTaskRun | None, run_id: str = ""
) -> ConfirmationRequest:
    """`ask_user_confirmation` の中断を、利用者の答えを待つ確認にする（#1839）。

    資料・業務データのツールを呼ばずに出した確認（#1917。`ConfirmationBudget.free_text_calls`）は、
    モデルが作った選択肢を外して自由入力で聞く（選択肢をツールの結果から作れないため）。
    ツールを呼んだ後の確認も、選択肢はこの Run のツールの結果にあるものだけを残し、2 つ未満なら
    自由入力にする（#1934。`ground_options`）。
    """
    from app.features.agent.runtime import runtime_repository

    budget = task.confirmation if task is not None else None
    round_ = (budget.rounds.get(call.trace_id or "") or budget.used) if budget is not None else 1
    item = item_from_arguments(call.arguments)
    if budget is not None and (call.trace_id or "") in budget.free_text_calls:
        item = item.model_copy(update={"options": [], "selection": "single"})
    elif budget is not None and budget.tools_consulted is not None and run_id:
        steps = list(runtime_repository.get_run(run_id).steps)
        item = ground_options(item, tool_grounding.tool_output_texts(steps))
    return ConfirmationRequest(
        source=SOURCE_AGENT,
        call_id=call.trace_id or "",
        items=[item],
        round=max(round_, 1),
        max_rounds=budget.max_rounds if budget is not None else 1,
    )


def _pending_key(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("kind") or KIND_RAG),
        str(entry.get("condition_id") or ""),
        str(entry.get("requirement_session_id") or ""),
    )


def _tool_confirmations(
    task: _SupportTaskRun | None, content: Mapping[str, Any] | None
) -> list[ConfirmationRequest]:
    """この Run のツールの結果が返した確認事項を、画面の回答前の確認にする（#1839）。

    確認事項は呼び先（NL2SQL・RAG）が作ったまま受け渡す。この Run で新しく確かめ中になった問いだけ
    （前の Run から残っている問いは出し直さない）。無人の Run は出さない。RAG の確認は、利用者に
    確認できる Run だけ（方針が「前提を示して回答する」・上限に達した Run では前提を示して
    答えている）。
    NL2SQL の確認は NL2SQL の業務プロファイルの方針に従う（聞かれたものを出す）。スキップは
    次の Run で `skip_clarification` を付けて呼び直させる。
    利用者が「確認せずに回答」を選んだ Run（#1903）は、NL2SQL の確認も出さない（モデルが
    `skip_clarification` で呼び直さなかったときも、カードで聞き直さない）。
    """
    budget = task.confirmation if task is not None else None
    if (
        task is None
        or budget is None
        or budget.unattended
        or budget.skipped
        or not isinstance(content, Mapping)
    ):
        return []
    previous = task.previous.get("pending_clarifications") if task.previous else None
    seen = {_pending_key(entry) for entry in previous or [] if isinstance(entry, Mapping)}
    pending = [
        entry
        for entry in content.get("pending_clarifications") or []
        if isinstance(entry, Mapping) and _pending_key(entry) not in seen
    ]
    nl2sql = [entry for entry in pending if entry.get("kind") == KIND_NL2SQL]
    if nl2sql:
        items = items_from_pending(nl2sql[:1], SOURCE_NL2SQL)
        if items:
            return [
                ConfirmationRequest(
                    source=SOURCE_NL2SQL,
                    items=items,
                    round=budget.used + 1,
                    max_rounds=budget.max_rounds,
                    context=nl2sql_context(nl2sql[0]),
                )
            ]
    rag = [entry for entry in pending if entry.get("kind") != KIND_NL2SQL]
    if rag and budget.asks:
        items = items_from_pending(rag, SOURCE_RAG)
        if items:
            return [
                ConfirmationRequest(
                    source=SOURCE_RAG,
                    items=items,
                    round=budget.used + 1,
                    max_rounds=budget.max_rounds,
                )
            ]
    return []


def _answer_outcome(
    run_id: str, answer: str, validation: dict[str, Any] | None
) -> dict[str, Any] | None:
    """回答の対応（#1305。`answer_outcome`）。決められなくても回答は保存する（対応は付けない）。"""
    from app.features.agent.runtime import runtime_repository

    try:
        steps = runtime_repository.get_run(run_id).steps
        return answer_outcome(answer, steps=steps, validation=validation)
    except Exception as exc:  # noqa: BLE001 - 対応は補助。決められなくても回答は返す
        logger.warning(
            "builtin_runtime_answer_outcome_failed",
            extra={"run_id": run_id, "exception_type": type(exc).__name__},
        )
        return None


def _validator_connection(evidence_tool: str) -> McpConnectionConfig | None:
    """根拠を返したツール（`<接続>__rag_search` など）の MCP 接続。"""
    base = mcp_base_tool_name(evidence_tool)
    for config in runtime_config_store.list_mcp_servers():
        if mcp_function_name(config.server_id, base) == evidence_tool:
            return config
    return None


def has_rag_evidence_tools(
    sdk_agent: Agent[Any] | None,
    skill_ids: list[str],
    cancellations: list[dict[str, Any]] | None = None,
) -> bool:
    """Agent が RAG の根拠のツール（`rag_search` / `rag_retrieve_evidence`）を持つか（#1277）。

    Run に渡したツールにあるか、Skill の requirement が名前で求めていれば持つとみなす（RAG の
    接続のツールを取れなかった Run でも、根拠を使えなかった回答として注記するため）。
    利用者が「資料の検索は不要」と取り消した Run（#1445）は持たないとみなす（資料を使わない）。
    """
    if KIND_RAG in declined_kinds({CANCEL_METADATA_KEY: cancellations or []}):
        return False
    tools = list(getattr(sdk_agent, "tools", []) or [])
    if any(mcp_base_tool_name(str(getattr(tool, "name", ""))) in EVIDENCE_TOOLS for tool in tools):
        return True
    return any(
        allowed is not None and bool(allowed & EVIDENCE_TOOLS)
        for allowed in agent_mcp_requirements(skill_ids).values()
    )


async def _validate_final_answer(
    run_id: str,
    answer: str,
    *,
    rag_tools: bool = False,
    data_scopes: data_scope.AgentDataScopes | None = None,
) -> tuple[str, dict[str, Any]]:
    """回答の最終の検証（#1246・#1277）。検証の結果を成果物に残し、利用者に見せる回答と、
    成果物の内容（回答の対応を決めるのに使う。#1305）を返す。

    モデルではなく Control Plane が、根拠を返した RAG の MCP 接続ごとに `rag_validate_answer` を
    呼ぶ（ポリシー・監査・Run の利用者のサービストークン・step はモデルのツールと同じ境界）。
    利用者が回答前の確認で答えた内容は前提（`premises`）として渡す（#1898）。
    判定が valid でなければ確かめられなかった段落を外して理由を足し、検証が失敗したら
    「検証できませんでした」を足す（回答は消さない。`answer_validation.combine_validations`）。
    `rag_tools` は Agent が RAG の根拠のツールを持つか（根拠の無い回答に注記するか）。

    今のターンに根拠が無いとき（#1879）: 資料に無い・答えられないことだけを述べる回答は注記せずに
    `skipped`（`absence_only`）。会話の続きで、今のターンが根拠・データのツールで結果を得ずに
    答えた（前の回答の整形・言い換え。#1835 の `grounded_in_thread` で拒答にしない回答）なら、
    前のターンの検証で裏付けた根拠（`carried_evidence`）だけで検証する。
    """
    from app.features.agent.runtime import runtime_repository

    def save(content: dict[str, Any]) -> dict[str, Any]:
        runtime_repository.save_builtin_artifact(
            run_id, kind=ANSWER_VALIDATION_KIND, name=ANSWER_VALIDATION_NAME, content=content
        )
        return content

    run = runtime_repository.get_run(run_id)
    # 会話の続きでは、前のターン（モデルに渡す往復の数まで）の検証で回答の段落を裏付けた根拠も
    # 渡す（#1837。前のターンの事実を言い直した段落を、今のターンの根拠だけで外さない）。
    max_turns = ChatHistoryBudget.from_settings(get_settings()).max_turns
    previous = runtime_repository.thread_answer_validations(run_id) if max_turns > 0 else []
    carried = carried_evidence(previous[-max_turns:] if max_turns > 0 else [])
    # 回答が引用した根拠を先にして、上限で切っても検証に入れる（#1364）。
    groups = run_evidence_groups(run.steps, answer, carried=carried)
    if not groups:
        if KIND_RAG in declined_kinds(run.metadata):
            # 利用者が「資料の検索は不要」とした（#1445）。資料と照らし合わせない（注記しない）。
            return answer, save(validation_content(STATUS_SKIPPED, reason=REASON_RAG_DECLINED))
        if rag_tools and (is_clarification_only(answer) or nl2sql_clarification_pending(run.steps)):
            # 確認の質問だけの回答は資料の主張を含まない（「確かめていない」を足さない。#1306）。
            return answer, save(
                validation_content(STATUS_SKIPPED, reason=REASON_CLARIFICATION_ONLY)
            )
        if rag_tools and is_absence_only(answer):
            # 資料に無い・答えられないことだけを述べる回答（拒答）は資料の主張を含まない（#1879）。
            return answer, save(validation_content(STATUS_SKIPPED, reason=REASON_ABSENCE_ONLY))
        if carried and not tool_grounding.grounding_used(run.steps):
            # 前の回答の整形・言い換え（#1879）は、前のターンで裏付けた根拠で確かめる。
            groups = carried_evidence_groups(carried, answer)
    if not groups:
        if rag_tools and answer.strip():
            content = validation_content(
                STATUS_UNVALIDATED, reason=REASON_NO_RAG_EVIDENCE, message=NO_EVIDENCE_NOTICE
            )
            return with_no_evidence_notice(answer), save(content)
        return answer, save(validation_content(STATUS_SKIPPED, reason=REASON_NO_RAG_EVIDENCE))
    refs = [ref for _tool, items in groups for ref in items]
    if not answer.strip():
        content = validation_content(STATUS_SKIPPED, reason=REASON_EMPTY_ANSWER, evidence=refs)
        return answer, save(content)
    validations = [
        await _validate_with_connection(run, answer, evidence_tool, items, data_scopes)
        for evidence_tool, items in groups
    ]
    content, published = combine_validations(answer, validations)
    return published, save(content)


async def _validate_with_connection(
    run: Any,
    answer: str,
    evidence_tool: str,
    refs: list[dict[str, Any]],
    data_scopes: data_scope.AgentDataScopes | None = None,
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
    recorder = _ToolRecorder(run.id)
    # 根拠の読み直しも Run と同じデータの範囲で行う（サービストークンの claim。#1379）。
    recorder.data_scopes = dict(data_scopes or {})
    # 利用者が回答前の確認で答えた前提（#1898）。答えをそのまま述べた段落と、資料の規則を答えに
    # 当てはめた結論を、資料の根拠だけで「確かめられない」として外さない。
    premises = validation_premises(run.metadata, run.confirmations)
    step_id, result = await recorder.call(
        ToolCall(
            name=definition.name,
            arguments={
                "query": run.goal[:MAX_QUERY_CHARS],
                "answer": answer,
                "evidence": refs,
                # 要求の充足・業務ガイドの手順と影響範囲の決定的な検査（#1276）。
                **connection_check_inputs(run.steps, evidence_tool),
                **({"premises": premises} if premises else {}),
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
