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
from rag_engine.resources.runtime import bind_prompt_overrides

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"
# 合成の匿名の対照（正しい部分回答・資料不足の明示・事実の矛盾・標準回答が方向性だけ・答えの無い回答、
# 資料不足の留保と標準回答の提案の方向・標準回答も確定できない・留保を含む事実の矛盾）。
# judge_inputs は判定へ実際に渡した入力。claude-opus-5-5 の記録は r_point_and_claim_scope が rubric 40（#5670）、
# r_heading_gap_and_attribute が 41（#5677・#5682）。それ以前は xai.grok-4.7（推論の量 medium）・rubric 34 の実測（#5262・#5302）。
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


def test_editing_the_prompt_preserves_the_recorded_claims_and_points_contract():
    case = next(case for case in CASES if case["id"] == "r_point_and_claim_scope")
    edited = CORRECTNESS_PROMPT + "\n回答の表記を尊重する。"
    recorded = AnswerCorrectnessOutput.model_validate(case["recorded_output"])
    with bind_prompt_overrides({"evaluation.correctness": edited}), patch(PARSE, return_value=recorded) as model:
        result = evaluate_answer_payload(case, object())
    system, inputs, _, schema = model.call_args.args
    assert model.call_count == 1 and system == edited and schema is AnswerCorrectnessOutput
    assert json.loads(inputs) == case["judge_inputs"]
    assert result["answer_correctness"] == case["expected_correctness"]
    assert result["answer_coverage"] == case["recorded_coverage"]


def test_topic_coverage_and_claim_references_are_scoped_per_item():
    """値を誤っても話題は覆える。同じ段落の記載先と資料不足は、それぞれの参照で表示する。"""
    case = next(case for case in CASES if case["id"] == "r_point_and_claim_scope")
    inputs = case["judge_inputs"]
    result = score_judgement(inputs, AnswerCorrectnessOutput.model_validate(case["recorded_output"]))
    assert [point["covered"] for point in result["coverage_points"]] == [True, False, True]
    paragraph = inputs["actual_responses"][0]["answer"][-1]["text"]
    reference = inputs["standard_answer"][-1]["text"]
    assert {(claim["verdict"], claim["standard"]) for claim in result["correctness_claims"] if claim["answer"] == paragraph} == {
        ("supported", reference),
        ("not_in_standard", ""),
    }


def test_heading_completes_each_gap_and_only_matching_availability_is_cited():
    case = next(case for case in CASES if case["id"] == "r_heading_gap_and_attribute")
    output = AnswerCorrectnessOutput.model_validate(case["recorded_output"])
    inputs = case["judge_inputs"]
    result = score_judgement(inputs, output)
    paragraphs = inputs["actual_responses"][0]["answer"]
    heading = paragraphs[1]["text"]
    matching_gap = inputs["standard_answer"][1]["text"]
    gaps = {(claim["answer"], claim["standard"]) for claim in result["correctness_claims"] if claim["states_source_gap"]}
    assert gaps == {(f"{heading} {paragraphs[2]['text']}", ""), (f"{heading} {paragraphs[3]['text']}", matching_gap)}
    assert [(point["point"], point["covered"]) for point in result["coverage_points"]] == [("対象の種類", False), ("保存期間", True)]


def test_both_complete_passages_are_shown_for_one_claim():
    payload = _payload(
        answer_text="項目Aは5です。\n項目Bは8です。\n項目Aの値は5です。",
        answer_points=["項目Aの値"],
    )
    output = _output(
        [{"claim": "項目Aの値は5。", "answer_ids": ["A1", "A3"], "standard_ids": ["S1"], "states_source_gap": False, "relation": "same"}],
        [{"point_id": "P1", "covered": True}],
    )
    with patch(PARSE, return_value=output):
        result = evaluate_answer_payload(payload, object())
    (claim,) = result["correctness_claims"]
    assert claim["answer"] == "項目Aは5です。 項目Aの値は5です。"
    assert claim["verdict"] == "supported"


def test_answering_whether_a_value_can_be_confirmed_counts_even_when_the_standard_disagrees():
    payload = _payload(
        question="今回の資料で項目Aの値を確認できますか。",
        standard_answer="今回の資料で項目Aの値を確認できます。",
        answer_text="今回の資料では項目Aの値を確認できません。",
        answer_points=["今回の資料で項目Aの値を確認できるか"],
    )
    output = _output(
        [
            {
                "claim": "項目Aの値は資料から確認できない。",
                "answer_ids": ["A1"],
                "standard_ids": ["S1"],
                "states_source_gap": True,
                "relation": "conflict",
            }
        ],
        [{"point_id": "P1", "covered": True}],
    )
    with patch(PARSE, return_value=output):
        result = evaluate_answer_payload(payload, object())
    (claim,) = result["correctness_claims"]
    assert claim["states_source_gap"] is True and claim["relation"] == "conflict"
    assert result["answer_correctness"] == 0 and result["answer_coverage"] == 1


def test_inline_reservation_keeps_the_reported_retention_separate_from_the_device_type():
    case = next(case for case in CASES if case["id"] == "h_inline_negation")
    output = AnswerCorrectnessOutput.model_validate(case["recorded_output"])
    result = score_judgement(case["judge_inputs"], output)
    points = {row["point"]: row["covered"] for row in result["coverage_points"]}
    assert points["機器の種類"] is False
    assert result["answer_coverage"] == 0.25


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
    assert inputs["question"] == "項目Aと項目Bの値は？"
    assert [turn["question"] for turn in inputs["actual_responses"]] == ["質問", "返答"]
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
            "relation": "conflict" if verdict == "contradicted" else "same",
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
        {"claim": "項目Aは3です。", "answer_ids": ["A1"], "states_source_gap": False, "relation": "conflict", "standard_ids": ["S1"]},
        {"claim": "項目Bは8です。", "answer_ids": ["A2"], "states_source_gap": False, "relation": "same", "standard_ids": ["S2"]},
    ]
    scores = score_judgement(inputs, _output(claims, [{"point_id": "P1", "covered": True}, {"point_id": "P2", "covered": True}]))
    assert scores["correctness_reason"] == "回答の「項目Aは3です。」と標準回答の「項目Aは5。」が食い違う。"
    assert scores["correctness_claims"][0] == {
        "verdict": "contradicted",
        "claim": "項目Aは3です。",
        "answer": "項目Aは3です。",
        "standard": "項目Aは5。",
        "states_source_gap": False,
        "relation": "conflict",
    }


@pytest.mark.parametrize(
    ("claims", "points"),
    [
        (
            [{"claim": "合成の主張", "answer_ids": ["A9"], "states_source_gap": False, "relation": "same", "standard_ids": ["S1"]}],
            ["P1", "P2"],
        ),
        (
            [{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "relation": "conflict", "standard_ids": ["S9"]}],
            ["P1", "P2"],
        ),
        ([{"claim": "合成の主張", "answer_ids": [], "states_source_gap": False, "relation": "same", "standard_ids": ["S1"]}], ["P1", "P2"]),
        ([{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "relation": "same", "standard_ids": ["S1"]}], ["P1"]),
        (
            [{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "relation": "same", "standard_ids": ["S1"]}],
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

    steps = CORRECTNESS_PROMPT.split("## Step ")
    assert [step[:2] for step in steps[1:]] == ["1:", "2:", "3:", "4:"]
    for path in ("actual_responses[].question", "standard_answer[].id", "points[].id", "actual_responses[].answer[].id"):
        assert path in steps[1]
    assert "actual_responses[].answer[].text" in steps[2] and "answer_ids" in steps[2]
    assert "points[].text" in steps[4] and "actual_responses[].answer[].sources[]" in steps[4]
    step3 = CORRECTNESS_PROMPT.split("## Step 3:", 1)[1].split("## Step 4:", 1)[0]
    questions = {line.split(":", 1)[0].removeprefix("- "): line for line in step3.splitlines() if line.startswith("- ")}
    assert tuple(questions) == ("states_source_gap", "standard_ids", "relation")
    relation = step3.split("- relation:", 1)[1]
    # 照合する観点は、その主張が答えている問い。条件と観点の両方を照合に使う。
    assert all(term in questions["standard_ids"] for term in ("対象・条件・適用範囲", "同じ問い", "standard_answer[].id"))
    assert "確認できない" in questions["states_source_gap"] and "記載がない" in questions["states_source_gap"]
    # 同じ結論だけを same にし、両立するだけの主張は compatible、両立しない主張だけを conflict にする（#5652）。
    for value in ("same:", "compatible:", "conflict:"):
        assert f"  - {value}" in relation
    assert "同じ事実・結論" in relation.split("compatible:", 1)[0]
    assert "両方成り立つ" in relation.split("compatible:", 1)[1].split("conflict:", 1)[0]
    assert "両方は成り立たない" in relation.split("conflict:", 1)[1] and "可能性の一つ" in relation.split("conflict:", 1)[1]
    assert "留保を添えていても false" in questions["states_source_gap"]
    # 主張は条件ごと取り出して claim に書く。条件を主張の種類として別に取り出させない。
    step2 = CORRECTNESS_PROMPT.split("## Step 2:", 1)[1].split("## Step 3:", 1)[0]
    assert "条件と結論は一つの主張にまとめ" in step2 and "claim" in step2
    schema = AnswerCorrectnessOutput.model_json_schema()
    claim_properties = schema["$defs"]["JudgedClaim"]["properties"]
    assert list(claim_properties) == ["claim", "answer_ids", *questions]
    assert "verdict" not in claim_properties
    assert all("description" not in claim_properties[key] for key in ("states_source_gap", "relation"))
    # 実際に送る指示（項目の説明の Step を足した文）でも、問いの答え方は 1 回だけ。
    guided, _stripped = with_schema_field_guide(CORRECTNESS_PROMPT, schema)
    assert all(guided.count(f"- {key}:") == 1 for key in questions)
    # 点・理由はコードが作るので、出力に点・理由の項目は無い。
    assert set(AnswerCorrectnessOutput.model_fields) == {"claims", "points"}


@pytest.mark.parametrize(
    ("standard_ids", "states_source_gap", "relation", "verdict"),
    [
        (["S1"], False, "same", "supported"),
        (["S1"], True, "same", "supported"),  # 標準回答も確定できないと述べる
        (["S1"], False, "conflict", "contradicted"),
        (["S1"], False, "compatible", "not_in_standard"),  # 両立する補足は支持にも矛盾にもしない（#5652）
        (["S1"], True, "conflict", "not_in_standard"),  # 資料の欠けを述べる主張は矛盾にしない（#5302）
        ([], False, "conflict", "not_in_standard"),  # 同じ点を述べる文が無い（条件・観点が違う。#5408）
        ([], True, "same", "not_in_standard"),
    ],
)
def test_verdict_is_decided_by_code_from_the_comparison_answers(standard_ids, states_source_gap, relation, verdict):
    """#5408: 印はモデルに選ばせず、同じ点の文・資料の欠け・両立の答えからコードが決める。"""
    claim = JudgedClaim(
        claim="合成の主張",
        answer_ids=["A1"],
        standard_ids=standard_ids,
        states_source_gap=states_source_gap,
        relation=relation,
    )
    assert claim_verdict(claim) == verdict


def test_reservation_only_answer_scores_zero_without_calling_it_a_contradiction():
    """本文がすべて留保（標準回答と合う主張が無い）なら 0。理由は矛盾ではなく、合う主張が無いこと。"""
    inputs = judge_inputs(_payload(answer_text="項目Aと項目Bは資料で確認できず、確定できません。"))
    output = _output(
        [{"claim": "合成の主張", "answer_ids": ["A1"], "states_source_gap": False, "relation": "same", "standard_ids": []}],
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
                "relation": "conflict" if verdict == "contradicted" else "same",
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
                "relation": "conflict",
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


@pytest.mark.parametrize(
    ("answer", "comparisons", "expected"),
    [
        (
            "項目Aは5です。項目Bは8か9の可能性があります。",
            [("項目Aは5。", "A1", ["S1"], False, "same"), ("項目Bは8か9。", "A2", ["S2"], False, "conflict")],
            0.0,
        ),
        (
            "項目Aは5です。資料が不足していますが、項目Bは9です。",
            [
                ("項目Aは5。", "A1", ["S1"], False, "same"),
                ("資料が不足している。", "A2", ["S2"], True, "conflict"),
                ("項目Bは9。", "A2", ["S2"], False, "conflict"),
            ],
            0.0,
        ),
        (
            "項目Aは5です。項目Cは追加の確認対象です。",
            [("項目Aは5。", "A1", ["S1"], False, "same"), ("項目Cは追加の確認対象。", "A2", [], False, "same")],
            1.0,
        ),
        (
            "項目Aは5です。変更には条件Xと条件Yが要り、制限中でないことも必要です。",
            [
                ("項目Aは5。", "A1", ["S1"], False, "same"),
                ("変更には条件X・条件Yと、制限中でないことが必要。", "A2", ["S2"], False, "compatible"),
            ],
            1.0,
        ),
    ],
    ids=["alternative_conclusion", "wrong_conclusion_with_reservation", "additional_uncompared_claim", "compatible_extra_condition"],
)
def test_mixed_claims_preserve_correctness_boundaries(answer, comparisons, expected):
    """合成の照合結果を再生する。意味の分類は実モデルで別途検証する。"""
    output = _output(
        [
            dict(claim=text, answer_ids=[answer_id], standard_ids=standard_ids, states_source_gap=gap, relation=relation)
            for text, answer_id, standard_ids, gap, relation in comparisons
        ],
        [{"point_id": "P1", "covered": True}, {"point_id": "P2", "covered": False}],
    )
    with patch(PARSE, return_value=output):
        result = evaluate_answer_payload(_payload(answer_text=answer), object())
    assert result["status"] == "completed"
    assert result["answer_correctness"] == expected
    assert result["answer_coverage"] == 0.5
    verdicts = [claim["verdict"] for claim in result["correctness_claims"]]
    assert ("contradicted" in verdicts) == (expected == 0.0)
