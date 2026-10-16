"""rag_poc のプロンプトの編集と反映。

rag_poc はプロンプトをファイル(`prompts/<key>.txt`)へ保存したが、rag では DB
(`rag_answer_prompts`)に保存し、rag_engine の runtime の ``prompt_overrides`` で渡す。
編集できるのは rag_poc と同じ 2 つ(回答生成のテンプレートと画像検索のプロンプト)で、
ほかの段(取込から評価まで、実行で使う固定のプロンプトすべて)はコードで管理する読み取り専用。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path

from rag_engine.knowledge.prompt_files import (
    IMAGE_RETRIEVAL_PROMPT_KEY,
    PROMPT_FILES,
    VLM_ANSWER_PROMPT_KEY,
)

EDITABLE_PROMPT_KEYS = (VLM_ANSWER_PROMPT_KEY, IMAGE_RETRIEVAL_PROMPT_KEY)
ANSWER_PROMPT_MAX_CHARS = 50_000


def default_prompt(key: str) -> str:
    return PROMPT_FILES[key].default_content or ""


def required_placeholders(key: str) -> tuple[str, ...]:
    return PROMPT_FILES[key].required_placeholders


def validate_prompt(key: str, content: str) -> None:
    """rag_poc の save_prompt_file と同じ検証(空と必須 placeholder の欠落は保存しない)。"""
    if key not in EDITABLE_PROMPT_KEYS:
        raise KeyError(key)
    if not content.strip():
        raise ValueError("内容が空です。既定の内容を使う場合は「既定に戻す」を使ってください。")
    missing = [
        f"{{{{{name}}}}}" for name in required_placeholders(key) if f"{{{{{name}}}}}" not in content
    ]
    if missing:
        raise ValueError(f"必須の placeholder がありません: {', '.join(missing)}")


# 処理の順の段の区分(取込 → 質問の解釈 → 検索 → 根拠の判定 → 生成 → 監査 → 評価)。
PROMPT_PHASES = (
    "ingestion",
    "question",
    "retrieval",
    "grounding",
    "generation",
    "audit",
    "evaluation",
)


@dataclass(frozen=True)
class PromptStage:
    """コードで管理する 1 段のプロンプト。

    ``parts`` は (部分の id, ``module:定数名``)。定数は実行の経路が使うものそのものを指す
    (画面に出す内容と実行で渡す内容をずらさない)。使われる条件の文は画面の i18n
    (``settings.answerPrompts.stage.<id>.condition``)に置く。
    """

    id: str
    phase: str
    parts: tuple[tuple[str, str], ...]


# 実行で使う固定のプロンプトをすべて、処理の順に並べる(#2054)。足したプロンプトをここに
# 載せ忘れると tests/test_answer_prompt_stages.py が失敗する。編集できる 2 つ
# (EDITABLE_PROMPT_KEYS)は編集欄で示す。
PROMPT_STAGES: tuple[PromptStage, ...] = (
    PromptStage(
        "external_ocr",
        "ingestion",
        (("instruction", "app.clients.external_parser:DOTS_OCR_PROMPT"),),
    ),
    PromptStage(
        "picture_description",
        "ingestion",
        (
            ("system", "rag_engine.adapters.oci:VISION_SYSTEM_PROMPT"),
            ("picture", "rag_engine.parsing.vision_prompt_rules:PICTURE_EXTRACTION_INSTRUCTION"),
            ("table", "rag_engine.parsing.vision_prompt_rules:TABLE_EXTRACTION_INSTRUCTION"),
        ),
    ),
    PromptStage(
        "field_extraction",
        "ingestion",
        (("instruction", "app.rag.ingestion_prompts:FIELD_EXTRACTION_PROMPT"),),
    ),
    PromptStage(
        "navigation_summary",
        "ingestion",
        (("instruction", "app.rag.ingestion_prompts:NAVIGATION_SUMMARY_PROMPT"),),
    ),
    PromptStage(
        "raptor_summary",
        "ingestion",
        (
            ("system", "app.rag.ingestion_prompts:RAPTOR_SUMMARY_SYSTEM_PROMPT"),
            ("instruction", "app.rag.ingestion_prompts:RAPTOR_SUMMARY_PROMPT"),
        ),
    ),
    PromptStage(
        "history_rewrite",
        "question",
        (("system", "app.rag.pipeline:HISTORY_REWRITE_SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "field_filter",
        "question",
        (("system", "app.rag.field_filter_reader:FIELD_FILTER_SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "routing",
        "question",
        (
            ("system", "rag_engine.generation.answering:QUERY_ROUTING_SYSTEM_PROMPT"),
            ("template", "rag_engine.generation.query_prompts:QUERY_ROUTING_PROMPT_TEMPLATE"),
        ),
    ),
    PromptStage(
        "expansion",
        "question",
        (
            ("system", "rag_engine.generation.answering:QUERY_EXPANSION_SYSTEM_PROMPT"),
            ("template", "rag_engine.generation.query_prompts:QUERY_EXPANSION_PROMPT_TEMPLATE"),
        ),
    ),
    PromptStage(
        "screen_linking",
        "retrieval",
        (("system", "rag_engine.retrieval.screen_catalog:LINK_SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "crag",
        "grounding",
        (
            ("system", "rag_engine.generation.answering:CRAG_GRADER_SYSTEM_PROMPT"),
            ("template", "rag_engine.generation.crag_support:CRAG_GRADE_PROMPT_TEMPLATE"),
        ),
    ),
    PromptStage(
        "section_extraction",
        "grounding",
        (("system", "rag_engine.generation.section_extraction:EXTRACTION_SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "faq_answer",
        "generation",
        (("system", "rag_engine.generation.faq_answer:SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "generation",
        "generation",
        (("system", "rag_engine.generation.grounded:GENERATE_SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "audit",
        "audit",
        (
            ("system", "rag_engine.generation.grounded:AUDIT_SYSTEM_PROMPT"),
            ("compactRule", "rag_engine.generation.grounded:AUDIT_COMPACT_REASON_RULE"),
        ),
    ),
    PromptStage(
        "application",
        "audit",
        (
            ("system", "rag_engine.generation.applications:APPLICATION_SYSTEM_PROMPT"),
            ("auditSystem", "rag_engine.generation.applications:APPLICATION_AUDIT_SYSTEM_PROMPT"),
        ),
    ),
    PromptStage(
        "evaluation",
        "evaluation",
        (
            ("scope", "rag_engine.evaluation.answer_eval:SCOPE_PROMPT"),
            ("system", "rag_engine.evaluation.answer_eval:SYSTEM_PROMPT"),
        ),
    ),
    PromptStage(
        "answer_validation",
        "evaluation",
        (("system", "rag_engine.evaluation.answer_validation:VALIDATION_SYSTEM_PROMPT"),),
    ),
)


def prompt_constant(ref: str) -> str:
    """``module:定数名`` の定数の内容。"""
    module_name, _, name = ref.partition(":")
    return str(getattr(import_module(module_name), name))


def readonly_prompt_stages() -> list[dict[str, object]]:
    """実行で使う各段のプロンプト(コードで管理し、画面では読み取り専用で示す)。"""
    return [
        {
            "id": stage.id,
            "phase": stage.phase,
            "prompts": [
                {"id": part_id, "content": prompt_constant(ref)} for part_id, ref in stage.parts
            ],
        }
        for stage in PROMPT_STAGES
    ]


@contextmanager
def prompt_overrides(overrides: Mapping[str, str]) -> Iterator[None]:
    """rag_engine の read_prompt が overrides を返すように、同期処理の間だけ runtime を有効にする。

    profile は今の current_profile()(runtime なしの既定)に上書きだけを足し、ほかの挙動を変えない。
    保存先(paths.prompts)を持たないので、read_prompt はファイルを読まず上書きか既定値を返す。
    """
    if not overrides:
        yield
        return
    from rag_engine.resources.runtime import ResourcePaths, Runtime, current_profile

    profile = current_profile()
    merged = {**dict(profile.prompt_overrides), **dict(overrides)}
    with tempfile.TemporaryDirectory(prefix="rag-engine-prompts-") as work:
        runtime = Runtime(
            ResourcePaths(workspace=Path(work), output=Path(work)),
            replace(profile, prompt_overrides=tuple(merged.items())),
        )
        with runtime.activate():
            yield
