"""回答プロンプトの画面の「各段のプロンプト」とコードのずれを防ぐ(#2054)。

- 一覧の各プロンプトの定数は、実行のコード(一覧の定義の外)から参照されている。
- 実行のコードが定義するプロンプトの定数は、一覧に入っているか、載せない理由がある。
- 段・区分・部分には画面の文言(i18n)がある。
"""

from __future__ import annotations

import ast
import importlib
import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

from app.rag.answer_prompts import PROMPT_PHASES, PROMPT_STAGES, readonly_prompt_stages

BACKEND = Path(__file__).resolve().parents[1]
RAG = BACKEND.parent
# 実行のコード(package 名, 置き場所)。テスト・CLI の置き場所は中に無い。
RUNTIME_PACKAGES = (
    ("app", BACKEND / "app"),
    ("rag_engine", RAG / "packages" / "rag_engine" / "rag_engine"),
)
I18N = RAG / "frontend" / "src" / "lib" / "i18n.ts"
# 一覧を作る module 自身の参照は「使われている」に数えない。
LIST_MODULE = "app.rag.answer_prompts"

# プロンプトの定数とみなす名前(module の直下に置く文字列か dict)。
PROMPT_NAME = re.compile(
    r"^_?[A-Z][A-Z0-9_]*(PROMPT|PROMPT_TEMPLATE|INSTRUCTIONS?|REQUIREMENTS|_RULE)$"
)

# 一覧に載せないプロンプトの定数と、その理由。
EDITABLE_DEFAULT = "編集できるプロンプトの既定値(画面は編集欄で示す)"
PROBE_ONLY = "Enterprise AI の契約の確認の CLI だけ"
NOT_LISTED = {
    "rag_engine.knowledge.prompt_files:DEFAULT_IMAGE_RETRIEVAL_PROMPT": EDITABLE_DEFAULT,
    "rag_engine.knowledge.prompt_files:DEFAULT_VLM_ANSWER_PROMPT": EDITABLE_DEFAULT,
    # 一覧に載せた定数へ組み込み済みの部品。
    "rag_engine.generation.query_prompts:_EXPANSION_INSTRUCTIONS": (
        "QUERY_ROUTING_PROMPT_TEMPLATE と QUERY_EXPANSION_PROMPT_TEMPLATE に組み込み済み"
    ),
    # 検証・確認の CLI だけが使う(回答・取込・品質評価の経路では使わない)。
    "rag_engine.evaluation.crag_eval:CRAG_JUDGE_SYSTEM_PROMPT": "検証 CLI(answer_verify_cli)だけ",
    "app.rag.enterprise_ai_probe:DEFAULT_LLM_PROMPT": PROBE_ONLY,
    "app.rag.enterprise_ai_probe:DEFAULT_VLM_PROMPT": PROBE_ONLY,
    "app.rag.file_processing_staging:FILE_PROCESSING_STAGING_PROMPT": "staging の確認だけ",
    # 解析の指示(ExtractionStrategy.prompt)は組み立てるが、OCI Generative AI (Vision) の解析を
    # 削除した後(#2042)はどのモデルにも渡していない。
    "app.rag.ingestion_strategy:BASE_EXTRACTION_REQUIREMENTS": "モデルに渡していない",
    "app.rag.ingestion_strategy:PROFILE_INSTRUCTIONS": "モデルに渡していない",
}


def _runtime_files() -> Iterator[tuple[str, Path]]:
    for package, root in RUNTIME_PACKAGES:
        for path in sorted(root.rglob("*.py")):
            parts = path.relative_to(root).with_suffix("").parts
            module = ".".join((package, *parts))
            yield (module.removesuffix(".__init__"), path)


@cache
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_level_names(tree: ast.Module) -> Iterator[str]:
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name):
                yield target.id


def _defined_prompt_constants() -> set[str]:
    """実行のコードが module の直下に置くプロンプトの定数(``module:名前``)。"""
    found: set[str] = set()
    for module, path in _runtime_files():
        if module == LIST_MODULE:
            continue
        names = [name for name in _module_level_names(_tree(path)) if PROMPT_NAME.match(name)]
        if not names:
            continue
        loaded = importlib.import_module(module)
        for name in names:
            if isinstance(getattr(loaded, name, None), str | dict):
                found.add(f"{module}:{name}")
    return found


def _listed_refs() -> list[str]:
    return [ref for stage in PROMPT_STAGES for _, ref in stage.parts]


def _loads(tree: ast.AST, name: str) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
    )


def _imports(tree: ast.AST, module: str, name: str) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == module
        and any(alias.name == name for alias in node.names)
        for node in ast.walk(tree)
    )


def _runtime_uses(ref: str) -> int:
    """定数を実行のコードが参照する回数(定義の module の中の読み取り + 他の module の import)。"""
    module, _, name = ref.partition(":")
    uses = 0
    for other, path in _runtime_files():
        if other == LIST_MODULE:
            continue
        tree = _tree(path)
        if other == module:
            uses += _loads(tree, name)
        elif _imports(tree, module, name) and _loads(tree, name):
            uses += 1
    return uses


@pytest.mark.parametrize("ref", _listed_refs(), ids=_listed_refs())
def test_listed_prompt_is_used_by_runtime_code(ref: str) -> None:
    assert _runtime_uses(ref) > 0, (
        f"{ref} は実行のコードから使われていません。一覧から外してください。"
    )


def test_runtime_prompt_constants_are_listed_or_explained() -> None:
    defined = _defined_prompt_constants()
    listed = set(_listed_refs())
    missing = sorted(defined - listed - set(NOT_LISTED))
    assert missing == [], (
        f"実行のコードのプロンプトが画面の一覧(PROMPT_STAGES)にありません: {missing}"
    )
    # 載せない理由の一覧に、もう無い定数を残さない。
    assert sorted(set(NOT_LISTED) - defined) == []
    assert listed & set(NOT_LISTED) == set()


def test_stages_follow_processing_order_with_content() -> None:
    stages = readonly_prompt_stages()
    ids = [stage["id"] for stage in stages]
    assert len(ids) == len(set(ids))
    order = [PROMPT_PHASES.index(str(stage["phase"])) for stage in stages]
    assert order == sorted(order), "段は処理の順(PROMPT_PHASES)に並べる"
    for stage in stages:
        prompts = stage["prompts"]
        assert isinstance(prompts, list) and prompts
        for prompt in prompts:
            assert str(prompt["content"]).strip(), f"{stage['id']}.{prompt['id']} が空です"


def test_every_stage_phase_and_part_has_screen_copy() -> None:
    keys = set(
        re.findall(
            r'^\s*"(settings\.answerPrompts\.[^"]+)":', I18N.read_text(encoding="utf-8"), re.M
        )
    )
    expected = {f"settings.answerPrompts.phase.{phase}" for phase in PROMPT_PHASES}
    for stage in PROMPT_STAGES:
        expected.add(f"settings.answerPrompts.stage.{stage.id}")
        expected.add(f"settings.answerPrompts.stage.{stage.id}.condition")
        expected.update(f"settings.answerPrompts.part.{part_id}" for part_id, _ in stage.parts)
    assert sorted(expected - keys) == []
