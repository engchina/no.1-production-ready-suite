"""本文全体の独立判定を保持し、不要な片段番号や自己申告ラベルに依存しない。"""

import json
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_eval import CORRECTNESS_PROMPT, CORRECTNESS_VALUES, AnswerCorrectnessOutput, evaluate_answer_payload

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"
# 合成の対照（正しい部分回答・事実の矛盾・単なる不足・資料不足の明示・答えのある質問の拒答など）。
# recorded_* は openai.gpt-oss-120b（推論の量 medium）・rubric_version 32 の実呼び出しの記録（#5207）。
CASES = json.loads((Path(__file__).with_name("fixtures") / "condition_evaluation_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
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


@pytest.mark.parametrize("verdict,score", [("consistent", 1), ("contradicts", 0), ("unassessed", None)])
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


@pytest.mark.parametrize("verdict,expected", [("consistent", 1), ("contradicts", 0), ("unassessed", None)])
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
    output = AnswerCorrectnessOutput(correctness="consistent", reason="正しい値を回答している。")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    inputs = json.loads(model.call_args.args[1])
    assert inputs["actual_responses"] == [{"question": "合成の値は？", "answer_text": "合成の値は24です。"}]
    assert "answer_text" not in inputs
    assert "synthetic.csv" not in model.call_args.args[1]
    assert result["answer_correctness"] == 1
    assert model.call_count == 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_recorded_judge_outputs_replay_to_expected_scores(case):
    """記録した判定を再生し、判定へ渡す入力と点への写像を確かめる（実モデルの意味判断の正しさは保証しない）。"""
    output = AnswerCorrectnessOutput(correctness=case["recorded_verdict"], reason=case["recorded_reason"])
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(case, object())
    system, prompt, _settings, schema = model.call_args.args
    assert system == CORRECTNESS_PROMPT and schema is AnswerCorrectnessOutput
    assert json.loads(prompt) == {
        "question": case["question"],
        "standard_answer": case["standard_answer"],
        "actual_responses": [{"question": case["question"], "answer_text": case["answer_text"]}],
    }
    assert case["recorded_verdict"] == case["expected_verdict"]
    assert result["answer_correctness"] == CORRECTNESS_VALUES[case["expected_verdict"]]
    assert result["correctness_reason"] == case["recorded_reason"]


def test_prompt_names_every_verdict_and_states_the_reason_language_once():
    """判定の値は「述べた内容が標準回答と合うか・食い違うか」の名前で、指示はすべての値を名指す。

    理由の言語の規則は指示だけに書き、出力の型の説明に重ねない。
    """
    verdicts = get_args(AnswerCorrectnessOutput.model_fields["correctness"].annotation)
    assert verdicts == ("consistent", "contradicts", "unassessed", "not_applicable")
    assert all(verdict in CORRECTNESS_PROMPT for verdict in verdicts)
    assert CORRECTNESS_VALUES == {"consistent": 1.0, "contradicts": 0.0}
    assert "日本語" in CORRECTNESS_PROMPT
    assert "日本語" not in json.dumps(AnswerCorrectnessOutput.model_json_schema(), ensure_ascii=False)
