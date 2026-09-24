"""Skill 外部定義のローダ。

Claude / Codex の skill 規約(ディレクトリ + `SKILL.md`(YAML frontmatter + 本文)、
progressive disclosure)を、本プロダクトの決定論的 `tool_calls` テンプレートへ中立に
再マッピングする。`.claude` / `.codex` のような vendor 固有名は使わず、中立な
`AGENT_SKILLS_DIR`(既定 `skills/<id>/SKILL.md`)と `AGENT_SKILLS_DEFINITIONS_JSON`
を読む。不正定義は warning でスキップし、安全側へ縮退する。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.features.agent.skills import AgentSkillDefinition

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]


def _split_frontmatter(text: str) -> tuple[JsonObject, str]:
    """`---` 区切りの YAML frontmatter と本文を分離する。"""
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    closing: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        return {}, text.strip()
    frontmatter_text = "\n".join(lines[1:closing])
    body = "\n".join(lines[closing + 1 :]).strip()
    try:
        data = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return {}, body
    return (data if isinstance(data, dict) else {}), body


def _skill_from_mapping(
    data: JsonObject,
    body: str,
    *,
    fallback_id: str,
    source: str,
) -> AgentSkillDefinition | None:
    skill_id = str(data.get("id") or fallback_id or "").strip()
    if not skill_id:
        return None
    instructions = body.strip() or str(data.get("instructions") or "")
    payload: JsonObject = {
        "id": skill_id,
        "name": str(data.get("name") or skill_id),
        "description": str(data.get("description") or ""),
        "instructions": instructions,
        "tool_calls": data.get("tool_calls") or [],
        "enabled": bool(data.get("enabled", True)),
        "tags": list(data.get("tags") or []),
        "source": source,
    }
    return AgentSkillDefinition.model_validate(payload)


def load_skills_from_dir(path: str | None) -> list[AgentSkillDefinition]:
    """`<path>/<id>/SKILL.md` を走査して project 層の skill を読む。"""
    if not path or not str(path).strip():
        return []
    base = Path(path)
    if not base.is_dir():
        return []
    skills: list[AgentSkillDefinition] = []
    seen: set[str] = set()
    for skill_md in sorted(base.glob("*/SKILL.md")):
        fallback_id = skill_md.parent.name
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            logger.warning("SKILL.md を読めませんでした: %s", skill_md)
            continue
        data, body = _split_frontmatter(text)
        try:
            skill = _skill_from_mapping(data, body, fallback_id=fallback_id, source="project")
        except ValidationError as exc:
            logger.warning("不正な SKILL.md をスキップ: %s (%s)", skill_md, exc)
            continue
        if skill is None or skill.id in seen:
            continue
        seen.add(skill.id)
        skills.append(skill)
    return skills


def load_skills_from_json(raw: str | None) -> list[AgentSkillDefinition]:
    """`AGENT_SKILLS_DEFINITIONS_JSON` を env 層の skill 宣言として読む。"""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("AGENT_SKILLS_DEFINITIONS_JSON が不正な JSON です")
        return []
    items: object
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("skills")
    else:
        items = None
    if not isinstance(items, list):
        return []
    skills: list[AgentSkillDefinition] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            skill = _skill_from_mapping(
                item, "", fallback_id=str(item.get("id") or ""), source="env"
            )
        except ValidationError as exc:
            logger.warning("JSON 宣言の不正 skill をスキップ: %s", exc)
            continue
        if skill is None or skill.id in seen:
            continue
        seen.add(skill.id)
        skills.append(skill)
    return skills
