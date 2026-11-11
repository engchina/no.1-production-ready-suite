"""述べた内容の矛盾、標準回答の未回答、未評価の回答単位を分ける。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_eval import RUBRIC_VERSION, AnswerCorrectnessOutput, evaluate_answer_payload

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"
DATA = {
    "question": "項目Aと項目Bの値を教えてください。",
    "standard_answer": "項目Aは5です。\n項目Bは8です。",
    "answer_text": "項目Aは5です。\n項目Bは3です。",
}


def row(answer, reference, relation):
    return {"answer_unit_id": answer, "standard_unit_ids": reference, "relation": relation, "reason": "合成の比較理由"}


def evaluate(data, rows, missing=()):
    output = AnswerCorrectnessOutput(comparisons=rows, uncovered_points=list(missing), reason="合成の全体理由")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    assert model.call_count == 1
    return result


def test_one_contradiction_cannot_be_overridden_by_a_free_overall_reason():
    result = evaluate(DATA, [row("A1", ["S1"], "consistent"), row("A2", ["S2"], "contradicts")])
    assert result["answer_correctness"] == 0
    assert result["claim_comparisons"][1]["answer_text"] == "項目Bは3です。"
    assert result["claim_comparisons"][1]["standard_texts"] == ["項目Bは8です。"]
    assert result["rubric_version"] == RUBRIC_VERSION == 27


def test_correct_partial_answer_records_missing_point_without_factual_failure():
    result = evaluate(
        DATA | {"answer_text": "項目Aは5です。"}, [row("A1", ["S1"], "consistent")], [{"standard_unit_id": "S2", "reason": "項目Bは未回答"}]
    )
    assert result["answer_correctness"] == 1
    assert result["uncovered_points"][0]["standard_text"] == "項目Bは8です。"


def test_unassessed_answer_tail_is_not_silently_a_correct_whole_answer():
    result = evaluate(DATA, [row("A1", ["S1"], "consistent")])
    assert result["answer_correctness"] is None
    assert result["status"] == "completed"


@pytest.mark.parametrize("rows", [[row("A9", ["S1"], "consistent")], [row("A1", ["S9"], "contradicts")], [row("A1", [], "consistent")]])
def test_unknown_or_missing_source_references_are_evaluation_errors_without_another_judge(rows):
    result = evaluate(DATA, rows)
    assert result["status"] == "error"
    assert result["judge_diagnostics"]["judge_calls"] == 1
    assert result["judge_diagnostics"]["judge_retries"] == 0


def test_insufficient_comparison_material_is_unassessed_without_rewriting_the_answer():
    result = evaluate(DATA, [row("A1", [], "unassessed"), row("A2", [], "unassessed")])
    assert result["answer_correctness"] is None
    assert result["status"] == "completed"


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).with_name("fixtures") / "condition_evaluation_cases.json").read_text()), ids=lambda case: case["id"]
)
def test_fixed_synthetic_inputs_are_preserved_in_the_reference_units(case):
    with patch(PARSE, return_value=AnswerCorrectnessOutput(comparisons=[], uncovered_points=[], reason="未判定")) as model:
        result = evaluate_answer_payload(case, object())
    inputs = json.loads(model.call_args.args[1])
    for field in ("standard_answer", "answer_text"):
        assert "".join("".join(unit["text"].split()) for unit in inputs[field]) == "".join(case[field].split())
    assert inputs["question"] == case["question"]
    assert result["answer_correctness"] is None
    assert model.call_count == 1


@pytest.mark.parametrize("section", ["gaps_section", "unverified_section", "deadline_section"])
def test_substantive_claims_under_notes_are_not_hidden_from_the_judge(section):
    from rag_engine.generation.answer_labels import answer_labels

    text = "項目Aは5です。\n\n" + getattr(answer_labels(), section) + "\n\n項目Bは3です。"
    with patch(PARSE, return_value=AnswerCorrectnessOutput(comparisons=[], uncovered_points=[], reason="未判定")) as model:
        result = evaluate_answer_payload(DATA | {"answer_text": text}, object())
    inputs = json.loads(model.call_args.args[1])
    assert "".join("".join(unit["text"].split()) for unit in inputs["answer_text"]) == "".join(text.split())
    assert result["answer_correctness"] is None
    assert model.call_count == 1


@pytest.mark.parametrize("context", ["資料により確認できた事項", "合成資料の説明"])
def test_presentation_units_are_recorded_separately_without_becoming_unassessed(context):
    result = evaluate(
        DATA | {"answer_text": context + "\n\n項目Aは5です。"}, [row("A1", [], "not_applicable"), row("A2", ["S1"], "consistent")]
    )
    assert result["answer_correctness"] == 1
    assert result["claim_comparisons"][0]["relation"] == "not_applicable"
    assert result["claim_comparisons"][0]["answer_text"] == context


def test_presentation_only_is_not_a_correct_answer():
    result = evaluate(DATA | {"answer_text": "資料により確認できた事項"}, [row("A1", [], "not_applicable")])
    assert result["answer_correctness"] is None


def test_wrong_claim_after_a_note_heading_still_dominates_the_score():
    text = "資料から確認できなかった事項\n\n項目Bは3です。"
    result = evaluate(DATA | {"answer_text": text}, [row("A1", [], "not_applicable"), row("A2", ["S2"], "contradicts")])
    assert result["answer_correctness"] == 0


def test_material_claim_without_comparison_is_still_unassessed():
    result = evaluate(DATA, [row("A1", ["S1"], "consistent"), row("A2", [], "unassessed")])
    assert result["answer_correctness"] is None
