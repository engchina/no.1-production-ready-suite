"""回答の生成は 1 回で、生成後に採点しない（#4714・#4718）。

LLM は決定論の stub。生成のほかの呼び出しが来たら失敗する。
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from test_grounded_answer import context, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem

ROW = "項目Aの保存年数は5年とする。"
OTHER = "委託先は年1回の点検を行う。"


def _units(prompt: str) -> dict[str, str]:
    return {text: uid for uid, text in re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\] (.*)$", prompt)}


def _ids(question: str) -> list[str]:
    return list(grounded.generation_request_ids(question))


def _settings() -> Any:
    return get_settings(environ={}, dotenv_path=None)


def _unused(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("不要な外部呼出")


def _run(question: str, ctx: Any, parts: Any, **options: Any) -> tuple[Any, list[str]]:
    calls: list[str] = []

    def model(_system: str, prompt: str, _settings: Any, schema: Any, **_kwargs: Any) -> Any:
        calls.append(schema.__name__)
        if not getattr(schema, "_uses_unit_ids", False):
            raise AssertionError(f"生成のほかの呼び出し: {schema.__name__}")
        return schema.model_validate(parts(_units(prompt)))

    with bind_dependencies(AnswerDependencies(_unused, _unused, model, _unused, _unused)):
        response = synthesize_grounded_answer(question, ctx, _settings(), image_prompt_mode="text_only", **options).response
    return response, calls


def _source(request_id: str, requirement: str, unit: str) -> dict[str, Any]:
    return {"request_id": request_id, "requirement": requirement, "answer": {"kind": "source", "basis": {"unit_ids": [unit]}}}


def _answer(request_id: str, requirement: str, unit: str, reasoning: str, result: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "requirement": requirement,
        "answer": {"kind": "answer", "basis": {"unit_ids": [unit]}, "reasoning": reasoning, "result": result},
    }


def _gap(request_id: str, requirement: str, text: str) -> dict[str, Any]:
    return {"request_id": request_id, "requirement": requirement, "answer": {"kind": "gap", "text": text}}


def test_generates_once_even_when_a_calculation_answer_only_quotes_the_source() -> None:
    question = "項目Aの保存年数を計算してください。"
    response, calls = _run(
        question,
        context(record(0, ROW, "合成の規程", source="synthetic.md")),
        lambda units: {
            "response_kind": "answered",
            "question_type": ["calculation"],
            "answer_parts": [_source(_ids(question)[0], "保存年数", units[ROW])],
            "summary_part_indexes": [0],
            "confidence": "high",
        },
    )
    assert calls == ["GroundedDraftWire"]
    assert response.generation_trace["llm_calls"] == 1
    assert ROW in response.answer_text


def test_broken_output_is_not_repaired_by_another_generation() -> None:
    calls: list[str] = []

    class Sample(BaseModel):
        value: int

    def model(_system: str, _prompt: str, _settings: Any, schema: Any, **_kwargs: Any) -> Any:
        calls.append(schema.__name__)
        try:
            Sample.model_validate({"value": "not-a-number"})
        except ValidationError as exc:
            raise exc
        raise AssertionError

    with bind_dependencies(AnswerDependencies(_unused, _unused, model, _unused, _unused)):
        response = synthesize_grounded_answer(
            "項目Aの保存年数は？", context(record(0, ROW, "合成の規程", source="synthetic.md")), _settings(), image_prompt_mode="text_only"
        ).response
    assert calls == ["GroundedDraftWire"]
    assert response.generation_trace["generation_failure"]["error"] == "ValidationError"
    assert ROW in response.answer_text


def test_no_candidates_returns_the_fixed_text_without_calling_the_model() -> None:
    response, calls = _run("項目Aの保存年数は？", context(), lambda _units: pytest.fail("生成を呼ばない"))
    assert calls == []
    assert response.answer_text.startswith(answer_labels().insufficient_evidence_summary)
    assert response.envelope["outcome"] == "insufficient_evidence"
    assert response.generation_trace["llm_calls"] == 0


def test_a_rejected_part_gets_no_line_added_by_the_program() -> None:
    question = "項目Aの保存年数を教えてください。"
    response, calls = _run(
        question,
        context(record(0, ROW, "合成の規程", source="synthetic.md")),
        lambda units: {
            "response_kind": "answered",
            "answer_parts": [_answer(_ids(question)[0], "保存年数", units[ROW], "E12 を読む。", "保存年数は5年です。")],
            "summary_part_indexes": [],
            "confidence": "high",
        },
    )
    assert calls == ["GroundedDraftWire"]
    assert answer_labels().gaps_section not in response.answer_text
    assert response.generation_trace["rounds"][0]["timing"]["answer_parts"]["parts"][0]["status"] == "missing"
    assert response.envelope["outcome"] == "answered"
    assert "保存年数は5年です。" not in response.answer_text


@pytest.mark.parametrize("selection", [[], None, [39]])
def test_single_question_keeps_a_conclusion_when_summary_selection_is_empty(selection: Any) -> None:
    question = "項目Aの保存年数を教えてください。"
    result = "保存年数は5年です。"

    def output(units: dict[str, str]) -> dict[str, Any]:
        value: dict[str, Any] = {
            "response_kind": "answered",
            "answer_parts": [_answer(_ids(question)[0], "保存年数", units[ROW], "原文の年数を読む。", result)],
            "confidence": "high",
        }
        if selection is not None:
            value["summary_part_indexes"] = selection
        return value

    response, calls = _run(
        question,
        context(record(0, ROW, "合成の資料", source="synthetic.md")),
        output,
    )
    assert calls == ["GroundedDraftWire"]
    assert response.generation_trace["llm_calls"] == 1
    assert response.answer_text.startswith(result)
    assert response.envelope["outcome"] == "answered"


def test_summary_fallback_prefers_a_conclusion_over_source_and_table() -> None:
    question = "項目Aの保存年数を教えてください。"
    request_id = _ids(question)[0]
    result = "保存年数は5年です。"
    response, calls = _run(
        question,
        context(record(0, ROW, "合成の資料", source="synthetic.md")),
        lambda units: {
            "response_kind": "answered",
            "answer_parts": [
                _source(request_id, "原文", units[ROW]),
                {
                    "request_id": request_id,
                    "requirement": "表",
                    "answer": {
                        "kind": "table",
                        "title": "合成の表",
                        "columns": ["項目", "値"],
                        "rows": [{"cells": ["項目A", "5年"], "basis": {"unit_ids": [units[ROW]]}}],
                    },
                },
                _answer(request_id, "保存年数", units[ROW], "原文の年数を読む。", result),
            ],
            "summary_part_indexes": [],
            "confidence": "high",
        },
    )
    assert calls == ["GroundedDraftWire"]
    assert response.answer_text.startswith(result)
    assert "合成の表" in response.answer_text


def test_heading_only_quote_is_not_promoted_to_a_fallback_conclusion() -> None:
    question = "項目Aの保存年数を教えてください。"
    heading = "# 合成の見出し"
    response, calls = _run(
        question,
        context(record(0, heading + "\n" + ROW, "合成の資料", source="synthetic.md")),
        lambda units: {
            "response_kind": "answered",
            "answer_parts": [_source(_ids(question)[0], "要点", units[heading])],
            "summary_part_indexes": [],
            "confidence": "low",
        },
    )
    assert calls == ["GroundedDraftWire"]
    assert not response.generation_trace["rounds"][0]["draft"]["summary"]


def test_table_only_answer_keeps_its_title_without_a_second_summary() -> None:
    question = "項目Aの保存年数を表で示してください。"
    title = "合成の表"
    response, calls = _run(
        question,
        context(record(0, ROW, "合成の資料", source="synthetic.md")),
        lambda units: {
            "response_kind": "answered",
            "answer_parts": [
                {
                    "request_id": _ids(question)[0],
                    "requirement": "表",
                    "answer": {
                        "kind": "table",
                        "title": title,
                        "columns": ["項目", "値"],
                        "rows": [{"cells": ["項目A", "5年"], "basis": {"unit_ids": [units[ROW]]}}],
                    },
                }
            ],
            "summary_part_indexes": [],
            "confidence": "high",
        },
    )
    assert calls == ["GroundedDraftWire"]
    assert response.answer_text.startswith(title)
    assert not response.generation_trace["rounds"][0]["draft"]["summary"]


def test_summary_fallback_preserves_a_legitimate_gap_for_another_part() -> None:
    question = "項目Aの保存年数と例外条件を教えてください。"
    gap = "例外の条件は資料から確認できません。"
    response, calls = _run(
        question,
        context(record(0, ROW, "合成の資料", source="synthetic.md")),
        lambda units: {
            "response_kind": "conditional",
            "answer_parts": [_source(_ids(question)[0], "保存年数", units[ROW]), _gap(_ids(question)[0], "例外条件", gap)],
            "summary_part_indexes": [],
            "confidence": "high",
        },
    )
    assert calls == ["GroundedDraftWire"]
    assert response.answer_text.startswith(ROW)
    assert gap in response.answer_text
    assert response.envelope["outcome"] == "conditional"


def test_only_the_gap_written_by_the_model_is_shown_as_unconfirmed() -> None:
    question = "項目Aの保存年数を教えてください。"
    text = "合成の例外の条件は資料にありません。"
    response, _calls = _run(
        question,
        context(record(0, ROW, "合成の規程", source="synthetic.md")),
        lambda _units: {
            "response_kind": "insufficient_evidence",
            "answer_parts": [_gap(_ids(question)[0], "保存年数", text)],
            "summary_part_indexes": [],
            "confidence": "low",
        },
    )
    assert text in response.answer_text
    assert response.envelope["outcome"] != "answered"


def test_a_citation_that_does_not_exist_disappears_without_a_mark() -> None:
    draft = GroundedDraft(
        request_coverage=[],
        items=[
            GroundedItem(
                kind="rule", request_id="Q1", text="存在しない根拠の説明", citations=[GroundedCitation(evidence_id="E99", lines="1")]
            )
        ],
        summary="",
        confidence="low",
    )
    span = {
        "evidence_id": "id-1",
        "label": "E1",
        "source_id": "p1",
        "source": "synthetic.md",
        "text": ROW,
        "function": ("synthetic.md", "合成"),
        "tags": [],
    }
    checked, dropped = grounded.verify("項目Aは？", draft, [span])
    assert checked == []
    assert dropped and dropped[0]["evidence_id"] == "E99"


def test_two_parts_use_the_first_generation_text_without_another_explanation_call() -> None:
    question = "権限の削除期限と委託先の義務を教えてください。"
    request_id = _ids(question)[0]
    reasoning = "規程の期限の条を読む。"
    response, calls = _run(
        question,
        context(record(0, f"{ROW}\n{OTHER}", "合成の規程", source="synthetic.md")),
        lambda units: {
            "response_kind": "answered",
            "answer_parts": [
                _answer(request_id, "期限", units[ROW], reasoning, "保存年数は5年です。"),
                _source(request_id, "委託先の義務", units[OTHER]),
            ],
            "summary_part_indexes": [0],
            "confidence": "high",
        },
    )
    assert calls == ["GroundedDraftWire"]
    assert reasoning in response.answer_text
    assert response.answer_text.startswith("保存年数は5年です。")


def test_retrieval_counts_separate_found_and_passed() -> None:
    question = "項目Aの保存年数は？"
    ctx = context(record(0, ROW, "合成の規程", source="synthetic.md"))
    response, _calls = _run(
        question,
        ctx,
        lambda units: {
            "response_kind": "answered",
            "answer_parts": [_source(_ids(question)[0], "保存年数", units[ROW])],
            "summary_part_indexes": [0],
            "confidence": "high",
        },
    )
    counts = response.generation_trace["retrieval_counts"]
    assert counts["found"] == 1 and counts["passed_to_model"] >= 1
    assert counts["candidate_count"] == 1
    assert {"top_score", "threshold"} <= set(counts)


def test_retrieval_counts_keep_the_candidates_before_the_threshold() -> None:
    question = "項目Aの保存年数は？"
    ctx = replace(context(record(0, ROW, "合成の規程", source="synthetic.md")), candidate_count=12)
    response, _calls = _run(
        question,
        ctx,
        lambda units: {
            "response_kind": "answered",
            "answer_parts": [_source(_ids(question)[0], "保存年数", units[ROW])],
            "summary_part_indexes": [0],
            "confidence": "high",
        },
    )
    counts = response.generation_trace["retrieval_counts"]
    assert (counts["candidate_count"], counts["found"]) == (12, 1)


def test_guide_steps_stay_as_instructions_and_coverage_is_not_judged() -> None:
    question = "項目Aの手続きを教えてください。"
    knowledge = RuntimeKnowledgeContext(
        original_question=question, expanded_question=question, path=Path("/nonexistent"), guide_steps=(("s1", "手順A", ("r0",)),)
    )
    seen: list[str] = []

    def model(_system: str, prompt: str, _settings: Any, schema: Any, **_kwargs: Any) -> Any:
        seen.append(prompt)
        return schema.model_validate(
            {
                "response_kind": "answered",
                "answer_parts": [_source(_ids(question)[0], "手続き", _units(prompt)[ROW])],
                "summary_part_indexes": [0],
                "confidence": "high",
            }
        )

    with bind_dependencies(AnswerDependencies(_unused, _unused, model, _unused, _unused)):
        response = synthesize_grounded_answer(
            question,
            context(record(0, ROW, "合成の規程", source="synthetic.md")),
            _settings(),
            image_prompt_mode="text_only",
            runtime_knowledge=knowledge,
        ).response
    assert len(seen) == 1
    assert "### 業務ガイドの扱い" in seen[0]
    assert "guide_step_coverage" not in response.generation_trace
    assert not hasattr(grounded, "guide_step_coverage")


def test_audit_settings_are_gone() -> None:
    assert not hasattr(_settings(), "answer_audit_mode")
    assert not hasattr(_settings(), "answer_max_correction_rounds")
    assert replace(_settings()) is not None
