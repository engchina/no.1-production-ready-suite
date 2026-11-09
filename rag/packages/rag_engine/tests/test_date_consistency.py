"""説明・要約に書いた年を質問と引いた根拠に照らす（#4042）。入力はすべて合成。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from application_verdict_stub import audit_reply, is_application_schema
from audit_fixtures import synthetic_explanation
from citation_unit_support import generated_fixture

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import applications, grounded
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from audit_fixtures import is_explanation_schema
from rag_engine.models.llm import GroundedApplication, GroundedCitation, GroundedItem

RULE = "受付の日を含め14暦日なので4月14日まで有効で、4月15日に停止する。"
QUESTION = "対象Xを2031年4月1日に受け付けました。いつまで有効ですか？"
WRONG = "受付の日を含め14暦日なので、2021年4月14日まで有効です。"
RIGHT = "受付の日を含め14暦日なので、2031年4月14日まで有効です。"


def record(text: str = RULE, *, source: str = "synthetic.txt") -> AnswerRecord:
    return AnswerRecord(
        id="c1",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source=source,
        chunk_level="parent",
        chunk_id="c1",
        source_run_id="run",
        metadata={"section_path": ["第1節"]},
    )


def draft(text: str, summary: str = "") -> dict[str, Any]:
    return {
        "request_coverage": [{"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "対象X", "relation": "direct"}]}],
        "items": [{"kind": "rule", "request_id": "Q1", "text": text, "citations": [{"evidence_id": "E1", "line_start": 1, "line_end": 1}]}],
        "summary": summary or text,
        "summary_item_indexes": [0],
        "confidence": "medium",
    }


def answer(*outputs: dict[str, Any]) -> tuple[Any, list[str]]:
    """監査なしの回答を、用意した出力で通す。LLM の呼び出しの入力を返す。"""
    prompts: list[str] = []
    values = iter(outputs)

    def parse(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        if is_application_schema(schema):
            return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt)
        if is_explanation_schema(schema):
            return synthetic_explanation(schema, prompt)
        prompts.append(prompt)
        return generated_fixture(schema, prompt, next(values))

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("追加の外部呼び出しは不可")

    context = AnswerContext(records=[record()], text=RULE)
    settings = replace(get_settings(environ={}, dotenv_path=None))
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        return synthesize_grounded_answer(QUESTION, context, settings, image_prompt_mode="text_only").response, prompts


def test_a_correct_year_adds_no_call_and_no_mark() -> None:
    result, prompts = answer(draft(RIGHT))
    assert len(prompts) == 1
    assert RIGHT in result.answer_text
    assert not result.envelope.get("passage_marks")


def test_summary_selection_cannot_add_a_year_absent_from_the_existing_result() -> None:
    result, prompts = answer(draft(RIGHT, summary=WRONG))
    assert len(prompts) == 1
    assert WRONG not in result.answer_text and RIGHT in result.answer_text
    assert result.answer_text.startswith(RIGHT)

