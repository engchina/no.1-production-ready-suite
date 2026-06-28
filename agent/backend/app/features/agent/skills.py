"""Agent Runtime の Skill registry。

Skill は外部能力を直接実行せず、標準 ToolCall の計画へ展開する。
実行・承認・監査・artifact 化は Runtime の通常ステップに委ねる。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

JsonObject = dict[str, Any]
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _now() -> datetime:
    return datetime.now(UTC)


class SkillToolCallTemplate(BaseModel):
    name: str
    arguments: JsonObject = Field(default_factory=dict)
    trace_id: str | None = None


class SkillMcpRequirement(BaseModel):
    """Skill の内部実装が必要とする MCP server / tool allowlist。"""

    server_id: str
    tool_names: list[str] = Field(default_factory=list)


class AgentSkillDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    instructions: str = ""
    mcp_requirements: list[SkillMcpRequirement] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    # 1 release の後方互換。新規 Runtime は mcp_requirements を使用する。
    tool_calls: list[SkillToolCallTemplate] = Field(default_factory=list)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    # 由来層: builtin(code) / project(SKILL.md) / env(JSON 宣言) / runtime(UI/API)。
    # builtin は予約 id として保護し、宣言・runtime からの上書き/削除を拒否する。
    source: str = "runtime"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AgentSkillListOutput(BaseModel):
    skills: list[AgentSkillDefinition]
    metadata: JsonObject = Field(default_factory=dict)


class AgentSkillRunInput(BaseModel):
    skill_id: str
    goal: str
    arguments: JsonObject = Field(default_factory=dict)
    trace_id: str | None = None


class AgentSkillPlanOutput(BaseModel):
    skill_id: str
    skill_name: str
    goal: str
    instructions: str = ""
    tool_calls: list[SkillToolCallTemplate] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class SkillRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._skills: dict[str, AgentSkillDefinition] = {}
        self._builtin_ids: set[str] = set()

    def register(self, skill: AgentSkillDefinition) -> None:
        """組込み(builtin)skill を登録する。id は予約され保護される。"""
        with self._lock:
            stamped = skill.model_copy(deep=True, update={"source": "builtin"})
            self._skills[stamped.id] = stamped
            self._builtin_ids.add(stamped.id)

    def set_declared(self, source: str, skills: list[AgentSkillDefinition]) -> None:
        """宣言層(project / env)を source 単位で置換する。builtin id は無視。"""
        with self._lock:
            for skill_id in [
                sid
                for sid, skill in self._skills.items()
                if skill.source == source and sid not in self._builtin_ids
            ]:
                del self._skills[skill_id]
            for skill in skills:
                if skill.id in self._builtin_ids:
                    continue
                self._skills[skill.id] = skill.model_copy(deep=True, update={"source": source})

    def upsert_custom(self, skill: AgentSkillDefinition) -> AgentSkillDefinition:
        """runtime(UI/API)層へ追加・更新する。builtin id は拒否。"""
        with self._lock:
            if skill.id in self._builtin_ids:
                raise ValueError("builtin skill cannot be overridden")
            stored = skill.model_copy(deep=True, update={"source": "runtime", "updated_at": _now()})
            self._skills[stored.id] = stored
            return stored.model_copy(deep=True)

    def remove(self, skill_id: str) -> None:
        """runtime 層の skill のみ削除する。builtin / 宣言層は拒否。"""
        with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                raise KeyError(skill_id)
            if skill.source != "runtime":
                raise ValueError(f"{skill.source} skill cannot be removed via API")
            del self._skills[skill_id]

    def export(self, source: str | None = None) -> list[AgentSkillDefinition]:
        with self._lock:
            return [
                skill.model_copy(deep=True)
                for skill in sorted(self._skills.values(), key=lambda item: item.id)
                if source is None or skill.source == source
            ]

    def list(self) -> list[AgentSkillDefinition]:
        with self._lock:
            return [
                skill.model_copy(deep=True)
                for skill in sorted(self._skills.values(), key=lambda item: item.id)
            ]

    def get(self, skill_id: str) -> AgentSkillDefinition | None:
        with self._lock:
            skill = self._skills.get(skill_id)
            return skill.model_copy(deep=True) if skill is not None else None

    def plan(self, request: AgentSkillRunInput) -> AgentSkillPlanOutput:
        skill = self.get(request.skill_id)
        if skill is None:
            raise KeyError(request.skill_id)
        if not skill.enabled:
            raise ValueError("skill disabled")
        render_context: JsonObject = {
            "goal": request.goal,
            "arguments": request.arguments,
            "trace_id": request.trace_id,
            "skill_id": skill.id,
        }
        planned: list[SkillToolCallTemplate] = []
        for index, template in enumerate(skill.tool_calls, start=1):
            trace_id = _render_optional_string(template.trace_id, render_context)
            if trace_id is None and request.trace_id:
                trace_id = f"{request.trace_id}:skill:{skill.id}:{index}"
            planned.append(
                SkillToolCallTemplate(
                    name=template.name,
                    arguments=_render_object(template.arguments, render_context),
                    trace_id=trace_id,
                )
            )
        return AgentSkillPlanOutput(
            skill_id=skill.id,
            skill_name=skill.name,
            goal=request.goal,
            instructions=skill.instructions,
            tool_calls=planned,
            metadata={
                "skill_tags": list(skill.tags),
                "tool_call_count": len(planned),
            },
        )


def _render_object(value: JsonObject, context: JsonObject) -> JsonObject:
    rendered = _render_value(value, context)
    return rendered if isinstance(rendered, dict) else {}


def _render_value(value: Any, context: JsonObject) -> Any:
    if isinstance(value, dict):
        rendered: JsonObject = {}
        for key, item in value.items():
            rendered_item = _render_value(item, context)
            if rendered_item is not None:
                rendered[key] = rendered_item
        return rendered
    if isinstance(value, list):
        return [
            rendered_item
            for item in value
            if (rendered_item := _render_value(item, context)) is not None
        ]
    if isinstance(value, str):
        return _render_string(value, context)
    return value


def _render_optional_string(value: str | None, context: JsonObject) -> str | None:
    if value is None:
        return None
    rendered = _render_string(value, context)
    return rendered if isinstance(rendered, str) and rendered else None


def _render_string(value: str, context: JsonObject) -> Any:
    exact = _exact_placeholder(value)
    if exact is not None:
        return _resolve_placeholder(exact, context)

    def replace(match: re.Match[str]) -> str:
        resolved = _resolve_placeholder(match.group(1), context)
        return "" if resolved is None else str(resolved)

    return _PLACEHOLDER_PATTERN.sub(replace, value)


def _exact_placeholder(value: str) -> str | None:
    match = _PLACEHOLDER_PATTERN.fullmatch(value.strip())
    return match.group(1) if match else None


def _resolve_placeholder(path: str, context: JsonObject) -> Any:
    parts = [part for part in path.split(".") if part]
    current: Any = context
    for part in parts:
        if isinstance(current, Mapping):
            current = current.get(part)
            continue
        return None
    return current


skill_registry = SkillRegistry()

skill_registry.register(
    AgentSkillDefinition(
        id="business_rag_research",
        name="業務 RAG 調査",
        description="外部業務 RAG を使って根拠付き情報を検索する。",
        instructions="ユーザーの目的を外部 RAG の query として扱い、引用と根拠を返す。",
        mcp_requirements=[
            SkillMcpRequirement(server_id="control-plane", tool_names=["external_rag_search"])
        ],
        tags=["rag", "research", "business-data"],
        tool_calls=[
            SkillToolCallTemplate(
                name="external_rag_search",
                arguments={
                    "query": "${goal}",
                    "business_view_id": "${arguments.business_view_id}",
                    "filters": "${arguments.filters}",
                    "top_k": "${arguments.top_k}",
                },
            )
        ],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="structured_data_query",
        name="構造化データ照会",
        description="外部 NL2SQL/構造化データサービスへ質問を渡して表形式結果を取得する。",
        instructions="SQL は監査・説明用途として受け取り、この Runtime 内では実行しない。",
        mcp_requirements=[
            SkillMcpRequirement(server_id="control-plane", tool_names=["external_nl2sql_query"])
        ],
        tags=["nl2sql", "structured-data", "audit-sql"],
        tool_calls=[
            SkillToolCallTemplate(
                name="external_nl2sql_query",
                arguments={
                    "question": "${goal}",
                    "data_domain_id": "${arguments.data_domain_id}",
                    "business_view_id": "${arguments.business_view_id}",
                    "filters": "${arguments.filters}",
                    "limit": "${arguments.limit}",
                    "mode": "${arguments.mode}",
                    "include_sql": "${arguments.include_sql}",
                },
            )
        ],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="mcp_tool_discovery",
        name="MCP ツール探索",
        description="外部 MCP gateway の tools/list を呼んで利用可能な tool を確認する。",
        instructions="MCP tool 実行前に schema と説明を確認する。",
        mcp_requirements=[
            SkillMcpRequirement(server_id="control-plane", tool_names=["external_mcp_list_tools"])
        ],
        tags=["mcp", "tool-discovery"],
        tool_calls=[
            SkillToolCallTemplate(
                name="external_mcp_list_tools",
                arguments={
                    "server_id": "${arguments.server_id}",
                },
            )
        ],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="mcp_tool_call",
        name="MCP ツール実行",
        description="外部 MCP gateway 経由で指定 tool を実行する。",
        instructions="MCP tool の schema に合わせた arguments を渡す。副作用は通常承認対象になる。",
        mcp_requirements=[
            SkillMcpRequirement(server_id="control-plane", tool_names=["external_mcp_call"])
        ],
        tags=["mcp", "tool-call"],
        tool_calls=[
            SkillToolCallTemplate(
                name="external_mcp_call",
                arguments={
                    "tool_name": "${arguments.tool_name}",
                    "arguments": "${arguments.arguments}",
                    "server_id": "${arguments.server_id}",
                },
            )
        ],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="rag_then_structured_data",
        name="RAG 後に構造化データ照会",
        description="業務 RAG で文脈を確認した後、外部 NL2SQL へ同じ目的を渡す。",
        instructions="非構造文脈と構造化表の両方が必要な調査に使う。",
        mcp_requirements=[
            SkillMcpRequirement(
                server_id="control-plane",
                tool_names=["external_rag_search", "external_nl2sql_query"],
            )
        ],
        tags=["rag", "nl2sql", "business-data"],
        tool_calls=[
            SkillToolCallTemplate(
                name="external_rag_search",
                arguments={
                    "query": "${goal}",
                    "business_view_id": "${arguments.business_view_id}",
                    "filters": "${arguments.filters}",
                    "top_k": "${arguments.top_k}",
                },
            ),
            SkillToolCallTemplate(
                name="external_nl2sql_query",
                arguments={
                    "question": "${goal}",
                    "data_domain_id": "${arguments.data_domain_id}",
                    "business_view_id": "${arguments.business_view_id}",
                    "filters": "${arguments.filters}",
                    "limit": "${arguments.limit}",
                    "mode": "${arguments.mode}",
                    "include_sql": "${arguments.include_sql}",
                },
            ),
        ],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="workspace_command",
        name="ワークスペースコマンド",
        description="許可済み prefix のコマンドを sandbox command tool として計画する。",
        instructions="コード調査・テスト・生成物確認のために、許可されたコマンドだけを使う。",
        mcp_requirements=[
            SkillMcpRequirement(server_id="control-plane", tool_names=["sandbox_command_run"])
        ],
        tags=["command", "workspace", "sandbox"],
        tool_calls=[
            SkillToolCallTemplate(
                name="sandbox_command_run",
                arguments={
                    "command": "${arguments.command}",
                    "cwd": "${arguments.cwd}",
                    "timeout_seconds": "${arguments.timeout_seconds}",
                    "output_limit_bytes": "${arguments.output_limit_bytes}",
                },
            )
        ],
    )
)


def reload_declared_skills() -> dict[str, int]:
    """設定の中立ディレクトリ / JSON 宣言を読み込み、project / env 層を更新する。

    Claude/Codex に倣い宣言(ファイル/env)を永続層、runtime(API/UI)を
    セッション層とする。env を後勝ちにするため project → env の順で適用する。
    """
    from app.features.agent.skills_loader import (
        load_skills_from_dir,
        load_skills_from_json,
    )
    from app.settings import get_settings

    settings = get_settings()
    project = load_skills_from_dir(settings.agent_skills_dir)
    env = load_skills_from_json(settings.agent_skills_definitions_json)
    skill_registry.set_declared("project", project)
    skill_registry.set_declared("env", env)
    return {"project": len(project), "env": len(env)}


# 起動時に宣言層を読み込む(未設定なら何もしない)。
reload_declared_skills()
