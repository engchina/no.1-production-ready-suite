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
    output = AnswerCorrectnessOutput(correctness="unassessed", response_appropriateness="not_applicable", reason="比較材料の確認")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(case, object())
    inputs = json.loads(model.call_args.args[1])
    for field in ("question", "standard_answer", "answer_text"):
        assert inputs[field] == case[field]
    assert result["answer_correctness"] is None
    assert model.call_count == 1


@pytest.mark.parametrize("kind,score", [("appropriate", 1), ("inappropriate", 0), ("unassessed", None)])
def test_expected_handling_is_scored_from_body_with_no_reference_answer(kind, score):
    data = {
        "question": "合成の資料にない項目の値は？",
        "standard_answer": "",
        "answer_text": "資料から値を確認できません。",
        "response_expectations": [
            {"question": "合成の質問", "answer_text": "資料から値を確認できません。", "expected_decisions": ["hold"]}
        ],
    }
    output = AnswerCorrectnessOutput(correctness="not_applicable", response_appropriateness=kind, reason="本文と期待する対応の比較")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    assert result["status"] == "completed"
    assert result["answer_correctness"] is None
    assert result["response_decision_accuracy"] == score
    assert model.call_count == 1
    schema = model.call_args.args[3].model_json_schema()
    assert set(schema["properties"]) == {"correctness", "response_appropriateness", "reason"}
    assert "answer_unit_id" not in json.dumps(schema)


@pytest.mark.parametrize("verdict,expected", [("correct", 1), ("incorrect", 0), ("unassessed", None)])
def test_model_whole_body_verdict_is_not_reinterpreted_by_coverage_or_headings(verdict, expected):
    from rag_engine.generation.answer_labels import answer_labels

    data = {
        "question": "合成の値は？",
        "standard_answer": "項目Aは5です。項目Bは8です。",
        "answer_text": answer_labels().gaps_section + "\n項目Aは5です。",
    }
    output = AnswerCorrectnessOutput(correctness=verdict, response_appropriateness="not_applicable", reason="本文の意味に基づく判定")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    assert json.loads(model.call_args.args[1])["answer_text"] == data["answer_text"]
    assert result["answer_correctness"] == expected
    assert model.call_count == 1
