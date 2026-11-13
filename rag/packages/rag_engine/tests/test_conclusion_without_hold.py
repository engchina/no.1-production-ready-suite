"""根拠に表があっても、モデルの結論を保留せず、確認済みの回答を未回答にしない（#4716）。

LLM は決定論の stub で、生成と説明の段のほかの呼び出しは受けない。
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from test_grounded_answer import context, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answering import synthesize_grounded_answer

QUESTION = "項目Aの保存年数を教えてください。"
RESULT = "項目Aの保存年数は5年です。"
ROW = "| 項目A | 通常 | 5年 |"
TABLE = "\n".join(["| 区分 | 条件 | 保存年数 |", "| --- | --- | --- |", ROW, "| 項目B | 特例 | 10年 |"])


def _answer() -> tuple[Any, list[str]]:
    calls: list[str] = []

    def model(_system: str, prompt: str, _settings: Any, schema: Any, **_options: Any) -> Any:
        calls.append(schema.__name__)
        if not getattr(schema, "_uses_unit_ids", False):
            raise AssertionError(f"生成と説明のほかの呼び出し: {schema.__name__}")
        units = {text: uid for uid, text in re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\] (.*)$", prompt)}
        return schema.model_validate(
            {"answer_body": RESULT + "[C1]", "response_kind": "answered", "confidence": "high", "citations": [{"unit_ids": [units[ROW]]}]}
        )

    def unused(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("不要な外部呼出")

    settings = replace(get_settings(environ={}, dotenv_path=None))
    ctx = context(record(0, TABLE, "合成の保存期間", source="synthetic-table.md"))
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        response = synthesize_grounded_answer(QUESTION, ctx, settings, image_prompt_mode="text_only").response
    return response, calls


def test_conclusion_and_summary_stay_when_the_evidence_has_a_table() -> None:
    response, _calls = _answer()
    assert response.answer_text.startswith(RESULT)
    assert answer_labels().neutral_summary not in response.answer_text
    assert response.cited_evidence


def test_answered_request_is_not_listed_as_unconfirmed() -> None:
    response, _calls = _answer()
    assert answer_labels().gaps_section not in response.answer_text
    assert response.envelope["outcome"] == "answered"
    assert response.insufficient_reason in (None, "")
