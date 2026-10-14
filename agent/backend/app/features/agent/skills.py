"""Agent Runtime の Skill registry。

Skill は Agent に割り当てる業務手順（instructions）と、使ってよい MCP ツールの許可リスト
（mcp_requirements）を持つ。組み込み Runtime が instructions を Agent の指示へ合成し、
許可リストのツールだけを LLM へ渡す。

`AGENT_SKILLS_DIR` の SKILL.md と同じディレクトリの `references/` は、スキルの参照文書
（`references`）として持つ（Agent Skills の progressive disclosure の第 3 段。#1803）。
本文は指示へ詰めず、パスの索引だけを渡し、必要なときに `skill_reference_read` で読ませる。

業務 Agent の公開の版は、使うスキルの中身（`PinnedSkill`）を固定する（#1855）。公開中の版の Run は
固定した中身で動き（`use_pinned_skills`・`resolve_skill`）、下書きの Run は今のスキルで動く。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from contextvars import ContextVar, Token
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


class PinnedSkillReference(BaseModel):
    """固定したスキルの参照文書の索引（本文は持たず、本文の hash で変わったかが分かる）"""

    path: str
    characters: int = 0
    sha256: str = ""


class PinnedSkill(BaseModel):
    """業務 Agent の公開の版に固定したスキルの中身（#1855）。

    公開中の版の Run は、今のスキルではなくこの中身（指示・説明・MCP のツール）で動く。
    `content_hash` はスキルの中身（参照文書の本文の hash を含む）の hash で、今のスキルと比べて
    「スキルが更新されています」を出す。参照文書の本文は固定しない（読むのは今の本文。
    `skill_reference_read` は索引にある参照文書だけを読ませる）。
    """

    id: str
    name: str
    description: str = ""
    instructions: str = ""
    mcp_requirements: list[SkillMcpRequirement] = Field(default_factory=list)
    references: list[PinnedSkillReference] = Field(default_factory=list)
    enabled: bool = True
    # 公開のときの由来層（builtin / project / env / runtime）。
    source: str = "runtime"
    content_hash: str = ""
    # 公開のときのスキルの更新日時（表示用）。
    skill_updated_at: datetime | None = None

    def definition(self) -> AgentSkillDefinition:
        """Run が使うスキルの定義（参照文書は索引だけ。本文は今のスキルから読む）。"""
        return AgentSkillDefinition(
            id=self.id,
            name=self.name,
            description=self.description,
            instructions=self.instructions,
            mcp_requirements=[item.model_copy(deep=True) for item in self.mcp_requirements],
            references=[
                SkillReference(path=item.path, characters=item.characters)
                for item in self.references
            ],
            enabled=self.enabled,
            source=self.source,
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def skill_content_hash(skill: AgentSkillDefinition) -> str:
    """スキルの中身の hash（名前・説明・指示・MCP のツール・有効・参照文書の本文。#1855）。

    SKILL.md のスキルは SKILL.md の内容（frontmatter と本文）と `references/` の本文から決まる値で、
    画面・API のスキルも同じ項目で比べる。更新日時・由来層は含めない（中身が同じなら同じ hash）。
    """
    payload = {
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "mcp_requirements": [
            {"server_id": item.server_id, "tool_names": list(item.tool_names)}
            for item in skill.mcp_requirements
        ],
        "enabled": skill.enabled,
        "references": [
            {"path": item.path, "sha256": _sha256(item.content)} for item in skill.references
        ],
    }
    return _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def pin_skill(skill: AgentSkillDefinition) -> PinnedSkill:
    """今のスキルの中身を固定する（業務 Agent の公開のとき。#1855）。"""
    return PinnedSkill(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        instructions=skill.instructions,
        mcp_requirements=[item.model_copy(deep=True) for item in skill.mcp_requirements],
        references=[
            PinnedSkillReference(
                path=item.path, characters=item.characters, sha256=_sha256(item.content)
            )
            for item in skill.references
        ],
        enabled=skill.enabled,
        source=skill.source,
        content_hash=skill_content_hash(skill),
        skill_updated_at=skill.updated_at,
    )


class AgentSkillListOutput(BaseModel):
    skills: list[AgentSkillDefinition]
    metadata: JsonObject = Field(default_factory=dict)


class SkillRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._skills: dict[str, AgentSkillDefinition] = {}
        self._builtin_ids: set[str] = set()
        self._hashes: dict[str, tuple[AgentSkillDefinition, str]] = {}

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

    def content_hash(self, skill_id: str) -> str | None:
        """今のスキルの中身の hash（無ければ None。#1855）。

        業務 Agent の一覧・保存のたびに「スキルが更新されています」を比べるため、定義の object
        ごとに覚える（定義を変えると object が置き換わるので、古い hash は使わない）。
        """
        with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                return None
            cached = self._hashes.get(skill_id)
            if cached is not None and cached[0] is skill:
                return cached[1]
            value = skill_content_hash(skill)
            self._hashes[skill_id] = (skill, value)
            return value


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


# この実行（async の task / thread）が使う、公開の版に固定したスキル（#1855）。None は今のスキル。
_pinned_skills: ContextVar[dict[str, AgentSkillDefinition] | None] = ContextVar(
    "agent_pinned_skills", default=None
)


def use_pinned_skills(
    pins: Sequence[PinnedSkill] | None,
) -> Token[dict[str, AgentSkillDefinition] | None]:
    """この実行のスキルを、公開の版に固定した中身にする（None は今のスキル）。

    戻り値の token を `reset_pinned_skills` に渡して戻す。`asyncio.to_thread` と task は
    context を写すため、ツールの呼び出し・指示の組み立ても同じ固定を使う。
    """
    value = None if pins is None else {item.id: item.definition() for item in pins}
    return _pinned_skills.set(value)


def reset_pinned_skills(token: Token[dict[str, AgentSkillDefinition] | None]) -> None:
    _pinned_skills.reset(token)


def resolve_skill(skill_id: str) -> AgentSkillDefinition | None:
    """実行が使うスキル（公開の版に固定した中身があればそれ、無ければ今のスキル。#1855）。

    固定した版に無いスキル（固定の後に足したもの）は使わない（None）。
    """
    pinned = _pinned_skills.get()
    if pinned is not None:
        found = pinned.get(skill_id)
        return found.model_copy(deep=True) if found is not None else None
    return skill_registry.get(skill_id)
