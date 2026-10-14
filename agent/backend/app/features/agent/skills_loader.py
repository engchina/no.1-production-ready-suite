"""Skill 外部定義のローダ。

Claude / Codex の skill 規約(ディレクトリ + `SKILL.md`(YAML frontmatter + 本文)、
progressive disclosure)を、本プロダクトの Skill（instructions + MCP ツールの許可リスト）へ中立に
再マッピングする。`.claude` / `.codex` のような vendor 固有名は使わず、中立な
`AGENT_SKILLS_DIR`(既定 `skills/<id>/SKILL.md`)と `AGENT_SKILLS_DEFINITIONS_JSON`
を読む。不正定義は warning でスキップし、安全側へ縮退する。

SKILL.md と同じディレクトリの `references/` の下のテキスト / Markdown は、スキルの参照文書として
読む（#1803）。symlink は辿らず、ディレクトリの外は読まない。件数・大きさに上限を置き、超えた分は
読まずに warning を出す。`scripts/` などの実行するファイルは読まない・実行しない。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.features.agent.skills import AgentSkillDefinition, SkillReference

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]

# 参照文書（`references/`）。読むのはテキスト / Markdown だけ。
REFERENCES_DIR = "references"
REFERENCE_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
REFERENCE_MAX_FILES = 100
REFERENCE_MAX_FILE_BYTES = 1024 * 1024
REFERENCE_MAX_TOTAL_BYTES = 4 * 1024 * 1024


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
        "mcp_requirements": data.get("mcp_requirements") or [],
        "enabled": bool(data.get("enabled", True)),
        "tags": list(data.get("tags") or []),
        "source": source,
    }
    return AgentSkillDefinition.model_validate(payload)


def load_skill_references(skill_dir: Path) -> list[SkillReference]:
    """`<skill_dir>/references/**` のテキスト / Markdown を参照文書として読む。

    symlink（ファイル・ディレクトリ）は辿らず、解決したパスが `references/` の外なら読まない。
    件数・1 ファイル・合計の上限を超えた分は読まない。
    """
    root = skill_dir / REFERENCES_DIR
    if root.is_symlink() or not root.is_dir():
        return []
    resolved_root = root.resolve()
    references: list[SkillReference] = []
    total = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        # symlink のディレクトリは降りない（os.walk は followlinks=False でも一覧には出す）。
        dirnames[:] = sorted(name for name in dirnames if not (current_path / name).is_symlink())
        for name in sorted(filenames):
            file_path = current_path / name
            relative = file_path.relative_to(skill_dir).as_posix()
            if file_path.is_symlink() or file_path.suffix.lower() not in REFERENCE_SUFFIXES:
                continue
            if not file_path.resolve().is_relative_to(resolved_root):
                logger.warning("参照文書がディレクトリの外を指すので読みません: %s", relative)
                continue
            if len(references) >= REFERENCE_MAX_FILES:
                logger.warning("参照文書の件数の上限を超えたので読みません: %s", relative)
                continue
            size = file_path.stat().st_size
            if size > REFERENCE_MAX_FILE_BYTES or total + size > REFERENCE_MAX_TOTAL_BYTES:
                logger.warning("参照文書の大きさの上限を超えたので読みません: %s", relative)
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.warning("参照文書を読めませんでした: %s", relative)
                continue
            total += size
            references.append(
                SkillReference(path=relative, characters=len(content), content=content)
            )
    return references


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
        references = load_skill_references(skill_md.parent)
        skills.append(skill.model_copy(update={"references": references}) if references else skill)
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
