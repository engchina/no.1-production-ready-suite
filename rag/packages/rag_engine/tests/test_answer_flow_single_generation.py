"""回答の生成は 1 回で、生成後に採点しない（#4714・#4718）。

LLM は決定論の stub。生成のほかの呼び出しが来たら失敗する。
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_grounded_answer import context, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext

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


@pytest.mark.parametrize(
    "body",
    [
        "保存年数は5年です。",
        "保存年数は5年です。追加の項目は資料にありません。",
        "## 回答\n保存年数は5年です。\n\n## 補足\n資料の適用条件を確認してください。",
        "資料に記載がなく、回答できません。",
    ],
)
def test_keeps_the_complete_body_without_another_generation(body):
    response, calls = _run(
        "保存年数は？",
        context(record(0, ROW, "合成の規則", source="synthetic.md")),
        lambda _units: {"answer_body": body, "response_kind": "answered", "confidence": "high", "citations": []},
    )
    assert calls == ["FullAnswerWire"]
    assert response.answer_text == body
    assert response.envelope["outcome"] == "answered"
    assert response.generation_trace["llm_calls"] == 1


def test_no_candidates_returns_the_fixed_text_without_calling_the_model():
    response, calls = _run("保存年数は？", context(), lambda _units: pytest.fail("生成を呼ばない"))
    assert calls == []
    assert response.answer_text.startswith(answer_labels().insufficient_evidence_summary)
    assert response.envelope["outcome"] == "insufficient_evidence"
    assert response.generation_trace["llm_calls"] == 0


def test_same_reference_can_be_used_twice_without_losing_the_body():
    response, calls = _run(
        "保存年数は？",
        context(record(0, ROW, "合成の規則", source="synthetic.md")),
        lambda units: {
            "answer_body": "保存年数は5年です。[C1]\n同じ規則を確認できます。[C1]",
            "response_kind": "answered",
            "confidence": "high",
            "citations": [{"unit_ids": [units[ROW]]}],
        },
    )
    assert calls == ["FullAnswerWire"]
    assert "保存年数は5年です。" in response.answer_text
    assert "同じ規則を確認できます。" in response.answer_text
    assert len(response.generation_trace["citation_lines"]) == 2


def test_retrieval_counts_separate_found_and_passed() -> None:
    question = "項目Aの保存年数は？"
    ctx = context(record(0, ROW, "合成の規程", source="synthetic.md"))
    response, _calls = _run(
        question,
        ctx,
        lambda units: {
            "response_kind": "answered",
            "answer_body": ROW + "[C1]",
            "citations": [{"unit_ids": [units[ROW]]}],
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
            "answer_body": ROW + "[C1]",
            "citations": [{"unit_ids": [units[ROW]]}],
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
                "answer_body": ROW + "[C1]",
                "citations": [{"unit_ids": [_units(prompt)[ROW]]}],
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
