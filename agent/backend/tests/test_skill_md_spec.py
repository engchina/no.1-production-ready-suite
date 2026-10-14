"""組み込みのスキル（repo 直下の `skills/`）は Agent Skills の仕様と外部へ配る形を満たす（#1816）。

- 仕様の検証は公式の `skills-ref`（anthropics/agentskills の参照実装）の `validate`。
- 仕様・best practices の目安（本文 500 行以内、参照は本文から 1 段の深さ）と、本プロダクトの拡張
  （`metadata` の MCP ツールの宣言が RAG / NL2SQL の MCP の契約にあるツールか）を確かめる。
- Claude Code の plugin marketplace（`.claude-plugin/marketplace.json` と
  `skills/.claude-plugin/plugin.json`）の必須の項目と、plugin の置き場所。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import skills_ref  # type: ignore[import-untyped]  # 型の情報（py.typed）が無い
import yaml

from app.features.agent.skills import BUILTIN_SKILL_IDS, BUILTIN_SKILLS_DIR, skill_registry
from app.features.agent.skills_loader import METADATA_MCP_TOOLS

REPO_ROOT = BUILTIN_SKILLS_DIR.parent
CONTRACTS = REPO_ROOT / "platform/contracts/mcp"
SKILL_DIRS = sorted(
    path for path in BUILTIN_SKILLS_DIR.iterdir() if path.is_dir() and not path.name.startswith(".")
)
SKILL_NAMES = [path.name for path in SKILL_DIRS]


def _frontmatter(skill_dir: Path) -> tuple[dict[str, object], str]:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    _, raw, body = text.split("---", 2)
    data = yaml.safe_load(raw)
    assert isinstance(data, dict)
    return data, body


def _contract_tools(server: str) -> set[str]:
    contract = json.loads((CONTRACTS / f"{server}-tools.json").read_text(encoding="utf-8"))
    return {str(tool["name"]) for tool in contract["tools"]}


def test_builtin_skills_are_the_skill_folders() -> None:
    assert set(SKILL_NAMES) == set(BUILTIN_SKILL_IDS)
    for name in SKILL_NAMES:
        skill = skill_registry.get(name)
        assert skill is not None and skill.source == "builtin"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_NAMES)
def test_skill_folder_passes_the_reference_validator(skill_dir: Path) -> None:
    assert skills_ref.validate(skill_dir) == []


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_NAMES)
def test_skill_md_follows_best_practices(skill_dir: Path) -> None:
    data, body = _frontmatter(skill_dir)
    description = str(data["description"])
    # 外部の Agent が選べるよう、何をするか＋いつ使うか（「使う」）と MCP サーバーを書く。
    assert "使う" in description and "MCP サーバー" in description
    # 本文は 500 行以内。長い参照は references/ に出し、本文から 1 段の深さで参照する。
    assert len(body.strip().splitlines()) <= 500
    linked = set(re.findall(r"\]\((references/[^)]+)\)", body))
    files = (
        {
            path.relative_to(skill_dir).as_posix()
            for path in (skill_dir / "references").rglob("*")
            if path.is_file()
        }
        if (skill_dir / "references").is_dir()
        else set()
    )
    assert linked == files
    for reference in files:
        assert "](references/" not in (skill_dir / reference).read_text(encoding="utf-8")


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=SKILL_NAMES)
def test_skill_mcp_tools_exist_in_the_mcp_contracts(skill_dir: Path) -> None:
    data, body = _frontmatter(skill_dir)
    metadata = data["metadata"]
    assert isinstance(metadata, dict)
    declared = str(metadata[METADATA_MCP_TOOLS]).split()
    for token in declared:
        server, _, tool = token.partition(":")
        assert server in {"rag", "nl2sql"}
        assert tool in _contract_tools(server), token
        # 本文の「使う MCP ツール」に、どの Agent でも分かる書き方（MCP サーバー名と素のツール名）
        # で出す。
        assert f"MCP サーバー `{server}`" in body
        assert f"`{tool}`" in body
    skill = skill_registry.get(skill_dir.name)
    assert skill is not None
    assert sorted(
        f"{item.server_id}:{tool}" for item in skill.mcp_requirements for tool in item.tool_names
    ) == sorted(declared)


def test_claude_code_plugin_marketplace_lists_the_skills_folder() -> None:
    marketplace = json.loads(
        (REPO_ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    )
    assert marketplace["name"] and marketplace["owner"]["name"]
    [entry] = marketplace["plugins"]
    assert entry["source"] == "./skills"
    plugin_root = REPO_ROOT / entry["source"]
    manifest = json.loads((plugin_root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    # entry の名前（install の ID）と plugin.json の名前（スキルの前置き）をそろえる。
    assert manifest["name"] == entry["name"]
    # plugin の root（skills/）の直下の <name>/SKILL.md をスキルとして読ませる。
    assert manifest["skills"] == "./"
