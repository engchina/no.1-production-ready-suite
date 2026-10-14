"""Agent Runtime の Skill registry。

Skill は Agent に割り当てる業務手順（instructions）と、使ってよい MCP ツールの許可リスト
（mcp_requirements）を持つ。組み込み Runtime が instructions を Agent の指示へ合成し、
許可リストのツールだけを LLM へ渡す。

`AGENT_SKILLS_DIR` の SKILL.md と同じディレクトリの `references/` は、スキルの参照文書
（`references`）として持つ（Agent Skills の progressive disclosure の第 3 段。#1803）。
本文は指示へ詰めず、パスの索引だけを渡し、必要なときに `skill_reference_read` で読ませる。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
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
    `tool_names` は MCP のツールの素の名前（`rag_search`）。Skill の指示も素の名前で書いてよく、
    組み込み Runtime が Run のときにモデルへ渡す名前（`rag__rag_search`）へ書き直す（#1303）。
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


class SkillReference(BaseModel):
    """スキルの参照文書（SKILL.md と同じディレクトリの `references/` の 1 ファイル）。

    `path` はスキルのディレクトリからの相対パス（`references/guide.md`）。本文（`content`）は
    API の応答・保存に出さず（`exclude`）、`skill_reference_read` だけが読む。
    """

    path: str
    characters: int = 0
    content: str = Field(default="", exclude=True)


class AgentSkillDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    instructions: str = ""
    mcp_requirements: list[SkillMcpRequirement] = Field(default_factory=list)
    # 参照文書（`AGENT_SKILLS_DIR` のスキルだけが持つ。画面・API・env の宣言では付けない）。
    references: list[SkillReference] = Field(default_factory=list)
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
                raise ValueError("組み込み・ファイル・環境変数のスキルは画面から削除できません。")
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

# 組み込みの Skill の ID（初期の業務 Agent・業種テンプレートが使う）。
BUSINESS_RAG_RESEARCH_SKILL_ID = "business_rag_research"
STRUCTURED_DATA_QUERY_SKILL_ID = "structured_data_query"
RAG_AND_STRUCTURED_DATA_SKILL_ID = "rag_and_structured_data"
# 改名した組み込みの Skill（旧 ID → 新 ID）。保存済みの業務 Agent とその版の読み込みで置き換える。
# #1573: 順序を固定した「RAG 後に構造化データ照会」を、順序を決めない横断調査にした。
RENAMED_SKILL_IDS: dict[str, str] = {
    "rag_then_structured_data": RAG_AND_STRUCTURED_DATA_SKILL_ID,
}


# 組み込みの Skill の指示（Markdown）の置き場所。ID ごとに 1 ファイル（#1795）。
BUILTIN_SKILL_INSTRUCTIONS_DIR = Path(__file__).with_name("builtin_skills")


def _builtin_instructions(skill_id: str) -> str:
    """組み込みの Skill の指示を `builtin_skills/<id>.md` から読む（#1795）。

    指示は手順で進める Markdown で書く（見出しの並びと書き方は
    docs/agent-control-plane-design.md）。Python の文字列の連結では改行・見出し・箇条書きを
    保てず、画面でも 1 つの段落になっていた。
    """
    path = BUILTIN_SKILL_INSTRUCTIONS_DIR / f"{skill_id}.md"
    return path.read_text(encoding="utf-8").strip()


def migrate_skill_ids(skill_ids: list[str]) -> list[str]:
    """旧 ID を新 ID に置き換える（順序を保ち、置き換えで重なった ID は 1 つにする）。"""
    migrated: list[str] = []
    for skill_id in skill_ids:
        current = RENAMED_SKILL_IDS.get(skill_id, skill_id)
        if current not in migrated:
            migrated.append(current)
    return migrated


skill_registry.register(
    AgentSkillDefinition(
        id=BUSINESS_RAG_RESEARCH_SKILL_ID,
        name="業務 RAG 調査",
        description="業務 RAG（MCP 接続 rag）を使って根拠付き情報を検索する。",
        instructions=_builtin_instructions(BUSINESS_RAG_RESEARCH_SKILL_ID),
        mcp_requirements=[
            SkillMcpRequirement(
                server_id="rag",
                tool_names=[
                    "rag_search",
                    "rag_list_search_answer_profiles",
                    "rag_read_source",
                    "rag_lookup_guides",
                    "rag_retrieve_evidence",
                    "rag_outline",
                    "rag_read_document",
                    "rag_read_section",
                ],
            )
        ],
        tags=["rag", "research", "business-data"],
    )
)
skill_registry.register(
    AgentSkillDefinition(
        id=STRUCTURED_DATA_QUERY_SKILL_ID,
        name="構造化データ照会",
        description="NL2SQL（MCP 接続 nl2sql）へ質問を渡して表形式の結果を取得する。",
        instructions=_builtin_instructions(STRUCTURED_DATA_QUERY_SKILL_ID),
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
        id=RAG_AND_STRUCTURED_DATA_SKILL_ID,
        name="資料とデータの横断調査",
        description=(
            "業務 RAG（資料）と NL2SQL（業務データ）のどちらを使うかをその都度判断し、"
            "順序を決めずに繰り返し使って調べる。"
        ),
        instructions=_builtin_instructions(RAG_AND_STRUCTURED_DATA_SKILL_ID),
        mcp_requirements=[
            SkillMcpRequirement(server_id="rag", tool_names=["rag_search"]),
            SkillMcpRequirement(
                server_id="nl2sql",
                tool_names=[
                    "nl2sql_query",
                    "nl2sql_get_job",
                    "nl2sql_list_profiles",
                    "nl2sql_recommend_profile",
                ],
            ),
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
