"""Agent Runtime の Skill registry。

Skill は Agent に割り当てる業務手順（instructions）と、使ってよい MCP ツールの許可リスト
（mcp_requirements）を持つ。組み込み Runtime が instructions を Agent の指示へ合成し、
許可リストのツールだけを LLM へ渡す。
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.features.agent.profile_name_migration import migrate_tool_name

JsonObject = dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


class SkillMcpRequirement(BaseModel):
    """Skill が使う MCP 接続とツールの許可リスト（#757）。

    `server_id` は MCP 接続の ID（`rag` / `nl2sql` / 登録した接続）。`control-plane` は
    Control Plane のツール（`tool_registry`）。`tool_names` が空なら接続のすべてのツールを使う。
    """

    server_id: str
    tool_names: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_saved_names(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("tool_names"), list):
            return {
                **value,
                "tool_names": [
                    migrate_tool_name(item) if isinstance(item, str) else item
                    for item in value["tool_names"]
                ],
            }
        return value


class AgentSkillDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    instructions: str = ""
    mcp_requirements: list[SkillMcpRequirement] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
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
                raise ValueError("組み込みのスキルと同じ ID は使えません。")
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
                raise ValueError(
                    "組み込み・ファイル・環境変数・プラグインのスキルは画面から削除できません。"
                )
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


skill_registry = SkillRegistry()

skill_registry.register(
    AgentSkillDefinition(
        id="business_rag_research",
        name="業務 RAG 調査",
        description="業務 RAG（MCP 接続 rag）を使って根拠付き情報を検索する。",
        instructions=(
            "ユーザーの目的を rag_search の query として扱い、根拠（evidence）に基づいて答える。"
            "対象の検索・回答プロファイルが分からなければ "
            "rag_list_search_answer_profiles で確かめる。"
            "回答に使った根拠（used_in_answer）を優先し、文書名と場所（locator の節・頁）を示す。"
            "根拠の excerpt が切り詰められている（truncated）か、前後の条件・例外を確かめる必要が"
            "あるときは、rag_read_source に document_id と chunk_id を渡して本文を読む。"
            "rag_search の outcome で答え方を決める。answered は根拠に沿って答える。"
            "conditional は conditions（説明が成り立つ条件）と gaps（資料で確かめられない点）を"
            "示し、条件ごとに分けて答える。needs_environment_data は confirmations（確かめる"
            "現場の値・記録）を挙げ、現場の値を推測で断定しない。insufficient_evidence は"
            "資料で確かめられなかったことを伝え、推測で補わない。requests の missing は"
            "答えていない要求として利用者に示す。"
        ),
        mcp_requirements=[
            SkillMcpRequirement(
                server_id="rag",
                tool_names=["rag_search", "rag_list_search_answer_profiles", "rag_read_source"],
            )
        ],
        tags=["rag", "research", "business-data"],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="structured_data_query",
        name="構造化データ照会",
        description="NL2SQL（MCP 接続 nl2sql）へ質問を渡して表形式の結果を取得する。",
        instructions=(
            "SQL は監査・説明用途として受け取り、この Runtime 内では実行しない。"
            "業務プロファイルが分からなければ nl2sql_recommend_profile で選ぶ。"
            "nl2sql_query / nl2sql_get_job は、ジョブが終わるまでツールの中で待ってから返す。"
            "それでも status が pending / running なら、nl2sql_get_job に job_id と "
            "wait_seconds=40 を渡して続きを取る。結果が出ないまま答えるときは、"
            "実行中であることと job_id を伝え、結果を推測で補わない。"
            "status が error なら error_message と error_code を伝える。"
        ),
        mcp_requirements=[
            SkillMcpRequirement(
                server_id="nl2sql",
                tool_names=[
                    "nl2sql_query",
                    "nl2sql_get_job",
                    "nl2sql_list_profiles",
                    "nl2sql_recommend_profile",
                ],
            )
        ],
        tags=["nl2sql", "structured-data", "audit-sql"],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id="rag_then_structured_data",
        name="RAG 後に構造化データ照会",
        description="業務 RAG で文脈を確認した後、NL2SQL へ同じ目的を渡す。",
        instructions="非構造文脈と構造化表の両方が必要な調査に使う。",
        mcp_requirements=[
            SkillMcpRequirement(server_id="rag", tool_names=["rag_search"]),
            SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query", "nl2sql_get_job"]),
        ],
        tags=["rag", "nl2sql", "business-data"],
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
