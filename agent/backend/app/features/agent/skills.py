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

    def replace_runtime(self, skills: list[AgentSkillDefinition]) -> list[str]:
        """runtime(UI/API)層を保存先の定義で置き換える（migration の後の読み直し。#1844）。

        builtin id の定義は登録せず（起動時の復元と同じ）、その id を返す。
        """
        skipped: list[str] = []
        with self._lock:
            for skill_id in [sid for sid, item in self._skills.items() if item.source == "runtime"]:
                del self._skills[skill_id]
            for skill in skills:
                if skill.id in self._builtin_ids:
                    skipped.append(skill.id)
                    continue
                self._skills[skill.id] = skill.model_copy(
                    deep=True, update={"source": "runtime", "updated_at": _now()}
                )
        return skipped

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

# 組み込みの Skill の ID（初期の業務 Agent・業種テンプレートが使う）。Agent Skills の `name`
# （小文字・数字・ハイフン。フォルダ名と同じ）で、定義は repo 直下の
# `skills/<name>/SKILL.md`（#1816）。
BUSINESS_RAG_RESEARCH_SKILL_ID = "business-rag-research"
STRUCTURED_DATA_QUERY_SKILL_ID = "structured-data-query"
RAG_AND_STRUCTURED_DATA_SKILL_ID = "rag-and-structured-data"
BUILTIN_SKILL_IDS: tuple[str, ...] = (
    BUSINESS_RAG_RESEARCH_SKILL_ID,
    STRUCTURED_DATA_QUERY_SKILL_ID,
    RAG_AND_STRUCTURED_DATA_SKILL_ID,
)

# 組み込みのスキルの置き場所（repo 直下の `skills/`。3 製品のどれにも属さず、Claude Code・
# Codex などの外部の Agent にも同じフォルダを入れる。#1816）。
# `agent/backend/app/features/agent/skills.py` から 5 段上。
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parents[5] / "skills"


def register_builtin_skills(directory: Path = BUILTIN_SKILLS_DIR) -> list[str]:
    """`skills/<name>/SKILL.md` の組み込みのスキルを登録する。

    指示・説明・使うツールはコードに持たず、SKILL.md を正本にする。

    組み込みの Agent・業種テンプレートが使う `BUILTIN_SKILL_IDS` が欠けたら起動を止める（配備で
    `skills/` が読めないまま、スキルの無い業務 Agent を動かさない）。
    """
    from app.features.agent.skills_loader import load_skills_from_dir

    skills = load_skills_from_dir(str(directory), source="builtin")
    missing = sorted(set(BUILTIN_SKILL_IDS) - {skill.id for skill in skills})
    if missing:
        raise RuntimeError(
            f"組み込みのスキル（{', '.join(missing)}）が {directory} にありません。"
            "repo 直下の skills/<name>/SKILL.md を配備してください。"
        )
    for skill in skills:
        skill_registry.register(skill)
    return [skill.id for skill in skills]


register_builtin_skills()


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
