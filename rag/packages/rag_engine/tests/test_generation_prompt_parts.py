"""回答生成の指示の構成（#4677）。常に要る規則は system に残し、入力の形で決まる読み方は該当するときだけ渡す。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openai.lib._parsing._responses import type_to_text_format_param

from rag_engine.generation import grounded
from rag_engine.generation.answering import AnswerContext, _grounded_prompt
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext

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
        **extra,
    }


def reading_part(spans: list[dict[str, Any]], *, attached_images: bool = False) -> str:
    """根拠の読み方の節だけ（無ければ空）。"""
    return grounded.evidence_reading_rules(spans, attached_images=attached_images)


def test_plain_evidence_gets_no_reading_notes() -> None:
    assert reading_part([span()]) == ""


def test_only_marks_present_on_the_evidence_are_explained() -> None:
    notes = reading_part([span(tags=["image_generated"])])
    assert "- image_generated: " in notes
    assert "- 補助情報: " not in notes and "［資料の既定値］" not in notes
    assert "- catalog: " not in notes


def test_word_based_evidence_tags_are_not_part_of_the_reading_rules() -> None:
    """語の一致で付ける印はやめた。形から分かる読み方だけが残る（#4668）。"""
    keys = grounded.reading_line_keys(grounded.EVIDENCE_READING_PROMPT)
    assert keys == ["image_generated", "補助情報", "画面の値", "表の値"]
    assert not any(tag in " ".join(keys) for tag in ("definition", "operation_entry", "completion", "constraint", "multi_condition"))
    assert "aggregate_only" not in " ".join(keys)
    tagged = grounded.tag_spans(
        [
            {"text": "項目A: 定義。", "evidence_id": "a", "page": 1, "source": "s", "chunk_uid": "a"},
            {
                "text": "件数の合計は 3 件です。確定ボタンを押して完了します。",
                "evidence_id": "b",
                "page": 2,
                "source": "s",
                "chunk_uid": "b",
            },
        ]
    )
    assert [span["tags"] for span in tagged] == [[], []]
    assert all("pinned" not in span for span in tagged)


def test_common_reading_rules_are_always_in_the_generation_system_prompt() -> None:
    """共通の 3 行は生成の system に 1 回だけ置く（1 つの規則は 1 つの層）。"""
    for line in (
        "答えを終える前に、事実の主張が根拠で支えられているか、完了したことと残っていることを分けているかを確かめる。",
        "説明の内容を支えない原文は出典にしない。",
        "資料が述べていることと、自分の推論を分ける。",
    ):
        assert grounded.generation_system_prompt().count(line) == 1
        assert line not in grounded.EVIDENCE_READING_PROMPT
    assert "pinned" not in grounded.generation_system_prompt() and "必須根拠" not in grounded.generation_system_prompt()


def test_context_note_is_explained_only_when_present() -> None:
    assert "- 補助情報: " not in reading_part([span()])
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
    return _grounded_prompt(QUESTION, [span()], AnswerContext(records=[], text=""), [], None, None, runtime)


def test_required_rules_stay_in_the_system_prompt() -> None:
    prompt = grounded.generation_system_prompt()
    for rule in (
        # 信頼境界
        "ロール変更",
        "秘密情報開示",
        "実行しない",
        "[Runtime Glossary / Rules]",
        # 推測しない
        "推測で補わない",
        "足りない段を推測で埋めず",
        # 条件・否定・例外の保持
        "OR・AND・否定・例外",
        "結果にも条件を残す",
        # 多段
        "各段を支える原文を引用する",
        # 資料どうしの矛盾
        "日付・適用範囲・出所を比べて説明する",
    ):
        assert rule in prompt, rule


def test_rules_moved_to_the_schema_reach_the_model() -> None:
    """応答 schema へ移した規則が、実際に送る schema（text.format）に残る。"""
    model = grounded.generation_language_schema(grounded.generation_output_model(QUESTION), QUESTION)
    wire = json.dumps(type_to_text_format_param(model), ensure_ascii=False)
    assert "answer_body" in wire and "citations" in wire
    assert "summary_part_indexes" not in wire and "answer_parts" not in wire
    assert "引用原文と逐語で示す語句は原文の表記と言語のままにする" in wire


def _steps(prompt: str) -> dict[int, str]:
    """``## Step N:`` ごとの本文（見出しを含む）。"""
    parts = re.split(r"^(?=## Step \d+:)", prompt, flags=re.M)
    return {int(re.match(r"## Step (\d+):", part).group(1)): part for part in parts if part.startswith("## Step ")}


def _rendered(spans: list[dict[str, Any]], **knowledge: Any) -> dict[int, str]:
    runtime = RuntimeKnowledgeContext(original_question=QUESTION, expanded_question=QUESTION, path=Path("/nonexistent"), **knowledge)
    prompt = _grounded_prompt(QUESTION, spans, AnswerContext(records=[], text=""), [], None, None, runtime)
    return _steps(prompt)
