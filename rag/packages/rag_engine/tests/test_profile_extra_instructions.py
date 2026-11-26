"""検索・回答プロファイルの追加の指示（質問に足す文・回答の指示に足す文。#5848）。

質問に足す文は、用語・同義語の照合の後に質問の末尾へ足し、その後の検索と回答の生成の質問にする。記録に残す質問は
変えない。回答の指示に足す文は、回答の生成の system の役割の文の後・固定の部分の前にだけ足す。資料・質問・足す文は
すべて合成で、外部のサービスには接続しない。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from budget_settings import with_evidence_budget
from grounded_stub import echo_model
from test_answer_generation import _chunk_run_stub, _hybrid_result, _rerank_ready_settings, _stored_child

from rag_engine.adapters.oci import RerankTextRank
from rag_engine.generation import answering, grounded
from rag_engine.generation.answer_models import (
    QUESTION_METADATA_SEPARATORS,
    extract_original_question,
    with_question_suffix,
)
from rag_engine.generation.response_language import render_language_placeholder
from rag_engine.resources.runtime import bind_prompt_overrides
from rag_engine.retrieval.guide_steps import GuidePlan, GuideStep, step_queries

QUESTION = "合成用語Aの合成の期限は？"
# 足す文の中の語（合成用語B）は用語に登録してあるが、照合は利用者の質問だけで行う。
SUFFIX = "合成用語Bの章の番号を書いてください。"
INSTRUCTION = "箇条書きで答えてください。"
_TERMS = [
    {"term": "合成用語A", "aliases": ["合成別名A"]},
    {"term": "合成用語B", "aliases": ["合成別名B"]},
]


def _run(tmp_path: Path, *, request_question: str | None = None, **changes: Any) -> tuple[Any, Any, Any, list[tuple[str, str]]]:
    """回答の流れを通し、(結果, 検索の呼び出し, rerank の呼び出し, 生成の (system, 入力)) を返す。"""
    path = tmp_path / "rk.json"
    path.write_text(json.dumps({"schema_version": 1, "terms": _TERMS, "rules": []}, ensure_ascii=False), encoding="utf-8")
    settings = replace(
        _rerank_ready_settings(),
        runtime_knowledge_path=path,
        runtime_terms_search_enabled=True,
        runtime_terms_answer_enabled=True,
        **changes,
    )
    generated: list[tuple[str, str]] = []

    def model(system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        generated.append((system, prompt))
        return echo_model(system, prompt, call_settings, schema, **options)

    search_result = _hybrid_result(_stored_child("chunk-docling-c000001", 1, "合成の本文"))
    with (
        patch("rag_engine.generation.answering.load_latest_or_source_chunk_run", return_value=_chunk_run_stub()),
        patch("rag_engine.generation.answering.check_adb_hybrid_search_ready"),
        patch("rag_engine.generation.answering.search_adb_hybrid_chunks", return_value=search_result) as search,
        patch("rag_engine.generation.answering.rerank_text_with_scores", return_value=[RerankTextRank(0, 0.9)]) as rerank,
        patch("rag_engine.generation.answering.parse_text_response", side_effect=model),
    ):
        result = answering.answer_question_result(
            QUESTION, "abcdef", ["docling"], settings, chunk_top_k=20, request_question=request_question
        )
    return result, search, rerank, generated


def _question_step(prompt: str) -> str:
    """生成の入力の問い合わせの Step の本文。"""
    return prompt.split("## Step 1: 問い合わせ\n", 1)[1].split("\n\n## Step 2:", 1)[0]


def test_helper_adds_one_newline_and_keeps_question_when_empty() -> None:
    assert with_question_suffix("合成の質問", "合成の足す文") == "合成の質問\n合成の足す文"
    assert with_question_suffix("合成の質問", "") == "合成の質問"


def test_suffix_is_added_after_term_matching_and_reaches_search_rerank_and_generation(tmp_path: Path) -> None:
    result, search, rerank, generated = _run(tmp_path, question_suffix=SUFFIX)

    # 用語・同義語の照合は利用者の質問だけ。足す文の中の登録語には別名を足さない。
    (call,) = search.call_args_list
    (vector_query,) = call.kwargs["retrieval_queries"]
    text_query = call.kwargs["text_queries"][vector_query]
    assert vector_query.endswith(SUFFIX) and "合成別名A" not in vector_query
    assert "合成別名A" in text_query and text_query.endswith(SUFFIX)
    assert "合成別名B" not in text_query
    # 全文検索の文は「用語を足した後の文 + 足す文」（足した語は足す文より前）。
    assert text_query.index("合成別名A") < text_query.index(SUFFIX)
    # rerank の質問と、回答の生成の質問・質問契約にも届く。
    assert rerank.call_args.args[0] == with_question_suffix(QUESTION, SUFFIX)
    system, prompt = generated[-1]
    assert _question_step(prompt).endswith("\n" + SUFFIX)
    assert result.task_contract["request_units"][0]["text"] == with_question_suffix(QUESTION, SUFFIX)
    assert "Glossary:\n- 合成用語A (aliases: 合成別名A)" in prompt and "合成用語B (aliases" not in prompt
    # 記録に残す質問は利用者の質問のまま。
    assert result.original_question == QUESTION
    assert SUFFIX not in result.question_plan.original_question
    # 処理の経過: 用語・同義語の照合の後・検索の前に 1 回。
    names = [step["name"] for step in result.execution_steps]
    assert names.count(answering.QUESTION_SUFFIX_STEP) == 1
    position = names.index(answering.QUESTION_SUFFIX_STEP)
    assert names.index(answering.TERM_SEARCH_STEP) < position < names.index("文書検索")
    step = result.execution_steps[position]
    assert {field["label"]: field["value"] for field in step["details"]["fields"]}["足した文"] == SUFFIX


def test_suffix_is_added_to_the_search_question_and_the_request_question(tmp_path: Path) -> None:
    """呼び出し元が検索の文を言い換えたとき（履歴による補完・MCP）も、検索の文と元の質問の両方に足す。"""
    original = "合成の元の質問は？"
    result, search, _rerank, generated = _run(tmp_path, question_suffix=SUFFIX, request_question=original)

    (vector_query,) = search.call_args.kwargs["retrieval_queries"]
    assert "合成用語A" in vector_query and vector_query.endswith(SUFFIX)
    _system, prompt = generated[-1]
    assert _question_step(prompt) == render_question(original) + "\n" + SUFFIX
    assert result.task_contract["original_question"] == with_question_suffix(original, SUFFIX)
    assert result.original_question == QUESTION


def render_question(question: str) -> str:
    """生成の入力に入る質問の形（全角の疑問符などの字幅をそろえる）。"""
    import unicodedata

    return unicodedata.normalize("NFKC", question)


def test_without_suffix_the_flow_is_unchanged(tmp_path: Path) -> None:
    result, search, rerank, generated = _run(tmp_path)

    (vector_query,) = search.call_args.kwargs["retrieval_queries"]
    assert SUFFIX not in vector_query and SUFFIX not in generated[-1][1]
    assert rerank.call_args.args[0] == QUESTION
    assert answering.QUESTION_SUFFIX_STEP not in [step["name"] for step in result.execution_steps]
    assert generated[-1][0] == grounded.generation_system_prompt()


def test_guide_step_search_uses_the_question_with_the_suffix() -> None:
    """業務ガイドの手順ごとの検索の主の検索文は「質問 + 手順の手がかり」。質問に足す文もここに入る。"""
    step = GuideStep(step_id="s1", title="合成の手順", retrieval_hints=("合成の手がかり",))
    text, queries = step_queries(with_question_suffix(QUESTION, SUFFIX), step)
    assert SUFFIX in text and "合成の手がかり" in text
    # 手がかりだけの検索文には質問も足す文も入らない。
    assert any(SUFFIX not in query for query in queries)


def test_guide_step_search_receives_the_suffixed_question(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_guide_search(question: str, *args: Any, **kwargs: Any) -> Any:
        seen.append(question)
        return [], [], {"skipped": "disabled"}

    monkeypatch.setattr(answering, "_search_guide_steps", fake_guide_search)
    plan = GuidePlan(guide_id="g1", revision=1, steps=(GuideStep(step_id="s1", title="合成の手順"),))
    settings = replace(_rerank_ready_settings(), question_suffix=SUFFIX)
    search_result = _hybrid_result(_stored_child("chunk-docling-c000001", 1, "合成の本文"))
    with (
        patch("rag_engine.generation.answering.load_latest_or_source_chunk_run", return_value=_chunk_run_stub()),
        patch("rag_engine.generation.answering.check_adb_hybrid_search_ready"),
        patch("rag_engine.generation.answering.search_adb_hybrid_chunks", return_value=search_result),
        patch("rag_engine.generation.answering.rerank_text_with_scores", return_value=[RerankTextRank(0, 0.9)]),
        patch("rag_engine.generation.answering.parse_text_response", side_effect=echo_model),
    ):
        answering.answer_question_result(QUESTION, "abcdef", ["docling"], settings, chunk_top_k=20, guide_plan=plan)
    assert seen == [with_question_suffix(QUESTION, SUFFIX)]


@pytest.mark.parametrize("separator", QUESTION_METADATA_SEPARATORS)
def test_separator_list_is_what_the_question_extraction_cuts_at(separator: str) -> None:
    """保存の検証（backend）が使う区切りの一覧は、元の質問の切り出しが実際に切る文字列。"""
    assert extract_original_question(f"合成の質問\n{separator}\n合成の後ろ") == "合成の質問"


def _steps(system: str) -> list[tuple[int, str]]:
    return [(int(number), title) for number, title in re.findall(r"^## Step (\d+): (.+)$", system, re.M)]


def test_answer_instruction_goes_after_the_role_and_before_the_fixed_part() -> None:
    default = grounded.generation_system_prompt()
    added = grounded.generation_system_prompt(answer_instruction=f"  {INSTRUCTION}\n")

    role_steps = _steps(grounded.GENERATION_ROLE_PROMPT)
    fixed_titles = [title for _number, title in _steps(grounded.GENERATION_FIXED_PROMPT)]
    steps = _steps(added)
    # 並びは 役割の文 → 追加の指示 → 固定の部分。番号は続きで振る。
    assert [number for number, _title in steps] == list(range(1, len(steps) + 1))
    assert steps[: len(role_steps)] == role_steps
    assert steps[len(role_steps)][1] == "追加の指示に従う"
    assert [title for _number, title in steps[len(role_steps) + 1 :]] == fixed_titles
    # 役割の文と固定の部分の文は変わらない（固定の部分は Step の番号だけが続きになる）。
    role, rest = added.split("\n## Step 5: 追加の指示に従う\n", 1)
    block, fixed = rest.split("\n## Step 6:", 1)
    default_role, default_fixed = default.split("\n## Step 5:", 1)
    assert role == default_role
    assert block.rstrip().endswith(INSTRUCTION) and block.count(INSTRUCTION) == 1
    unnumbered = re.compile(r"^## Step \d+:", re.M)
    assert unnumbered.sub("## Step N:", fixed) == unnumbered.sub("## Step N:", default_fixed)
    # 空・空白だけなら足さない。
    assert grounded.generation_system_prompt(answer_instruction="") == default
    assert grounded.generation_system_prompt(answer_instruction=" \n ") == default


def test_answer_instruction_follows_a_saved_role_and_precedes_the_assumption_step() -> None:
    with bind_prompt_overrides({"generation.role": "## Step 1: 保存済みの指示"}):
        system = grounded.generation_system_prompt(assume_unconfirmed=True, answer_instruction=INSTRUCTION)
    titles = [title for _number, title in _steps(system)]
    fixed_titles = [title for _number, title in _steps(grounded.GENERATION_FIXED_PROMPT)]
    assert titles[:2] == ["保存済みの指示", "追加の指示に従う"]
    assert titles[2 : 2 + len(fixed_titles)] == fixed_titles
    assert "未確定の前提" in titles[-1]
    assert [number for number, _title in _steps(system)] == list(range(1, len(titles) + 1))


def test_step_headings_inside_the_instruction_are_renumbered_with_the_rest() -> None:
    system = grounded.generation_system_prompt(answer_instruction="## Step 1: 合成の見出し\n合成の本文。")
    numbers = [number for number, _title in _steps(system)]
    assert numbers == list(range(1, len(numbers) + 1))
    assert "合成の見出し" in [title for _number, title in _steps(system)]


def test_answer_instruction_reaches_only_the_generation_system(tmp_path: Path) -> None:
    """質問の側（検索の文・生成の入力）には入らず、生成の system にだけ入る。"""
    _result, search, rerank, generated = _run(tmp_path, answer_instruction=INSTRUCTION)

    system, prompt = generated[-1]
    assert system == grounded.generation_system_prompt(answer_instruction=INSTRUCTION)
    assert INSTRUCTION not in prompt
    assert INSTRUCTION not in str(search.call_args.kwargs["retrieval_queries"])
    assert INSTRUCTION not in rerank.call_args.args[0]


def test_batched_reading_gets_the_instruction_only_in_the_final_generation() -> None:
    """分割読取の組ごとの読み取りには渡さず、最後の回答の生成には渡す。"""
    from test_answer_flow_single_generation import retrying_parse
    from test_batched_evidence import FILLER, PRINCIPLE, _record, body, config, finding

    from rag_engine.dependencies import AnswerDependencies, bind_dependencies
    from rag_engine.generation.batched_evidence import BatchReading

    reading_systems: list[str] = []
    generation_systems: list[str] = []

    def final_reply(sent: str) -> dict[str, Any]:
        unit = re.search(r"(?m)^\d+\| \[(E[0-9]+\.[0-9]+)\] " + re.escape(PRINCIPLE), sent).group(1)
        return {"answer_body": f"係長が承認します。[{unit}]", "response_kind": "answered", "confidence": "high"}

    def model(system: str, prompt: str, settings: Any, schema: Any, **kwargs: Any) -> Any:
        if issubclass(schema, BatchReading):
            reading_systems.append(system)
            values = json.loads(prompt)["evidence"]
            return schema(findings=[finding(value, answer=PRINCIPLE) for value in values if body(value).startswith(PRINCIPLE)])
        generation_systems.append(system)
        return retrying_parse(schema, prompt, final_reply, retries=settings.llm_max_retries, calls=[], prompts=[])

    def unused(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("不要な呼出し")

    records = [_record(0, PRINCIPLE + FILLER[:2000], score=0.9), _record(1, "補足" + FILLER, score=0.5)]
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        response = answering.synthesize_grounded_answer(
            "合成の申請の承認者は？",
            answering.AnswerContext(records=records, text=""),
            with_evidence_budget(config(), 9000, answer_instruction=INSTRUCTION),
            image_prompt_mode="text_only",
        ).response

    assert response.generation_trace["generation_input_budget"]["batch_count"] == len(reading_systems) >= 2
    assert all(INSTRUCTION not in system for system in reading_systems)
    assert generation_systems == [grounded.generation_system_prompt(answer_instruction=INSTRUCTION)]
