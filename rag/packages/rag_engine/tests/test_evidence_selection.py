"""同じ原文を、モデルで読まずに関連度・重複・入力予算で選ぶ。"""

import json
from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation.evidence_selection import select_evidence
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded


def span(index, text, start=0):
    return {"evidence_id": f"E{index}", "source_id": f"s{index}", "text": text, "start": start, "end": start + len(text)}


def select(spans, *, chars=1000, tokens=10000, **kwargs):
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, max_output_tokens=100) for key, value in base.llm_providers.items()}
    settings = replace(base, llm_providers=providers, evidence_budget_chars=chars, answer_max_input_tokens=tokens)
    return select_evidence(
        spans,
        settings,
        render=lambda values: json.dumps(values, ensure_ascii=False),
        evidence_text=lambda values: "".join(value["text"] for value in values),
        tag=list,
        system="合成の指示",
        schema_for=lambda _values: {"type": "object"},
        **kwargs,
    )


def test_rank_and_exact_duplicate_selection_without_model():
    low = span(0, "低い順位の原文。")
    high = span(1, "高い順位の原文。")
    selected, _, diagnostic = select([low, high, dict(high)], source_scores={"s0": 0.2, "s1": 0.9})
    assert selected == [high, low]
    assert diagnostic["batch_count"] == 0
    assert diagnostic["duplicate_span_count"] == 1
    assert diagnostic["excluded_span_count"] == 0


def test_fitting_many_sources_builds_the_complete_input_once():
    inputs = [span(i, f"合成の本文{i}。") for i in range(150)]
    rendered_counts = []
    settings = get_settings(environ={}, dotenv_path=None)

    def render(values):
        rendered_counts.append(len(values))
        return json.dumps(values, ensure_ascii=False)

    selected, _, diagnostic = select_evidence(
        [*inputs, inputs[0]],
        settings,
        render=render,
        evidence_text=lambda values: "".join(value["text"] for value in values),
        tag=list,
        system="合成の指示",
        schema_for=lambda _values: {"type": "object"},
    )
    assert selected == inputs
    assert rendered_counts == [0, 150]
    assert diagnostic["duplicate_span_count"] == 1


def test_long_input_retains_source_positions_and_reports_partial_input():
    original = span(0, "合成の原文です。" * 100, start=120)
    selected, _, diagnostic = select([original], chars=100)
    piece = selected[0]
    assert 0 < len(piece["text"]) < len(original["text"])
    assert piece["text"].endswith("。")
    assert piece["text"] == original["text"][: len(piece["text"])]
    assert piece["start"] == 120
    assert piece["end"] == 120 + len(piece["text"])
    assert piece["source_id"] == original["source_id"]
    assert piece["evidence_id"] != original["evidence_id"]
    assert diagnostic["excluded_span_count"] == diagnostic["partial_span_count"] == 1
    assert diagnostic["fits"] is True


def test_lower_ranked_large_span_does_not_remove_already_selected_evidence():
    high = span(1, "重要な原文。")
    selected, _, diagnostic = select([span(0, "x" * 1000), high], chars=30, source_scores={"s0": 0.1, "s1": 0.9})
    assert selected == [high]
    assert diagnostic["excluded_span_count"] == 1


def test_model_token_budget_is_checked_alongside_character_budget():
    _, _, diagnostic = select([span(0, "合成の本文。" * 100)], chars=10000, tokens=350)
    assert diagnostic["fits"] is True
    assert diagnostic["estimated_input_tokens"] <= diagnostic["available_input_tokens"]
    assert diagnostic["partial_span_count"] == 1


def test_fixed_input_that_cannot_fit_is_a_technical_failure():
    with pytest.raises(GenerationInputBudgetExceeded) as error:
        select([span(0, "合成の本文。")], tokens=1)
    assert error.value.diagnostic["reason"] == "fixed_input_exceeds_limit"


def test_no_complete_source_unit_fits_is_not_an_empty_success():
    with pytest.raises(GenerationInputBudgetExceeded) as error:
        select([span(0, "x" * 1000)], chars=10)
    assert error.value.diagnostic["reason"] == "no_evidence_fits"


def test_html_table_is_not_cut_into_fragments_to_fill_the_budget():
    table = "<table><tbody>" + "".join(f"<tr><td>項目{i}</td><td>合成の値</td></tr>" for i in range(30)) + "</tbody></table>"
    original = span(0, table, start=120)
    other = span(1, "合成の本文。")
    selected, _, diagnostic = select([original, other], chars=150)
    assert selected == [other]
    assert diagnostic["excluded_span_count"] == 1
    assert diagnostic["partial_span_count"] == 0
