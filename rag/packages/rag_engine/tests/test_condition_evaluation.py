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
# 判定へ渡す回答の形は judged_responses（無ければ本文そのまま）。
CASES = json.loads((Path(__file__).with_name("fixtures") / "condition_evaluation_cases.json").read_text())
# 不足の節を本文から分けて渡す合成の対照（#5262）。judged_responses は判定へ実際に渡した回答、recorded_* は
# xai.grok-4.7（推論の量 medium）・rubric_version 33 の実呼び出しの記録。
SPLIT_CASES = json.loads((Path(__file__).with_name("fixtures") / "correctness_judge_input_cases.json").read_text())


def _judged(case):
    return case.get("judged_responses") or [{"question": case["question"], "answer_text": case["answer_text"]}]


@pytest.mark.parametrize("case", CASES + SPLIT_CASES, ids=lambda case: case["id"])
def test_fixed_synthetic_inputs_are_passed_as_complete_text(case):
    output = AnswerCorrectnessOutput(correctness="unassessed", reason="比較材料の確認")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(case, object())
    inputs = json.loads(model.call_args.args[1])
    for field in ("question", "standard_answer"):
        assert inputs[field] == case[field]
    assert inputs["actual_responses"] == _judged(case)
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


@pytest.mark.parametrize("case", CASES + SPLIT_CASES, ids=lambda case: case["id"])
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
        "actual_responses": _judged(case),
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


def test_gap_section_is_passed_apart_from_the_answer_body():
    """「確認できなかった事項」の節は本文から分けて unconfirmed で渡す（書式で決め、語の意味は見ない）。"""
    from rag_engine.generation.answer_labels import answer_labels

    labels = answer_labels()
    answer = (
        f"{labels.rules_section}\n\n{labels.item_bullet}項目Aは5です。\n根拠：synthetic.pdf p.2\n\n"
        f"{labels.gaps_section}\n\n{labels.item_bullet}項目Bの値\n{labels.item_bullet}項目Cの値\n\n"
        f"{labels.unverified_section}\n\n{labels.item_bullet}合成の章節 3"
    )
    data = {"question": "項目A・B・Cの値は？", "standard_answer": "項目Aは5、項目Bは8、項目Cは9。", "answer_text": answer}
    output = AnswerCorrectnessOutput(correctness="consistent", reason="述べた値が一致する")
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(data, object())
    (response,) = json.loads(model.call_args.args[1])["actual_responses"]
    assert response["unconfirmed"] == ["項目Bの値", "項目Cの値"]
    assert labels.gaps_section not in response["answer_text"]
    assert "項目Aは5です。" in response["answer_text"] and labels.unverified_section in response["answer_text"]
    assert "synthetic.pdf" not in response["answer_text"]
    assert result["answer_correctness"] == 1


@pytest.mark.parametrize(
    "answer",
    ["項目Aは5です。", "{gaps}\n\n{bullet}項目Aの値"],
    ids=["no_gap_section", "gap_section_only"],
)
def test_answer_without_other_body_is_not_split(answer):
    """節が無い回答と、節だけの回答（拒答か、資料に無いという答え）は本文のまま渡す。"""
    from rag_engine.generation.answer_labels import answer_labels

    labels = answer_labels()
    text = answer.format(gaps=labels.gaps_section, bullet=labels.item_bullet)
    output = AnswerCorrectnessOutput(correctness="contradicts", reason="比較")
    with patch(PARSE, return_value=output) as model:
        evaluate_answer_payload({"question": "項目Aの値は？", "standard_answer": "項目Aは5。", "answer_text": text}, object())
    assert json.loads(model.call_args.args[1])["actual_responses"] == [{"question": "項目Aの値は？", "answer_text": text}]


def test_prompt_names_the_gap_items_field_once():
    """不足の項目（unconfirmed）は指示の入力の読み方で 1 回だけ定義し、判定の Step で使い方を書く。"""
    step1, step2 = CORRECTNESS_PROMPT.split("## Step 2")
    assert "unconfirmed は" in step1
    assert "unconfirmedの事項は判定に使わない" in step2
