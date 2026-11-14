"""本文全体の独立判定を保持し、不要な片段番号や自己申告ラベルに依存しない。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_eval import AnswerCorrectnessOutput, evaluate_answer_payload

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).with_name("fixtures") / "condition_evaluation_cases.json").read_text()), ids=lambda case: case["id"]
)
def test_fixed_synthetic_inputs_are_passed_as_complete_text(case):
    output = AnswerCorrectnessOutput(correctness="unassessed", reason="比較材料の確認")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(case, object())
    inputs = json.loads(model.call_args.args[1])
    for field in ("question", "standard_answer"):
        assert inputs[field] == case[field]
    assert inputs["actual_responses"][0]["answer_text"] == case["answer_text"]
    assert result["answer_correctness"] is None
    assert model.call_count == 1


@pytest.mark.parametrize("verdict,score", [("correct", 1), ("incorrect", 0), ("unassessed", None)])
def test_hold_expectation_written_in_standard_answer_is_scored_as_correctness(verdict, score):
    data = {
        "question": "合成の資料にない項目の値は？",
        "standard_answer": "資料に記載がないため答えられないと伝える。",
        "answer_text": "資料から値を確認できません。",
    }
    output = AnswerCorrectnessOutput(correctness=verdict, reason="本文と標準回答の比較")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    assert result["status"] == "completed"
    assert result["answer_correctness"] == score
    assert model.call_count == 1
    inputs = json.loads(model.call_args.args[1])
    assert set(inputs) == {"question", "standard_answer", "actual_responses"}
    schema = model.call_args.args[3].model_json_schema()
    assert set(schema["properties"]) == {"correctness", "reason"}
    assert "answer_unit_id" not in json.dumps(schema)


@pytest.mark.parametrize("verdict,expected", [("correct", 1), ("incorrect", 0), ("unassessed", None)])
def test_model_whole_body_verdict_is_not_reinterpreted_by_coverage_or_headings(verdict, expected):
    from rag_engine.generation.answer_labels import answer_labels

    data = {
        "question": "合成の値は？",
        "standard_answer": "項目Aは5です。項目Bは8です。",
        "answer_text": answer_labels().gaps_section + "\n項目Aは5です。",
    }
    output = AnswerCorrectnessOutput(correctness=verdict, reason="本文の意味に基づく判定")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    assert json.loads(model.call_args.args[1])["actual_responses"][0]["answer_text"] == data["answer_text"]
    assert result["answer_correctness"] == expected
    assert model.call_count == 1


def test_actual_citations_are_removed_from_the_compared_body():
    actual = "合成の値は24です。\n根拠：synthetic.csv p.1"
    data = {
        "question": "合成の値は？",
        "standard_answer": "合成の値は24です。",
        "answer_text": actual,
        "actual_responses": [{"question": "合成の値は？", "answer_text": actual}],
    }
    output = AnswerCorrectnessOutput(correctness="correct", reason="正しい値を回答している。")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    inputs = json.loads(model.call_args.args[1])
    assert inputs["actual_responses"] == [{"question": "合成の値は？", "answer_text": "合成の値は24です。"}]
    assert "answer_text" not in inputs
    assert "synthetic.csv" not in model.call_args.args[1]
    assert result["answer_correctness"] == 1
    assert model.call_count == 1
