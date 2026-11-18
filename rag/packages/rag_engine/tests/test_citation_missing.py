"""資料で答えた本文の引用の欠落を、形式の不合格として 1 回だけ再試行する（#5391）。

LLM は決定論の stub。adapter と同じく、形式の不合格を共通の枠（既定 1 回）でやり直し、最後の試行だけ検証の
context に最後の試行の印を付ける（``retrying_parse``）。資料・質問・応答はすべて合成。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

import pytest
from test_answer_flow_single_generation import _run_traced, context, record, retrying_parse

from rag_engine.generation import grounded
from rag_engine.generation.citation_unit_ids import SentUnitCatalog
from rag_engine.generation.full_answer_wire import CITATION_MISSING_MESSAGE, full_answer_model, lacks_citation

SOURCE = "合成装置Aの入力電圧は12 Vです。"
QUESTION = "合成装置Aの入力電圧は？"
BODY = "入力電圧は12 Vです。"


def _context() -> Any:
    return context(record(0, SOURCE, "合成仕様A", source="synthetic-spec-a.md"))


def _reply(kind: str = "answered", *, cited_from: int | None = None, mark: str = "[{unit}]") -> Any:
    """試行ごとの応答。``cited_from`` 回目（0 始まり）の試行から本文に印を付ける。"""
    attempts = 0

    def reply(units: dict[str, str]) -> dict[str, Any]:
        nonlocal attempts
        cited = cited_from is not None and attempts >= cited_from
        attempts += 1
        return {
            "answer_body": BODY + (mark.format(unit=units[SOURCE]) if cited else ""),
            "response_kind": kind,
            "confidence": "high",
        }

    return reply


def test_a_body_with_a_citation_is_accepted_without_another_call():
    response, calls, _prompts = _run_traced(QUESTION, _context(), _reply(cited_from=0))
    assert calls == ["FullAnswerWire"]
    assert response.confidence == "high"
    assert len(response.generation_trace["citation_lines"]) == 1
    assert "citation_missing" not in response.generation_trace


def test_a_missing_citation_is_retried_once_with_the_finding_and_the_retry_binds_the_source():
    response, calls, prompts = _run_traced(QUESTION, _context(), _reply(cited_from=1))
    assert calls == ["FullAnswerWire", "FullAnswerWire"]
    # 再試行の入力は、同じ入力の後に指摘の Step を 1 つ足したもの。番号は送る指示（項目の説明の Step を含む）の続き。
    assert CITATION_MISSING_MESSAGE not in prompts[0]
    assert prompts[1].startswith(prompts[0] + "\n\n## Step ")
    assert CITATION_MISSING_MESSAGE in prompts[1]
    system_steps = [int(number) for number in re.findall(r"(?m)^## Step (\d+):", grounded.generation_system_prompt())]
    assert re.search(r"## Step (\d+): 引用のラベルを書く", prompts[1]).group(1) == str(system_steps[-1] + 2)
    assert response.answer_text.startswith(BODY)
    assert len(response.generation_trace["citation_lines"]) == 1
    assert response.cited_evidence
    assert response.confidence == "high"
    assert "citation_missing" not in response.generation_trace


def test_a_citation_still_missing_after_the_retry_keeps_the_body():
    response, calls, _prompts = _run_traced(QUESTION, _context(), _reply())
    assert calls == ["FullAnswerWire", "FullAnswerWire"]
    assert response.answer_text == BODY
    assert response.envelope["outcome"] == "answered"
    assert response.confidence == "low"
    assert response.generation_trace["citation_missing"] is True
    assert "generation_failure" not in response.generation_trace
    # 取得しただけの資料を引用に昇格させない。
    assert not response.cited_evidence
    assert response.generation_trace["citation_lines"] == []


@pytest.mark.parametrize("kind", ["insufficient_evidence", "needs_clarification", "needs_environment_data", "needs_human"])
def test_kinds_that_do_not_answer_from_the_sources_are_not_retried(kind):
    response, calls, _prompts = _run_traced(QUESTION, _context(), _reply(kind))
    assert calls == ["FullAnswerWire"]
    assert "citation_missing" not in response.generation_trace
    assert BODY in response.answer_text


def test_a_conditional_answer_also_needs_a_citation():
    response, calls, _prompts = _run_traced(QUESTION, _context(), _reply("conditional", cited_from=1))
    assert calls == ["FullAnswerWire", "FullAnswerWire"]
    assert len(response.generation_trace["citation_lines"]) == 1


def test_an_unknown_unit_label_keeps_the_existing_failure_and_resend():
    """未知の単位のラベルは従来どおり同じ入力で送り直し、最後も不合格なら生成の失敗にする。"""
    response, calls, prompts = _run_traced(QUESTION, _context(), _reply(mark="[E1.9]", cited_from=0))
    assert calls == ["FullAnswerWire", "FullAnswerWire"]
    assert prompts[1] == prompts[0]
    assert response.generation_trace["generation_failure"]["stage"] == "answer.generate"
    assert "citation_missing" not in response.generation_trace


@pytest.mark.parametrize("mark", ["[ {unit} ]", "［{unit}］", "[​{unit}]"], ids=["space", "full-width", "zero-width-space"])
def test_padded_marks_count_as_citations(mark):
    response, calls, _prompts = _run_traced(QUESTION, _context(), _reply(mark=mark, cited_from=0))
    assert calls == ["FullAnswerWire"]
    assert len(response.generation_trace["citation_lines"]) == 1


def test_no_citation_is_required_when_no_evidence_was_sent():
    empty = SentUnitCatalog(())
    wire = full_answer_model(empty).model_validate({"answer_body": BODY, "response_kind": "answered", "confidence": "high"})
    assert wire.answer_body == BODY
    assert not lacks_citation(BODY, "answered", empty)


@pytest.mark.parametrize("cited_from", [1, None], ids=["recovered", "still-missing"])
def test_batched_reading_checks_only_the_final_generation(cited_from):
    """分割読取の組ごとの読取は対象外。最終生成だけを確かめ、読取は再試行しない。"""
    from test_batched_evidence import FILLER, PRINCIPLE, _record, body, config, finding

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation import answering
    from rag_engine.generation.batched_evidence import BatchReading

    calls: list[str] = []
    prompts: list[str] = []
    attempts = 0

    def final_reply(sent: str) -> dict[str, Any]:
        nonlocal attempts
        unit = re.search(r"(?m)^\d+\| \[(E[0-9]+\.[0-9]+)\] " + re.escape(PRINCIPLE), sent).group(1)
        cited = cited_from is not None and attempts >= cited_from
        attempts += 1
        return {"answer_body": "係長が承認します。" + (f"[{unit}]" if cited else ""), "response_kind": "answered", "confidence": "high"}

    def model(system, prompt, settings, schema, **kwargs):
        if issubclass(schema, BatchReading):
            calls.append(schema.__name__)
            values = json.loads(prompt)["evidence"]
            return schema(findings=[finding(value, answer=PRINCIPLE) for value in values if body(value).startswith(PRINCIPLE)])
        return retrying_parse(schema, prompt, final_reply, retries=settings.llm_max_retries, calls=calls, prompts=prompts)

    def unused(*args, **kwargs):
        pytest.fail("不要な呼出し")

    records = [_record(0, PRINCIPLE + FILLER[:2000], score=0.9), _record(1, "補足" + FILLER, score=0.5)]
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = answering.synthesize_grounded_answer(
            "合成の申請の承認者は？",
            answering.AnswerContext(records=records, text=""),
            replace(config(), evidence_budget_chars=9000),
            image_prompt_mode="text_only",
        ).response

    batch_reads = calls.count("BatchReading")
    assert batch_reads >= 2
    assert calls.count("FullAnswerWire") == 2
    assert result.generation_trace["generation_input_budget"]["batch_count"] == batch_reads
    assert result.answer_text.startswith("係長が承認します。")
    assert "generation_failure" not in result.generation_trace
    if cited_from is None:
        assert result.generation_trace["citation_missing"] is True
        assert result.confidence == "low"
    else:
        assert "citation_missing" not in result.generation_trace
        assert len(result.generation_trace["citation_lines"]) == 1
