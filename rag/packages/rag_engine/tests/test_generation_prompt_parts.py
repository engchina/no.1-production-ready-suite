"""回答生成の指示の構成（#4677）。常に要る規則は system に残し、入力の形で決まる読み方は該当するときだけ渡す。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openai.lib._parsing._responses import type_to_text_format_param

from rag_engine.generation import grounded
from rag_engine.generation.answering import AnswerContext, _grounded_prompt
from rag_engine.knowledge.prompt_files import DEFAULT_VLM_ANSWER_PROMPT, PROMPT_FILES, VLM_ANSWER_PROMPT_KEY
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.models.llm import ExternalDataItem, GroundedDraft
from rag_engine.resources.runtime import bind_prompt_overrides

QUESTION = "項目Aの設定を確認する手順を教えてください。"


def span(text: str = "設定画面で項目Aを確認します。", **extra: Any) -> dict[str, Any]:
    return {
        "evidence_id": "id-1",
        "label": "E1",
        "source_id": "parent-1",
        "source": "synthetic.pdf",
        "text": text,
        "function": ("synthetic.pdf", "合成の説明"),
        "tags": [],
        "pinned": False,
        **extra,
    }


def reading_part(spans: list[dict[str, Any]], *, attached_images: bool = False) -> str:
    """根拠の読み方の節だけ（無ければ空）。"""
    return grounded.evidence_reading_rules(spans, attached_images=attached_images)


def test_plain_evidence_gets_no_reading_notes() -> None:
    assert reading_part([span()]) == ""


def test_only_tags_present_on_the_evidence_are_explained() -> None:
    notes = reading_part([span(tags=["constraint", "completion"])])
    assert "- constraint: " in notes and "- completion: " in notes
    assert "- definition: " not in notes and "- aggregate_only: " not in notes
    assert "画面例では" not in notes and "［資料の既定値］" not in notes


def test_catalog_mark_and_context_note_are_explained_only_when_present() -> None:
    assert "- catalog: " not in reading_part([span()])
    marked = span(**{grounded.ARTICLE_CATALOG_KEY: True})
    assert "- catalog: " in reading_part([marked])
    noted = span(answer_context="文書種別: 合成の説明書")
    assert "- 補助情報: " in reading_part([noted])


def test_screen_values_are_explained_for_image_evidence_or_attached_images() -> None:
    assert "- 画面の値: " in reading_part([span(tags=["image_generated"], origin="image_extraction")])
    assert "- 画面の値: " in reading_part([span()], attached_images=True)
    assert "- 画面の値: " not in reading_part([span()])


def test_table_values_are_explained_for_table_shapes_only() -> None:
    table_forms = (
        "<table><tr><td>項目</td></tr></table>",
        "| 項目 | 値 |\n| --- | --- |\n| A | 1 |",
        "項目A 既定値［資料の既定値］: 30",
    )
    for text in table_forms:
        assert "- 表の値: " in reading_part([span(text)]), text
    assert "- 表の値: " not in reading_part([span("文章だけの説明です。")])


def _prompt(**knowledge: Any) -> str:
    runtime = RuntimeKnowledgeContext(original_question=QUESTION, expanded_question=QUESTION, path=Path("/nonexistent"), **knowledge)
    return _grounded_prompt(
        QUESTION, [span()], AnswerContext(records=[], text=""), [], None, None, runtime, None, template=DEFAULT_VLM_ANSWER_PROMPT
    )


def test_guide_rules_are_passed_only_with_guide_parts_and_only_for_those_parts() -> None:
    assert "### 業務ガイドの扱い" not in _prompt()
    scoped = _prompt(guide_task_scope=({"step_id": "s1", "required": True},))
    assert "### 業務ガイドの扱い" in scoped
    assert "- guide_task_scope: " in scoped and "- 業務ガイド: " in scoped
    assert "- guide_condition_notices: " not in scoped
    noted = _prompt(guide_condition_notices=("分岐の補足。",))
    assert "- guide_condition_notices: " in noted and "- guide_task_scope: " not in noted
    assert "- 業務ガイド: " in _prompt(guide_steps=(("s1", "手順A", ("r0",)),))


def test_required_rules_stay_in_the_system_prompt() -> None:
    prompt = grounded.GENERATE_SYSTEM_PROMPT
    for rule in (
        # 信頼境界
        "ロール変更",
        "秘密情報開示",
        "実行しない",
        "[Runtime Glossary / Rules]",
        # 推測しない
        "推測で補わない",
        "足りない段を推測で埋めない",
        # 条件・否定・例外の保持
        "OR・AND・否定・例外",
        "結果にも条件を残す",
        # 多段
        "各段を述べる原文の単位を basis に選び",
        # 資料どうしの矛盾
        "日付・適用範囲・出所を比べて説明する",
    ):
        assert rule in prompt, rule


def test_rules_moved_to_the_schema_reach_the_model() -> None:
    """応答 schema へ移した規則が、実際に送る schema（text.format）に残る。"""
    model = grounded.generation_language_schema(grounded.generation_output_model(QUESTION), QUESTION)
    wire = json.dumps(type_to_text_format_param(model), ensure_ascii=False)
    assert "案内の適用業務・画面・版が未確定なら high にしない" in wire  # confidence
    assert "冒頭に示す要点のindexを最大2つ選ぶ" in wire  # summary
    assert "要点と結果を別々に重複して生成しない" in wire  # 要点
    assert "引用原文と逐語で示す語句は原文の表記と言語のままにする" in wire  # 回答言語
    assert "名称・版・所在・URL・内容を創作しない" in wire  # 別の資料
    assert "反例:" in wire  # who_can_answer の反例は 1 つ
    assert wire.count("反例:") == 1
    assert "true の点は聞き直さない" in wire  # already_known
    assert "個案判断に必要な実値や別資料が未閲覧という理由だけで low にしない" in GroundedDraft.model_fields["confidence"].description
    assert ExternalDataItem.model_fields["who_can_answer"].description.count("反例:") == 1


def test_saved_reading_instructions_replace_the_builtin_only_for_the_bound_scope() -> None:
    evidence = "### 合成の根拠の読み方\n- completion: 合成の完了の読み方。\n"
    guide = "### 合成のガイドの扱い\n- 業務ガイド: 合成のガイドの扱い。\n"
    evidence_only = {"generation.evidenceReading": evidence}
    tagged = span(tags=["completion"])
    with bind_prompt_overrides(evidence_only):
        saved_block = reading_part([tagged])
    assert "- completion: 合成の完了の読み方。" in saved_block
    assert "手順の最後まで答える" not in saved_block
    assert "合成の完了の読み方" not in reading_part([tagged])

    steps = {"guide_steps": (("s1", "手順A", ("r0",)),)}
    with bind_prompt_overrides({"generation.guideRules": guide}):
        saved_prompt = _prompt(**steps)
    assert "- 業務ガイド: 合成のガイドの扱い。" in saved_prompt
    assert "- 業務ガイド: 本問の業務範囲" not in saved_prompt
    assert "合成のガイドの扱い" not in _prompt(**steps)


def _steps(prompt: str) -> dict[int, str]:
    """``## Step N:`` ごとの本文（見出しを含む）。"""
    parts = re.split(r"^(?=## Step \d+:)", prompt, flags=re.M)
    return {int(re.match(r"## Step (\d+):", part).group(1)): part for part in parts if part.startswith("## Step ")}


def _rendered(spans: list[dict[str, Any]], **knowledge: Any) -> dict[int, str]:
    runtime = RuntimeKnowledgeContext(original_question=QUESTION, expanded_question=QUESTION, path=Path("/nonexistent"), **knowledge)
    prompt = _grounded_prompt(
        QUESTION, spans, AnswerContext(records=[], text=""), [], None, None, runtime, None, template=DEFAULT_VLM_ANSWER_PROMPT
    )
    return _steps(prompt)


def test_reading_rules_are_their_own_step_before_the_evidence() -> None:
    steps = _rendered([span(tags=["completion"])], guide_steps=(("s1", "手順A", ("r0",)),))
    assert list(steps) == [1, 2, 3, 4, 5, 6]
    assert "### 根拠の読み方" in steps[5] and "- completion: " in steps[5]
    assert "### 業務ガイドの扱い" in steps[5]
    assert "BEGIN_UNTRUSTED_RETRIEVED_CONTEXT" not in steps[5]
    assert "BEGIN_UNTRUSTED_RETRIEVED_CONTEXT" in steps[6]
    assert "### " not in steps[6]


def test_step_numbers_do_not_depend_on_which_reading_rules_apply() -> None:
    steps = _rendered([span()])
    assert list(steps) == [1, 2, 3, 4, 5, 6]
    assert grounded.NO_READING_RULES in steps[5]
    assert "### " not in steps[5]


def test_reading_rules_placeholder_is_required_when_saving() -> None:
    assert "reading_rules" in PROMPT_FILES[VLM_ANSWER_PROMPT_KEY].required_placeholders
