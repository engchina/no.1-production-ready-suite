"""回答の生成の記録を、除く一覧に無いキーはそのまま診断へ写す（#4735）。"""

import json
from typing import Any

from rag_engine.generation.answer_models import AnswerQuestionResult

from app.rag.answer_engine import (
    _TRACE_KEYS_EXCLUDED_FROM_DIAGNOSTICS,
    _outcome_from_result,
    _SearchState,
)


def _result(trace: dict[str, Any]) -> AnswerQuestionResult:
    return AnswerQuestionResult(
        answer="合成の回答",
        question_display="合成の質問",
        original_question="合成の質問",
        selected_strategy="simple",
        effective_strategy="simple",
        generation_trace=trace,
    )


def _diagnostics(trace: dict[str, Any]) -> dict[str, Any]:
    return _outcome_from_result(_result(trace), _SearchState()).diagnostics


def test_retrieval_counts_reach_the_diagnostics() -> None:
    counts = {
        "candidate_count": 12,
        "found": 5,
        "passed_to_model": 5,
        "top_score": 0.9,
        "threshold": 0.4,
    }
    assert _diagnostics({"retrieval_counts": counts})["retrieval_counts"] == counts


def test_key_not_in_the_exclusion_list_reaches_the_diagnostics() -> None:
    # 記録に新しいキーが足されたら、ここに書き足さなくても応答と実行の記録へ届く。
    assert _diagnostics({"synthetic_new_key": {"value": 1}})["synthetic_new_key"] == {"value": 1}


def test_excluded_keys_do_not_reach_the_diagnostics() -> None:
    trace = {key: "合成の本文" for key in _TRACE_KEYS_EXCLUDED_FROM_DIAGNOSTICS}
    diagnostics = _diagnostics(trace)
    assert not (set(diagnostics) & _TRACE_KEYS_EXCLUDED_FROM_DIAGNOSTICS)


def test_every_body_or_prompt_key_is_excluded() -> None:
    assert {
        "rounds",
        "selected_evidence",
        "provided_evidence_scope",
        "citation_lines",
        "applications",
        "task_contract",
        "query_overflow",
        "document_background",
        "unread_sections",
        "guide_steps",
        "zero_evidence",
    } <= _TRACE_KEYS_EXCLUDED_FROM_DIAGNOSTICS


def test_explicit_fields_still_win_over_the_trace() -> None:
    # 章節の拡張は評価器の判定の全文（grades）を除いた形だけを出す。
    diagnostics = _diagnostics({"section_expansion": {"applied": True, "grades": ["合成の判定"]}})
    assert diagnostics["section_expansion"] == {"applied": True}


def test_generation_citation_traces_are_copied_from_the_excluded_rounds() -> None:
    citation_trace = {"input": {"prompt_sha256": "synthetic"}}
    trace = {"rounds": [{"draft": {"text": "合成"}, "generation_citation_trace": citation_trace}]}
    diagnostics = _diagnostics(trace)
    assert "rounds" not in diagnostics
    assert diagnostics["generation_citation_traces"] == [citation_trace]


def test_new_keys_do_not_grow_the_stored_record_much() -> None:
    # diagnostics_json は本文・プロンプトの写しで肥大させない（新たに写るのは数値と短い状態だけ）。
    large = "合成の本文" * 4000
    trace = {key: large for key in _TRACE_KEYS_EXCLUDED_FROM_DIAGNOSTICS}
    base = len(json.dumps(_diagnostics(trace), ensure_ascii=False))
    trace.update(
        pipeline="grounded",
        llm_calls=1,
        final_fact_count=3,
        escalation_signals=["structured_output_invalid"],
        same_unit_fill={"applied": False, "reason": "no_new_compatible_evidence"},
        retrieval_counts={
            "candidate_count": 12,
            "found": 5,
            "passed_to_model": 5,
            "top_score": 0.9,
            "threshold": 0.4,
        },
        extra_retrieval_budget={"max_queries": 8, "used": {"crag": 1}, "remaining": 7},
        crag_partial_projection={"applied": False, "reason": "answerable_part_unverified"},
    )
    assert len(json.dumps(_diagnostics(trace), ensure_ascii=False)) - base < 1024
