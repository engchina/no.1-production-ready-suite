"""主張ごとの正確性と要点の網羅（#5262）: 判定へ渡す入力、印からの採点、記録の再生。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_eval import (
    CORRECTNESS_PROMPT,
    AnswerCorrectnessOutput,
    JudgedClaim,
    claim_verdict,
    evaluate_answer_payload,
    judge_inputs,
    score_judgement,
)

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"
# 合成の匿名の対照（正しい部分回答・資料不足の明示・事実の矛盾・標準回答が方向性だけ・答えの無い回答、
# 資料不足の留保と標準回答の提案の方向・標準回答も確定できない・留保を含む事実の矛盾）。
# judge_inputs は判定へ実際に渡した入力、recorded_output は xai.grok-4.7（推論の量 medium）・rubric_version 34 の
# 実呼び出しの出力（#5262・#5302）。
CASES = json.loads((Path(__file__).with_name("fixtures") / "correctness_judge_cases.json").read_text())


def _output(claims, points):
    return AnswerCorrectnessOutput.model_validate({"claims": claims, "points": points})


def _payload(**fields):
    return {
        "question": "項目Aと項目Bの値は？",
        "standard_answer": "項目Aは5。項目Bは8。",
        "answer_text": "項目Aは5です。\n根拠：synthetic.pdf p.1\n項目Bは資料で確認できませんでした。",
        **fields,
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_recorded_judge_outputs_replay_to_expected_scores(case):
    """記録した出力を再生し、渡す入力・点・理由を確かめる（実モデルの意味判断の正しさは保証しない）。"""
    output = AnswerCorrectnessOutput.model_validate(case["recorded_output"])
    with patch(PARSE, return_value=output) as model:
        result = evaluate_answer_payload(
            {
                "question": case["question"],
                "standard_answer": case["standard_answer"],
                "answer_points": case["answer_points"],
                "answer_text": case["answer_text"],
            },
            object(),
        )
    system, prompt, _settings, schema = model.call_args.args
    assert system == CORRECTNESS_PROMPT and schema is AnswerCorrectnessOutput
    assert json.loads(prompt) == case["judge_inputs"]
    assert model.call_count == 1
    assert result["status"] == "completed"
    assert result["answer_correctness"] == case["expected_correctness"]
    assert result["answer_coverage"] == case["recorded_coverage"]
    assert result["correctness_reason"] == case["recorded_reason"]


def test_inputs_number_standard_points_and_attach_citations_to_their_passage():
    """標準回答・要点・本文を番号つきの単位で渡す。要点は標準回答に結合しない。出典の行は本文の段落にせず、
    直前の段落の sources に添える（網羅の要点が根拠の場所に触れるときに読む）。"""
    inputs = judge_inputs(_payload(answer_points=["項目Aの値", "確かめる条件"]))
    assert inputs["standard_answer"] == [{"id": "S1", "text": "項目Aは5。"}, {"id": "S2", "text": "項目Bは8。"}]
    assert inputs["points"] == [{"id": "P1", "text": "項目Aの値"}, {"id": "P2", "text": "確かめる条件"}]
    (response,) = inputs["actual_responses"]
    assert response["answer"] == [
        {"id": "A1", "text": "項目Aは5です。", "sources": ["根拠：synthetic.pdf p.1"]},
        {"id": "A2", "text": "項目Bは資料で確認できませんでした。"},
    ]


def test_citation_lines_before_any_passage_are_not_attached():
    """先頭の出典の行は添える段落が無いので渡さない。段落の番号は出典の行の有無で変わらない。"""
    inputs = judge_inputs(_payload(answer_text="根拠：synthetic.pdf p.1\n項目Aは5です。\n根拠：synthetic.pdf p.2"))
    (response,) = inputs["actual_responses"]
    assert response["answer"] == [{"id": "A1", "text": "項目Aは5です。", "sources": ["根拠：synthetic.pdf p.2"]}]


def test_points_default_to_standard_sentences_and_answer_ids_run_across_turns():
    inputs = judge_inputs(
        _payload(
            actual_responses=[
                {"question": "質問", "answer_text": "どちらの項目ですか。"},
                {"question": "返答", "answer_text": "項目Aは5です。項目Bは8です。"},
            ]
        )
    )
    assert inputs["points"] == [{"id": "P1", "text": "項目Aは5。"}, {"id": "P2", "text": "項目Bは8。"}]
    ids = [[row["id"] for row in response["answer"]] for response in inputs["actual_responses"]]
    assert ids == [["A1"], ["A2", "A3"]]


@pytest.mark.parametrize(
    ("verdicts", "correctness"),
    [
        (["supported", "not_in_standard"], 1.0),  # 正しい部分回答と、標準回答に記載の無い主張
        (["supported", "contradicted"], 0.0),  # 矛盾が 1 つでもあれば 0
        (["not_in_standard"], 0.0),  # 標準回答と合う主張が無い（答えていない）
        ([], 0.0),
    ],
    ids=["partial", "contradiction", "no_answer", "no_claims"],
)
def test_correctness_is_computed_from_claim_verdicts(verdicts, correctness):
    inputs = judge_inputs(_payload())
    claims = [
        {
            "claim": "合成の主張",
            "answer_ids": [f"A{index}"],
            "standard_ids": [] if verdict == "not_in_standard" else [f"S{index}"],
            "states_source_gap": False,
            "consistent": verdict != "contradicted",
        }
        for index, verdict in enumerate(verdicts, 1)
    ]
    scores = score_judgement(inputs, _output(claims, [{"point_id": "P1", "covered": True}, {"point_id": "P2", "covered": False}]))
    assert scores["answer_correctness"] == correctness
    assert scores["answer_coverage"] == 0.5
    assert scores["coverage_points"] == [{"point": "項目Aは5。", "covered": True}, {"point": "項目Bは8。", "covered": False}]


def test_reason_lists_each_contradiction_with_the_original_text():
    inputs = judge_inputs(_payload(answer_text="項目Aは3です。項目Bは8です。"))
    claims = [
        {"claim": "項目Aは3です。", "answer_ids": ["A1"], "states_source_gap": False, "consistent": False, "standard_ids": ["S1"]},
        {"claim": "項目Bは8です。", "answer_ids": ["A2"], "states_source_gap": False, "consistent": True, "standard_ids": ["S2"]},
    ]
    scores = score_judgement(inputs, _output(claims, [{"point_id": "P1", "covered": True}, {"point_id": "P2", "covered": True}]))
    assert scores["correctness_reason"] == "回答の「項目Aは3です。」と標準回答の「項目Aは5。」が食い違う。"
    assert scores["correctness_claims"][0] == {
        "verdict": "contradicted",
        "claim": "項目Aは3です。",
        "answer": "項目Aは3です。",
        "standard": "項目Aは5。",
        "states_source_gap": False,
        "consistent": False,
    }


@pytest.mark.parametrize(
    ("claims", "points"),
    [
        (
            [{"claim": "合成の主張", "answer_ids": ["A9"], "states_source_gap": False, "consistent": True, "standard_ids": ["S1"]}],
            ["P1", "P2"],
        ),
        (
            [{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "consistent": False, "standard_ids": ["S9"]}],
            ["P1", "P2"],
        ),
        ([{"claim": "合成の主張", "answer_ids": [], "states_source_gap": False, "consistent": True, "standard_ids": ["S1"]}], ["P1", "P2"]),
        ([{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "consistent": True, "standard_ids": ["S1"]}], ["P1"]),
        (
            [{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "consistent": True, "standard_ids": ["S1"]}],
            ["P1", "P2", "P3"],
        ),
    ],
    ids=["unknown_answer_id", "unknown_standard_id", "no_answer_id", "missing_point", "extra_point"],
)
def test_output_pointing_outside_the_input_is_an_evaluation_error(claims, points):
    """入力に無い id・要点の過不足は評価のエラーにする（点を推測で補わない）。"""
    output = _output(claims, [{"point_id": point, "covered": True} for point in points])
    with patch(PARSE, return_value=output):
        result = evaluate_answer_payload(_payload(), object())
    assert result["status"] == "error" and result["error_type"] == "InvalidJudgeReference"
    assert "answer_correctness" not in result


def test_comparison_questions_live_only_in_the_comparison_step():
    """照合の問いの答え方は指示の Step 3 にだけ書く（応答 schema の説明に重ねない。#5250）。印はモデルに選ばせない（#5408）。"""
    from pr_backend_core.response_schema import with_schema_field_guide

    assert [line for line in CORRECTNESS_PROMPT.splitlines() if line.startswith("## Step")] == [
        "## Step 1: 評価の入力を読む",
        "## Step 2: 本文を主張に分ける",
        "## Step 3: 主張を標準回答と照らす",
        "## Step 4: 要点の網羅を確かめる",
    ]
    step3 = CORRECTNESS_PROMPT.split("## Step 3:", 1)[1].split("## Step 4:", 1)[0]
    questions = {line.split(":", 1)[0].removeprefix("- "): line for line in step3.splitlines() if line.startswith("- ")}
    assert tuple(questions) == ("standard_ids", "states_source_gap", "consistent")
    # 同じ点は、同じ対象・同じ条件・適用範囲・観点。条件・観点が違う文は照らす文にしない。
    assert "同じ条件・適用範囲・観点" in questions["standard_ids"] and "違う文" in questions["standard_ids"]
    assert "確認できない" in questions["states_source_gap"] and "記載が無い" in questions["states_source_gap"]
    assert "同時に真になり得る" in questions["consistent"] and "確定できない" in questions["consistent"]
    # 主張は条件ごと取り出して claim に書く。条件を主張の種類として別に取り出させない。
    step2 = CORRECTNESS_PROMPT.split("## Step 2:", 1)[1].split("## Step 3:", 1)[0]
    assert "条件と結論を別の主張に分けない" in step2 and "claim" in step2
    schema = AnswerCorrectnessOutput.model_json_schema()
    claim_properties = schema["$defs"]["JudgedClaim"]["properties"]
    assert list(claim_properties) == ["claim", "answer_ids", "standard_ids", "states_source_gap", "consistent"]
    assert "verdict" not in claim_properties
    assert all("description" not in claim_properties[key] for key in ("states_source_gap", "consistent"))
    # 実際に送る指示（項目の説明の Step を足した文）でも、問いの答え方は 1 回だけ。
    guided, _stripped = with_schema_field_guide(CORRECTNESS_PROMPT, schema)
    assert all(guided.count(f"- {key}:") == 1 for key in questions)
    # 点・理由はコードが作るので、出力に点・理由の項目は無い。
    assert set(AnswerCorrectnessOutput.model_fields) == {"claims", "points"}


@pytest.mark.parametrize(
    ("standard_ids", "states_source_gap", "consistent", "verdict"),
    [
        (["S1"], False, True, "supported"),
        (["S1"], True, True, "supported"),  # 標準回答も確定できないと述べる
        (["S1"], False, False, "contradicted"),
        (["S1"], True, False, "not_in_standard"),  # 資料の欠けを述べる主張は矛盾にしない（#5302）
        ([], False, False, "not_in_standard"),  # 同じ点を述べる文が無い（条件・観点が違う。#5408）
        ([], True, True, "not_in_standard"),
    ],
)
def test_verdict_is_decided_by_code_from_the_comparison_answers(standard_ids, states_source_gap, consistent, verdict):
    """#5408: 印はモデルに選ばせず、同じ点の文・資料の欠け・両立の答えからコードが決める。"""
    claim = JudgedClaim(
        claim="合成の主張",
        answer_ids=["A1"],
        standard_ids=standard_ids,
        states_source_gap=states_source_gap,
        consistent=consistent,
    )
    assert claim_verdict(claim) == verdict


def test_reservation_only_answer_scores_zero_without_calling_it_a_contradiction():
    """本文がすべて留保（標準回答と合う主張が無い）なら 0。理由は矛盾ではなく、合う主張が無いこと。"""
    inputs = judge_inputs(_payload(answer_text="項目Aと項目Bは資料で確認できず、確定できません。"))
    output = _output(
        [{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "consistent": True, "standard_ids": []}],
        [{"point_id": "P1", "covered": False}, {"point_id": "P2", "covered": False}],
    )
    scores = score_judgement(inputs, output)
    assert scores["answer_correctness"] == 0.0
    assert scores["correctness_reason"] == "標準回答と合う主張がありません（標準回答に具体的な答えが無いか、回答が質問に答えていません）。"
    assert "食い違う" not in scores["correctness_reason"]


@pytest.mark.parametrize(
    ("standard_answer", "verdict", "correctness"),
    [
        ("架空のサービスAの状態と処理記録に基づいた確認手順を提案できる。", "not_in_standard", 0.0),
        ("状態画面でstatusがreadyであることを確かめる。処理記録の完了件数は3件。", "supported", 1.0),
        ("状態画面でstatusがstoppedであることを確かめる。処理記録の完了件数は8件。", "contradicted", 0.0),
    ],
)
def test_coverage_does_not_override_correctness(standard_answer, verdict, correctness):
    """#5398: 要点をすべて覆っても、正確性は主張の印だけで決まる。合う主張が無いときの理由は、標準回答に
    具体的な答えが無い場合と質問に答えていない場合のどちらとも断定しない。"""
    inputs = judge_inputs(
        _payload(
            question="架空のサービスAの起動をどう確認しますか。",
            standard_answer=standard_answer,
            answer_points=["起動状態の確認方法", "処理完了件数の確認方法"],
            answer_text="状態画面でstatusがreadyであることを確かめます。処理記録で完了件数が3件であることを確かめます。",
        )
    )
    standard_ids = [] if verdict == "not_in_standard" else ["S1"]
    output = _output(
        [
            {
                "claim": "合成の主張",
                "answer_ids": ["A1"],
                "standard_ids": standard_ids,
                "states_source_gap": False,
                "consistent": verdict != "contradicted",
            }
        ],
        [{"point_id": "P1", "covered": True}, {"point_id": "P2", "covered": True}],
    )
    scores = score_judgement(inputs, output)
    assert (scores["answer_correctness"], scores["answer_coverage"]) == (correctness, 1.0)
    if verdict == "not_in_standard":
        assert "標準回答に具体的な答えが無いか" in scores["correctness_reason"]


def test_contradiction_reason_quotes_the_claim_with_its_condition():
    """#5408: 矛盾の理由には、判定のモデルが条件ごと書いた主張を出す（条件の無い段落だけを引かない）。"""
    inputs = judge_inputs(
        _payload(
            standard_answer="全体を再起動したときは、サービスAの起動を確かめる。",
            answer_text="全体を再起動する場合について説明します。サービスAの確認は不要です。",
        )
    )
    output = _output(
        [
            {
                "claim": "全体を再起動する場合、サービスAの確認は不要。",
                "answer_ids": ["A1", "A2"],
                "standard_ids": ["S1"],
                "states_source_gap": False,
                "consistent": False,
            }
        ],
        [{"point_id": "P1", "covered": False}],
    )
    scores = score_judgement(inputs, output)
    assert scores["answer_correctness"] == 0.0
    assert scores["correctness_reason"] == (
        "回答の「全体を再起動する場合、サービスAの確認は不要。」と標準回答の「全体を再起動したときは、サービスAの起動を確かめる。」が食い違う。"
    )
    assert scores["correctness_claims"][0]["answer"] == "全体を再起動する場合について説明します。 サービスAの確認は不要です。"
