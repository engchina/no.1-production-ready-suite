"""rag_poc のプロンプトの編集と反映。

rag_poc はプロンプトをファイル(`prompts/<key>.txt`)へ保存したが、rag では DB
(`rag_answer_prompts`)に保存し、rag_engine の runtime の ``prompt_overrides`` で渡す。
回答生成・文書解析・文書分割の編集可能な指示は、それぞれの設定画面で管理する。
生成は役割と回答の書き方だけを編集でき、信頼の境界・出力の形・引用の決まりと、質問・検索の
内部の段の文はコードで管理する（画面には読み取り専用で出す。#4782）。
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from rag_engine.knowledge.prompt_files import IMAGE_RETRIEVAL_PROMPT_KEY, PROMPT_FILES
from rag_engine.parsing.vision_prompt_rules import split_image_retrieval_prompt

INGESTION_PROMPT_REFS = {
    "external_ocr": "app.clients.external_parser:DOTS_OCR_PROMPT",
    "heading_levels": "app.rag.ingestion_prompts:HEADING_LEVEL_PROMPT",
    "field_extraction": "app.rag.ingestion_prompts:FIELD_EXTRACTION_PROMPT",
    "navigation_summary": "app.rag.ingestion_prompts:NAVIGATION_SUMMARY_PROMPT",
}
EDITABLE_PROMPT_KEYS = (IMAGE_RETRIEVAL_PROMPT_KEY, *INGESTION_PROMPT_REFS)
ANSWER_PROMPT_MAX_CHARS = 50_000


def default_prompt(key: str) -> str:
    if key in INGESTION_PROMPT_REFS:
        return prompt_constant(INGESTION_PROMPT_REFS[key])
    if key in STAGE_PROMPT_REFS:
        return prompt_constant(STAGE_PROMPT_REFS[key])
    return PROMPT_FILES[key].default_content or ""


# 欠けても LLM への入力が落ちない差し込み（回答の既定の言語）。
# 保存した指示に無ければ差し込まれないだけなので、必須にしない。
_OPTIONAL_PLACEHOLDERS = frozenset({"language"})


def required_placeholders(key: str) -> tuple[str, ...]:
    if key in STAGE_PROMPT_REFS:
        names = re.findall(r"{{([A-Za-z_][A-Za-z_0-9]*)}}", default_prompt(key))
        return tuple(dict.fromkeys(name for name in names if name not in _OPTIONAL_PLACEHOLDERS))
    return () if key in INGESTION_PROMPT_REFS else PROMPT_FILES[key].required_placeholders


def validate_prompt(key: str, content: str) -> None:
    """rag_poc の save_prompt_file と同じ検証(空と必須 placeholder の欠落は保存しない)。"""
    if key not in EDITABLE_PROMPT_KEYS:
        raise KeyError(key)
    if not content.strip():
        raise ValueError("内容が空です。既定の内容を使う場合は「既定に戻す」を使ってください。")
    missing = [
        f"{{{{{name}}}}}" for name in required_placeholders(key) if f"{{{{{name}}}}}" not in content
    ]
    missing.extend(token for token in required_tokens(key) if token not in content)
    if missing:
        raise ValueError(f"必須の placeholder がありません: {', '.join(missing)}")


# 設定の概要の順を土台に、独立した知識の準備と、検索・回答の実行順を区別する。
PROMPT_PHASES = (
    "ingestion",
    "knowledge",
    "question",
    "retrieval",
    "generation",
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
# 載せ忘れると tests/test_answer_prompt_stages.py が失敗する。編集できる指示
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
            ("rules", "rag_engine.parsing.vision_prompt_rules:VISION_EXTRACTION_INSTRUCTION"),
            ("picture", "rag_engine.parsing.vision_prompt_rules:PICTURE_EXTRACTION_INSTRUCTION"),
            ("table", "rag_engine.parsing.vision_prompt_rules:TABLE_EXTRACTION_INSTRUCTION"),
            (
                "contextImage",
                "rag_engine.parsing.vision_prompt_rules:VISION_CONTEXT_IMAGE_INSTRUCTION",
            ),
            (
                "contextRecords",
                "rag_engine.parsing.vision_prompt_rules:VISION_CONTEXT_RECORDS_INSTRUCTION",
            ),
        ),
    ),
    PromptStage(
        "heading_levels",
        "ingestion",
        (("instruction", "app.rag.ingestion_prompts:HEADING_LEVEL_PROMPT"),),
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
    # 回答の過程の表から業務ガイドの下書きを作る(#2397。検索・回答プロファイルの知識の作成)。
    PromptStage(
        "support_guide_draft",
        "knowledge",
        (
            ("system", "app.rag.support_guide_process_import:GUIDE_DRAFT_SYSTEM_PROMPT"),
            ("instruction", "app.rag.support_guide_process_import:GUIDE_DRAFT_PROMPT"),
        ),
    ),
    PromptStage(
        "support_guide_conditions",
        "question",
        (
            ("system", "app.rag.support_guide_conditions:CONDITION_SYSTEM_PROMPT"),
            # 確認に自由入力で答えた選択式の条件の選択肢を決める（#2726）。
            ("answerChoice", "app.rag.support_guide_conditions:ANSWER_SYSTEM_PROMPT"),
        ),
    ),
    PromptStage(
        "history_rewrite",
        "question",
        (("system", "app.rag.history_question:HISTORY_REWRITE_SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "screen_linking",
        "retrieval",
        (("system", "rag_engine.retrieval.screen_catalog:LINK_SYSTEM_PROMPT"),),
    ),
    # 検索の要らない発話（挨拶・雑談）への根拠なしの短い応答（#4787）。
    PromptStage(
        "small_talk",
        "generation",
        (("system", "app.rag.small_talk:SMALL_TALK_SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "faq_answer",
        "generation",
        (("system", "rag_engine.generation.faq_answer:SYSTEM_PROMPT"),),
    ),
    PromptStage(
        "generation",
        "generation",
        (
            ("batchReading", "rag_engine.generation.batched_evidence:BATCH_READING_PROMPT"),
            # system = 編集できる役割の文 + 必ず足す固定の部分（+ 未確定の前提）（#4782）。
            ("role", "rag_engine.generation.grounded:GENERATION_ROLE_PROMPT"),
            ("fixed", "rag_engine.generation.grounded:GENERATION_FIXED_PROMPT"),
            ("assumptionSystem", "rag_engine.generation.grounded:ASSUMPTION_SYSTEM_PROMPT"),
            # user message の固定のテンプレート。
            ("template", "rag_engine.knowledge.prompt_files:ANSWER_INPUT_TEMPLATE"),
            # 該当する入力のときだけ生成の入力へ足す読み方（タグ・値の種類 / 業務ガイド。#4677）。
            ("evidenceReading", "rag_engine.generation.grounded:EVIDENCE_READING_PROMPT"),
            ("guideRules", "rag_engine.generation.grounded:GUIDE_RULES_PROMPT"),
        ),
    ),
    PromptStage(
        "evaluation",
        "evaluation",
        (("correctness", "rag_engine.evaluation.answer_eval:CORRECTNESS_PROMPT"),),
    ),
)


# 段の部分のうち編集できるもの（#4782）。生成は役割と回答の書き方、FAQ は回答の指示だけ。
# 信頼の境界・出力の形・引用の決まりと、質問・検索の内部の段の文は編集させない。
_EDITABLE_STAGE_PARTS = frozenset({"generation.role", "faq_answer.system"})

# 実行の段に対応する、編集できる自然言語の指示。既存の解析設定の key は維持する。
STAGE_PROMPT_REFS = {
    f"{stage.id}.{part_id}": ref
    for stage in PROMPT_STAGES
    for part_id, ref in stage.parts
    if stage.id == "picture_description"
    or stage.phase == "evaluation"
    or f"{stage.id}.{part_id}" in _EDITABLE_STAGE_PARTS
}
EDITABLE_PROMPT_KEYS = (*EDITABLE_PROMPT_KEYS, *STAGE_PROMPT_REFS)


def required_tokens(key: str) -> tuple[str, ...]:
    """入力テンプレートの単一括弧の差し込み口を保持する。"""
    if key not in STAGE_PROMPT_REFS:
        return ()
    return tuple(
        dict.fromkeys(re.findall(r"(?<!{){[A-Za-z_][A-Za-z_0-9]*}(?!})", default_prompt(key)))
    )


def stage_prompt_permission(key: str) -> str | None:
    """指示の配置先の設定権限。条件の認識は回答プロンプトに置く。"""
    for stage in PROMPT_STAGES:
        if key not in {f"{stage.id}.{part}" for part, _ref in stage.parts}:
            continue
        if stage.id == "picture_description":
            return "menu.settings_parser_adapters"
        if stage.phase == "evaluation":
            return "menu.settings_evaluation"
        return "menu.settings_prompts"
    return None


def prompt_constant(ref: str) -> str:
    """``module:定数名`` の定数の内容。"""
    module_name, _, name = ref.partition(":")
    return str(getattr(import_module(module_name), name))


def separate_vision_prompt_rows(
    saved: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """旧一体型の保存内容を、読取時だけ独立した編集欄へ投影する。"""
    result = {key: dict(row) for key, row in saved.items()}
    row = result.get(IMAGE_RETRIEVAL_PROMPT_KEY)
    if row is not None:
        body, legacy = split_image_retrieval_prompt(str(row["content"]))
        row["content"] = body
        if legacy is not None and "picture_description.rules" not in result:
            result["picture_description.rules"] = {**row, "content": legacy}
    return result


def readonly_prompt_stages(
    *, image_prompt: str | None = None, overrides: Mapping[str, str] | None = None
) -> list[dict[str, object]]:
    """処理順の固定指示と、図の読み取りで実際に使うテンプレートを示す。"""
    stages: list[dict[str, object]] = []
    saved = dict(overrides or {})
    if image_prompt is not None:
        _body, legacy = split_image_retrieval_prompt(image_prompt)
        if legacy is not None:
            saved.setdefault("picture_description.rules", legacy)
    by_ref = {ref: saved[key] for key, ref in INGESTION_PROMPT_REFS.items() if key in saved}
    for stage in PROMPT_STAGES:
        parts = [
            {
                "id": part_id,
                "content": saved.get(
                    f"{stage.id}.{part_id}", by_ref.get(ref, prompt_constant(ref))
                ),
                "prompt_key": f"{stage.id}.{part_id}"
                if f"{stage.id}.{part_id}" in STAGE_PROMPT_REFS
                else None,
            }
            for part_id, ref in stage.parts
        ]
        if stage.id == "picture_description":
            # 解析と同じ正規化を使い、保存済みのテンプレートに足す共通規則も含める。
            parts.insert(
                1,
                {
                    "id": "template",
                    "content": split_image_retrieval_prompt(
                        image_prompt
                        if image_prompt is not None
                        else default_prompt(IMAGE_RETRIEVAL_PROMPT_KEY)
                    )[0],
                },
            )
            from rag_engine.adapters.oci import _picture_output_contract

            parts.append(
                {"id": "contract", "content": _picture_output_contract(), "prompt_key": None}
            )
        stages.append({"id": stage.id, "phase": stage.phase, "prompts": parts})
    return stages


@contextmanager
def prompt_overrides(overrides: Mapping[str, str]) -> Iterator[None]:
    """rag_engine の read_prompt が overrides を返すように、同期処理の間だけ runtime を有効にする。

    現在の指示の上書きを引き継ぎ、このスコープで指定した内容を優先する。
    保存先(paths.prompts)を持たないので、read_prompt はファイルを読まず上書きか既定値を返す。
    """
    if not overrides:
        yield
        return
    from rag_engine.resources.runtime import ResourcePaths, Runtime, current_runtime

    current = current_runtime()
    merged = {**dict(current.prompt_overrides if current else ()), **dict(overrides)}
    with tempfile.TemporaryDirectory(prefix="rag-engine-prompts-") as work:
        runtime = Runtime(
            ResourcePaths(workspace=Path(work), output=Path(work)),
            prompt_overrides=tuple(merged.items()),
        )
        with runtime.activate():
            yield
